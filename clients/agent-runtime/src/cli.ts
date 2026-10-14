import { realpath } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { AgentSessions, ApplicationScheduler } from './sessions.ts';
import { SessionStore } from './store.ts';
import { CodexAdapter } from './codex.ts';
import { ClaudeAdapter } from './claude.ts';
import { createAgentBridge } from './bridge.ts';
import { AgentError } from './types.ts';

export type HostConfig = {
  profileId: string; projectId: string; workspace: string; stateDirectory: string;
  model: string; engine: 'codex' | 'claude'; allowanceMicrousd: number;
  inputMicrousdPerMillion: number; outputMicrousdPerMillion: number; maxInputTokens: number; maxOutputTokens: number;
  allowCloudInference?: boolean;
};

/** Bounded partial JSONL frames, including streams that never send a newline. */
export async function* readHostLines(input: AsyncIterable<Buffer | string>, onOverflow: () => void, limit = 128 * 1024) {
  let parts: Buffer[] = [], size = 0, oversized = false;
  for await (const value of input) {
    const chunk = Buffer.isBuffer(value) ? value : Buffer.from(value);
    let offset = 0;
    while (offset < chunk.length) {
      const newline = chunk.indexOf(10, offset), end = newline === -1 ? chunk.length : newline;
      const piece = chunk.subarray(offset, end);
      if (!oversized && size + piece.length > limit) { oversized = true; parts = []; size = 0; onOverflow(); }
      if (!oversized && piece.length) { parts.push(piece); size += piece.length; }
      if (newline !== -1) {
        if (!oversized) yield Buffer.concat(parts, size).toString('utf8').replace(/\r$/, '');
        parts = []; size = 0; oversized = false;
      }
      offset = newline === -1 ? chunk.length : newline + 1;
    }
  }
  if (!oversized && size) yield Buffer.concat(parts, size).toString('utf8').replace(/\r$/, '');
}

function validateConfig(config: HostConfig) {
  if (!config || typeof config !== 'object') throw new AgentError('invalid_host_config');
  for (const field of ['profileId', 'projectId', 'workspace', 'stateDirectory', 'model', 'engine'] as const) if (typeof config[field] !== 'string' || !config[field] || config[field].length > 4096) throw new AgentError('invalid_host_config');
  for (const field of ['allowanceMicrousd', 'inputMicrousdPerMillion', 'outputMicrousdPerMillion', 'maxInputTokens', 'maxOutputTokens'] as const) if (!Number.isSafeInteger(config[field]) || config[field] <= 0) throw new AgentError('invalid_host_config');
  if (!['codex', 'claude'].includes(config.engine)) throw new AgentError('invalid_host_config');
  if (config.allowCloudInference !== undefined && typeof config.allowCloudInference !== 'boolean') throw new AgentError('invalid_host_config');
}

export function createHostWriter(output: { write: (frame: Buffer, complete: () => void) => unknown }, onOverflow: () => void, limit = 64 * 1024 * 1024) {
  let queued = 0, closed = false;
  return (value: unknown) => {
    if (closed) return;
    const frame = Buffer.from(JSON.stringify(value) + '\n');
    if (queued + frame.length > limit) { closed = true; onOverflow(); return; }
    queued += frame.length;
    try { output.write(frame, () => { queued -= frame.length; }); }
    catch { closed = true; queued -= frame.length; onOverflow(); }
  };
}

/** Developer host: newline JSON control, explicit environment-owned API auth, no automatic inference. */
export async function runHost(config: HostConfig) {
  validateConfig(config);
  const workspace = await realpath(config.workspace);
  const stateDirectory = resolve(config.stateDirectory);
  const manager = new AgentSessions({ profileId: config.profileId, store: new SessionStore(stateDirectory), scheduler: new ApplicationScheduler(),
    runtimeFactory: engine => {
      if (engine === 'codex') return new CodexAdapter();
      return new ClaudeAdapter();
    },
    authorizeWorkspace: async (project, path) => project === config.projectId && path === workspace,
    authorizeAllowance: async (project, amount) => project === config.projectId && amount <= config.allowanceMicrousd,
    authorizeCloud: async project => project === config.projectId && config.allowCloudInference === true,
    parentAllowances: { profileMicrousd: config.allowanceMicrousd, projectMicrousd: config.allowanceMicrousd },
    resolveConnection: async connectionId => {
      if (connectionId !== 'provider') throw new AgentError('connection_unavailable');
      const provider = config.engine === 'codex' ? 'openai' : 'anthropic';
      const credential = process.env[provider === 'openai' ? 'OPENAI_API_KEY' : 'ANTHROPIC_API_KEY'];
      if (!credential) throw new AgentError('credential_unavailable');
      return { provider, upstream: provider === 'openai' ? 'https://api.openai.com/v1' : 'https://api.anthropic.com/v1',
        credential, version: 'environment-api-v1', models: [config.model],
        inputMicrousdPerMillion: config.inputMicrousdPerMillion, outputMicrousdPerMillion: config.outputMicrousdPerMillion,
        maxInputTokens: config.maxInputTokens, maxOutputTokens: config.maxOutputTokens, requestLimit: 25 };
    },
  });
  await manager.initialize();
  let stopping = false;
  const send = createHostWriter(process.stdout, () => {
    stopping = true; process.stdin.destroy();
    void manager.dispose().catch(() => { process.exitCode = 1; });
  });
  for (const event of ['event', 'approval', 'review']) manager.on(event, data => send({ type: event, data }));
  const bridge = createAgentBridge({ sessions: manager, senderPolicy: sender => sender === 1 ? { projectIds: new Set([config.projectId]), workspaceRoots: new Set([workspace]) } : undefined });
  const pending = new Set<Promise<void>>();
  const stop = () => { stopping = true; process.stdin.destroy(); void manager.dispose().catch(() => send({ type: 'error', code: 'cleanup_required' })); };
  process.once('SIGINT', stop); process.once('SIGTERM', stop);
  process.stdout.once('error', stop);
  send({ type: 'ready', schemaVersion: 1, projectId: config.projectId, model: config.model, engine: config.engine, credentialConfigured: Boolean(process.env[config.engine === 'codex' ? 'OPENAI_API_KEY' : 'ANTHROPIC_API_KEY']) });
  try {
    for await (const line of readHostLines(process.stdin, () => send({ type: 'error', code: 'request_limit' }))) {
      if (stopping) break;
      if (pending.size >= 8 || Buffer.byteLength(line) > 128 * 1024) { send({ type: 'error', code: 'request_limit' }); continue; }
      let request: unknown;
      try { request = JSON.parse(line); } catch { send({ type: 'error', code: 'invalid_json' }); continue; }
      const requestId = request && typeof request === 'object' ? (request as Record<string, unknown>).requestId : undefined;
      if (requestId !== undefined && !(typeof requestId === 'string' && requestId.length <= 256) && !(typeof requestId === 'number' && Number.isSafeInteger(requestId))) { send({ type: 'error', code: 'invalid_request_id' }); continue; }
      const payload = request && typeof request === 'object' ? { ...request as Record<string, unknown> } : request;
      if (payload && typeof payload === 'object') delete (payload as Record<string, unknown>).requestId;
      const task = bridge(1, payload).then(result => { send({ type: 'result', requestId, result }); }, error => {
        send({ type: 'error', requestId, code: error instanceof AgentError ? error.code : 'host_request_failed' });
      });
      pending.add(task); void task.finally(() => pending.delete(task));
    }
  } finally {
    process.removeListener('SIGINT', stop); process.removeListener('SIGTERM', stop);
    process.stdout.removeListener('error', stop);
    await manager.dispose(); await Promise.allSettled(pending);
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  try {
    const raw = process.env.HORMUZ_AGENT_HOST_CONFIG;
    if (!raw || Buffer.byteLength(raw) > 8192) throw new AgentError('host_config_required');
    const config = JSON.parse(raw);
    await runHost(config);
  } catch (error) { process.stderr.write(JSON.stringify({ code: error instanceof AgentError ? error.code : 'host_start_failed' }) + '\n'); process.exitCode = 1; }
}
