import { createInterface } from 'node:readline';
import { appendFileSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { spawn } from 'node:child_process';

const scenario = process.argv.find(value => value.startsWith('--scenario='))?.slice(11) ?? 'normal';
const trace = join(process.env.CODEX_HOME, 'fixture-trace.jsonl');
const record = value => appendFileSync(trace, JSON.stringify(value) + '\n');
const send = value => process.stdout.write(JSON.stringify(value) + '\n');
const notify = (method, params) => send({ method, params });
const threadId = 'thread-1';
let turnIndex = 0;
const callbacks = new Map();
record({ envKeys: Object.keys(process.env), config: readFileSync(join(process.env.CODEX_HOME, 'config.toml'), 'utf8') });
writeFileSync(join(process.env.CODEX_HOME, 'fixture-root.pid'), String(process.pid));
if (scenario === 'stubborn') process.on('SIGTERM', () => {});
const child = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { stdio: 'ignore' });
writeFileSync(join(process.env.CODEX_HOME, 'fixture-child.pid'), String(child.pid));

function complete(turnId, status = 'completed') {
  notify('turn/completed', { threadId, turn: { id: turnId, status } });
}
function request(method, params, id, next) {
  callbacks.set(id, next);
  send({ id, method, params });
}

const lines = createInterface({ input: process.stdin });
lines.on('line', line => {
  const message = JSON.parse(line);
  record(message);
  if (!message.method) {
    const next = callbacks.get(message.id); callbacks.delete(message.id); next?.(message); return;
  }
  if (message.method === 'initialize') {
    if (scenario === 'init-timeout') return;
    send({ id: message.id, result: { userAgent: `hormuz/${scenario === 'version' ? '0.159.0' : '0.160.0'} (fixture)`, codexHome: process.env.CODEX_HOME, platformFamily: 'unix', platformOs: 'macos' } });
  } else if (message.method === 'initialized') { /* notification */ }
  else if (message.method === 'thread/start' || message.method === 'thread/resume') {
    send({ id: message.id, result: { thread: { id: threadId, cwd: message.params.cwd } } });
  } else if (message.method === 'turn/start') {
    if (message.params.sandboxPolicy.type !== 'readOnly' || message.params.sandboxPolicy.networkAccess !== false) process.exit(20);
    const turnId = `turn-${++turnIndex}`;
    const p = { threadId, turnId };
    notify('turn/started', { threadId, turn: { id: turnId, status: 'inProgress' } });
    send({ id: message.id, result: { turn: { id: turnId, status: 'inProgress' } } });
    if (scenario === 'hang') return;
    if (scenario === 'crash') {
      // Process-group ancestry restrictions in the command test sandbox prohibit signalling
      // an orphan after the leader exits. Other failure cases test supervisor-owned cleanup.
      child.once('exit', () => process.exit(9)); child.kill('SIGTERM'); return;
    }
    if (scenario === 'huge') { process.stdout.write('x'.repeat(100_000)); return; }
    if (scenario === 'foreign') {
      request('item/commandExecution/requestApproval', { ...p, threadId: 'other', itemId: 'forged', command: 'true', cwd: message.params.cwd }, 100, response => {
        if (!response.error) process.exit(12);
      });
      request('item/commandExecution/requestApproval', { ...p, turnId: 'stale', itemId: 'forged', command: 'true', cwd: message.params.cwd }, 101, response => {
        if (!response.error) process.exit(13);
      });
      request('attestation/generate', { ...p }, 102, response => {
        if (response.error?.code !== -32601) process.exit(14);
      });
      notify('item/agentMessage/delta', { threadId: 'other', turnId, delta: 'FOREIGN' });
      notify('turn/completed', { threadId, turn: { id: 'stale', status: 'completed' } });
    }
    if (message.params.collaborationMode.mode === 'plan') {
      const finishPlan = () => {
        notify('item/plan/delta', { ...p, itemId: 'plan-1', delta: 'Inspect the project.\n' });
        notify('turn/plan/updated', { ...p, explanation: 'Proposed work', plan: [{ step: 'Inspect the project', status: 'pending' }] });
        notify('item/completed', { ...p, item: { type: 'plan', id: 'plan-1', text: 'Inspect the project.\nChange one file and validate it.' } });
        complete(turnId);
      };
      if (scenario === 'plan-approval') request('item/commandExecution/requestApproval', { ...p, itemId: 'write-1', command: 'touch bad', cwd: message.params.cwd }, 110, result => {
        if (result.result?.decision !== 'decline') process.exit(15); else finishPlan();
      });
      else if (scenario === 'question') request('item/tool/requestUserInput', { ...p, itemId: 'q-1', questions: [{ id: 'choice', header: 'Scope', question: 'Which scope?', options: null }] }, 111, result => {
        if (result.result?.answers?.choice?.answers?.[0] !== 'Small') process.exit(16); else finishPlan();
      });
      else finishPlan();
    } else {
      request('item/commandExecution/requestApproval', { ...p, itemId: 'cmd-1', command: 'printf test', cwd: message.params.cwd, kind: 'command' }, 120 + turnIndex * 10, result => {
        if (result.result?.decision !== 'accept') { complete(turnId); return; }
        notify('item/commandExecution/outputDelta', { ...p, itemId: 'cmd-1', delta: 'test' });
        const item = { type: 'fileChange', id: 'file-1', changes: [{ path: join(message.params.cwd, 'example.txt'), kind: { type: 'add' }, diff: '+changed' }] };
        notify('item/started', { ...p, item });
        request('item/fileChange/requestApproval', { ...p, itemId: 'file-1' }, 121 + turnIndex * 10, response => {
          if (response.result?.decision === 'accept') {
            writeFileSync(join(message.params.cwd, 'example.txt'), 'changed');
            notify('turn/diff/updated', { ...p, diff: '+changed' });
            notify('item/agentMessage/delta', { ...p, itemId: 'answer-1', delta: 'Done' });
            notify('thread/tokenUsage/updated', { ...p, tokenUsage: { total: { inputTokens: 10, outputTokens: 3 } } });
          }
          complete(turnId);
        });
      });
    }
  } else if (message.method === 'turn/interrupt') {
    send({ id: message.id, result: {} }); complete(message.params.turnId, 'interrupted');
  } else send({ id: message.id, error: { code: -32601, message: 'Unknown method' } });
});
// Deliberately leave an owned worker after normal EOF; the supervisor must reap its group.
lines.on('close', () => { if (scenario !== 'stubborn') process.exit(0); else setInterval(() => {}, 1000); });
