import { createHash, randomUUID } from 'node:crypto';
import { constants } from 'node:fs';
import { lstat, mkdir, open, realpath, rename, rm } from 'node:fs/promises';
import { homedir } from 'node:os';
import { basename, dirname, isAbsolute, join, parse, relative, resolve, sep } from 'node:path';
import { AgentError } from './types.ts';
import type { Engine } from './types.ts';

export const AGENT_CLI_CONFIG_LIMITS = Object.freeze({
  maxBytes: 16 * 1024,
  maxAllowanceMicrousd: 1_000_000_000_000,
  maxPriceMicrousdPerMillion: 1_000_000_000,
  maxInputTokens: 2_000_000,
  maxOutputTokens: 100_000,
});

export type AgentCLIConfig = {
  schemaVersion: 1;
  profileId: string;
  engine: Engine;
  model: string;
  allowanceMicrousd: number;
  projectAllowanceMicrousd: number;
  profileAllowanceMicrousd: number;
  inputMicrousdPerMillion: number;
  outputMicrousdPerMillion: number;
  maxInputTokens: number;
  maxOutputTokens: number;
  codexExecutable?: string;
};
export type FolderContext = {
  workspace: string;
  profileId: string;
  projectId: string;
  stateDirectory: string;
  configPath: string;
};
const CONFIG_FIELDS = new Set(['schemaVersion', 'profileId', 'engine', 'model', 'allowanceMicrousd', 'projectAllowanceMicrousd', 'profileAllowanceMicrousd', 'inputMicrousdPerMillion', 'outputMicrousdPerMillion', 'maxInputTokens', 'maxOutputTokens', 'codexExecutable']);

function profile(value: unknown): asserts value is string {
  if (typeof value !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/.test(value) || value === '.' || value === '..') throw new AgentError('invalid_agent_cli_profile');
}
function absolutePath(value: unknown): asserts value is string {
  if (typeof value !== 'string' || value.length > 4096 || value.includes('\0') || !isAbsolute(value) || resolve(value) !== value || parse(value).root === value) throw new AgentError('agent_cli_state_path_denied');
}
function contains(root: string, path: string): boolean {
  const difference = relative(root, path);
  return difference === '' || difference !== '..' && !difference.startsWith(`..${sep}`) && !isAbsolute(difference);
}
function outsideWorkspace(workspace: string, state: string): void {
  if (contains(workspace, state)) throw new AgentError('agent_cli_state_inside_workspace', 'Agent CLI state must be outside the selected folder. Choose a state directory outside this folder, especially when using your home directory as the workspace.');
}
function positiveInteger(value: unknown, ceiling: number): boolean {
  return Number.isSafeInteger(value) && (value as number) > 0 && (value as number) <= ceiling;
}

/** Prices and caps are explicit user settings; no vendor rates are assumed. */
export function validateAgentCLIConfig(value: unknown): asserts value is AgentCLIConfig {
  if (!value || typeof value !== 'object' || Array.isArray(value) || Object.getPrototypeOf(value) !== Object.prototype && Object.getPrototypeOf(value) !== null) throw new AgentError('invalid_agent_cli_config');
  const config = value as Record<string, unknown>;
  if (Object.keys(config).some(key => !CONFIG_FIELDS.has(key)) || config.schemaVersion !== 1) throw new AgentError('invalid_agent_cli_config');
  profile(config.profileId);
  if (!['codex', 'claude'].includes(config.engine as string) || typeof config.model !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/.test(config.model)) throw new AgentError('invalid_agent_cli_config');
  for (const key of ['allowanceMicrousd', 'projectAllowanceMicrousd', 'profileAllowanceMicrousd']) if (!positiveInteger(config[key], AGENT_CLI_CONFIG_LIMITS.maxAllowanceMicrousd)) throw new AgentError('invalid_agent_cli_config');
  if ((config.allowanceMicrousd as number) > (config.projectAllowanceMicrousd as number) || (config.projectAllowanceMicrousd as number) > (config.profileAllowanceMicrousd as number)) throw new AgentError('invalid_agent_cli_config');
  for (const key of ['inputMicrousdPerMillion', 'outputMicrousdPerMillion']) if (!positiveInteger(config[key], AGENT_CLI_CONFIG_LIMITS.maxPriceMicrousdPerMillion)) throw new AgentError('invalid_agent_cli_config');
  if (!positiveInteger(config.maxInputTokens, AGENT_CLI_CONFIG_LIMITS.maxInputTokens) || !positiveInteger(config.maxOutputTokens, AGENT_CLI_CONFIG_LIMITS.maxOutputTokens)) throw new AgentError('invalid_agent_cli_config');
  if (config.codexExecutable !== undefined) {
    if (config.engine !== 'codex') throw new AgentError('invalid_agent_cli_config');
    absolutePath(config.codexExecutable);
  }
  if (/\bsk-(?:ant-)?[A-Za-z0-9_-]{16,}/.test(JSON.stringify(config))) throw new AgentError('invalid_agent_cli_config');
}

/** Exact decimal USD to integer micro-USD; exponent and excess precision fail closed. */
export function dollarToMicrousd(value: string): number {
  if (typeof value !== 'string') throw new AgentError('invalid_dollar_amount');
  const match = /^(0|[1-9][0-9]{0,9})(?:\.([0-9]{1,6}))?$/.exec(value);
  if (!match) throw new AgentError('invalid_dollar_amount');
  const amount = BigInt(match[1]!) * 1_000_000n + BigInt((match[2] ?? '').padEnd(6, '0'));
  if (amount > BigInt(Number.MAX_SAFE_INTEGER)) throw new AgentError('dollar_amount_out_of_range');
  return Number(amount);
}

function missing(error: unknown): boolean { return (error as NodeJS.ErrnoException).code === 'ENOENT'; }
function uid(): number {
  if (!process.getuid || !['darwin', 'linux'].includes(process.platform)) throw new AgentError('agent_cli_platform_unsupported');
  return process.getuid();
}
async function checkDirectory(path: string, privateMode: boolean, create: boolean): Promise<void> {
  let info;
  try { info = await lstat(path); }
  catch (error) {
    if (!missing(error) || !create) throw error;
    try { await mkdir(path, { mode: 0o700 }); }
    catch (creationError) { if ((creationError as NodeJS.ErrnoException).code !== 'EEXIST') throw creationError; }
    info = await lstat(path);
  }
  if (info.isSymbolicLink() || !info.isDirectory()) throw new AgentError('agent_cli_state_path_denied');
  if (privateMode && ((info.mode & 0o777) !== 0o700 || info.uid !== uid())) throw new AgentError('agent_cli_private_state_required');
  if (!privateMode && (info.mode & 0o022) !== 0 && ((info.mode & 0o1000) === 0 || info.uid !== 0 && info.uid !== uid())) throw new AgentError('agent_cli_state_path_denied');
}
async function prepareState(stateRoot: string, profileId: string, create: boolean): Promise<void> {
  let path = parse(stateRoot).root;
  for (const part of stateRoot.slice(path.length).split(sep).filter(Boolean)) {
    path = join(path, part); await checkDirectory(path, path === stateRoot, create);
  }
  await checkDirectory(join(stateRoot, 'profiles'), true, create);
  await checkDirectory(join(stateRoot, 'profiles', profileId), true, create);
}
async function canonicalFutureDirectory(path: string): Promise<string> {
  const remaining: string[] = [];
  let ancestor = path;
  for (;;) {
    try {
      const info = await lstat(ancestor);
      // A supplied existing state root itself must not be a symlink. Aliased
      // system ancestors (such as /tmp on macOS) are canonicalized before use.
      if (ancestor === path && info.isSymbolicLink()) throw new AgentError('agent_cli_state_path_denied');
      if (!info.isDirectory() && !info.isSymbolicLink()) throw new AgentError('agent_cli_state_path_denied');
      return join(await realpath(ancestor), ...remaining.reverse());
    } catch (error) {
      if (!missing(error)) throw error;
      remaining.push(basename(ancestor)); ancestor = dirname(ancestor);
    }
  }
}
function projectId(workspace: string): string {
  const name = (basename(workspace) || 'root').normalize('NFKD').replace(/[^A-Za-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40) || 'folder';
  return `folder-${name}-${createHash('sha256').update(workspace).digest('hex').slice(0, 32)}`;
}

export async function resolveFolderContext(workspace: string, profileId: string, stateRoot?: string): Promise<FolderContext> {
  profile(profileId); uid();
  if (typeof workspace !== 'string' || !workspace || workspace.length > 4096 || workspace.includes('\0')) throw new AgentError('agent_cli_workspace_invalid');
  const canonicalWorkspace = await realpath(resolve(workspace));
  if (!(await lstat(canonicalWorkspace)).isDirectory()) throw new AgentError('agent_cli_workspace_invalid');
  const requestedState = stateRoot ?? join(homedir(), '.hormuz', 'agent-cli');
  absolutePath(requestedState);
  const canonicalState = await canonicalFutureDirectory(requestedState);
  outsideWorkspace(canonicalWorkspace, canonicalState);
  await prepareState(canonicalState, profileId, true);
  const stateDirectory = join(canonicalState, 'profiles', profileId);
  return { workspace: canonicalWorkspace, profileId, projectId: projectId(canonicalWorkspace), stateDirectory, configPath: join(stateDirectory, 'config.json') };
}

async function checkContext(context: FolderContext): Promise<void> {
  profile(context.profileId); uid(); absolutePath(context.stateDirectory); absolutePath(context.configPath);
  if (context.configPath !== join(context.stateDirectory, 'config.json') || dirname(context.stateDirectory) !== join(dirname(dirname(context.stateDirectory)), 'profiles')) throw new AgentError('agent_cli_state_path_denied');
  if (basename(context.stateDirectory) !== context.profileId || await realpath(context.workspace) !== context.workspace || !(await lstat(context.workspace)).isDirectory() || projectId(context.workspace) !== context.projectId) throw new AgentError('agent_cli_config_scope_denied');
  const stateRoot = dirname(dirname(context.stateDirectory));
  absolutePath(stateRoot);
  outsideWorkspace(context.workspace, stateRoot);
  await prepareState(stateRoot, context.profileId, false);
}
async function readConfig(path: string): Promise<string> {
  for (let attempt = 0; attempt < 3; attempt++) {
    const info = await lstat(path);
    if (!info.isFile() || info.isSymbolicLink() || info.uid !== uid() || (info.mode & 0o777) !== 0o600 || info.nlink > 1) throw new AgentError('agent_cli_config_file_invalid');
    if (info.size > AGENT_CLI_CONFIG_LIMITS.maxBytes) throw new AgentError('agent_cli_config_too_large');
    const handle = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW);
    try {
      const before = await handle.stat();
      if (!before.isFile() || before.uid !== uid() || (before.mode & 0o777) !== 0o600 || before.nlink > 1) throw new AgentError('agent_cli_config_file_invalid');
      if (before.nlink === 0) continue;
      if (before.size > AGENT_CLI_CONFIG_LIMITS.maxBytes) throw new AgentError('agent_cli_config_too_large');
      const buffer = Buffer.alloc(before.size + 1);
      let offset = 0;
      while (offset < buffer.length) {
        const { bytesRead } = await handle.read(buffer, offset, buffer.length - offset, offset);
        if (bytesRead === 0) break;
        offset += bytesRead;
      }
      const after = await handle.stat();
      if (after.nlink === 0) continue;
      if (offset !== before.size || after.size !== before.size || after.mtimeMs !== before.mtimeMs || after.ctimeMs !== before.ctimeMs || after.uid !== uid() || (after.mode & 0o777) !== 0o600 || after.nlink !== 1) throw new AgentError('agent_cli_config_changed');
      try { return new TextDecoder('utf-8', { fatal: true }).decode(buffer.subarray(0, offset)); }
      catch { throw new AgentError('invalid_agent_cli_config'); }
    } finally { await handle.close(); }
  }
  throw new AgentError('agent_cli_config_changed');
}

export async function loadAgentCLIConfig(context: FolderContext): Promise<AgentCLIConfig | undefined> {
  await checkContext(context);
  let text: string;
  try { text = await readConfig(context.configPath); }
  catch (error) { if (missing(error)) return undefined; throw error; }
  let config: unknown;
  try { config = JSON.parse(text); } catch { throw new AgentError('invalid_agent_cli_config'); }
  validateAgentCLIConfig(config);
  if (config.profileId !== context.profileId) throw new AgentError('agent_cli_config_scope_denied');
  return config;
}

export async function saveAgentCLIConfig(context: FolderContext, config: AgentCLIConfig): Promise<void> {
  validateAgentCLIConfig(config);
  if (config.profileId !== context.profileId) throw new AgentError('agent_cli_config_scope_denied');
  const serialized = JSON.stringify(config);
  if (Buffer.byteLength(serialized) > AGENT_CLI_CONFIG_LIMITS.maxBytes) throw new AgentError('agent_cli_config_too_large');
  await checkContext(context); await loadAgentCLIConfig(context);
  const temporary = join(context.stateDirectory, `.config.${randomUUID()}.tmp`);
  let handle;
  try {
    handle = await open(temporary, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW, 0o600);
    await handle.chmod(0o600); await handle.writeFile(serialized, 'utf8'); await handle.sync();
    await handle.close(); handle = undefined;
    await checkContext(context); await loadAgentCLIConfig(context);
    await rename(temporary, context.configPath);
    const directoryHandle = await open(context.stateDirectory, constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW);
    try { await directoryHandle.sync(); } finally { await directoryHandle.close(); }
  } finally { try { await handle?.close(); } finally { await rm(temporary, { force: true }); } }
}
