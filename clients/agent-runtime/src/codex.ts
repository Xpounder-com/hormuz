import { mkdir, realpath, writeFile, lstat, open } from 'node:fs/promises';
import { constants } from 'node:fs';
import { isAbsolute, relative, join, resolve, basename, dirname } from 'node:path';
import { RpcProcess } from './rpc.ts';
import { prepareSandbox } from './sandbox.ts';
import { RuntimePreparation } from './preparation.ts';
import type { LaunchOptions } from './sandbox.ts';
import { AgentError } from './types.ts';
import type { AgentSessionAdapter, RuntimeStart, RuntimeRun, RunResult, ActionRequest, ApprovalDecision, RuntimeEvent, RuntimeHistoryPolicy } from './types.ts';

export const CODEX_VERSION = '0.160.0';
export type CodexLaunchOptions = LaunchOptions;
export type CodexAdapterOptions = {
  command?: string;
  args?: string[];
  /** Trusted application wiring only. Must enforce OS read/write/network containment. */
  prepareLaunch?: (options: CodexLaunchOptions) => Promise<{ command: string; args: string[] }>;
  requestTimeoutMs?: number;
  approvalTimeoutMs?: number;
  runTimeoutMs?: number;
  shutdownTimeoutMs?: number;
  preparationTimeoutMs?: number;
  maxFrameBytes?: number;
  maxOutputBytes?: number;
};
type ActiveRun = {
  options: RuntimeRun; turnId?: string; starting: boolean; text: string; steps: string; finalMessage: string;
  items: Map<string, Record<string, unknown>>; controller: AbortController;
  ready: Promise<void>; markReady: () => void; notifications: Array<{ method: string; raw: unknown }>;
  resolve: (result: RunResult) => void;
};

function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new AgentError('invalid_runtime_payload');
  return value as Record<string, unknown>;
}
function identifier(value: unknown): string {
  if (typeof value !== 'string' || !value || value.length > 256) throw new AgentError('invalid_runtime_identity');
  return value;
}
function inside(root: string, path: string): boolean {
  const rel = relative(root, path);
  return rel === '' || (!rel.startsWith('..') && !isAbsolute(rel));
}
function endpointUrl(input: string): URL {
  const url = new URL(input);
  if (url.protocol !== 'http:' || !['127.0.0.1', '[::1]'].includes(url.hostname) || !url.port || url.username || url.password || url.search || url.hash) {
    throw new AgentError('invalid_gateway_endpoint');
  }
  return url;
}
async function manifest(path: string): Promise<Record<string, unknown>> {
  const file = await open(path, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
  try {
    const before = await file.stat();
    if (!before.isFile() || before.size > 16_384) throw new AgentError('unsupported_codex_layout');
    const buffer = Buffer.alloc(16_385);
    const { bytesRead } = await file.read(buffer, 0, buffer.length, 0);
    const after = await file.stat();
    if (bytesRead > 16_384 || before.size !== bytesRead || after.size !== before.size || after.mtimeMs !== before.mtimeMs) throw new AgentError('unsupported_codex_layout');
    return object(JSON.parse(buffer.subarray(0, bytesRead).toString()));
  } finally { await file.close(); }
}
const npmTargets: Record<string, string> = {
  'darwin-arm64': 'aarch64-apple-darwin', 'darwin-x64': 'x86_64-apple-darwin',
  'linux-arm64': 'aarch64-unknown-linux-musl', 'linux-x64': 'x86_64-unknown-linux-musl',
  'win32-arm64': 'aarch64-pc-windows-msvc', 'win32-x64': 'x86_64-pc-windows-msvc',
};
async function platformManifestPath(packageRoot: string, target: string): Promise<string> {
  const candidates = [join(packageRoot, 'node_modules', '@openai', `codex-${target}`, 'package.json'), join(dirname(packageRoot), `codex-${target}`, 'package.json')];
  const found = new Set<string>();
  for (const path of candidates) {
    try { found.add(await realpath(path)); }
    catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error; }
  }
  if (found.size !== 1) throw new AgentError('unsupported_codex_layout');
  return found.values().next().value!;
}

/** One pinned Codex app-server process per host session; no renderer-supplied RPC. */
export class CodexAdapter implements AgentSessionAdapter {
  readonly engine = 'codex' as const;
  readonly version = CODEX_VERSION;
  private settings: CodexAdapterOptions;
  private rpc?: RpcProcess;
  private startOptions?: RuntimeStart;
  private threadId?: string;
  private active?: ActiveRun;
  private disposed = false;
  private startCalled = false;
  private executable?: Promise<{ command: string; packageRoot?: string }>;
  private preparation = new RuntimePreparation();

  constructor(options: CodexAdapterOptions = {}) { this.settings = options; }

  private resolveExecutable(): Promise<{ command: string; packageRoot?: string }> {
    return this.executable ??= (async () => {
      try {
        const supplied = this.settings.command ?? '/opt/homebrew/bin/codex';
        if (!isAbsolute(supplied)) throw new AgentError('absolute_runtime_required');
        let command = await realpath(supplied);
        let packageRoot: string | undefined;
        if (basename(command) === 'codex.js' && basename(dirname(command)) === 'bin') {
          // Resolve the pinned official npm layout without executing/importing
          // its wrapper. Its aliases point at the native binary, not codex.js.
          packageRoot = dirname(dirname(command));
          const cli = await manifest(join(packageRoot, 'package.json'));
          const target = `${process.platform}-${process.arch}`, triple = npmTargets[target];
          const platformPackage = `@openai/codex-${target}`;
          if (cli.name !== '@openai/codex' || cli.version !== CODEX_VERSION) throw new AgentError('unsupported_codex_version');
          if (!triple || object(cli.bin).codex !== 'bin/codex.js' || object(cli.optionalDependencies)[platformPackage] !== `npm:@openai/codex@${CODEX_VERSION}-${target}`) throw new AgentError('unsupported_codex_layout');
          const packagePath = await platformManifestPath(packageRoot, target);
          const native = await manifest(packagePath);
          if (native.name !== '@openai/codex' || native.version !== `${CODEX_VERSION}-${target}` || !Array.isArray(native.os) || native.os.length !== 1 || native.os[0] !== process.platform || !Array.isArray(native.cpu) || native.cpu.length !== 1 || native.cpu[0] !== process.arch) throw new AgentError('unsupported_codex_layout');
          command = await realpath(join(dirname(packagePath), 'vendor', triple, 'bin', process.platform === 'win32' ? 'codex.exe' : 'codex'));
        }
        const info = await lstat(command);
        if (!info.isFile() || (process.platform !== 'win32' && (info.mode & 0o111) === 0)) throw new AgentError('unsupported_codex_layout');
        return { command, ...(packageRoot ? { packageRoot } : {}) };
      } catch (error) { throw error instanceof AgentError ? error : new AgentError('unsupported_codex_layout'); }
    })();
  }

  async historyPolicy(): Promise<RuntimeHistoryPolicy> {
    const finishPreparation = this.preparation.begin();
    try { return { engine: 'codex', runtimeVersion: CODEX_VERSION, executablePath: (await this.resolveExecutable()).command }; }
    finally { finishPreparation(); }
  }

  async start(options: RuntimeStart): Promise<{ threadId: string }> {
    if (this.disposed || this.startCalled) throw new AgentError('runtime_already_started');
    this.startCalled = true;
    const finishPreparation = this.preparation.begin();
    try {
      const endpoint = endpointUrl(options.endpoint);
      if (options.threadId !== undefined) identifier(options.threadId);
      if (!options.credential || options.credential.length > 4096 || /[\r\n\0]/.test(options.credential)) throw new AgentError('invalid_gateway_credential');
      if (!options.model || options.model.length > 256 || /[\r\n\0]/.test(options.model)) throw new AgentError('invalid_runtime_model');
      if (!isAbsolute(options.workspace) || !isAbsolute(options.configDir)) throw new AgentError('invalid_runtime_path');
      const workspace = await realpath(options.workspace);
      if (!(await lstat(workspace)).isDirectory()) throw new AgentError('invalid_runtime_path');
      if (this.disposed) throw new AgentError('runtime_closed');
      await mkdir(options.configDir, { recursive: true, mode: 0o700 });
      if ((await lstat(options.configDir)).isSymbolicLink()) throw new AgentError('invalid_runtime_path');
      const configDir = await realpath(options.configDir);
      if (inside(workspace, configDir) || inside(configDir, workspace)) throw new AgentError('runtime_home_not_isolated');
      if (this.disposed) throw new AgentError('runtime_closed');
      await mkdir(join(configDir, 'tmp'), { mode: 0o700, recursive: true });
      if (this.disposed) throw new AgentError('runtime_closed');
      await mkdir(join(configDir, 'Library', 'Preferences'), { mode: 0o700, recursive: true });
      const executable = await this.resolveExecutable();
      const safePath = '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin';
      const env = {
        PATH: safePath, HOME: configDir, CODEX_HOME: configDir, TMPDIR: join(configDir, 'tmp'), LANG: 'en_US.UTF-8',
        OPENSSL_CONF: '/dev/null',
        CFFIXED_USER_HOME: configDir,
        HORMUZ_CODEX_GATEWAY_TOKEN: options.credential,
        ...(executable.packageRoot ? { CODEX_MANAGED_PACKAGE_ROOT: executable.packageRoot, CODEX_MANAGED_BY_NPM: '1' } : {}),
      };
      // No user profile, inherited auth, MCP, login-shell environment, telemetry, or provider fallback.
      const config = [
        `model = ${JSON.stringify(options.model)}`,
        'model_provider = "hormuz"', 'approval_policy = "on-request"', 'approvals_reviewer = "user"',
        'sandbox_mode = "read-only"', 'web_search = "disabled"', 'allow_login_shell = false',
        'cli_auth_credentials_store = "file"', 'check_for_update_on_startup = false',
        '[model_providers.hormuz]', 'name = "Hormuz loopback gateway"', `base_url = ${JSON.stringify(endpoint.href.replace(/\/$/, ''))}`,
        'env_key = "HORMUZ_CODEX_GATEWAY_TOKEN"', 'wire_api = "responses"', 'requires_openai_auth = false',
        'request_max_retries = 2', 'stream_max_retries = 2', 'stream_idle_timeout_ms = 60000',
        '[shell_environment_policy]', 'inherit = "none"', 'include_only = []', `set = { PATH = ${JSON.stringify(safePath)}, HOME = ${JSON.stringify(configDir)}, TMPDIR = ${JSON.stringify(join(configDir, 'tmp'))} }`,
        '[analytics]', 'enabled = false', '[feedback]', 'enabled = false',
        '[features]', 'multi_agent = false', 'remote_control = false', 'apps = false',
        // Workspace authorization grants tool scope, not trust in repo-controlled runtime config.
        `[projects.${JSON.stringify(workspace)}]`, 'trust_level = "untrusted"',
      ].join('\n') + '\n';
      const configPath = join(configDir, 'config.toml');
      try { if ((await lstat(configPath)).isSymbolicLink()) throw new AgentError('invalid_runtime_path'); }
      catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error; }
      if (this.disposed) throw new AgentError('runtime_closed');
      await writeFile(configPath, config, { mode: 0o600 });
      if (this.disposed) throw new AgentError('runtime_closed');
      this.startOptions = { ...options, workspace, configDir };
      const command = executable.command;
      const args = [...(this.settings.args ?? []), 'app-server', '--stdio', '--strict-config'];
      const launch = await (this.settings.prepareLaunch ?? prepareSandbox)({ command, args, workspace, configDir, endpoint: endpoint.href, env, managedPreferenceDomain: 'com.openai.codex' });
      if (this.disposed) throw new AgentError('runtime_closed');
      this.rpc = new RpcProcess({ ...launch, cwd: workspace, env,
        requestTimeoutMs: this.settings.requestTimeoutMs, shutdownTimeoutMs: this.settings.shutdownTimeoutMs,
        maxFrameBytes: this.settings.maxFrameBytes, maxOutputBytes: this.settings.maxOutputBytes,
        onNotification: (method, params) => this.notification(method, params),
        onRequest: (method, params, id) => this.serverRequest(method, params, id),
        onFailure: () => this.finish('unknown'),
      });
      finishPreparation();
      const initialized = object(await this.rpc.request('initialize', {
        clientInfo: { name: 'hormuz', version: '0.1.0' },
        capabilities: { experimentalApi: true, requestAttestation: false, explicitGatewayOauth: true },
      }));
      if (typeof initialized.userAgent !== 'string' || !initialized.userAgent.startsWith(`hormuz/${CODEX_VERSION} `)) throw new AgentError('unsupported_codex_version');
      if (initialized.codexHome !== configDir) throw new AgentError('runtime_home_mismatch');
      this.rpc.notify('initialized');
      const params = {
        model: options.model, modelProvider: 'hormuz', cwd: workspace, approvalPolicy: 'on-request', approvalsReviewer: 'user',
        sandbox: 'read-only',
        developerInstructions: 'You are operating inside Hormuz. Honour the selected mode, working folder, approvals, and network restrictions. Planning must not mutate files. Do not use subagents, remote control, or unsupervised background work.',
      };
      const response = object(await this.rpc.request(options.threadId ? 'thread/resume' : 'thread/start', options.threadId ? { ...params, threadId: options.threadId, excludeTurns: true } : params));
      const thread = object(response.thread);
      this.threadId = identifier(thread.id);
      if (options.threadId && this.threadId !== options.threadId) throw new AgentError('runtime_thread_mismatch');
      if (thread.cwd !== undefined && thread.cwd !== workspace) throw new AgentError('runtime_workspace_mismatch');
      return { threadId: this.threadId };
    } catch (error) {
      // Release this token before self-disposal; otherwise cleanup would wait
      // for the very start call that is awaiting cleanup.
      finishPreparation();
      await this.dispose();
      throw error instanceof AgentError ? error : new AgentError('runtime_start_failed');
    } finally { finishPreparation(); }
  }

  async run(options: RuntimeRun): Promise<RunResult> {
    if (this.disposed || !this.rpc || !this.threadId || !this.startOptions) throw new AgentError('runtime_not_started');
    if (this.active) throw new AgentError('runtime_busy');
    if (!options.prompt || Buffer.byteLength(options.prompt) > 262_144 || !['plan', 'execute'].includes(options.mode)) throw new AgentError('invalid_runtime_prompt');
    if (options.signal.aborted) return { status: 'interrupted', threadId: this.threadId };
    let resolve!: (result: RunResult) => void;
    const completed = new Promise<RunResult>(res => { resolve = res; });
    let markReady!: () => void;
    const ready = new Promise<void>(res => { markReady = res; });
    const active: ActiveRun = { options, starting: true, text: '', steps: '', finalMessage: '', items: new Map(), controller: new AbortController(), ready, markReady, notifications: [], resolve };
    this.active = active;
    const abort = () => { void this.interrupt(); };
    options.signal.addEventListener('abort', abort, { once: true });
    const timer = setTimeout(() => { void this.interrupt().then(() => this.finish('unknown')); }, this.settings.runTimeoutMs ?? 15 * 60_000);
    try {
      // Execution starts read-only too. Each write must trigger a concrete runtime
      // escalation; plan approval never becomes an unrestricted workspace tool grant.
      const sandboxPolicy = { type: 'readOnly', networkAccess: false };
      const response = object(await this.rpc.request('turn/start', {
        threadId: this.threadId, input: [{ type: 'text', text: options.prompt, text_elements: [] }], cwd: this.startOptions.workspace,
        approvalPolicy: 'on-request', approvalsReviewer: 'user', sandboxPolicy,
        collaborationMode: { mode: options.mode === 'plan' ? 'plan' : 'default', settings: { model: this.startOptions.model, reasoning_effort: null, developer_instructions: null } },
      }));
      const turnId = identifier(object(response.turn).id);
      if (this.active === active) {
        active.turnId = turnId; active.starting = false;
        active.markReady();
        for (const notification of active.notifications.splice(0)) this.notification(notification.method, notification.raw);
        if (options.signal.aborted) await this.interrupt();
      }
      return await completed;
    } catch (error) {
      this.finish('unknown');
      await this.dispose();
      throw error instanceof AgentError ? error : new AgentError('runtime_run_failed');
    } finally { clearTimeout(timer); options.signal.removeEventListener('abort', abort); }
  }

  private notification(method: string, raw: unknown): void {
    const active = this.active;
    if (!active || !raw || typeof raw !== 'object') return;
    const params = object(raw);
    if (params.threadId !== this.threadId) return;
    // turn/start's response binds identity. Early events cannot invent an active turn.
    if (active.starting) {
      if (active.notifications.length >= 128) throw new AgentError('runtime_early_event_limit');
      active.notifications.push({ method, raw }); return;
    }
    if (method === 'turn/started') return;
    const turnId = typeof params.turnId === 'string' ? params.turnId : params.turn && typeof params.turn === 'object' ? object(params.turn).id : undefined;
    if (turnId !== active.turnId || !active.turnId) return;
    const emit = (type: RuntimeEvent['type'], data: unknown) => active.options.onEvent({ type, turnId: active.turnId, data });
    if (method === 'turn/completed') {
      const status = object(params.turn).status;
      this.finish(status === 'completed' ? 'completed' : status === 'interrupted' ? 'interrupted' : status === 'failed' ? 'failed' : 'unknown');
    } else if (method === 'turn/plan/updated') {
      if (!Array.isArray(params.plan) || params.plan.length > 100) throw new AgentError('invalid_runtime_plan');
      active.steps = params.plan.map(value => { const step = object(value); return typeof step.step === 'string' ? step.step : ''; }).join('\n');
      emit('plan', { explanation: params.explanation, steps: params.plan });
    } else if (method === 'item/plan/delta') {
      if (typeof params.delta !== 'string') throw new AgentError('invalid_runtime_plan');
      active.text += params.delta;
      if (Buffer.byteLength(active.text) > 524_288) throw new AgentError('runtime_plan_limit');
      emit('plan', { text: active.text });
    } else if (method === 'item/started' || method === 'item/completed') {
      const item = object(params.item);
      const itemId = identifier(item.id);
      if (active.items.size >= 4096 && !active.items.has(itemId)) throw new AgentError('runtime_item_limit');
      active.items.set(itemId, item);
      if (item.type === 'plan' && typeof item.text === 'string') { active.text = item.text; emit('plan', { text: item.text }); }
      else if (item.type === 'agentMessage') { if (typeof item.text === 'string') active.finalMessage = item.text; emit('message', { item, completed: method === 'item/completed' }); }
      else if (item.type === 'fileChange') emit('diff', { item, completed: method === 'item/completed' });
      else emit('tool', { item, completed: method === 'item/completed' });
    } else if (method === 'item/agentMessage/delta') emit('message', { itemId: params.itemId, delta: params.delta });
    else if (method === 'turn/diff/updated' || method === 'item/fileChange/patchUpdated') emit('diff', params);
    else if (method === 'thread/tokenUsage/updated') emit('usage', params.tokenUsage);
    else if (method === 'item/commandExecution/outputDelta' || method === 'item/fileChange/outputDelta') emit('tool', { itemId: params.itemId, delta: params.delta });
    else if (method === 'error' && params.willRetry !== true) this.finish('failed');
  }

  private async serverRequest(method: string, raw: unknown, id: string | number): Promise<unknown> {
    if (!['item/commandExecution/requestApproval', 'item/fileChange/requestApproval', 'item/tool/requestUserInput'].includes(method)) throw new AgentError('unsupported_server_request');
    const params = object(raw), active = this.active;
    if (active?.starting) await active.ready;
    if (!active || !active.turnId || params.threadId !== this.threadId || params.turnId !== active.turnId || active.controller.signal.aborted) throw new AgentError('stale_runtime_request');
    const itemId = identifier(params.itemId);
    let kind: ActionRequest['kind'];
    let operation: Record<string, unknown>;
    if (method === 'item/tool/requestUserInput') {
      if (!Array.isArray(params.questions) || !params.questions.length || params.questions.length > 10) throw new AgentError('invalid_runtime_question');
      for (const question of params.questions) { const q = object(question); identifier(q.id); if (typeof q.question !== 'string' || q.question.length > 16_384) throw new AgentError('invalid_runtime_question'); }
      kind = 'question'; operation = { itemId, questions: params.questions };
    } else {
      // Planning never gains mutation rights from an action approval. Network remains denied.
      if (active.options.mode === 'plan' || params.networkApprovalContext || params.kind === 'network') return { decision: 'decline' };
      if (method === 'item/commandExecution/requestApproval') {
        if (typeof params.command !== 'string' || !params.command || params.command.length > 65_536 || typeof params.cwd !== 'string') throw new AgentError('invalid_runtime_command');
        if (!inside(this.startOptions!.workspace, await realpath(params.cwd))) return { decision: 'decline' };
        kind = 'command'; operation = { itemId, command: params.command, cwd: params.cwd, reason: params.reason };
      } else {
        if (params.grantRoot && (typeof params.grantRoot !== 'string' || !inside(this.startOptions!.workspace, await realpath(params.grantRoot)))) return { decision: 'decline' };
        const item = active.items.get(itemId);
        if (!item || item.type !== 'fileChange' || !Array.isArray(item.changes) || !item.changes.length || item.changes.length > 100) throw new AgentError('missing_runtime_file_change');
        for (const value of item.changes) {
          const change = object(value);
          if (typeof change.path !== 'string' || !change.path || change.path.length > 16_384 || typeof change.diff !== 'string') throw new AgentError('invalid_runtime_file_change');
          if (!inside(this.startOptions!.workspace, resolve(this.startOptions!.workspace, change.path))) return { decision: 'decline' };
        }
        kind = 'file'; operation = { itemId, changes: item.changes, reason: params.reason };
      }
    }
    const request: ActionRequest = { id: String(id), turnId: active.turnId, kind, operation };
    if (kind === 'question') active.options.onEvent({ type: 'question', turnId: active.turnId, data: operation });
    let timer: NodeJS.Timeout | undefined;
    let onAbort: (() => void) | undefined;
    const cancelled = new Promise<ApprovalDecision>(resolve => {
      onAbort = () => resolve({ decision: 'cancel' });
      active.controller.signal.addEventListener('abort', onAbort, { once: true });
      timer = setTimeout(() => resolve({ decision: 'cancel' }), this.settings.approvalTimeoutMs ?? 5 * 60_000);
    });
    let decision: ApprovalDecision;
    try { decision = await Promise.race([active.options.onApproval(request), cancelled]); }
    finally { if (timer) clearTimeout(timer); if (onAbort) active.controller.signal.removeEventListener('abort', onAbort); }
    if (this.active !== active || active.controller.signal.aborted) throw new AgentError('stale_runtime_request');
    if (!decision || !['accept', 'decline', 'cancel'].includes(decision.decision)) throw new AgentError('invalid_runtime_decision');
    if (kind === 'question') {
      if (decision.decision !== 'accept') { void this.interrupt(); return { answers: {} }; }
      const answers: Record<string, { answers: string[] }> = {};
      for (const question of params.questions as unknown[]) {
        const questionId = identifier(object(question).id);
        const values = decision.answers?.[questionId];
        if (!Array.isArray(values) || !values.length || values.length > 10 || values.some(value => typeof value !== 'string' || value.length > 16_384)) throw new AgentError('invalid_runtime_answer');
        answers[questionId] = { answers: values };
      }
      return { answers };
    }
    return { decision: decision.decision };
  }

  private finish(status: RunResult['status']): void {
    const active = this.active;
    if (!active) return;
    this.active = undefined;
    active.markReady();
    active.controller.abort();
    const plan = active.options.mode === 'plan' && status === 'completed' ? active.text || active.steps || active.finalMessage : undefined;
    active.resolve({ status, threadId: this.threadId, ...(plan ? { plan } : {}) });
  }

  async interrupt(): Promise<void> {
    const active = this.active;
    if (!active || !this.rpc || !this.threadId) return;
    if (!active.turnId) { await this.dispose(); return; }
    active.controller.abort();
    try { await this.rpc.request('turn/interrupt', { threadId: this.threadId, turnId: active.turnId }, Math.min(this.settings.requestTimeoutMs ?? 30_000, 5_000)); }
    catch { await this.dispose(); return; }
    this.finish('interrupted');
  }

  async dispose(): Promise<void> {
    this.disposed = true;
    this.finish('unknown');
    await this.preparation.drain(this.settings.preparationTimeoutMs);
    await this.rpc?.dispose();
    if (this.startOptions) this.startOptions.credential = '';
  }
}
