import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, readFile, rm, realpath } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { once } from 'node:events';
import { AgentSessions, ApplicationScheduler } from '../src/sessions.ts';
import { SessionStore } from '../src/store.ts';
import { snapshotWorkspace, diffWorkspace } from '../src/workspace.ts';
import { createAgentBridge, registerAgentIPC } from '../src/bridge.ts';
import type { AgentIPCEvent } from '../src/bridge.ts';
import type { AgentSessionAdapter, RuntimeStart, RuntimeRun, RunResult } from '../src/types.ts';

class FakeRuntime implements AgentSessionAdapter {
  readonly engine = 'codex' as const;
  readonly version = 'fixture-1';
  options?: RuntimeStart;
  async start(options: RuntimeStart) { this.options = options; return { threadId: options.threadId ?? 'fixture-thread' }; }
  async run(options: RuntimeRun): Promise<RunResult> {
    if (options.mode === 'plan') {
      const denied = await options.onApproval({ id: 'forbidden-write', turnId: 'plan-turn', kind: 'file', operation: { file_path: join(this.options!.workspace, 'result.txt') } });
      assert.equal(denied.decision, 'decline');
      options.onEvent({ type: 'plan', turnId: 'plan-turn', data: 'Write result.txt with the accepted result.' });
      return { status: 'completed', plan: 'Write result.txt with the accepted result.' };
    }
    const decision = await options.onApproval({ id: 'write-1', turnId: 'exec-turn', kind: 'file', operation: { file_path: join(this.options!.workspace, 'result.txt') } });
    if (options.signal.aborted || decision.decision === 'cancel') return { status: 'interrupted' };
    if (decision.decision === 'accept') await writeFile(join(this.options!.workspace, 'result.txt'), 'accepted\n');
    return { status: 'completed' };
  }
  async interrupt() {}
  async dispose() {}
}
async function fixture(runtimeFactory: () => AgentSessionAdapter = () => new FakeRuntime(), authorizeCloud = async () => true) {
  const dir = await realpath(await mkdtemp(join(tmpdir(), 'hormuz-session-test-')));
  const workspace = join(dir, 'workspace'); await mkdir(workspace); await writeFile(join(workspace, 'user.txt'), 'pre-existing dirty user content\n');
  const store = new SessionStore(join(dir, 'store'));
  const scheduler = new ApplicationScheduler();
  const manager = new AgentSessions({ profileId: 'profile', store, scheduler, runtimeFactory,
    observeWorkspace: snapshotWorkspace, observeDiff: diffWorkspace,
    authorizeWorkspace: async (project, root) => project === 'project' && root === workspace,
    authorizeAllowance: async (_project, cap) => cap <= 10_000_000,
    authorizeCloud,
    parentAllowances: { profileMicrousd: 10_000_000, projectMicrousd: 5_000_000 },
    resolveConnection: async () => ({ provider: 'openai', upstream: 'https://api.openai.com/v1', credential: 'fixture-key-not-used', version: 'connection-1', models: ['fixture'], inputMicrousdPerMillion: 1_000_000, outputMicrousdPerMillion: 1_000_000, maxInputTokens: 20_000, maxOutputTokens: 100 }),
    approvalTimeoutMs: 5000, runTimeoutMs: 10_000,
  });
  await manager.initialize();
  const session = await manager.create({ projectId: 'project', workspace, engine: 'codex', model: 'fixture', connectionId: 'connection', cloudAllowed: true, allowanceMicrousd: 1_000_000 });
  return { dir, workspace, manager, store, scheduler, session, cleanup: async () => { await manager.dispose(); await rm(dir, { recursive: true, force: true }); } };
}
test('complete plan, action approval, actual diff and explicit resume journey', async () => {
  const f = await fixture();
  try {
    await assert.rejects(f.manager.execute(f.session.id, 'project', 'unaccepted'), /plan_not_accepted/);
    const planned = await f.manager.plan(f.session.id, 'project', 'Add a result file.');
    assert.equal(planned.phase, 'ready'); assert.ok(planned.plan);
    await f.manager.acceptPlan(f.session.id, 'project', planned.plan!.revision);
    const approval = once(f.manager, 'approval');
    const running = f.manager.execute(f.session.id, 'project', planned.plan!.revision);
    const [request] = await approval;
    await assert.rejects(f.manager.decide(f.session.id, 'project', request.approvalId, 'wrong', { decision: 'accept' }), /stale_approval/);
    await f.manager.decide(f.session.id, 'project', request.approvalId, request.fingerprint, { decision: 'accept' });
    await assert.rejects(f.manager.decide(f.session.id, 'project', request.approvalId, request.fingerprint, { decision: 'accept' }), /stale_approval/);
    assert.equal((await running).phase, 'completed');
    const diff = await f.manager.review(f.session.id, 'project');
    assert.deepEqual(diff.changes.map(c => c.path), ['result.txt']); assert.equal(diff.authorship, 'unknown');
    assert.equal(await readFile(join(f.workspace, 'user.txt'), 'utf8'), 'pre-existing dirty user content\n');
    const review = await f.manager.acceptReview(f.session.id, 'project', diff.observedRevision);
    assert.equal(review.executionAuthority, false);
    const rejected = await f.manager.recordReview(f.session.id, 'project', diff.observedRevision, 'rejected');
    assert.equal(rejected.executionAuthority, false); assert.equal(rejected.disposition, 'rejected');
    assert.equal(await readFile(join(f.workspace, 'result.txt'), 'utf8'), 'accepted\n');
    await writeFile(join(f.workspace, 'result.txt'), 'concurrent edit');
    await assert.rejects(f.manager.acceptReview(f.session.id, 'project', diff.observedRevision), /stale_review/);
    const resumed = await f.manager.resume(f.session.id, 'project');
    assert.equal(resumed.plan, undefined);
    const saved = await f.store.load(f.session.id, 'profile', 'project');
    assert.equal(saved.threadId, 'fixture-thread'); assert.equal(saved.acceptedPlanFingerprint, undefined);
    const exported = await f.store.export(f.session.id, 'profile', 'project'); assert.equal(exported.threadId, undefined);
  } finally { await f.cleanup(); }
});
test('session consent cannot override the trusted host cloud restriction', async () => {
  let allowed = false;
  const f = await fixture(() => new FakeRuntime(), async () => allowed);
  try {
    assert.equal(f.session.cloudAllowed, true);
    await assert.rejects(f.manager.plan(f.session.id, 'project', 'Work.'), /cloud_not_authorized/);
    assert.equal((await f.manager.get(f.session.id, 'project')).accounting.requestCount, 0);
    allowed = true;
    assert.equal((await f.manager.plan(f.session.id, 'project', 'Work.')).phase, 'ready');
  } finally { await f.cleanup(); }
});
test('editing or rejecting a plan clears its prior execution authority', async () => {
  const f = await fixture();
  try {
    const planned = await f.manager.plan(f.session.id, 'project', 'Add a result file.');
    await f.manager.acceptPlan(f.session.id, 'project', planned.plan!.revision);
    const changed = await f.manager.revisePlan(f.session.id, 'project', planned.plan!.revision, 'Do a different bounded operation.');
    assert.notEqual(changed.plan!.revision, planned.plan!.revision);
    await assert.rejects(f.manager.execute(f.session.id, 'project', changed.plan!.revision), /plan_not_accepted/);
    await assert.rejects(f.manager.rejectPlan(f.session.id, 'project', planned.plan!.revision), /stale_plan/);
    await f.manager.rejectPlan(f.session.id, 'project', changed.plan!.revision);
    assert.equal((await f.manager.get(f.session.id, 'project')).phase, 'idle');
    assert.equal((await f.manager.get(f.session.id, 'project')).plan, undefined);
  } finally { await f.cleanup(); }
});
test('IPC validates the current document and child-frame authority before project lookup', async () => {
  const f = await fixture();
  try {
    let handler!: (event: AgentIPCEvent, input: unknown) => Promise<unknown>;
    let removed = false;
    const unregister = registerAgentIPC({ handle: (_channel, fn) => { handler = fn; }, removeHandler: () => { removed = true; } }, {
      sessions: f.manager, senderPolicy: () => ({ projectIds: new Set(['project']), workspaceRoots: new Set([f.workspace]) }),
      authorizeEvent: event => event.senderFrame?.url === 'app://hormuz/project' && event.senderFrame.parent === null,
    });
    const input = { schemaVersion: 1, method: 'get', projectId: 'project', sessionId: f.session.id };
    await assert.rejects(handler({ sender: { id: 10 }, senderFrame: { url: 'https://external.example', parent: null } }, input), /unauthorized_document/);
    await assert.rejects(handler({ sender: { id: 10 }, senderFrame: { url: 'app://hormuz/project', parent: {} } }, input), /unauthorized_document/);
    await handler({ sender: { id: 10 }, senderFrame: { url: 'app://hormuz/project', parent: null } }, input);
    unregister(); assert.equal(removed, true);
  } finally { await f.cleanup(); }
});
test('shutdown waits for a pending mutation before releasing the private store lease', async () => {
  const f = await fixture();
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  let entered!: () => void;
  const waiting = new Promise<void>(resolve => { entered = resolve; });
  const save = f.store.save.bind(f.store);
  f.store.save = async record => { entered(); await gate; await save(record); };
  try {
    const creating = f.manager.create({ projectId: 'project', workspace: f.workspace, engine: 'codex', model: 'fixture', connectionId: 'connection', cloudAllowed: true, allowanceMicrousd: 1_000_000 });
    await waiting;
    let disposed = false;
    const shutdown = f.manager.dispose().then(() => { disposed = true; });
    await assert.rejects(f.store.acquireLease(), /LEASE/);
    assert.equal(disposed, false);
    release(); await creating; await shutdown;
    const unlock = await f.store.acquireLease(); await unlock();
    await assert.rejects(f.manager.initialize(), /host_unavailable/);
  } finally { release(); await f.cleanup(); }
});
test('uncertain process cleanup retains ownership and permits a later cleanup retry', async () => {
  let failures = 0;
  class FlakyRuntime extends FakeRuntime {
    override async dispose() { if (this.options && failures++ < 2) throw new Error('fixture cleanup failure'); }
  }
  const f = await fixture(() => new FlakyRuntime());
  try {
    await assert.rejects(f.manager.plan(f.session.id, 'project', 'Work.'), /cleanup_required/);
    assert.equal(f.scheduler.activeOwner, f.session.id);
    await assert.rejects(f.manager.dispose(), /fixture cleanup failure/);
    await assert.rejects(f.store.acquireLease(), /LEASE/);
    await f.manager.dispose();
    assert.equal(f.scheduler.activeOwner, null);
    const unlock = await f.store.acquireLease(); await unlock();
  } finally { await f.cleanup(); }
});
test('shutdown owns an in-flight review and prevents its observer starting after lease release', async () => {
  const f = await fixture();
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  let entered!: () => void;
  const waiting = new Promise<void>(resolve => { entered = resolve; });
  const load = f.store.load.bind(f.store);
  try {
    const plan = await f.manager.plan(f.session.id, 'project', 'Work.');
    await f.manager.acceptPlan(f.session.id, 'project', plan.plan!.revision);
    const approval = once(f.manager, 'approval');
    const executing = f.manager.execute(f.session.id, 'project', plan.plan!.revision);
    const [request] = await approval;
    await f.manager.decide(f.session.id, 'project', request.approvalId, request.fingerprint, { decision: 'accept' }); await executing;
    f.store.load = async (...args) => { entered(); await gate; return await load(...args); };
    const reviewing = f.manager.review(f.session.id, 'project');
    const rejected = assert.rejects(reviewing, /host_unavailable/);
    await waiting;
    const shutdown = f.manager.dispose();
    await assert.rejects(f.store.acquireLease(), /LEASE/);
    release(); await rejected; await shutdown;
    const unlock = await f.store.acquireLease(); await unlock();
  } finally { release(); f.store.load = load; await f.cleanup(); }
});
test('deny and cancel prevent file changes; interrupt resolves pending requests', async () => {
  const f = await fixture();
  try {
    const planned = await f.manager.plan(f.session.id, 'project', 'Add a result file.');
    await f.manager.acceptPlan(f.session.id, 'project', planned.plan!.revision);
    const approval = once(f.manager, 'approval'); const running = f.manager.execute(f.session.id, 'project', planned.plan!.revision);
    const [request] = await approval;
    await f.manager.decide(f.session.id, 'project', request.approvalId, request.fingerprint, { decision: 'decline' }); await running;
    assert.equal((await f.manager.review(f.session.id, 'project')).changes.length, 0);
    const next = await f.manager.plan(f.session.id, 'project', 'Add a result file.');
    await f.manager.acceptPlan(f.session.id, 'project', next.plan!.revision);
    const pending = once(f.manager, 'approval'); const active = f.manager.execute(f.session.id, 'project', next.plan!.revision);
    await pending; await f.manager.interrupt(f.session.id, 'project');
    assert.equal((await active).phase, 'interrupted');
    assert.equal((await f.manager.review(f.session.id, 'project')).changes.length, 0);
    assert.equal(f.scheduler.activeOwner, null);
  } finally { await f.cleanup(); }
});
test('stale plan, cross-project access, parent allowance and shared scheduler fail closed', async () => {
  const f = await fixture();
  try {
    const plan = await f.manager.plan(f.session.id, 'project', 'Work.');
    await f.manager.acceptPlan(f.session.id, 'project', plan.plan!.revision);
    await writeFile(join(f.workspace, 'user.txt'), 'changed');
    await assert.rejects(f.manager.execute(f.session.id, 'project', plan.plan!.revision), /stale_plan/);
    await assert.rejects(f.manager.get(f.session.id, 'other'), /SCOPE_DENIED/);
    await assert.rejects(f.manager.create({ projectId: 'project', workspace: f.workspace, engine: 'codex', model: 'fixture', connectionId: 'connection', cloudAllowed: true, allowanceMicrousd: 20_000_000 }), /allowance_not_authorized/);
    const release = f.scheduler.acquire('plain-chat');
    try { await assert.rejects(f.manager.plan(f.session.id, 'project', 'Work.'), /application_busy/); } finally { release(); }
  } finally { await f.cleanup(); }
});
test('host IPC rejects arbitrary authority, unknown senders and out-of-scope workspace', async () => {
  const f = await fixture();
  try {
    const bridge = createAgentBridge({ sessions: f.manager, senderPolicy: sender => sender === 10 ? { projectIds: new Set(['project']), workspaceRoots: new Set([f.workspace]) } : undefined });
    await assert.rejects(bridge(11, {}), /unauthorized_sender/);
    await assert.rejects(bridge(10, { schemaVersion: 1, method: 'get', projectId: 'other', sessionId: f.session.id }), /unauthorized_project/);
    await assert.rejects(bridge(10, { schemaVersion: 1, method: 'get', projectId: 'project', sessionId: f.session.id, credential: 'injected' }), /invalid_request/);
    await assert.rejects(bridge(10, { schemaVersion: 1, method: 'rpc', projectId: 'project' }), /unsupported_operation/);
    const view = await bridge(10, { schemaVersion: 1, method: 'get', projectId: 'project', sessionId: f.session.id }) as Record<string, unknown>;
    assert.equal(view.threadId, undefined); assert.equal(view.profileId, 'profile');
    const otherWindow = createAgentBridge({ sessions: f.manager, senderPolicy: () => ({ projectIds: new Set(['project']), workspaceRoots: new Set(['/unrelated']) }) });
    await assert.rejects(otherWindow(10, { schemaVersion: 1, method: 'get', projectId: 'project', sessionId: f.session.id }), /unauthorized_workspace/);
  } finally { await f.cleanup(); }
});
