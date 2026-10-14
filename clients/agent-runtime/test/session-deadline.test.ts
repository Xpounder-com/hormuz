import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtemp, mkdir, realpath, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { AgentSessions, ApplicationScheduler } from '../src/sessions.ts';
import type { AgentSessionOptions, Connection, Session } from '../src/sessions.ts';
import { SessionStore } from '../src/store.ts';
import { snapshotWorkspace } from '../src/workspace.ts';
import type { AgentSessionAdapter, RuntimeRun, RuntimeStart, RunResult } from '../src/types.ts';

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((finish, fail) => { resolve = finish; reject = fail; });
  return { promise, resolve, reject };
}
const connection: Connection = {
  provider: 'openai', upstream: 'https://api.openai.com/v1', credential: 'unused-fixture-credential',
  version: 'fixture-connection', models: ['fixture'], inputMicrousdPerMillion: 1_000_000,
  outputMicrousdPerMillion: 1_000_000, maxInputTokens: 100_000, maxOutputTokens: 100,
};
class Runtime implements AgentSessionAdapter {
  readonly engine = 'codex' as const; readonly version = 'fixture';
  starts = 0; runs = 0; disposed = false; options?: RuntimeStart;
  async start(options: RuntimeStart) { this.starts++; this.options = options; return { threadId: 'fixture-thread' }; }
  async run(_options: RuntimeRun): Promise<RunResult> { this.runs++; return { status: 'completed', plan: 'Write a result file.' }; }
  async interrupt() {}
  async dispose() { this.disposed = true; }
}
type Authority = 'cloud' | 'allowance' | 'workspace' | 'connection';
async function fixture(timeout = 250) {
  const dir = await realpath(await mkdtemp(join(tmpdir(), 'hormuz-deadline-')));
  const workspace = join(dir, 'workspace'); await mkdir(workspace);
  const store = new SessionStore(join(dir, 'state')), scheduler = new ApplicationScheduler();
  const runtimes: Runtime[] = [];
  const blocked = deferred<boolean>(), entered = deferred<void>();
  const state: { block?: Authority; signal?: AbortSignal; factory?: () => Runtime; rates?: Partial<Connection> } = {};
  const authority = async (kind: Authority, signal?: AbortSignal) => {
    if (state.block !== kind) return true;
    state.signal = signal; entered.resolve(); return await blocked.promise;
  };
  const options: AgentSessionOptions = {
    profileId: 'profile', store, scheduler, runTimeoutMs: timeout,
    runtimeFactory: () => { const runtime = state.factory?.() ?? new Runtime(); runtimes.push(runtime); return runtime; },
    observeWorkspace: snapshotWorkspace,
    authorizeWorkspace: async (_project, _workspace, signal) => await authority('workspace', signal),
    authorizeAllowance: async (_project, _allowance, signal) => await authority('allowance', signal),
    authorizeCloud: async (_project, signal) => await authority('cloud', signal),
    resolveConnection: async (_id, signal) => { await authority('connection', signal); return { ...connection, ...state.rates }; },
    parentAllowances: { profileMicrousd: 10_000_000, projectMicrousd: 5_000_000 },
  };
  const manager = new AgentSessions(options); await manager.initialize();
  const create = () => manager.create({ projectId: 'project', workspace, engine: 'codex', model: 'fixture',
    connectionId: 'connection', cloudAllowed: true, allowanceMicrousd: 1_000_000 });
  return { dir, workspace, store, scheduler, manager, options, create, state, entered, blocked, runtimes,
    cleanup: async () => { blocked.resolve(false); await manager.dispose(); await rm(dir, { recursive: true, force: true }); } };
}

for (const kind of ['cloud', 'allowance', 'workspace', 'connection'] as const) {
  test('planning deadline bounds a never-resolving ' + kind + ' callback and ignores its late authorization', { timeout: 3000 }, async () => {
    const f = await fixture();
    try {
      const session = await f.create(); f.state.block = kind;
      const planning = f.manager.plan(session.id, 'project', 'Work.');
      const rejected = assert.rejects(planning, { code: 'interrupted' });
      await f.entered.promise; await rejected;
      assert.equal(f.state.signal?.aborted, true); assert.equal(f.scheduler.activeOwner, null);
      assert.equal(f.runtimes.length, 1); assert.equal(f.runtimes[0].starts, 0);
      const saved = await f.store.load(session.id, 'profile', 'project');
      assert.equal(saved.phase, 'interrupted'); assert.equal(saved.runtimeAttempts, 0);
      assert.equal(saved.acceptedPlanFingerprint, undefined);
      f.blocked.resolve(true); await new Promise<void>(resolve => setImmediate(resolve));
      assert.equal(f.runtimes.length, 1); assert.equal(f.runtimes[0].starts, 0);
      await f.manager.dispose(); const release = await new SessionStore(f.store.root).acquireLease(); await release();
    } finally { await f.cleanup(); }
  });
  test('execution deadline revokes the accepted plan while ' + kind + ' authorization remains pending', { timeout: 3000 }, async () => {
    const f = await fixture();
    try {
      const session = await f.create(), planned = await f.manager.plan(session.id, 'project', 'Work.');
      await f.manager.acceptPlan(session.id, 'project', planned.plan!.revision);
      f.state.block = kind;
      await assert.rejects(f.manager.execute(session.id, 'project', planned.plan!.revision), { code: 'interrupted' });
      const saved = await f.store.load(session.id, 'profile', 'project');
      assert.equal(saved.acceptedPlanFingerprint, undefined); assert.equal(saved.runtimeAttempts, 1);
      assert.equal(saved.phase, 'interrupted'); assert.equal(f.scheduler.activeOwner, null);
      f.blocked.resolve(true); await new Promise<void>(resolve => setImmediate(resolve));
      assert.equal(f.runtimes.reduce((total, runtime) => total + runtime.starts, 0), 1);
    } finally { await f.cleanup(); }
  });
}

for (const kind of ['workspace', 'allowance'] as const) {
  test('create deadline bounds ' + kind + ' authorization without creating a late session', { timeout: 3000 }, async () => {
    const f = await fixture();
    try {
      f.state.block = kind; await assert.rejects(f.create(), { code: 'interrupted' });
      f.blocked.resolve(true); await new Promise<void>(resolve => setImmediate(resolve));
      assert.deepEqual(await f.store.list('profile', 'project'), []); assert.equal(f.runtimes.length, 0);
      await f.manager.dispose(); const release = await new SessionStore(f.store.root).acquireLease(); await release();
    } finally { await f.cleanup(); }
  });
}

test('host disposal interrupts a pending authority callback immediately without waiting for its resolution', { timeout: 3000 }, async () => {
  const f = await fixture(10_000);
  try {
    const session = await f.create(); f.state.block = 'connection';
    const planning = f.manager.plan(session.id, 'project', 'Work.'), rejected = assert.rejects(planning, { code: 'interrupted' });
    await f.entered.promise; await f.manager.dispose(); await rejected;
    assert.equal(f.state.signal?.aborted, true); assert.equal(f.scheduler.activeOwner, null);
    const release = await new SessionStore(f.store.root).acquireLease(); await release();
    f.blocked.resolve(true); await new Promise<void>(resolve => setImmediate(resolve));
    assert.equal(f.runtimes.length, 1);
  } finally { await f.cleanup(); }
});

test('successful planning closes a stalled approval decision after its request expires, before clearing the operation deadline', { timeout: 3000 }, async () => {
  const f = await fixture(1000), authorizing = deferred<void>(), authorization = deferred<boolean>();
  let calls = 0, signal: AbortSignal | undefined, deciding: Promise<string> | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  f.options.approvalTimeoutMs = 75;
  f.options.authorizeCloud = async (_project, currentSignal) => {
    if (++calls !== 2) return true;
    signal = currentSignal; authorizing.resolve(); return await authorization.promise;
  };
  class QuestionRuntime extends Runtime {
    override async run(options: RuntimeRun): Promise<RunResult> {
      this.runs++;
      await options.onApproval({ id: 'question', turnId: 'turn', kind: 'question', operation: {} });
      return { status: 'completed', plan: 'Write a result file.' };
    }
  }
  try {
    const session = await f.create(); f.state.factory = () => new QuestionRuntime();
    f.manager.on('approval', request => {
      deciding = f.manager.decide(session.id, 'project', request.approvalId, request.fingerprint, { decision: 'accept' })
        .then(() => 'resolved', error => error.code ?? error.message);
    });
    const planning = f.manager.plan(session.id, 'project', 'Plan work.');
    await authorizing.promise;
    assert.equal(signal?.aborted, false);
    const planned = await planning;
    assert.equal(planned.phase, 'ready'); assert.equal(calls, 3);
    // The operation completed before its 1000ms deadline. A cleared timer alone
    // would leave decide pending until this independent 200ms regression bound.
    const result = await Promise.race([deciding!, new Promise<string>(resolve => { timer = setTimeout(() => resolve('pending'), 200); })]);
    clearTimeout(timer);
    assert.equal(result, 'interrupted'); assert.equal(signal?.aborted, true);
    assert.equal(f.scheduler.activeOwner, null); assert.equal(f.runtimes[1].disposed, true);
    assert.equal((await f.store.load(session.id, 'profile', 'project')).acceptedPlanFingerprint, undefined);
    await f.manager.dispose();
    const release = await new SessionStore(f.store.root).acquireLease(); await release();
    authorization.resolve(true); await deciding;
    assert.equal((await f.store.load(session.id, 'profile', 'project')).acceptedPlanFingerprint, undefined);
  } finally { clearTimeout(timer); authorization.resolve(false); await deciding; await f.cleanup(); }
});

for (const mode of ['accept', 'revise', 'resume'] as const) {
  test(mode + ' deadline cannot preserve previously accepted execution authority', { timeout: 3000 }, async () => {
    const f = await fixture();
    try {
      const session = await f.create(), planned = await f.manager.plan(session.id, 'project', 'Work.');
      const revision = planned.plan!.revision; await f.manager.acceptPlan(session.id, 'project', revision);
      f.state.block = 'connection';
      const operation = mode === 'accept' ? f.manager.acceptPlan(session.id, 'project', revision)
        : mode === 'revise' ? f.manager.revisePlan(session.id, 'project', revision, 'Changed plan.')
          : f.manager.resume(session.id, 'project');
      await assert.rejects(operation, { code: 'interrupted' });
      assert.equal((await f.store.load(session.id, 'profile', 'project')).acceptedPlanFingerprint, undefined);
      assert.equal(f.scheduler.activeOwner, null);
      f.blocked.reject(new Error('late rejected host callback'));
      await new Promise<void>(resolve => setImmediate(resolve));
    } finally { await f.cleanup(); }
  });
}

test('the operation deadline also bounds authority revalidation after the adapter has completed its plan', { timeout: 3000 }, async () => {
  const f = await fixture(), validated = deferred<Connection>(); let calls = 0;
  f.options.resolveConnection = async () => ++calls === 1 ? connection : await validated.promise;
  try {
    const session = await f.create();
    await assert.rejects(f.manager.plan(session.id, 'project', 'Work.'), { code: 'interrupted' });
    const saved = await f.store.load(session.id, 'profile', 'project');
    assert.equal(saved.phase, 'interrupted'); assert.equal(saved.plan, undefined);
    assert.equal(f.scheduler.activeOwner, null); assert.equal(f.runtimes[1].disposed, true);
    validated.resolve(connection); await new Promise<void>(resolve => setImmediate(resolve));
    assert.equal((await f.store.load(session.id, 'profile', 'project')).plan, undefined);
  } finally { validated.resolve(connection); await f.cleanup(); }
});

for (const stage of ['snapshot', 'grant_write'] as const) {
  test('acceptance cancellation during an owned ' + stage + ' cannot persist a grant or release its lease early', { timeout: 5000 }, async () => {
    // Setup must succeed independently of CI filesystem latency. Keep the short
    // deadline on the deliberately blocked acceptance operation under test.
    const f = await fixture(2000), started = deferred<void>(), finish = deferred<void>();
    const save = f.store.save.bind(f.store); let signal: AbortSignal | undefined;
    try {
      const session = await f.create(), planned = await f.manager.plan(session.id, 'project', 'Work.');
      await f.manager.acceptPlan(session.id, 'project', planned.plan!.revision);
      f.options.runTimeoutMs = 250;
      f.options.authorizeWorkspace = async (_project, _workspace, currentSignal) => { signal = currentSignal; return true; };
      if (stage === 'snapshot') f.options.observeWorkspace = async root => { started.resolve(); await finish.promise; return await snapshotWorkspace(root); };
      else f.store.save = async record => { if (record.acceptedPlanFingerprint) { started.resolve(); await finish.promise; } await save(record); };
      const accepting = f.manager.acceptPlan(session.id, 'project', planned.plan!.revision);
      const rejected = assert.rejects(accepting, { code: 'interrupted' });
      await started.promise;
      if (!signal!.aborted) await new Promise<void>(resolve => signal!.addEventListener('abort', () => resolve(), { once: true }));
      await assert.rejects(new SessionStore(f.store.root).acquireLease(), /SESSION_STORE_IN_USE/);
      finish.resolve(); await rejected;
      assert.equal((await f.store.load(session.id, 'profile', 'project')).acceptedPlanFingerprint, undefined);
    } finally { finish.resolve(); f.store.save = save; await f.cleanup(); }
  });
}

test('startup deadline bounds pending adapter history metadata and retains failed cleanup until retry', { timeout: 3000 }, async () => {
  const f = await fixture();
  const history = deferred<undefined>(); let disposals = 0;
  class WaitingHistory extends Runtime {
    async historyPolicy() { return await history.promise; }
    override async dispose() { disposals++; if (disposals === 1) throw new Error('uncertain-process-cleanup'); this.disposed = true; }
  }
  try {
    const session = await f.create(); f.state.factory = () => new WaitingHistory();
    await assert.rejects(f.manager.plan(session.id, 'project', 'Work.'), { code: 'cleanup_required' });
    assert.equal(f.scheduler.activeOwner, session.id);
    await assert.rejects(new SessionStore(f.store.root).acquireLease(), /SESSION_STORE_IN_USE/);
    await f.manager.dispose(); assert.equal(f.scheduler.activeOwner, null);
    const release = await new SessionStore(f.store.root).acquireLease(); await release();
    history.resolve(undefined); await new Promise<void>(resolve => setImmediate(resolve));
    assert.equal(f.runtimes[1].starts, 0); assert.equal(disposals, 2);
  } finally { history.resolve(undefined); await f.cleanup(); }
});

test('startup timeout holds scheduler and state lease until owned adapter cleanup finishes, then prevents late startup', { timeout: 3000 }, async () => {
  const f = await fixture(), startup = deferred<void>(), cleaning = deferred<void>(), cleaned = deferred<void>();
  let ownedProcesses = 0;
  class WaitingStart extends Runtime {
    override async start(options: RuntimeStart) {
      this.starts++; this.options = options; ownedProcesses++;
      await startup.promise;
      if (this.disposed) throw new Error('runtime_closed');
      return { threadId: 'late-thread' };
    }
    override async dispose() { this.disposed = true; cleaning.resolve(); await cleaned.promise; ownedProcesses = 0; }
  }
  try {
    const session = await f.create(); f.state.factory = () => new WaitingStart();
    const planning = f.manager.plan(session.id, 'project', 'Work.'), rejected = assert.rejects(planning, { code: 'interrupted' });
    await cleaning.promise; assert.equal(ownedProcesses, 1); assert.equal(f.scheduler.activeOwner, session.id);
    await assert.rejects(new SessionStore(f.store.root).acquireLease(), /SESSION_STORE_IN_USE/);
    cleaned.resolve(); await rejected; assert.equal(ownedProcesses, 0); assert.equal(f.scheduler.activeOwner, null);
    await f.manager.dispose(); const release = await new SessionStore(f.store.root).acquireLease(); await release();
    startup.resolve(); await new Promise<void>(resolve => setImmediate(resolve));
    assert.equal(f.runtimes[1].runs, 0); assert.equal(ownedProcesses, 0);
    const saved = await f.store.load(session.id, 'profile', 'project');
    assert.equal(saved.runtimeAttempts, 1); assert.equal(saved.threadId, undefined); assert.equal(saved.phase, 'interrupted');
  } finally { cleaned.resolve(); startup.resolve(); await f.cleanup(); }
});

test('run deadline bounds a noncooperative adapter and refunds an admitted request blocked in dispatch authorization', { timeout: 3000 }, async () => {
  const f = await fixture(), dispatch = deferred<boolean>(); let calls = 0, signal: AbortSignal | undefined;
  f.options.authorizeCloud = async (_project, currentSignal) => {
    if (++calls === 1) return true;
    signal = currentSignal; return await dispatch.promise;
  };
  const requestFinished = deferred<void>();
  class RequestingRuntime extends Runtime {
    override async run(_options: RuntimeRun): Promise<RunResult> {
      this.runs++;
      void fetch(this.options!.endpoint + '/responses', { method: 'POST',
        headers: { authorization: 'Bearer ' + this.options!.credential, 'content-type': 'application/json' },
        body: JSON.stringify({ model: 'fixture', input: 'hello' }), signal: AbortSignal.timeout(1000) })
        .then(async response => { await response.text(); }).catch(() => {}).finally(() => requestFinished.resolve());
      return await new Promise<RunResult>(() => {});
    }
  }
  try {
    const session = await f.create(); f.state.factory = () => new RequestingRuntime();
    assert.equal((await f.manager.plan(session.id, 'project', 'Work.')).phase, 'interrupted');
    assert.equal(signal?.aborted, true); assert.equal(f.scheduler.activeOwner, null);
    const saved = await f.store.load(session.id, 'profile', 'project');
    assert.deepEqual(saved.accounting, { spentMicrousd: 0, heldMicrousd: 0, unknownMicrousd: 0, requestCount: 1, coverage: 'enforced' });
    await requestFinished.promise;
    dispatch.resolve(true); await new Promise<void>(resolve => setImmediate(resolve));
    assert.equal(f.runtimes[1].disposed, true);
  } finally { dispatch.resolve(false); await f.cleanup(); }
});

test('late events and results from a timed-out adapter cannot enter a later run of the same session', { timeout: 3000 }, async () => {
  const f = await fixture(), late = deferred<RunResult>(); let staleRun: RuntimeRun | undefined;
  class LateRuntime extends Runtime {
    override async run(options: RuntimeRun) { this.runs++; staleRun = options; return await late.promise; }
  }
  try {
    const session = await f.create(); f.state.factory = () => new LateRuntime();
    assert.equal((await f.manager.plan(session.id, 'project', 'First plan.')).phase, 'interrupted');
    f.state.factory = () => new Runtime();
    const planned = await f.manager.plan(session.id, 'project', 'Second plan.');
    const events: unknown[] = []; f.manager.on('event', event => events.push(event));
    staleRun!.onEvent({ type: 'plan', data: 'Late obsolete plan.' });
    late.resolve({ status: 'completed', plan: 'Late obsolete plan.' });
    await new Promise<void>(resolve => setImmediate(resolve));
    assert.deepEqual(events, []);
    assert.equal(((await f.store.load(session.id, 'profile', 'project')) as Session).plan?.revision, planned.plan!.revision);
  } finally { late.resolve({ status: 'interrupted' }); await f.cleanup(); }
});

test('zero or invalid configured token rates fail before native startup', { timeout: 3000 }, async () => {
  const f = await fixture();
  try {
    const session = await f.create();
    for (const [key, value] of [['inputMicrousdPerMillion', 0], ['outputMicrousdPerMillion', 0], ['inputMicrousdPerMillion', -1], ['outputMicrousdPerMillion', 1.5]] as const) {
      f.state.rates = { [key]: value };
      await assert.rejects(f.manager.plan(session.id, 'project', 'Work.'), { code: 'invalid_connection_rates' });
    }
    assert.equal(f.runtimes.length, 1); assert.equal((await f.store.load(session.id, 'profile', 'project')).runtimeAttempts, 0);
  } finally { await f.cleanup(); }
});
