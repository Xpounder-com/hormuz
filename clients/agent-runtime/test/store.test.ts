import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { chmod, link, lstat, mkdir, mkdtemp, open, readFile, readdir, realpath, rename, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import test from 'node:test';
import { promisify } from 'node:util';
import { MAX_SESSION_BYTES, SESSION_RUNTIME_LIMITS, SessionStore } from '../src/store.ts';
import type { SessionRecord } from '../src/store.ts';
import type { RuntimeHistoryPolicy } from '../src/types.ts';

async function fixture(): Promise<{ root: string; cleanup: () => Promise<void> }> {
  const root = await realpath(await mkdtemp(join(tmpdir(), 'hormuz-store-test-')));
  return { root, cleanup: () => rm(root, { recursive: true, force: true }) };
}
function record(overrides: Partial<SessionRecord> = {}): SessionRecord {
  return { schemaVersion: 1, id: 'session-1', profileId: 'profile-1', projectId: 'project-1', workspace: join(tmpdir(), 'workspace'), engine: 'codex', model: 'gpt-test', runtimeVersion: 'test-v1', threadId: 'provider-thread', phase: 'interrupted', plan: 'run the focused tests', acceptedPlanFingerprint: 'approved-in-this-process', accounting: { spentMicrousd: 23, heldMicrousd: 0, unknownMicrousd: 0, requestCount: 1 }, ...overrides };
}

test('private atomic persistence survives a new process-facing store and filters project/profile scope', async () => {
  const { root, cleanup } = await fixture();
  try {
    const store = new SessionStore(root);
    await store.save(record());
    await store.save(record({ id: 'session-2', profileId: 'profile-2' }));
    await store.save(record({ id: 'session-3', projectId: 'project-2' }));
    const reopened = new SessionStore(root);
    assert.deepEqual(await reopened.load('session-1', 'profile-1', 'project-1'), record());
    assert.deepEqual((await reopened.list('profile-1', 'project-1')).map(item => item.id), ['session-1']);
    assert.equal((await lstat(root)).mode & 0o777, 0o700);
    assert.equal((await lstat(join(root, 'sessions'))).mode & 0o777, 0o700);
    assert.equal((await lstat(join(root, 'sessions', 'session-1.json'))).mode & 0o777, 0o600);
    assert.deepEqual((await readdir(join(root, 'sessions'))).sort(), ['session-1.json', 'session-2.json', 'session-3.json']);
    await assert.rejects(reopened.load('session-1', 'profile-2', 'project-1'), /SESSION_SCOPE_DENIED/);
    await assert.rejects(reopened.load('session-1', 'profile-1', 'project-2'), /SESSION_SCOPE_DENIED/);
    await assert.rejects(reopened.save(record({ profileId: 'profile-2' })), /SESSION_SCOPE_DENIED/);
    await assert.rejects(reopened.remove('session-1', 'profile-1', 'project-2'), /SESSION_SCOPE_DENIED/);
  } finally { await cleanup(); }
});

test('approval/history reads tolerate the owning checkpoint writer replacing complete private records', async () => {
  const { root, cleanup } = await fixture();
  try {
    const writer = new SessionStore(root), reader = new SessionStore(root);
    await writer.save(record({ checkpoint: 0 }));
    const writerDone = (async () => {
      // One bounded checkpoint writer, one reader: exercise the atomic replacement
      // race occurring during active approval decisions, without network traffic.
      for (let checkpoint = 1; checkpoint <= 40; checkpoint++) await writer.save(record({ checkpoint }));
    })();
    const readDone = (async () => {
      for (let index = 0; index < 40; index++) {
        const observed = await reader.load('session-1', 'profile-1', 'project-1');
        assert.equal(observed.id, 'session-1');
        assert.equal(observed.profileId, 'profile-1');
        assert.equal(observed.projectId, 'project-1');
        assert.ok(Number.isSafeInteger(observed.checkpoint));
        assert.ok(Number(observed.checkpoint) >= 0 && Number(observed.checkpoint) <= 40);
        assert.equal((await lstat(join(root, 'sessions', 'session-1.json'))).mode & 0o777, 0o600);
      }
    })();
    await Promise.all([writerDone, readDone]);
    assert.equal((await reader.load('session-1', 'profile-1', 'project-1')).checkpoint, 40);
    assert.deepEqual(await readdir(join(root, 'sessions')), ['session-1.json']);
  } finally { await cleanup(); }
});

test('credentials, persisted grants, unsupported schemas, temporary projects and unbounded records fail closed', async () => {
  const { root, cleanup } = await fixture();
  try {
    const store = new SessionStore(root);
    await assert.rejects(store.save(record({ nested: { API_KEY: 'sensitive' } })), /SESSION_SECRET_OR_GRANT_DENIED/);
    await assert.rejects(store.save(record({ grants: ['workspace-write'] })), /SESSION_SECRET_OR_GRANT_DENIED/);
    await assert.rejects(store.save(record({ historyPolicy: { engine: 'codex', runtimeVersion: '0.160.0', executablePath: '/host/runtime' } })), /SESSION_SECRET_OR_GRANT_DENIED/);
    await assert.rejects(store.save(record({ runtimeHistoryPolicy: {} })), /SESSION_SECRET_OR_GRANT_DENIED/);
    await assert.rejects(store.save(record({ publicContent: 'sk-ant-api03-aVeryLongSecretThatMustNotBeSaved' })), /SESSION_SECRET_OR_GRANT_DENIED/);
    await assert.rejects(store.save(record({ schemaVersion: 2 as 1 })), /SESSION_SCHEMA_UNSUPPORTED/);
    await assert.rejects(store.save(record({ projectId: 'temporary' })), /SESSION_NAMED_PROJECT_REQUIRED/);
    await assert.rejects(store.save(record({ projectId: 'temporary-123' })), /SESSION_NAMED_PROJECT_REQUIRED/);
    await assert.rejects(store.save(record({ accounting: { spentMicrousd: Infinity } })), /SESSION_RECORD_INVALID/);
    await assert.rejects(store.save(record({ accounting: { spentMicrousd: '0', heldMicrousd: 0, unknownMicrousd: 0, requestCount: 1 } })), /SESSION_ACCOUNTING_INVALID/);
    await assert.rejects(store.save(record({ allowanceMicrousd: -1 })), /SESSION_ALLOWANCE_INVALID/);
    for (const runtimeAttempts of [-1, 129, 1.5, '1']) await assert.rejects(store.save(record({ runtimeAttempts: runtimeAttempts as number })), /SESSION_RUNTIME_ATTEMPTS_INVALID/);
    await assert.rejects(store.save(record({ publicContent: 'a'.repeat(MAX_SESSION_BYTES + 1) })), /SESSION_RECORD_TOO_LARGE/);
    await store.save(record());
    await writeFile(join(root, 'sessions', 'session-1.json'), '{broken', { mode: 0o600 });
    await assert.rejects(store.load('session-1', 'profile-1', 'project-1'), SyntaxError);
    await assert.rejects(store.save(record()), SyntaxError);
    await writeFile(join(root, 'sessions', 'session-1.json'), JSON.stringify(record({ schemaVersion: 2 as 1 })), { mode: 0o600 });
    await assert.rejects(store.load('session-1', 'profile-1', 'project-1'), /SESSION_SCHEMA_UNSUPPORTED/);
  } finally { await cleanup(); }
});

test('traversal, symlink stores, planted session symlinks and permissive permissions are denied', async () => {
  const { root, cleanup } = await fixture();
  const outside = await fixture();
  try {
    const store = new SessionStore(root);
    await assert.rejects(store.save(record({ id: '../outside' })), /SESSION_ID_INVALID/);
    await assert.rejects(store.runtimeDirectory('../outside'), /SESSION_ID_INVALID/);
    await store.save(record());
    await writeFile(join(outside.root, 'important.json'), JSON.stringify(record()), { mode: 0o600 });
    await rm(join(root, 'sessions', 'session-1.json'));
    await symlink(join(outside.root, 'important.json'), join(root, 'sessions', 'session-1.json'));
    await assert.rejects(store.load('session-1', 'profile-1', 'project-1'), /SESSION_FILE_INVALID/);
    await assert.rejects(store.save(record()), /SESSION_FILE_INVALID/);
    assert.equal(JSON.parse(await readFile(join(outside.root, 'important.json'), 'utf8')).id, 'session-1');
    await rm(join(root, 'sessions', 'session-1.json'));
    await link(join(outside.root, 'important.json'), join(root, 'sessions', 'session-1.json'));
    await assert.rejects(store.load('session-1', 'profile-1', 'project-1'), /SESSION_FILE_INVALID/);
    await assert.rejects(store.remove('session-1', 'profile-1', 'project-1'), /SESSION_FILE_INVALID/);
    await symlink(root, join(outside.root, 'linked-store'));
    await assert.rejects(new SessionStore(join(outside.root, 'linked-store')).list('profile-1', 'project-1'), /SESSION_STORE_PATH_DENIED/);
    await chmod(join(root, 'sessions'), 0o755);
    await assert.rejects(store.list('profile-1', 'project-1'), /SESSION_STORE_NOT_PRIVATE/);
  } finally { await cleanup(); await outside.cleanup(); }
});

test('exports contain public context and cannot resume or grant runtime authority', async () => {
  const { root, cleanup } = await fixture();
  try {
    const store = new SessionStore(root);
    await store.save(record({ publicContent: [{ text: 'the accepted plan and outcome', threadId: 'nested-thread' }] }));
    const exported = await store.export('session-1', 'profile-1', 'project-1');
    assert.equal(exported.workspaceLabel, 'workspace');
    assert.equal(exported.plan, 'run the focused tests');
    assert.equal(exported.kind, 'hormuz-agent-session-export');
    assert.equal(exported.workspace, undefined);
    assert.equal(exported.threadId, undefined);
    assert.equal(exported.acceptedPlanFingerprint, undefined);
    assert.equal((exported.publicContent as Record<string, unknown>[])[0]!.threadId, undefined);
  } finally { await cleanup(); }
});

test('secure deletion removes only the named transcript subtree and never follows transcript symlinks', async () => {
  const { root, cleanup } = await fixture();
  const outside = await fixture();
  try {
    const store = new SessionStore(root);
    await store.save(record());
    await store.save(record({ id: 'session-2' }));
    const one = await store.runtimeDirectory('session-1');
    const two = await store.runtimeDirectory('session-2');
    await writeFile(join(one, 'transcript.json'), 'session one');
    await writeFile(join(two, 'transcript.json'), 'session two');
    await writeFile(join(outside.root, 'keep.txt'), 'outside');
    await symlink(outside.root, join(one, 'external-link'));
    await store.remove('session-1', 'profile-1', 'project-1');
    await assert.rejects(lstat(one), { code: 'ENOENT' });
    await assert.rejects(store.load('session-1', 'profile-1', 'project-1'), { code: 'ENOENT' });
    assert.equal(await readFile(join(two, 'transcript.json'), 'utf8'), 'session two');
    assert.equal(await readFile(join(outside.root, 'keep.txt'), 'utf8'), 'outside');
    await rm(two, { recursive: true }); await symlink(outside.root, two);
    await assert.rejects(store.remove('session-2', 'profile-1', 'project-1'), /SESSION_RUNTIME_PATH_DENIED/);
    assert.equal((await store.load('session-2', 'profile-1', 'project-1')).id, 'session-2');
  } finally { await cleanup(); await outside.cleanup(); }
});

test('the private root has one live lease, exact-owner release, and dead-owner recovery', async () => {
  const { root, cleanup } = await fixture();
  try {
    const first = new SessionStore(root);
    const release = await first.acquireLease();
    await assert.rejects(new SessionStore(root).acquireLease(), /SESSION_STORE_IN_USE/);
    await release(); await release();
    const releaseAgain = await new SessionStore(root).acquireLease(); await releaseAgain();
    // macOS and Linux reject this PID with ESRCH; no signal is sent, kill(0) only checks liveness.
    const deadPid = 2147483647;
    assert.throws(() => process.kill(deadPid, 0), { code: 'ESRCH' });
    await writeFile(join(root, 'owner.lock'), JSON.stringify({ schemaVersion: 1, pid: deadPid, owner: 'dead-process' }), { mode: 0o600 });
    const recoveredRelease = await new SessionStore(root).acquireLease();
    assert.equal(JSON.parse(await readFile(join(root, 'owner.lock'), 'utf8')).pid, process.pid);
    assert.equal((await lstat(join(root, 'owner.lock'))).mode & 0o777, 0o600);
    await recoveredRelease();
    await assert.rejects(lstat(join(root, 'owner.lock')), { code: 'ENOENT' });
    await assert.rejects(lstat(join(root, 'owner.recovery')), { code: 'ENOENT' });
  } finally { await cleanup(); }
});

test('lease release refuses a replaced owner and orphaned recovery guards fail closed', async () => {
  const { root, cleanup } = await fixture();
  try {
    const release = await new SessionStore(root).acquireLease();
    await writeFile(join(root, 'owner.lock'), JSON.stringify({ schemaVersion: 1, pid: process.pid, owner: 'replacement' }), { mode: 0o600 });
    await assert.rejects(release(), /SESSION_STORE_LEASE_CHANGED/);
    assert.equal(JSON.parse(await readFile(join(root, 'owner.lock'), 'utf8')).owner, 'replacement');
    await mkdir(join(root, 'owner.recovery'), { mode: 0o700 });
    await assert.rejects(new SessionStore(root).acquireLease(), /SESSION_STORE_RECOVERY_IN_PROGRESS/);
  } finally { await cleanup(); }
});

test('concurrent dead-owner recovery yields one owner without deleting the new live lease', async () => {
  const { root, cleanup } = await fixture();
  try {
    const initialRelease = await new SessionStore(root).acquireLease(); await initialRelease();
    await writeFile(join(root, 'owner.lock'), JSON.stringify({ schemaVersion: 1, pid: 2147483647, owner: 'dead-process' }), { mode: 0o600 });
    const results = await Promise.allSettled([new SessionStore(root).acquireLease(), new SessionStore(root).acquireLease()]);
    const successful = results.filter(result => result.status === 'fulfilled');
    assert.equal(successful.length, 1);
    assert.equal(JSON.parse(await readFile(join(root, 'owner.lock'), 'utf8')).pid, process.pid);
    await (successful[0] as PromiseFulfilledResult<() => Promise<void>>).value();
    await assert.rejects(lstat(join(root, 'owner.lock')), { code: 'ENOENT' });
  } finally { await cleanup(); }
});

test('runtime metadata checks ordinary private trees, tolerate file updates, and require an existing owned root', async () => {
  const { root, cleanup } = await fixture();
  try {
    const store = new SessionStore(root);
    await assert.rejects(store.checkRuntimeTree('session-1'), /SESSION_RUNTIME_MISSING/);
    const runtime = await store.runtimeDirectory('session-1');
    await mkdir(join(runtime, 'sessions'));
    const transcript = join(runtime, 'sessions', 'thread.jsonl');
    await writeFile(transcript, 'private transcript');
    assert.deepEqual(await store.checkRuntimeTree('session-1'), { bytes: Buffer.byteLength('private transcript'), entries: 2 });
    // Live monitoring must not reject ordinary native transcript updates.
    const writing = (async () => { for (let index = 0; index < 20; index++) await writeFile(transcript, 'changed'.repeat(index + 1)); })();
    const reading = (async () => { for (let index = 0; index < 20; index++) assert.ok((await store.checkRuntimeTree('session-1')).bytes <= 140); })();
    await Promise.all([writing, reading]);
    assert.deepEqual(await store.checkRuntimeTree('session-1'), { bytes: 140, entries: 2 });
    assert.equal(await readFile(transcript, 'utf8'), 'changed'.repeat(20));
    await rename(runtime, runtime + '-owned'); await mkdir(runtime, { mode: 0o700 });
    await assert.rejects(store.checkRuntimeTree('session-1'), /SESSION_RUNTIME_CHANGED/);
    await assert.rejects(store.runtimeDirectory('session-1'), /SESSION_RUNTIME_CHANGED/);
    await chmod(join(root, 'runtime'), 0o755);
    await assert.rejects(store.checkRuntimeTree('session-1'), /SESSION_STORE_NOT_PRIVATE/);
  } finally { await cleanup(); }
});

test('runtime aggregate logical bytes count sparse transcripts without reading or allocating their contents', async () => {
  const { root, cleanup } = await fixture();
  try {
    const store = new SessionStore(root), runtime = await store.runtimeDirectory('session-1');
    const first = await open(join(runtime, 'one.jsonl'), 'w', 0o600);
    try { await first.truncate(SESSION_RUNTIME_LIMITS.maxBytes / 2); } finally { await first.close(); }
    const second = await open(join(runtime, 'two.jsonl'), 'w', 0o600);
    try { await second.truncate(SESSION_RUNTIME_LIMITS.maxBytes / 2); } finally { await second.close(); }
    assert.deepEqual(await store.checkRuntimeTree('session-1'), { bytes: SESSION_RUNTIME_LIMITS.maxBytes, entries: 2 });
    await writeFile(join(runtime, 'extra.jsonl'), 'x');
    await assert.rejects(store.checkRuntimeTree('session-1'), /SESSION_RUNTIME_BYTES_LIMIT/);
    assert.equal((await lstat(join(runtime, 'one.jsonl'))).size, SESSION_RUNTIME_LIMITS.maxBytes / 2);
  } finally { await cleanup(); }
});

test('runtime traversal enforces finite entry and depth limits', async () => {
  const { root, cleanup } = await fixture();
  try {
    const store = new SessionStore(root), runtime = await store.runtimeDirectory('session-1');
    for (let index = 0; index < SESSION_RUNTIME_LIMITS.maxEntries; index++) await writeFile(join(runtime, `${index}.jsonl`), '');
    assert.equal((await store.checkRuntimeTree('session-1')).entries, SESSION_RUNTIME_LIMITS.maxEntries);
    await writeFile(join(runtime, 'excess.jsonl'), '');
    await assert.rejects(store.checkRuntimeTree('session-1'), /SESSION_RUNTIME_ENTRIES_LIMIT/);
    const deep = await store.runtimeDirectory('session-2');
    let path = deep;
    for (let depth = 0; depth < SESSION_RUNTIME_LIMITS.maxDepth; depth++) { path = join(path, 'nested'); await mkdir(path); }
    assert.equal((await store.checkRuntimeTree('session-2')).entries, SESSION_RUNTIME_LIMITS.maxDepth);
    await mkdir(join(path, 'excess'));
    await assert.rejects(store.checkRuntimeTree('session-2'), /SESSION_RUNTIME_DEPTH_LIMIT/);
  } finally { await cleanup(); }
});

test('runtime metadata rejects symlinks, hardlinks, special files, and writable nested entries without following them', async () => {
  const { root, cleanup } = await fixture();
  try {
    const store = new SessionStore(root), runtime = await store.runtimeDirectory('session-1');
    const outside = join(root, 'private.jsonl'); await writeFile(outside, 'retain outside bytes', { mode: 0o600 });
    const planted = join(runtime, 'planted');
    await symlink(outside, planted);
    await assert.rejects(store.checkRuntimeTree('session-1'), /SESSION_RUNTIME_ENTRY_DENIED/);
    await rm(planted); await link(outside, planted);
    await assert.rejects(store.checkRuntimeTree('session-1'), /SESSION_RUNTIME_ENTRY_DENIED/);
    await rm(planted);
    await promisify(execFile)('/usr/bin/mkfifo', [planted], { timeout: 2000 });
    await assert.rejects(store.checkRuntimeTree('session-1'), /SESSION_RUNTIME_ENTRY_DENIED/);
    await rm(planted); await mkdir(planted); await chmod(planted, 0o777);
    await assert.rejects(store.checkRuntimeTree('session-1'), /SESSION_RUNTIME_NOT_PRIVATE/);
    assert.equal(await readFile(outside, 'utf8'), 'retain outside bytes');
  } finally { await cleanup(); }
});

test('Codex 0.160 history accepts only its three pinned native launcher aliases without inspecting target contents', async () => {
  const { root, cleanup } = await fixture();
  try {
    const store = new SessionStore(root), runtime = await store.runtimeDirectory('session-1');
    const executablePath = join(root, 'pinned-native-codex');
    const target = await open(executablePath, 'w', 0o600);
    // Target data is outside the runtime tree and must not contribute to usage.
    try { await target.truncate(SESSION_RUNTIME_LIMITS.maxBytes + 1); } finally { await target.close(); }
    const policy: RuntimeHistoryPolicy = { engine: 'codex', runtimeVersion: '0.160.0', executablePath };
    const launchers = join(runtime, 'tmp', 'arg0', 'codex-arg0AbC123');
    await mkdir(launchers, { recursive: true, mode: 0o700 });
    for (const name of ['apply_patch', 'applypatch', 'codex-execve-wrapper']) await symlink(executablePath, join(launchers, name));
    assert.deepEqual(await store.checkRuntimeTree('session-1', policy), { bytes: 3 * Buffer.byteLength(executablePath), entries: 6 });
    await assert.rejects(store.checkRuntimeTree('session-1'), /SESSION_RUNTIME_ENTRY_DENIED/);
    // A hardlink to the symlink also invalidates the otherwise allowed alias.
    await link(join(launchers, 'apply_patch'), join(launchers, 'extra-link'));
    await assert.rejects(store.checkRuntimeTree('session-1', policy), /SESSION_RUNTIME_ENTRY_DENIED/);
  } finally { await cleanup(); }
});

test('Codex launcher exceptions reject foreign targets, alias names, paths, versions, and noncanonical policies', async () => {
  const { root, cleanup } = await fixture();
  try {
    const store = new SessionStore(root), executablePath = join(root, 'pinned-native-codex');
    const policy: RuntimeHistoryPolicy = { engine: 'codex', runtimeVersion: '0.160.0', executablePath };
    const cases = [
      { name: 'tmp/arg0/codex-arg0abc/apply_patch', target: join(root, 'foreign-native') },
      { name: 'tmp/arg0/codex-arg0abc/shell', target: executablePath },
      { name: 'tmp/arg0/codex-arg0abc-unsafe/apply_patch', target: executablePath },
      { name: 'tmp/arg0/codex-arg0/apply_patch', target: executablePath },
      { name: 'tmp/other/codex-arg0abc/apply_patch', target: executablePath },
      { name: 'apply_patch', target: executablePath },
      { name: 'tmp/arg0/codex-arg0abc/extra/apply_patch', target: executablePath },
      { name: 'tmp/arg0/codex-arg0abc/apply_patch', target: '../../../../pinned-native-codex' },
    ];
    for (const [index, candidate] of cases.entries()) {
      const id = `candidate-${index}`, runtime = await store.runtimeDirectory(id), path = join(runtime, candidate.name);
      await mkdir(dirname(path), { recursive: true, mode: 0o700 }); await symlink(candidate.target, path);
      await assert.rejects(store.checkRuntimeTree(id, policy), /SESSION_RUNTIME_ENTRY_DENIED/);
    }
    await store.runtimeDirectory('empty');
    for (const invalid of [
      { ...policy, runtimeVersion: '0.160.1' },
      { ...policy, engine: 'claude' },
      { ...policy, executablePath: 'relative-native' },
      { ...policy, executablePath: `${root}/../pinned-native-codex` },
      { ...policy, executablePath: `${root}//pinned-native-codex` },
      { ...policy, executablePath: `${executablePath}\0` },
    ]) await assert.rejects(store.checkRuntimeTree('empty', invalid as RuntimeHistoryPolicy), /SESSION_RUNTIME_POLICY_INVALID/);
  } finally { await cleanup(); }
});
