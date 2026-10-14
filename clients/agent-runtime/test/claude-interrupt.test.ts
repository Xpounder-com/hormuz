import assert from 'node:assert/strict';
import { mkdir, mkdtemp, realpath, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { RpcProcess } from '../src/rpc.ts';

test('Claude worker reports interruption when an aborted SDK iterator ends without a result', async () => {
  const root = await realpath(await mkdtemp(join(tmpdir(), 'hormuz-claude-abort-')));
  const workspace = join(root, 'project'), configDir = join(root, 'runtime');
  let rpc: RpcProcess | undefined;
  const threadId = '11111111-1111-4111-8111-111111111111', turnId = '22222222-2222-4222-8222-222222222222';
  let initialized!: () => void, complete!: (value: unknown) => void, failInitialization!: (error: Error) => void, failCompletion!: (error: Error) => void;
  const ready = new Promise<void>((resolve, reject) => { initialized = resolve; failInitialization = reject; });
  const completed = new Promise<unknown>((resolve, reject) => { complete = resolve; failCompletion = reject; });
  // A transport failure can reject both promises before their sequential waits.
  void ready.catch(() => {}); void completed.catch(() => {});
  try {
    await mkdir(workspace); await mkdir(configDir, { mode: 0o700 });
    rpc = new RpcProcess({
      command: process.execPath,
      args: [fileURLToPath(new URL('../src/claude-worker.ts', import.meta.url)), fileURLToPath(new URL('./fixtures/claude-query.mjs', import.meta.url))],
      cwd: workspace,
      env: { HOME: configDir, CLAUDE_CONFIG_DIR: configDir, TMPDIR: root, CLAUDE_CODE_TMPDIR: root,
        ANTHROPIC_BASE_URL: 'http://127.0.0.1:9999', ANTHROPIC_API_KEY: 'synthetic-scoped-token' },
      requestTimeoutMs: 3000, shutdownTimeoutMs: 100,
      onNotification: (method, params) => {
        const value = params as { threadId: string; turnId: string; type?: string; data?: { runtime?: unknown }; status?: string };
        assert.equal(value.threadId, threadId); assert.equal(value.turnId, turnId);
        if (method === 'event' && value.type === 'tool' && value.data?.runtime) initialized();
        if (method === 'turn/completed') complete(value.status);
      },
      onRequest: async () => { throw new Error('unexpected_approval'); },
      onFailure: error => { failInitialization(error); failCompletion(error); },
    });
    assert.deepEqual(await rpc.request('initialize', {}), { engine: 'claude', version: '0.3.299' });
    await rpc.request('session/start', { threadId, workspace, model: 'test-model', resume: false });
    await rpc.request('turn/start', { threadId, turnId, prompt: 'HANG', mode: 'execute' });
    await bounded(ready);
    await rpc.request('turn/interrupt', { threadId, turnId });
    // Inspect the actual worker frame rather than the adapter's local fallback;
    // this is independent of interrupt acknowledgement/stdout batching order.
    assert.equal(await bounded(completed), 'interrupted');
  } finally {
    try { await rpc?.dispose(); }
    finally { await rm(root, { recursive: true, force: true }); }
  }
});

async function bounded<T>(promise: Promise<T>): Promise<T> {
  let timer: NodeJS.Timeout | undefined;
  try {
    return await Promise.race([promise, new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new Error('fixture_timeout')), 3000); })]);
  } finally { if (timer) clearTimeout(timer); }
}
