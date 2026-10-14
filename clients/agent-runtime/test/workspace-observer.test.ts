import assert from 'node:assert/strict';
import { link, lstat, mkdir, mkdtemp, readFile, realpath, readdir, rename, rm, symlink, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { createInterface } from 'node:readline';
import test from 'node:test';
import { observeWorkspaceSnapshot, observeWorkspaceDiff, cleanupWorkspaceObservers } from '../src/workspace-observer.ts';
import { prepareSandbox } from '../src/sandbox.ts';
import { RpcProcess } from '../src/rpc.ts';

test('production observations use a credential-free read-only worker and clean its private temporary state', { skip: process.platform !== 'darwin', timeout: 20_000 }, async () => {
  const root = await realpath(await mkdtemp('/private/tmp/hormuz-observer-'));
  try {
    const workspace = join(root, 'workspace'), state = join(root, 'state');
    await mkdir(workspace); await mkdir(state, { mode: 0o700 });
    await writeFile(join(workspace, 'code.ts'), 'export const value = 1;\n');
    await writeFile(join(workspace, '.env'), 'SECRET=value');
    const outside = join(root, 'private.txt'); await writeFile(outside, 'outside private canary');
    await symlink(outside, join(workspace, 'symlink.txt'));
    await link(outside, join(workspace, 'hardlink.txt'));
    const baseline = await observeWorkspaceSnapshot(workspace, state);
    assert.equal(baseline.files['code.ts']?.text, 'export const value = 1;\n');
    assert.equal(baseline.files['.env']?.text, undefined);
    assert.equal(baseline.files['symlink.txt'], undefined);
    assert.equal(baseline.files['hardlink.txt'], undefined);
    assert.equal(baseline.coverage.omitted.find(item => item.path === 'hardlink.txt')?.reason, 'hardlink');
    assert.deepEqual(await readdir(state), []);
    await writeFile(join(workspace, 'code.ts'), 'export const value = 2;\n');
    const diff = await observeWorkspaceDiff(baseline, state);
    assert.equal(diff.authorship, 'unknown');
    assert.equal(diff.changes[0]?.path, 'code.ts');
    assert.equal(diff.changes[0]?.beforeText, 'export const value = 1;\n');
    assert.equal(diff.changes[0]?.afterText, 'export const value = 2;\n');
    assert.equal(await readFile(outside, 'utf8'), 'outside private canary');
    assert.deepEqual(await readdir(state), []);
    assert.equal((await lstat(state)).mode & 0o777, 0o700);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('uncertain observer cleanup retains its owned handle and blocks new work until retry succeeds', { skip: process.platform !== 'darwin', timeout: 10_000 }, async t => {
  const root = await realpath(await mkdtemp('/private/tmp/hormuz-observer-cleanup-'));
  const workspace = join(root, 'workspace'), state = join(root, 'state');
  let rejectCleanup = true;
  const dispose = RpcProcess.prototype.dispose;
  const mocked = t.mock.method(RpcProcess.prototype, 'dispose', function (this: RpcProcess) {
    return rejectCleanup ? Promise.reject(new Error('synthetic-cleanup-failure')) : dispose.call(this);
  });
  try {
    await mkdir(workspace); await mkdir(state, { mode: 0o700 }); await writeFile(join(workspace, 'file.txt'), 'public');
    await assert.rejects(observeWorkspaceSnapshot(workspace, state), /synthetic-cleanup-failure/);
    assert.equal((await readdir(state)).filter(name => name.startsWith('observer-')).length, 1);
    await assert.rejects(observeWorkspaceSnapshot(workspace, state), /workspace_observer_busy/);
    rejectCleanup = false;
    await cleanupWorkspaceObservers(state);
    assert.deepEqual(await readdir(state), []);
    assert.equal((await observeWorkspaceSnapshot(workspace, state)).files['file.txt']?.text, 'public');
  } finally {
    rejectCleanup = false; mocked.mock.restore();
    await cleanupWorkspaceObservers(state);
    await rm(root, { recursive: true, force: true });
  }
});

test('OS containment denies a coordinated ancestor swap after path validation', { skip: process.platform !== 'darwin', timeout: 10_000 }, async () => {
  const root = await realpath(await mkdtemp('/private/tmp/hormuz-observer-race-'));
  let child: ReturnType<typeof spawn> | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let exited: Promise<unknown[]> | undefined;
  try {
    const workspace = join(root, 'workspace'), config = join(root, 'runtime'), outside = join(root, 'private');
    await mkdir(workspace); await mkdir(config, { mode: 0o700 }); await mkdir(outside);
    const safe = join(workspace, 'safe'), ancestor = join(workspace, 'part'); await mkdir(safe);
    await writeFile(join(safe, 'data.txt'), 'public workspace');
    await writeFile(join(outside, 'data.txt'), 'private canary must not be read');
    await symlink(outside, ancestor);
    const sourceDirectory = dirname(fileURLToPath(new URL('../src/workspace.ts', import.meta.url)));
    const script = join(workspace, 'ancestor-fixture.mjs');
    await writeFile(script, `
import { constants } from 'node:fs';
import { lstat, open } from 'node:fs/promises';
import { assertWorkspacePath } from ${JSON.stringify(new URL('../src/workspace.ts', import.meta.url).href)};
const path = ${JSON.stringify(join(ancestor, 'data.txt'))};
const ack = () => new Promise(resolve => process.stdin.once('data', resolve));
const first = await lstat(path); // A private inode can be stat'ed without reading bytes.
process.stdout.write(JSON.stringify({ phase: 'stat' }) + '\\n'); await ack();
await assertWorkspacePath(${JSON.stringify(workspace)}, path);
process.stdout.write(JSON.stringify({ phase: 'validated' }) + '\\n'); await ack();
let handle;
try {
  handle = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW);
  const second = await handle.stat();
  const text = await handle.readFile('utf8');
  process.stdout.write(JSON.stringify({ phase: 'result', read: true, sameInode: first.ino === second.ino, text }) + '\\n');
} catch (error) { process.stdout.write(JSON.stringify({ phase: 'result', read: false, code: error.code }) + '\\n'); }
finally { await handle?.close(); process.stdin.destroy(); }
`);
    const env = { PATH: '/usr/bin:/bin', HOME: config, TMPDIR: join(config, 'tmp'), OPENSSL_CONF: '/dev/null' };
    const launch = await prepareSandbox({ command: process.execPath, args: [script], workspace, configDir: config, endpoint: 'http://127.0.0.1:1/v1', env,
      workspaceWrite: false, allowProxyNetwork: false, trustedReadPaths: [sourceDirectory, join(dirname(sourceDirectory), 'package.json')] });
    child = spawn(launch.command, launch.args, { env, stdio: ['pipe', 'pipe', 'ignore'] });
    exited = once(child, 'exit');
    timer = setTimeout(() => child?.kill('SIGTERM'), 5_000);
    let result: Record<string, unknown> | undefined;
    for await (const line of createInterface({ input: child.stdout! })) {
      const message = JSON.parse(line) as Record<string, unknown>;
      if (message.phase === 'stat') { await rm(ancestor); await rename(safe, ancestor); child.stdin!.write('ack\n'); }
      else if (message.phase === 'validated') { await rename(ancestor, safe); await symlink(outside, ancestor); child.stdin!.write('ack\n'); }
      else if (message.phase === 'result') result = message;
    }
    const [code] = await exited;
    assert.equal(code, 0);
    assert.equal(result?.read, false);
    assert.ok(['EPERM', 'EACCES'].includes(String(result?.code)));
    assert.equal(await readFile(join(outside, 'data.txt'), 'utf8'), 'private canary must not be read');
  } finally {
    if (timer) clearTimeout(timer);
    if (child && child.exitCode === null && child.signalCode === null) { child.kill('SIGTERM'); await exited; }
    await rm(root, { recursive: true, force: true });
  }
});

test('a replaced workspace root cannot silently broaden the observer grant', { skip: process.platform !== 'darwin' }, async () => {
  const root = await realpath(await mkdtemp('/private/tmp/hormuz-observer-scope-'));
  try {
    const workspace = join(root, 'workspace'), state = join(root, 'state'), outside = join(root, 'outside');
    await mkdir(state, { mode: 0o700 }); await mkdir(outside);
    await symlink(outside, workspace);
    await assert.rejects(observeWorkspaceSnapshot(workspace, state), /workspace_root_changed/);
    assert.deepEqual(await readdir(state), []);
  } finally { await rm(root, { recursive: true, force: true }); }
});
