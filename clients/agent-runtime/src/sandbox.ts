import { platform } from 'node:os';
import { randomUUID } from 'node:crypto';
import { constants } from 'node:fs';
import { realpath, mkdir, lstat, open, rename, rm } from 'node:fs/promises';
import { isAbsolute, join, resolve, relative } from 'node:path';
import { AgentError } from './types.ts';

export type LaunchOptions = { command: string; args: string[]; workspace: string; configDir: string; endpoint: string; env: NodeJS.ProcessEnv; trustedReadPaths?: string[]; workspaceWrite?: boolean; managedPreferenceDomain?: 'com.openai.codex'; allowProxyNetwork?: boolean; allowPrivateUnixSockets?: boolean; privateTempDir?: string };
const quote = (value: string) => JSON.stringify(value);
const within = (root: string, path: string) => { const value = relative(root, path); return value === '' || (!value.startsWith('..') && !isAbsolute(value)); };

/** macOS process tree boundary; unsupported OSs fail closed until a qualified owner exists. */
export async function prepareSandbox(options: LaunchOptions): Promise<{ command: string; args: string[] }> {
  if (platform() !== 'darwin') throw new AgentError('platform_sandbox_unqualified');
  const workspace = await realpath(options.workspace);
  if (workspace !== resolve(options.workspace)) throw new AgentError('workspace_root_changed');
  const config = await realpath(options.configDir);
  const configInfo = await lstat(options.configDir);
  if (!configInfo.isDirectory() || configInfo.isSymbolicLink() || (configInfo.mode & 0o777) !== 0o700) throw new AgentError('unsafe_sandbox_config');
  if (options.managedPreferenceDomain !== undefined && options.managedPreferenceDomain !== 'com.openai.codex') throw new AgentError('unqualified_preference_domain');
  if (options.managedPreferenceDomain && (options.env.HOME !== config || options.env.CFFIXED_USER_HOME !== config)) throw new AgentError('managed_preferences_home_mismatch');
  const endpoint = new URL(options.endpoint);
  if (endpoint.protocol !== 'http:' || endpoint.hostname !== '127.0.0.1' || !endpoint.port) throw new AgentError('invalid_local_endpoint');
  if (!isAbsolute(options.command)) throw new AgentError('absolute_runtime_required');
  const executable = await realpath(options.command);
  if ((options.trustedReadPaths?.length ?? 0) > 16) throw new AgentError('too_many_runtime_paths');
  const trusted: string[] = [];
  for (const path of options.trustedReadPaths ?? []) {
    if (!isAbsolute(path)) throw new AgentError('absolute_runtime_path_required');
    trusted.push(await realpath(path));
  }
  let temp = join(config, 'tmp');
  if (options.privateTempDir !== undefined) {
    if (!isAbsolute(options.privateTempDir)) throw new AgentError('unsafe_sandbox_temp');
    const info = await lstat(options.privateTempDir);
    temp = await realpath(options.privateTempDir);
    if (temp !== resolve(options.privateTempDir) || !info.isDirectory() || info.isSymbolicLink() || (info.mode & 0o777) !== 0o700 || info.uid !== process.getuid?.()
      || within(workspace, temp) || within(temp, workspace) || within(config, temp) || within(temp, config)) throw new AgentError('unsafe_sandbox_temp');
    if (options.env.TMPDIR !== temp || options.env.CLAUDE_CODE_TMPDIR !== temp) throw new AgentError('sandbox_temp_mismatch');
  } else await mkdir(temp, { mode: 0o700, recursive: true });
  const sockets = join(temp, 'cc-socks');
  if (options.allowPrivateUnixSockets) await mkdir(sockets, { mode: 0o700, recursive: true });
  const paths = [
    `(subpath ${quote(workspace)}) (subpath ${quote(config)})`,
    ...(options.privateTempDir ? [`(subpath ${quote(temp)})`] : []),
    '(subpath "/System") (subpath "/Library/Apple") (subpath "/Library/Developer")',
    '(subpath "/usr/bin") (subpath "/usr/sbin") (subpath "/usr/lib") (subpath "/usr/libexec") (subpath "/usr/share") (subpath "/bin") (subpath "/sbin")',
    '(subpath "/opt/homebrew/Cellar") (subpath "/opt/homebrew/lib") (subpath "/opt/homebrew/opt")',
    `(literal ${quote(executable)})`,
    ...trusted.map(path => `(subpath ${quote(path)})`),
  ];
  const profile = [
    '(version 1)', '(deny default)',
    // Explicitly bound mediated preference access as well as filesystem access.
    '(deny user-preference-read user-preference-write)',
    '(allow process-fork sysctl-read)',
    '(allow process-exec', ...paths, ')',
    '(allow signal (target same-sandbox))',
    ...(options.managedPreferenceDomain ? [
      '(allow mach-lookup (global-name "com.apple.cfprefsd.agent") (global-name "com.apple.cfprefsd.daemon") (global-name "com.apple.cfprefsd.daemon.system") (local-name "com.apple.cfprefsd.agent"))',
      '(allow ipc-posix-shm-read* (ipc-posix-name-prefix "apple.cfprefs."))',
      '(allow user-preference-read (preference-domain "com.openai.codex"))',
      '(allow file-read-data (literal "/dev/autofs_nowait"))',
    ] : []),
    '(allow file-read-metadata)',
    // Modern dyld opens / as an openat root. A literal
    // root rule does not grant recursive access to private user directories.
    '(allow file-read* (literal "/")', ...paths,
    '  (literal "/dev/null") (literal "/dev/zero") (literal "/dev/random") (literal "/dev/urandom") (subpath "/dev/fd")',
    '  (subpath "/private/etc")',
    // Apple system.sb grants this public ICU/timezone database for runtime clocks.
    '  (subpath "/private/var/db/timezone")',
    '  (literal "/opt/homebrew/etc/openssl@3/openssl.cnf") (literal "/opt/homebrew/etc/openssl@3/cert.pem")',
    ')',
    `(allow file-write* ${options.workspaceWrite === false ? '' : `(subpath ${quote(workspace)}) `}(subpath ${quote(config)}) ${options.privateTempDir ? `(subpath ${quote(temp)}) ` : ''}(literal "/dev/null"))`,
    ...(options.allowProxyNetwork === false ? [] : [`(allow network-outbound (remote ip "localhost:${endpoint.port}"))`]),
    ...(options.allowPrivateUnixSockets ? [
      `(allow network-bind network-inbound (local unix-socket (path-prefix ${quote(sockets + '/')})))`,
      `(allow network-outbound (remote unix-socket (path-prefix ${quote(sockets + '/')})))`,
    ] : []),
  ].join('\n');
  const profilePath = join(config, 'sandbox.sb');
  const temporary = join(config, `.sandbox-${randomUUID()}.tmp`);
  let handle;
  try {
    handle = await open(temporary, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | (constants.O_NOFOLLOW ?? 0), 0o600);
    await handle.writeFile(profile, 'utf8'); await handle.sync(); await handle.close(); handle = undefined;
    try {
      const info = await lstat(profilePath);
      if (!info.isFile() || info.isSymbolicLink() || info.nlink !== 1 || (info.mode & 0o777) !== 0o600) throw new AgentError('unsafe_sandbox_profile');
    } catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error; }
    // rename replaces the directory entry and never writes through its target.
    await rename(temporary, profilePath);
  } finally { await handle?.close(); await rm(temporary, { force: true }); }
  return { command: '/usr/bin/sandbox-exec', args: ['-f', profilePath, executable, ...options.args] };
}
