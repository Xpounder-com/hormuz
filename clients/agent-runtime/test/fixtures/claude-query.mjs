import { appendFileSync, writeFileSync, readFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';

export const sdkVersion = '0.3.299';
export async function getSessionInfo(sessionId) {
  return existsSync(join(process.env.CLAUDE_CONFIG_DIR, `fixture-session-${sessionId}.json`)) ? { sessionId } : undefined;
}
export function query({ prompt, options }) {
  const sessionId = options.sessionId ?? options.resume;
  const configDir = process.env.CLAUDE_CONFIG_DIR;
  appendFileSync(join(configDir, 'fixture-claude.jsonl'), JSON.stringify({ sessionId, resume: options.resume, mode: options.permissionMode, settingSources: options.settingSources, tools: options.tools, envKeys: Object.keys(options.env), baseUrl: options.env.ANTHROPIC_BASE_URL, outputLimit: options.env.CLAUDE_CODE_MAX_OUTPUT_TOKENS, thinking: options.thinking, temporary: options.env.CLAUDE_CODE_TMPDIR }) + '\n');
  const disk = join(configDir, `fixture-session-${sessionId}.json`);
  if (options.resume && !existsSync(disk)) throw new Error('Missing session');
  writeFileSync(disk, JSON.stringify({ id: sessionId }));
  let closed = false;
  const call = async (name, value, id, invokeCallback = false) => {
    const hook = options.hooks.PreToolUse[0].hooks[0];
    const result = await hook({ hook_event_name: 'PreToolUse', session_id: sessionId, tool_name: name, tool_input: value, tool_use_id: id }, undefined, { signal: options.abortController.signal });
    if (result.hookSpecificOutput?.permissionDecision !== 'allow') return false;
    const updated = result.hookSpecificOutput.updatedInput ?? value;
    if (invokeCallback) {
      const permission = await options.canUseTool(name, value, { toolUseID: id, signal: options.abortController.signal, requestId: id });
      if (permission.behavior !== 'allow') return false;
      const normalized = await options.canUseTool(name, updated, { toolUseID: id, signal: options.abortController.signal, requestId: id });
      if (normalized.behavior !== 'allow') throw new Error('Normalized approved input denied');
      const forged = await options.canUseTool(name, { ...value, injected: 'foreign' }, { toolUseID: id, signal: options.abortController.signal, requestId: id });
      if (forged.behavior !== 'deny') throw new Error('Modified approval identity accepted');
    }
    if (name === 'Write') writeFileSync(value.file_path, value.content);
    return true;
  };
  const stream = (async function* () {
    if (typeof prompt !== 'string') { for await (const message of prompt) { prompt = message.message.content; break; } }
    const init = { type: 'system', subtype: 'init', session_id: sessionId, claude_code_version: '2.1.299', cwd: options.cwd, permissionMode: options.permissionMode, apiKeySource: 'ANTHROPIC_API_KEY', mcp_servers: [] };
    const result = { type: 'result', subtype: 'success', is_error: false, session_id: sessionId, result: 'UNVALIDATED OUTPUT', usage: { input_tokens: 10, output_tokens: 5 }, modelUsage: {}, total_cost_usd: 0 };
    if (prompt === 'NO_INIT_END') return;
    if (prompt === 'NO_INIT_RESULT') { yield result; return; }
    if (prompt === 'LATE_INIT_ASSISTANT') yield { type: 'assistant', session_id: sessionId, message: { content: [{ type: 'text', text: 'UNVALIDATED OUTPUT' }] } };
    if (prompt === 'LATE_INIT_STREAM') yield { type: 'stream_event', session_id: sessionId, event: { type: 'content_block_delta', delta: { type: 'text_delta', text: 'UNVALIDATED OUTPUT' } } };
    if (prompt === 'NO_INIT_HOOK') await call('Write', { file_path: join(options.cwd, 'claude.txt'), content: 'unvalidated' }, 'pre-init-hook');
    if (prompt === 'NO_INIT_CALLBACK') {
      const permission = await options.canUseTool('Write', { file_path: join(options.cwd, 'claude.txt'), content: 'unvalidated' }, { toolUseID: 'pre-init-callback', signal: options.abortController.signal, requestId: 'pre-init-callback' });
      if (permission.behavior === 'allow') writeFileSync(join(options.cwd, 'claude.txt'), 'unvalidated');
    }
    if (prompt === 'STARTUP_METADATA') yield { type: 'system', subtype: 'status', session_id: sessionId, status: null };
    let queuedHook;
    if (prompt === 'QUEUED_INIT_HOOK') queuedHook = call('Write', { file_path: join(options.cwd, 'claude.txt'), content: 'changed' }, 'queued-init-hook', true);
    let queuedCallback;
    if (prompt === 'QUEUED_INIT_CALLBACK') queuedCallback = options.canUseTool('Write', { file_path: join(options.cwd, 'claude.txt'), content: 'changed' }, { toolUseID: 'queued-init-callback', signal: options.abortController.signal, requestId: 'queued-init-callback' });
    if (prompt === 'BAD_INIT_VERSION') init.claude_code_version = '2.1.298';
    if (prompt === 'BAD_INIT_CWD') init.cwd = join(options.cwd, '..');
    if (prompt === 'BAD_INIT_MODE') init.permissionMode = 'bypassPermissions';
    if (prompt === 'BAD_INIT_MCP') init.mcp_servers = [{ name: 'ambient', status: 'connected' }];
    if (prompt === 'BAD_INIT_MCP_SHAPE') init.mcp_servers = null;
    if (prompt === 'BAD_INIT_MCP_MISSING') delete init.mcp_servers;
    if (prompt === 'BAD_INIT_SESSION_MISSING') delete init.session_id;
    yield init;
    if (prompt === 'DUPLICATE_INIT') yield init;
    if (queuedHook) { if (!(await queuedHook)) throw new Error('Queued valid init was not consumed'); yield result; return; }
    if (queuedCallback) {
      if ((await queuedCallback).behavior !== 'allow') throw new Error('Queued valid init was not consumed');
      writeFileSync(join(options.cwd, 'claude.txt'), 'changed'); yield result; return;
    }
    if (prompt === 'HANG') {
      await new Promise(resolve => options.abortController.signal.addEventListener('abort', resolve, { once: true }));
      return;
    }
    if (prompt === 'FOREIGN_SESSION') {
      yield { type: 'assistant', session_id: 'foreign', message: { content: [{ type: 'text', text: 'FORGED' }] } }; return;
    }
    if (prompt === 'QUESTION') await call('AskUserQuestion', { questions: [{ question: 'Which scope?', header: 'Scope', options: [{ label: 'Small', description: 'One file' }] }] }, 'question-1', true);
    else {
      if (prompt === 'OUTSIDE') {
        if (await call('Write', { file_path: join(options.cwd, '..', 'outside.txt'), content: 'escaped' }, 'file-outside')) throw new Error('Escaped');
      } else {
        await call('Bash', { command: 'printf test', description: 'Local command' }, 'command-1', true);
        // Simulate SDK auto-approval skipping canUseTool: the hook must still gate it.
        await call('Write', { file_path: join(options.cwd, 'claude.txt'), content: 'changed' }, 'file-1');
      }
    }
    if (closed || options.abortController.signal.aborted) return;
    const text = options.permissionMode === 'plan' ? 'Inspect the project. Change one file after approval.' : 'Completed the scoped change.';
    yield { type: 'stream_event', session_id: sessionId, event: { type: 'content_block_delta', delta: { type: 'text_delta', text } } };
    yield { type: 'assistant', session_id: sessionId, message: { content: [{ type: 'text', text }] } };
    yield { type: 'result', subtype: 'success', is_error: false, session_id: sessionId, result: text, usage: { input_tokens: 10, output_tokens: 5 }, modelUsage: {}, total_cost_usd: 0 };
  })();
  stream.close = () => { closed = true; };
  stream.interrupt = async () => { closed = true; options.abortController.abort(); };
  return stream;
}
