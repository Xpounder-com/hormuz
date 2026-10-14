import { AgentError } from './types.ts';

/** Own pre-spawn filesystem work until it settles, even after cancellation. */
export class RuntimePreparation {
  private closed = false;
  private pending = new Set<Promise<void>>();

  begin(): () => void {
    if (this.closed) throw new AgentError('runtime_closed');
    if (this.pending.size >= 2) throw new AgentError('runtime_busy');
    let resolve!: () => void;
    const completion = new Promise<void>(done => { resolve = done; });
    this.pending.add(completion);
    let finished = false;
    return () => {
      if (finished) return;
      finished = true;
      this.pending.delete(completion);
      resolve();
    };
  }

  async drain(timeoutMs = 2_000): Promise<void> {
    this.closed = true;
    if (!this.pending.size) return;
    // Cleanup failure leaves every unfinished token retained. The caller must
    // keep its scheduler/profile lease and retry after the owned work settles.
    const delay = Number.isFinite(timeoutMs) ? Math.max(1, Math.min(timeoutMs, 30_000)) : 2_000;
    let timer: NodeJS.Timeout | undefined;
    try {
      await Promise.race([
        Promise.all(this.pending),
        new Promise<never>((_resolve, reject) => { timer = setTimeout(() => reject(new AgentError('runtime_preparation_pending')), delay); }),
      ]);
    } finally { clearTimeout(timer); }
  }
}
