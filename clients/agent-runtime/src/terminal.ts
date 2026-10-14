import { stripVTControlCharacters } from 'node:util';
import { StringDecoder } from 'node:string_decoder';
import type { AgentSessions } from './sessions.ts';
import { AgentError } from './types.ts';
import type { ActionRequest, ApprovalDecision, RuntimeEvent } from './types.ts';

export type TerminalOptions = {
  sessions: AgentSessions;
  projectId: string;
  workspace: string;
  engine: 'codex' | 'claude';
  model: string;
  allowanceMicrousd: number;
  listSessions: () => Promise<Array<{ id: string; phase: string; model: string; engine: string; updatedAt: number }>>;
  onCloudConsent: (allowed: boolean) => void;
  initialPrompt?: string;
  resumeSessionId?: string;
  /** Explicitly injected IO is trusted application/test wiring. Production uses a TTY. */
  input?: NodeJS.ReadableStream;
  output?: NodeJS.WritableStream;
};

const LINE_LIMIT = 64 * 1024;
const QUEUE_LIMIT = 8;
const OUTPUT_LIMIT = 1024 * 1024;
type SessionView = Awaited<ReturnType<AgentSessions['get']>>;
type ApprovalEvent = ActionRequest & { sessionId: string; projectId: string; approvalId: string; fingerprint: string };
type TTYInput = NodeJS.ReadableStream & { isTTY?: boolean; isRaw?: boolean; setRawMode?: (mode: boolean) => unknown; pause?: () => unknown };

/** Provider strings never supply terminal commands, hyperlinks, clipboard escapes, or carriage returns. */
export function sanitizeTerminalText(value: string): string {
  return stripVTControlCharacters(value
    .replace(/\x1b\][\s\S]*?(?:\x07|\x1b\\|$)/g, '')
    .replace(/\x1b[P^_][\s\S]*?(?:\x1b\\|$)/g, ''))
    .replace(/\x1b[^\r\n]*/g, '')
    .replace(/\r\n?/g, '\n')
    .replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u200e\u200f\u202a-\u202e\u2066-\u2069]/g, '');
}
function display(value: unknown): string {
  const text = typeof value === 'string' ? value : JSON.stringify(value, null, 2) ?? '';
  return sanitizeTerminalText(text).split('\n').map(line => `  ${line}`).join('\n');
}

/** One write at a time; the pending bytes include the currently blocked write. */
class OutputBroker {
  #output: NodeJS.WritableStream;
  #queue: Array<{ frame: Buffer; resolve: () => void }> = [];
  #bytes = 0;
  #writing = false;
  #closed = false;
  #fail: (error: Error) => void;
  #timer?: ReturnType<typeof setTimeout>;
  #current?: { frame: Buffer; resolve: () => void };
  constructor(output: NodeJS.WritableStream, fail: (error: Error) => void) { this.#output = output; this.#fail = fail; }
  write(text: string): Promise<void> {
    if (this.#closed || !text) return Promise.resolve();
    const frame = Buffer.from(text);
    if (this.#bytes + frame.length > OUTPUT_LIMIT || this.#queue.length >= 512) {
      this.#fail(new AgentError('terminal_output_limit')); this.close(); return Promise.resolve();
    }
    this.#bytes += frame.length;
    return new Promise(resolve => { this.#queue.push({ frame, resolve }); this.#pump(); });
  }
  #pump() {
    if (this.#writing || this.#closed) return;
    const item = this.#queue.shift(); if (!item) return;
    this.#writing = true; this.#current = item;
    let finished = false;
    const complete = (error?: Error | null) => {
      if (finished) return; finished = true; clearTimeout(this.#timer);
      this.#bytes -= item.frame.length; this.#writing = false; this.#current = undefined; item.resolve();
      if (error) { this.#fail(new AgentError('terminal_output_failed')); this.close(); }
      else this.#pump();
    };
    this.#timer = setTimeout(() => { complete(new AgentError('terminal_output_timeout')); }, 10_000);
    try { this.#output.write(item.frame, complete); } catch { complete(new AgentError('terminal_output_failed')); }
  }
  close() {
    if (this.#closed) return; this.#closed = true; clearTimeout(this.#timer);
    this.#current?.resolve(); this.#current = undefined;
    for (const item of this.#queue.splice(0)) item.resolve();
  }
}

/** A single bounded parser owns stdin for both REPL commands and runtime questions. */
class InputBroker {
  #input: TTYInput;
  #line = Buffer.alloc(LINE_LIMIT);
  #length = 0;
  #queue: string[] = [];
  #pending?: { resolve: (line: string | null) => void; cleanup: () => void };
  #closed = false;
  #escape = false;
  #csi = false;
  #previousCR = false;
  #raw: boolean;
  #interrupt: (idleQuit?: boolean) => void;
  #fail: (error: Error) => void;
  #echo: (text: string) => void;
  #finish: () => void;
  #echoDecoder = new StringDecoder('utf8');
  constructor(input: TTYInput, raw: boolean, interrupt: (idleQuit?: boolean) => void, fail: (error: Error) => void, echo: (text: string) => void, finish: () => void) {
    this.#input = input; this.#raw = raw; this.#interrupt = interrupt; this.#fail = fail; this.#echo = echo;
    this.#finish = finish;
    input.on('data', this.#data); input.on('end', this.#end); input.on('close', this.#end); input.on('error', this.#error);
    if ((input as { readableEnded?: boolean; destroyed?: boolean }).readableEnded || (input as { destroyed?: boolean }).destroyed) this.#end();
  }
  #error = () => { this.#fail(new AgentError('terminal_input_failed')); this.close(); };
  #end = () => { this.close(); this.#finish(); };
  #data = (chunk: Buffer | string) => {
    if (this.#closed) return;
    const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    let echo = '';
    for (const byte of bytes) {
      if (this.#closed) break;
      if (byte === 3) { this.discard(); this.#interrupt(); continue; }
      if (byte === 4 && this.#raw && this.#length === 0) { this.close(); this.#finish(); continue; }
      if (this.#escape) {
        if (!this.#csi && byte === 91) { this.#csi = true; continue; }
        if (!this.#csi || byte >= 64 && byte <= 126) { this.#escape = false; this.#csi = false; }
        continue;
      }
      if (byte === 27) { this.#escape = true; continue; }
      if (byte === 10 && this.#previousCR) { this.#previousCR = false; continue; }
      this.#previousCR = byte === 13;
      if (byte === 10 || byte === 13) {
        let line: string;
        try { line = new TextDecoder('utf-8', { fatal: true }).decode(this.#line.subarray(0, this.#length)); }
        catch { this.#fail(new AgentError('terminal_invalid_utf8')); this.close(); break; }
        this.#length = 0; if (this.#raw) echo += '\n';
        if (line.trim() === '/interrupt') { this.discard(); this.#interrupt(false); continue; }
        if (this.#pending) { const pending = this.#pending; this.#pending = undefined; pending.cleanup(); pending.resolve(line); }
        else if (this.#queue.length < QUEUE_LIMIT) this.#queue.push(line);
        else { this.#fail(new AgentError('terminal_input_queue_limit')); this.close(); }
        continue;
      }
      if ((byte === 127 || byte === 8) && this.#raw) {
        if (this.#length) { this.#length--; while (this.#length > 0 && (this.#line[this.#length]! & 0xc0) === 0x80) this.#length--; echo += '\b \b'; }
        continue;
      }
      if (byte < 32 && byte !== 9 || byte === 127) continue;
      if (this.#length === LINE_LIMIT) { this.#fail(new AgentError('terminal_input_limit')); this.close(); break; }
      this.#line[this.#length++] = byte;
      if (this.#raw) echo += String.fromCharCode(byte);
    }
    if (echo) this.#echo(this.#echoDecoder.write(Buffer.from(echo, 'latin1')));
  };
  discard() { this.#queue = []; this.#length = 0; this.#echoDecoder = new StringDecoder('utf8'); }
  read(signal?: AbortSignal, fresh = false): Promise<string | null> {
    if (fresh) this.discard();
    if (this.#closed || signal?.aborted) return Promise.resolve(null);
    if (this.#pending) throw new AgentError('terminal_concurrent_prompt');
    const queued = this.#queue.shift(); if (queued !== undefined) return Promise.resolve(queued);
    return new Promise(resolve => {
      const abort = () => { if (this.#pending?.resolve === resolve) { this.#pending.cleanup(); this.#pending = undefined; resolve(null); } };
      signal?.addEventListener('abort', abort, { once: true });
      this.#pending = { resolve, cleanup: () => signal?.removeEventListener('abort', abort) };
    });
  }
  close() {
    if (this.#closed) return; this.#closed = true; this.discard();
    const pending = this.#pending; this.#pending = undefined; pending?.cleanup(); pending?.resolve(null);
  }
  get closed() { return this.#closed; }
  dispose() {
    this.close(); this.#input.removeListener('data', this.#data); this.#input.removeListener('end', this.#end);
    this.#input.removeListener('close', this.#end); this.#input.removeListener('error', this.#error); this.#input.pause?.();
  }
}

const HELP = 'Type a request to make a read-only plan.\n/plan [TEXT]  /accept  /revise TEXT  /reject  /execute\n/diff  /review accept|reject|changes  /status  /sessions\n/resume ID  /new  /interrupt  /help  /quit\n/interrupt or Ctrl+C interrupts active work; Ctrl+C while idle quits.\n';

/** The terminal never grants authority by replaying a transcript or executing a typed request directly. */
export async function runTerminal(options: TerminalOptions): Promise<number> {
  const input = (options.input ?? process.stdin) as TTYInput;
  const output = options.output ?? process.stdout;
  const injected = options.input !== undefined && options.output !== undefined;
  const isTTY = input.isTTY === true && (output as { isTTY?: boolean }).isTTY === true;
  let exitCode = 0, stopping = false, consent = false;
  let broker: InputBroker | undefined;
  let session: SessionView | undefined, acceptedRevision: string | undefined, lastPrompt: string | undefined;
  let reviewRevision: string | undefined;
  let active: { id: string; controller: AbortController; interrupted: boolean } | undefined;
  let approvalTail = Promise.resolve(), approvalCount = 0;
  const approvalControllers = new Map<string, AbortController>();
  const cancelled = () => stopping || broker?.closed === true;
  const stop = (error?: Error) => {
    if (error) exitCode = 1; stopping = true; broker?.close(); active?.controller.abort();
    consent = false;
    try { options.onCloudConsent(false); } catch { exitCode = 1; }
    void options.sessions.dispose().catch(() => { exitCode = 1; });
  };
  const writer = new OutputBroker(output, error => stop(error));
  const say = (text: string) => writer.write(text);
  const interrupt = (idleQuit = true) => {
    if (!active) { if (idleQuit) stop(); else void say('\nNo active work.\nhormuz> '); return; }
    active.interrupted = true; active.controller.abort(); broker?.discard();
    void say('\nInterrupting active work.\n');
    void options.sessions.interrupt(active.id, options.projectId).catch(error => {
      if (!(error instanceof AgentError && error.code === 'session_not_running')) stop(error as Error);
    });
  };
  const outputError = () => stop(new AgentError('terminal_output_failed'));
  const signalStop = () => stop();
  const consentPrompt = async () => {
    if (cancelled()) return false;
    if (consent) return true;
    options.onCloudConsent(false);
    await say(`\nCloud inference sends this project's prompts and tool context to ${options.engine === 'codex' ? 'OpenAI' : 'Anthropic'}.\nProject: ${sanitizeTerminalText(options.projectId)}\nWorkspace: ${sanitizeTerminalText(options.workspace)}\nEngine/model: ${options.engine} / ${sanitizeTerminalText(options.model)}\nSession allowance: $${(options.allowanceMicrousd / 1_000_000).toFixed(6)}\nAllow cloud inference for this launch? Type yes [default: no]: `);
    if (cancelled()) return false;
    const answer = await broker!.read(undefined, true);
    if (cancelled()) return false;
    consent = answer?.trim().toLowerCase() === 'yes'; options.onCloudConsent(consent);
    await say(consent ? 'Cloud inference allowed for this launch.\n' : 'Cloud inference declined.\n');
    return consent && !cancelled();
  };
  const compatible = (view: SessionView) => {
    if (view.engine !== options.engine || view.model !== options.model || view.workspace !== options.workspace || view.projectId !== options.projectId) throw new AgentError('terminal_session_configuration_mismatch');
  };
  const update = (view: SessionView) => { compatible(view); if (view.plan?.revision !== session?.plan?.revision) acceptedRevision = undefined; session = view; };
  const create = async () => {
    if (cancelled()) return;
    const view = await options.sessions.create({ projectId: options.projectId, workspace: options.workspace, engine: options.engine, model: options.model, connectionId: 'provider', cloudAllowed: true, allowanceMicrousd: options.allowanceMicrousd });
    if (cancelled()) return;
    update(view);
    acceptedRevision = undefined; reviewRevision = undefined; lastPrompt = undefined;
    await say(`Session ${session!.id}. Engine/model fixed for this launch.\n`);
  };
  const current = () => { if (!session) throw new AgentError('terminal_session_required'); return session; };
  const status = async () => {
    const view = await options.sessions.get(current().id, options.projectId);
    if (cancelled()) return;
    update(view);
    const money = (amount: number) => `$${(amount / 1_000_000).toFixed(6)}`;
    await say(`Session: ${sanitizeTerminalText(view.id)}\nPhase: ${sanitizeTerminalText(view.phase)}\nEngine/model: ${view.engine} / ${sanitizeTerminalText(view.model)}\nConfigured-rate accounting: spent ${money(view.accounting.spentMicrousd)}, held ${money(view.accounting.heldMicrousd)}, uncertain ${money(view.accounting.unknownMicrousd)}.\nUncertain usage is included in held funds.\nAllowance: ${money(view.allowanceMicrousd)}\nRequests: ${view.accounting.requestCount}. Runtime attempts: ${view.runtimeAttempts}.\nPlan accepted in this launch: ${acceptedRevision === view.plan?.revision && acceptedRevision !== undefined ? 'yes' : 'no'}\n`);
  };
  const decide = async (request: ApprovalEvent, decision: ApprovalDecision) => {
    try { await options.sessions.decide(request.sessionId, options.projectId, request.approvalId, request.fingerprint, decision); return true; }
    catch (error) { await say(`Approval was not applied: ${sanitizeTerminalText(error instanceof AgentError ? error.code : 'approval_failed')}.\n`); return false; }
  };
  const approve = async (request: ApprovalEvent, run: NonNullable<typeof active>, controller: AbortController) => {
    const signal = AbortSignal.any([run.controller.signal, controller.signal]);
    if (signal.aborted || stopping) return;
    let decision: ApprovalDecision = { decision: 'decline' };
    await say(`\nApproval ${sanitizeTerminalText(request.approvalId)} (${request.kind})\nWorking directory: ${sanitizeTerminalText(options.workspace)}\nRequested operation:\n${display(request.operation)}\n`);
    if (request.kind === 'question') {
      const questions = request.operation.questions;
      if (Array.isArray(questions) && questions.length > 0 && questions.length <= 10) {
        const answers: Record<string, string[]> = Object.create(null);
        let complete = true;
        for (const value of questions) {
          if (!value || typeof value !== 'object' || typeof value.id !== 'string' || typeof value.question !== 'string') { complete = false; break; }
          const choices = Array.isArray(value.options) ? value.options : [];
          await say(`Question ${sanitizeTerminalText(value.id)}:\n${display(value.question)}\n${choices.map((choice: unknown, index: number) => `${index + 1}. ${display(choice)}`).join('\n')}\nAnswer explicitly (option number or text; blank declines): `);
          const answer = await broker!.read(signal, true);
          if (!answer?.trim() || answer.trim().startsWith('/')) { complete = false; if (answer?.trim() === '/quit') stop(); break; }
          const trimmed = answer.trim();
          const selected = trimmed.split(',').map(part => part.trim());
          if (choices.length && selected.every(part => /^\d+$/.test(part))) {
            const labels = selected.map(part => choices[Number(part) - 1]?.label);
            if (labels.some(label => typeof label !== 'string') || value.multiSelect !== true && labels.length !== 1) { complete = false; break; }
            answers[value.id] = labels as string[];
          } else answers[value.id] = [trimmed];
        }
        if (complete) decision = { decision: 'accept', answers };
      }
    } else {
      await say('Approve this action once? Type yes [default: no]: ');
      const answer = await broker!.read(signal, true);
      if (answer?.trim().toLowerCase() === 'yes') decision = { decision: 'accept' };
      else if (answer?.trim() === '/quit') stop();
    }
    if (signal.aborted || stopping || broker!.closed) return;
    if (await decide(request, decision)) await say(decision.decision === 'accept' ? 'Approved once.\n' : 'Declined.\n');
  };
  const onApproval = (request: ApprovalEvent) => {
    const run = active;
    if (!run || request.sessionId !== run.id || request.projectId !== options.projectId) return;
    if (++approvalCount > QUEUE_LIMIT) { stop(new AgentError('terminal_approval_queue_limit')); return; }
    const controller = new AbortController(); approvalControllers.set(request.approvalId, controller);
    approvalTail = approvalTail.then(() => approve(request, run, controller)).catch(error => stop(error as Error)).finally(() => {
      approvalCount--; approvalControllers.delete(request.approvalId);
    });
  };
  const onApprovalResolved = (event: { sessionId: string; approvalId: string }) => {
    if (event.sessionId === active?.id) approvalControllers.get(event.approvalId)?.abort();
  };
  const onEvent = (event: RuntimeEvent & { sessionId: string; projectId: string }) => {
    if (!active || event.sessionId !== active.id || event.projectId !== options.projectId) return;
    if (active.interrupted) return;
    void say(`\n[${event.type}]\n${display(event.data)}\n`);
  };
  const run = async (action: () => Promise<SessionView>) => {
    if (cancelled()) return;
    const state = { id: current().id, controller: new AbortController(), interrupted: false };
    active = state; reviewRevision = undefined;
    try { const view = await action(); if (!cancelled()) update(view); }
    finally { state.controller.abort(); await approvalTail; active = undefined; broker!.discard(); }
    if (cancelled()) return;
    await say(`Phase: ${current().phase}.\n`);
    if (current().phase === 'ready' && current().plan) await say(`Plan revision: ${current().plan!.revision}\n${display(current().plan!.text)}\nUse /accept, then /execute to act.\n`);
  };
  const plan = async (text: string) => {
    if (cancelled()) return;
    if (!text.trim()) throw new AgentError('invalid_prompt');
    if (!await consentPrompt() || cancelled()) return;
    if (!session) await create();
    if (cancelled()) return;
    lastPrompt = text; acceptedRevision = undefined;
    await run(() => options.sessions.plan(current().id, options.projectId, text));
  };
  const resume = async (id: string) => {
    if (cancelled()) return;
    if (!id) throw new AgentError('terminal_session_required');
    const stored = await options.sessions.get(id, options.projectId);
    if (cancelled()) return;
    compatible(stored);
    if (!await consentPrompt() || cancelled()) return;
    const resumed = await options.sessions.resume(id, options.projectId);
    if (cancelled()) return;
    update(resumed); acceptedRevision = undefined; reviewRevision = undefined; lastPrompt = undefined;
    await say(`Resumed ${id}. Phase: ${current().phase}. Any plan needs a fresh /accept.\n`);
  };
  const diff = async () => {
    const result = await options.sessions.review(current().id, options.projectId); reviewRevision = result.observedRevision;
    await say(`Observed revision: ${sanitizeTerminalText(result.observedRevision)}\nAuthorship: ${result.authorship}\n`);
    for (const change of result.changes) {
      await say(`\n${change.kind}: ${sanitizeTerminalText(change.path)}\n`);
      if (change.binary || change.sensitive) await say('Text unavailable (binary or sensitive file).\n');
      else await say(`Before:\n${display(change.beforeText ?? '(absent or unavailable)')}\nAfter:\n${display(change.afterText ?? '(absent or unavailable)')}\n`);
    }
    await say(`Changes: ${result.changes.length}. Unavailable: ${result.unavailable.length}.\nReview records a decision only; files stay as observed.\n`);
  };
  const command = async (line: string) => {
    if (cancelled()) return;
    const trimmed = line.trim(); if (!trimmed) return;
    if (!trimmed.startsWith('/')) { await plan(line); return; }
    const space = trimmed.indexOf(' '), name = space < 0 ? trimmed : trimmed.slice(0, space), arg = space < 0 ? '' : trimmed.slice(space + 1).trim();
    switch (name) {
      case '/quit': stop(); break;
      case '/help': await say(HELP); break;
      case '/new': await create(); break;
      case '/plan': await plan(arg || lastPrompt || ''); break;
      case '/resume': await resume(arg); break;
      case '/sessions': {
        const rows = await options.listSessions();
        for (const row of rows) await say(`${sanitizeTerminalText(row.id)}  ${sanitizeTerminalText(row.phase)}  ${sanitizeTerminalText(row.engine)}/${sanitizeTerminalText(row.model)}  ${new Date(row.updatedAt).toISOString()}\n`);
        if (!rows.length) await say('No sessions for this project.\n'); break;
      }
      case '/status': await status(); break;
      case '/accept': {
        const view = current(); if (!view.plan) throw new AgentError('terminal_plan_required');
        update(await options.sessions.acceptPlan(view.id, options.projectId, view.plan.revision)); acceptedRevision = view.plan.revision;
        await say('Plan accepted. Use /execute to start; acceptance alone runs nothing.\n'); break;
      }
      case '/revise': {
        const view = current(); if (!view.plan) throw new AgentError('terminal_plan_required');
        update(await options.sessions.revisePlan(view.id, options.projectId, view.plan.revision, arg)); acceptedRevision = undefined;
        await say(`Plan revised; acceptance cleared.\n${display(current().plan!.text)}\n`); break;
      }
      case '/reject': {
        const view = current(); if (!view.plan) throw new AgentError('terminal_plan_required');
        update(await options.sessions.rejectPlan(view.id, options.projectId, view.plan.revision)); acceptedRevision = undefined; await say('Plan rejected.\n'); break;
      }
      case '/execute': {
        const view = current(); if (!view.plan || acceptedRevision !== view.plan.revision) throw new AgentError('plan_not_accepted');
        if (!await consentPrompt() || cancelled()) break; acceptedRevision = undefined;
        await run(() => options.sessions.execute(view.id, options.projectId, view.plan!.revision)); break;
      }
      case '/diff': await diff(); break;
      case '/review': {
        const disposition = { accept: 'accepted', reject: 'rejected', changes: 'request_changes' }[arg] as 'accepted' | 'rejected' | 'request_changes' | undefined;
        if (!disposition) throw new AgentError('invalid_review_disposition');
        if (!reviewRevision) throw new AgentError('terminal_diff_required');
        await options.sessions.recordReview(current().id, options.projectId, reviewRevision, disposition);
        await say(`Review ${disposition} recorded; no execution authority granted.\n`); break;
      }
      default: await say('Unknown command. Use /help.\n');
    }
  };
  const oldRaw = input.isRaw ?? false;
  output.on('error', outputError);
  try {
    if (!injected && !isTTY) { await say('Interactive terminal requires a TTY; piped approval input is not accepted.\n'); return 1; }
    options.onCloudConsent(false);
    if (isTTY && input.setRawMode) input.setRawMode(true);
    broker = new InputBroker(input, isTTY, interrupt, stop, text => { void say(text); }, signalStop);
    process.on('SIGINT', interrupt); process.on('SIGTERM', signalStop);
    options.sessions.on('event', onEvent); options.sessions.on('approval', onApproval); options.sessions.on('approval-resolved', onApprovalResolved);
    if (!cancelled()) await options.sessions.initialize();
    if (!cancelled()) await say(`Hormuz agent terminal\nProject: ${sanitizeTerminalText(options.projectId)}\nWorkspace: ${sanitizeTerminalText(options.workspace)}\nEngine/model: ${options.engine} / ${sanitizeTerminalText(options.model)}\n${HELP}`);
    if (!cancelled() && options.resumeSessionId) await resume(options.resumeSessionId);
    if (!cancelled() && options.initialPrompt) await plan(options.initialPrompt);
    while (!stopping && !broker.closed) {
      const line = broker.read(); await say('hormuz> ');
      const next = await line; if (next === null) break;
      try { await command(next); }
      catch (error) { if (!cancelled()) await say(`Error: ${sanitizeTerminalText(error instanceof AgentError ? error.code : 'terminal_request_failed')}.\n`); }
    }
  } catch (error) { if (!cancelled()) { exitCode = 1; await say(`Error: ${sanitizeTerminalText(error instanceof AgentError ? error.code : 'terminal_failed')}.\n`); } }
  finally {
    stopping = true; active?.controller.abort(); broker?.close();
    options.sessions.removeListener('event', onEvent); options.sessions.removeListener('approval', onApproval); options.sessions.removeListener('approval-resolved', onApprovalResolved);
    process.removeListener('SIGINT', interrupt); process.removeListener('SIGTERM', signalStop);
    if (isTTY && input.setRawMode) input.setRawMode(oldRaw);
    try { await options.sessions.dispose(); } catch { exitCode = 1; await say('Error: cleanup_required.\n'); }
    await approvalTail; broker?.dispose(); options.onCloudConsent(false); await say('\nTerminal closed.\n'); writer.close(); output.removeListener('error', outputError);
  }
  return exitCode;
}
