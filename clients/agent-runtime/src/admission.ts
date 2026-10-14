import { randomBytes, timingSafeEqual } from 'node:crypto';
import http from 'node:http';
import https from 'node:https';
import type { Socket } from 'node:net';

export type ProxyAccounting = {
  spentMicrousd: number;
  heldMicrousd: number;
  /** Unknown reservations are a subset of held, never an additional charge. */
  unknownMicrousd: number;
  requestCount: number;
};
export type InferenceProxyOptions = {
  provider: 'openai' | 'anthropic';
  upstream: string;
  credential: string;
  model: string;
  allowanceMicrousd: number;
  /** Supply a conservative maximum applicable input rate, including cache creation. */
  inputMicrousdPerMillion: number;
  outputMicrousdPerMillion: number;
  maxInputTokens: number;
  maxOutputTokens: number;
  requestLimit?: number;
  timeoutMs?: number;
  allowLoopbackUpstream?: boolean;
  initialAccounting?: ProxyAccounting;
  /** Durable checkpoint. Reservation must persist before any provider egress. */
  onAccounting?: (snapshot: ProxyAccounting & { coverage: 'enforced' }) => Promise<void> | void;
  /** Revalidate host authority after checkpoint, immediately before each egress. */
  authorizeDispatch?: () => Promise<void> | void;
};

const MAX_REQUEST_BYTES = 2 * 1024 * 1024;
const MAX_RESPONSE_BYTES = 8 * 1024 * 1024;
const MAX_EVENT_BYTES = 1024 * 1024;
const FRAMING_TOKENS = 4096;
const MAX_DEPTH = 32;
const OPENAI_FIELDS = new Set(['model', 'input', 'instructions', 'tools', 'tool_choice', 'parallel_tool_calls', 'temperature', 'top_p', 'max_output_tokens', 'stream', 'stream_options', 'store', 'truncation', 'reasoning', 'text', 'metadata', 'include', 'prompt_cache_key', 'safety_identifier', 'service_tier', 'background', 'client_metadata']);
const CLAUDE_SDK_BETAS = new Set(['claude-code-20250219', 'interleaved-thinking-2025-05-14', 'thinking-token-count-2026-05-13', 'context-management-2025-06-27', 'prompt-caching-scope-2026-01-05']);
const CLAUDE_COUNT_FIELDS = new Set(['model', 'messages', 'system', 'tools', 'tool_choice', 'thinking']);
const CLAUDE_FIELDS = new Set(['model', 'messages', 'system', 'tools', 'tool_choice', 'max_tokens', 'stream', 'temperature', 'top_p', 'top_k', 'stop_sequences', 'metadata', 'thinking', 'output_config']);

type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
type JsonObject = { [key: string]: Json };
type Usage = { input: number; output: number };
class Rejection extends Error {
  readonly status: number;
  readonly code: string;
  readonly retryAfter?: string;
  constructor(status: number, code: string, retryAfter?: string) { super(code); this.status = status; this.code = code; this.retryAfter = retryAfter; }
}
const object = (value: unknown): value is JsonObject => !!value && typeof value === 'object' && !Array.isArray(value);
const integer = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0;
function reject(code = 'unsupported_request'): never { throw new Rejection(422, code); }
function checkKeys(value: JsonObject, keys: Set<string>): void { for (const key of Object.keys(value)) if (!keys.has(key)) reject(); }
function strings(value: Json): boolean { return Array.isArray(value) && value.every((entry) => typeof entry === 'string'); }
function jsonDepth(value: Json, depth = 0): number {
  if (depth > MAX_DEPTH) reject('request_too_complex');
  if (value && typeof value === 'object') return 1 + Object.values(value).reduce<number>((count, child) => count + jsonDepth(child, depth + 1), 0);
  return 1;
}
function cost(tokens: number, rate: number): number {
  const result = (BigInt(tokens) * BigInt(rate) + 999999n) / 1000000n;
  if (result > BigInt(Number.MAX_SAFE_INTEGER)) reject('cost_out_of_range');
  return Number(result);
}
function textBlocks(value: Json, kinds: string[]): boolean {
  return typeof value === 'string' || Array.isArray(value) && value.every((entry) => {
    if (!object(entry) || !kinds.includes(String(entry.type)) || typeof entry.text !== 'string') return false;
    return Object.keys(entry).every((key) => ['type', 'text', 'annotations'].includes(key)) && (entry.annotations === undefined || Array.isArray(entry.annotations) && entry.annotations.length === 0);
  });
}
function openaiTool(tool: Json, namespace = false): void {
  if (!object(tool)) reject();
  if (tool.type === 'namespace' && !namespace) {
    checkKeys(tool, new Set(['type', 'name', 'description', 'tools']));
    if (typeof tool.name !== 'string' || !Array.isArray(tool.tools)) reject();
    for (const child of tool.tools) openaiTool(child, true);
    return;
  }
  if (tool.type === 'function') {
    checkKeys(tool, new Set(['type', 'name', 'description', 'parameters', 'strict', 'defer_loading']));
    if (typeof tool.name !== 'string' || !object(tool.parameters)) reject();
  } else if (tool.type === 'custom') {
    checkKeys(tool, new Set(['type', 'name', 'description', 'format', 'defer_loading']));
    if (typeof tool.name !== 'string') reject();
    if (tool.format !== undefined) {
      if (!object(tool.format)) reject();
      if (tool.format.type === 'text') checkKeys(tool.format, new Set(['type']));
      else if (tool.format.type === 'grammar') {
        checkKeys(tool.format, new Set(['type', 'syntax', 'definition']));
        if (!['lark', 'regex'].includes(String(tool.format.syntax)) || typeof tool.format.definition !== 'string') reject();
      } else reject();
    }
  } else reject('hosted_tools_not_allowed');
}
function openaiInput(input: Json): void {
  if (typeof input === 'string') return;
  if (!Array.isArray(input)) reject();
  for (const entry of input) {
    if (!object(entry)) reject();
    if (entry.type === undefined || entry.type === 'message') {
      checkKeys(entry, new Set(['type', 'role', 'content', 'id', 'status', 'phase']));
      if (!['system', 'developer', 'user', 'assistant'].includes(String(entry.role)) || !textBlocks(entry.content, ['input_text', 'output_text'])) reject('text_input_required');
    } else if (entry.type === 'function_call') {
      checkKeys(entry, new Set(['type', 'id', 'call_id', 'name', 'arguments', 'status']));
      if (typeof entry.call_id !== 'string' || typeof entry.name !== 'string' || typeof entry.arguments !== 'string') reject();
    } else if (entry.type === 'function_call_output') {
      checkKeys(entry, new Set(['type', 'id', 'call_id', 'output', 'status']));
      if (typeof entry.call_id !== 'string' || !textBlocks(entry.output, ['input_text', 'output_text'])) reject();
    } else if (entry.type === 'custom_tool_call') {
      checkKeys(entry, new Set(['type', 'id', 'call_id', 'name', 'input', 'status']));
      if (typeof entry.call_id !== 'string' || typeof entry.name !== 'string' || typeof entry.input !== 'string') reject();
    } else if (entry.type === 'custom_tool_call_output') {
      checkKeys(entry, new Set(['type', 'id', 'call_id', 'output']));
      if (typeof entry.call_id !== 'string' || !textBlocks(entry.output, ['input_text', 'output_text'])) reject();
    } else if (entry.type === 'reasoning') {
      // Both encrypted and unencrypted reasoning histories require a qualified
      // model/context contract; do not silently drop or rewrite runtime state.
      reject('reasoning_history_not_qualified');
    } else reject('text_input_required');
  }
}
function claudeBlocks(value: Json, system = false): void {
  if (typeof value === 'string') return;
  if (!Array.isArray(value)) reject('text_input_required');
  for (const block of value) {
    if (!object(block)) reject();
    if (block.cache_control !== undefined) {
      if (!object(block.cache_control) || block.cache_control.type !== 'ephemeral' || Object.keys(block.cache_control).some((key) => !['type', 'ttl'].includes(key)) || block.cache_control.ttl !== undefined && !['5m', '1h'].includes(String(block.cache_control.ttl))) reject();
    }
    if (block.type === 'text') {
      checkKeys(block, new Set(['type', 'text', 'cache_control']));
      if (typeof block.text !== 'string') reject();
    } else if (!system && block.type === 'tool_use') {
      checkKeys(block, new Set(['type', 'id', 'name', 'input', 'cache_control']));
      if (typeof block.id !== 'string' || typeof block.name !== 'string' || !object(block.input)) reject();
    } else if (!system && block.type === 'tool_result') {
      checkKeys(block, new Set(['type', 'tool_use_id', 'content', 'is_error', 'cache_control']));
      if (typeof block.tool_use_id !== 'string') reject();
      claudeBlocks(block.content, true);
    } else if (!system && block.type === 'thinking') {
      checkKeys(block, new Set(['type', 'thinking', 'signature']));
      if (typeof block.thinking !== 'string' || typeof block.signature !== 'string') reject();
    } else reject('text_input_required');
  }
}
function prepare(options: InferenceProxyOptions, body: JsonObject, path: string): { body: Buffer; inputBound: number; outputBound: number; counting: boolean } {
  const counting = options.provider === 'anthropic' && path === '/v1/messages/count_tokens';
  if (body.model !== options.model) reject('model_not_allowed');
  if (options.provider === 'openai') {
    if (path === '/v1/responses/compact') reject('unbounded_compaction_not_allowed');
    checkKeys(body, OPENAI_FIELDS);
    openaiInput(body.input);
    if (body.instructions !== undefined && typeof body.instructions !== 'string') reject();
    if (body.tools !== undefined) { if (!Array.isArray(body.tools)) reject(); for (const tool of body.tools) openaiTool(tool); }
    if (body.tool_choice !== undefined && typeof body.tool_choice !== 'string') {
      if (!object(body.tool_choice) || !['function', 'custom'].includes(String(body.tool_choice.type)) || typeof body.tool_choice.name !== 'string') reject();
      checkKeys(body.tool_choice, new Set(['type', 'name']));
    } else if (typeof body.tool_choice === 'string' && !['auto', 'none', 'required'].includes(body.tool_choice)) reject();
    if (body.store === true || body.background === true) reject();
    if (body.service_tier !== undefined && body.service_tier !== 'default') reject('service_tier_not_allowed');
    body.service_tier = 'default';
    if (body.include !== undefined) {
      if (!Array.isArray(body.include) || body.include.length > 1 || body.include.some(value => value !== 'reasoning.encrypted_content')) reject();
      // This optional output request is omitted explicitly by the documented
      // projection. Opaque histories/compaction remain unsupported on input.
      if (body.include.length) delete body.include;
    }
    if (body.client_metadata !== undefined) {
      if (!object(body.client_metadata) || Object.keys(body.client_metadata).length > 16 || Object.entries(body.client_metadata).some(([key, value]) => Buffer.byteLength(key) > 64 || typeof value !== 'string' || Buffer.byteLength(value) > 1024)) reject('client_metadata_not_allowed');
    }
    if (body.reasoning !== undefined) {
      if (!object(body.reasoning)) reject();
      checkKeys(body.reasoning, new Set(['effort', 'summary']));
      if (body.reasoning.effort !== undefined && !['none', 'minimal', 'low', 'medium', 'high', 'xhigh'].includes(String(body.reasoning.effort))) reject();
      if (body.reasoning.summary !== undefined && !['auto', 'concise', 'detailed'].includes(String(body.reasoning.summary))) reject();
    }
    if (body.text !== undefined) {
      if (!object(body.text)) reject();
      checkKeys(body.text, new Set(['format', 'verbosity']));
      if (body.text.format !== undefined && (!object(body.text.format) || body.text.format.type !== 'text' || Object.keys(body.text.format).length !== 1)) reject();
    }
    body.store = false;
    body.truncation = 'disabled';
  } else {
    checkKeys(body, counting ? CLAUDE_COUNT_FIELDS : CLAUDE_FIELDS);
    if (!Array.isArray(body.messages) || body.messages.some((message) => !object(message) || !['user', 'assistant'].includes(String(message.role)) || Object.keys(message).some((key) => !['role', 'content'].includes(key)))) reject();
    for (const message of body.messages) claudeBlocks((message as JsonObject).content);
    if (body.system !== undefined) claudeBlocks(body.system, true);
    if (body.tools !== undefined) {
      if (!Array.isArray(body.tools)) reject();
      for (const tool of body.tools) {
        if (!object(tool)) reject();
        checkKeys(tool, new Set(['name', 'description', 'input_schema', 'cache_control', 'strict', 'defer_loading']));
        if (typeof tool.name !== 'string' || !object(tool.input_schema)) reject('hosted_tools_not_allowed');
      }
    }
    if (body.tool_choice !== undefined) {
      if (!object(body.tool_choice) || !['auto', 'any', 'none', 'tool'].includes(String(body.tool_choice.type))) reject();
      checkKeys(body.tool_choice, new Set(['type', 'name', 'disable_parallel_tool_use']));
    }
    if (body.stop_sequences !== undefined && !strings(body.stop_sequences)) reject();
    if (body.thinking !== undefined) {
      if (!object(body.thinking) || !['enabled', 'disabled', 'adaptive'].includes(String(body.thinking.type))) reject();
      checkKeys(body.thinking, new Set(['type', 'budget_tokens', 'display']));
      if (body.thinking.budget_tokens !== undefined && (!integer(body.thinking.budget_tokens) || body.thinking.budget_tokens > options.maxOutputTokens)) reject();
    }
    if (body.output_config !== undefined) reject('output_configuration_not_qualified');
  }
  if (body.stream !== undefined && typeof body.stream !== 'boolean') reject();
  const capKey = options.provider === 'openai' ? 'max_output_tokens' : 'max_tokens';
  const supplied = body[capKey];
  if (supplied !== undefined && (!integer(supplied) || supplied < 1 || supplied > options.maxOutputTokens)) reject('output_limit_exceeded');
  const outputBound = counting ? 0 : supplied === undefined ? options.maxOutputTokens : supplied as number;
  if (!counting) body[capKey] = outputBound;
  const nodes = jsonDepth(body);
  const encoded = Buffer.from(JSON.stringify(body));
  if (encoded.length > MAX_REQUEST_BYTES) reject('request_too_large');
  // Text-only, fully inline input. Count the entire wire body (including schemas and
  // tool arguments) at four tokens/UTF8 byte plus per-node and protocol framing.
  // No history lookup, encrypted content, multimodal input or hosted tool egress.
  const inputBound = encoded.length * 4 + nodes * 32 + FRAMING_TOKENS;
  if (!Number.isSafeInteger(inputBound) || inputBound > options.maxInputTokens) reject('input_limit_exceeded');
  return { body: encoded, inputBound, outputBound, counting };
}
function usageFrom(value: unknown, provider: InferenceProxyOptions['provider']): Usage | undefined {
  if (!object(value) || !object(value.usage)) return;
  const usage = value.usage;
  if (provider === 'openai') {
    if (integer(usage.input_tokens) && integer(usage.output_tokens)) return { input: usage.input_tokens, output: usage.output_tokens };
  } else {
    const input = usage.input_tokens;
    const output = usage.output_tokens;
    const creation = usage.cache_creation_input_tokens ?? 0;
    const read = usage.cache_read_input_tokens ?? 0;
    if (integer(input) && integer(output) && integer(creation) && integer(read) && Number.isSafeInteger(input + creation + read)) return { input: input + creation + read, output };
  }
}
class UsageObserver {
  private json: Buffer[] = [];
  private total = 0;
  private pending = '';
  private usage?: Usage;
  private claudeInput?: number;
  private claudeOutput?: number;
  private terminal = false;
  private decoder = new TextDecoder();
  private readonly provider: InferenceProxyOptions['provider'];
  private readonly sse: boolean;
  private readonly counting: boolean;
  constructor(provider: InferenceProxyOptions['provider'], sse: boolean, counting = false) { this.provider = provider; this.sse = sse; this.counting = counting; }
  push(chunk: Buffer): Buffer {
    const frames: Buffer[] = [];
    this.total += chunk.length;
    if (this.total > MAX_RESPONSE_BYTES) throw new Rejection(502, 'response_too_large');
    if (!this.sse) { this.json.push(chunk); return Buffer.alloc(0); }
    this.pending += this.decoder.decode(chunk, { stream: true });
    if (Buffer.byteLength(this.pending) > MAX_EVENT_BYTES) throw new Rejection(502, 'event_too_large');
    // SSE permits LF and CRLF. Parse a whole frame, including multi-line data.
    let match: RegExpExecArray | null;
    while ((match = /\r?\n\r?\n/.exec(this.pending))) {
      const frame = this.pending.slice(0, match.index);
      this.pending = this.pending.slice(match.index + match[0].length);
      const data = frame.split(/\r?\n/).filter((line) => line.startsWith('data:')).map((line) => line.slice(5).trimStart()).join('\n');
      if (!data || data === '[DONE]') { frames.push(Buffer.from(frame + match[0])); continue; }
      let value: unknown;
      try { value = JSON.parse(data); } catch { throw new Rejection(502, 'invalid_upstream_event'); }
      if (!object(value)) throw new Rejection(502, 'invalid_upstream_event');
      if (['error', 'response.failed'].includes(String(value.type)) || value.error !== undefined && value.error !== null || frame.split(/\r?\n/).some((line) => /^event:\s*error\s*$/.test(line))) throw new Rejection(502, 'upstream_error');
      frames.push(Buffer.from(frame + match[0]));
      if (this.provider === 'openai' && ['response.completed', 'response.incomplete'].includes(String(value.type))) {
        this.usage = usageFrom(value.response, this.provider);
        this.terminal = !!this.usage;
      } else if (this.provider === 'anthropic') {
        if (value.type === 'message_start') {
          const usage = usageFrom(value.message, this.provider);
          if (usage) { this.claudeInput = usage.input; this.claudeOutput = usage.output; }
        } else if (value.type === 'message_delta' && object(value.usage) && integer(value.usage.output_tokens)) this.claudeOutput = value.usage.output_tokens;
        else if (value.type === 'message_stop') this.terminal = true;
      }
    }
    return Buffer.concat(frames);
  }
  jsonBody(): Buffer { return Buffer.concat(this.json); }
  finish(): Usage | undefined {
    if (!this.sse) {
      try {
        const value: unknown = JSON.parse(this.jsonBody().toString());
        if (!object(value) || value.error !== undefined && value.error !== null || value.status === 'failed') throw new Rejection(502, 'upstream_error');
        if (this.counting) return object(value) && integer(value.input_tokens) ? { input: 0, output: 0 } : undefined;
        return usageFrom(value, this.provider);
      } catch (error) { if (error instanceof Rejection) throw error; throw new Rejection(502, 'invalid_upstream_response'); }
    }
    if (this.pending.trim() || !this.terminal) return;
    if (this.provider === 'openai') return this.usage;
    if (this.claudeInput !== undefined && this.claudeOutput !== undefined) return { input: this.claudeInput, output: this.claudeOutput };
  }
}

/** One local, authenticated model-request lane with conservative lifetime admission. */
export class InferenceProxy {
  readonly capabilities = Object.freeze({ encryptedReasoning: false, reasoningHistory: false, compaction: false, anthropicSdkBetaMarker: true, anthropicBetaHeaders: false });
  private readonly options: InferenceProxyOptions;
  private readonly upstream: URL;
  private readonly agent: http.Agent | https.Agent;
  private readonly token = randomBytes(32).toString('hex');
  private readonly sockets = new Set<Socket>();
  private readonly accounting: ProxyAccounting;
  private server?: http.Server;
  private endpoint?: string;
  private active = false;
  private closed = false;
  private ledgerHealthy = true;
  private abortActive?: () => void;
  private activeFinished?: Promise<void>;
  constructor(options: InferenceProxyOptions) {
    this.options = { ...options, requestLimit: options.requestLimit ?? 100, timeoutMs: options.timeoutMs ?? 120000 };
    if (!['openai', 'anthropic'].includes(options.provider) || typeof options.model !== 'string' || !options.model || options.model.length > 128 || typeof options.credential !== 'string' || !options.credential || options.credential.length > 4096 || /[\r\n\0]/.test(options.model + options.credential)) throw new Error('invalid_proxy_configuration');
    for (const field of ['allowanceMicrousd', 'inputMicrousdPerMillion', 'outputMicrousdPerMillion', 'maxInputTokens', 'maxOutputTokens', 'requestLimit', 'timeoutMs'] as const) {
      const value = this.options[field];
      if (!integer(value) || (['maxInputTokens', 'maxOutputTokens', 'requestLimit', 'timeoutMs'].includes(field) && value === 0)) throw new Error('invalid_proxy_configuration');
    }
    if (this.options.timeoutMs! > 300000) throw new Error('invalid_proxy_configuration');
    this.upstream = new URL(options.upstream);
    const loopback = ['127.0.0.1', '[::1]', 'localhost'].includes(this.upstream.hostname);
    if (this.upstream.username || this.upstream.password || this.upstream.search || this.upstream.hash || this.upstream.protocol !== 'https:' && !(options.allowLoopbackUpstream && loopback && this.upstream.protocol === 'http:') || loopback && !options.allowLoopbackUpstream) throw new Error('invalid_upstream');
    if (!['/', '/v1', '/v1/'].includes(this.upstream.pathname)) throw new Error('invalid_upstream_path');
    const initial = options.initialAccounting ?? { spentMicrousd: 0, heldMicrousd: 0, unknownMicrousd: 0, requestCount: 0 };
    if (['spentMicrousd', 'heldMicrousd', 'unknownMicrousd', 'requestCount'].some((key) => !integer(initial[key as keyof ProxyAccounting])) || initial.unknownMicrousd > initial.heldMicrousd || !Number.isSafeInteger(initial.spentMicrousd + initial.heldMicrousd)) throw new Error('invalid_initial_accounting');
    // A new proxy owns no active requests. Every restored hold is uncertain.
    this.accounting = { ...initial, unknownMicrousd: initial.heldMicrousd };
    const Agent = this.upstream.protocol === 'https:' ? https.Agent : http.Agent;
    this.agent = new Agent({ keepAlive: true, maxSockets: 1, maxFreeSockets: 1, timeout: this.options.timeoutMs });
  }
  summary(): ProxyAccounting & { coverage: 'enforced' } { return { ...this.accounting, coverage: 'enforced' }; }
  async start(): Promise<{ endpoint: string; credential: string }> {
    if (this.closed) throw new Error('proxy_closed');
    if (this.endpoint) return { endpoint: this.endpoint, credential: this.token };
    if (this.server) throw new Error('proxy_start_in_progress');
    this.server = http.createServer({ requestTimeout: this.options.timeoutMs, headersTimeout: Math.min(this.options.timeoutMs!, 10000), maxHeaderSize: 8192 }, (req, res) => { void this.handle(req, res); });
    this.server.maxConnections = 8;
    this.server.keepAliveTimeout = 1000;
    this.server.on('connection', (socket) => { this.sockets.add(socket); socket.on('close', () => this.sockets.delete(socket)); });
    this.server.on('clientError', (_error, socket) => { socket.destroy(); });
    await new Promise<void>((resolve, rejectStart) => {
      this.server!.once('error', rejectStart);
      this.server!.listen(0, '127.0.0.1', () => { this.server!.off('error', rejectStart); resolve(); });
    });
    const address = this.server.address();
    if (!address || typeof address === 'string') throw new Error('proxy_listen_failed');
    this.endpoint = `http://127.0.0.1:${address.port}/v1`;
    return { endpoint: this.endpoint, credential: this.token };
  }
  async stop(): Promise<void> {
    if (this.closed) return;
    this.closed = true;
    const activeFinished = this.activeFinished;
    this.abortActive?.();
    this.agent.destroy();
    if (!this.server?.listening) { await activeFinished; return; }
    const closing = new Promise<void>((resolve) => { this.server!.close(() => resolve()); });
    this.server.closeIdleConnections();
    for (const socket of this.sockets) socket.destroy();
    await closing;
    await activeFinished;
  }
  private authenticated(req: http.IncomingMessage): boolean {
    const auth = req.headers.authorization;
    const key = req.headers['x-api-key'];
    const supplied = auth?.startsWith('Bearer ') ? auth.slice(7) : this.options.provider === 'anthropic' && typeof key === 'string' ? key : undefined;
    if (typeof supplied !== 'string') return false;
    const a = Buffer.from(supplied), b = Buffer.from(this.token);
    return a.length === b.length && timingSafeEqual(a, b);
  }
  private error(res: http.ServerResponse, status: number, code: string, retryAfter?: string): void {
    if (res.headersSent) { res.destroy(); return; }
    res.writeHead(status, { 'content-type': 'application/json', 'cache-control': 'no-store', ...(retryAfter ? { 'retry-after': retryAfter } : {}) });
    res.end(JSON.stringify({ error: { code, message: code } }));
  }
  private async read(req: http.IncomingMessage): Promise<Buffer> {
    const chunks: Buffer[] = [];
    let size = 0;
    const timer = setTimeout(() => req.destroy(new Error('request_timeout')), this.options.timeoutMs);
    try {
      for await (const raw of req) {
        const chunk = Buffer.isBuffer(raw) ? raw : Buffer.from(raw);
        size += chunk.length;
        if (size > MAX_REQUEST_BYTES) throw new Rejection(413, 'request_too_large');
        chunks.push(chunk);
      }
      return Buffer.concat(chunks);
    } finally { clearTimeout(timer); }
  }
  private async handle(req: http.IncomingMessage, res: http.ServerResponse): Promise<void> {
    if (!this.authenticated(req)) { this.error(res, 401, 'unauthorized'); return; }
    // SDK0.3.299 adds precisely this marker. It does not select additional model
    // features: normalize it to the qualified stable Messages endpoint.
    const path = this.options.provider === 'anthropic' && req.url === '/v1/messages?beta=true' ? '/v1/messages' : req.url ?? '';
    const paths = this.options.provider === 'openai' ? ['/v1/responses', '/v1/responses/compact'] : ['/v1/messages', '/v1/messages/count_tokens'];
    if (req.method !== 'POST' || !paths.includes(path)) { this.error(res, 404, 'unsupported_endpoint'); return; }
    if (path === '/v1/messages/count_tokens' && this.upstream.hostname !== 'api.anthropic.com' && !(this.options.allowLoopbackUpstream && ['127.0.0.1', '[::1]', 'localhost'].includes(this.upstream.hostname))) { this.error(res, 422, 'unpriced_token_counting_not_allowed'); return; }
    if (this.options.provider === 'anthropic') {
      if (req.headers['anthropic-version'] !== undefined && req.headers['anthropic-version'] !== '2023-06-01') { this.error(res, 422, 'anthropic_version_not_qualified'); return; }
      const beta = req.headers['anthropic-beta'];
      if (beta !== undefined) {
        if (typeof beta !== 'string' || beta.length > 512) { this.error(res, 422, 'anthropic_beta_not_qualified'); return; }
        const flags = beta.split(',').map(value => value.trim());
        if (flags.length > CLAUDE_SDK_BETAS.size || new Set(flags).size !== flags.length || flags.some(value => !CLAUDE_SDK_BETAS.has(value))) { this.error(res, 422, 'anthropic_beta_not_qualified'); return; }
      }
      // Known automatic SDK headers are deliberately not forwarded: initial
      // qualified payloads use disabled thinking and no context-management edits.
    }
    if (this.closed) { this.error(res, 503, 'proxy_closed'); return; }
    if (!this.ledgerHealthy) { this.error(res, 503, 'accounting_unavailable'); return; }
    if (this.active) { this.error(res, 409, 'request_in_progress'); return; }
    if (!/^application\/json(?:\s*;|$)/i.test(req.headers['content-type'] ?? '') || req.headers['content-encoding'] && req.headers['content-encoding'] !== 'identity') { this.error(res, 415, 'json_required'); return; }
    if (Number(req.headers['content-length'] ?? 0) > MAX_REQUEST_BYTES) { this.error(res, 413, 'request_too_large'); return; }
    this.active = true;
    let finishActive!: () => void;
    this.activeFinished = new Promise<void>((resolve) => { finishActive = resolve; });
    let reservation = 0;
    let admitted = false;
    let reservationCheckpointed = false;
    let settled = false;
    let dispatched = false;
    let unknownMarked = false;
    let unknownCheckpointAttempted = false;
    try {
      const raw = await this.read(req);
      if (this.closed || res.destroyed) throw new Rejection(503, 'proxy_closed');
      let value: Json;
      try { value = JSON.parse(raw.toString('utf8')); } catch { throw new Rejection(400, 'invalid_json'); }
      if (!object(value)) throw new Rejection(400, 'invalid_json');
      if (this.options.provider === 'anthropic' && req.headers['anthropic-beta'] !== undefined && value.thinking !== undefined && (!object(value.thinking) || value.thinking.type !== 'disabled')) reject('anthropic_beta_payload_not_qualified');
      const prepared = prepare(this.options, value, path);
      // Official Anthropic token counting is free, but consumes the same bounded
      // request lane/counter. Its estimate never replaces the paid-request bound.
      reservation = prepared.counting ? 0 : cost(prepared.inputBound, this.options.inputMicrousdPerMillion) + cost(prepared.outputBound, this.options.outputMicrousdPerMillion);
      if (!Number.isSafeInteger(reservation) || this.accounting.requestCount >= this.options.requestLimit! || this.accounting.spentMicrousd + this.accounting.heldMicrousd + reservation > this.options.allowanceMicrousd) throw new Rejection(402, 'allowance_exhausted');
      // Reserve and mutate synchronously, then checkpoint before dispatch. Every
      // externally retried request occupies a fresh reservation and request slot.
      this.accounting.heldMicrousd += reservation;
      this.accounting.requestCount += 1;
      admitted = true;
      try { await this.checkpoint(); } catch { throw new Rejection(503, 'accounting_unavailable'); }
      reservationCheckpointed = true;
      try { await this.options.authorizeDispatch?.(); }
      catch { this.ledgerHealthy = false; throw new Rejection(403, 'dispatch_not_authorized'); }
      if (this.closed || res.destroyed) throw new Rejection(503, 'proxy_closed');
      dispatched = true;
      const usage = await this.forward(res, prepared.body, prepared.counting, path);
      if (usage && usage.input <= prepared.inputBound && usage.output <= prepared.outputBound) {
        const actual = cost(usage.input, this.options.inputMicrousdPerMillion) + cost(usage.output, this.options.outputMicrousdPerMillion);
        const settledAccounting = { ...this.accounting, heldMicrousd: this.accounting.heldMicrousd - reservation, spentMicrousd: this.accounting.spentMicrousd + actual };
        await this.checkpoint(settledAccounting);
        Object.assign(this.accounting, settledAccounting);
        settled = true;
      } else {
        this.accounting.unknownMicrousd += reservation;
        unknownMarked = true;
        unknownCheckpointAttempted = true;
        await this.checkpoint();
      }
      res.end();
    } catch (error) {
      if (admitted && !dispatched && reservationCheckpointed) {
        // Dispatch was provably prevented. Persist the refund before applying it
        // locally, while keeping the committed admitted-request counter monotonic.
        const releasedAccounting = { ...this.accounting, heldMicrousd: this.accounting.heldMicrousd - reservation };
        try {
          await this.checkpoint(releasedAccounting);
          Object.assign(this.accounting, releasedAccounting);
        } catch {
          // A failed release may have partially persisted. Keep the conservative
          // hold locally and quarantine rather than reporting a successful refund.
          error = new Rejection(503, 'accounting_unavailable');
        }
      } else if (admitted && !dispatched) {
        // Initial persistence may have committed before its callback failed.
        // Preserve both the hold and count; no provider request was authorized.
      } else if (admitted && !settled) {
        if (!unknownMarked) this.accounting.unknownMicrousd += reservation;
        if (!unknownCheckpointAttempted) {
          try { await this.checkpoint(); } catch { /* Admission remains quarantined. */ }
        }
      }
      const failure = error instanceof Rejection ? error : new Rejection(502, 'upstream_unavailable');
      this.error(res, failure.status, failure.code, failure.retryAfter);
    } finally {
      this.abortActive = undefined;
      this.active = false;
      finishActive();
      this.activeFinished = undefined;
    }
  }
  private async checkpoint(accounting: ProxyAccounting = this.accounting): Promise<void> {
    try { await this.options.onAccounting?.({ ...accounting, coverage: 'enforced' }); }
    catch { this.ledgerHealthy = false; throw new Rejection(503, 'accounting_unavailable'); }
  }
  private async forward(res: http.ServerResponse, body: Buffer, counting: boolean, path: string): Promise<Usage | undefined> {
    const url = new URL(path, this.upstream.origin);
    const transport = this.upstream.protocol === 'https:' ? https : http;
    return new Promise<Usage | undefined>((resolve, rejectForward) => {
      let done = false;
      const finish = (error?: Error, usage?: Usage): void => {
        if (done) return;
        done = true;
        clearTimeout(timer);
        res.off('close', cancelled);
        if (error) rejectForward(error); else resolve(usage);
      };
      const upstreamRequest = transport.request(url, {
        agent: this.agent,
        method: 'POST',
        headers: {
          'content-type': 'application/json', 'content-length': body.length, 'accept': 'application/json, text/event-stream', 'accept-encoding': 'identity',
          ...(this.options.provider === 'openai' ? { authorization: `Bearer ${this.options.credential}` } : { 'x-api-key': this.options.credential, 'anthropic-version': '2023-06-01' }),
        },
      });
      const abort = (): void => { upstreamRequest.destroy(); finish(new Rejection(502, 'request_interrupted')); };
      const cancelled = (): void => { if (!res.writableFinished) abort(); };
      const timer = setTimeout(() => { upstreamRequest.destroy(); finish(new Rejection(504, 'upstream_timeout')); }, this.options.timeoutMs);
      this.abortActive = abort;
      res.on('close', cancelled);
      upstreamRequest.on('error', () => finish(new Rejection(502, 'upstream_unavailable')));
      upstreamRequest.on('response', (response) => {
        if (!response.statusCode || response.statusCode < 200 || response.statusCode >= 300) {
          response.destroy();
          const status = response.statusCode === 429 ? 429 : 502;
          // Error bodies and provider response headers can contain credentials or
          // internal URLs. Return only an error code controlled by this process.
          const rawRetry = response.headers['retry-after'];
          let retryAfter: string | undefined;
          if (status === 429 && typeof rawRetry === 'string') {
            if (/^\d{1,12}$/.test(rawRetry)) retryAfter = String(Number(rawRetry));
            else { const time = Date.parse(rawRetry); if (Number.isFinite(time)) retryAfter = new Date(time).toUTCString(); }
          }
          finish(new Rejection(status, status === 429 ? 'upstream_rate_limited' : 'upstream_error', retryAfter));
          return;
        }
        const contentType = String(response.headers['content-type'] ?? '');
        if (response.headers['content-encoding'] && response.headers['content-encoding'] !== 'identity' || !/^(application\/json|text\/event-stream)(?:\s*;|$)/i.test(contentType)) {
          response.destroy(); finish(new Rejection(502, 'unsupported_upstream_response')); return;
        }
        const streaming = /^text\/event-stream/i.test(contentType);
        const observer = new UsageObserver(this.options.provider, streaming, counting);
        const writeHeaders = (): void => { res.writeHead(200, { 'content-type': streaming ? 'text/event-stream; charset=utf-8' : 'application/json', 'cache-control': 'no-store' }); };
        if (streaming) writeHeaders();
        const sanitize = (output: Buffer): Buffer => {
          let text = output.toString('utf8');
          // Operate on complete JSON bodies/SSE frames so secrets split across TCP
          // chunks cannot escape. Never forward provider headers or error frames.
          for (const secret of new Set([this.options.credential, JSON.stringify(this.options.credential).slice(1, -1)])) text = text.replaceAll(secret, '[redacted]');
          return Buffer.from(text);
        };
        response.on('data', (chunk: Buffer) => {
          if (done) return;
          try {
            const safe = observer.push(chunk);
            if (safe.length && !res.write(sanitize(safe))) response.pause();
          } catch { response.destroy(); finish(new Rejection(502, 'upstream_response_limit')); res.destroy(); }
        });
        const drain = (): void => { response.resume(); };
        res.on('drain', drain);
        response.on('end', () => {
          res.off('drain', drain);
          try {
            const usage = observer.finish();
            if (!streaming) { writeHeaders(); res.write(sanitize(observer.jsonBody())); }
            finish(undefined, usage);
          } catch (error) { finish(error instanceof Rejection ? error : new Rejection(502, 'invalid_upstream_response')); }
        });
        response.on('aborted', () => { res.off('drain', drain); finish(new Rejection(502, 'upstream_response_interrupted')); res.destroy(); });
        response.on('error', () => { res.off('drain', drain); finish(new Rejection(502, 'upstream_response_interrupted')); res.destroy(); });
      });
      upstreamRequest.end(body);
    });
  }
}
