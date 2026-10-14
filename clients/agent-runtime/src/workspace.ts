import { createHash } from 'node:crypto';
import { constants } from 'node:fs';
import { lstat, open, opendir, realpath } from 'node:fs/promises';
import { isAbsolute, join, relative, resolve, sep } from 'node:path';

export const WORKSPACE_LIMITS = Object.freeze({ maxFiles: 5000, maxFileBytes: 1024 * 1024, maxTotalBytes: 8 * 1024 * 1024, maxEntries: 15000, maxDirectories: 2000, maxDepth: 32, maxDurationMs: 10000 });
const EXCLUDED_NAMES = new Set(['.git', 'node_modules', '.hormuz-agent-runtime', '.hormuz-agent-store']);
export type WorkspaceOmission = { path: string; reason: 'excluded' | 'symlink' | 'hardlink' | 'special_file' | 'too_large' | 'total_limit' | 'file_limit' | 'scan_limit' | 'depth_limit' | 'unreadable' | 'concurrent_change' | 'binary_text' | 'sensitive_text' };
export type WorkspaceCoverage = { complete: boolean; omitted: WorkspaceOmission[] };
export type WorkspaceFile = { path: string; size: number; hash: string; text?: string; textOmitted?: 'binary' | 'sensitive' };
export type WorkspaceSnapshot = { schemaVersion: 1; root: string; fingerprint: string; revision: string; files: Record<string, WorkspaceFile>; coverage: WorkspaceCoverage; capturedAt: string };
export type WorkspaceChange = { path: string; kind: 'added' | 'modified' | 'removed'; beforeHash?: string; afterHash?: string; beforeText?: string; afterText?: string; binary: boolean; sensitive: boolean };
export type WorkspaceDiff = { root: string; baselineRevision: string; observedRevision: string; changes: WorkspaceChange[]; unavailable: { path: string; reason: string }[]; coverage: { baseline: WorkspaceCoverage; observed: WorkspaceCoverage }; authorship: 'unknown' };

function contains(root: string, path: string): boolean {
  const name = relative(root, path);
  return name === '' || (!isAbsolute(name) && name !== '..' && !name.startsWith(`..${sep}`));
}

/** Resolve inside an existing, explicit workspace; symlinks never extend its scope. */
export async function assertWorkspacePath(root: string, path: string): Promise<string> {
  const lexicalRoot = resolve(root);
  const rootInfo = await lstat(lexicalRoot);
  if (!rootInfo.isDirectory() || rootInfo.isSymbolicLink()) throw new Error('WORKSPACE_ROOT_INVALID');
  const canonicalRoot = await realpath(lexicalRoot);
  let candidate = isAbsolute(path) ? resolve(path) : resolve(canonicalRoot, path);
  if (contains(lexicalRoot, candidate)) candidate = resolve(canonicalRoot, relative(lexicalRoot, candidate));
  if (!contains(canonicalRoot, candidate)) throw new Error('WORKSPACE_PATH_ESCAPE');
  const parts = relative(canonicalRoot, candidate).split(sep).filter(Boolean);
  let cursor = canonicalRoot;
  for (let index = 0; index < parts.length; index++) {
    cursor = join(cursor, parts[index]!);
    try {
      const info = await lstat(cursor);
      if (info.isSymbolicLink()) throw new Error('WORKSPACE_SYMLINK_DENIED');
      if (index < parts.length - 1 && !info.isDirectory()) throw new Error('WORKSPACE_PARENT_INVALID');
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') break;
      throw error;
    }
  }
  return candidate;
}

function sha(value: string | Uint8Array): string { return createHash('sha256').update(value).digest('hex'); }
function sensitivePath(path: string): boolean {
  const parts = path.toLowerCase().split('/');
  const name = parts.at(-1)!;
  if (/^\.env(?:[._-]|$)/.test(name) || /\.(?:pem|key|p12|pfx|jks|keystore)$/.test(name)) return true;
  if (/^id_(?:rsa|dsa|ecdsa|ed25519)$/.test(name)) return true;
  if (['.npmrc', '.pypirc', '.netrc', '.git-credentials', '.pgpass', 'kubeconfig'].includes(name)) return true;
  if (parts.some(part => ['.aws', '.ssh', '.gnupg', '.kube'].includes(part))) return true;
  if (parts.includes('gcloud') && /credentials|tokens|config/.test(name)) return true;
  if (/^(?:credentials?|secrets?|auth|authentication|tokens?|oauth|config|settings)(?:[._-][^.]+)?\.(?:json|yaml|yml|toml|ini|conf|config|txt)$/.test(name)) return true;
  return /^(?:credentials?|secrets?|auth|authentication|tokens?|oauth)$/.test(name);
}
function sensitiveContent(text: string): boolean {
  return /\bsk-(?:ant-)?[A-Za-z0-9_-]{16,}/.test(text) || /\bBearer\s+[A-Za-z0-9._~+/-]{20,}/i.test(text) || /["'](?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret|authorization)["']\s*:\s*["'][^"'\r\n]{12,}["']/i.test(text);
}
function stableFingerprint(root: string, files: Record<string, WorkspaceFile>, coverage: WorkspaceCoverage): string {
  return sha(JSON.stringify({ root, files: Object.keys(files).sort().map(path => [path, files[path]!.hash, files[path]!.size, files[path]!.textOmitted ?? null]), coverage }));
}
function isMissing(error: unknown): boolean { return ['ENOENT', 'ENOTDIR'].includes((error as NodeJS.ErrnoException).code ?? ''); }

/** Bounded observation of tracked and untracked files; never resets a dirty checkout. */
export async function snapshotWorkspace(root: string): Promise<WorkspaceSnapshot> {
  const canonicalRoot = await assertWorkspacePath(root, '.');
  const files: Record<string, WorkspaceFile> = Object.create(null) as Record<string, WorkspaceFile>;
  const omitted: WorkspaceOmission[] = [];
  const queue: { path: string; depth: number }[] = [{ path: '', depth: 0 }];
  const started = performance.now();
  const expired = () => performance.now() - started >= WORKSPACE_LIMITS.maxDurationMs;
  let entries = 0, directories = 0, bytes = 0, fileCount = 0;
  const omit = (path: string, reason: WorkspaceOmission['reason']) => { omitted.push({ path, reason }); };
  while (queue.length > 0) {
    const item = queue.shift()!;
    if (entries >= WORKSPACE_LIMITS.maxEntries || directories >= WORKSPACE_LIMITS.maxDirectories || expired()) {
      omit(item.path, 'scan_limit');
      for (const pending of queue) omit(pending.path, 'scan_limit');
      break;
    }
    if (item.depth > WORKSPACE_LIMITS.maxDepth) { omit(item.path, 'depth_limit'); continue; }
    const fullDirectory = join(canonicalRoot, item.path);
    let names: string[] = [];
    try {
      await assertWorkspacePath(canonicalRoot, fullDirectory);
      const directory = await opendir(fullDirectory);
      directories++;
      try {
        while (entries < WORKSPACE_LIMITS.maxEntries && !expired()) {
          const entry = await directory.read();
          if (!entry) break;
          entries++;
          names.push(entry.name);
        }
        if (expired() || entries >= WORKSPACE_LIMITS.maxEntries && await directory.read()) omit(item.path, 'scan_limit');
      } finally { await directory.close(); }
    } catch (error) {
      omit(item.path, isMissing(error) ? 'concurrent_change' : 'unreadable');
      continue;
    }
    names = names.sort();
    for (const name of names) {
      if (expired()) { omit(item.path, 'scan_limit'); break; }
      const path = item.path ? `${item.path}/${name}` : name;
      if (EXCLUDED_NAMES.has(name)) { omit(path, 'excluded'); continue; }
      const full = join(canonicalRoot, path);
      try {
        const info = await lstat(full);
        if (info.isSymbolicLink()) { omit(path, 'symlink'); continue; }
        if (info.isDirectory()) { queue.push({ path, depth: item.depth + 1 }); continue; }
        if (!info.isFile()) { omit(path, 'special_file'); continue; }
        if (info.nlink > 1) { omit(path, 'hardlink'); continue; }
        if (fileCount >= WORKSPACE_LIMITS.maxFiles) { omit(path, 'file_limit'); continue; }
        if (info.size > WORKSPACE_LIMITS.maxFileBytes) { omit(path, 'too_large'); continue; }
        if (bytes + info.size > WORKSPACE_LIMITS.maxTotalBytes) { omit(path, 'total_limit'); continue; }
        // Recheck ancestors and use O_NOFOLLOW for the final component. All reads are bounded.
        await assertWorkspacePath(canonicalRoot, full);
        const handle = await open(full, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
        try {
          const before = await handle.stat();
          if (!before.isFile() || before.size !== info.size || before.ino !== info.ino || before.dev !== info.dev || before.nlink !== 1) { omit(path, before.nlink > 1 ? 'hardlink' : 'concurrent_change'); continue; }
          const buffer = Buffer.alloc(Math.min(info.size + 1, WORKSPACE_LIMITS.maxFileBytes + 1));
          let read = 0;
          while (read < buffer.length) {
            const result = await handle.read(buffer, read, buffer.length - read, read);
            if (result.bytesRead === 0) break;
            read += result.bytesRead;
          }
          const after = await handle.stat();
          if (read !== info.size || before.size !== after.size || before.mtimeMs !== after.mtimeMs || before.ctimeMs !== after.ctimeMs || after.nlink !== 1) { omit(path, 'concurrent_change'); continue; }
          const content = buffer.subarray(0, read);
          const file: WorkspaceFile = { path, size: read, hash: sha(content) };
          const decoded = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true });
          if (sensitivePath(path)) { file.textOmitted = 'sensitive'; omit(path, 'sensitive_text'); }
          else {
            try {
              if (content.includes(0)) throw new Error('binary');
              const text = decoded.decode(content);
              if (sensitiveContent(text)) { file.textOmitted = 'sensitive'; omit(path, 'sensitive_text'); }
              else file.text = text;
            } catch { file.textOmitted = 'binary'; omit(path, 'binary_text'); }
          }
          files[path] = file;
          bytes += read;
          fileCount++;
        } finally { await handle.close(); }
      } catch (error) { omit(path, isMissing(error) ? 'concurrent_change' : 'unreadable'); }
    }
  }
  omitted.sort((a, b) => a.path < b.path ? -1 : a.path > b.path ? 1 : a.reason < b.reason ? -1 : a.reason > b.reason ? 1 : 0);
  const coverage: WorkspaceCoverage = { complete: omitted.every(item => ['excluded', 'binary_text', 'sensitive_text'].includes(item.reason)), omitted };
  const fingerprint = stableFingerprint(canonicalRoot, files, coverage);
  return { schemaVersion: 1, root: canonicalRoot, fingerprint, revision: fingerprint, files, coverage, capturedAt: new Date().toISOString() };
}

function unavailable(path: string, coverage: WorkspaceCoverage): boolean {
  return coverage.omitted.some(item => !['binary_text', 'sensitive_text'].includes(item.reason) && (item.path === '' || path === item.path || path.startsWith(`${item.path}/`)));
}

export async function diffWorkspace(baseline: WorkspaceSnapshot): Promise<WorkspaceDiff> {
  const validPath = (path: string) => path !== '' && !isAbsolute(path) && !path.includes('\0') && path.split('/').every(part => part !== '' && part !== '.' && part !== '..') && contains(baseline.root, resolve(baseline.root, path));
  if (typeof baseline.root !== 'string' || !isAbsolute(baseline.root) || baseline.files === null || typeof baseline.files !== 'object' || Array.isArray(baseline.files) || !baseline.coverage || typeof baseline.coverage.complete !== 'boolean' || !Array.isArray(baseline.coverage.omitted) || Object.keys(baseline.files).length > WORKSPACE_LIMITS.maxFiles || baseline.coverage.omitted.length > WORKSPACE_LIMITS.maxEntries * 2) throw new Error('WORKSPACE_BASELINE_INVALID');
  let total = 0;
  for (const [path, file] of Object.entries(baseline.files)) {
    if (!validPath(path) || !file || file.path !== path || !Number.isSafeInteger(file.size) || file.size < 0 || file.size > WORKSPACE_LIMITS.maxFileBytes || typeof file.hash !== 'string' || !/^[a-f0-9]{64}$/.test(file.hash)) throw new Error('WORKSPACE_BASELINE_INVALID');
    if (file.textOmitted !== undefined && !['binary', 'sensitive'].includes(file.textOmitted) || file.textOmitted !== undefined && file.text !== undefined) throw new Error('WORKSPACE_BASELINE_INVALID');
    if (file.text !== undefined && (typeof file.text !== 'string' || Buffer.byteLength(file.text) !== file.size || sha(file.text) !== file.hash)) throw new Error('WORKSPACE_BASELINE_INVALID');
    total += file.size;
  }
  if (total > WORKSPACE_LIMITS.maxTotalBytes || baseline.coverage.omitted.some(item => !item || typeof item.path !== 'string' || item.path !== '' && !validPath(item.path) || !['excluded', 'symlink', 'hardlink', 'special_file', 'too_large', 'total_limit', 'file_limit', 'scan_limit', 'depth_limit', 'unreadable', 'concurrent_change', 'binary_text', 'sensitive_text'].includes(item.reason))) throw new Error('WORKSPACE_BASELINE_INVALID');
  if (baseline.schemaVersion !== 1 || baseline.fingerprint !== stableFingerprint(baseline.root, baseline.files, baseline.coverage) || baseline.revision !== baseline.fingerprint) throw new Error('WORKSPACE_BASELINE_INVALID');
  const observed = await snapshotWorkspace(baseline.root);
  const changes: WorkspaceChange[] = [];
  const missing: { path: string; reason: string }[] = [];
  const paths = new Set([...Object.keys(baseline.files), ...Object.keys(observed.files)]);
  for (const path of [...paths].sort()) {
    const before = baseline.files[path], after = observed.files[path];
    if (before?.hash === after?.hash) continue;
    if (!before && unavailable(path, baseline.coverage)) { missing.push({ path, reason: 'baseline_coverage' }); continue; }
    if (!after && unavailable(path, observed.coverage)) { missing.push({ path, reason: 'observed_coverage' }); continue; }
    const sensitive = sensitivePath(path) || before?.textOmitted === 'sensitive' || after?.textOmitted === 'sensitive';
    changes.push({ path, kind: !before ? 'added' : !after ? 'removed' : 'modified', ...(before ? { beforeHash: before.hash, ...(!sensitive && before.text !== undefined ? { beforeText: before.text } : {}) } : {}), ...(after ? { afterHash: after.hash, ...(!sensitive && after.text !== undefined ? { afterText: after.text } : {}) } : {}), binary: before?.textOmitted === 'binary' || after?.textOmitted === 'binary', sensitive });
  }
  return { root: baseline.root, baselineRevision: baseline.revision, observedRevision: observed.revision, changes, unavailable: missing, coverage: { baseline: baseline.coverage, observed: observed.coverage }, authorship: 'unknown' };
}
