import { constants } from 'node:fs';
import { lstat, mkdir, open, readdir, rename, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';
import { AgentError } from './types.ts';

export type BudgetAccounting = { spentMicrousd: number; heldMicrousd: number; unknownMicrousd: number; requestCount: number };
type Receipt = { schemaVersion: 1; sessionId: string; profileId: string; projectId: string; accounting: BudgetAccounting };
const identity = (value: string) => { if (typeof value !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(value)) throw new AgentError('invalid_budget_identity'); };
const amount = (value: number) => { if (!Number.isSafeInteger(value) || value < 0) throw new AgentError('invalid_budget_amount'); };
const total = (accounting: BudgetAccounting) => accounting.spentMicrousd + accounting.heldMicrousd;

/** Content-free lifetime receipts survive session deletion; one owning SessionStore lease serializes checkpoints. */
export class BudgetLedger {
  root: string;
  constructor(root: string) { this.root = root; }
  async #directory() {
    try { await mkdir(this.root, { mode: 0o700 }); } catch (error) { if ((error as NodeJS.ErrnoException).code !== 'EEXIST') throw error; }
    const info = await lstat(this.root);
    if (!info.isDirectory() || info.isSymbolicLink() || (info.mode & 0o777) !== 0o700) throw new AgentError('unsafe_budget_store');
  }
  async #read(path: string): Promise<Receipt> {
    for (let attempt = 0; attempt < 3; attempt++) {
      const handle = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW);
      try {
        const info = await handle.stat();
        if (!info.isFile() || info.nlink > 1 || info.size > 4096 || (info.mode & 0o777) !== 0o600) throw new AgentError('unsafe_budget_receipt');
        if (info.nlink === 0) continue;
        const buffer = Buffer.alloc(info.size + 1);
        let offset = 0;
        while (offset < buffer.length) {
          const result = await handle.read(buffer, offset, buffer.length - offset, offset);
          if (result.bytesRead === 0) break;
          offset += result.bytesRead;
        }
        const after = await handle.stat();
        if (offset !== info.size || after.size !== info.size || after.mtimeMs !== info.mtimeMs || (after.mode & 0o777) !== 0o600 || after.nlink > 1) throw new AgentError('corrupt_budget_receipt');
        if (after.nlink === 0) continue; // Owning checkpoint atomically replaced this version.
        if (after.ctimeMs !== info.ctimeMs) throw new AgentError('corrupt_budget_receipt');
        const receipt = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(buffer.subarray(0, offset))) as Receipt;
        if (!receipt || receipt.schemaVersion !== 1 || !receipt.accounting) throw new AgentError('corrupt_budget_receipt');
        identity(receipt.sessionId); identity(receipt.profileId); identity(receipt.projectId);
        for (const key of ['spentMicrousd', 'heldMicrousd', 'unknownMicrousd', 'requestCount'] as const) amount(receipt.accounting[key]);
        if (receipt.accounting.unknownMicrousd > receipt.accounting.heldMicrousd || !Number.isSafeInteger(total(receipt.accounting))) throw new AgentError('corrupt_budget_receipt');
        return receipt;
      } finally { await handle.close(); }
    }
    throw new AgentError('corrupt_budget_receipt');
  }
  async totals(profileId: string, projectId: string) {
    identity(profileId); identity(projectId); await this.#directory();
    const names = await readdir(this.root);
    if (names.length > 10_000) throw new AgentError('budget_history_limit');
    let profileMicrousd = 0, projectMicrousd = 0;
    for (const name of names) {
      if (!name.endsWith('.json') || name.startsWith('.')) continue;
      const receipt = await this.#read(join(this.root, name));
      if (name !== `${receipt.sessionId}.json`) throw new AgentError('corrupt_budget_receipt');
      if (receipt.profileId === profileId) {
        profileMicrousd += total(receipt.accounting);
        if (receipt.projectId === projectId) projectMicrousd += total(receipt.accounting);
      }
    }
    if (!Number.isSafeInteger(profileMicrousd) || !Number.isSafeInteger(projectMicrousd)) throw new AgentError('budget_total_overflow');
    return { profileMicrousd, projectMicrousd };
  }
  async forSession(sessionId: string, profileId: string, projectId: string) {
    identity(sessionId); identity(profileId); identity(projectId); await this.#directory();
    const receipt = await this.#read(join(this.root, `${sessionId}.json`));
    if (receipt.sessionId !== sessionId || receipt.profileId !== profileId || receipt.projectId !== projectId) throw new AgentError('budget_scope_mismatch');
    return { ...receipt.accounting };
  }
  async checkpoint(sessionId: string, profileId: string, projectId: string, accounting: BudgetAccounting, limits: { profileMicrousd: number; projectMicrousd: number }) {
    identity(sessionId); identity(profileId); identity(projectId); await this.#directory();
    for (const key of ['spentMicrousd', 'heldMicrousd', 'unknownMicrousd', 'requestCount'] as const) amount(accounting[key]);
    amount(limits.profileMicrousd); amount(limits.projectMicrousd);
    if (accounting.unknownMicrousd > accounting.heldMicrousd || !Number.isSafeInteger(total(accounting))) throw new AgentError('invalid_budget_amount');
    const path = join(this.root, `${sessionId}.json`);
    let previous: Receipt | undefined;
    try { previous = await this.#read(path); } catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error; }
    if (previous && (previous.sessionId !== sessionId || previous.profileId !== profileId || previous.projectId !== projectId)) throw new AgentError('budget_scope_mismatch');
    if (previous && (accounting.spentMicrousd < previous.accounting.spentMicrousd || accounting.requestCount < previous.accounting.requestCount)) throw new AgentError('budget_history_regression');
    const sums = await this.totals(profileId, projectId), old = previous ? total(previous.accounting) : 0;
    if (sums.profileMicrousd - old + total(accounting) > limits.profileMicrousd || sums.projectMicrousd - old + total(accounting) > limits.projectMicrousd) throw new AgentError('parent_allowance_exhausted');
    const receipt: Receipt = { schemaVersion: 1, sessionId, profileId, projectId, accounting: { ...accounting } };
    const temp = join(this.root, `.${sessionId}.${randomUUID()}.tmp`);
    const handle = await open(temp, constants.O_CREAT | constants.O_EXCL | constants.O_WRONLY | constants.O_NOFOLLOW, 0o600);
    try {
      try { await handle.writeFile(JSON.stringify(receipt)); await handle.sync(); } finally { await handle.close(); }
      await this.#directory(); await rename(temp, path); const dir = await open(this.root, constants.O_RDONLY | constants.O_NOFOLLOW); try { await dir.sync(); } finally { await dir.close(); }
    }
    finally { await rm(temp, { force: true }); }
  }
}
