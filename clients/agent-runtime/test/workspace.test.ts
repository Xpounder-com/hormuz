import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, rm, symlink, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import test from 'node:test';
import { assertWorkspacePath, diffWorkspace, snapshotWorkspace, WORKSPACE_LIMITS } from '../src/workspace.ts';

async function fixture(): Promise<{ root: string; cleanup: () => Promise<void> }> {
  const root = await mkdtemp('/private/tmp/hormuz-workspace-test-');
  return { root, cleanup: () => rm(root, { recursive: true, force: true }) };
}

test('snapshots include dirty and untracked work, exclude runtime directories, and are deterministic', async () => {
  const { root, cleanup } = await fixture();
  try {
    await mkdir(join(root, 'src'));
    await writeFile(join(root, 'src', 'already-dirty.txt'), 'a user change made before the session\n');
    await writeFile(join(root, 'untracked.txt'), 'untracked\n');
    await writeFile(join(root, 'bom.txt'), '\ufeffUTF8 BOM\n');
    for (const name of ['.git', 'node_modules', '.hormuz-agent-runtime', '.hormuz-agent-store']) {
      await mkdir(join(root, name)); await writeFile(join(root, name, 'private.txt'), 'excluded');
    }
    const baseline = await snapshotWorkspace(root);
    const next = await snapshotWorkspace(root);
    assert.equal(baseline.fingerprint, next.fingerprint);
    assert.equal(baseline.files['src/already-dirty.txt']?.text, 'a user change made before the session\n');
    assert.deepEqual(Object.keys(baseline.files).sort(), ['bom.txt', 'src/already-dirty.txt', 'untracked.txt']);
    assert.equal(baseline.files['bom.txt']?.text, '\ufeffUTF8 BOM\n');
    assert.equal(baseline.coverage.complete, true);
    assert.equal(baseline.coverage.omitted.filter(item => item.reason === 'excluded').length, 4);
    assert.equal((await diffWorkspace(baseline)).changes.length, 0);
    assert.equal(await readFile(join(root, 'src', 'already-dirty.txt'), 'utf8'), 'a user change made before the session\n');
  } finally { await cleanup(); }
});

test('workspace scope denies absolute escapes, traversal, and symlink files or parent directories', async () => {
  const { root, cleanup } = await fixture();
  const outside = await fixture();
  try {
    await writeFile(join(outside.root, 'secret.txt'), 'outside');
    await symlink(outside.root, join(root, 'linked-directory'));
    await symlink(join(outside.root, 'secret.txt'), join(root, 'linked-file'));
    await assert.rejects(assertWorkspacePath(root, '../escape.txt'), /WORKSPACE_PATH_ESCAPE/);
    await assert.rejects(assertWorkspacePath(root, join(outside.root, 'secret.txt')), /WORKSPACE_PATH_ESCAPE/);
    await assert.rejects(assertWorkspacePath(root, 'linked-directory/secret.txt'), /WORKSPACE_SYMLINK_DENIED/);
    await assert.rejects(assertWorkspacePath(root, 'linked-file'), /WORKSPACE_SYMLINK_DENIED/);
    assert.equal(await assertWorkspacePath(root, 'new/future.txt'), join(root, 'new/future.txt'));
    const snapshot = await snapshotWorkspace(root);
    assert.deepEqual(Object.keys(snapshot.files), []);
    assert.equal(snapshot.coverage.complete, false);
    assert.equal(snapshot.coverage.omitted.filter(item => item.reason === 'symlink').length, 2);
    assert.equal(await readFile(join(outside.root, 'secret.txt'), 'utf8'), 'outside');
  } finally { await cleanup(); await outside.cleanup(); }
});

test('diff reports observed revisions and keeps concurrent user changes unattributed', async () => {
  const { root, cleanup } = await fixture();
  try {
    await writeFile(join(root, 'user-dirty.txt'), 'uncommitted user content');
    await writeFile(join(root, 'edited.txt'), 'before\n');
    await writeFile(join(root, 'deleted.txt'), 'remove me\n');
    const baseline = await snapshotWorkspace(root);
    await writeFile(join(root, 'edited.txt'), 'agent or concurrent editor changed this\n');
    await writeFile(join(root, 'added.txt'), 'new\n');
    await rm(join(root, 'deleted.txt'));
    const diff = await diffWorkspace(baseline);
    assert.equal(diff.authorship, 'unknown');
    assert.equal(diff.baselineRevision, baseline.revision);
    assert.notEqual(diff.observedRevision, baseline.revision);
    assert.deepEqual(diff.changes.map(item => [item.path, item.kind]), [['added.txt', 'added'], ['deleted.txt', 'removed'], ['edited.txt', 'modified']]);
    assert.equal(diff.changes[2]!.beforeText, 'before\n');
    assert.equal(diff.changes[2]!.afterText, 'agent or concurrent editor changed this\n');
    assert.equal(await readFile(join(root, 'user-dirty.txt'), 'utf8'), 'uncommitted user content');
    await assert.rejects(diffWorkspace({ ...baseline, root: join(root, 'other') }), /WORKSPACE_BASELINE_INVALID/);
    const edited = baseline.files['edited.txt']!;
    await assert.rejects(diffWorkspace({ ...baseline, files: { ...baseline.files, 'edited.txt': { ...edited, text: 'faked baseline\n' } } }), /WORKSPACE_BASELINE_INVALID/);
  } finally { await cleanup(); }
});

test('omitted files are unavailable, never falsely reported as deleted or added', async () => {
  const { root, cleanup } = await fixture();
  try {
    await writeFile(join(root, 'normal.txt'), 'small');
    await writeFile(join(root, 'oversized.txt'), Buffer.alloc(WORKSPACE_LIMITS.maxFileBytes + 1));
    const baseline = await snapshotWorkspace(root);
    assert.equal(baseline.coverage.complete, false);
    assert.equal(baseline.coverage.omitted.find(item => item.path === 'oversized.txt')?.reason, 'too_large');
    await rm(join(root, 'normal.txt'));
    await symlink('/etc/hosts', join(root, 'normal.txt'));
    await writeFile(join(root, 'oversized.txt'), 'became small');
    const diff = await diffWorkspace(baseline);
    assert.deepEqual(diff.changes, []);
    assert.deepEqual(diff.unavailable, [{ path: 'normal.txt', reason: 'observed_coverage' }, { path: 'oversized.txt', reason: 'baseline_coverage' }]);
  } finally { await cleanup(); }
});

test('binary content has hashes with omitted text and total captured bytes stay bounded', async () => {
  const { root, cleanup } = await fixture();
  try {
    await writeFile(join(root, '00-binary.dat'), Buffer.from([0, 255, 1, 2]));
    for (let index = 0; index < 9; index++) await writeFile(join(root, `${index + 1}.txt`), Buffer.alloc(WORKSPACE_LIMITS.maxFileBytes, 65));
    const baseline = await snapshotWorkspace(root);
    assert.equal(baseline.files['00-binary.dat']?.text, undefined);
    assert.equal(baseline.files['00-binary.dat']?.hash.length, 64);
    assert.equal(baseline.coverage.omitted.some(item => item.reason === 'binary_text'), true);
    assert.equal(baseline.coverage.omitted.some(item => item.reason === 'total_limit'), true);
    assert.ok(Object.values(baseline.files).reduce((total, item) => total + item.size, 0) <= WORKSPACE_LIMITS.maxTotalBytes);
    await writeFile(join(root, '00-binary.dat'), Buffer.from([0, 255, 9, 2]));
    const diff = await diffWorkspace(baseline);
    assert.equal(diff.changes[0]?.path, '00-binary.dat');
    assert.equal(diff.changes[0]?.binary, true);
  } finally { await cleanup(); }
});

test('sensitive names and obvious inline credentials have hashes but no snapshot or diff text', async () => {
  const { root, cleanup } = await fixture();
  try {
    for (const name of ['.env', '.env.production', 'private.pem', 'signing.key', 'credentials.json', 'auth.json', 'config.toml']) await writeFile(join(root, name), 'a private value that must remain local');
    await writeFile(join(root, 'normal.ts'), 'export const value = 1;\n');
    await writeFile(join(root, 'inline.ts'), 'const token = "sk-ant-api03-aVeryLongSecretThatMustNotLeak";\n');
    const baseline = await snapshotWorkspace(root);
    assert.equal(baseline.coverage.complete, true);
    assert.equal(baseline.files['normal.ts']?.text, 'export const value = 1;\n');
    for (const path of Object.keys(baseline.files).filter(path => path !== 'normal.ts')) {
      assert.equal(baseline.files[path]?.text, undefined);
      assert.equal(baseline.files[path]?.textOmitted, 'sensitive');
      assert.equal(baseline.files[path]?.hash.length, 64);
    }
    await writeFile(join(root, '.env'), 'changed local credential');
    await writeFile(join(root, 'normal.ts'), 'export const value = 2;\n');
    // Restarted baselines retain revisions while omitting all source text at rest.
    const hashOnly = structuredClone(baseline);
    for (const file of Object.values(hashOnly.files)) delete file.text;
    const diff = await diffWorkspace(hashOnly);
    const sensitive = diff.changes.find(change => change.path === '.env')!;
    assert.equal(sensitive.sensitive, true);
    assert.equal(sensitive.beforeText, undefined);
    assert.equal(sensitive.afterText, undefined);
    assert.notEqual(sensitive.beforeHash, sensitive.afterHash);
    const ordinary = diff.changes.find(change => change.path === 'normal.ts')!;
    assert.equal(ordinary.sensitive, false);
    assert.equal(ordinary.binary, false);
    assert.equal(ordinary.afterText, 'export const value = 2;\n');
  } finally { await cleanup(); }
});

test('file count limit provides honest partial coverage without removing untouched files', async () => {
  const { root, cleanup } = await fixture();
  try {
    for (let index = 0; index <= WORKSPACE_LIMITS.maxFiles; index++) await writeFile(join(root, `file-${String(index).padStart(5, '0')}`), 'x');
    const baseline = await snapshotWorkspace(root);
    assert.equal(Object.keys(baseline.files).length, WORKSPACE_LIMITS.maxFiles);
    assert.equal(baseline.coverage.omitted.at(-1)?.reason, 'file_limit');
    assert.equal((await diffWorkspace(baseline)).changes.length, 0);
    assert.equal(await readFile(join(root, `file-${String(WORKSPACE_LIMITS.maxFiles).padStart(5, '0')}`), 'utf8'), 'x');
  } finally { await cleanup(); }
});
