import { lstat, mkdtemp, realpath, rm } from 'node:fs/promises';
import { dirname, isAbsolute, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { AgentError } from './types.ts';
import { RpcProcess } from './rpc.ts';
import { prepareSandbox } from './sandbox.ts';
import type { WorkspaceDiff, WorkspaceSnapshot } from './workspace.ts';

const sourceDirectory = dirname(fileURLToPath(import.meta.url));
const worker = join(sourceDirectory, 'workspace-observer-worker.ts');
const MAX_FRAME_BYTES = 64 * 1024 * 1024;
let activeObservers = 0;
type OwnedObserver = { stateRoot: string; config: string; rpc?: RpcProcess; ino?: number; dev?: number; released: boolean; cleanup?: Promise<void> };
const ownedObservers = new Map<string, Set<OwnedObserver>>();

async function cleanupOwned(observer: OwnedObserver): Promise<void> {
  if (observer.released) return;
  if (observer.cleanup) return await observer.cleanup;
  const cleanup = (async () => {
    await observer.rpc?.dispose();
    if (await realpath(observer.stateRoot) !== observer.stateRoot) throw new AgentError('unsafe_observer_cleanup');
    const state = await lstat(observer.stateRoot);
    if (!state.isDirectory() || state.isSymbolicLink() || (state.mode & 0o777) !== 0o700) throw new AgentError('unsafe_observer_cleanup');
    try {
      const config = await lstat(observer.config);
      if (!config.isDirectory() || config.isSymbolicLink() || config.ino !== observer.ino || config.dev !== observer.dev || (config.mode & 0o777) !== 0o700) throw new AgentError('unsafe_observer_cleanup');
      await rm(observer.config, { recursive: true, force: false });
    } catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error; }
    observer.released = true;
    const entries = ownedObservers.get(observer.stateRoot);
    entries?.delete(observer);
    if (entries?.size === 0) ownedObservers.delete(observer.stateRoot);
    activeObservers--;
  })();
  observer.cleanup = cleanup;
  try { await cleanup; }
  finally { if (observer.cleanup === cleanup) observer.cleanup = undefined; }
}

/** Retry only owned observation handles before the host relinquishes its state lease. */
export async function cleanupWorkspaceObservers(privateConfigRoot: string): Promise<void> {
  if (!isAbsolute(privateConfigRoot)) throw new AgentError('invalid_observer_path');
  for (const observer of [...ownedObservers.get(resolve(privateConfigRoot)) ?? []]) await cleanupOwned(observer);
}

function inside(root: string, path: string): boolean {
  const name = relative(root, path);
  return name === '' || !isAbsolute(name) && name !== '..' && !name.startsWith(`..${sep}`);
}

/** Workspace bytes are read only by a disposable, credential-free OS-contained owner. */
async function observe(root: string, privateConfigRoot: string, method: 'snapshot' | 'diff', baseline?: WorkspaceSnapshot): Promise<unknown> {
  if (activeObservers >= 1) throw new AgentError('workspace_observer_busy');
  activeObservers++;
  let owned: OwnedObserver | undefined;
  try {
    if (!isAbsolute(root) || !isAbsolute(privateConfigRoot)) throw new AgentError('invalid_observer_path');
    const workspace = resolve(root), state = resolve(privateConfigRoot);
    if (await realpath(workspace) !== workspace || (await lstat(workspace)).isSymbolicLink()) throw new AgentError('workspace_root_changed');
    if (await realpath(state) !== state || inside(workspace, state)) throw new AgentError('unsafe_observer_store');
    const stateInfo = await lstat(state);
    if (!stateInfo.isDirectory() || stateInfo.isSymbolicLink() || (stateInfo.mode & 0o777) !== 0o700) throw new AgentError('unsafe_observer_store');
    const config = await mkdtemp(join(state, 'observer-'));
    owned = { stateRoot: state, config, released: false };
    const entries = ownedObservers.get(state) ?? new Set<OwnedObserver>();
    entries.add(owned); ownedObservers.set(state, entries);
    const configInfo = await lstat(config); owned.ino = configInfo.ino; owned.dev = configInfo.dev;
    const env = { PATH: '/usr/bin:/bin', HOME: config, CFFIXED_USER_HOME: config, TMPDIR: join(config, 'tmp'), OPENSSL_CONF: '/dev/null', LANG: 'en_US.UTF-8' };
    const launch = await prepareSandbox({ command: process.execPath, args: [worker, workspace], workspace, configDir: config, endpoint: 'http://127.0.0.1:1/v1', env,
      workspaceWrite: false, allowProxyNetwork: false, trustedReadPaths: [sourceDirectory, join(dirname(sourceDirectory), 'package.json')] });
    const rpc = new RpcProcess({ ...launch, cwd: config, env, requestTimeoutMs: 15_000, shutdownTimeoutMs: 500,
      maxFrameBytes: MAX_FRAME_BYTES, maxOutputBytes: MAX_FRAME_BYTES,
      onNotification: () => { throw new AgentError('invalid_observer_notification'); },
      onRequest: async () => { throw new AgentError('invalid_observer_request'); }, onFailure: () => {} });
    owned.rpc = rpc;
    return await rpc.request(method, method === 'snapshot' ? {} : { baseline });
  } finally {
    // Keep process/config references and the worker slot if cleanup is uncertain.
    if (owned) await cleanupOwned(owned);
    else activeObservers--;
  }
}

export async function observeWorkspaceSnapshot(root: string, privateConfigRoot: string): Promise<WorkspaceSnapshot> {
  const value = await observe(root, privateConfigRoot, 'snapshot') as WorkspaceSnapshot;
  if (!value || value.schemaVersion !== 1 || value.root !== resolve(root) || value.revision !== value.fingerprint || !/^[a-f0-9]{64}$/.test(value.fingerprint) || !value.files || !value.coverage) throw new AgentError('invalid_workspace_observation');
  return value;
}

export async function observeWorkspaceDiff(baseline: WorkspaceSnapshot, privateConfigRoot: string): Promise<WorkspaceDiff> {
  const value = await observe(baseline.root, privateConfigRoot, 'diff', baseline) as WorkspaceDiff;
  if (!value || value.root !== baseline.root || value.baselineRevision !== baseline.revision || value.authorship !== 'unknown' || !Array.isArray(value.changes) || !value.coverage) throw new AgentError('invalid_workspace_observation');
  return value;
}
