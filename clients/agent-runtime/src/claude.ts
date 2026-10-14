import { mkdir, realpath, lstat, mkdtemp, rm } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { dirname, isAbsolute, relative, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { RpcProcess } from './rpc.ts';
import { prepareSandbox } from './sandbox.ts';
import { AgentError } from './types.ts';
import type { LaunchOptions } from './sandbox.ts';
import type { AgentSessionAdapter, RuntimeStart, RuntimeRun, RunResult, ApprovalDecision, RuntimeEvent, ActionRequest } from './types.ts';

export const CLAUDE_SDK_VERSION = '0.3.299';
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const workerPath = fileURLToPath(new URL('./claude-worker' + (import.meta.url.endsWith('.ts') ? '.ts' : '.js'), import.meta.url));
export type ClaudeAdapterOptions = {
  command?: string;
  /** Trusted test/application wiring, never renderer input. */
  sdkFixture?: string;
  prepareLaunch?: (options: LaunchOptions) => Promise<{ command: string; args: string[] }>;
  requestTimeoutMs?: number; approvalTimeoutMs?: number; runTimeoutMs?: number; shutdownTimeoutMs?: number;
};
type Active = { turnId: string; options: RuntimeRun; controller: AbortController; plan: string; resolve: (result: RunResult) => void };
const record = (input: unknown): Record<string, unknown> => {
  if (!input || typeof input !== 'object' || Array.isArray(input)) throw new AgentError('invalid_runtime_payload');
  return input as Record<string, unknown>;
};
const within = (root: string, path: string) => { const rel = relative(root, path); return rel === '' || (!rel.startsWith('..') && !isAbsolute(rel)); };

/** Resolve from trusted installed code, never from the project being worked on. */
async function runtimeReadPaths(sdkFixture?: string): Promise<string[]> {
  const sdkEntry = await realpath(createRequire(workerPath).resolve('@anthropic-ai/claude-agent-sdk'));
  const paths = [await realpath(dirname(workerPath)), await realpath(fileURLToPath(new URL('../package.json', import.meta.url))), dirname(sdkEntry)];
  if (sdkFixture) paths.push(await realpath(dirname(sdkFixture)));
  else {
    // The pinned SDK uses createRequire at its own entry to find the optional
    // platform package. This also handles npm hoisting and pnpm's real paths.
    const sdkRequire = createRequire(sdkEntry);
    const base = `@anthropic-ai/claude-agent-sdk-${process.platform}-${process.arch}`;
    const report = process.platform === 'linux' ? process.report?.getReport() as { header?: { glibcVersionRuntime?: string } } | undefined : undefined;
    const musl = report !== undefined && report.header?.glibcVersionRuntime === undefined;
    const packages = process.platform === 'linux' ? musl ? [base + '-musl', base] : [base, base + '-musl'] : [base];
    let binary: string | undefined;
    for (const name of packages) {
      try { binary = await realpath(sdkRequire.resolve(`${name}/claude${process.platform === 'win32' ? '.exe' : ''}`)); break; }
      catch (error) {
        if (!['MODULE_NOT_FOUND', 'ENOENT', 'ERR_PACKAGE_PATH_NOT_EXPORTED'].includes((error as NodeJS.ErrnoException).code ?? '')) throw error;
      }
    }
    if (!binary) throw new AgentError('claude_native_unavailable');
    paths.push(dirname(binary));
  }
  return [...new Set(paths)];
}

/** Claude's SDK runs only in a supervised worker, never in the privileged host/renderer. */
export class ClaudeAdapter implements AgentSessionAdapter {
  readonly engine = 'claude' as const;
  readonly version = CLAUDE_SDK_VERSION;
  private settings: ClaudeAdapterOptions;
  private options?: RuntimeStart;
  private threadId?: string;
  private resume = false;
  private rpc?: RpcProcess;
  private active?: Active;
  private disposed = false;
  private startCalled = false;
  private temporary?: { path: string; dev: number; ino: number };
  constructor(settings: ClaudeAdapterOptions = {}) { this.settings = settings; }

  async start(options: RuntimeStart): Promise<{ threadId: string }> {
    if (this.startCalled || this.disposed) throw new AgentError('runtime_already_started');
    this.startCalled = true;
    const endpoint = new URL(options.endpoint);
    if (endpoint.protocol !== 'http:' || endpoint.hostname !== '127.0.0.1' || !endpoint.port || endpoint.username || endpoint.password || endpoint.search || endpoint.hash) throw new AgentError('invalid_gateway_endpoint');
    if (!options.credential || options.credential.length > 4096 || /[\r\n\0]/.test(options.credential)) throw new AgentError('invalid_gateway_credential');
    if (!options.model || options.model.length > 256 || /[\r\n\0]/.test(options.model)) throw new AgentError('invalid_runtime_model');
    if (options.maxOutputTokens !== undefined && (!Number.isSafeInteger(options.maxOutputTokens) || options.maxOutputTokens < 1 || options.maxOutputTokens > 1_000_000)) throw new AgentError('invalid_runtime_output_limit');
    if (!isAbsolute(options.workspace) || !isAbsolute(options.configDir)) throw new AgentError('invalid_runtime_path');
    const workspace = await realpath(options.workspace);
    await mkdir(options.configDir, { mode: 0o700, recursive: true });
    if ((await lstat(options.configDir)).isSymbolicLink()) throw new AgentError('invalid_runtime_path');
    const configDir = await realpath(options.configDir);
    if (!(await lstat(workspace)).isDirectory() || within(workspace, configDir) || within(configDir, workspace)) throw new AgentError('runtime_home_not_isolated');
    await mkdir(join(configDir, 'tmp'), { mode: 0o700, recursive: true });
    await mkdir(join(configDir, 'Library', 'Preferences'), { mode: 0o700, recursive: true });
    if (options.threadId && !uuid.test(options.threadId)) throw new AgentError('invalid_runtime_identity');
    this.options = { ...options, workspace, configDir };
    this.threadId = options.threadId ?? randomUUID();
    this.resume = Boolean(options.threadId);
    try {
      // Import/version handshake only. SDK query/session materialization begins with the first turn.
      await this.launch(false);
      await this.closeWorker();
      return { threadId: this.threadId };
    } catch (error) { await this.dispose(); throw error instanceof AgentError ? error : new AgentError('runtime_start_failed'); }
  }

  private async launch(workspaceWrite: boolean): Promise<void> {
    const options = this.options!;
    if (!this.temporary) {
      // Darwin Unix socket addresses need a short path. Portable injected
      // fixtures use the host OS temp directory; production remains fail closed
      // on platforms without a qualified sandbox launcher.
      const parent = process.platform === 'darwin' ? '/private/tmp' : tmpdir();
      const path = await realpath(await mkdtemp(join(parent, 'hm-c-')));
      const info = await lstat(path);
      this.temporary = { path, dev: info.dev, ino: info.ino };
    }
    const temporary = this.temporary;
    const identity = await lstat(temporary.path);
    if (!identity.isDirectory() || identity.isSymbolicLink() || identity.dev !== temporary.dev || identity.ino !== temporary.ino || (identity.mode & 0o777) !== 0o700 || identity.uid !== process.getuid?.()) throw new AgentError('runtime_temp_changed');
    const env: NodeJS.ProcessEnv = {
      PATH: '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin', HOME: options.configDir,
      CFFIXED_USER_HOME: options.configDir, OPENSSL_CONF: '/dev/null', TMPDIR: temporary.path,
      CLAUDE_CODE_TMPDIR: temporary.path,
      LANG: 'en_US.UTF-8', CLAUDE_CONFIG_DIR: options.configDir,
      ANTHROPIC_API_KEY: options.credential, ANTHROPIC_BASE_URL: options.endpoint.replace(/\/$/, '').replace(/\/v1$/, ''),
      CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC: '1', DISABLE_TELEMETRY: '1', DISABLE_ERROR_REPORTING: '1',
      CLAUDE_CODE_DISABLE_AUTO_MEMORY: '1', CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION: 'false',
      CLAUDE_CODE_MAX_OUTPUT_TOKENS: String(options.maxOutputTokens ?? 1024), CLAUDE_CODE_STARTUP_FAILURE_RESULTS: '1',
      CLAUDE_CODE_MAX_RETRIES: '2', API_TIMEOUT_MS: '60000',
    };
    const args = [workerPath, ...(this.settings.sdkFixture ? [this.settings.sdkFixture] : [])];
    const trustedReadPaths = await runtimeReadPaths(this.settings.sdkFixture);
    const launch = await (this.settings.prepareLaunch ?? prepareSandbox)({ command: this.settings.command ?? process.execPath, args,
      workspace: options.workspace, configDir: options.configDir, endpoint: options.endpoint, env, workspaceWrite,
      allowPrivateUnixSockets: true,
      privateTempDir: temporary.path,
      trustedReadPaths,
    });
    if (this.disposed) throw new AgentError('runtime_closed');
    this.rpc = new RpcProcess({ ...launch, env, cwd: options.workspace, requestTimeoutMs: this.settings.requestTimeoutMs,
      shutdownTimeoutMs: this.settings.shutdownTimeoutMs, onNotification: (method, params) => this.notification(method, params),
      onRequest: (method, params, id) => this.approval(method, params, id), onFailure: () => this.finish('unknown'),
    });
    const initialized = record(await this.rpc.request('initialize', {}));
    if (initialized.version !== CLAUDE_SDK_VERSION || initialized.engine !== 'claude') throw new AgentError('unsupported_claude_version');
    const bound = record(await this.rpc.request('session/start', { threadId: this.threadId, resume: this.resume, workspace: options.workspace, model: options.model }));
    if (bound.threadId !== this.threadId) throw new AgentError('runtime_thread_mismatch');
    if (bound.available === false) throw new AgentError('runtime_session_missing');
  }

  async run(options: RuntimeRun): Promise<RunResult> {
    if (!this.options || this.disposed || !this.threadId) throw new AgentError('runtime_not_started');
    if (this.active || this.rpc) throw new AgentError('runtime_busy');
    if (!options.prompt || Buffer.byteLength(options.prompt) > 262_144 || !['plan', 'execute'].includes(options.mode)) throw new AgentError('invalid_runtime_prompt');
    if (options.signal.aborted) return { status: 'interrupted', threadId: this.threadId };
    let resolve!: (result: RunResult) => void;
    const completed = new Promise<RunResult>(res => { resolve = res; });
    const active: Active = { turnId: randomUUID(), options, controller: new AbortController(), plan: '', resolve };
    this.active = active;
    const abort = () => { void this.interrupt(); };
    options.signal.addEventListener('abort', abort, { once: true });
    const timer = setTimeout(() => { void this.interrupt(); }, this.settings.runTimeoutMs ?? 15 * 60_000);
    try {
      await this.launch(options.mode === 'execute');
      if (this.active !== active || active.controller.signal.aborted || options.signal.aborted) {
        this.finish('interrupted');
        return await completed;
      }
      const response = record(await this.rpc!.request('turn/start', { threadId: this.threadId, turnId: active.turnId, prompt: options.prompt, mode: options.mode }));
      if (response.turnId !== active.turnId) throw new AgentError('runtime_turn_mismatch');
      if (options.signal.aborted) await this.interrupt();
      const result = await completed;
      if (result.status === 'completed' || result.status === 'interrupted') this.resume = true;
      return result;
    } catch (error) { this.finish('unknown'); throw error instanceof AgentError ? error : new AgentError('runtime_run_failed'); }
    finally { clearTimeout(timer); options.signal.removeEventListener('abort', abort); await this.closeWorker(); }
  }

  private notification(method: string, raw: unknown): void {
    const active = this.active;
    if (!active) return;
    const params = record(raw);
    if (params.threadId !== this.threadId || params.turnId !== active.turnId) return;
    if (method === 'turn/completed') {
      this.finish(params.status === 'completed' ? 'completed' : params.status === 'interrupted' ? 'interrupted' : params.status === 'failed' ? 'failed' : 'unknown');
    } else if (method === 'event') {
      const type = params.type;
      if (!['message', 'plan', 'tool', 'usage', 'diff', 'question'].includes(String(type))) throw new AgentError('invalid_runtime_event');
      if (type === 'plan' && typeof record(params.data).text === 'string') active.plan = record(params.data).text as string;
      active.options.onEvent({ type: type as RuntimeEvent['type'], turnId: active.turnId, data: params.data });
    }
  }

  private async approval(method: string, raw: unknown, id: string | number): Promise<unknown> {
    if (method !== 'approval') throw new AgentError('unsupported_server_request');
    const params = record(raw), active = this.active;
    if (!active || params.threadId !== this.threadId || params.turnId !== active.turnId || active.controller.signal.aborted) throw new AgentError('stale_runtime_request');
    if (!['command', 'file', 'question'].includes(String(params.kind)) || (active.options.mode === 'plan' && params.kind !== 'question')) return { decision: 'decline' };
    const operation = record(params.operation);
    const request: ActionRequest = { id: String(id), turnId: active.turnId, kind: params.kind as ActionRequest['kind'], operation };
    let timer: NodeJS.Timeout | undefined;
    let onAbort!: () => void;
    const cancellation = new Promise<ApprovalDecision>(res => {
      onAbort = () => res({ decision: 'cancel' });
      active.controller.signal.addEventListener('abort', onAbort, { once: true });
      timer = setTimeout(onAbort, this.settings.approvalTimeoutMs ?? 5 * 60_000);
    });
    let decision;
    try { decision = await Promise.race([active.options.onApproval(request), cancellation]); }
    finally { if (timer) clearTimeout(timer); active.controller.signal.removeEventListener('abort', onAbort); }
    if (this.active !== active || active.controller.signal.aborted) throw new AgentError('stale_runtime_request');
    if (!decision || !['accept', 'decline', 'cancel'].includes(decision.decision)) throw new AgentError('invalid_runtime_decision');
    return decision;
  }

  private finish(status: RunResult['status']): void {
    const active = this.active;
    if (!active) return;
    this.active = undefined; active.controller.abort();
    active.resolve({ status, threadId: this.threadId, ...(active.options.mode === 'plan' && status === 'completed' && active.plan ? { plan: active.plan } : {}) });
  }
  async interrupt(): Promise<void> {
    const active = this.active;
    if (!active) return;
    active.controller.abort();
    try { await this.rpc?.request('turn/interrupt', { threadId: this.threadId, turnId: active.turnId }, 5_000); }
    catch { /* Lost worker cannot report a completed turn. */ }
    this.finish('interrupted'); await this.closeWorker();
  }
  private async closeWorker(): Promise<void> { const rpc = this.rpc; await rpc?.dispose(); if (this.rpc === rpc) this.rpc = undefined; }
  private async removeTemporary(): Promise<void> {
    const temporary = this.temporary;
    if (!temporary) return;
    try {
      const info = await lstat(temporary.path);
      if (!info.isDirectory() || info.isSymbolicLink() || info.dev !== temporary.dev || info.ino !== temporary.ino || info.uid !== process.getuid?.()) throw new AgentError('runtime_temp_changed');
      await rm(temporary.path, { recursive: true, force: true });
    } catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error; }
    if (this.temporary === temporary) this.temporary = undefined;
  }
  async dispose(): Promise<void> { this.disposed = true; this.finish('unknown'); await this.closeWorker(); await this.removeTemporary(); if (this.options) this.options.credential = ''; }
}
