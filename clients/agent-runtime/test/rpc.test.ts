import test from 'node:test';
import assert from 'node:assert/strict';
import { RpcProcess } from '../src/rpc.ts';

const permissionDenied = () => Object.assign(new Error('synthetic group signal denial'), { code: 'EPERM' });
const actualSignal = process.kill.bind(process);
const fixture = `process.stdin.setEncoding('utf8'); process.stdin.on('data', data => { for (const line of data.trim().split('\\n')) { const request = JSON.parse(line); process.stdout.write(JSON.stringify({ id: request.id, result: {} }) + '\\n'); } }); process.on('SIGTERM', () => process.exit(0)); setInterval(() => {}, 1000);`;
function spawn(signalProcess: (pid: number, signal: NodeJS.Signals | 0) => boolean) {
  return new RpcProcess({ command: process.execPath, args: ['-e', fixture], cwd: process.cwd(), env: {}, shutdownTimeoutMs: 200, requestTimeoutMs: 1000,
    signalProcess, onNotification: () => {}, onRequest: async () => ({}), onFailure: () => {} });
}

for (const phase of ['SIGTERM', 'SIGKILL'] as const) test(`EPERM after ${phase} is benign only after the owned root is reaped and the group is proven absent`, async () => {
  let rootPid = 0, probes = 0, denied = false;
  const rpc = spawn((pid, signal) => {
    rootPid = -pid;
    if (signal === 0) {
      probes++;
      assert.throws(() => actualSignal(rootPid, 0), { code: 'ESRCH' }, 'root must already have been reaped');
      return actualSignal(pid, 0);
    }
    if (signal === phase && !denied) {
      denied = true;
      if (phase === 'SIGTERM') actualSignal(pid, signal);
      throw permissionDenied();
    }
    return actualSignal(pid, signal);
  });
  try {
    await rpc.request('ready', {}); await rpc.dispose();
    assert.equal(denied, true); assert.ok(probes >= 1 && probes <= 3);
    assert.throws(() => actualSignal(rootPid, 0), { code: 'ESRCH' });
  } finally { await rpc.dispose(); }
});

for (const unknown of ['positive', 'EPERM'] as const) test(`a reaped root with ${unknown} group-presence probes retains failed cleanup for retry`, async () => {
  let simulate = true, probes = 0;
  const rpc = spawn((pid, signal) => {
    if (simulate && signal === 'SIGKILL') throw permissionDenied();
    if (simulate && signal === 0) { probes++; if (unknown === 'EPERM') throw permissionDenied(); return true; }
    return actualSignal(pid, signal);
  });
  try {
    await rpc.request('ready', {});
    await assert.rejects(rpc.dispose(), { code: 'runtime_cleanup_required' }); assert.equal(probes, 3);
    simulate = false; await rpc.dispose();
  } finally { simulate = false; await rpc.dispose(); }
});

test('EPERM while the owned root remains alive cannot be treated as completed cleanup', async () => {
  let simulate = true, rootPid = 0, probes = 0;
  const rpc = spawn((pid, signal) => {
    rootPid = -pid;
    if (simulate && signal === 'SIGTERM') throw permissionDenied();
    if (signal === 0) probes++;
    return actualSignal(pid, signal);
  });
  try {
    await rpc.request('ready', {});
    await assert.rejects(rpc.dispose(), { code: 'runtime_cleanup_required' });
    assert.equal(actualSignal(rootPid, 0), true); assert.equal(probes, 0);
    simulate = false; await rpc.dispose();
    assert.throws(() => actualSignal(rootPid, 0), { code: 'ESRCH' });
  } finally { simulate = false; await rpc.dispose(); }
});
