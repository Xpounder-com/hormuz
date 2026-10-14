import { createHash, randomUUID } from 'node:crypto';
import { EventEmitter } from 'node:events';
import { join } from 'node:path';
import { BudgetLedger } from './budgets.ts';
import { AgentError, SCHEMA_VERSION } from './types.ts';
import type { ActionRequest, AgentSessionAdapter, ApprovalDecision, Engine, Phase, RuntimeEvent } from './types.ts';
import { SessionStore } from './store.ts';
import { diffWorkspace, assertWorkspacePath } from './workspace.ts';
import { observeWorkspaceSnapshot, observeWorkspaceDiff, cleanupWorkspaceObservers } from './workspace-observer.ts';
import type { WorkspaceSnapshot } from './workspace.ts';
import { InferenceProxy } from './admission.ts';

export class ApplicationScheduler {
  #owner: string | null = null;
  acquire(owner: string): () => void {
    if (this.#owner) throw new AgentError('application_busy');
    this.#owner = owner;
    let released = false;
    return () => { if (!released && this.#owner === owner) { this.#owner = null; released = true; } };
  }
  get activeOwner() { return this.#owner; }
}
export type Connection = {
  provider: 'openai' | 'anthropic'; upstream: string; credential: string; version: string;
  models: string[]; inputMicrousdPerMillion: number; outputMicrousdPerMillion: number;
  maxInputTokens: number; maxOutputTokens: number; requestLimit?: number; allowLoopbackUpstream?: boolean;
};
type Accounting = { spentMicrousd: number; heldMicrousd: number; unknownMicrousd: number; requestCount: number; coverage?: string };
export type AcceptedPlan = { revision: string; text: string; workspaceFingerprint: string; connectionVersion: string };
export type Session = {
  schemaVersion: 1; id: string; profileId: string; projectId: string; workspace: string;
  engine: Engine; model: string; runtimeVersion: string; connectionId: string; cloudAllowed: boolean;
  allowanceMicrousd: number; phase: Phase; threadId?: string; plan?: AcceptedPlan;
  acceptedPlanFingerprint?: string; accounting: Accounting; baseline?: WorkspaceSnapshot;
  createdAt: number; updatedAt: number;
  review?: { observedRevision: string; disposition: 'accepted' | 'rejected' | 'request_changes'; recordedAt: number };
};
type Pending = {
  request: ActionRequest; sessionId: string; fingerprint: string; expiresAt: number;
  resolve: (decision: ApprovalDecision) => void; timer: ReturnType<typeof setTimeout>;
};
type Running = { session: Session; adapter: AgentSessionAdapter; proxy: InferenceProxy; controller: AbortController; secrets: string[] };
export type AgentSessionOptions = {
  profileId: string; store: SessionStore; scheduler: ApplicationScheduler;
  runtimeFactory: (engine: Engine) => AgentSessionAdapter;
  resolveConnection: (id: string) => Promise<Connection>;
  authorizeWorkspace: (projectId: string, workspace: string) => Promise<boolean>;
  authorizeAllowance: (projectId: string, allowanceMicrousd: number) => Promise<boolean>;
  authorizeCloud: (projectId: string) => Promise<boolean>;
  parentAllowances: { profileMicrousd: number; projectMicrousd: number };
  runTimeoutMs?: number; approvalTimeoutMs?: number;
  /** Trusted test/application wiring only; production defaults to OS-sandboxed observation. */
  observeWorkspace?: (root: string, privateConfigRoot: string) => Promise<WorkspaceSnapshot>;
  observeDiff?: (baseline: WorkspaceSnapshot, privateConfigRoot: string) => Promise<Awaited<ReturnType<typeof diffWorkspace>>>;
};
const digest = (value: unknown) => createHash('sha256').update(JSON.stringify(value)).digest('hex');
const id = (value: string) => {
  if (typeof value !== 'string' || !/^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$/.test(value)) throw new AgentError('invalid_identity');
  return value;
};
const finiteAmount = (value: number) => {
  if (!Number.isSafeInteger(value) || value <= 0) throw new AgentError('invalid_allowance');
  return value;
};

/** Host-only owner. UI events carry no credentials, raw RPC access or implicit grants. */
export class AgentSessions extends EventEmitter {
  #options: AgentSessionOptions;
  #running: Running | null = null;
  #pending = new Map<string, Pending>();
  #releaseLease: (() => Promise<void>) | null = null;
  #closed = false;
  #mutating = false;
  #activeController: AbortController | null = null;
  #quiescent: Promise<void> = Promise.resolve();
  #finishRun: (() => void) | null = null;
  #releaseRun: (() => void) | null = null;
  #mutationDone: Promise<void> = Promise.resolve();
  #shutdown: Promise<void> | null = null;
  #budgets: BudgetLedger;
  #reviewBaseline: { sessionId: string; snapshot: WorkspaceSnapshot } | null = null;
  #reads = new Set<Promise<unknown>>();
  #scrub<T>(value: T, secrets: string[]): T {
    let encoded = JSON.stringify(value);
    for (const secret of secrets) if (secret.length >= 8) encoded = encoded.split(secret).join('[REDACTED]');
    return JSON.parse(encoded) as T;
  }
  constructor(options: AgentSessionOptions) { super(); id(options.profileId); finiteAmount(options.parentAllowances.profileMicrousd); finiteAmount(options.parentAllowances.projectMicrousd); this.#options = options; this.#budgets = new BudgetLedger(join(options.store.root, 'usage')); }
  #snapshot(root: string) { this.#available(); return (this.#options.observeWorkspace ?? observeWorkspaceSnapshot)(root, this.#options.store.root); }
  #diff(baseline: WorkspaceSnapshot) { this.#available(); return (this.#options.observeDiff ?? observeWorkspaceDiff)(baseline, this.#options.store.root); }
  async initialize() {
    if (this.#closed) throw new AgentError('host_unavailable');
    if (this.#releaseLease) return;
    this.#releaseLease = await this.#options.store.acquireLease();
  }
  #available() {
    if (this.#closed || !this.#releaseLease) throw new AgentError('host_unavailable');
  }
  async #mutate<T>(action: () => Promise<T>): Promise<T> {
    this.#available();
    if (this.#mutating || this.#options.scheduler.activeOwner) throw new AgentError('application_busy');
    this.#mutating = true;
    let finish!: () => void;
    this.#mutationDone = new Promise(resolve => { finish = resolve; });
    try { return await action(); } finally { this.#mutating = false; finish(); }
  }
  create(input: Parameters<AgentSessions['createInternal']>[0]) { return this.#mutate(() => this.createInternal(input)); }
  plan(sessionId: string, projectId: string, prompt: string) { return this.#mutate(() => this.planInternal(sessionId, projectId, prompt)); }
  acceptPlan(sessionId: string, projectId: string, revision: string) { return this.#mutate(() => this.acceptPlanInternal(sessionId, projectId, revision)); }
  revisePlan(sessionId: string, projectId: string, revision: string, text: string) {
    return this.#mutate(async () => {
      if (typeof text !== 'string' || !text.trim() || Buffer.byteLength(text) > 64 * 1024) throw new AgentError('invalid_plan');
      const session = await this.#load(sessionId, projectId);
      if (session.phase !== 'ready' || session.plan?.revision !== revision) throw new AgentError('stale_plan');
      const current = await this.#snapshot(session.workspace), connection = await this.#connection(session);
      if (current.fingerprint !== session.plan.workspaceFingerprint || connection.version !== session.plan.connectionVersion) throw new AgentError('stale_plan');
      session.plan = { ...session.plan, text, revision: digest({ ...session.plan, revision: undefined, text, engine: session.engine, model: session.model, runtime: session.runtimeVersion, workspace: session.workspace }) };
      delete session.acceptedPlanFingerprint;
      await this.#save(session); return this.#view(session);
    });
  }
  rejectPlan(sessionId: string, projectId: string, revision: string) {
    return this.#mutate(async () => {
      const session = await this.#load(sessionId, projectId);
      if (session.phase !== 'ready' || session.plan?.revision !== revision) throw new AgentError('stale_plan');
      delete session.plan; delete session.acceptedPlanFingerprint; session.phase = 'idle';
      await this.#save(session); return this.#view(session);
    });
  }
  execute(sessionId: string, projectId: string, revision: string) { return this.#mutate(() => this.executeInternal(sessionId, projectId, revision)); }
  resume(sessionId: string, projectId: string) { return this.#mutate(() => this.resumeInternal(sessionId, projectId)); }
  remove(sessionId: string, projectId: string) { return this.#mutate(() => this.removeInternal(sessionId, projectId)); }
  private async createInternal(input: { projectId: string; workspace: string; engine: Engine; model: string; connectionId: string; cloudAllowed: boolean; allowanceMicrousd: number }) {
    this.#available(); id(input.projectId); id(input.connectionId);
    if (!['codex', 'claude'].includes(input.engine) || typeof input.model !== 'string' || !input.model || input.model.length > 128) throw new AgentError('invalid_engine_model');
    if (!await this.#options.authorizeWorkspace(input.projectId, input.workspace)) throw new AgentError('workspace_not_authorized');
    finiteAmount(input.allowanceMicrousd);
    if (!await this.#options.authorizeAllowance(input.projectId, input.allowanceMicrousd)) throw new AgentError('allowance_not_authorized');
    const baseline = await this.#snapshot(input.workspace);
    if (this.#options.store.root === baseline.root || this.#options.store.root.startsWith(baseline.root + '/')) throw new AgentError('store_inside_workspace');
    const adapter = this.#options.runtimeFactory(input.engine);
    if (adapter.engine !== input.engine) { await adapter.dispose(); throw new AgentError('runtime_engine_mismatch'); }
    const session: Session = {
      schemaVersion: SCHEMA_VERSION, id: randomUUID(), profileId: this.#options.profileId,
      projectId: input.projectId, workspace: baseline.root, engine: input.engine, model: input.model,
      runtimeVersion: adapter.version, connectionId: input.connectionId, cloudAllowed: input.cloudAllowed,
      allowanceMicrousd: input.allowanceMicrousd, phase: 'idle',
      accounting: { spentMicrousd: 0, heldMicrousd: 0, unknownMicrousd: 0, requestCount: 0 },
      createdAt: Date.now(), updatedAt: Date.now(),
    };
    await adapter.dispose();
    await this.#budgets.checkpoint(session.id, session.profileId, session.projectId, session.accounting, this.#options.parentAllowances);
    await this.#save(session);
    return this.#view(session);
  }
  async #load(sessionId: string, projectId: string): Promise<Session> {
    this.#available(); id(sessionId); id(projectId);
    const record = await this.#options.store.load(sessionId, this.#options.profileId, projectId);
    const session = record as Session;
    if (typeof session.connectionId !== 'string' || typeof session.cloudAllowed !== 'boolean' || !Number.isSafeInteger(session.allowanceMicrousd) || session.allowanceMicrousd <= 0 || !Number.isFinite(session.createdAt) || !Number.isFinite(session.updatedAt) || !session.accounting) throw new AgentError('corrupt_session');
    id(session.connectionId);
    for (const value of Object.values(session.accounting)) if (typeof value !== 'string' && (!Number.isSafeInteger(value) || Number(value) < 0)) throw new AgentError('corrupt_session');
    for (const key of ['spentMicrousd', 'heldMicrousd', 'unknownMicrousd', 'requestCount'] as const) if (!Number.isSafeInteger(session.accounting[key]) || session.accounting[key] < 0) throw new AgentError('corrupt_session');
    if (session.accounting.unknownMicrousd > session.accounting.heldMicrousd) throw new AgentError('corrupt_session');
    if (session.plan && (typeof session.plan.text !== 'string' || typeof session.plan.revision !== 'string' || typeof session.plan.workspaceFingerprint !== 'string' || typeof session.plan.connectionVersion !== 'string')) throw new AgentError('corrupt_session');
    // The pre-egress journal is authoritative if a crash preceded the content-store checkpoint.
    session.accounting = await this.#budgets.forSession(session.id, session.profileId, session.projectId);
    this.#available();
    return session;
  }
  #view(session: Session) {
    const { threadId: _thread, baseline: _baseline, acceptedPlanFingerprint: _grant, ...view } = session;
    return structuredClone(view);
  }
  async get(sessionId: string, projectId: string) { return this.#view(await this.#load(sessionId, projectId)); }
  private async resumeInternal(sessionId: string, projectId: string) {
    const session = await this.#load(sessionId, projectId);
    if (this.#running?.session.id === sessionId) throw new AgentError('session_busy');
    const connection = await this.#connection(session);
    const adapter = this.#options.runtimeFactory(session.engine);
    try {
      if (adapter.version !== session.runtimeVersion) throw new AgentError('runtime_version_changed');
      const current = await this.#snapshot(session.workspace);
      if (current.root !== session.workspace) throw new AgentError('workspace_changed');
      if (session.plan && connection.version !== session.plan.connectionVersion) session.plan = undefined;
      if (session.plan && current.fingerprint !== session.plan.workspaceFingerprint) session.plan = undefined;
      session.phase = ['planning', 'executing', 'unknown'].includes(session.phase) ? 'unknown' : session.plan ? 'ready' : 'idle';
      // A resumed history never carries live tool grants or plan-execution authority.
      delete session.acceptedPlanFingerprint;
      await this.#save(session);
      return this.#view(session);
    } finally { await adapter.dispose(); }
  }
  async #connection(session: Session) {
    if (!session.cloudAllowed) throw new AgentError('cloud_not_authorized');
    if (!await this.#options.authorizeCloud(session.projectId)) throw new AgentError('cloud_not_authorized');
    if (!await this.#options.authorizeAllowance(session.projectId, session.allowanceMicrousd)) throw new AgentError('allowance_not_authorized');
    if (!await this.#options.authorizeWorkspace(session.projectId, session.workspace)) throw new AgentError('workspace_not_authorized');
    const connection = await this.#options.resolveConnection(session.connectionId);
    if ((session.engine === 'codex' ? 'openai' : 'anthropic') !== connection.provider || !connection.models.includes(session.model)) throw new AgentError('connection_model_mismatch');
    if (!connection.credential || !connection.version) throw new AgentError('credential_unavailable');
    if (session.accounting.spentMicrousd + session.accounting.heldMicrousd >= session.allowanceMicrousd) throw new AgentError('allowance_exhausted');
    return connection;
  }
  async #save(session: Session) {
    session.updatedAt = Date.now();
    const record = JSON.parse(JSON.stringify(session));
    // Persist provenance hashes, not copies of arbitrary working-tree credentials/content.
    if (record.baseline) for (const file of Object.values(record.baseline.files) as Record<string, unknown>[]) delete file.text;
    await this.#options.store.save(record);
  }
  private async planInternal(sessionId: string, projectId: string, prompt: string) {
    if (typeof prompt !== 'string' || !prompt.trim() || Buffer.byteLength(prompt) > 64 * 1024) throw new AgentError('invalid_prompt');
    const session = await this.#load(sessionId, projectId);
    const before = await this.#snapshot(session.workspace);
    session.plan = undefined; delete session.acceptedPlanFingerprint;
    const result = await this.#run(session, 'plan', prompt, before);
    if (result.status === 'completed' && result.plan?.trim()) {
      if (Buffer.byteLength(result.plan) > 64 * 1024) throw new AgentError('plan_too_large');
      const after = await this.#snapshot(session.workspace);
      if (before.fingerprint !== after.fingerprint) { session.phase = 'unknown'; await this.#save(session); throw new AgentError('planning_workspace_changed'); }
      const connection = await this.#connection(session);
      const plan = { text: result.plan, workspaceFingerprint: after.fingerprint, connectionVersion: connection.version };
      session.plan = { ...plan, revision: digest({ ...plan, engine: session.engine, model: session.model, runtime: session.runtimeVersion, workspace: session.workspace }) };
      session.phase = 'ready'; await this.#save(session);
    }
    return this.#view(session);
  }
  private async acceptPlanInternal(sessionId: string, projectId: string, revision: string) {
    const session = await this.#load(sessionId, projectId);
    if (session.phase !== 'ready' || !session.plan || session.plan.revision !== revision) throw new AgentError('stale_plan');
    const connection = await this.#connection(session);
    const current = await this.#snapshot(session.workspace);
    if (current.fingerprint !== session.plan.workspaceFingerprint || connection.version !== session.plan.connectionVersion) throw new AgentError('stale_plan');
    session.acceptedPlanFingerprint = revision;
    await this.#save(session); return this.#view(session);
  }
  private async executeInternal(sessionId: string, projectId: string, revision: string) {
    const session = await this.#load(sessionId, projectId);
    if (session.phase !== 'ready' || !session.plan || session.plan.revision !== revision || session.acceptedPlanFingerprint !== revision) throw new AgentError('plan_not_accepted');
    const before = await this.#snapshot(session.workspace);
    const connection = await this.#connection(session);
    if (before.fingerprint !== session.plan.workspaceFingerprint || connection.version !== session.plan.connectionVersion) throw new AgentError('stale_plan');
    delete session.acceptedPlanFingerprint;
    session.baseline = before;
    this.#reviewBaseline = { sessionId, snapshot: before };
    await this.#run(session, 'execute', `Execute this accepted plan within the authorized workspace. Ask for concrete tool permission when required.\n\n${session.plan.text}`, before);
    return this.#view(session);
  }
  async #run(session: Session, mode: 'plan' | 'execute', prompt: string, _before: WorkspaceSnapshot) {
    this.#available();
    const release = this.#options.scheduler.acquire(session.id);
    let adapter: AgentSessionAdapter | undefined; let proxy: InferenceProxy | undefined;
    const controller = new AbortController();
    this.#activeController = controller;
    this.#quiescent = new Promise(resolve => { this.#finishRun = resolve; });
    const timeout = setTimeout(() => controller.abort(), this.#options.runTimeoutMs ?? 300_000);
    try {
      const connection = await this.#connection(session);
      adapter = this.#options.runtimeFactory(session.engine);
      if (adapter.engine !== session.engine) throw new AgentError('runtime_engine_mismatch');
      if (adapter.version !== session.runtimeVersion) throw new AgentError('runtime_version_changed');
      proxy = new InferenceProxy({ ...connection, model: session.model, allowanceMicrousd: session.allowanceMicrousd, initialAccounting: session.accounting,
        authorizeDispatch: async () => {
          if (!session.cloudAllowed || !await this.#options.authorizeCloud(session.projectId)) throw new AgentError('cloud_not_authorized');
          if (!await this.#options.authorizeWorkspace(session.projectId, session.workspace)) throw new AgentError('workspace_not_authorized');
          if (!await this.#options.authorizeAllowance(session.projectId, session.allowanceMicrousd)) throw new AgentError('allowance_not_authorized');
          const current = await this.#options.resolveConnection(session.connectionId);
          if (current.version !== connection.version || current.credential !== connection.credential || current.upstream !== connection.upstream || current.provider !== connection.provider || !current.models.includes(session.model)) throw new AgentError('stale_connection');
        },
        onAccounting: async accounting => {
          await this.#budgets.checkpoint(session.id, session.profileId, session.projectId, accounting, this.#options.parentAllowances);
          session.accounting = accounting; await this.#save(session);
        } });
      const endpoint = await proxy.start();
      const configDir = await this.#options.store.runtimeDirectory(session.id);
      const started = await adapter.start({ workspace: session.workspace, model: session.model, configDir, ...endpoint, threadId: session.threadId, maxOutputTokens: connection.maxOutputTokens });
      if (controller.signal.aborted || this.#closed) throw new AgentError('interrupted');
      session.threadId = started.threadId; session.phase = mode === 'plan' ? 'planning' : 'executing';
      this.#running = { session, adapter, proxy, controller, secrets: [connection.credential, endpoint.credential] }; await this.#save(session);
      const abort = () => { this.#clearApprovals(session.id); void adapter?.interrupt().catch(() => {}); };
      controller.signal.addEventListener('abort', abort, { once: true });
      try {
        const result = await adapter.run({ sessionId: session.id, prompt, mode, signal: controller.signal,
          onEvent: event => this.#event(session, event), onApproval: request => this.#approve(session, mode, request, controller.signal) });
        session.phase = result.status; if (result.threadId) session.threadId = result.threadId;
        return this.#scrub(result, this.#running.secrets);
      } finally { controller.signal.removeEventListener('abort', abort); }
    } catch (error) {
      session.phase = controller.signal.aborted ? 'interrupted' : 'unknown';
      if (error instanceof AgentError) throw error;
      const failure = new AgentError('runtime_failed');
      Object.defineProperty(failure, 'cause', { value: error, enumerable: false });
      throw failure;
    } finally {
      clearTimeout(timeout); this.#clearApprovals(session.id);
      // Stop dispatch and owned processes before settling or relinquishing ownership.
      let cleanupFailed = false;
      try { await adapter?.dispose(); } catch { cleanupFailed = true; }
      try { await proxy?.stop(); } catch { cleanupFailed = true; }
      if (proxy) session.accounting = proxy.summary();
      if (cleanupFailed && adapter && proxy) { session.phase = 'unknown'; this.#running ??= { session, adapter, proxy, controller, secrets: [] }; this.#releaseRun = release; }
      else { this.#running = null; release(); this.#activeController = null; }
      try { await this.#save(session); } finally { this.#finishRun?.(); this.#finishRun = null; }
      if (cleanupFailed) throw new AgentError('cleanup_required');
    }
  }
  #event(session: Session, event: RuntimeEvent) {
    if (this.#running?.session.id !== session.id) return;
    if (Buffer.byteLength(JSON.stringify(event)) > 1024 * 1024) { this.#running.controller.abort(); return; }
    this.emit('event', { schemaVersion: 1, sessionId: session.id, projectId: session.projectId, ...this.#scrub(event, this.#running.secrets) });
  }
  async #approve(session: Session, mode: 'plan' | 'execute', request: ActionRequest, signal: AbortSignal): Promise<ApprovalDecision> {
    if (signal.aborted || this.#running?.session.id !== session.id || !request.id || !request.turnId) return { decision: 'decline' };
    if (mode === 'plan' && request.kind !== 'question') return { decision: 'decline' };
    if (request.kind === 'network') return { decision: 'decline' }; // Inference is mediated separately; shell network has no default grant.
    const running = this.#running;
    const operation = request.operation;
    const paths: string[] = [];
    for (const key of ['cwd', 'path', 'file_path', 'grantRoot']) {
      if (operation[key] !== undefined) {
        if (typeof operation[key] !== 'string') return { decision: 'decline' };
        paths.push(operation[key] as string);
      }
    }
    if (operation.changes !== undefined) {
      if (!Array.isArray(operation.changes) || operation.changes.length > 500) return { decision: 'decline' };
      for (const change of operation.changes) {
        if (!change || typeof change !== 'object' || typeof change.path !== 'string') return { decision: 'decline' };
        paths.push(change.path);
      }
    }
    for (const path of paths) try { await assertWorkspacePath(session.workspace, path); } catch { return { decision: 'decline' }; }
    if (signal.aborted || this.#running !== running) return { decision: 'decline' };
    if (Buffer.byteLength(JSON.stringify(request)) > 128 * 1024 || this.#pending.size >= 8) return { decision: 'decline' };
    const fingerprint = digest({ sessionId: session.id, plan: session.plan?.revision, ...request });
    const approvalId = randomUUID();
    return await new Promise(resolve => {
      const timer = setTimeout(() => this.#resolve(approvalId, { decision: 'decline' }), this.#options.approvalTimeoutMs ?? 60_000);
      this.#pending.set(approvalId, { request, sessionId: session.id, fingerprint, expiresAt: Date.now() + (this.#options.approvalTimeoutMs ?? 60_000), resolve, timer });
      this.emit('approval', { schemaVersion: 1, sessionId: session.id, projectId: session.projectId, approvalId, fingerprint, ...this.#scrub(request, running.secrets) });
    });
  }
  async decide(sessionId: string, projectId: string, approvalId: string, fingerprint: string, decision: ApprovalDecision) {
    await this.#load(sessionId, projectId);
    const pending = this.#pending.get(approvalId);
    if (!pending || pending.sessionId !== sessionId || pending.fingerprint !== fingerprint || pending.expiresAt < Date.now() || this.#running?.session.id !== sessionId) throw new AgentError('stale_approval');
    if (!['accept', 'decline', 'cancel'].includes(decision.decision)) throw new AgentError('invalid_decision');
    const running = this.#running;
    const connection = await this.#connection(running.session);
    if (this.#pending.get(approvalId) !== pending || this.#running !== running || running.controller.signal.aborted) throw new AgentError('stale_approval');
    if (running.session.plan && connection.version !== running.session.plan.connectionVersion) throw new AgentError('stale_connection');
    const operation = pending.request.operation;
    const paths = ['cwd', 'path', 'file_path', 'grantRoot'].flatMap(key => typeof operation[key] === 'string' ? [operation[key] as string] : []);
    if (Array.isArray(operation.changes)) for (const change of operation.changes) if (change && typeof change.path === 'string') paths.push(change.path);
    for (const path of paths) await assertWorkspacePath(running.session.workspace, path);
    if (this.#pending.get(approvalId) !== pending || this.#running !== running || running.controller.signal.aborted) throw new AgentError('stale_approval');
    this.#resolve(approvalId, structuredClone(decision));
  }
  #resolve(approvalId: string, decision: ApprovalDecision) {
    const pending = this.#pending.get(approvalId); if (!pending) return;
    this.#pending.delete(approvalId); clearTimeout(pending.timer); pending.resolve(decision);
  }
  #clearApprovals(sessionId: string) { for (const [key, value] of this.#pending) if (value.sessionId === sessionId) this.#resolve(key, { decision: 'cancel' }); }
  async interrupt(sessionId: string, projectId: string) {
    await this.#load(sessionId, projectId);
    if (this.#running?.session.id !== sessionId) throw new AgentError('session_not_running');
    this.#running.controller.abort(); await this.#running.adapter.interrupt();
  }
  async review(sessionId: string, projectId: string) {
    this.#available();
    if (this.#reads.size >= 1) throw new AgentError('application_busy');
    const reading = this.reviewInternal(sessionId, projectId);
    this.#reads.add(reading);
    try { return await reading; } finally { this.#reads.delete(reading); }
  }
  private async reviewInternal(sessionId: string, projectId: string) {
    const session = await this.#load(sessionId, projectId);
    if (this.#running?.session.id === sessionId) throw new AgentError('session_busy');
    if (!session.baseline) throw new AgentError('no_execution_baseline');
    const live = this.#reviewBaseline?.sessionId === sessionId ? this.#reviewBaseline.snapshot : undefined;
    return { ...await this.#diff(live ?? session.baseline), baselineTextAvailable: Boolean(live) };
  }
  acceptReview(sessionId: string, projectId: string, observedRevision: string) { return this.recordReview(sessionId, projectId, observedRevision, 'accepted'); }
  async recordReview(sessionId: string, projectId: string, observedRevision: string, disposition: 'accepted' | 'rejected' | 'request_changes') {
    return await this.#mutate(async () => {
      if (!['accepted', 'rejected', 'request_changes'].includes(disposition)) throw new AgentError('invalid_review_disposition');
      const review = await this.review(sessionId, projectId);
      if (review.observedRevision !== observedRevision) throw new AgentError('stale_review');
      const session = await this.#load(sessionId, projectId);
      const record = { ...session, review: { observedRevision, disposition, recordedAt: Date.now() } };
      await this.#options.store.save(JSON.parse(JSON.stringify(record)));
      this.emit('review', { schemaVersion: 1, sessionId, projectId, observedRevision, disposition });
      return { observedRevision, disposition, executionAuthority: false };
    });
  }
  private async removeInternal(sessionId: string, projectId: string) {
    this.#available(); if (this.#running?.session.id === sessionId) throw new AgentError('session_busy');
    await this.#options.store.remove(sessionId, this.#options.profileId, projectId);
    if (this.#reviewBaseline?.sessionId === sessionId) this.#reviewBaseline = null;
  }
  dispose() {
    if (this.#shutdown) return this.#shutdown;
    this.#closed = true;
    const shutdown = this.disposeInternal();
    this.#shutdown = shutdown;
    // Failed owned-process cleanup keeps the lease; another call can retry cleanup.
    void shutdown.catch(() => { if (this.#shutdown === shutdown) this.#shutdown = null; });
    return shutdown;
  }
  private async disposeInternal() {
    this.#activeController?.abort();
    if (this.#running) await this.#running.adapter.interrupt().catch(() => {});
    await this.#quiescent;
    await this.#mutationDone;
    await Promise.allSettled([...this.#reads]);
    await cleanupWorkspaceObservers(this.#options.store.root);
    if (this.#running) {
      await this.#running.adapter.dispose(); await this.#running.proxy.stop();
      this.#releaseRun?.(); this.#releaseRun = null; this.#running = null;
    }
    await this.#releaseLease?.(); this.#releaseLease = null; this.removeAllListeners();
    this.#reviewBaseline = null;
  }
}
