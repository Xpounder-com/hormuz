import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, rm, access, realpath, rename, writeFile, lstat } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, dirname, basename, isAbsolute } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import { ClaudeAdapter } from '../src/claude.ts';
import type { ActionRequest, RuntimeEvent } from '../src/types.ts';

const fixture = fileURLToPath(new URL('./fixtures/claude-query.mjs', import.meta.url));
async function setup(settings: ConstructorParameters<typeof ClaudeAdapter>[0] = {}) {
  const root = await realpath(await mkdtemp(join(tmpdir(), 'hormuz-claude-test-')));
  const workspace = join(root, 'project'), configDir = join(root, 'runtime');
  await mkdir(workspace); await mkdir(configDir, { mode: 0o700 });
  const adapter = new ClaudeAdapter({ sdkFixture: fixture, prepareLaunch: async ({ command, args }) => ({ command, args }),
    requestTimeoutMs: 3000, shutdownTimeoutMs: 100, runTimeoutMs: 3000, approvalTimeoutMs: 300, ...settings });
  const options = { workspace, configDir, endpoint: 'http://127.0.0.1:9999/v1', credential: 'test-scoped-token', model: 'test-model' };
  return { adapter, options, cleanup: async () => { await adapter.dispose(); await rm(root, { recursive: true, force: true }); } };
}
function runOptions(mode: 'plan' | 'execute', prompt = 'NORMAL', approvals: ActionRequest[] = [], events: RuntimeEvent[] = []) {
  return { sessionId: 'host-session', mode, prompt, signal: new AbortController().signal,
    onEvent: (event: RuntimeEvent) => events.push(event), onApproval: async (request: ActionRequest) => { approvals.push(request); return { decision: 'accept' as const }; } };
}

test('Claude worker plans read-only, asks once for each execution side effect even when SDK callback is skipped, and resumes disk session', async () => {
  const { adapter, options, cleanup } = await setup();
  try {
    const { threadId } = await adapter.start(options);
    const approvals: ActionRequest[] = [], events: RuntimeEvent[] = [];
    const plan = await adapter.run(runOptions('plan', 'NORMAL', approvals, events));
    assert.equal(plan.status, 'completed'); assert.match(plan.plan!, /after approval/);
    assert.equal(approvals.length, 0); await assert.rejects(access(join(options.workspace, 'claude.txt')));
    const result = await adapter.run(runOptions('execute', 'NORMAL', approvals, events));
    assert.equal(result.status, 'completed'); assert.equal(result.threadId, threadId);
    assert.deepEqual(approvals.map(value => value.kind), ['command', 'file']);
    assert.equal(await readFile(join(options.workspace, 'claude.txt'), 'utf8'), 'changed');
    assert.ok(events.some(event => event.type === 'usage')); assert.ok(events.some(event => event.type === 'message'));
    const trace = (await readFile(join(options.configDir, 'fixture-claude.jsonl'), 'utf8')).trim().split('\n').map(line => JSON.parse(line));
    assert.equal(trace[0].mode, 'plan'); assert.equal(trace[1].mode, 'default'); assert.equal(trace[1].resume, threadId);
    assert.deepEqual(trace[0].settingSources, []); assert.equal(trace[0].tools.includes('Agent'), false); assert.equal(trace[0].tools.includes('WebFetch'), false);
    assert.equal(trace[0].envKeys.includes('OPENAI_API_KEY'), false); assert.equal(trace[0].baseUrl, 'http://127.0.0.1:9999');
    assert.equal(trace[0].outputLimit, '1024'); assert.deepEqual(trace[0].thinking, { type: 'disabled' });
    assert.match(basename(trace[0].temporary), /^hm-c-/); assert.ok(isAbsolute(trace[0].temporary));
    assert.equal(await realpath(trace[0].temporary), trace[0].temporary);
    const temporary = await lstat(trace[0].temporary);
    assert.equal(temporary.isDirectory(), true); assert.equal(temporary.isSymbolicLink(), false);
    assert.equal(temporary.mode & 0o777, 0o700); assert.equal(temporary.uid, process.getuid?.());
    assert.equal(trace[0].temporary, trace[1].temporary);
    await adapter.dispose(); await assert.rejects(access(trace[0].temporary), /ENOENT/);
  } finally { await cleanup(); }
});

test('Claude denials block auto-approved writes and foreign paths never reach approval', async () => {
  for (const prompt of ['NORMAL', 'OUTSIDE']) {
    const { adapter, options, cleanup } = await setup();
    try {
      await adapter.start(options);
      let approvals = 0;
      const result = await adapter.run({ ...runOptions('execute', prompt), onApproval: async () => { approvals++; return { decision: 'decline' }; } });
      assert.equal(result.status, 'completed'); assert.equal(approvals, prompt === 'NORMAL' ? 2 : 0);
      await assert.rejects(access(join(options.workspace, 'claude.txt'))); await assert.rejects(access(join(options.workspace, '..', 'outside.txt')));
    } finally { await cleanup(); }
  }
});

test('Claude planning questions stay bound to their runtime turn and map explicit answers', async () => {
  const { adapter, options, cleanup } = await setup();
  try {
    await adapter.start(options);
    const result = await adapter.run({ ...runOptions('plan', 'QUESTION'), onApproval: async request => {
      assert.equal(request.kind, 'question'); const questions = request.operation.questions as Array<{ id: string }>;
      assert.equal(questions[0].id, 'question-1'); return { decision: 'accept', answers: { 'question-1': ['Small'] } };
    } });
    assert.equal(result.status, 'completed');
  } finally { await cleanup(); }
});

test('Claude interruption cleans the active worker and supports explicit persisted resume', async () => {
  const { adapter, options, cleanup } = await setup();
  try {
    const { threadId } = await adapter.start(options);
    const running = adapter.run(runOptions('execute', 'HANG'));
    await new Promise(resolve => setTimeout(resolve, 250)); await adapter.interrupt();
    assert.equal((await running).status, 'interrupted');
    await adapter.dispose();
    const resumed = new ClaudeAdapter({ sdkFixture: fixture, prepareLaunch: async ({ command, args }) => ({ command, args }), shutdownTimeoutMs: 100 });
    try { assert.equal((await resumed.start({ ...options, threadId })).threadId, threadId); assert.equal((await resumed.run(runOptions('plan'))).status, 'completed'); }
    finally { await resumed.dispose(); }
  } finally { await cleanup(); }
});

test('Claude rejects unsupported SDK versions and foreign session output', async () => {
  const invalid = await setup({ sdkFixture: fileURLToPath(new URL('./fixtures/claude-bad-version.mjs', import.meta.url)) });
  try { await assert.rejects(invalid.adapter.start(invalid.options), { code: 'unsupported_claude_version' }); } finally { await invalid.cleanup(); }
  const foreign = await setup();
  try { await foreign.adapter.start(foreign.options); const events: RuntimeEvent[] = [];
    assert.equal((await foreign.adapter.run(runOptions('execute', 'FOREIGN_SESSION', [], events))).status, 'failed');
    assert.equal(JSON.stringify(events).includes('FORGED'), false);
  } finally { await foreign.cleanup(); }
});

test('Claude resume rejects a missing SDK transcript before an inference turn', async () => {
  const { adapter, options, cleanup } = await setup();
  try { await assert.rejects(adapter.start({ ...options, threadId: '11111111-1111-4111-8111-111111111111' }), { code: 'runtime_session_missing' }); }
  finally { await cleanup(); }
});

test('Claude cancellation during worker launch never starts a late inference query', async () => {
  let launchCount = 0, release!: () => void, entered!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  const launchEntered = new Promise<void>(resolve => { entered = resolve; });
  const { adapter, options, cleanup } = await setup({ prepareLaunch: async ({ command, args }) => {
    if (++launchCount === 2) { entered(); await gate; }
    return { command, args };
  } });
  try {
    await adapter.start(options);
    const running = adapter.run(runOptions('execute'));
    await launchEntered; await adapter.interrupt(); release();
    assert.equal((await running).status, 'interrupted');
    await assert.rejects(access(join(options.configDir, 'fixture-claude.jsonl')));
  } finally { release(); await cleanup(); }
});

test('Claude refuses a replaced private temporary root and never removes its replacement', async () => {
  let temporary = '', replaced = false;
  const { adapter, options, cleanup } = await setup({ prepareLaunch: async ({ command, args, privateTempDir }) => {
    temporary = privateTempDir!; return { command, args };
  } });
  try {
    await adapter.start(options);
    await rename(temporary, temporary + '-owned'); replaced = true;
    await mkdir(temporary, { mode: 0o700 }); await writeFile(join(temporary, 'foreign.txt'), 'retain replacement');
    await assert.rejects(adapter.run(runOptions('plan')), { code: 'runtime_temp_changed' });
    await assert.rejects(adapter.dispose(), { code: 'runtime_temp_changed' });
    assert.equal(await readFile(join(temporary, 'foreign.txt'), 'utf8'), 'retain replacement');
  } finally {
    if (replaced) { await rm(temporary, { recursive: true, force: true }); await rename(temporary + '-owned', temporary); }
    await cleanup();
  }
});

test('Claude production SDK import and version handshake work inside real read-only OS worker sandbox', { skip: process.platform !== 'darwin' || process.env.HORMUZ_TEST_INSTALLED_CLAUDE !== '1' }, async () => {
  const { options, cleanup } = await setup();
  const adapter = new ClaudeAdapter({ requestTimeoutMs: 5000, shutdownTimeoutMs: 100 });
  try { assert.match((await adapter.start(options)).threadId, /^[0-9a-f-]{36}$/); }
  finally { await adapter.dispose(); await cleanup(); }
});

test('Claude launcher grants canonical SDK/native roots instead of an entire nested node_modules tree', { skip: process.platform !== 'darwin' }, async () => {
  let paths: string[] = [];
  const f = await setup({ sdkFixture: undefined, prepareLaunch: async ({ command, args, trustedReadPaths }) => {
    paths = trustedReadPaths ?? []; return { command, args };
  } });
  try {
    await f.adapter.start(f.options);
    const sdkEntry = await realpath(createRequire(import.meta.url).resolve('@anthropic-ai/claude-agent-sdk'));
    const native = await realpath(createRequire(sdkEntry).resolve(`@anthropic-ai/claude-agent-sdk-${process.platform}-${process.arch}/claude`));
    assert.ok(paths.includes(dirname(sdkEntry))); assert.ok(paths.includes(dirname(native)));
    assert.ok(paths.includes(await realpath(fileURLToPath(new URL('../package.json', import.meta.url)))));
    assert.equal(paths.some(path => basename(path) === 'node_modules'), false);
    assert.equal(paths.length, 4);
  } finally { await f.cleanup(); }
});
