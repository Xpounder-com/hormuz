import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { syncBuiltinESMExports } from 'node:module';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { CodexAdapter } from '../src/codex.ts';
import { ClaudeAdapter } from '../src/claude.ts';
import { AgentSessions, ApplicationScheduler } from '../src/sessions.ts';
import { SessionStore } from '../src/store.ts';
import { snapshotWorkspace } from '../src/workspace.ts';
import type { AgentSessionAdapter, RuntimeRun } from '../src/types.ts';

const claudeFixture = fileURLToPath(new URL('./fixtures/claude-query.mjs', import.meta.url));
const codexFixture = fileURLToPath(new URL('./fixtures/codex-server.mjs', import.meta.url));
function gate() {
  let enter!: () => void, release!: () => void;
  const entered = new Promise<void>(resolve => { enter = resolve; });
  const pending = new Promise<void>(resolve => { release = resolve; });
  return { enter, entered, pending, release };
}
async function folder() {
  const root = await fs.realpath(await fs.mkdtemp(join(tmpdir(), 'hormuz-preparation-test-')));
  const workspace = join(root, 'project'), configDir = join(root, 'runtime');
  await fs.mkdir(workspace);
  return { root, options: { workspace, configDir, model: 'fixture', endpoint: 'http://127.0.0.1:9999/v1', credential: 'synthetic-token' } };
}
const runOptions = (): RuntimeRun => ({ sessionId: 'synthetic-session', prompt: 'NORMAL', mode: 'execute', signal: new AbortController().signal,
  onEvent: () => {}, onApproval: async () => ({ decision: 'accept' }) });

test('both adapters retain initial filesystem preparation until delayed folder resolution settles', { timeout: 5_000 }, async t => {
  for (const engine of ['codex', 'claude'] as const) {
    const f = await folder(), blocked = gate();
    const originalRealpath = fs.realpath;
    let launches = 0, delayed = false;
    const launch = async ({ command, args }: { command: string; args: string[] }) => { launches++; return { command, args }; };
    const adapter: AgentSessionAdapter = engine === 'codex' ? new CodexAdapter({ command: process.execPath, prepareLaunch: launch, preparationTimeoutMs: 25 })
      : new ClaudeAdapter({ sdkFixture: claudeFixture, prepareLaunch: launch, preparationTimeoutMs: 25 });
    t.mock.method(fs, 'realpath', async (...args: Parameters<typeof fs.realpath>) => {
      if (args[0] === f.options.workspace && !delayed) { delayed = true; blocked.enter(); await blocked.pending; }
      return originalRealpath(...args);
    });
    syncBuiltinESMExports();
    const started = adapter.start(f.options).catch(error => error);
    try {
      await blocked.entered;
      await assert.rejects(adapter.dispose(), { code: 'runtime_preparation_pending' }, engine);
      await assert.rejects(fs.access(f.options.configDir), { code: 'ENOENT' });
      blocked.release();
      assert.equal((await started).code, 'runtime_closed', engine);
      await adapter.dispose();
      assert.equal(launches, 0, engine);
      await assert.rejects(fs.access(f.options.configDir), { code: 'ENOENT' });
    } finally {
      blocked.release(); await started;
      t.mock.restoreAll(); syncBuiltinESMExports();
      await adapter.dispose(); await fs.rm(f.root, { recursive: true, force: true });
    }
  }
});

test('both adapters keep delayed sandbox preparation owned and prevent a late process spawn', { timeout: 5_000 }, async () => {
  for (const engine of ['codex', 'claude'] as const) {
    const f = await folder(), blocked = gate();
    let temporary = '';
    const launch = async ({ command, args, privateTempDir }: { command: string; args: string[]; privateTempDir?: string }) => {
      temporary = privateTempDir ?? ''; blocked.enter(); await blocked.pending;
      await fs.writeFile(join(f.options.configDir, 'sandbox-prepared'), 'owned preparation');
      return { command, args };
    };
    const adapter: AgentSessionAdapter = engine === 'codex' ? new CodexAdapter({ command: process.execPath, args: [codexFixture], prepareLaunch: launch, preparationTimeoutMs: 25 })
      : new ClaudeAdapter({ sdkFixture: claudeFixture, prepareLaunch: launch, preparationTimeoutMs: 25 });
    const started = adapter.start(f.options).catch(error => error);
    try {
      await blocked.entered;
      await assert.rejects(adapter.dispose(), { code: 'runtime_preparation_pending' }, engine);
      await assert.rejects(fs.access(join(f.options.configDir, 'sandbox-prepared')), { code: 'ENOENT' });
      blocked.release();
      assert.equal((await started).code, 'runtime_closed', engine);
      await adapter.dispose();
      assert.equal(await fs.readFile(join(f.options.configDir, 'sandbox-prepared'), 'utf8'), 'owned preparation');
      await assert.rejects(fs.access(join(f.options.configDir, 'fixture-root.pid')), { code: 'ENOENT' });
      await assert.rejects(fs.access(join(f.options.configDir, 'fixture-claude.jsonl')), { code: 'ENOENT' });
      if (temporary) await assert.rejects(fs.access(temporary), { code: 'ENOENT' });
    } finally { blocked.release(); await started; await adapter.dispose(); await fs.rm(f.root, { recursive: true, force: true }); }
  }
});

test('Claude owns private temp creation until its late directory is tracked and removed', { timeout: 5_000 }, async t => {
  const f = await folder(), blocked = gate(), originalMkdtemp = fs.mkdtemp;
  let temporary = '', launches = 0;
  const adapter = new ClaudeAdapter({ sdkFixture: claudeFixture, preparationTimeoutMs: 25, prepareLaunch: async ({ command, args }) => { launches++; return { command, args }; } });
  t.mock.method(fs, 'mkdtemp', async (...args: Parameters<typeof fs.mkdtemp>) => {
    if (String(args[0]).endsWith('/hm-c-')) {
      blocked.enter(); await blocked.pending;
      const value = await originalMkdtemp(...args); temporary = String(value); return value;
    }
    return originalMkdtemp(...args);
  });
  syncBuiltinESMExports();
  const started = adapter.start(f.options).catch(error => error);
  try {
    await blocked.entered;
    await assert.rejects(adapter.dispose(), { code: 'runtime_preparation_pending' });
    blocked.release(); assert.equal((await started).code, 'runtime_closed');
    await adapter.dispose(); assert.equal(launches, 0); assert.ok(temporary);
    await assert.rejects(fs.access(temporary), { code: 'ENOENT' });
  } finally {
    blocked.release(); await started; t.mock.restoreAll(); syncBuiltinESMExports();
    await adapter.dispose(); await fs.rm(f.root, { recursive: true, force: true });
  }
});

test('Claude run preparation remains owned during disposal without a late inference query', { timeout: 5_000 }, async () => {
  const f = await folder(), blocked = gate();
  let launches = 0;
  const adapter = new ClaudeAdapter({ sdkFixture: claudeFixture, preparationTimeoutMs: 25, shutdownTimeoutMs: 50,
    prepareLaunch: async ({ command, args }) => { if (++launches === 2) { blocked.enter(); await blocked.pending; } return { command, args }; } });
  let running: Promise<unknown> | undefined;
  try {
    await adapter.start(f.options);
    running = adapter.run(runOptions()).catch(error => error);
    await blocked.entered;
    await assert.rejects(adapter.dispose(), { code: 'runtime_preparation_pending' });
    blocked.release(); assert.equal((await running as { code: string }).code, 'runtime_closed');
    await adapter.dispose(); await assert.rejects(fs.access(join(f.options.configDir, 'fixture-claude.jsonl')), { code: 'ENOENT' });
  } finally { blocked.release(); await running; await adapter.dispose(); await fs.rm(f.root, { recursive: true, force: true }); }
});

test('Codex history preparation keeps its open manifest handle owned through cancellation', { timeout: 5_000 }, async t => {
  const f = await folder(), blocked = gate();
  const packageRoot = join(f.root, 'package'), wrapper = join(packageRoot, 'bin', 'codex.js'), manifestPath = join(packageRoot, 'package.json');
  await fs.mkdir(join(packageRoot, 'bin'), { recursive: true }); await fs.writeFile(wrapper, 'never executed');
  await fs.writeFile(manifestPath, '{"name":"@openai/codex","version":"0.160.0","bin":{"codex":"bin/codex.js"},"optionalDependencies":{}}');
  const adapter = new CodexAdapter({ command: wrapper, preparationTimeoutMs: 25 });
  const originalOpen = fs.open;
  let closed = false;
  t.mock.method(fs, 'open', async (...args: Parameters<typeof fs.open>) => {
    const handle = await originalOpen(...args);
    if (args[0] === manifestPath) {
      const read = handle.read.bind(handle), close = handle.close.bind(handle);
      t.mock.method(handle, 'read', async (buffer: Buffer, offset: number, length: number, position: number) => { blocked.enter(); await blocked.pending; return read(buffer, offset, length, position); });
      t.mock.method(handle, 'close', async () => { await close(); closed = true; });
    }
    return handle;
  });
  syncBuiltinESMExports();
  const policy = adapter.historyPolicy().catch(error => error);
  try {
    await blocked.entered;
    await assert.rejects(adapter.dispose(), { code: 'runtime_preparation_pending' });
    assert.equal(closed, false); blocked.release(); assert.equal((await policy).code, 'unsupported_codex_layout');
    await adapter.dispose(); assert.equal(closed, true);
  } finally {
    blocked.release(); await policy; t.mock.restoreAll(); syncBuiltinESMExports();
    await adapter.dispose(); await fs.rm(f.root, { recursive: true, force: true });
  }
});

test('canceled Codex config write prevents profile lease and scheduler transfer until preparation settles', { timeout: 5_000 }, async t => {
  const f = await folder(), blocked = gate(), originalWriteFile = fs.writeFile;
  let configPath = '', launches = 0;
  t.mock.method(fs, 'writeFile', async (...args: Parameters<typeof fs.writeFile>) => {
    if (String(args[0]).endsWith('/config.toml')) { configPath = String(args[0]); blocked.enter(); await blocked.pending; }
    return originalWriteFile(...args);
  });
  syncBuiltinESMExports();
  let adapter: CodexAdapter | undefined;
  const store = new SessionStore(join(f.root, 'state')), scheduler = new ApplicationScheduler();
  const manager = new AgentSessions({ profileId: 'profile', store, scheduler, runtimeFactory: () => adapter = new CodexAdapter({ command: process.execPath, preparationTimeoutMs: 25,
    prepareLaunch: async ({ command, args }) => { launches++; return { command, args }; } }),
    observeWorkspace: snapshotWorkspace, authorizeWorkspace: async () => true, authorizeCloud: async () => true, authorizeAllowance: async () => true,
    parentAllowances: { profileMicrousd: 2_000_000, projectMicrousd: 2_000_000 }, runTimeoutMs: 500,
    resolveConnection: async () => ({ provider: 'openai', upstream: 'https://api.openai.com/v1', credential: 'synthetic-key-not-dispatched', version: 'fixture', models: ['fixture'],
      inputMicrousdPerMillion: 1_000_000, outputMicrousdPerMillion: 1_000_000, maxInputTokens: 20_000, maxOutputTokens: 100 }) });
  let planned: Promise<unknown> | undefined;
  try {
    await manager.initialize();
    const session = await manager.create({ projectId: 'project', workspace: f.options.workspace, engine: 'codex', model: 'fixture', connectionId: 'fixture', cloudAllowed: true, allowanceMicrousd: 1_000_000 });
    planned = manager.plan(session.id, 'project', 'No provider request may run.').catch(error => error);
    await Promise.race([blocked.entered, planned.then(value => { throw new Error('Plan completed before the config write: ' + JSON.stringify(value)); })]);
    assert.equal((await planned as { code: string }).code, 'cleanup_required');
    await assert.rejects(manager.dispose(), { code: 'runtime_preparation_pending' });
    assert.ok(scheduler.activeOwner);
    const contender = new SessionStore(store.root);
    await assert.rejects(contender.acquireLease(), /SESSION_STORE_IN_USE/);
    await assert.rejects(fs.access(configPath), { code: 'ENOENT' });
    blocked.release(); await adapter!.dispose(); await manager.dispose();
    assert.equal(scheduler.activeOwner, null); assert.equal(launches, 0);
    assert.match(await fs.readFile(configPath, 'utf8'), /model_provider = "hormuz"/);
    const release = await contender.acquireLease(); await release();
    assert.equal(((await store.load(session.id, 'profile', 'project')).accounting as { requestCount: number }).requestCount, 0);
  } finally {
    blocked.release(); await planned; t.mock.restoreAll(); syncBuiltinESMExports();
    await manager.dispose(); await fs.rm(f.root, { recursive: true, force: true });
  }
});
