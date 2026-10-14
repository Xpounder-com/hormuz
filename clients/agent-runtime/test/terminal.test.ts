import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { mkdtemp, mkdir, readFile, realpath, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { PassThrough, Writable } from 'node:stream';
import test from 'node:test';
import { AgentSessions, ApplicationScheduler } from '../src/sessions.ts';
import { SessionStore } from '../src/store.ts';
import { diffWorkspace, snapshotWorkspace } from '../src/workspace.ts';
import { runTerminal, sanitizeTerminalText } from '../src/terminal.ts';
import type { TerminalOptions } from '../src/terminal.ts';
import type { AgentSessionAdapter, ApprovalDecision, RuntimeRun, RuntimeStart, RunResult } from '../src/types.ts';

class TerminalDriver extends EventEmitter {
  input = new PassThrough();
  text = '';
  cursor = 0;
  stall = false;
  output = new Writable({ write: (chunk: Buffer, _encoding, callback) => {
    this.text += chunk.toString('utf8'); this.emit('output');
    if (!this.stall) callback();
  } });
  constructor() { super(); this.output.on('error', () => {}); }
  async waitFor(marker: string) {
    if (!this.text.includes(marker, this.cursor)) {
      await new Promise<void>((resolve, reject) => {
        const timer = setTimeout(() => { this.removeListener('output', check); reject(new Error(`Missing output: ${marker}\n${this.text.slice(-2000)}`)); }, 3000);
        const check = () => { if (this.text.includes(marker, this.cursor)) { clearTimeout(timer); this.removeListener('output', check); resolve(); } };
        this.on('output', check); check();
      });
    }
    this.cursor = this.text.indexOf(marker, this.cursor) + marker.length;
    // A real user types after the terminal has finished rendering the prompt.
    await new Promise<void>(resolve => setImmediate(resolve));
  }
  send(line: string) { this.input.write(line + '\n'); }
  async prompt() { await this.waitFor('hormuz> '); }
  close() { this.input.destroy(); this.output.destroy(); }
}

type Behavior = (options: RuntimeRun, start: RuntimeStart) => Promise<RunResult>;
async function fixture(behavior?: Behavior, approvalTimeoutMs = 1000) {
  const directory = await realpath(await mkdtemp(join(tmpdir(), 'hormuz-terminal-test-')));
  const workspace = join(directory, 'workspace'); await mkdir(workspace); await writeFile(join(workspace, 'existing.txt'), 'Keep my dirty work.\n');
  const store = new SessionStore(join(directory, 'private'));
  let observer = snapshotWorkspace;
  let cloud = false, starts = 0, interrupts = 0, disposals = 0;
  const decisions: ApprovalDecision[] = [];
  class Runtime implements AgentSessionAdapter {
    readonly engine = 'codex' as const;
    readonly version = 'terminal-fixture-1';
    startOptions?: RuntimeStart;
    async start(options: RuntimeStart) { starts++; this.startOptions = options; return { threadId: options.threadId ?? 'fixture-thread' }; }
    async run(options: RuntimeRun): Promise<RunResult> {
      if (behavior) return await behavior(options, this.startOptions!);
      if (options.mode === 'plan') {
        options.onEvent({ type: 'message', data: '\x1b[31mExploring\x1b[0m\x1b]52;c;ZXhpbA==\x07\x00 safely.' });
        options.onEvent({ type: 'plan', data: 'Create result.txt with the approved text.' });
        return { status: 'completed', plan: 'Create result.txt with the approved text.' };
      }
      const decision = await options.onApproval({ id: 'file-1', turnId: 'execute-1', kind: 'file', operation: {
        cwd: workspace, changes: [{ path: join(workspace, 'result.txt'), content: 'Approved result.\n' }],
      } });
      decisions.push(decision);
      if (options.signal.aborted || decision.decision === 'cancel') return { status: 'interrupted' };
      if (decision.decision === 'accept') await writeFile(join(workspace, 'result.txt'), 'Approved result.\n');
      return { status: 'completed' };
    }
    async interrupt() { interrupts++; }
    async dispose() { disposals++; }
  }
  const manager = new AgentSessions({ profileId: 'profile', store, scheduler: new ApplicationScheduler(), runtimeFactory: () => new Runtime(),
    authorizeWorkspace: async (project, root) => project === 'project' && root === workspace,
    authorizeAllowance: async (_project, amount) => amount <= 1_000_000,
    authorizeCloud: async () => cloud,
    parentAllowances: { profileMicrousd: 2_000_000, projectMicrousd: 2_000_000 },
    resolveConnection: async id => {
      assert.equal(id, 'provider');
      return { provider: 'openai', upstream: 'https://api.openai.com/v1', credential: 'fixture-key-never-used', version: 'fixture-connection-1', models: ['fixture'],
        inputMicrousdPerMillion: 1_000_000, outputMicrousdPerMillion: 1_000_000, maxInputTokens: 1000, maxOutputTokens: 100 };
    },
    observeWorkspace: root => observer(root), observeDiff: diffWorkspace, approvalTimeoutMs, runTimeoutMs: 3000,
  });
  const driver = new TerminalDriver();
  const listSessions = async () => {
    const rows = await store.list('profile', 'project');
    return rows.map(row => ({ id: row.id, phase: row.phase, model: row.model, engine: row.engine, updatedAt: Number(row.updatedAt) }));
  };
  const options: TerminalOptions = { sessions: manager, projectId: 'project', workspace, engine: 'codex', model: 'fixture', allowanceMicrousd: 1_000_000,
    listSessions, onCloudConsent: allowed => { cloud = allowed; }, input: driver.input, output: driver.output };
  return { workspace, store, manager, driver, options, listSessions, decisions,
    setObserver: (next: typeof snapshotWorkspace) => { observer = next; },
    counters: () => ({ cloud, starts, interrupts, disposals }),
    cleanup: async () => { driver.close(); await manager.dispose(); await rm(directory, { recursive: true, force: true }); },
  };
}

async function makePlan(f: Awaited<ReturnType<typeof fixture>>) {
  await f.driver.prompt(); f.driver.send('Create a result file.');
  await f.driver.waitFor('Allow cloud inference for this launch?'); f.driver.send('yes');
  await f.driver.waitFor('Use /accept, then /execute'); await f.driver.prompt();
  return (await f.listSessions())[0]!.id;
}
async function accept(f: Awaited<ReturnType<typeof fixture>>) {
  f.driver.send('/accept'); await f.driver.waitFor('Plan accepted.'); await f.driver.prompt();
}

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>(done => { resolve = done; });
  return { promise, resolve };
}

test('terminal wires plan, explicit accept, file approval, diff, review and resume without model requests', { timeout: 10_000 }, async () => {
  const f = await fixture(); const running = runTerminal(f.options);
  try {
    const id = await makePlan(f);
    f.driver.send('/execute'); await f.driver.waitFor('Error: plan_not_accepted.'); await f.driver.prompt();
    await accept(f);
    f.driver.send(`/resume ${id}`); await f.driver.waitFor('Any plan needs a fresh /accept.'); await f.driver.prompt();
    f.driver.send('/execute'); await f.driver.waitFor('Error: plan_not_accepted.'); await f.driver.prompt();
    await accept(f); f.driver.send('/execute');
    await f.driver.waitFor('Approve this action once?');
    assert.ok(f.driver.text.includes(join(f.workspace, 'result.txt'))); assert.ok(f.driver.text.includes(`Working directory: ${f.workspace}`));
    f.driver.send('yes'); await f.driver.waitFor('Phase: completed.'); await f.driver.prompt();
    assert.equal(await readFile(join(f.workspace, 'result.txt'), 'utf8'), 'Approved result.\n');
    assert.equal(await readFile(join(f.workspace, 'existing.txt'), 'utf8'), 'Keep my dirty work.\n');
    f.driver.send('/diff'); await f.driver.waitFor('added: result.txt'); await f.driver.waitFor('Changes: 1.'); await f.driver.prompt();
    f.driver.send('/review accept'); await f.driver.waitFor('Review accepted recorded'); await f.driver.prompt();
    f.driver.send('/sessions'); await f.driver.waitFor(id); await f.driver.prompt();
    f.driver.send('/quit'); assert.equal(await running, 0);
    assert.deepEqual(f.decisions, [{ decision: 'accept' }]);
    assert.equal(f.counters().cloud, false); assert.equal(f.driver.input.listenerCount('data'), 0);
    assert.equal(f.manager.listenerCount('approval'), 0); assert.equal(f.manager.listenerCount('event'), 0);
    assert.equal(/[\x1b\x00\x07]/.test(f.driver.text), false); assert.ok(f.driver.text.includes('Exploring safely.'));
    const unlock = await f.store.acquireLease(); await unlock();
  } finally { f.driver.input.end(); await running; await f.cleanup(); }
});

test('blank action response denies once, and revising or rejecting clears acceptance', { timeout: 10_000 }, async () => {
  const f = await fixture(); const running = runTerminal(f.options);
  try {
    await makePlan(f); await accept(f);
    f.driver.send('/revise Create a different result file.'); await f.driver.waitFor('Plan revised; acceptance cleared.'); await f.driver.prompt();
    f.driver.send('/execute'); await f.driver.waitFor('Error: plan_not_accepted.'); await f.driver.prompt();
    await accept(f); f.driver.send('/execute'); await f.driver.waitFor('Approve this action once?'); f.driver.send('');
    await f.driver.waitFor('Declined.'); await f.driver.waitFor('Phase: completed.'); await f.driver.prompt();
    assert.deepEqual(f.decisions, [{ decision: 'decline' }]); await assert.rejects(readFile(join(f.workspace, 'result.txt')), /ENOENT/);
    f.driver.send('/plan Create another plan.'); await f.driver.waitFor('Use /accept, then /execute'); await f.driver.prompt();
    await accept(f); f.driver.send('/reject'); await f.driver.waitFor('Plan rejected.'); await f.driver.prompt();
    f.driver.send('/execute'); await f.driver.waitFor('Error: plan_not_accepted.'); await f.driver.prompt();
    f.driver.send('/quit'); assert.equal(await running, 0);
  } finally { f.driver.input.end(); await running; await f.cleanup(); }
});

test('cloud consent is explicit, defaults to deny, and queued yes cannot approve it', { timeout: 10_000 }, async () => {
  const f = await fixture(); const running = runTerminal(f.options);
  try {
    await f.driver.prompt(); f.driver.input.write('Plan a change.\nyes\n');
    await f.driver.waitFor('Allow cloud inference for this launch?');
    assert.equal(f.counters().starts, 0); f.driver.send('');
    await f.driver.waitFor('Cloud inference declined.'); await f.driver.prompt();
    assert.equal(f.counters().starts, 0); assert.deepEqual(await f.listSessions(), []);
    f.driver.send('/quit'); assert.equal(await running, 0);
  } finally { f.driver.input.end(); await running; await f.cleanup(); }
});

test('engine questions share one broker and collect explicit answers in sequence', { timeout: 10_000 }, async () => {
  let answer: ApprovalDecision | undefined;
  const f = await fixture(async options => {
    assert.equal(options.mode, 'plan');
    answer = await options.onApproval({ id: 'question-1', turnId: 'planning', kind: 'question', operation: { questions: [
      { id: 'format', question: 'Choose a format.', options: [{ label: 'Text' }, { label: 'JSON' }] },
      { id: 'purpose', question: 'What is the purpose?', options: [] },
    ] } });
    return { status: 'completed', plan: 'Make the requested text report.' };
  });
  const running = runTerminal({ ...f.options, initialPrompt: 'Prepare a report.' });
  try {
    await f.driver.waitFor('Allow cloud inference for this launch?'); f.driver.send('yes');
    await f.driver.waitFor('Answer explicitly'); f.driver.send('1');
    await f.driver.waitFor('Answer explicitly'); f.driver.send('Show the actual measurements.');
    await f.driver.waitFor('Use /accept, then /execute'); await f.driver.prompt();
    assert.deepEqual(answer, { decision: 'accept', answers: { format: ['Text'], purpose: ['Show the actual measurements.'] } });
    f.driver.send('/quit'); assert.equal(await running, 0);
  } finally { f.driver.input.end(); await running; await f.cleanup(); }
});

test('expired approval releases its input prompt and cannot steal the next REPL command', { timeout: 10_000 }, async () => {
  const f = await fixture(undefined, 40); const running = runTerminal(f.options);
  try {
    await makePlan(f); await accept(f); f.driver.send('/execute'); await f.driver.waitFor('Approve this action once?');
    await f.driver.waitFor('Phase: completed.'); await f.driver.prompt();
    f.driver.send('/status'); await f.driver.waitFor('Plan accepted in this launch: no'); await f.driver.prompt();
    assert.deepEqual(f.decisions, [{ decision: 'decline' }]);
    f.driver.send('/quit'); assert.equal(await running, 0);
  } finally { f.driver.input.end(); await running; await f.cleanup(); }
});

test('approval expiry releases only that prompt before another action in the same run', { timeout: 10_000 }, async () => {
  const decisions: ApprovalDecision[] = [];
  const f = await fixture(async (options, start) => {
    if (options.mode === 'plan') return { status: 'completed', plan: 'Run two individually approved commands.' };
    for (const id of ['expired-command', 'next-command']) decisions.push(await options.onApproval({ id, turnId: 'same-turn', kind: 'command', operation: { command: `echo ${id}`, cwd: start.workspace } }));
    return { status: 'completed' };
  }, 100);
  const running = runTerminal(f.options);
  try {
    await makePlan(f); await accept(f); f.driver.send('/execute');
    await f.driver.waitFor('Approve this action once?');
    await f.driver.waitFor('next-command'); await f.driver.waitFor('Approve this action once?'); f.driver.send('yes');
    await f.driver.waitFor('Phase: completed.'); await f.driver.prompt();
    assert.deepEqual(decisions, [{ decision: 'decline' }, { decision: 'accept' }]);
    f.driver.send('/quit'); assert.equal(await running, 0);
  } finally { f.driver.input.end(); await running; await f.cleanup(); }
});

test('EOF while approval waits cancels work, awaits disposal, and releases private store lease', { timeout: 10_000 }, async () => {
  const f = await fixture(); const running = runTerminal(f.options);
  try {
    await makePlan(f); await accept(f); f.driver.send('/execute'); await f.driver.waitFor('Approve this action once?'); f.driver.input.end();
    assert.equal(await running, 0); assert.equal(f.counters().cloud, false); assert.ok(f.counters().interrupts > 0);
    await assert.rejects(readFile(join(f.workspace, 'result.txt')), /ENOENT/);
    const unlock = await f.store.acquireLease(); await unlock();
    assert.equal(f.driver.input.listenerCount('data'), 0); assert.equal(f.manager.listenerCount('approval'), 0);
  } finally { f.driver.input.end(); await running; await f.cleanup(); }
});

test('Ctrl+C interrupts active work and returns to REPL; another Ctrl+C while idle exits', { timeout: 10_000 }, async () => {
  const f = await fixture(); const running = runTerminal(f.options);
  try {
    await makePlan(f); await accept(f); f.driver.send('/execute'); await f.driver.waitFor('Approve this action once?'); f.driver.input.write(Buffer.from([3]));
    await f.driver.waitFor('Phase: interrupted.'); await f.driver.prompt();
    assert.ok(f.counters().interrupts > 0); await assert.rejects(readFile(join(f.workspace, 'result.txt')), /ENOENT/);
    f.driver.input.write(Buffer.from([3])); assert.equal(await running, 0);
  } finally { f.driver.input.end(); await running; await f.cleanup(); }
});

test('/interrupt while a question is pending cancels the run instead of supplying an answer', { timeout: 10_000 }, async () => {
  let decision: ApprovalDecision | undefined;
  const f = await fixture(async options => {
    decision = await options.onApproval({ id: 'question', turnId: 'plan', kind: 'question', operation: { questions: [{ id: 'question', question: 'Proceed?', options: [{ label: 'Yes' }, { label: 'No' }] }] } });
    return { status: options.signal.aborted ? 'interrupted' : 'completed', plan: options.signal.aborted ? undefined : 'Proceed.' };
  });
  const running = runTerminal({ ...f.options, initialPrompt: 'Plan a change.' });
  try {
    await f.driver.waitFor('Allow cloud inference for this launch?'); f.driver.send('yes');
    await f.driver.waitFor('Answer explicitly'); f.driver.send('/interrupt');
    await f.driver.waitFor('Phase: interrupted.'); await f.driver.prompt();
    assert.equal(decision?.decision, 'cancel'); f.driver.send('/quit'); assert.equal(await running, 0);
  } finally { f.driver.input.end(); await running; await f.cleanup(); }
});

test('resume rejects a stored model mismatch before cloud consent or runtime startup', { timeout: 10_000 }, async () => {
  const f = await fixture(); await f.manager.initialize();
  const seeded = await f.manager.create({ projectId: 'project', workspace: f.workspace, engine: 'codex', model: 'other-model', connectionId: 'provider', cloudAllowed: true, allowanceMicrousd: 1_000_000 });
  const running = runTerminal({ ...f.options, resumeSessionId: seeded.id });
  try {
    assert.equal(await running, 1); assert.ok(f.driver.text.includes('terminal_session_configuration_mismatch'));
    assert.equal(f.driver.text.includes('Allow cloud inference for this launch?'), false); assert.equal(f.counters().starts, 0);
  } finally { f.driver.input.end(); await running; await f.cleanup(); }
});

test('bounded output stops a runtime that outruns a blocked writer and still disposes it', { timeout: 10_000 }, async () => {
  let driver: TerminalDriver;
  const f = await fixture(async options => {
    driver.stall = true;
    for (let index = 0; index < 3; index++) options.onEvent({ type: 'message', data: 'x'.repeat(600_000) });
    return { status: 'completed', plan: 'No action.' };
  });
  driver = f.driver;
  const running = runTerminal({ ...f.options, initialPrompt: 'Plan a change.' });
  try {
    await f.driver.waitFor('Allow cloud inference for this launch?'); f.driver.send('yes');
    assert.equal(await running, 1); assert.ok(f.driver.text.length < 1024 * 1024); assert.ok(f.counters().disposals >= 2);
    assert.equal(f.counters().cloud, false); const unlock = await f.store.acquireLease(); await unlock();
  } finally { f.driver.input.end(); await running; await f.cleanup(); }
});

test('oversized UTF-8 input and overfull input queue fail closed before runtime startup', { timeout: 10_000 }, async () => {
  for (const input of [Buffer.from('🧪'.repeat(16_385)), Buffer.from('status\n'.repeat(10))]) {
    const f = await fixture(); const running = runTerminal(f.options);
    try {
      await f.driver.prompt(); f.driver.input.write(input); assert.equal(await running, 1); assert.equal(f.counters().starts, 0);
      const unlock = await f.store.acquireLease(); await unlock();
    } finally { f.driver.input.end(); await running; await f.cleanup(); }
  }
});

for (const cancel of ['Ctrl+C', 'EOF'] as const) {
  for (const operation of ['plan', '/new'] as const) {
    test(`${cancel} during delayed ${operation} session creation closes the manager before any late plan`, { timeout: 10_000 }, async () => {
      const f = await fixture();
      const entered = deferred(), release = deferred(), disposalCalled = deferred();
      const create = f.manager.create.bind(f.manager), plan = f.manager.plan.bind(f.manager);
      const dispose = f.manager.dispose.bind(f.manager);
      let disposal: Promise<void> | undefined;
      f.manager.dispose = () => { disposal = dispose(); disposalCalled.resolve(); return disposal; };
      let planCalls = 0;
      f.manager.create = async input => {
        const result = await create(input);
        entered.resolve(); await release.promise;
        return result;
      };
      f.manager.plan = (...args) => { planCalls++; return plan(...args); };
      const running = runTerminal(f.options);
      try {
        await f.driver.prompt();
        f.driver.send(operation === 'plan' ? 'Plan a bounded change.' : '/new');
        if (operation === 'plan') { await f.driver.waitFor('Allow cloud inference for this launch?'); f.driver.send('yes'); }
        await entered.promise;
        if (cancel === 'Ctrl+C') f.driver.input.write(Buffer.from([3])); else f.driver.input.end();
        await new Promise<void>(done => setImmediate(done));
        assert.equal(f.counters().cloud, false);
        // Stop must release ownership while the terminal's create promise is still delayed.
        await disposalCalled.promise; await disposal;
        const unlock = await f.store.acquireLease(); await unlock();
        release.resolve(); assert.equal(await running, 0);
        assert.equal(planCalls, 0); assert.equal(f.counters().starts, 0);
        assert.equal(f.driver.text.includes('Engine/model fixed for this launch.'), false);
        const rows = await f.listSessions(); assert.equal(rows.length, 1);
        const saved = await f.store.load(rows[0]!.id, 'profile', 'project');
        assert.equal((saved.accounting as { requestCount: number }).requestCount, 0);
      } finally { release.resolve(); f.driver.input.end(); await running; await f.cleanup(); }
    });
  }

  for (const operation of ['plan', 'execute'] as const) {
    test(`${cancel} during delayed backend ${operation} baseline starts no additional runtime or dispatch`, { timeout: 10_000 }, async () => {
      const f = await fixture(); const running = runTerminal(f.options);
      const entered = deferred(), release = deferred();
      try {
        let id: string;
        if (operation === 'execute') { id = await makePlan(f); await accept(f); }
        else {
          await f.driver.prompt(); f.driver.send('/new'); await f.driver.waitFor('Engine/model fixed for this launch.'); await f.driver.prompt();
          id = (await f.listSessions())[0]!.id;
        }
        const startsBefore = f.counters().starts;
        f.setObserver(async root => { entered.resolve(); await release.promise; return await snapshotWorkspace(root); });
        f.driver.send(operation === 'plan' ? 'Plan a bounded change.' : '/execute');
        if (operation === 'plan') { await f.driver.waitFor('Allow cloud inference for this launch?'); f.driver.send('yes'); }
        await entered.promise;
        if (cancel === 'Ctrl+C') f.driver.input.write(Buffer.from([3])); else f.driver.input.end();
        await new Promise<void>(done => setImmediate(done)); release.resolve();
        if (cancel === 'Ctrl+C') { await f.driver.prompt(); f.driver.send('/quit'); }
        assert.equal(await running, 0); assert.equal(f.counters().starts, startsBefore);
        const saved = await f.store.load(id, 'profile', 'project');
        assert.equal((saved.accounting as { requestCount: number }).requestCount, 0);
        const unlock = await f.store.acquireLease(); await unlock();
      } finally { release.resolve(); f.driver.input.end(); await running; await f.cleanup(); }
    });
  }

  test(`${cancel} during delayed initialization never continues to the initial cloud plan`, { timeout: 10_000 }, async () => {
    const f = await fixture(); const entered = deferred(), release = deferred();
    const initialize = f.manager.initialize.bind(f.manager), plan = f.manager.plan.bind(f.manager);
    let planCalls = 0;
    f.manager.initialize = async () => { await initialize(); entered.resolve(); await release.promise; };
    f.manager.plan = (...args) => { planCalls++; return plan(...args); };
    const running = runTerminal({ ...f.options, initialPrompt: 'Plan a bounded change.' });
    try {
      await entered.promise;
      if (cancel === 'Ctrl+C') f.driver.input.write(Buffer.from([3])); else f.driver.input.end();
      await new Promise<void>(done => setImmediate(done)); release.resolve();
      assert.equal(await running, 0); assert.equal(planCalls, 0); assert.equal(f.counters().starts, 0);
      assert.equal(f.driver.text.includes('Allow cloud inference for this launch?'), false);
      const unlock = await f.store.acquireLease(); await unlock();
    } finally { release.resolve(); f.driver.input.end(); await running; await f.cleanup(); }
  });

  for (const boundary of ['get', 'resume'] as const) {
    test(`${cancel} during delayed resume ${boundary} prevents any late initial plan`, { timeout: 10_000 }, async () => {
      const f = await fixture(); const entered = deferred(), release = deferred();
      await f.manager.initialize();
      const seeded = await f.manager.create({ projectId: 'project', workspace: f.workspace, engine: 'codex', model: 'fixture', connectionId: 'provider', cloudAllowed: true, allowanceMicrousd: 1_000_000 });
      const get = f.manager.get.bind(f.manager), resume = f.manager.resume.bind(f.manager), plan = f.manager.plan.bind(f.manager);
      let planCalls = 0;
      f.manager.plan = (...args) => { planCalls++; return plan(...args); };
      if (boundary === 'get') f.manager.get = async (...args) => { const view = await get(...args); entered.resolve(); await release.promise; return view; };
      else f.manager.resume = async (...args) => { const view = await resume(...args); entered.resolve(); await release.promise; return view; };
      const running = runTerminal({ ...f.options, resumeSessionId: seeded.id, initialPrompt: 'Plan a bounded change.' });
      try {
        if (boundary === 'resume') { await f.driver.waitFor('Allow cloud inference for this launch?'); f.driver.send('yes'); }
        await entered.promise;
        if (cancel === 'Ctrl+C') f.driver.input.write(Buffer.from([3])); else f.driver.input.end();
        await new Promise<void>(done => setImmediate(done)); release.resolve();
        assert.equal(await running, 0); assert.equal(planCalls, 0); assert.equal(f.counters().starts, 0);
        assert.equal(f.counters().cloud, false); assert.equal(f.driver.text.includes(`Resumed ${seeded.id}`), false);
        const saved = await f.store.load(seeded.id, 'profile', 'project');
        assert.equal((saved.accounting as { requestCount: number }).requestCount, 0);
        const unlock = await f.store.acquireLease(); await unlock();
      } finally { release.resolve(); f.driver.input.end(); await running; await f.cleanup(); }
    });
  }
}

test('status presents readable configured-rate accounting without exposing the session JSON', { timeout: 10_000 }, async () => {
  const f = await fixture(); const running = runTerminal(f.options);
  try {
    await makePlan(f); await accept(f); f.driver.send('/status');
    await f.driver.waitFor('Plan accepted in this launch: yes'); await f.driver.prompt();
    assert.ok(f.driver.text.includes('Phase: ready'));
    assert.ok(f.driver.text.includes('Configured-rate accounting: spent $0.000000, held $0.000000, uncertain $0.000000.'));
    assert.ok(f.driver.text.includes('Allowance: $1.000000'));
    assert.ok(f.driver.text.includes('Requests: 0. Runtime attempts: 1.'));
    assert.equal(f.driver.text.includes('"schemaVersion"'), false); assert.equal(f.driver.text.includes('"connectionId"'), false);
    f.driver.send('/quit'); assert.equal(await running, 0);
  } finally { f.driver.input.end(); await running; await f.cleanup(); }
});

test('untrusted terminal output removes OSC, ANSI, control characters, and incomplete escapes', () => {
  assert.equal(sanitizeTerminalText('\x1b[31mred\x1b[0m\x1b]8;;https://evil.test\x1b\\link\x1b]8;;\x1b\\\x00\x07\rnext\x9b'), 'redlink\nnext');
  assert.equal(sanitizeTerminalText('safe\x1b]52;c;unfinished'), 'safe');
  assert.equal(sanitizeTerminalText('safe\x1b['), 'safe');
});
