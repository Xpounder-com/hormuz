import assert from 'node:assert/strict';
import { once } from 'node:events';
import { mkdtemp, realpath, rm } from 'node:fs/promises';
import http from 'node:http';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import type { AddressInfo } from 'node:net';
import { InferenceProxy } from '../src/admission.ts';
import type { InferenceProxyOptions } from '../src/admission.ts';
import { BudgetLedger } from '../src/budgets.ts';

const base = {
  provider: 'openai' as const, credential: 'secret-provider-key', model: 'test-model',
  allowanceMicrousd: 1000000, inputMicrousdPerMillion: 1000000,
  outputMicrousdPerMillion: 1000000, maxInputTokens: 100000, maxOutputTokens: 100,
  requestLimit: 10, timeoutMs: 5000, allowLoopbackUpstream: true,
};
type Fixture = {
  proxy: InferenceProxy; endpoint: string; credential: string;
  requests: { path: string; body: Record<string, unknown>; headers: http.IncomingHttpHeaders }[];
};
async function fixture(run: (f: Fixture) => Promise<void>, provider?: (req: http.IncomingMessage, res: http.ServerResponse, body: Record<string, unknown>) => void, options: Partial<InferenceProxyOptions> = {}): Promise<void> {
  const requests: Fixture['requests'] = [];
  const server = http.createServer(async (req, res) => {
    const chunks: Buffer[] = [];
    for await (const chunk of req) chunks.push(chunk);
    const body = JSON.parse(Buffer.concat(chunks).toString());
    requests.push({ path: req.url!, body, headers: req.headers });
    if (provider) provider(req, res, body);
    else { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify({ usage: { input_tokens: 10, output_tokens: 20 } })); }
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const upstream = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  const proxy = new InferenceProxy({ ...base, upstream, ...options });
  try {
    const started = await proxy.start();
    await run({ proxy, ...started, requests });
  } finally {
    await proxy.stop();
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
}
async function request(f: Fixture, body: unknown = { model: 'test-model', input: 'hello' }, path = '/responses', token = f.credential): Promise<{ status: number; body: unknown }> {
  const response = await fetch(f.endpoint + path, { method: 'POST', headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json' }, body: typeof body === 'string' ? body : JSON.stringify(body), signal: AbortSignal.timeout(10000) });
  const text = await response.text();
  let value: unknown; try { value = JSON.parse(text); } catch { value = text; }
  return { status: response.status, body: value };
}

test('JSON usage settles reservation; upstream receives only real provider authentication and output cap', async () => {
  await fixture(async (f) => {
    assert.match(f.endpoint, /^http:\/\/127\.0\.0\.1:\d+\/v1$/);
    assert.notEqual(f.credential, base.credential);
    const result = await request(f);
    assert.equal(result.status, 200);
    assert.equal(f.requests.length, 1);
    assert.equal(f.requests[0].path, '/v1/responses');
    assert.equal(f.requests[0].headers.authorization, `Bearer ${base.credential}`);
    assert.equal(f.requests[0].body.max_output_tokens, 100);
    assert.equal(f.requests[0].body.store, false);
    assert.equal(f.requests[0].body.truncation, 'disabled');
    assert.deepEqual(f.proxy.summary(), { spentMicrousd: 30, heldMicrousd: 0, unknownMicrousd: 0, requestCount: 1, coverage: 'enforced' });
  });
});

test('token, path, malformed JSON, model and hosted/modal inputs fail before any egress', async () => {
  await fixture(async (f) => {
    for (const [body, path, token, expected] of [
      [{ model: 'test-model', input: 'hello' }, '/responses', 'wrong', 401],
      [{ model: 'test-model', input: 'hello' }, '/responses?x=1', f.credential, 404],
      [{ model: 'test-model', input: 'hello' }, '/elsewhere', f.credential, 404],
      ['{bad', '/responses', f.credential, 400],
      [{ model: 'other-model', input: 'hello' }, '/responses', f.credential, 422],
      [{ model: 'test-model', input: 'hello', tools: [{ type: 'web_search' }] }, '/responses', f.credential, 422],
      [{ model: 'test-model', input: [{ role: 'user', content: [{ type: 'input_image', image_url: 'https://example.test' }] }] }, '/responses', f.credential, 422],
      [{ model: 'test-model', input: 'hello', previous_response_id: 'remote' }, '/responses', f.credential, 422],
      [{ model: 'test-model', input: 'hello', include: ['web_search_call.action.sources'] }, '/responses', f.credential, 422],
      [{ model: 'test-model', input: 'hello' }, '/responses/compact', f.credential, 422],
    ] as [unknown, string, string, number][]) assert.equal((await request(f, body, path, token)).status, expected);
    assert.equal(f.requests.length, 0);
    assert.equal(f.proxy.summary().requestCount, 0);
  });
});

test('admission refuses insufficient allowance and request lifetime limits without egress', async () => {
  await fixture(async (f) => {
    assert.equal((await request(f)).status, 402);
    assert.equal(f.requests.length, 0);
  }, undefined, { allowanceMicrousd: 1 });
  await fixture(async (f) => {
    assert.equal((await request(f)).status, 200);
    assert.equal((await request(f)).status, 402);
    assert.equal(f.requests.length, 1);
  }, undefined, { requestLimit: 1 });
});

test('persisted unknown holds consume allowance and the lifetime request counter', async () => {
  await fixture(async (f) => {
    assert.equal((await request(f)).status, 402);
    assert.equal(f.requests.length, 0);
    assert.deepEqual(f.proxy.summary(), { spentMicrousd: 2, heldMicrousd: 998, unknownMicrousd: 998, requestCount: 1, coverage: 'enforced' });
  }, undefined, { allowanceMicrousd: 1000, initialAccounting: { spentMicrousd: 2, heldMicrousd: 998, unknownMicrousd: 998, requestCount: 1 } });
  await fixture(async (f) => {
    assert.equal((await request(f)).status, 402);
    assert.equal(f.requests.length, 0);
  }, undefined, { requestLimit: 1, initialAccounting: { spentMicrousd: 0, heldMicrousd: 0, unknownMicrousd: 0, requestCount: 1 } });
});

test('at most one model request enters the provider at a time', async () => {
  let entered!: () => void;
  const entering = new Promise<void>((resolve) => { entered = resolve; });
  let release!: () => void;
  const released = new Promise<void>((resolve) => { release = resolve; });
  await fixture(async (f) => {
    const first = request(f);
    await entering;
    try { assert.equal((await request(f)).status, 409); assert.equal(f.requests.length, 1); }
    finally { release(); }
    assert.equal((await first).status, 200);
  }, (_req, res) => {
    entered();
    void released.then(() => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify({ usage: { input_tokens: 10, output_tokens: 1 } })); });
  });
});

test('Responses SSE usage settles only a complete terminal event with bounded streaming', async () => {
  await fixture(async (f) => {
    const result = await request(f, { model: 'test-model', input: 'hello', stream: true });
    assert.equal(result.status, 200);
    assert.equal(f.proxy.summary().spentMicrousd, 17);
    assert.equal(f.proxy.summary().heldMicrousd, 0);
  }, (_req, res) => {
    res.writeHead(200, { 'content-type': 'text/event-stream' });
    res.write('data: {"type":"response.output_text.delta","delta":"hi"}\r\n\r\n');
    res.write('data: {"type":"response.completed","response":{"usage":{"input_tokens":10,');
    res.end('"output_tokens":7}}}\n\n');
  });
});

test('Anthropic key authentication, text tool histories and cached usage settle conservatively', async () => {
  await fixture(async (f) => {
    const response = await fetch(f.endpoint + '/messages', {
      method: 'POST', headers: { 'x-api-key': f.credential, 'content-type': 'application/json' }, signal: AbortSignal.timeout(10000),
      body: JSON.stringify({ model: 'test-model', max_tokens: 80, messages: [{ role: 'user', content: 'hello' }], tools: [{ name: 'shell', input_schema: { type: 'object' } }] }),
    });
    assert.equal(response.status, 200); await response.text();
    assert.equal(f.requests[0].headers['x-api-key'], base.credential);
    assert.equal(f.requests[0].headers['anthropic-version'], '2023-06-01');
    assert.equal(f.requests[0].body.max_tokens, 80);
    assert.equal(f.proxy.summary().spentMicrousd, 34);
    assert.equal(f.proxy.summary().heldMicrousd, 0);
  }, (_req, res) => {
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ usage: { input_tokens: 10, cache_creation_input_tokens: 4, cache_read_input_tokens: 3, output_tokens: 17 } }));
  }, { provider: 'anthropic' });
});

test('Anthropic SSE combines initial input/cache tokens and final output only after message_stop', async () => {
  await fixture(async (f) => {
    assert.equal((await request(f, { model: 'test-model', messages: [{ role: 'user', content: 'hello' }], stream: true }, '/messages')).status, 200);
    assert.equal(f.proxy.summary().spentMicrousd, 38);
    assert.equal(f.proxy.summary().heldMicrousd, 0);
  }, (_req, res) => {
    res.writeHead(200, { 'content-type': 'text/event-stream' });
    res.write('event: message_start\ndata: {"type":"message_start","message":{"usage":{"input_tokens":10,"cache_read_input_tokens":8,"output_tokens":0}}}\n\n');
    res.write('data: {"type":"message_delta","usage":{"output_tokens":20}}\n\n');
    res.end('data: {"type":"message_stop"}\n\n');
  }, { provider: 'anthropic' });
});

test('ambiguous errors and missing usage retain unknown reservations; retries get fresh reservations', async () => {
  await fixture(async (f) => {
    const first = await request(f);
    assert.equal(first.status, 502);
    assert.equal(JSON.stringify(first.body).includes(base.credential), false);
    const hold = f.proxy.summary().heldMicrousd;
    assert.ok(hold > 0);
    assert.equal(f.proxy.summary().unknownMicrousd, hold);
    assert.equal((await request(f)).status, 502);
    assert.equal(f.proxy.summary().heldMicrousd, hold * 2);
    assert.equal(f.proxy.summary().requestCount, 2);
    assert.equal(f.requests.length, 2);
  }, (_req, res) => { res.writeHead(500, { 'content-type': 'application/json' }); res.end(JSON.stringify({ error: base.credential })); });
  await fixture(async (f) => {
    assert.equal((await request(f)).status, 200);
    assert.equal(f.proxy.summary().unknownMicrousd, f.proxy.summary().heldMicrousd);
    assert.ok(f.proxy.summary().heldMicrousd > 0);
  }, (_req, res) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end('{"output":[]}'); });
});

test('downstream disconnect, upstream disconnect and stop preserve dispatched costs and close resources', async () => {
  let entered!: () => void;
  let entering = new Promise<void>((resolve) => { entered = resolve; });
  await fixture(async (f) => {
    const abort = new AbortController();
    const response = fetch(f.endpoint + '/responses', { method: 'POST', headers: { authorization: `Bearer ${f.credential}`, 'content-type': 'application/json' }, body: JSON.stringify({ model: 'test-model', input: 'hello' }), signal: abort.signal });
    await entering;
    abort.abort();
    await assert.rejects(response);
    await f.proxy.stop();
    assert.ok(f.proxy.summary().heldMicrousd > 0);
    assert.equal(f.proxy.summary().unknownMicrousd, f.proxy.summary().heldMicrousd);
    await assert.rejects(fetch(f.endpoint + '/responses', { signal: AbortSignal.timeout(1000) }));
  }, (_req, _res) => { entered(); });
  await fixture(async (f) => {
    const response = request(f);
    assert.equal((await response).status, 502);
    assert.ok(f.proxy.summary().unknownMicrousd > 0);
  }, (_req, res) => {
    res.writeHead(200, { 'content-type': 'application/json' });
    res.write('{"usage":');
    res.socket!.destroy();
  });
  entering = new Promise<void>((resolve) => { entered = resolve; });
  await fixture(async (f) => {
    const response = request(f);
    await entering;
    await f.proxy.stop();
    await assert.rejects(response);
    assert.equal(f.proxy.summary().unknownMicrousd, f.proxy.summary().heldMicrousd);
    assert.ok(f.proxy.summary().heldMicrousd > 0);
  }, () => { entered(); });
});

test('timeouts are finite and do not retry', async () => {
  await fixture(async (f) => {
    assert.equal((await request(f)).status, 504);
    assert.equal(f.requests.length, 1);
    assert.ok(f.proxy.summary().unknownMicrousd > 0);
  }, () => {}, { timeoutMs: 40 });
});

test('request size, complexity, input cap and output cap reject before egress', async () => {
  await fixture(async (f) => {
    assert.equal((await request(f, { model: 'test-model', input: 'a'.repeat(30000) })).status, 422);
    assert.equal((await request(f, { model: 'test-model', input: 'hello', max_output_tokens: 101 })).status, 422);
    let schema: Record<string, unknown> = {};
    for (let i = 0; i < 40; i++) schema = { child: schema };
    assert.equal((await request(f, { model: 'test-model', input: 'hello', tools: [{ type: 'function', name: 'tool', parameters: schema }] })).status, 422);
    assert.equal(f.requests.length, 0);
  });
});

test('custom function namespace tools work; configuration validation rejects secrets in URLs and unsafe upstreams', async () => {
  await fixture(async (f) => {
    assert.equal((await request(f, { model: 'test-model', input: 'hello', tools: [{ type: 'namespace', name: 'functions', tools: [{ type: 'function', name: 'shell', parameters: { type: 'object' } }, { type: 'custom', name: 'patch', format: { type: 'text' } }] }] })).status, 200);
  });
  for (const upstream of ['http://example.test', 'https://key:secret@example.test', 'https://example.test?key=secret', 'http://127.0.0.1']) assert.throws(() => new InferenceProxy({ ...base, upstream, allowLoopbackUpstream: false }));
  assert.throws(() => new InferenceProxy({ ...base, upstream: 'https://example.test', initialAccounting: { spentMicrousd: 0, heldMicrousd: 1, unknownMicrousd: 2, requestCount: 0 } }));
});

test('durable reservation checkpoint precedes provider egress and records settlement', async () => {
  const checkpoints: ReturnType<InferenceProxy['summary']>[] = [];
  await fixture(async (f) => {
    assert.equal((await request(f)).status, 200);
    assert.equal(checkpoints.length, 2);
    assert.ok(checkpoints[0].heldMicrousd > 0);
    assert.equal(checkpoints[0].requestCount, 1);
    assert.equal(checkpoints[0].spentMicrousd, 0);
    assert.equal(checkpoints[1].heldMicrousd, 0);
    assert.equal(checkpoints[1].spentMicrousd, 30);
  }, (_req, res) => {
    assert.equal(checkpoints.length, 1, 'provider must not receive request before reservation is durable');
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ usage: { input_tokens: 10, output_tokens: 20 } }));
  }, { onAccounting: async (snapshot) => { checkpoints.push(snapshot); } });
});

test('failed initial admission checkpoint retains conservative accounting and never egresses', async () => {
  await fixture(async (f) => {
    assert.equal((await request(f)).status, 503);
    assert.equal(f.requests.length, 0);
    assert.ok(f.proxy.summary().heldMicrousd > 0);
    assert.equal(f.proxy.summary().unknownMicrousd, 0);
    assert.equal(f.proxy.summary().requestCount, 1);
    assert.equal((await request(f)).status, 503);
    assert.equal(f.requests.length, 0);
  }, undefined, { onAccounting: async () => { throw new Error('sensitive filesystem path or credential'); } });
});

test('failed settlement checkpoint retains unknown hold and quarantines further admission', async () => {
  let calls = 0;
  await fixture(async (f) => {
    await assert.rejects(request(f));
    assert.equal(f.requests.length, 1);
    assert.ok(f.proxy.summary().heldMicrousd > 0);
    assert.equal(f.proxy.summary().heldMicrousd, f.proxy.summary().unknownMicrousd);
    assert.equal(f.proxy.summary().spentMicrousd, 0);
    assert.equal((await request(f)).status, 503);
    assert.equal(f.requests.length, 1);
  }, undefined, { onAccounting: async () => { if (++calls > 1) throw new Error('checkpoint unavailable'); } });
});

test('official-compatible token counting is free but consumes bounded request lane/counter without generation cap', async () => {
  await fixture(async (f) => {
    const result = await request(f, { model: 'test-model', messages: [{ role: 'user', content: 'hello' }] }, '/messages/count_tokens');
    assert.equal(result.status, 200);
    assert.deepEqual(result.body, { input_tokens: 12 });
    assert.equal(f.requests[0].body.max_tokens, undefined);
    assert.deepEqual(f.proxy.summary(), { spentMicrousd: 0, heldMicrousd: 0, unknownMicrousd: 0, requestCount: 1, coverage: 'enforced' });
    assert.equal((await request(f, { model: 'test-model', messages: [{ role: 'user', content: 'hello' }] }, '/messages/count_tokens')).status, 402);
  }, (_req, res) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end('{"input_tokens":12}'); }, { provider: 'anthropic', allowanceMicrousd: 0, requestLimit: 1 });
});

test('restored in-flight holds become unknown and changed service tiers cannot alter configured rate coverage', async () => {
  await fixture(async (f) => {
    assert.equal(f.proxy.summary().heldMicrousd, 100);
    assert.equal(f.proxy.summary().unknownMicrousd, 100);
    assert.equal((await request(f, { model: 'test-model', input: 'hello', service_tier: 'priority' })).status, 422);
    assert.equal(f.requests.length, 0);
  }, undefined, { initialAccounting: { spentMicrousd: 0, heldMicrousd: 100, unknownMicrousd: 0, requestCount: 1 } });
});

test('even successful provider responses redact real keys across chunk boundaries; error JSON is never echoed', async () => {
  await fixture(async (f) => {
    const result = await request(f);
    assert.equal(result.status, 200);
    assert.equal(JSON.stringify(result.body).includes(base.credential), false);
    assert.equal(JSON.stringify(result.body).includes('[redacted]'), true);
    assert.equal(f.proxy.summary().spentMicrousd, 3);
  }, (_req, res) => {
    res.writeHead(200, { 'content-type': 'application/json; internal-key=' + base.credential });
    res.write('{"error":null,"text":"secret-provider-');
    setImmediate(() => res.end('key","usage":{"input_tokens":1,"output_tokens":2}}'));
  });
  await fixture(async (f) => {
    const result = await request(f);
    assert.equal(result.status, 502);
    assert.equal(JSON.stringify(result.body).includes(base.credential), false);
    assert.ok(f.proxy.summary().unknownMicrousd > 0);
  }, (_req, res) => {
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ error: { message: base.credential } }));
  });
  await fixture(async (f) => {
    const result = await request(f, { model: 'test-model', input: 'hello', stream: true });
    assert.equal(result.status, 200);
    assert.equal(String(result.body).includes(base.credential), false);
    assert.equal(String(result.body).includes('[redacted]'), true);
    assert.equal(f.proxy.summary().spentMicrousd, 3);
  }, (_req, res) => {
    res.writeHead(200, { 'content-type': 'text/event-stream' });
    res.write('data: {"type":"response.output_text.delta","delta":"secret-provider-');
    setImmediate(() => {
      res.write('key"}\n\n');
      res.end('data: {"type":"response.completed","response":{"usage":{"input_tokens":1,"output_tokens":2}}}\n\n');
    });
  });
});

test('rate limits propagate sanitized Retry-After without automatic retry or releasing dispatched hold', async () => {
  await fixture(async (f) => {
    const response = await fetch(f.endpoint + '/responses', { method: 'POST', headers: { authorization: `Bearer ${f.credential}`, 'content-type': 'application/json' }, body: JSON.stringify({ model: 'test-model', input: 'hello' }), signal: AbortSignal.timeout(10000) });
    assert.equal(response.status, 429);
    assert.equal(response.headers.get('retry-after'), '5');
    assert.equal((await response.text()).includes(base.credential), false);
    assert.equal(f.requests.length, 1);
    assert.ok(f.proxy.summary().unknownMicrousd > 0);
  }, (_req, res) => { res.writeHead(429, { 'content-type': 'application/json', 'retry-after': '5' }); res.end(JSON.stringify({ error: base.credential })); });
});

test('pinned Codex optional encrypted-output projection is explicit and bounded metadata is supported', async () => {
  await fixture(async (f) => {
    assert.deepEqual(f.proxy.capabilities, { encryptedReasoning: false, reasoningHistory: false, compaction: false, anthropicSdkBetaMarker: true, anthropicBetaHeaders: false });
    assert.equal((await request(f, { model: 'test-model', input: 'hello', include: ['reasoning.encrypted_content'], reasoning: { summary: 'auto' }, client_metadata: { session_id: 'synthetic-session', turn_id: 'synthetic-turn' } })).status, 200);
    assert.equal(f.requests.length, 1);
    assert.equal(f.requests[0].body.include, undefined);
    assert.deepEqual(f.requests[0].body.client_metadata, { session_id: 'synthetic-session', turn_id: 'synthetic-turn' });
    const validCount = f.requests.length;
    for (const invalid of [{ field: {} }, { field: 'a'.repeat(1025) }, { ['k'.repeat(65)]: 'value' }, Object.fromEntries(Array.from({ length: 17 }, (_, i) => [`field${i}`, 'value']))]) assert.equal((await request(f, { model: 'test-model', input: 'hello', client_metadata: invalid })).status, 422);
    for (const item of [{ type: 'reasoning', id: 'reasoning', summary: [{ type: 'summary_text', text: 'Unqualified reasoning history.' }] }, { type: 'reasoning', id: 'reasoning', summary: [], encrypted_content: 'opaque-synthetic' }]) assert.equal((await request(f, { model: 'test-model', input: [item] })).status, 422);
    assert.equal(f.requests.length, validCount);
  });
});

test('only exact Messages beta=true SDK marker is projected, known optional beta headers are omitted', async () => {
  await fixture(async (f) => {
    const headers = { 'x-api-key': f.credential, 'content-type': 'application/json', 'anthropic-version': '2023-06-01', 'anthropic-beta': 'claude-code-20250219,interleaved-thinking-2025-05-14,thinking-token-count-2026-05-13,context-management-2025-06-27,prompt-caching-scope-2026-01-05' };
    const body = JSON.stringify({ model: 'test-model', messages: [{ role: 'user', content: 'Synthetic request.' }], thinking: { type: 'disabled' } });
    const response = await fetch(f.endpoint + '/messages?beta=true', { method: 'POST', headers, body, signal: AbortSignal.timeout(10000) });
    assert.equal(response.status, 200); await response.text();
    assert.equal(f.requests.length, 1);
    assert.equal(f.requests[0].path, '/v1/messages');
    assert.equal(f.requests[0].headers['anthropic-beta'], undefined);
    assert.equal(f.requests[0].headers['anthropic-version'], '2023-06-01');
    for (const path of ['/messages?beta=false', '/messages?beta=true&other=1', '/messages?beta=true&beta=true', '/messages?%62eta=true', '/messages/count_tokens?beta=true']) assert.equal((await request(f, JSON.parse(body), path)).status, 404);
    const unknownBeta = await fetch(f.endpoint + '/messages', { method: 'POST', headers: { ...headers, 'anthropic-beta': 'unqualified-hosted-feature' }, body, signal: AbortSignal.timeout(10000) });
    assert.equal(unknownBeta.status, 422); await unknownBeta.text();
    const context = await request(f, { ...JSON.parse(body), context_management: { edits: [{ type: 'clear_thinking_20251015', keep: 'all' }] } }, '/messages');
    assert.equal(context.status, 422);
    assert.equal((await request(f, { ...JSON.parse(body), output_config: { effort: 'high' } }, '/messages')).status, 422);
    const enabledThinking = await fetch(f.endpoint + '/messages?beta=true', { method: 'POST', headers, body: JSON.stringify({ ...JSON.parse(body), thinking: { type: 'enabled', budget_tokens: 20 } }), signal: AbortSignal.timeout(10000) });
    assert.equal(enabledThinking.status, 422); await enabledThinking.text();
    assert.equal(f.requests.length, 1);
  }, undefined, { provider: 'anthropic' });
});

test('revoked dispatch authority durably refunds its reservation with monotonic request count', async () => {
  let cloudAllowed = true, authorityChecks = 0;
  const checkpoints: ReturnType<InferenceProxy['summary']>[] = [];
  const root = await realpath(await mkdtemp(join(tmpdir(), 'hormuz-dispatch-budget-test-')));
  try {
    const ledger = new BudgetLedger(join(root, 'usage'));
    await fixture(async (f) => {
      assert.equal((await request(f)).status, 200);
      assert.equal(f.requests.length, 1);
      cloudAllowed = false;
      assert.equal((await request(f)).status, 403);
      assert.equal(f.requests.length, 1);
      assert.equal(authorityChecks, 2);
      const accounting = { spentMicrousd: 30, heldMicrousd: 0, unknownMicrousd: 0, requestCount: 2 };
      assert.deepEqual(f.proxy.summary(), { ...accounting, coverage: 'enforced' });
      assert.equal(checkpoints.length, 4, 'each reservation has a durable settlement or refund');
      assert.deepEqual(checkpoints.at(-1), { ...accounting, coverage: 'enforced' });
      const restarted = new BudgetLedger(join(root, 'usage'));
      assert.deepEqual(await restarted.forSession('session', 'profile', 'project'), accounting);
      assert.deepEqual(await restarted.totals('profile', 'project'), { profileMicrousd: 30, projectMicrousd: 30 });
      assert.equal((await request(f)).status, 503);
      assert.equal(f.requests.length, 1);
      assert.equal(checkpoints.length, 4, 'quarantined authority cannot create another admission');
    }, undefined, {
      onAccounting: async snapshot => {
        const { coverage: _coverage, ...accounting } = snapshot;
        await ledger.checkpoint('session', 'profile', 'project', accounting, { profileMicrousd: base.allowanceMicrousd, projectMicrousd: base.allowanceMicrousd });
        checkpoints.push(snapshot);
      },
      authorizeDispatch: async () => {
        authorityChecks++;
        assert.ok(checkpoints.at(-1)!.heldMicrousd > 0, 'authority must be checked after durable admission');
        if (!cloudAllowed) throw new Error('cloud permission revoked');
      },
    });
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('failed durable refund retains the prior hold and quarantines without provider egress', async () => {
  const root = await realpath(await mkdtemp(join(tmpdir(), 'hormuz-dispatch-refund-test-')));
  try {
    const ledger = new BudgetLedger(join(root, 'usage'));
    let checkpoints = 0;
    await fixture(async (f) => {
      assert.equal((await request(f)).status, 503);
      assert.equal(f.requests.length, 0);
      assert.equal(checkpoints, 2, 'reservation and exactly one refund attempt');
      const { coverage: _coverage, ...accounting } = f.proxy.summary();
      assert.ok(accounting.heldMicrousd > 0);
      assert.equal(accounting.unknownMicrousd, 0);
      assert.equal(accounting.spentMicrousd, 0);
      assert.equal(accounting.requestCount, 1);
      const restarted = new BudgetLedger(join(root, 'usage'));
      assert.deepEqual(await restarted.forSession('session', 'profile', 'project'), accounting);
      assert.deepEqual(await restarted.totals('profile', 'project'), { profileMicrousd: accounting.heldMicrousd, projectMicrousd: accounting.heldMicrousd });
      assert.equal((await request(f)).status, 503);
      assert.equal(f.requests.length, 0);
      assert.equal(checkpoints, 2);
    }, undefined, {
      onAccounting: async snapshot => {
        if (++checkpoints > 1) throw new Error('refund checkpoint unavailable');
        const { coverage: _coverage, ...accounting } = snapshot;
        await ledger.checkpoint('session', 'profile', 'project', accounting, { profileMicrousd: base.allowanceMicrousd, projectMicrousd: base.allowanceMicrousd });
      },
      authorizeDispatch: () => { throw new Error('cloud permission revoked'); },
    });
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('initial checkpoint that commits before failing preserves its durable hold and admitted count', async () => {
  const root = await realpath(await mkdtemp(join(tmpdir(), 'hormuz-partial-admission-test-')));
  try {
    const ledger = new BudgetLedger(join(root, 'usage'));
    let checkpoints = 0, authorityChecks = 0;
    await fixture(async (f) => {
      assert.equal((await request(f)).status, 503);
      assert.equal(f.requests.length, 0);
      assert.equal(authorityChecks, 0, 'failed admission checkpoint cannot authorize dispatch');
      const { coverage: _coverage, ...accounting } = f.proxy.summary();
      assert.ok(accounting.heldMicrousd > 0);
      assert.equal(accounting.unknownMicrousd, 0);
      assert.equal(accounting.requestCount, 1);
      const restarted = new BudgetLedger(join(root, 'usage'));
      assert.deepEqual(await restarted.forSession('session', 'profile', 'project'), accounting);
      assert.deepEqual(await restarted.totals('profile', 'project'), { profileMicrousd: accounting.heldMicrousd, projectMicrousd: accounting.heldMicrousd });
      assert.equal((await request(f)).status, 503);
      assert.equal(checkpoints, 1, 'uncertain initial checkpoint is quarantined without a second write');
    }, undefined, {
      onAccounting: async snapshot => {
        checkpoints++;
        const { coverage: _coverage, ...accounting } = snapshot;
        await ledger.checkpoint('session', 'profile', 'project', accounting, { profileMicrousd: base.allowanceMicrousd, projectMicrousd: base.allowanceMicrousd });
        throw new Error('later session save failed after budget receipt committed');
      },
      authorizeDispatch: () => { authorityChecks++; },
    });
  } finally { await rm(root, { recursive: true, force: true }); }
});
