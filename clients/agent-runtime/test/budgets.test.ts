import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, realpath, rm, writeFile, link } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { BudgetLedger } from '../src/budgets.ts';

test('durable content-free parent receipts prevent session deletion/recreation refunds', async () => {
  const root = await realpath(await mkdtemp(join(tmpdir(), 'hormuz-budget-test-')));
  try {
    const ledger = new BudgetLedger(join(root, 'usage')), limits = { profileMicrousd: 100, projectMicrousd: 60 };
    await ledger.checkpoint('s1', 'profile', 'project', { spentMicrousd: 30, heldMicrousd: 20, unknownMicrousd: 20, requestCount: 1 }, limits);
    const restarted = new BudgetLedger(join(root, 'usage'));
    assert.deepEqual(await restarted.totals('profile', 'project'), { profileMicrousd: 50, projectMicrousd: 50 });
    await assert.rejects(restarted.checkpoint('s2', 'profile', 'project', { spentMicrousd: 0, heldMicrousd: 11, unknownMicrousd: 0, requestCount: 1 }, limits), /parent_allowance_exhausted/);
    await assert.rejects(restarted.checkpoint('s1', 'profile', 'project', { spentMicrousd: 0, heldMicrousd: 0, unknownMicrousd: 0, requestCount: 0 }, limits), /budget_history_regression/);
    await assert.rejects(restarted.checkpoint('s1', 'other', 'project', { spentMicrousd: 30, heldMicrousd: 20, unknownMicrousd: 20, requestCount: 1 }, limits), /budget_scope_mismatch/);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('budget reads remain bounded and scope-safe while complete checkpoint receipts are replaced', async () => {
  const root = await realpath(await mkdtemp(join(tmpdir(), 'hormuz-budget-reader-test-')));
  try {
    const writer = new BudgetLedger(join(root, 'usage')), reader = new BudgetLedger(join(root, 'usage'));
    const limits = { profileMicrousd: 100, projectMicrousd: 100 };
    await writer.checkpoint('s1', 'profile', 'project', { spentMicrousd: 0, heldMicrousd: 0, unknownMicrousd: 0, requestCount: 0 }, limits);
    const writing = (async () => {
      for (let index = 1; index <= 40; index++) await writer.checkpoint('s1', 'profile', 'project', { spentMicrousd: index, heldMicrousd: 0, unknownMicrousd: 0, requestCount: index }, limits);
    })();
    const reading = (async () => {
      for (let index = 0; index < 40; index++) {
        const account = await reader.forSession('s1', 'profile', 'project');
        assert.ok(Number.isSafeInteger(account.spentMicrousd));
        assert.ok(account.spentMicrousd >= 0 && account.spentMicrousd <= 40);
        assert.equal(account.spentMicrousd, account.requestCount);
      }
    })();
    await Promise.all([writing, reading]);
    assert.equal((await reader.forSession('s1', 'profile', 'project')).spentMicrousd, 40);
    const path = join(root, 'usage', 's1.json');
    await writeFile(path, ' '.repeat(4097), { mode: 0o600 });
    await assert.rejects(reader.forSession('s1', 'profile', 'project'), /unsafe_budget_receipt/);
    await writeFile(path, JSON.stringify({ schemaVersion: 1, sessionId: 1, profileId: 'profile', projectId: 'project', accounting: { spentMicrousd: 0, heldMicrousd: 0, unknownMicrousd: 0, requestCount: 0 } }), { mode: 0o600 });
    await assert.rejects(reader.forSession('s1', 'profile', 'project'), /invalid_budget_identity/);
    await link(path, join(root, 'outside-receipt.json'));
    await assert.rejects(reader.forSession('s1', 'profile', 'project'), /unsafe_budget_receipt/);
  } finally { await rm(root, { recursive: true, force: true }); }
});
