import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { once } from 'node:events';
import { mkdtemp, mkdir, realpath, readFile, rm, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { ClaudeAdapter } from '../src/claude.ts';
import { AgentSessions, ApplicationScheduler } from '../src/sessions.ts';
import { SessionStore } from '../src/store.ts';
import type { ActionRequest, RuntimeEvent } from '../src/types.ts';

const enabled = process.platform === 'darwin' && process.env.HORMUZ_TEST_INSTALLED_CLAUDE === '1';

test('installed pinned Claude SDK plans, approves a Write, reviews its diff and continues persisted history through the production host', { skip: !enabled, timeout: 45_000 }, async t => {
  const root = await realpath(await mkdtemp('/private/tmp/hormuz-claude-installed-'));
  const workspace = join(root, 'p'), path = join(workspace, 'proof.txt');
  await mkdir(workspace); await writeFile(join(workspace, 'unchanged.txt'), 'original synthetic content');
  let generations = 0, totalRequests = 0;
  const envelopes: Record<string, unknown>[] = [];
  const server = createServer(async (request, response) => {
    if (++totalRequests > 6) { response.writeHead(429, { 'retry-after': '1' }); response.end(); return; }
    const chunks: Buffer[] = []; let bytes = 0;
    for await (const raw of request) { const chunk = Buffer.from(raw); bytes += chunk.length; if (bytes > 2_097_152) { response.writeHead(413); response.end(); return; } chunks.push(chunk); }
    const body = JSON.parse(Buffer.concat(chunks).toString()) as Record<string, unknown>;
    if (request.url === '/v1/messages/count_tokens') { response.writeHead(200, { 'content-type': 'application/json' }); response.end('{"input_tokens":100}'); return; }
    assert.equal(request.url, '/v1/messages');
    envelopes.push(body);
    const generation = ++generations;
    const write = generation === 2;
    const block = write ? { type: 'tool_use', id: 'offline-write', name: 'Write', input: {} } : { type: 'text', text: '' };
    const text = generation === 1 ? 'Write offline approved change to proof.txt after a concrete file approval.' : generation === 3 ? 'Completed the approved change.' : 'Review the existing approved result without any further writes.';
    const delta = write ? { type: 'input_json_delta', partial_json: JSON.stringify({ file_path: path, content: 'offline approved change' }) } : { type: 'text_delta', text };
    const events = [
      { type: 'message_start', message: { id: `message-${generations}`, type: 'message', role: 'assistant', model: body.model, content: [], stop_reason: null, stop_sequence: null, usage: { input_tokens: 100, output_tokens: 0 } } },
      { type: 'content_block_start', index: 0, content_block: block },
      { type: 'content_block_delta', index: 0, delta },
      { type: 'content_block_stop', index: 0 },
      { type: 'message_delta', delta: { stop_reason: write ? 'tool_use' : 'end_turn', stop_sequence: null }, usage: { output_tokens: 10 } },
      { type: 'message_stop' },
    ];
    response.writeHead(200, { 'content-type': 'text/event-stream' });
    for (const event of events) response.write(`event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`);
    response.end();
  });
  server.maxConnections = 2; server.requestTimeout = 5_000; server.headersTimeout = 5_000;
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  const address = server.address(); assert.ok(address && typeof address !== 'string');
  const connection = { provider: 'anthropic' as const, upstream: `http://127.0.0.1:${address.port}`, credential: 'fake-upstream-key', model: 'claude-sonnet-4-5-20250929', version: 'fake-connection-1', models: ['claude-sonnet-4-5-20250929'],
    allowanceMicrousd: 2_000_000, inputMicrousdPerMillion: 1_000_000, outputMicrousdPerMillion: 1_000_000,
    maxInputTokens: 2_000_000, maxOutputTokens: 1024, requestLimit: 6, timeoutMs: 20_000, allowLoopbackUpstream: true };
  // Persistent history can have a normal long profile path; only the SDK's
  // separately owned ephemeral Unix socket root needs a short address.
  const store = new SessionStore(join(root, 'private-profile-history-with-a-long-canonical-state-directory'));
  const settings = { profileId: 'installed-claude', store, scheduler: new ApplicationScheduler(), runtimeFactory: () => new ClaudeAdapter({ requestTimeoutMs: 5_000, runTimeoutMs: 20_000, shutdownTimeoutMs: 100 }),
    resolveConnection: async () => connection, authorizeCloud: async () => true, authorizeWorkspace: async (project: string, value: string) => project === 'synthetic-project' && value === workspace,
    authorizeAllowance: async (_project: string, value: number) => value === connection.allowanceMicrousd,
    parentAllowances: { profileMicrousd: 2_000_000, projectMicrousd: 2_000_000 }, runTimeoutMs: 20_000, approvalTimeoutMs: 5_000 };
  let manager = new AgentSessions(settings);
  const approvals: ActionRequest[] = [];
  const events: Array<RuntimeEvent & { sessionId?: string }> = [];
  const deadline = new AbortController(); const timer = setTimeout(() => deadline.abort(), 40_000);
  const listen = () => { manager.on('event', event => events.push(event)); manager.on('approval', request => approvals.push(request)); };
  try {
    listen(); await manager.initialize();
    const created = await manager.create({ projectId: 'synthetic-project', workspace, engine: 'claude', model: connection.model, connectionId: 'fake-provider', cloudAllowed: true, allowanceMicrousd: connection.allowanceMicrousd });
    const planned = await manager.plan(created.id, 'synthetic-project', 'Plan one synthetic approved file change.');
    assert.equal(planned.phase, 'ready'); assert.ok(planned.plan); assert.equal(approvals.length, 0);
    await assert.rejects(readFile(path), /ENOENT/);
    await manager.acceptPlan(created.id, 'synthetic-project', planned.plan.revision);
    const pending = once(manager, 'approval', { signal: deadline.signal });
    const execution = manager.execute(created.id, 'synthetic-project', planned.plan.revision);
    const [approval] = await Promise.race([pending, execution.then(() => { throw new Error('execution_completed_before_concrete_approval'); })]);
    assert.equal(approval.kind, 'file'); assert.equal(approval.operation.path, path);
    await assert.rejects(readFile(path), /ENOENT/);
    await manager.decide(created.id, 'synthetic-project', approval.approvalId, approval.fingerprint, { decision: 'accept' });
    assert.equal((await execution).phase, 'completed');
    assert.equal(approvals.length, 1); assert.equal(await readFile(path, 'utf8'), 'offline approved change');
    assert.equal(await readFile(join(workspace, 'unchanged.txt'), 'utf8'), 'original synthetic content');
    const reviewed = await manager.review(created.id, 'synthetic-project');
    assert.deepEqual(reviewed.changes.map(change => change.path), ['proof.txt']); assert.equal(reviewed.authorship, 'unknown');
    await manager.acceptReview(created.id, 'synthetic-project', reviewed.observedRevision);
    const beforeResume = await store.load(created.id, 'installed-claude', 'synthetic-project');
    assert.equal(typeof beforeResume.threadId, 'string');
    await manager.dispose(); manager = new AgentSessions(settings); listen(); await manager.initialize();
    await manager.resume(created.id, 'synthetic-project');
    const continued = await manager.plan(created.id, 'synthetic-project', 'Plan a review of the approved result without using tools.');
    assert.equal(continued.phase, 'ready'); assert.match(continued.plan!.text, /without any further writes/);
    const afterResume = await store.load(created.id, 'installed-claude', 'synthetic-project');
    assert.equal(afterResume.threadId, beforeResume.threadId);
    const history = envelopes[3].messages as Array<{ content: Array<Record<string, unknown>> }>;
    assert.ok(history.some(message => message.content.some(block => block.type === 'tool_result' && block.tool_use_id === 'offline-write')), 'continued SDK query must include the real completed tool history');
    const nativeVersions = events.filter(event => event.type === 'tool' && event.data && typeof event.data === 'object' && 'runtime' in event.data).map(event => (event.data as { runtime: unknown }).runtime);
    assert.equal(nativeVersions.length, 3);
    assert.ok(nativeVersions.every(value => JSON.stringify(value) === JSON.stringify({ sdkVersion: '0.3.299', cliVersion: '2.1.299', apiKeySource: 'ANTHROPIC_API_KEY' })));
    assert.equal(generations, 4); assert.ok(totalRequests <= 6);
    assert.ok(envelopes.every(value => value.max_tokens === 1024 && (value.thinking as { type: string }).type === 'disabled' && value.context_management === undefined));
    assert.equal(continued.accounting.requestCount, 4); assert.equal(continued.accounting.heldMicrousd, 0); assert.equal(continued.accounting.unknownMicrousd, 0);
    assert.equal(JSON.stringify(events).includes(connection.credential), false);
    t.diagnostic(JSON.stringify({ sdkVersion: '0.3.299', cliVersion: '2.1.299', syntheticRequests: generations, concreteApprovals: approvals.length, realToolHistoryResume: true, providerSpend: 0 }));
  } finally {
    clearTimeout(timer); deadline.abort();
    let clean = false;
    try { await manager.dispose(); clean = true; }
    finally {
      server.closeAllConnections(); await new Promise<void>(resolve => server.close(() => resolve()));
      if (clean) await rm(root, { recursive: true, force: true });
    }
  }
});
