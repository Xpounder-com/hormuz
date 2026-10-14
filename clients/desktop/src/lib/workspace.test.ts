import test from 'node:test';
import assert from 'node:assert/strict';
import { MockWorkspace } from './workspace.ts';
import { initialState } from './fixtures.ts';
import { activeChat, preview } from './planner.ts';
import type { Timer } from './types.ts';

function fixture() {
  let sequence = 0;
  const jobs = new Map<number, () => void>();
  const timer: Timer = {
    after: (fn) => {
      const id = ++sequence;
      jobs.set(id, fn);
      return id;
    },
    clear: (id) => {
      jobs.delete(id as number);
    },
  };
  const service = new MockWorkspace(initialState(), timer);
  const step = () => {
    const job = jobs.entries().next().value;
    if (job) {
      jobs.delete(job[0]);
      job[1]();
    }
  };
  const flush = () => {
    let count = 0;
    while (jobs.size) {
      assert.ok(count++ < 100, 'fixture work must be finite');
      step();
    }
  };
  return { service, jobs, step, flush };
}

test('typing preview is pure, proposes a downloaded writing model, and never schedules work', () => {
  const { service, jobs } = fixture();
  service.setDraft('Write a first draft');
  const before = structuredClone(service.state);
  for (let i = 0; i < 100; i++) {
    const p = preview(service.state);
    assert.equal(p.model?.id, 'gemma');
    assert.equal(p.preparation, true);
  }
  assert.deepEqual(service.state, before);
  assert.equal(jobs.size, 0);
});

test('connection does not grant cloud permission; explicit consent permits research Auto', () => {
  const { service, flush } = fixture();
  service.configureConnection();
  service.testConnection();
  flush();
  service.setDraft('Compare evidence for my research');
  assert.equal(preview(service.state).model?.place, 'local');
  service.setCloudAllowed(true);
  assert.equal(preview(service.state).model?.id, 'claude');
  service.setLocalRestriction(true);
  assert.equal(preview(service.state).model?.place, 'local');
});

test('blocked cloud pin stays pinned, with explicit Auto recovery', () => {
  const { service } = fixture();
  service.pin('claude');
  service.setDraft('Research');
  const p = preview(service.state);
  assert.equal(p.model?.id, 'claude');
  assert.equal(p.blocked, true);
  service.send();
  assert.equal(service.state.attempt, null);
  assert.equal(activeChat(service.state).intent, 'claude');
  service.pin('auto');
  assert.equal(preview(service.state).blocked, false);
});

test('project membership does not select files; a local-only selected copy blocks a cloud pin', () => {
  const { service } = fixture();
  service.setCloudAllowed(true);
  service.state.connection.status = 'verified';
  service.addSampleFile('notes');
  assert.deepEqual(activeChat(service.state).selectedFiles, []);
  service.toggleFile('notes');
  service.pin('claude');
  assert.equal(preview(service.state).blocked, true);
  service.toggleFile('notes');
  assert.equal(preview(service.state).blocked, false);
});

test('context and fictional allowance constraints are applied before ranking and to pins', () => {
  const { service } = fixture();
  service.setCloudAllowed(true);
  service.state.connection.status = 'verified';
  service.pin('claude');
  service.setCap(0);
  assert.equal(preview(service.state).blocked, true);
  service.setCap(10);
  service.state.profileUsed = 24.95;
  assert.equal(preview(service.state).blocked, true);
  service.state.profileUsed = 0;
  service.setDraft('x'.repeat(30000));
  assert.equal(preview(service.state).blocked, true);
});

test('each destination owns its draft and intent across bar/workspace navigation', () => {
  const { service } = fixture();
  const id = service.state.activeChatId;
  service.setDraft('Origin draft');
  service.pin('gemma');
  service.selectProject('launch');
  service.setDraft('Other draft');
  service.navigate(id);
  assert.equal(activeChat(service.state).draft, 'Origin draft');
  assert.equal(activeChat(service.state).intent, 'gemma');
});

test('load on Send reaches Ready before response and allows only one active attempt', () => {
  const { service, step, flush } = fixture();
  service.setDraft('Write my introduction');
  service.send();
  assert.equal(service.state.attempt?.phase, 'preparing');
  assert.equal(service.state.models.find((m) => m.id === 'gemma')?.state, 'loading');
  const first = service.state.attempt?.id;
  service.setDraft('second');
  service.send();
  assert.equal(service.state.attempt?.id, first);
  step();
  assert.equal(service.state.attempt?.phase, 'running');
  assert.equal(
    service.state.models.filter((m) => m.place === 'local' && m.state === 'ready').length,
    1,
  );
  flush();
  assert.equal(service.state.attempt, null);
  assert.equal(activeChat(service.state).messages.at(-1)?.receipt?.status, 'completed');
});

test('changed restrictions during preparation refuse dispatch without hidden fallback', () => {
  const { service, flush } = fixture();
  service.setDraft('Write a draft');
  service.send();
  service.setProjectInstructions('New instructions after Send');
  flush();
  assert.equal(service.state.attempt, null);
  assert.equal(activeChat(service.state).messages.at(-1)?.receipt?.status, 'blocked');
  assert.equal(service.state.profileUsed, 0);
});

test('streaming results retain frozen origin and selected context while navigating and editing', () => {
  const { service, flush } = fixture();
  service.toggleFile('brief');
  service.setDraft('Compare research');
  service.send();
  const origin = activeChat(service.state);
  service.removeFile('brief');
  service.setProjectInstructions('Changed');
  service.selectProject('launch');
  flush();
  assert.equal(activeChat(service.state).messages.length, 0);
  const receipt = origin.messages.at(-1)?.receipt!;
  assert.equal(receipt.origin, 'Research notes');
  assert.deepEqual(receipt.context, ['Research brief.md · v1']);
  assert.ok(!receipt.instructions.includes('Changed'));
});

test('stop keeps partial output and unknown cloud holds; no delayed callback resumes work', () => {
  const { service, step, flush } = fixture();
  service.state.connection.status = 'verified';
  service.setCloudAllowed(true);
  service.setDraft('Research evidence');
  service.send();
  step();
  const chat = activeChat(service.state);
  const text = chat.messages.at(-1)?.text;
  assert.ok(text);
  service.stop();
  flush();
  assert.equal(chat.messages.at(-1)?.text, text);
  assert.equal(chat.messages.at(-1)?.receipt?.status, 'canceled');
  assert.equal(service.state.profileUsed, 0.12);
});

test('Temporary consent is independent, and discard cannot refund unknown profile holds', () => {
  const { service, flush } = fixture();
  service.setCloudAllowed(true);
  service.state.connection.status = 'verified';
  service.newChat(null);
  assert.equal(preview(service.state).cloudAllowed, false);
  service.setCloudAllowed(true);
  service.setDraft('Research');
  service.send();
  service.stop();
  const temporary = activeChat(service.state).id;
  service.deleteChat(temporary);
  service.newChat(null);
  flush();
  assert.equal(preview(service.state).cloudAllowed, false);
  assert.equal(service.state.profileUsed, 0.12);
  assert.ok(!service.state.chats.some((c) => c.id === temporary));
});

test('saving Temporary adopts destination defaults and preserves earlier receipts', () => {
  const { service, flush } = fixture();
  service.newChat(null);
  service.setCloudAllowed(true);
  service.pin('gemma');
  service.setDraft('Write a draft');
  service.send();
  flush();
  service.setDraft('Continue this draft');
  service.saveTemporary('launch');
  const saved = activeChat(service.state);
  assert.equal(saved.projectId, 'launch');
  assert.equal(saved.intent, 'auto');
  assert.equal(saved.draft, 'Continue this draft');
  assert.equal(preview(service.state).cloudAllowed, false);
  assert.equal(saved.messages.at(-1)?.receipt?.origin, 'Temporary chat');
});

test('busy deletion is blocked, then chat deletion preserves project copies and accounting', () => {
  const { service, flush } = fixture();
  const id = service.state.activeChatId;
  service.setDraft('Research');
  service.send();
  service.deleteProject('research');
  assert.ok(service.state.projects.some((p) => p.id === 'research'));
  service.deleteChat(id);
  assert.ok(service.state.chats.some((c) => c.id === id));
  service.stop();
  flush();
  service.deleteChat(id);
  assert.ok(
    service.state.projects.find((p) => p.id === 'research')?.files.some((f) => f.id === 'brief'),
  );
});

test('setup cancellation and disposal clear owned bounded work without a ready shortcut', () => {
  const { service, jobs, flush } = fixture();
  service.pin('mistral');
  service.downloadModel('mistral');
  service.cancelSetup('mistral');
  flush();
  assert.equal(service.state.models.find((m) => m.id === 'mistral')?.state, 'offered');
  assert.equal(preview(service.state).blocked, true);
  service.downloadModel('mistral');
  flush();
  assert.equal(service.state.models.find((m) => m.id === 'mistral')?.state, 'downloaded');
  service.loadModel('mistral');
  service.testConnection();
  assert.ok(jobs.size);
  service.dispose();
  assert.equal(jobs.size, 0);
});

test('an invalid connection can be retried explicitly; removal invalidates pending test', () => {
  const { service, flush } = fixture();
  service.configureConnection();
  service.setConnectionScenario('invalid');
  service.testConnection();
  flush();
  assert.equal(service.state.connection.status, 'invalid');
  service.setConnectionScenario('success');
  service.testConnection();
  service.removeConnection();
  flush();
  assert.equal(service.state.connection.status, 'absent');
});

test('a busy cloud connection cannot be retested, edited or removed until Stop', () => {
  const { service, flush } = fixture();
  service.state.connection.status = 'verified';
  service.setCloudAllowed(true);
  service.setDraft('Compare research evidence');
  service.send();
  service.testConnection();
  service.setConnectionScenario('offline');
  service.removeConnection();
  assert.equal(service.state.connection.status, 'verified');
  assert.equal(service.state.connection.scenario, 'success');
  service.stop();
  flush();
  service.removeConnection();
  assert.equal(service.state.connection.status, 'absent');
});
