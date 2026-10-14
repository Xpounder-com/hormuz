import { snapshotWorkspace, diffWorkspace } from './workspace.ts';
import type { WorkspaceSnapshot } from './workspace.ts';

const workspace = process.argv[2];
const MAX_FRAME_BYTES = 64 * 1024 * 1024;
let buffer = Buffer.alloc(0), used = false;

async function request(frame: unknown): Promise<void> {
  if (!frame || typeof frame !== 'object' || Array.isArray(frame)) { process.exitCode = 2; return; }
  const record = frame as Record<string, unknown>;
  if (!Number.isSafeInteger(record.id) || !record.params || typeof record.params !== 'object' || Array.isArray(record.params)) { process.exitCode = 2; return; }
  const params = record.params as Record<string, unknown>;
  try {
    let result;
    if (record.method === 'snapshot' && Object.keys(params).length === 0) result = await snapshotWorkspace(workspace!);
    else if (record.method === 'diff' && Object.keys(params).length === 1 && params.baseline && typeof params.baseline === 'object' && (params.baseline as WorkspaceSnapshot).root === workspace) result = await diffWorkspace(params.baseline as WorkspaceSnapshot);
    else throw new Error('invalid-request');
    const output = Buffer.from(JSON.stringify({ id: record.id, result }) + '\n');
    if (output.length > MAX_FRAME_BYTES) throw new Error('frame-limit');
    process.stdout.write(output);
  } catch { process.stdout.write(JSON.stringify({ id: record.id, error: { code: -32602, message: 'Workspace observation failed' } }) + '\n'); }
}

process.stdin.on('data', (chunk: Buffer) => {
  if (used) return;
  if (buffer.length + chunk.length > MAX_FRAME_BYTES) { used = true; buffer = Buffer.alloc(0); process.exitCode = 2; process.stdin.pause(); return; }
  buffer = Buffer.concat([buffer, chunk]);
  const newline = buffer.indexOf(10);
  if (newline < 0) return;
  used = true; process.stdin.pause();
  let frame;
  try { frame = JSON.parse(buffer.subarray(0, newline).toString('utf8')); }
  catch { process.exitCode = 2; return; }
  buffer = Buffer.alloc(0);
  void request(frame);
});
