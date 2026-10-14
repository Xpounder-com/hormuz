import { spawn } from 'node:child_process';
import type { ChildProcessWithoutNullStreams } from 'node:child_process';
import { AgentError } from './types.ts';

type RpcId = string | number;
type Pending = { resolve: (value: unknown) => void; reject: (error: Error) => void; timer: NodeJS.Timeout };
export type RpcProcessOptions = {
  command: string;
  args: string[];
  cwd: string;
  env: NodeJS.ProcessEnv;
  requestTimeoutMs?: number;
  shutdownTimeoutMs?: number;
  maxFrameBytes?: number;
  maxOutputBytes?: number;
  /** Trusted host/test syscall wiring only; never exposed through the UI bridge. */
  signalProcess?: (pid: number, signal: NodeJS.Signals | 0) => boolean;
  onNotification: (method: string, params: unknown) => void;
  onRequest: (method: string, params: unknown, id: RpcId) => Promise<unknown>;
  onFailure: (error: AgentError) => void;
};

/** Internal stdio transport; only adapters choose methods and executable arguments. */
export class RpcProcess {
  private child: ChildProcessWithoutNullStreams;
  private pending = new Map<number, Pending>();
  private serverRequests = new Set<RpcId>();
  private activeServerRequests = 0;
  private nextId = 1;
  private buffer = Buffer.alloc(0);
  private outputBytes = 0;
  private queuedWriteBytes = 0;
  private failed = false;
  private exited = false;
  private closing?: Promise<void>;
  private closed: Promise<void>;
  private resolveClosed!: () => void;
  private options: RpcProcessOptions;

  constructor(options: RpcProcessOptions) {
    if (process.platform === 'win32') throw new AgentError('unsupported_process_supervision');
    // Do not retain a copy of the child environment (it contains the scoped gateway token).
    this.options = { ...options, env: {} };
    this.closed = new Promise(resolve => { this.resolveClosed = resolve; });
    this.child = spawn(options.command, options.args, {
      cwd: options.cwd, env: options.env, stdio: ['pipe', 'pipe', 'pipe'], detached: true,
    });
    this.child.stdout.on('data', (data: Buffer) => this.read(data));
    // Stderr is counted but never persisted or surfaced: upstream logs can contain secrets.
    this.child.stderr.on('data', (data: Buffer) => this.countOutput(data.length));
    this.child.stdin.on('error', () => this.fail('runtime_pipe_failed'));
    this.child.on('error', () => {
      if (!this.child.pid) { this.exited = true; this.resolveClosed(); }
      this.fail('runtime_spawn_failed');
    });
    this.child.on('exit', () => {
      this.exited = true;
      this.resolveClosed();
      if (!this.closing) this.fail('runtime_exited');
    });
  }

  async request(method: string, params: unknown, timeoutMs = this.options.requestTimeoutMs ?? 30_000): Promise<unknown> {
    if (this.failed || this.closing) throw new AgentError('runtime_closed');
    if (this.pending.size >= 16) throw new AgentError('runtime_request_limit');
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new AgentError('runtime_request_timeout'));
        this.fail('runtime_request_timeout');
      }, timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
      this.send({ id, method, params });
    });
  }

  notify(method: string, params: unknown = {}): void { this.send({ method, params }); }

  private send(frame: unknown): void {
    if (this.failed || this.closing) return;
    const data = Buffer.from(`${JSON.stringify(frame)}\n`);
    const max = this.options.maxFrameBytes ?? 1_048_576;
    if (data.length > max || this.queuedWriteBytes + data.length > max * 2) {
      this.fail('runtime_frame_limit');
      return;
    }
    this.queuedWriteBytes += data.length;
    this.child.stdin.write(data, () => { this.queuedWriteBytes -= data.length; });
  }

  private countOutput(bytes: number): boolean {
    this.outputBytes += bytes;
    if (this.outputBytes > (this.options.maxOutputBytes ?? 64 * 1_048_576)) {
      this.fail('runtime_output_limit');
      return false;
    }
    return true;
  }

  private read(chunk: Buffer): void {
    if (this.failed || !this.countOutput(chunk.length)) return;
    this.buffer = Buffer.concat([this.buffer, chunk]);
    const max = this.options.maxFrameBytes ?? 1_048_576;
    while (!this.failed) {
      const end = this.buffer.indexOf(10);
      if (end < 0) {
        if (this.buffer.length > max) this.fail('runtime_frame_limit');
        return;
      }
      if (end > max) { this.fail('runtime_frame_limit'); return; }
      const line = this.buffer.subarray(0, end).toString('utf8');
      this.buffer = this.buffer.subarray(end + 1);
      if (!line.trim()) continue;
      let frame: unknown;
      try { frame = JSON.parse(line); } catch { this.fail('runtime_invalid_json'); return; }
      if (!frame || typeof frame !== 'object' || Array.isArray(frame)) { this.fail('runtime_invalid_frame'); return; }
      this.dispatch(frame as Record<string, unknown>);
    }
  }

  private dispatch(frame: Record<string, unknown>): void {
    if (typeof frame.method === 'string') {
      if (frame.id === undefined) {
        try { this.options.onNotification(frame.method, frame.params); } catch { this.fail('runtime_invalid_notification'); }
        return;
      }
      const id = frame.id;
      if (!((typeof id === 'string' && id.length <= 128) || (typeof id === 'number' && Number.isSafeInteger(id)))) {
        this.fail('runtime_invalid_request_id'); return;
      }
      if (this.serverRequests.has(id) || this.serverRequests.size >= 4096 || this.activeServerRequests >= 8) {
        this.fail('runtime_server_request_limit'); return;
      }
      this.serverRequests.add(id);
      this.activeServerRequests++;
      void Promise.resolve().then(() => this.options.onRequest(frame.method as string, frame.params, id)).then(
        result => this.send({ id, result }),
        error => this.send({ id, error: { code: error instanceof AgentError && error.code === 'unsupported_server_request' ? -32601 : -32602, message: 'Request rejected by Hormuz' } }),
      ).finally(() => { this.activeServerRequests--; });
      return;
    }
    if (typeof frame.id !== 'number') { this.fail('runtime_invalid_response_id'); return; }
    const request = this.pending.get(frame.id);
    if (!request) return; // Late response to a timed-out request has no authority.
    this.pending.delete(frame.id);
    clearTimeout(request.timer);
    if (frame.error !== undefined) request.reject(new AgentError('runtime_rpc_error'));
    else if (Object.hasOwn(frame, 'result')) request.resolve(frame.result);
    else { request.reject(new AgentError('runtime_invalid_response')); this.fail('runtime_invalid_response'); }
  }

  private fail(code: string): void {
    if (this.failed) return;
    this.failed = true;
    const error = new AgentError(code);
    for (const request of this.pending.values()) { clearTimeout(request.timer); request.reject(error); }
    this.pending.clear();
    this.options.onFailure(error);
    // Keep the rejection observable to the host's dispose() without an unhandled rejection.
    void this.dispose().catch(() => this.options.onFailure(new AgentError('runtime_cleanup_failed')));
  }

  dispose(): Promise<void> {
    if (this.closing) return this.closing;
    this.failed = true;
    const closing = this.shutdown();
    this.closing = closing;
    void closing.catch(() => { if (this.closing === closing) this.closing = undefined; });
    return this.closing;
  }

  private async shutdown(): Promise<void> {
    for (const request of this.pending.values()) { clearTimeout(request.timer); request.reject(new AgentError('runtime_closed')); }
    this.pending.clear();
    const waitMs = this.options.shutdownTimeoutMs ?? 1_000;
    const wait = async () => {
      let timer: NodeJS.Timeout | undefined;
      await Promise.race([this.closed, new Promise<void>(resolve => { timer = setTimeout(resolve, waitMs); })]);
      if (timer) clearTimeout(timer);
    };
    // This process has its own session/process group. Only its descendants are signalled.
    let groupGone = false;
    const signalProcess = this.options.signalProcess ?? process.kill;
    const signalGroup = async (signal: NodeJS.Signals) => {
      if (groupGone || !this.child.pid) return;
      const pid = -this.child.pid;
      try { signalProcess(pid, signal); }
      catch (error) {
        const code = (error as NodeJS.ErrnoException).code;
        if (code === 'ESRCH') { groupGone = true; return; }
        if (code !== 'EPERM') throw error;
        // Darwin can report EPERM while an exiting group disappears. The
        // owned root's waitpid/exit event plus group ESRCH are both required;
        // root exit alone says nothing about surviving descendants.
        if (!this.exited) await wait();
        if (!this.exited) throw new AgentError('runtime_cleanup_required');
        for (let attempt = 0; attempt < 3; attempt++) {
          try { signalProcess(pid, 0); }
          catch (probe) {
            if ((probe as NodeJS.ErrnoException).code === 'ESRCH') { groupGone = true; return; }
          }
          if (attempt < 2) await new Promise<void>(resolve => setTimeout(resolve, Math.max(1, Math.min(waitMs, 25))));
        }
        throw new AgentError('runtime_cleanup_required');
      }
    };
    await signalGroup('SIGTERM');
    this.child.stdin.end();
    await wait();
    await signalGroup('SIGKILL');
    // Node's exit event follows waitpid/reaping. A signalled PID alone is not proof
    // of cleanup; retain the transport for a bounded disposal retry if uncertain.
    await wait();
    if (!this.exited) throw new AgentError('runtime_cleanup_required');
    this.child.stdout.destroy(); this.child.stderr.destroy(); this.child.stdin.destroy();
    this.buffer = Buffer.alloc(0);
  }
}
