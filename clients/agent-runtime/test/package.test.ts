import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { mkdtemp, mkdir, cp, rm, realpath, access } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

test('packed CLI ships executable JavaScript and runs from an installed node_modules path', { skip: process.platform === 'win32' }, async () => {
  const root = await realpath(await mkdtemp(join(tmpdir(), 'hormuz-cli-package-test-')));
  const source = fileURLToPath(new URL('..', import.meta.url));
  const modulePath = join(root, 'node_modules', '@hormuz', 'agent-runtime');
  const run = (command: string, args: string[], cwd = root) => spawnSync(command, args, { cwd, encoding: 'utf8', timeout: 30000, maxBuffer: 512 * 1024, env: { ...process.env, OPENAI_API_KEY: '', ANTHROPIC_API_KEY: '' } });
  try {
    const packed = run('npm', ['pack', '--json', '--ignore-scripts', '--cache', join(root, 'cache'), '--pack-destination', root], source);
    assert.equal(packed.status, 0, packed.stderr);
    const metadata = JSON.parse(packed.stdout)[0];
    assert.ok(metadata.files.some((file: { path: string }) => file.path === 'dist/agent-command.js'));
    assert.ok(metadata.files.some((file: { path: string }) => file.path === 'dist/claude-worker.js'));
    assert.ok(metadata.files.some((file: { path: string }) => file.path === 'dist/workspace-observer-worker.js'));
    assert.ok(metadata.files.some((file: { path: string }) => file.path === 'LICENSE'));
    assert.equal(metadata.files.some((file: { path: string }) => file.path.startsWith('test/') || file.path.startsWith('src/') || file.path.includes('node_modules/')), false);
    await mkdir(modulePath, { recursive: true });
    const extracted = run('tar', ['-xzf', join(root, metadata.filename), '--strip-components=1', '-C', modulePath]);
    assert.equal(extracted.status, 0, extracted.stderr);
    // Reproduce npm's normal hoisted layout without a nested node_modules link.
    // Copy only the pinned SDK and the current native platform package offline;
    // real copies ensure the SDK itself resolves its launcher from this layout.
    const sourceRequire = createRequire(join(source, 'package.json'));
    const sdkEntry = await realpath(sourceRequire.resolve('@anthropic-ai/claude-agent-sdk'));
    const sdkRequire = createRequire(sdkEntry);
    const scope = join(root, 'node_modules', '@anthropic-ai'); await mkdir(scope, { recursive: true });
    await cp(dirname(sdkEntry), join(scope, 'claude-agent-sdk'), { recursive: true });
    const base = `@anthropic-ai/claude-agent-sdk-${process.platform}-${process.arch}`;
    let native: { name: string; path: string } | undefined;
    for (const name of process.platform === 'linux' ? [base, base + '-musl'] : [base]) {
      try { native = { name, path: await realpath(sdkRequire.resolve(`${name}/claude`)) }; break; }
      catch (error) { if (!['MODULE_NOT_FOUND', 'ENOENT'].includes((error as NodeJS.ErrnoException).code ?? '')) throw error; }
    }
    assert.ok(native, 'the pinned SDK native package must be installed');
    await cp(dirname(native.path), join(scope, native.name.slice('@anthropic-ai/'.length)), { recursive: true });
    await assert.rejects(access(join(modulePath, 'node_modules')), /ENOENT/);
    const entry = join(modulePath, 'bin', 'hormuz-agent.mjs');
    const help = run(process.execPath, [entry, '--help']);
    assert.equal(help.status, 0, help.stderr); assert.match(help.stdout, /Hormuz Agent/);
    const importing = run(process.execPath, ['--input-type=module', '-e', 'await import(process.argv[1]); process.stdout.write("compiled-import-ok");', pathToFileURL(join(modulePath, 'dist', 'index.js')).href]);
    assert.equal(importing.status, 0, importing.stderr);
    const workspace = join(root, 'folder with spaces'); await mkdir(workspace);
    const state = join(root, 'private-profile');
    const init = run(process.execPath, [entry, 'init', '--cwd', workspace, '--state-root', state, '--engine', 'claude', '--model', 'fixture-model', '--input-rate', '1', '--output-rate', '2']);
    assert.equal(init.status, 0, init.stderr);
    const doctor = run(process.execPath, [entry, 'doctor', '--cwd', workspace, '--state-root', state]);
    assert.equal(doctor.status, 2, doctor.stderr);
    const diagnosis = JSON.parse(doctor.stdout);
    assert.equal(diagnosis.engine, 'claude'); assert.equal(diagnosis.runtimeLocated, true); assert.equal(diagnosis.credentialConfigured, false);
    if (process.platform === 'darwin') {
      // Exercise the compiled OS-contained worker too; its URL must use .js.
      const probe = run(process.execPath, ['--input-type=module', '-e',
        'const m=await import(process.argv[1]); const s=await m.observeWorkspaceSnapshot(process.argv[2],process.argv[3]); process.stdout.write(s.root);',
        pathToFileURL(join(modulePath, 'dist', 'index.js')).href, workspace, state]);
      assert.equal(probe.status, 0, probe.stderr); assert.equal(probe.stdout, workspace);
      const claude = run(process.execPath, ['--input-type=module', '-e', HOISTED_CLAUDE_PROBE,
        pathToFileURL(join(modulePath, 'dist', 'index.js')).href, workspace, join(root, 'native-runtime')]);
      assert.equal(claude.status, 0, claude.stderr);
      const qualification = JSON.parse(claude.stdout);
      assert.equal(qualification.status, 'completed', JSON.stringify(qualification)); assert.match(qualification.plan, /Hoisted native launcher/);
      assert.equal(qualification.generations, 1); assert.ok(qualification.requests <= 3);
      assert.equal(qualification.providerSpend, 0);
    }
    await access(join(modulePath, 'dist', 'terminal.js'));
  } finally { await rm(root, { recursive: true, force: true }); }
});

// One native SDK turn against a bounded localhost-only synthetic provider.
// The adapter uses its production OS sandbox and packed .js worker throughout.
const HOISTED_CLAUDE_PROBE = `
import { createServer } from 'node:http';
import { mkdir } from 'node:fs/promises';
const { ClaudeAdapter } = await import(process.argv[1]);
const workspace = process.argv[2], configDir = process.argv[3];
await mkdir(configDir, { mode: 0o700 });
let requests = 0, generations = 0; const observedEvents = [];
const server = createServer(async (request, response) => {
  if (++requests > 3) { response.writeHead(429, { 'retry-after': '1' }); response.end(); return; }
  const chunks = []; let bytes = 0;
  for await (const raw of request) {
    const chunk = Buffer.from(raw); bytes += chunk.length;
    if (bytes > 2097152) { response.writeHead(413); response.end(); return; }
    chunks.push(chunk);
  }
  const pathname = new URL(request.url, 'http://127.0.0.1').pathname;
  if (pathname === '/v1/messages/count_tokens') { response.writeHead(200, { 'content-type': 'application/json' }); response.end('{"input_tokens":100}'); return; }
  if (pathname !== '/v1/messages') { response.writeHead(404); response.end(); return; }
  const body = JSON.parse(Buffer.concat(chunks).toString('utf8')); generations++;
  const events = [
    { type: 'message_start', message: { id: 'hoisted-message', type: 'message', role: 'assistant', model: body.model, content: [], stop_reason: null, stop_sequence: null, usage: { input_tokens: 100, output_tokens: 0 } } },
    { type: 'content_block_start', index: 0, content_block: { type: 'text', text: '' } },
    { type: 'content_block_delta', index: 0, delta: { type: 'text_delta', text: 'Hoisted native launcher produces a read-only synthetic plan.' } },
    { type: 'content_block_stop', index: 0 },
    { type: 'message_delta', delta: { stop_reason: 'end_turn', stop_sequence: null }, usage: { output_tokens: 10 } },
    { type: 'message_stop' }
  ];
  response.writeHead(200, { 'content-type': 'text/event-stream' });
  for (const event of events) response.write('event: ' + event.type + '\\ndata: ' + JSON.stringify(event) + '\\n\\n');
  response.end();
});
server.maxConnections = 2; server.requestTimeout = 5000; server.headersTimeout = 5000;
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const controller = new AbortController();
const timer = setTimeout(() => controller.abort(), 15000);
const adapter = new ClaudeAdapter({ requestTimeoutMs: 5000, runTimeoutMs: 12000, shutdownTimeoutMs: 100 });
try {
  await adapter.start({ workspace, configDir, endpoint: 'http://127.0.0.1:' + server.address().port + '/v1', credential: 'local-synthetic-key', model: 'claude-sonnet-4-5-20250929', maxOutputTokens: 1024 });
  const result = await adapter.run({ sessionId: 'hoisted-package', prompt: 'Plan one synthetic task without tools.', mode: 'plan', signal: controller.signal, onEvent: event => observedEvents.push(event), onApproval: async () => ({ decision: 'decline' }) });
  process.stdout.write(JSON.stringify({ ...result, requests, generations, providerSpend: 0, observedEvents }));
} finally {
  clearTimeout(timer); controller.abort();
  try { await adapter.dispose(); }
  finally { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
}
`;
