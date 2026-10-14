import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, readFile, realpath, rm, symlink, lstat, readdir, link, copyFile, chmod } from 'node:fs/promises';
import { tmpdir, platform } from 'node:os';
import { join } from 'node:path';
import { execFile } from 'node:child_process';
import { createServer } from 'node:http';
import { promisify } from 'node:util';
import { prepareSandbox } from '../src/sandbox.ts';

test('macOS outer sandbox allows owned workspace writes and denies unrelated private reads/writes', { skip: platform() !== 'darwin' }, async () => {
  const root = await realpath(await mkdtemp(join(tmpdir(), 'hormuz-sandbox-test-')));
  try {
    const workspace = join(root, 'workspace'), configDir = join(root, 'runtime');
    await mkdir(workspace); await mkdir(configDir, { mode: 0o700 });
    const outside = join(root, 'private-canary.txt'); await writeFile(outside, 'synthetic private data');
    const launch = await prepareSandbox({ command: '/bin/sh', args: ['-c', 'printf allowed > "$1/allowed.txt"; if cat "$2" >/dev/null 2>&1; then exit 42; fi; if printf denied > "$3" 2>/dev/null; then exit 43; fi', 'fixture', workspace, outside, join(root, 'outside-write.txt')], workspace, configDir, endpoint: 'http://127.0.0.1:1/v1', env: {} });
    const result = await promisify(execFile)(launch.command, launch.args, { timeout: 5000, maxBuffer: 8192, env: { PATH: '/usr/bin:/bin', HOME: configDir, TMPDIR: join(configDir, 'tmp') } });
    assert.equal(result.stdout, ''); assert.equal(await readFile(join(workspace, 'allowed.txt'), 'utf8'), 'allowed');
    assert.equal(await readFile(outside, 'utf8'), 'synthetic private data');
    await assert.rejects(readFile(join(root, 'outside-write.txt')), /ENOENT/);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('planning sandbox denies project writes while allowing private transcript writes', { skip: platform() !== 'darwin' }, async () => {
  const root = await realpath(await mkdtemp(join(tmpdir(), 'hormuz-sandbox-plan-test-')));
  try {
    const workspace = join(root, 'workspace'), configDir = join(root, 'runtime');
    await mkdir(workspace); await mkdir(configDir, { mode: 0o700 });
    await writeFile(join(workspace, 'existing.txt'), 'user content');
    const launch = await prepareSandbox({ command: '/bin/sh', args: ['-c', 'printf transcript > "$2/transcript.txt"; if printf changed > "$1/existing.txt" 2>/dev/null; then exit 42; fi; if printf new > "$1/new.txt" 2>/dev/null; then exit 43; fi', 'fixture', workspace, configDir], workspace, configDir, endpoint: 'http://127.0.0.1:1/v1', env: {}, workspaceWrite: false });
    await promisify(execFile)(launch.command, launch.args, { timeout: 5000, maxBuffer: 8192, env: { PATH: '/usr/bin:/bin', HOME: configDir, TMPDIR: join(configDir, 'tmp') } });
    assert.equal(await readFile(join(workspace, 'existing.txt'), 'utf8'), 'user content');
    await assert.rejects(readFile(join(workspace, 'new.txt')), { code: 'ENOENT' });
    assert.equal(await readFile(join(configDir, 'transcript.txt'), 'utf8'), 'transcript');
    assert.equal((await lstat(join(configDir, 'sandbox.sb'))).mode & 0o777, 0o600);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('sandbox profile regeneration rejects symlinks and hardlinks without touching their targets', { skip: platform() !== 'darwin' }, async () => {
  const root = await realpath(await mkdtemp(join(tmpdir(), 'hormuz-sandbox-profile-test-')));
  try {
    const workspace = join(root, 'workspace'), configDir = join(root, 'runtime');
    await mkdir(workspace); await mkdir(configDir, { mode: 0o700 });
    const options = { command: '/bin/sh', args: [], workspace, configDir, endpoint: 'http://127.0.0.1:1/v1', env: {} };
    await prepareSandbox(options); await prepareSandbox(options);
    const path = join(configDir, 'sandbox.sb'), outside = join(root, 'outside.sb');
    await writeFile(outside, 'keep this unrelated file', { mode: 0o600 });
    await rm(path); await symlink(outside, path);
    await assert.rejects(prepareSandbox(options), /unsafe_sandbox_profile/);
    assert.equal(await readFile(outside, 'utf8'), 'keep this unrelated file');
    await rm(path); await link(outside, path);
    await assert.rejects(prepareSandbox(options), /unsafe_sandbox_profile/);
    assert.equal(await readFile(outside, 'utf8'), 'keep this unrelated file');
    assert.equal((await readdir(configDir)).some(name => name.startsWith('.sandbox-')), false);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('Codex CF support requires a private preferences home and never grants preference writes', { skip: platform() !== 'darwin' }, async () => {
  const root = await realpath(await mkdtemp(join(tmpdir(), 'hormuz-sandbox-cf-test-')));
  try {
    const workspace = join(root, 'workspace'), configDir = join(root, 'runtime');
    await mkdir(workspace); await mkdir(configDir, { mode: 0o700 });
    const options = { command: '/bin/sh', args: [], workspace, configDir, endpoint: 'http://127.0.0.1:1/v1', env: {}, managedPreferenceDomain: 'com.openai.codex' as const };
    await assert.rejects(prepareSandbox(options), /managed_preferences_home_mismatch/);
    await prepareSandbox({ ...options, env: { HOME: configDir, CFFIXED_USER_HOME: configDir } });
    const profile = await readFile(join(configDir, 'sandbox.sb'), 'utf8');
    assert.ok(profile.includes('(deny user-preference-read user-preference-write)'));
    assert.ok(profile.includes('(allow user-preference-read (preference-domain "com.openai.codex"))'));
    assert.ok(profile.includes('(global-name "com.apple.cfprefsd.daemon.system")'));
    assert.equal(profile.includes('(allow user-preference-write'), false);
    assert.equal(profile.includes('(allow mach-lookup)'), false);
    assert.equal(profile.includes('(subpath "/Library/Preferences")'), false);
    assert.equal(profile.includes('(subpath "/Users")'), false);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('process execution cannot use an unapproved private binary outside the workspace', { skip: platform() !== 'darwin' }, async () => {
  const root = await realpath(await mkdtemp(join(tmpdir(), 'hormuz-sandbox-exec-test-')));
  try {
    const workspace = join(root, 'workspace'), configDir = join(root, 'runtime');
    await mkdir(workspace); await mkdir(configDir, { mode: 0o700 });
    const outside = join(root, 'unapproved-echo'); await copyFile('/bin/echo', outside); await chmod(outside, 0o755);
    const launch = await prepareSandbox({ command: '/bin/sh', args: ['-c', 'if "$1" unexpected >/dev/null 2>&1; then exit 42; fi', 'fixture', outside], workspace, configDir, endpoint: 'http://127.0.0.1:1/v1', env: {} });
    await promisify(execFile)(launch.command, launch.args, { timeout: 5000, maxBuffer: 8192, env: { PATH: '/usr/bin:/bin', HOME: configDir, TMPDIR: join(configDir, 'tmp') } });
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('sandboxed commands cannot hardlink an unrelated private file into the workspace', { skip: platform() !== 'darwin' }, async () => {
  const root = await realpath(await mkdtemp('/private/tmp/hormuz-hardlink-'));
  try {
    const workspace = join(root, 'workspace'), configDir = join(root, 'runtime');
    await mkdir(workspace); await mkdir(configDir, { mode: 0o700 });
    const outside = join(root, 'private.txt'), alias = join(workspace, 'alias.txt');
    await writeFile(outside, 'synthetic private canary');
    const launch = await prepareSandbox({ command: '/bin/sh', args: ['-c', 'if /bin/ln "$1" "$2" 2>/dev/null; then /bin/cat "$2"; exit 42; fi', 'fixture', outside, alias], workspace, configDir, endpoint: 'http://127.0.0.1:1/v1', env: {} });
    await promisify(execFile)(launch.command, launch.args, { timeout: 5000, maxBuffer: 8192, env: { PATH: '/usr/bin:/bin', HOME: configDir, TMPDIR: join(configDir, 'tmp') } });
    await assert.rejects(lstat(alias), { code: 'ENOENT' });
    assert.equal(await readFile(outside, 'utf8'), 'synthetic private canary');
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('private runtime Unix sockets do not permit TCP listeners or other socket paths', { skip: platform() !== 'darwin' }, async () => {
  const root = await realpath(await mkdtemp('/private/tmp/hormuz-uds-'));
  try {
    const workspace = join(root, 'workspace'), configDir = join(root, 'runtime');
    await mkdir(workspace); await mkdir(configDir, { mode: 0o700 });
    const script = join(workspace, 'unix-fixture.mjs');
    await writeFile(script, `
import { createServer, createConnection } from 'node:net';
const servers = [];
const listen = options => new Promise(resolve => {
  const server = createServer(socket => { socket.end('private'); }); servers.push(server);
  server.once('error', error => resolve({ ok: false, code: error.code }));
  server.listen(options, () => resolve({ ok: true }));
});
const approvedPath = ${JSON.stringify(join(configDir, 'tmp', 'cc-socks', 'fixture.sock'))};
try {
  const own = await listen(approvedPath);
  const body = own.ok ? await new Promise((resolve, reject) => { const socket = createConnection(approvedPath); let body = ''; socket.setTimeout(1000, () => socket.destroy(new Error('timeout'))); socket.on('data', data => { body += data; }); socket.once('end', () => resolve(body)); socket.once('error', reject); }) : '';
  const unrelated = await listen(${JSON.stringify(join(workspace, 'outside.sock'))});
  const tcp = await listen({ host: '127.0.0.1', port: 0 });
  process.stdout.write(JSON.stringify({ own, body, unrelated, tcp }));
} finally { for (const server of servers) if (server.listening) await new Promise(resolve => server.close(resolve)); }
`);
    const launch = await prepareSandbox({ command: process.execPath, args: [script], workspace, configDir, endpoint: 'http://127.0.0.1:1/v1', env: {}, allowPrivateUnixSockets: true, allowProxyNetwork: false });
    const result = await promisify(execFile)(launch.command, launch.args, { timeout: 5000, maxBuffer: 8192, env: { PATH: '/usr/bin:/bin', HOME: configDir, TMPDIR: join(configDir, 'tmp'), OPENSSL_CONF: '/dev/null' } });
    const observed = JSON.parse(result.stdout);
    assert.equal(observed.own.ok, true); assert.equal(observed.body, 'private');
    assert.equal(observed.unrelated.ok, false); assert.equal(observed.tcp.ok, false);
    const profile = await readFile(join(configDir, 'sandbox.sb'), 'utf8');
    assert.equal(profile.includes('(remote ip'), false);
    assert.equal(profile.includes('(allow network-bind network-inbound)'), false);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('sandboxed Node reaches only its proxy port and signals only its own process tree', { skip: platform() !== 'darwin' }, async () => {
  const root = await realpath(await mkdtemp(join(tmpdir(), 'hormuz-sandbox-network-test-')));
  let proxyRequests = 0, unrelatedRequests = 0;
  const proxy = createServer((_request, response) => { proxyRequests++; response.end('approved-proxy'); });
  const unrelated = createServer((_request, response) => { unrelatedRequests++; response.end('unrelated'); });
  const listen = (server: ReturnType<typeof createServer>) => new Promise<number>((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => { server.off('error', reject); const address = server.address(); if (!address || typeof address === 'string') reject(new Error('missing-port')); else resolve(address.port); });
  });
  const close = (server: ReturnType<typeof createServer>) => new Promise<void>((resolve, reject) => {
    if (!server.listening) { resolve(); return; }
    server.closeAllConnections(); server.close(error => error ? reject(error) : resolve());
  });
  try {
    const allowedPort = await listen(proxy), unrelatedPort = await listen(unrelated);
    const workspace = join(root, 'workspace'), configDir = join(root, 'runtime');
    await mkdir(workspace); await mkdir(configDir, { mode: 0o700 });
    const outside = join(root, 'private-canary.txt'); await writeFile(outside, 'synthetic private data');
    await symlink(outside, join(workspace, 'escaped-link'));
    const script = join(workspace, 'network-fixture.mjs');
    await writeFile(script, `
import { readFileSync } from 'node:fs';
import { request } from 'node:http';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
const ask = port => new Promise(resolve => {
  const req = request({ host: '127.0.0.1', port, path: '/', agent: false, timeout: 600 }, res => { let body = ''; res.on('data', data => { body += data; }); res.on('end', () => resolve({ ok: true, body })); });
  req.once('timeout', () => req.destroy(new Error('fixture-timeout')));
  req.once('error', error => resolve({ ok: false, code: error.code ?? 'error' }));
  req.end();
});
const own = spawn(process.execPath, ['-e', 'setTimeout(() => {}, 10000)'], { stdio: 'ignore', env: process.env });
process.once('SIGTERM', () => { own.kill('SIGTERM'); process.exit(124); });
let ownSignalAllowed = false, outsideSignalDenied = false, escapedReadDenied = false;
try {
  await once(own, 'spawn');
  try { process.kill(own.pid, 0); ownSignalAllowed = true; } catch {}
  try { process.kill(${process.pid}, 0); } catch (error) { outsideSignalDenied = error.code === 'EPERM'; }
  try { readFileSync(${JSON.stringify(join(workspace, 'escaped-link'))}); } catch (error) { escapedReadDenied = ['EPERM', 'EACCES'].includes(error.code); }
  const allowed = await ask(${allowedPort});
  const denied = await ask(${unrelatedPort});
  process.stdout.write(JSON.stringify({ allowed, denied, ownSignalAllowed, outsideSignalDenied, escapedReadDenied }));
} finally {
  const exited = once(own, 'exit'); own.kill('SIGTERM'); await exited;
}
`);
    const launch = await prepareSandbox({ command: process.execPath, args: [script], workspace, configDir, endpoint: `http://127.0.0.1:${allowedPort}/v1`, env: {} });
    const result = await promisify(execFile)(launch.command, launch.args, { timeout: 5000, maxBuffer: 8192, env: { PATH: '/usr/bin:/bin', HOME: configDir, TMPDIR: join(configDir, 'tmp') } });
    const observed = JSON.parse(result.stdout);
    assert.deepEqual(observed.allowed, { ok: true, body: 'approved-proxy' });
    assert.equal(observed.denied.ok, false);
    assert.equal(proxyRequests, 1); assert.equal(unrelatedRequests, 0);
    assert.equal(observed.ownSignalAllowed, true);
    assert.equal(observed.outsideSignalDenied, true);
    assert.equal(observed.escapedReadDenied, true);
    const profile = await readFile(join(configDir, 'sandbox.sb'), 'utf8');
    assert.ok(profile.includes('(deny default)'));
    assert.ok(profile.includes(`(allow network-outbound (remote ip "localhost:${allowedPort}"))`));
    assert.equal(profile.includes('(subpath "/Users")'), false);
    assert.equal(profile.includes('(subpath "/private/var")'), false);
  } finally { await close(proxy); await close(unrelated); await rm(root, { recursive: true, force: true }); }
});
