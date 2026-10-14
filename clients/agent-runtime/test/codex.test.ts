import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, rm, access, realpath } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { CodexAdapter } from '../src/codex.ts';
import type { RuntimeEvent, ActionRequest } from '../src/types.ts';

const fixture = fileURLToPath(new URL('./fixtures/codex-server.mjs', import.meta.url));
const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

async function setup(scenario = 'normal', settings: ConstructorParameters<typeof CodexAdapter>[0] = {}) {
  const root = await realpath(await mkdtemp(join(tmpdir(), 'hormuz-codex-test-')));
  const workspace = join(root, 'project'), configDir = join(root, 'runtime');
  await mkdir(workspace); await mkdir(configDir);
  const adapter = new CodexAdapter({ command: process.execPath, args: [fixture, `--scenario=${scenario}`],
    // Identity launcher tests protocol/supervision only. OS confinement has a separate real proof.
    prepareLaunch: async ({ command, args }) => ({ command, args }),
    requestTimeoutMs: 500, approvalTimeoutMs: 200, shutdownTimeoutMs: 50, runTimeoutMs: 2_000, ...settings });
  const options = { workspace, configDir, endpoint: 'http://127.0.0.1:9999/v1', credential: 'test-gateway-token', model: 'test-model' };
  const cleanup = async () => { await adapter.dispose(); await rm(root, { recursive: true, force: true }); };
  return { adapter, options, cleanup };
}

function runOptions(mode: 'plan' | 'execute', events: RuntimeEvent[] = [], approvals: ActionRequest[] = []) {
  return { sessionId: 'host-session', prompt: 'Implement a small improvement', mode, signal: new AbortController().signal,
    onEvent: (event: RuntimeEvent) => events.push(event),
    onApproval: async (request: ActionRequest) => { approvals.push(request); return { decision: 'accept' as const }; } };
}

test('real subprocess handshake, native read-only plan, approved execution, streamed diff and usage', async () => {
  const { adapter, options, cleanup } = await setup();
  try {
    const result = await adapter.start(options);
    assert.equal(result.threadId, 'thread-1');
    const events: RuntimeEvent[] = [], approvals: ActionRequest[] = [];
    const plan = await adapter.run(runOptions('plan', events, approvals));
    assert.equal(plan.status, 'completed'); assert.match(plan.plan!, /Change one file/);
    assert.equal(approvals.length, 0);
    const execution = await adapter.run(runOptions('execute', events, approvals));
    assert.equal(execution.status, 'completed');
    assert.deepEqual(approvals.map(request => request.kind), ['command', 'file']);
    assert.equal(await readFile(join(options.workspace, 'example.txt'), 'utf8'), 'changed');
    assert.ok(events.some(event => event.type === 'diff'));
    assert.ok(events.some(event => event.type === 'usage'));
    assert.ok(events.some(event => event.type === 'message'));
    const trace = (await readFile(join(options.configDir, 'fixture-trace.jsonl'), 'utf8')).trim().split('\n').map(line => JSON.parse(line));
    const turns = trace.filter(value => value.method === 'turn/start');
    assert.deepEqual(turns[0].params.sandboxPolicy, { type: 'readOnly', networkAccess: false });
    assert.equal(turns[0].params.collaborationMode.mode, 'plan');
    assert.deepEqual(turns[1].params.sandboxPolicy, { type: 'readOnly', networkAccess: false });
    assert.equal(turns[1].params.collaborationMode.mode, 'default');
    assert.equal(turns[1].params.approvalPolicy, 'on-request');
    assert.equal(turns[1].params.approvalsReviewer, 'user');
    assert.equal(trace[0].envKeys.includes('OPENAI_API_KEY'), false);
    assert.equal(trace[0].envKeys.includes('ANTHROPIC_API_KEY'), false);
    assert.match(trace[0].config, /inherit = "none"/);
    assert.match(trace[0].config, /env_key = "HORMUZ_CODEX_GATEWAY_TOKEN"/);
    assert.equal(trace[0].config.includes(options.credential), false);
  } finally { await cleanup(); }
});

test('read-only planning declines action approval without invoking host approval callback', async () => {
  const { adapter, options, cleanup } = await setup('plan-approval');
  try {
    await adapter.start(options);
    let called = false;
    const result = await adapter.run({ ...runOptions('plan'), onApproval: async () => { called = true; return { decision: 'accept' }; } });
    assert.equal(result.status, 'completed'); assert.equal(called, false);
  } finally { await cleanup(); }
});

test('declined execution approval performs no file modification', async () => {
  const { adapter, options, cleanup } = await setup();
  try {
    await adapter.start(options);
    const result = await adapter.run({ ...runOptions('execute'), onApproval: async () => ({ decision: 'decline' }) });
    assert.equal(result.status, 'completed');
    await assert.rejects(access(join(options.workspace, 'example.txt')));
  } finally { await cleanup(); }
});

test('foreign thread, stale turn, and unsupported server requests cannot gain approval or emit events', async () => {
  const { adapter, options, cleanup } = await setup('foreign');
  try {
    await adapter.start(options);
    const events: RuntimeEvent[] = [], approvals: ActionRequest[] = [];
    const result = await adapter.run(runOptions('execute', events, approvals));
    assert.equal(result.status, 'completed');
    assert.deepEqual(approvals.map(request => request.operation.itemId), ['cmd-1', 'file-1']);
    assert.equal(JSON.stringify(events).includes('FOREIGN'), false);
  } finally { await cleanup(); }
});

test('native planning questions map answers to the exact runtime question identity', async () => {
  const { adapter, options, cleanup } = await setup('question');
  try {
    await adapter.start(options);
    const result = await adapter.run({ ...runOptions('plan'), onApproval: async request => {
      assert.equal(request.kind, 'question'); assert.equal(request.turnId, 'turn-1');
      return { decision: 'accept', answers: { choice: ['Small'] } };
    } });
    assert.equal(result.status, 'completed'); assert.match(result.plan!, /Inspect/);
  } finally { await cleanup(); }
});

test('interruption cancels the current bound turn and allows a subsequent resume on disk', async () => {
  const { adapter, options, cleanup } = await setup('hang');
  try {
    await adapter.start(options);
    const running = adapter.run(runOptions('execute'));
    await delay(50); await adapter.interrupt();
    assert.equal((await running).status, 'interrupted');
    await adapter.dispose();
    const resumed = new CodexAdapter({ command: process.execPath, args: [fixture], prepareLaunch: async ({ command, args }) => ({ command, args }), shutdownTimeoutMs: 50 });
    try {
      assert.equal((await resumed.start({ ...options, threadId: 'thread-1' })).threadId, 'thread-1');
      assert.equal((await resumed.run(runOptions('plan'))).status, 'completed');
      const trace = await readFile(join(options.configDir, 'fixture-trace.jsonl'), 'utf8');
      assert.match(trace, /"method":"thread\/resume"/);
    } finally { await resumed.dispose(); }
  } finally { await cleanup(); }
});

test('version mismatch, handshake deadline, crashed runtime, and oversized output fail with bounded cleanup', async t => {
  for (const scenario of ['version', 'init-timeout', 'crash', 'huge']) await t.test(scenario, async () => {
    const { adapter, options, cleanup } = await setup(scenario, { maxFrameBytes: 10_000 });
    try {
      if (scenario === 'version') await assert.rejects(adapter.start(options), { code: 'unsupported_codex_version' });
      else if (scenario === 'init-timeout') await assert.rejects(adapter.start(options), { code: 'runtime_request_timeout' });
      else { await adapter.start(options); assert.equal((await adapter.run(runOptions('execute'))).status, 'unknown'); }
      await adapter.dispose();
      const pid = Number(await readFile(join(options.configDir, 'fixture-child.pid'), 'utf8'));
      let alive = true;
      for (let i = 0; i < 10 && alive; i++) {
        try { process.kill(pid, 0); await delay(25); } catch { alive = false; }
      }
      assert.equal(alive, false, 'owned background descendant must be terminated');
    } finally { await cleanup(); }
  });
});

test('rejects remote gateway URLs and config directories inside the project', async () => {
  const invalid = await setup();
  try { await assert.rejects(invalid.adapter.start({ ...invalid.options, endpoint: 'https://api.openai.com/v1' }), { code: 'invalid_gateway_endpoint' }); }
  finally { await invalid.cleanup(); }
  const nested = await setup();
  try { await assert.rejects(nested.adapter.start({ ...nested.options, configDir: join(nested.options.workspace, 'runtime') }), { code: 'runtime_home_not_isolated' }); }
  finally { await nested.cleanup(); }
});

test('forced shutdown waits for the owned root to be killed and reaped before returning', async () => {
  const { adapter, options, cleanup } = await setup('stubborn', { shutdownTimeoutMs: 100 });
  try {
    await adapter.start(options);
    const pid = Number(await readFile(join(options.configDir, 'fixture-root.pid'), 'utf8'));
    await adapter.dispose();
    assert.throws(() => process.kill(pid, 0), { code: 'ESRCH' });
  } finally { await cleanup(); }
});
