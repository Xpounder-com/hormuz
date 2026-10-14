import { query as sdkQuery, getSessionInfo as sdkGetSessionInfo } from '@anthropic-ai/claude-agent-sdk';
import type { Query, Options, HookJSONOutput, PermissionResult, SDKMessage, SDKUserMessage } from '@anthropic-ai/claude-agent-sdk';
import { createRequire } from 'node:module';
import { readFile, realpath } from 'node:fs/promises';
import { dirname, join, resolve, relative, isAbsolute } from 'node:path';
import { createHash } from 'node:crypto';

const VERSION = '0.3.299';
const CLI_VERSION = '2.1.299';
const object = (value: unknown): Record<string, unknown> => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid object');
  return value as Record<string, unknown>;
};
const fixture = process.argv[2] ? await import(process.argv[2]) : undefined;
const query: typeof sdkQuery = fixture?.query ?? sdkQuery;
const packagePath = join(dirname(createRequire(import.meta.url).resolve('@anthropic-ai/claude-agent-sdk')), 'package.json');
const version = fixture?.sdkVersion ?? JSON.parse(await readFile(packagePath, 'utf8')).version;
let session: { threadId: string; workspace: string; model: string; resume: boolean } | undefined;
type Turn = { turnId: string; mode: 'plan' | 'execute'; controller: AbortController; query?: Query };
let active: Turn | undefined;
let input = Buffer.alloc(0), outputBytes = 0, queuedBytes = 0, nextId = 1;
const pending = new Map<string, { resolve: (value: unknown) => void; timer: NodeJS.Timeout }>();

function send(value: unknown): void {
  const frame = Buffer.from(JSON.stringify(value) + '\n');
  outputBytes += frame.length;
  if (frame.length > 1_048_576 || outputBytes > 64 * 1_048_576 || queuedBytes + frame.length > 2_097_152) { void stop(); return; }
  queuedBytes += frame.length;
  process.stdout.write(frame, () => { queuedBytes -= frame.length; });
}
function event(type: string, data: unknown): void { if (active && session) send({ method: 'event', params: { threadId: session.threadId, turnId: active.turnId, type, data } }); }
async function ask(kind: string, operation: Record<string, unknown>): Promise<Record<string, unknown>> {
  const current = active;
  if (!current || current.controller.signal.aborted || pending.size >= 8) return { decision: 'decline' };
  const id = `approval-${nextId++}`;
  const result = new Promise<unknown>(resolve => {
    const timer = setTimeout(() => { pending.delete(id); resolve({ decision: 'cancel' }); }, 5 * 60_000);
    pending.set(id, { resolve, timer });
  });
  send({ id, method: 'approval', params: { threadId: session!.threadId, turnId: current.turnId, kind, operation } });
  const decision = object(await result);
  return active === current && !current.controller.signal.aborted ? decision : { decision: 'cancel' };
}
const within = (root: string, path: string) => { const rel = relative(root, path); return rel === '' || (!rel.startsWith('..') && !isAbsolute(rel)); };
async function scopedPath(value: unknown, root = session!.workspace): Promise<string> {
  if (typeof value !== 'string' || !value || value.length > 16_384) throw new Error('Invalid path');
  const path = resolve(root, value);
  let ancestor = path;
  const tail: string[] = [];
  for (let depth = 0; depth < 128; depth++) {
    try {
      const canonical = join(await realpath(ancestor), ...tail.reverse());
      if (!within(root, canonical)) throw new Error('Out of scope');
      return canonical;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
      const parent = dirname(ancestor); if (parent === ancestor) throw error;
      tail.push(ancestor.slice(parent.length + 1)); ancestor = parent;
    }
  }
  throw new Error('Path depth limit');
}

async function runTurn(turn: Turn, prompt: string): Promise<void> {
  const decisions = new Map<string, { originalHash: string; approvedHash: string; result: PermissionResult }>();
  const hash = (name: string, value: unknown) => createHash('sha256').update(name + JSON.stringify(value)).digest('hex');
  const deny = (message: string): PermissionResult => ({ behavior: 'deny', message });
  const decide = async (name: string, raw: Record<string, unknown>, toolId: string): Promise<PermissionResult> => {
    if (active !== turn || turn.controller.signal.aborted) return deny('Turn ended');
    const existing = decisions.get(toolId);
    if (existing) { const requestHash = hash(name, raw); return [existing.originalHash, existing.approvedHash].includes(requestHash) ? existing.result : deny('Tool identity changed'); }
    if (decisions.size >= 1024) return deny('Tool call limit');
    let result: PermissionResult = deny('Unsupported tool');
    try {
      if (['Read', 'Grep', 'Glob'].includes(name)) {
        const target = name === 'Read' ? raw.file_path : raw.path ?? session!.workspace;
        await scopedPath(target);
        if (name === 'Glob' && (typeof raw.pattern !== 'string' || isAbsolute(raw.pattern) || raw.pattern.split('/').includes('..'))) return deny('Out-of-scope glob');
        result = { behavior: 'allow', updatedInput: raw };
      } else if (name === 'TodoWrite') {
        event('plan', { steps: raw.todos }); result = { behavior: 'allow', updatedInput: raw };
      } else if (name === 'ExitPlanMode') {
        // Accepting a vendor plan tool cannot change the host-approved turn mode.
        event('plan', { proposal: raw }); result = deny('Approve the plan in Hormuz before a separate execution turn');
      } else if (name === 'AskUserQuestion') {
        if (!Array.isArray(raw.questions) || !raw.questions.length || raw.questions.length > 10) return deny('Invalid questions');
        const questions: Array<Record<string, unknown> & { id: string }> = raw.questions.map((value, index) => ({ ...object(value), id: `question-${index + 1}` }));
        event('question', { questions });
        const decision = await ask('question', { questions, toolId });
        if (decision.decision === 'accept') {
          const supplied = object(decision.answers), answers: Record<string, string> = {};
          for (const question of questions) {
            const values = supplied[question.id];
            if (typeof question.question !== 'string' || !Array.isArray(values) || !values.length || values.length > 10 || values.some(value => typeof value !== 'string' || value.length > 16_384)) return deny('Invalid question answers');
            answers[question.question] = values.join(', ');
          }
          result = { behavior: 'allow', updatedInput: { ...raw, answers } };
        } else result = deny('Question declined');
      } else if (name === 'Bash') {
        if (turn.mode === 'plan') return deny('Shell commands are disabled while planning');
        if (typeof raw.command !== 'string' || !raw.command || raw.command.length > 65_536 || raw.run_in_background === true) return deny('Unsupported command');
        const decision = await ask('command', { toolId, command: raw.command, cwd: session!.workspace, description: raw.description });
        result = decision.decision === 'accept' ? { behavior: 'allow', updatedInput: { ...raw, timeout: Math.min(typeof raw.timeout === 'number' ? raw.timeout : 120_000, 120_000), run_in_background: false } } : deny('Command declined');
      } else if (['Write', 'Edit', 'NotebookEdit'].includes(name)) {
        const path = await scopedPath(raw.file_path ?? raw.notebook_path);
        if (turn.mode === 'plan') return deny('Project writes are disabled while planning');
        const changes = [{ path, tool: name, ...(name === 'Write' ? { content: raw.content } : { old: raw.old_string, new: raw.new_string }), input: raw }];
        const decision = await ask('file', { toolId, changes, path });
        result = decision.decision === 'accept' ? { behavior: 'allow', updatedInput: raw } : deny('File change declined');
      }
    } catch { result = deny('Invalid or out-of-scope tool request'); }
    if (active !== turn || turn.controller.signal.aborted) result = deny('Turn ended');
    decisions.set(toolId, { originalHash: hash(name, raw), approvedHash: hash(name, result.behavior === 'allow' ? result.updatedInput ?? raw : raw), result });
    return result;
  };
  let status: 'completed' | 'failed' | 'interrupted' | 'unknown' = 'unknown';
  let finalText = '';
  try {
    const options: Options = {
      cwd: session!.workspace, model: session!.model, settingSources: [], mcpServers: {}, strictMcpConfig: true, plugins: [],
      tools: ['Read', 'Glob', 'Grep', 'Bash', 'Write', 'Edit', 'NotebookEdit', 'AskUserQuestion', 'ExitPlanMode', 'TodoWrite'],
      allowedTools: [], permissionMode: turn.mode === 'plan' ? 'plan' : 'default',
      includePartialMessages: true, persistSession: true, maxTurns: 32, promptSuggestions: false,
      // v1 admits only bounded text/tool history. Enable extended thinking only
      // once its opaque signed history has a qualified admission contract.
      thinking: { type: 'disabled' },
      env: { ...process.env }, abortController: turn.controller,
      ...(session!.resume ? { resume: session!.threadId } : { sessionId: session!.threadId }),
      systemPrompt: { type: 'preset', preset: 'claude_code', append: 'You are inside Hormuz. Use only the authorized project. Planning must not change project files or run shell commands. Execution requires concrete per-action approvals. Never launch subagents, background work, remote control, or network tools. The host owns mode changes.' },
      hooks: { PreToolUse: [{ hooks: [async (input): Promise<HookJSONOutput> => {
        if (input.hook_event_name !== 'PreToolUse' || input.session_id !== session!.threadId) return { hookSpecificOutput: { hookEventName: 'PreToolUse', permissionDecision: 'deny', permissionDecisionReason: 'Foreign tool request' } };
        const result = await decide(input.tool_name, object(input.tool_input), input.tool_use_id);
        return { hookSpecificOutput: { hookEventName: 'PreToolUse', permissionDecision: result.behavior === 'allow' ? 'allow' : 'deny',
          ...(result.behavior === 'allow' ? { updatedInput: result.updatedInput } : { permissionDecisionReason: result.message }) } };
      }] }] },
      canUseTool: (name, value, context) => decide(name, value, context.toolUseID),
      stderr: () => { /* Never relay SDK logs, which may contain credentials. */ },
    };
    // Streaming input is the SDK's supported bidirectional mode for permissions,
    // hooks, and interruption; one host turn still supplies exactly one prompt.
    const input = (async function* (): AsyncGenerator<SDKUserMessage> {
      yield { type: 'user', session_id: session!.threadId, message: { role: 'user', content: prompt }, parent_tool_use_id: null };
    })();
    turn.query = query({ prompt: input, options });
    for await (const message of turn.query) {
      const value = message as SDKMessage & Record<string, unknown>;
      if (value.session_id && value.session_id !== session!.threadId) throw new Error('Foreign session message');
      if (value.type === 'system' && value.subtype === 'init') {
        if (value.claude_code_version !== CLI_VERSION || value.cwd !== session!.workspace || value.permissionMode !== (turn.mode === 'plan' ? 'plan' : 'default')) throw new Error('Unsupported native runtime configuration');
        if (Array.isArray(value.mcp_servers) && value.mcp_servers.length) throw new Error('Unconfigured MCP runtime');
        event('tool', { runtime: { sdkVersion: VERSION, cliVersion: value.claude_code_version, apiKeySource: value.apiKeySource } });
      } else if (value.type === 'assistant') {
        const body = object(value.message);
        if (Array.isArray(body.content)) for (const block of body.content) {
          const content = object(block);
          if (content.type === 'text' && typeof content.text === 'string') { finalText = content.text; event('message', { text: content.text }); }
          else if (content.type === 'tool_use') event('tool', { id: content.id, name: content.name, input: content.input });
        }
      } else if (value.type === 'stream_event') {
        const data = object(value.event);
        if (data.type === 'content_block_delta' && data.delta && typeof data.delta === 'object') {
          const delta = object(data.delta); if (delta.type === 'text_delta') event('message', { delta: delta.text });
        }
      } else if (value.type === 'result') {
        status = value.subtype === 'success' && value.is_error === false ? 'completed' : turn.controller.signal.aborted ? 'interrupted' : 'failed';
        if (typeof value.result === 'string') finalText = value.result;
        event('usage', { usage: value.usage, modelUsage: value.modelUsage, estimatedCostUsd: value.total_cost_usd });
        break;
      } else if (value.type === 'tool_progress') event('tool', { toolId: value.tool_use_id, name: value.tool_name, elapsedSeconds: value.elapsed_time_seconds });
    }
  } catch { status = turn.controller.signal.aborted ? 'interrupted' : 'failed'; }
  finally { turn.query?.close(); }
  if (active !== turn) return;
  if (turn.mode === 'plan' && status === 'completed' && finalText) event('plan', { text: finalText });
  send({ method: 'turn/completed', params: { threadId: session!.threadId, turnId: turn.turnId, status } });
  active = undefined;
}

async function handle(value: Record<string, unknown>): Promise<void> {
  if (!value.method) {
    const request = pending.get(String(value.id));
    if (request) { clearTimeout(request.timer); pending.delete(String(value.id)); request.resolve(value.result ?? { decision: 'cancel' }); }
    return;
  }
  let result: unknown;
  const params = object(value.params ?? {});
  if (value.method === 'initialize') result = { engine: 'claude', version };
  else if (value.method === 'session/start') {
    if (session || version !== VERSION || typeof params.threadId !== 'string' || typeof params.workspace !== 'string' || typeof params.model !== 'string') throw new Error('Invalid session');
    session = { threadId: params.threadId, workspace: await realpath(params.workspace), model: params.model, resume: params.resume === true };
    const info = session.resume ? await (fixture?.getSessionInfo ?? sdkGetSessionInfo)(session.threadId, { dir: session.workspace }) : undefined;
    result = { threadId: session.threadId, available: !session.resume || Boolean(info && info.sessionId === session.threadId) };
  } else if (value.method === 'turn/start') {
    if (!session || active || params.threadId !== session.threadId || typeof params.turnId !== 'string' || typeof params.prompt !== 'string' || !['plan', 'execute'].includes(String(params.mode))) throw new Error('Invalid turn');
    active = { turnId: params.turnId, mode: params.mode as 'plan' | 'execute', controller: new AbortController() };
    result = { turnId: active.turnId };
    send({ id: value.id, result });
    void runTurn(active, params.prompt); return;
  } else if (value.method === 'turn/interrupt') {
    if (!active || params.threadId !== session?.threadId || params.turnId !== active.turnId) throw new Error('Foreign interrupt');
    active.controller.abort(); for (const request of pending.values()) { clearTimeout(request.timer); request.resolve({ decision: 'cancel' }); } pending.clear();
    active.query?.close(); result = {};
  } else throw new Error('Unsupported method');
  send({ id: value.id, result });
}

async function stop(): Promise<void> {
  active?.controller.abort(); active?.query?.close();
  for (const request of pending.values()) { clearTimeout(request.timer); request.resolve({ decision: 'cancel' }); }
  pending.clear(); process.stdin.destroy(); process.exit(0);
}
process.stdin.on('data', (chunk: Buffer) => {
  input = Buffer.concat([input, chunk]);
  let end;
  while ((end = input.indexOf(10)) >= 0) {
    if (end > 1_048_576) { void stop(); return; }
    const line = input.subarray(0, end).toString(); input = input.subarray(end + 1);
    try { const value = object(JSON.parse(line)); void handle(value).catch(() => send({ id: value.id, error: { code: -32602, message: 'Request rejected by Hormuz' } })); }
    catch { void stop(); return; }
  }
  if (input.length > 1_048_576) void stop();
});
process.stdin.on('end', () => { void stop(); });
process.stdout.on('error', () => { void stop(); });
process.on('SIGTERM', () => { void stop(); });
