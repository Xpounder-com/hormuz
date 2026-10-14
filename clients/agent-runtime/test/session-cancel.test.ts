import assert from 'node:assert/strict';
import { lstat, mkdir, mkdtemp, realpath, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { AgentSessions, ApplicationScheduler } from '../src/sessions.ts';
import { SessionStore } from '../src/store.ts';
import { diffWorkspace, snapshotWorkspace } from '../src/workspace.ts';
import type { AgentSessionAdapter, RunResult, RuntimeStart } from '../src/types.ts';

function latch() {
  let release!: () => void;
  const promise = new Promise<void>(resolve => { release = resolve; });
  return { promise, release };
}
function readGate() { return { entered: latch(), finish: latch(), active: false, cleaned: false }; }

async function fixture(initialize = true) {
  const root = await realpath(await mkdtemp(join(tmpdir(), 'hormuz-session-cancel-')));
  const workspace = join(root, 'workspace'); await mkdir(workspace);
  await writeFile(join(workspace, 'original.txt'), 'pre-existing user content');
  const snapshot = await snapshotWorkspace(workspace);
  const store = new SessionStore(join(root, 'store')), scheduler = new ApplicationScheduler();
  const counts = { factories: 0, starts: 0, inferenceRuns: 0 };
  let blockedRead: ReturnType<typeof readGate> | undefined;
  const manager = new AgentSessions({
    profileId: 'profile', store, scheduler,
    runtimeFactory: (): AgentSessionAdapter => {
      counts.factories++;
      return {
        engine: 'codex', version: 'cancel-fixture-1',
        start: async (_options: RuntimeStart): Promise<{ threadId: string }> => { counts.starts++; assert.fail('cancelled preparation must never start a runtime'); },
        run: async (): Promise<RunResult> => { counts.inferenceRuns++; assert.fail('cancelled preparation must never start inference'); },
        interrupt: async () => {}, dispose: async () => {},
      };
    },
    observeWorkspace: async path => {
      assert.equal(path, workspace);
      const gate = blockedRead; blockedRead = undefined;
      if (!gate) return structuredClone(snapshot);
      gate.active = true; gate.entered.release();
      try { await gate.finish.promise; return structuredClone(snapshot); }
      finally { gate.active = false; gate.cleaned = true; }
    },
    observeDiff: diffWorkspace,
    authorizeWorkspace: async (project, path) => project === 'project' && path === workspace,
    authorizeAllowance: async (_project, amount) => amount <= 1_000_000,
    authorizeCloud: async () => true,
    parentAllowances: { profileMicrousd: 5_000_000, projectMicrousd: 2_000_000 },
    resolveConnection: async () => ({
      provider: 'openai', upstream: 'http://127.0.0.1:1/v1', allowLoopbackUpstream: true,
      credential: 'synthetic-never-used-key', version: 'connection-1', models: ['fixture'],
      inputMicrousdPerMillion: 1_000_000, outputMicrousdPerMillion: 1_000_000,
      maxInputTokens: 20_000, maxOutputTokens: 100,
    }),
    runTimeoutMs: 1000, approvalTimeoutMs: 1000,
  });
  if (initialize) await manager.initialize();
  const create = () => manager.create({ projectId: 'project', workspace, engine: 'codex', model: 'fixture', connectionId: 'connection', cloudAllowed: true, allowanceMicrousd: 1_000_000 });
  const blockNextSnapshot = () => { const gate = readGate(); blockedRead = gate; return gate; };
  return { root, store, scheduler, counts, snapshot, manager, create, blockNextSnapshot,
    cleanup: async () => { blockedRead?.finish.release(); await manager.dispose(); await rm(root, { recursive: true, force: true }); } };
}

test('shutdown awaits one delayed initial lease acquisition and releases its late owner', { timeout: 5000 }, async () => {
  const f = await fixture(false), entered = latch(), finish = latch();
  const originalAcquire = f.store.acquireLease.bind(f.store);
  let acquisitions = 0, disposed = false;
  f.store.acquireLease = async () => {
    acquisitions++;
    const release = await originalAcquire(); entered.release();
    await finish.promise;
    return release;
  };
  const first = f.manager.initialize(), second = f.manager.initialize();
  const firstRejected = assert.rejects(first, { code: 'host_unavailable' });
  const secondRejected = assert.rejects(second, { code: 'host_unavailable' });
  let shutdown: Promise<void> | undefined;
  try {
    await entered.promise;
    shutdown = f.manager.dispose().then(() => { disposed = true; });
    await new Promise<void>(resolve => setImmediate(resolve));
    assert.equal(acquisitions, 1); assert.equal(disposed, false);
    assert.equal((await lstat(join(f.store.root, 'owner.lock'))).mode & 0o777, 0o600);
    await assert.rejects(new SessionStore(f.store.root).acquireLease(), /SESSION_STORE_IN_USE/);
    finish.release(); await Promise.all([firstRejected, secondRejected, shutdown]);
    assert.equal(disposed, true);
    await assert.rejects(lstat(join(f.store.root, 'owner.lock')), { code: 'ENOENT' });
    const release = await new SessionStore(f.store.root).acquireLease(); await release();
    await assert.rejects(f.manager.initialize(), { code: 'host_unavailable' });
  } finally {
    finish.release(); await Promise.allSettled([firstRejected, secondRejected, shutdown]); await f.cleanup();
  }
});

for (const mode of ['plan', 'execute'] as const) {
  test(`interrupt cancels the ${mode} baseline before native startup and consumes previous execution authority`, { timeout: 5000 }, async () => {
    const f = await fixture();
    let gate: ReturnType<typeof readGate> | undefined;
    let operation: Promise<unknown> | undefined;
    try {
      const session = await f.create(), revision = 'accepted-fixture-plan';
      const record = await f.store.load(session.id, 'profile', 'project');
      await f.store.save({ ...record, phase: 'ready', plan: { text: 'A previously accepted bounded plan.', revision, workspaceFingerprint: f.snapshot.fingerprint, connectionVersion: 'connection-1' }, acceptedPlanFingerprint: revision });
      gate = f.blockNextSnapshot();
      operation = mode === 'plan' ? f.manager.plan(session.id, 'project', 'Replace the previous plan.') : f.manager.execute(session.id, 'project', revision);
      const rejected = assert.rejects(operation, { code: 'interrupted' });
      await gate.entered.promise;
      await f.manager.interrupt(session.id, 'project');
      assert.equal(gate.active, true);
      gate.finish.release(); await rejected;
      assert.equal(gate.cleaned, true); assert.equal(gate.active, false);
      assert.deepEqual(f.counts, { factories: 1, starts: 0, inferenceRuns: 0 });
      assert.equal(f.scheduler.activeOwner, null);
      const saved = await f.store.load(session.id, 'profile', 'project');
      assert.equal(saved.acceptedPlanFingerprint, undefined);
      assert.equal(saved.runtimeAttempts, 0);
      assert.equal((await f.manager.get(session.id, 'project')).accounting.requestCount, 0);
      await assert.rejects(f.manager.execute(session.id, 'project', revision), { code: 'plan_not_accepted' });
      if (mode === 'plan') assert.equal(saved.plan, undefined);
    } finally { gate?.finish.release(); if (operation) await Promise.allSettled([operation]); await f.cleanup(); }
  });

  test(`host disposal waits for the ${mode} baseline to clean up and prevents late runtime startup`, { timeout: 5000 }, async () => {
    const f = await fixture();
    let gate: ReturnType<typeof readGate> | undefined;
    let operation: Promise<unknown> | undefined, shutdown: Promise<void> | undefined;
    try {
      const session = await f.create(), revision = 'accepted-fixture-plan';
      const record = await f.store.load(session.id, 'profile', 'project');
      await f.store.save({ ...record, phase: 'ready', plan: { text: 'A previously accepted bounded plan.', revision, workspaceFingerprint: f.snapshot.fingerprint, connectionVersion: 'connection-1' }, acceptedPlanFingerprint: revision });
      gate = f.blockNextSnapshot();
      operation = mode === 'plan' ? f.manager.plan(session.id, 'project', 'Replace the previous plan.') : f.manager.execute(session.id, 'project', revision);
      const rejected = assert.rejects(operation, { code: 'interrupted' });
      await gate.entered.promise;
      let disposed = false;
      shutdown = f.manager.dispose().then(() => { assert.equal(gate!.cleaned, true); disposed = true; });
      await new Promise<void>(resolve => setImmediate(resolve));
      assert.equal(disposed, false); assert.equal(gate.active, true);
      await assert.rejects(new SessionStore(f.store.root).acquireLease(), /SESSION_STORE_IN_USE/);
      gate.finish.release(); await Promise.all([rejected, shutdown]);
      assert.equal(disposed, true); assert.equal(gate.active, false);
      assert.deepEqual(f.counts, { factories: 1, starts: 0, inferenceRuns: 0 });
      assert.equal(f.scheduler.activeOwner, null);
      const saved = await f.store.load(session.id, 'profile', 'project');
      assert.equal(saved.acceptedPlanFingerprint, undefined);
      assert.equal(saved.runtimeAttempts, 0);
      const release = await new SessionStore(f.store.root).acquireLease(); await release();
      await assert.rejects(f.manager.initialize(), { code: 'host_unavailable' });
    } finally { gate?.finish.release(); await Promise.allSettled([operation, shutdown]); await f.cleanup(); }
  });
}

test('host disposal during create observation prevents a late session and waits for read cleanup', { timeout: 5000 }, async () => {
  const f = await fixture(), gate = f.blockNextSnapshot();
  const creating = f.create(), rejected = assert.rejects(creating, { code: 'host_unavailable' });
  let shutdown: Promise<void> | undefined;
  try {
    await gate.entered.promise;
    let disposed = false;
    shutdown = f.manager.dispose().then(() => { assert.equal(gate.cleaned, true); disposed = true; });
    await new Promise<void>(resolve => setImmediate(resolve));
    assert.equal(disposed, false);
    gate.finish.release(); await Promise.all([rejected, shutdown]);
    assert.equal(disposed, true); assert.equal(gate.active, false);
    assert.deepEqual(f.counts, { factories: 0, starts: 0, inferenceRuns: 0 });
    assert.deepEqual(await f.store.list('profile', 'project'), []);
    const release = await new SessionStore(f.store.root).acquireLease(); await release();
  } finally { gate.finish.release(); await Promise.allSettled([creating, shutdown]); await f.cleanup(); }
});
