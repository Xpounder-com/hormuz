import { randomUUID } from 'node:crypto';
import { constants } from 'node:fs';
import type { Stats } from 'node:fs';
import { lstat, mkdir, open, opendir, readlink, rename, rm, rmdir } from 'node:fs/promises';
import { basename, dirname, isAbsolute, join, parse, relative, resolve, sep } from 'node:path';
import type { Engine, Phase, RuntimeHistoryPolicy } from './types.ts';

export const MAX_SESSION_BYTES = 16 * 1024 * 1024;
export const SESSION_RUNTIME_LIMITS = Object.freeze({
  maxBytes: 64 * 1024 * 1024,
  maxEntries: 4096,
  maxDepth: 16,
  maxScanDurationMs: 10_000,
});
export type SessionRuntimeUsage = { bytes: number; entries: number };
const MAX_RECORD_DEPTH = 40;
const PHASES = new Set<Phase>(['idle', 'planning', 'ready', 'executing', 'completed', 'interrupted', 'failed', 'unknown']);
const FORBIDDEN_KEYS = new Set(['credential', 'credentials', 'secret', 'secrets', 'password', 'apikey', 'apikeys', 'apitoken', 'authtoken', 'bearertoken', 'accesstoken', 'refreshtoken', 'authorization', 'environment', 'env', 'headers', 'grants', 'toolgrants', 'permissions', 'runtimeauthority', 'configdir', 'historypolicy', 'runtimehistorypolicy']);
const EXPORT_ONLY_KEYS = new Set(['threadid', 'runtimeid', 'runtimepath', 'runtimedirectory', 'sessionhandle', 'workspace', 'baseline', 'acceptedplanfingerprint', 'pendingapprovals', 'approvaldecisions']);
export type SessionRecord = {
  schemaVersion: 1;
  id: string;
  profileId: string;
  projectId: string;
  workspace: string;
  engine: Engine;
  model: string;
  runtimeVersion: string;
  threadId?: string;
  phase: Phase;
  runtimeAttempts?: number;
  plan?: unknown;
  [key: string]: unknown;
};

function normalizedKey(key: string): string { return key.toLowerCase().replace(/[^a-z0-9]/g, ''); }
function identifier(value: unknown, name: string): asserts value is string {
  if (typeof value !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(value) || value === '.' || value === '..') throw new Error(`SESSION_${name}_INVALID`);
}
function scope(profileId: string, projectId: string): void {
  identifier(profileId, 'PROFILE'); identifier(projectId, 'PROJECT');
  if (/^(temporary(?:[-_.].*)?|temp|scratch|unsaved)$/i.test(projectId)) throw new Error('SESSION_NAMED_PROJECT_REQUIRED');
}
function checkJSON(value: unknown, depth = 0, location: string[] = []): void {
  if (depth > MAX_RECORD_DEPTH) throw new Error('SESSION_RECORD_TOO_DEEP');
  if (typeof value === 'string') {
    if (/\bsk-(?:ant-)?[A-Za-z0-9_-]{16,}/.test(value)) throw new Error('SESSION_SECRET_OR_GRANT_DENIED');
    return;
  }
  if (value === null || typeof value === 'boolean') return;
  if (typeof value === 'number' && Number.isFinite(value)) return;
  if (Array.isArray(value)) { for (const item of value) checkJSON(item, depth + 1, location); return; }
  if (typeof value !== 'object' || value === undefined || Object.getPrototypeOf(value) !== Object.prototype && Object.getPrototypeOf(value) !== null) throw new Error('SESSION_RECORD_INVALID');
  for (const [key, item] of Object.entries(value)) {
    const filenameMap = location.length === 2 && location[0] === 'baseline' && location[1] === 'files';
    if (filenameMap) {
      if (!item || typeof item !== 'object' || Array.isArray(item) || Object.keys(item).some(field => !['path', 'size', 'hash', 'textOmitted'].includes(field))) throw new Error('SESSION_BASELINE_CONTENT_DENIED');
    } else if (FORBIDDEN_KEYS.has(normalizedKey(key))) throw new Error('SESSION_SECRET_OR_GRANT_DENIED');
    checkJSON(item, depth + 1, [...location, key]);
  }
}
function validate(value: unknown): asserts value is SessionRecord {
  checkJSON(value);
  if (value === null || typeof value !== 'object' || Array.isArray(value)) throw new Error('SESSION_RECORD_INVALID');
  const record = value as Record<string, unknown>;
  if (record.schemaVersion !== 1) throw new Error('SESSION_SCHEMA_UNSUPPORTED');
  identifier(record.id, 'ID'); identifier(record.profileId, 'PROFILE'); identifier(record.projectId, 'PROJECT');
  scope(record.profileId, record.projectId);
  if (typeof record.workspace !== 'string' || !isAbsolute(record.workspace) || record.workspace.includes('\0')) throw new Error('SESSION_WORKSPACE_INVALID');
  if (!['codex', 'claude'].includes(record.engine as string) || typeof record.model !== 'string' || record.model.length === 0 || typeof record.runtimeVersion !== 'string' || record.runtimeVersion.length === 0 || !PHASES.has(record.phase as Phase)) throw new Error('SESSION_RECORD_INVALID');
  if (record.threadId !== undefined && (typeof record.threadId !== 'string' || record.threadId.length === 0 || record.threadId.length > 1024)) throw new Error('SESSION_THREAD_INVALID');
  if (record.accounting !== undefined) {
    if (!record.accounting || typeof record.accounting !== 'object' || Array.isArray(record.accounting)) throw new Error('SESSION_ACCOUNTING_INVALID');
    const accounting = record.accounting as Record<string, unknown>;
    if (['spentMicrousd', 'heldMicrousd', 'unknownMicrousd', 'requestCount'].some(key => !Number.isSafeInteger(accounting[key]) || (accounting[key] as number) < 0)) throw new Error('SESSION_ACCOUNTING_INVALID');
  }
  if (record.allowanceMicrousd !== undefined && (!Number.isSafeInteger(record.allowanceMicrousd) || (record.allowanceMicrousd as number) <= 0)) throw new Error('SESSION_ALLOWANCE_INVALID');
  if (record.cloudAllowed !== undefined && typeof record.cloudAllowed !== 'boolean') throw new Error('SESSION_RECORD_INVALID');
  if (record.runtimeAttempts !== undefined && (!Number.isSafeInteger(record.runtimeAttempts) || (record.runtimeAttempts as number) < 0 || (record.runtimeAttempts as number) > 128)) throw new Error('SESSION_RUNTIME_ATTEMPTS_INVALID');
  if (record.connectionId !== undefined) identifier(record.connectionId, 'CONNECTION');
}
function assertIdentity(record: SessionRecord, id: string, profileId: string, projectId: string): void {
  if (record.id !== id || record.profileId !== profileId || record.projectId !== projectId) throw new Error('SESSION_SCOPE_DENIED');
}
function missing(error: unknown): boolean { return (error as NodeJS.ErrnoException).code === 'ENOENT'; }

function runtimeEntry(info: Stats, privateRoot = false): void {
  if (info.isSymbolicLink() || !info.isDirectory() && !info.isFile() || info.isFile() && info.nlink !== 1) throw new Error('SESSION_RUNTIME_ENTRY_DENIED');
  if (info.uid !== process.getuid!() || (info.mode & 0o022) !== 0 || privateRoot && (info.mode & 0o777) !== 0o700) throw new Error('SESSION_RUNTIME_NOT_PRIVATE');
  if (!Number.isSafeInteger(info.size) || info.size < 0) throw new Error('SESSION_RUNTIME_METADATA_INVALID');
}
function sameRuntimeIdentity(first: Pick<Stats, 'dev' | 'ino'>, second: Pick<Stats, 'dev' | 'ino'>): boolean {
  return first.dev === second.dev && first.ino === second.ino;
}
function checkHistoryPolicy(policy: RuntimeHistoryPolicy): void {
  if (!policy || typeof policy !== 'object' || policy.engine !== 'codex' || policy.runtimeVersion !== '0.160.0' || typeof policy.executablePath !== 'string' || policy.executablePath.includes('\0') || policy.executablePath.length > 4096 || !isAbsolute(policy.executablePath) || resolve(policy.executablePath) !== policy.executablePath || parse(policy.executablePath).root === policy.executablePath) throw new Error('SESSION_RUNTIME_POLICY_INVALID');
  // The host adapter supplies a canonical realpath. Do not resolve or open a
  // launcher target here: this policy is ephemeral host authority, never history.
}
async function checkLauncherAlias(root: string, path: string, info: Stats, policy?: RuntimeHistoryPolicy): Promise<void> {
  const name = relative(root, path);
  if (!policy || !/^tmp\/arg0\/codex-arg0[A-Za-z0-9]+\/(?:apply_patch|applypatch|codex-execve-wrapper)$/.test(name) || info.uid !== process.getuid!() || info.nlink !== 1 || !Number.isSafeInteger(info.size) || info.size < 0) throw new Error('SESSION_RUNTIME_ENTRY_DENIED');
  // Symlink permissions are normally 0777; only the owned parent directories
  // establish privacy. Read the link text itself, never the pinned target bytes.
  if (await readlink(path) !== policy.executablePath) throw new Error('SESSION_RUNTIME_ENTRY_DENIED');
  const after = await lstat(path);
  if (!after.isSymbolicLink() || after.uid !== process.getuid!() || after.nlink !== 1 || !sameRuntimeIdentity(info, after) || after.size !== info.size || after.ctimeMs !== info.ctimeMs) throw new Error('SESSION_RUNTIME_CHANGED');
}

async function directory(path: string, create: boolean, privateMode: boolean): Promise<void> {
  let info;
  try { info = await lstat(path); }
  catch (error) {
    if (!missing(error) || !create) throw error;
    try { await mkdir(path, { mode: 0o700 }); } catch (creationError) { if ((creationError as NodeJS.ErrnoException).code !== 'EEXIST') throw creationError; }
    info = await lstat(path);
  }
  if (info.isSymbolicLink() || !info.isDirectory()) throw new Error('SESSION_STORE_PATH_DENIED');
  if (privateMode && (info.mode & 0o777) !== 0o700) throw new Error('SESSION_STORE_NOT_PRIVATE');
}

async function prepareRoot(root: string, create = true): Promise<void> {
  const parsed = parse(root);
  let current = parsed.root;
  const parts = root.slice(parsed.root.length).split(sep).filter(Boolean);
  for (const part of parts) {
    current = join(current, part);
    await directory(current, create, current === root);
  }
}

async function syncDirectory(path: string): Promise<void> {
  const handle = await open(path, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
  try { await handle.sync(); } finally { await handle.close(); }
}

async function readPrivate(path: string, maxBytes = MAX_SESSION_BYTES): Promise<string> {
  for (let attempt = 0; attempt < 3; attempt++) {
    const info = await lstat(path);
    if (!info.isFile() || info.isSymbolicLink() || (info.mode & 0o777) !== 0o600 || info.size > maxBytes || info.nlink > 1) throw new Error('SESSION_FILE_INVALID');
    if (info.nlink === 0) continue;
    const handle = await open(path, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
    try {
      const before = await handle.stat();
      // An owner's atomic rename can legitimately replace the inode between
      // lstat and open. The actual O_NOFOLLOW descriptor is the read authority.
      if (!before.isFile() || (before.mode & 0o777) !== 0o600 || before.size > maxBytes || before.nlink > 1) throw new Error('SESSION_FILE_INVALID');
      if (before.nlink === 0) continue;
      const buffer = Buffer.alloc(before.size + 1);
      let offset = 0;
      while (offset < buffer.length) {
        const read = await handle.read(buffer, offset, buffer.length - offset, offset);
        if (read.bytesRead === 0) break;
        offset += read.bytesRead;
      }
      const after = await handle.stat();
      if (offset !== before.size || after.size !== before.size || after.mtimeMs !== before.mtimeMs || (after.mode & 0o777) !== 0o600 || after.nlink > 1) throw new Error('SESSION_FILE_CHANGED');
      // Unlinking the previous immutable version changes ctime, without changing
      // content. Retry the current directory entry rather than weakening the
      // in-place content-change guard. Every attempt closes its own descriptor.
      if (after.nlink === 0) continue;
      if (after.ctimeMs !== before.ctimeMs) throw new Error('SESSION_FILE_CHANGED');
      return new TextDecoder('utf-8', { fatal: true }).decode(buffer.subarray(0, offset));
    } finally { await handle.close(); }
  }
  throw new Error('SESSION_FILE_CHANGED');
}

function stripAuthority(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stripAuthority);
  if (value !== null && typeof value === 'object') {
    const result: Record<string, unknown> = Object.create(null) as Record<string, unknown>;
    for (const [key, child] of Object.entries(value)) {
      if (!FORBIDDEN_KEYS.has(normalizedKey(key)) && !EXPORT_ONLY_KEYS.has(normalizedKey(key))) result[key] = stripAuthority(child);
    }
    return result;
  }
  return value;
}

/** Private local metadata. Provider transcripts belong only to the named runtime subtree. */
export class SessionStore {
  readonly root: string;
  #ready: Promise<void> | undefined;
  #leaseOwner: string | undefined;
  #runtimeAnchors = new Map<string, Pick<Stats, 'dev' | 'ino'>>();
  constructor(root: string) {
    if (!isAbsolute(root) || root.includes('\0') || resolve(root) === parse(resolve(root)).root) throw new Error('SESSION_STORE_ROOT_INVALID');
    this.root = resolve(root);
  }
  async #initialize(): Promise<void> {
    if (!this.#ready) this.#ready = (async () => {
      await prepareRoot(this.root);
      await directory(join(this.root, 'sessions'), true, true);
      await directory(join(this.root, 'runtime'), true, true);
    })();
    try { await this.#ready; } catch (error) { this.#ready = undefined; throw error; }
    // Revalidate on every public operation; a replaced root cannot redirect writes.
    await prepareRoot(this.root, false);
    await directory(join(this.root, 'sessions'), false, true);
    await directory(join(this.root, 'runtime'), false, true);
  }
  #path(id: string): string { identifier(id, 'ID'); return join(this.root, 'sessions', `${id}.json`); }

  async save(session: SessionRecord): Promise<void> {
    validate(session);
    const serialized = JSON.stringify(session);
    if (Buffer.byteLength(serialized) > MAX_SESSION_BYTES) throw new Error('SESSION_RECORD_TOO_LARGE');
    await this.#initialize();
    const path = this.#path(session.id);
    try { assertIdentity(await this.load(session.id, session.profileId, session.projectId), session.id, session.profileId, session.projectId); }
    catch (error) { if (!missing(error)) throw error; }
    const temporary = join(dirname(path), `.${session.id}.${randomUUID()}.tmp`);
    let handle;
    try {
      handle = await open(temporary, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | (constants.O_NOFOLLOW ?? 0), 0o600);
      await handle.writeFile(serialized, 'utf8');
      await handle.sync();
      await handle.close(); handle = undefined;
      await directory(dirname(path), false, true);
      // Do not overwrite a planted symlink or a newly introduced cross-scope record.
      try { const old = JSON.parse(await readPrivate(path)); validate(old); assertIdentity(old, session.id, session.profileId, session.projectId); }
      catch (error) { if (!missing(error)) throw error; }
      await rename(temporary, path);
      await syncDirectory(dirname(path));
    } finally { await handle?.close(); await rm(temporary, { force: true }); }
  }

  async load(id: string, profileId: string, projectId: string): Promise<SessionRecord> {
    scope(profileId, projectId); await this.#initialize();
    const record: unknown = JSON.parse(await readPrivate(this.#path(id)));
    validate(record); assertIdentity(record, id, profileId, projectId);
    return record;
  }

  async list(profileId: string, projectId: string): Promise<SessionRecord[]> {
    scope(profileId, projectId); await this.#initialize();
    const iterator = await opendir(join(this.root, 'sessions'));
    const names: string[] = [];
    try {
      for (let count = 0; count < 10000; count++) {
        const entry = await iterator.read();
        if (!entry) break;
        if (entry.name.endsWith('.json')) names.push(entry.name);
        if (count === 9999 && await iterator.read()) throw new Error('SESSION_LIST_LIMIT');
      }
    } finally { await iterator.close(); }
    const records: SessionRecord[] = [];
    for (const name of names.sort()) {
      const id = name.slice(0, -5); identifier(id, 'ID');
      const record: unknown = JSON.parse(await readPrivate(this.#path(id)));
      validate(record);
      if (record.id !== id) throw new Error('SESSION_SCOPE_DENIED');
      if (record.profileId === profileId && record.projectId === projectId) records.push(record);
    }
    return records;
  }

  async runtimeDirectory(id: string): Promise<string> {
    identifier(id, 'ID'); await this.#initialize();
    const path = join(this.root, 'runtime', id);
    await directory(path, true, true);
    const info = await lstat(path);
    const known = this.#runtimeAnchors.get(path);
    if (known && !sameRuntimeIdentity(known, info)) throw new Error('SESSION_RUNTIME_CHANGED');
    this.#runtimeAnchors.set(path, { dev: info.dev, ino: info.ino });
    return path;
  }

  /** Bounded metadata observation, without opening or reading transcript files. */
  async checkRuntimeTree(id: string, policy?: RuntimeHistoryPolicy): Promise<SessionRuntimeUsage> {
    identifier(id, 'ID');
    if (!['darwin', 'linux'].includes(process.platform) || !process.getuid) throw new Error('SESSION_RUNTIME_PLATFORM_UNSUPPORTED');
    if (policy !== undefined) checkHistoryPolicy(policy);
    await this.#initialize();
    const path = join(this.root, 'runtime', id);
    const started = performance.now();
    const usage: SessionRuntimeUsage = { bytes: 0, entries: 0 };
    const anchors: { path: string; info: Stats; privateRoot: boolean }[] = [];
    const checkDeadline = () => {
      if (performance.now() - started > SESSION_RUNTIME_LIMITS.maxScanDurationMs) throw new Error('SESSION_RUNTIME_SCAN_LIMIT');
    };
    const checkDirectory = async (anchor: { path: string; info: Stats; privateRoot: boolean }) => {
      checkDeadline();
      let current: Stats;
      try { current = await lstat(anchor.path); }
      catch (error) { if (missing(error)) throw new Error('SESSION_RUNTIME_CHANGED'); throw error; }
      runtimeEntry(current, anchor.privateRoot);
      if (!current.isDirectory() || !sameRuntimeIdentity(current, anchor.info)) throw new Error('SESSION_RUNTIME_CHANGED');
    };
    for (const anchorPath of [this.root, join(this.root, 'runtime'), path]) {
      let info: Stats;
      try { info = await lstat(anchorPath); }
      catch (error) { if (missing(error)) throw new Error('SESSION_RUNTIME_MISSING'); throw error; }
      runtimeEntry(info, true);
      if (!info.isDirectory()) throw new Error('SESSION_RUNTIME_PATH_DENIED');
      const known = this.#runtimeAnchors.get(anchorPath);
      if (known && !sameRuntimeIdentity(known, info)) throw new Error('SESSION_RUNTIME_CHANGED');
      this.#runtimeAnchors.set(anchorPath, { dev: info.dev, ino: info.ino });
      anchors.push({ path: anchorPath, info, privateRoot: true });
    }
    const visit = async (currentPath: string, depth: number, initial: Stats): Promise<void> => {
      checkDeadline();
      if (depth > SESSION_RUNTIME_LIMITS.maxDepth) throw new Error('SESSION_RUNTIME_DEPTH_LIMIT');
      const anchor = { path: currentPath, info: initial, privateRoot: depth === 0 };
      // O_NOFOLLOW and inode checks guard replaced directories. No transcript
      // descriptor is opened, including for a planted FIFO or device entry.
      const handle = await open(currentPath, constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW);
      let iterator;
      try {
        const actual = await handle.stat(); runtimeEntry(actual, anchor.privateRoot);
        if (!actual.isDirectory() || !sameRuntimeIdentity(initial, actual)) throw new Error('SESSION_RUNTIME_CHANGED');
        await checkDirectory(anchor);
        iterator = await opendir(currentPath, { bufferSize: 1 });
        await checkDirectory(anchor);
        for (;;) {
          checkDeadline();
          const entry = await iterator.read();
          if (!entry) break;
          if (++usage.entries > SESSION_RUNTIME_LIMITS.maxEntries) throw new Error('SESSION_RUNTIME_ENTRIES_LIMIT');
          if (depth + 1 > SESSION_RUNTIME_LIMITS.maxDepth) throw new Error('SESSION_RUNTIME_DEPTH_LIMIT');
          await checkDirectory(anchor);
          const entryPath = join(currentPath, entry.name);
          let info: Stats;
          try { info = await lstat(entryPath); }
          catch (error) { if (missing(error)) continue; throw error; }
          if (info.isSymbolicLink()) {
            try { await checkLauncherAlias(path, entryPath, info, policy); }
            catch (error) { if (missing(error)) continue; throw error; }
          } else runtimeEntry(info);
          if (info.isDirectory()) await visit(entryPath, depth + 1, info);
          else {
            // Logical size counts sparse files too. Content may legitimately
            // change during a live scan; the next serialized scan observes it.
            usage.bytes += info.size;
            if (usage.bytes > SESSION_RUNTIME_LIMITS.maxBytes) throw new Error('SESSION_RUNTIME_BYTES_LIMIT');
          }
        }
        await checkDirectory(anchor);
        const after = await handle.stat(); runtimeEntry(after, anchor.privateRoot);
        if (!sameRuntimeIdentity(initial, after) || after.nlink === 0) throw new Error('SESSION_RUNTIME_CHANGED');
      } finally {
        try { await iterator?.close(); } finally { await handle.close(); }
      }
    };
    try {
      await visit(path, 0, anchors[2]!.info);
      for (const anchor of anchors) await checkDirectory(anchor);
      return usage;
    } catch (error) {
      if (error instanceof Error && error.message.startsWith('SESSION_RUNTIME_')) throw error;
      throw new Error('SESSION_RUNTIME_SCAN_FAILED', { cause: error });
    }
  }

  async remove(id: string, profileId: string, projectId: string): Promise<void> {
    await this.load(id, profileId, projectId);
    const path = join(this.root, 'runtime', id);
    try {
      // rm unlinks symlinks inside this subtree; it never follows their targets.
      const info = await lstat(path);
      if (info.isSymbolicLink() || !info.isDirectory() || (info.mode & 0o777) !== 0o700) throw new Error('SESSION_RUNTIME_PATH_DENIED');
      await rm(path, { recursive: true, force: false });
    } catch (error) { if (!missing(error)) throw error; }
    await rm(this.#path(id));
    await syncDirectory(join(this.root, 'sessions'));
    await syncDirectory(join(this.root, 'runtime'));
  }

  async export(id: string, profileId: string, projectId: string): Promise<Record<string, unknown>> {
    const record = await this.load(id, profileId, projectId);
    return { ...stripAuthority(record) as Record<string, unknown>, kind: 'hormuz-agent-session-export', workspaceLabel: basename(record.workspace) };
  }

  /** One live manager per private root; only a provably dead PID's lease is recoverable. */
  async acquireLease(): Promise<() => Promise<void>> {
    await this.#initialize();
    if (this.#leaseOwner) throw new Error('SESSION_STORE_ALREADY_LEASED');
    const path = join(this.root, 'owner.lock');
    const recoveryPath = join(this.root, 'owner.recovery');
    const owner = randomUUID();
    try { await lstat(recoveryPath); throw new Error('SESSION_STORE_RECOVERY_IN_PROGRESS'); }
    catch (error) { if (!missing(error)) throw error; }
    let acquired = false;
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        const handle = await open(path, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | (constants.O_NOFOLLOW ?? 0), 0o600);
        try { await handle.writeFile(JSON.stringify({ schemaVersion: 1, pid: process.pid, owner })); await handle.sync(); } finally { await handle.close(); }
        acquired = true; break;
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== 'EEXIST' || attempt !== 0) throw error;
        // Atomic recovery guard prevents two stale-owner recoveries deleting a new live lease.
        try { await mkdir(recoveryPath, { mode: 0o700 }); }
        catch (guardError) { if ((guardError as NodeJS.ErrnoException).code === 'EEXIST') throw new Error('SESSION_STORE_RECOVERY_IN_PROGRESS'); throw guardError; }
        try {
          const contents = await readPrivate(path, 4096);
          const lock: unknown = JSON.parse(contents);
          if (!lock || typeof lock !== 'object' || !('schemaVersion' in lock) || lock.schemaVersion !== 1 || !('pid' in lock) || !Number.isSafeInteger(lock.pid) || (lock.pid as number) < 1 || !('owner' in lock) || typeof lock.owner !== 'string') throw new Error('SESSION_STORE_LEASE_INVALID');
          try { process.kill(lock.pid as number, 0); throw new Error('SESSION_STORE_IN_USE'); }
          catch (pidError) { if ((pidError as NodeJS.ErrnoException).code !== 'ESRCH') throw pidError; }
          if (await readPrivate(path, 4096) !== contents) throw new Error('SESSION_STORE_LEASE_CHANGED');
          await rm(path);
          const replacement = await open(path, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | (constants.O_NOFOLLOW ?? 0), 0o600);
          try { await replacement.writeFile(JSON.stringify({ schemaVersion: 1, pid: process.pid, owner })); await replacement.sync(); } finally { await replacement.close(); }
          acquired = true;
        } finally { await rmdir(recoveryPath); }
        if (acquired) break;
      }
    }
    if (!acquired) throw new Error('SESSION_STORE_IN_USE');
    this.#leaseOwner = owner;
    await syncDirectory(this.root);
    return async () => {
      if (this.#leaseOwner !== owner) return;
      await this.#initialize();
      const lock: unknown = JSON.parse(await readPrivate(path, 4096));
      if (!lock || typeof lock !== 'object' || !('owner' in lock) || lock.owner !== owner || !('pid' in lock) || lock.pid !== process.pid) throw new Error('SESSION_STORE_LEASE_CHANGED');
      await rm(path); await syncDirectory(this.root); this.#leaseOwner = undefined;
    };
  }
}
