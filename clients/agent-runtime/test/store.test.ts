import assert from 'node:assert/strict';
import { chmod, link, lstat, mkdir, mkdtemp, readFile, readdir, realpath, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { MAX_SESSION_BYTES, SessionStore } from '../src/store.ts';
import type { SessionRecord } from '../src/store.ts';

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
    await assert.rejects(store.save(record({ publicContent: 'sk-ant-api03-aVeryLongSecretThatMustNotBeSaved' })), /SESSION_SECRET_OR_GRANT_DENIED/);
    await assert.rejects(store.save(record({ schemaVersion: 2 as 1 })), /SESSION_SCHEMA_UNSUPPORTED/);
    await assert.rejects(store.save(record({ projectId: 'temporary' })), /SESSION_NAMED_PROJECT_REQUIRED/);
    await assert.rejects(store.save(record({ projectId: 'temporary-123' })), /SESSION_NAMED_PROJECT_REQUIRED/);
    await assert.rejects(store.save(record({ accounting: { spentMicrousd: Infinity } })), /SESSION_RECORD_INVALID/);
    await assert.rejects(store.save(record({ accounting: { spentMicrousd: '0', heldMicrousd: 0, unknownMicrousd: 0, requestCount: 1 } })), /SESSION_ACCOUNTING_INVALID/);
    await assert.rejects(store.save(record({ allowanceMicrousd: -1 })), /SESSION_ALLOWANCE_INVALID/);
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
