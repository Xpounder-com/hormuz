import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { once } from 'node:events';
import { mkdtemp, mkdir, readFile, realpath, rm, writeFile } from 'node:fs/promises';
import http from 'node:http';
import type { AddressInfo } from 'node:net';
import { platform, tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import type { TestContext } from 'node:test';
import { promisify } from 'node:util';
import { CodexAdapter, CODEX_VERSION } from '../src/codex.ts';
import { InferenceProxy } from '../src/admission.ts';
import { AgentSessions, ApplicationScheduler } from '../src/sessions.ts';
import { SessionStore } from '../src/store.ts';
import type { ActionRequest, RuntimeEvent } from '../src/types.ts';

// Explicit opt-in: the default test suite cannot launch a locally installed agent.
// These tests use synthetic files, responses and prices, never a paid provider.
const enabled = process.env.HORMUZ_TEST_INSTALLED_CODEX === '1' && platform() === 'darwin';
const COMMAND = '/opt/homebrew/bin/codex';
const MODEL = 'gpt-5.1-codex';
const PROVIDER_KEY = 'synthetic-provider-key';
const MAX_BODY_BYTES = 512 * 1024;
type Envelope = Record<string, unknown>;

async function installedVersion(root: string, t: TestContext): Promise<boolean> {
  const result = await promisify(execFile)(COMMAND, ['--version'], { timeout: 1500, maxBuffer: 8192,
    env: { PATH: '/opt/homebrew/bin:/usr/bin:/bin', HOME: root, CODEX_HOME: root, TMPDIR: root } });
  if (result.stdout.trim() !== `codex-cli ${CODEX_VERSION}`) { t.skip(`Requires installed Codex ${CODEX_VERSION}`); return false; }
  return true;
}
function message(text: string, number: number): Envelope {
  return { id: `msg-synthetic-${number}`, type: 'message', role: 'assistant', status: 'completed', content: [{ type: 'output_text', text, annotations: [] }] };
}
function sendResponse(res: http.ServerResponse, item: Envelope, number: number): void {
  const response = { id: `resp-synthetic-${number}`, object: 'response', status: 'completed', output: [item],
    usage: { input_tokens: 1, output_tokens: 1, total_tokens: 2, input_tokens_details: { cached_tokens: 0 }, output_tokens_details: { reasoning_tokens: 0 } } };
  const events: Envelope[] = [{ type: 'response.created', response: { ...response, status: 'in_progress', output: [] } }];
  if (item.type === 'message') {
    const part = (item.content as Envelope[])[0];
    events.push(
      { type: 'response.output_item.added', output_index: 0, item: { ...item, status: 'in_progress', content: [] } },
      { type: 'response.content_part.added', item_id: item.id, output_index: 0, content_index: 0, part: { type: 'output_text', text: '', annotations: [] } },
      { type: 'response.output_text.delta', item_id: item.id, output_index: 0, content_index: 0, delta: part.text },
      { type: 'response.output_text.done', item_id: item.id, output_index: 0, content_index: 0, text: part.text },
      { type: 'response.content_part.done', item_id: item.id, output_index: 0, content_index: 0, part },
    );
  } else {
    events.push(
      { type: 'response.output_item.added', output_index: 0, item: { ...item, status: 'in_progress', arguments: '' } },
      { type: 'response.function_call_arguments.delta', item_id: item.id, output_index: 0, delta: item.arguments },
      { type: 'response.function_call_arguments.done', item_id: item.id, output_index: 0, arguments: item.arguments },
    );
  }
  events.push({ type: 'response.output_item.done', output_index: 0, item }, { type: 'response.completed', response });
  res.writeHead(200, { 'content-type': 'text/event-stream' });
  for (const event of events) res.write(`data: ${JSON.stringify(event)}\n\n`);
  res.end();
}
async function fakeProvider(reply: (body: Envelope, count: number) => Envelope, limit: number) {
  const requests: Envelope[] = [];
  const server = http.createServer(async (req, res) => {
    req.setTimeout(2000, () => req.destroy());
    try {
      if (req.method !== 'POST' || req.url !== '/v1/responses') { res.writeHead(404); res.end(); return; }
      const chunks: Buffer[] = [];
      let bytes = 0;
      for await (const raw of req) {
        const chunk = Buffer.from(raw); bytes += chunk.length;
        if (bytes > MAX_BODY_BYTES) { res.writeHead(413); res.end(); return; }
        chunks.push(chunk);
      }
      if (requests.length >= limit) { res.writeHead(429, { 'retry-after': '1' }); res.end(); return; }
      const body = JSON.parse(Buffer.concat(chunks).toString()) as Envelope;
      requests.push(body);
      sendResponse(res, reply(body, requests.length), requests.length);
    } catch { if (!res.headersSent) { res.writeHead(500); res.end(); } else res.destroy(); }
  });
  server.maxConnections = 2; server.requestTimeout = 5000; server.headersTimeout = 2000; server.keepAliveTimeout = 1000;
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  return { requests, endpoint: `http://127.0.0.1:${(server.address() as AddressInfo).port}/v1`,
    close: async () => { server.closeAllConnections(); await new Promise<void>(resolve => server.close(() => resolve())); } };
}
function settings() { return { requestTimeoutMs: 2500, approvalTimeoutMs: 1500, runTimeoutMs: 5000, shutdownTimeoutMs: 100 }; }
function proxyOptions(upstream: string) {
  return { provider: 'openai' as const, upstream, credential: PROVIDER_KEY, model: MODEL,
    // Artificial fixture rates: no inference or money changes hands.
    allowanceMicrousd: 1000, inputMicrousdPerMillion: 1, outputMicrousdPerMillion: 1,
    maxInputTokens: 2_000_000, maxOutputTokens: 128, requestLimit: 4, timeoutMs: 2500, allowLoopbackUpstream: true };
}
function envelopeShape(body: Envelope) {
  return { fields: Object.keys(body).sort(), include: body.include,
    inputs: (body.input as Envelope[]).map(item => ({ type: item.type, fields: Object.keys(item).sort() })),
    tools: (body.tools as Envelope[]).map(tool => ({ type: tool.type, name: tool.name, fields: Object.keys(tool).sort() })) };
}
async function projectFixture() {
  const root = await realpath(await mkdtemp(join(tmpdir(), 'hormuz-installed-codex-')));
  const workspace = join(root, 'project'), configDir = join(root, 'runtime');
  await mkdir(workspace, { mode: 0o700 }); await mkdir(configDir, { mode: 0o700 });
  await writeFile(join(workspace, 'unchanged.txt'), 'Synthetic pre-existing content.\n');
  return { root, workspace, configDir };
}

test('installed Codex first Responses envelope passes the declared nonopaque projection', { skip: !enabled, timeout: 10000 }, async t => {
  const f = await projectFixture();
  let provider: Awaited<ReturnType<typeof fakeProvider>> | undefined;
  let proxy: InferenceProxy | undefined;
  const adapter = new CodexAdapter(settings());
  const client = new http.Agent({ keepAlive: true, maxSockets: 1, maxFreeSockets: 1 });
  const deadline = new AbortController(); const timer = setTimeout(() => deadline.abort(), 8000);
  try {
    if (!await installedVersion(f.configDir, t)) return;
    provider = await fakeProvider((_body, count) => message('<proposed_plan>Inspect the synthetic project without making changes.</proposed_plan>', count), 2);
    await adapter.start({ workspace: f.workspace, configDir: f.configDir, endpoint: provider.endpoint, credential: 'synthetic-gateway-token', model: MODEL });
    const result = await adapter.run({ sessionId: 'installed-envelope', prompt: 'Produce a one sentence plan. Do not use any tools.', mode: 'plan', signal: deadline.signal,
      onEvent: () => {}, onApproval: async () => ({ decision: 'decline' }) });
    assert.equal(result.status, 'completed'); assert.match(result.plan!, /synthetic project/);
    assert.equal(provider.requests.length, 1);
    const captured = provider.requests[0];
    assert.deepEqual(captured.include, ['reasoning.encrypted_content']);
    assert.equal(captured.max_output_tokens, undefined);
    assert.ok(captured.client_metadata && typeof captured.client_metadata === 'object');
    assert.ok((captured.tools as Envelope[]).every(tool => tool.type === 'function'));
    // Schema only: never print prompts, generated keys, metadata values or history.
    t.diagnostic(JSON.stringify({ installedVersion: CODEX_VERSION, requestShape: envelopeShape(captured) }));
    await adapter.dispose();
    proxy = new InferenceProxy(proxyOptions(provider.endpoint));
    const local = await proxy.start();
    const resultBody = await new Promise<{ status: number; body: string }>((resolve, reject) => {
      const req = http.request(local.endpoint + '/responses', { method: 'POST', agent: client, headers: { authorization: `Bearer ${local.credential}`, 'content-type': 'application/json' }, timeout: 2500 }, res => {
        let body = ''; res.on('data', chunk => { body += String(chunk); }); res.on('end', () => resolve({ status: res.statusCode!, body })); res.on('error', reject);
      });
      req.on('timeout', () => req.destroy(new Error('fixture_timeout'))); req.on('error', reject); req.end(JSON.stringify(captured));
    });
    assert.equal(resultBody.status, 200);
    assert.equal(provider.requests.length, 2);
    assert.equal(provider.requests[1].include, undefined);
    assert.equal(provider.requests[1].max_output_tokens, 128);
    assert.equal(proxy.summary().unknownMicrousd, 0);
    assert.equal(proxy.summary().spentMicrousd, 2);
    assert.equal(proxy.capabilities.encryptedReasoning, false);
  } finally {
    clearTimeout(timer); deadline.abort(); client.destroy();
    try { await adapter.dispose(); } finally { try { await proxy?.stop(); } finally { await provider?.close(); await rm(f.root, { recursive: true, force: true }); } }
  }
});

test('installed Codex completes a host-approved tool, diff and persisted session resume through the admission proxy', { skip: !enabled, timeout: 10000 }, async t => {
  const f = await projectFixture();
  let provider: Awaited<ReturnType<typeof fakeProvider>> | undefined;
  let manager: AgentSessions | undefined;
  const deadline = new AbortController(); const timer = setTimeout(() => deadline.abort(), 8000);
  const approvals: ActionRequest[] = [], events: RuntimeEvent[] = [];
  try {
    if (!await installedVersion(f.configDir, t)) return;
    provider = await fakeProvider((body, count) => {
      if (count === 1) return message('<proposed_plan>Write offline-approved to result.txt after concrete command approval.</proposed_plan>', count);
      if (count === 2) {
        assert.ok((body.tools as Envelope[]).some(tool => tool.name === 'exec_command'));
        return { id: 'fc-synthetic-write', type: 'function_call', call_id: 'call-synthetic-write', name: 'exec_command', status: 'completed',
          arguments: JSON.stringify({ cmd: "printf 'offline-approved\\n' > result.txt", workdir: f.workspace, yield_time_ms: 1000, max_output_tokens: 100, sandbox_permissions: 'require_escalated', justification: 'Write the synthetic fixture file after explicit host approval.' }) };
      }
      if (count === 3) {
        assert.ok((body.input as Envelope[]).some(item => item.type === 'function_call_output' && item.call_id === 'call-synthetic-write'));
        return message('The synthetic scoped change is complete.', count);
      }
      assert.ok((body.input as Envelope[]).some(item => item.type === 'function_call_output' && item.call_id === 'call-synthetic-write'), 'resume must carry the actual completed tool history');
      return message('<proposed_plan>Review the existing synthetic result without further changes.</proposed_plan>', count);
    }, 4);
    const options = proxyOptions(provider.endpoint);
    const store = new SessionStore(join(f.root, 'host-store'));
    manager = new AgentSessions({ profileId: 'installed-fixture', store, parentAllowances: { profileMicrousd: 1000, projectMicrousd: 1000 }, scheduler: new ApplicationScheduler(), runtimeFactory: () => new CodexAdapter(settings()),
      authorizeWorkspace: async (project, workspace) => project === 'synthetic-project' && workspace === f.workspace,
      authorizeAllowance: async (_project, allowance) => allowance === options.allowanceMicrousd,
      authorizeCloud: async () => true,
      resolveConnection: async () => ({ ...options, version: 'synthetic-connection-1', models: [MODEL] }),
      runTimeoutMs: 5000, approvalTimeoutMs: 1500 });
    manager.on('event', event => events.push(event)); manager.on('approval', request => approvals.push(request));
    await manager.initialize();
    const created = await manager.create({ projectId: 'synthetic-project', workspace: f.workspace, engine: 'codex', model: MODEL, connectionId: 'synthetic-provider', cloudAllowed: true, allowanceMicrousd: options.allowanceMicrousd });
    const planned = await manager.plan(created.id, 'synthetic-project', 'Make a plan for the synthetic change.');
    assert.equal(planned.phase, 'ready'); assert.ok(planned.plan);
    await manager.acceptPlan(created.id, 'synthetic-project', planned.plan.revision);
    const pending = once(manager, 'approval', { signal: deadline.signal });
    const execution = manager.execute(created.id, 'synthetic-project', planned.plan.revision);
    const [approval] = await Promise.race([pending, execution.then(() => { throw new Error('execution_completed_before_concrete_approval'); })]);
    assert.equal(approval.kind, 'command');
    await assert.rejects(readFile(join(f.workspace, 'result.txt')), /ENOENT/);
    await manager.decide(created.id, 'synthetic-project', approval.approvalId, approval.fingerprint, { decision: 'accept' });
    assert.equal((await execution).phase, 'completed');
    assert.equal(await readFile(join(f.workspace, 'result.txt'), 'utf8'), 'offline-approved\n');
    assert.equal(await readFile(join(f.workspace, 'unchanged.txt'), 'utf8'), 'Synthetic pre-existing content.\n');
    const reviewed = await manager.review(created.id, 'synthetic-project');
    assert.deepEqual(reviewed.changes.map(change => change.path), ['result.txt']);
    assert.equal(reviewed.authorship, 'unknown');
    await manager.acceptReview(created.id, 'synthetic-project', reviewed.observedRevision);
    const beforeResume = await store.load(created.id, 'installed-fixture', 'synthetic-project');
    assert.equal(typeof beforeResume.threadId, 'string');
    await manager.resume(created.id, 'synthetic-project');
    const continued = await manager.plan(created.id, 'synthetic-project', 'Plan a review of the result without using tools.');
    assert.equal(continued.phase, 'ready');
    const afterResume = await store.load(created.id, 'installed-fixture', 'synthetic-project');
    assert.equal(afterResume.threadId, beforeResume.threadId);
    assert.equal(continued.accounting.requestCount, 4); assert.equal(continued.accounting.spentMicrousd, 8);
    assert.equal(continued.accounting.heldMicrousd, 0); assert.equal(continued.accounting.unknownMicrousd, 0);
    assert.equal(provider.requests.length, 4); assert.equal(approvals.length, 1);
    assert.ok(events.some(event => event.type === 'tool'));
    assert.ok(provider.requests.every(body => body.include === undefined && body.max_output_tokens === 128));
    assert.equal(JSON.stringify(events).includes(PROVIDER_KEY), false);
    t.diagnostic(JSON.stringify({ installedVersion: CODEX_VERSION, syntheticRequests: 4, concreteApprovals: 1, toolHistoryRoundtrip: true, persistedThreadResume: true, providerSpend: 0 }));
  } finally {
    clearTimeout(timer); deadline.abort();
    try { await manager?.dispose(); } finally { await provider?.close(); await rm(f.root, { recursive: true, force: true }); }
  }
});

test('installed Codex denies a scoped escalation and interrupts a pending approval without writes', { skip: !enabled, timeout: 10000 }, async t => {
  const f = await projectFixture();
  let provider: Awaited<ReturnType<typeof fakeProvider>> | undefined;
  const adapter = new CodexAdapter(settings());
  const deadline = new AbortController(); const timer = setTimeout(() => deadline.abort(), 8000);
  try {
    if (!await installedVersion(f.configDir, t)) return;
    provider = await fakeProvider((_body, count) => {
      if (count === 2 || count === 4) return message('The synthetic denied or interrupted tool will not be retried.', count);
      const file = count === 1 ? 'declined.txt' : 'interrupted.txt';
      return { id: `fc-synthetic-${count}`, type: 'function_call', call_id: `call-synthetic-${count}`, name: 'exec_command', status: 'completed',
        arguments: JSON.stringify({ cmd: `printf denied > ${file}`, workdir: f.workspace, yield_time_ms: 1000, max_output_tokens: 100, sandbox_permissions: 'require_escalated', justification: 'Synthetic denial and interruption fixture.' }) };
    }, 4);
    await adapter.start({ workspace: f.workspace, configDir: f.configDir, endpoint: provider.endpoint, credential: 'synthetic-gateway-token', model: MODEL });
    let denied = 0;
    const decline = await adapter.run({ sessionId: 'installed-denial', prompt: 'Run the synthetic scoped fixture tool.', mode: 'execute', signal: deadline.signal,
      onEvent: () => {}, onApproval: async () => { denied++; return { decision: 'decline' }; } });
    assert.equal(denied, 1); assert.equal(decline.status, 'completed');
    await assert.rejects(readFile(join(f.workspace, 'declined.txt')), /ENOENT/);
    let announce!: () => void;
    const announced = new Promise<void>(resolve => { announce = resolve; });
    const pending = adapter.run({ sessionId: 'installed-interruption', prompt: 'Attempt the next synthetic tool and await a decision.', mode: 'execute', signal: deadline.signal,
      onEvent: () => {}, onApproval: async () => { announce(); return await new Promise(() => {}); } });
    await Promise.race([announced, pending.then(() => { throw new Error('execution_completed_before_pending_approval'); })]);
    await assert.rejects(readFile(join(f.workspace, 'interrupted.txt')), /ENOENT/);
    await adapter.interrupt();
    assert.equal((await pending).status, 'interrupted');
    await assert.rejects(readFile(join(f.workspace, 'interrupted.txt')), /ENOENT/);
    assert.ok(provider.requests.length <= 4);
    t.diagnostic(JSON.stringify({ installedVersion: CODEX_VERSION, declinedToolRequests: 1, interruptStatus: 'interrupted', syntheticRequests: provider.requests.length, providerSpend: 0 }));
  } finally {
    clearTimeout(timer); deadline.abort();
    try { await adapter.dispose(); } finally { await provider?.close(); await rm(f.root, { recursive: true, force: true }); }
  }
});
