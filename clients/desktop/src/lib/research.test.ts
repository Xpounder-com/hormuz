import test from 'node:test';
import assert from 'node:assert/strict';
import { MockWorkspace } from './workspace.ts';
import { initialState } from './fixtures.ts';
import {
  MockResearch,
  researchPreview,
  findingStatus,
  rebuttalLetter,
  newResearchSession,
} from './research.ts';
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
  const ws = new MockWorkspace(initialState(), timer);
  const research = new MockResearch(ws, timer);
  const id = ws.state.activeChatId;
  research.open(id);
  const session = () => research.state.sessions[id];
  const step = () => {
    const item = jobs.entries().next().value;
    if (item) {
      jobs.delete(item[0]);
      item[1]();
    }
  };
  const flush = () => {
    let n = 0;
    while (jobs.size) {
      assert.ok(++n < 50, 'research work must be finite');
      step();
    }
  };
  const currentContext = () =>
    researchPreview(ws.state, session(), research.state.totalRounds).context;
  return { ws, research, id, jobs, session, step, flush, currentContext };
}
function draftResponses(f: ReturnType<typeof fixture>, evidence = true) {
  const round = f.session().rounds.at(-1)!;
  for (const finding of round.reviewers.flatMap((r) => r.findings))
    f.research.respond(f.id, round.id, finding.id, {
      response: 'We narrow the claim to this pilot and explain the evaluation boundary.',
      evidence: evidence ? 'Methods §2 / Limitations §4 · sample locator' : '',
      action: 'rebut',
    });
}

test('research preview reads state without a load, save, reservation or timer', () => {
  const f = fixture();
  const before = structuredClone(f.ws.state);
  const researchBefore = structuredClone(f.research.state);
  for (let i = 0; i < 10; i++) {
    const p = researchPreview(f.ws.state, f.session(), 0);
    assert.equal(p.blocked, false);
    assert.deepEqual(
      p.roles.map((r) => r.id),
      ['qwen', 'gemma'],
    );
  }
  assert.deepEqual(f.ws.state, before);
  assert.deepEqual(f.research.state, researchBefore);
  assert.equal(f.jobs.size, 0);
});

test('duplicate, missing-download and local-only cloud reviewers block before a round', () => {
  const f = fixture();
  f.research.reviewer(f.id, 1, 'qwen');
  f.research.start(f.id);
  assert.equal(f.session().rounds.length, 0);
  f.research.reviewer(f.id, 1, 'mistral');
  assert.equal(researchPreview(f.ws.state, f.session(), 0).blocked, true);
  f.research.reviewer(f.id, 1, 'claude');
  f.ws.state.connection.status = 'verified';
  assert.equal(researchPreview(f.ws.state, f.session(), 0).blocked, true);
  f.ws.setCloudAllowed(true);
  f.ws.addSampleFile('notes');
  f.ws.toggleFile('notes');
  assert.equal(researchPreview(f.ws.state, f.session(), 0).blocked, true);
});

test('heat changes critique tone while severity and requested evidence remain fixed', () => {
  const f = fixture();
  f.research.update(f.id, 'heat', 'gentle');
  f.research.start(f.id);
  f.flush();
  const gentle = f.session().rounds[0].reviewers.flatMap((r) => r.findings);
  f.research.update(f.id, 'heat', 'scorched');
  f.research.start(f.id);
  f.flush();
  const hot = f.session().rounds[1].reviewers.flatMap((r) => r.findings);
  assert.deepEqual(
    gentle.map((f) => [f.severity, f.request]),
    hot.map((f) => [f.severity, f.request]),
  );
  assert.notEqual(gentle[0].critique, hot[0].critique);
});

test('review roles serialize, freeze v1, exclude history and retain origin across project navigation', () => {
  const f = fixture();
  const paper = f.session().paper;
  f.research.start(f.id);
  assert.equal(f.session().rounds[0].reviewers[1].status, 'queued');
  assert.equal(f.ws.state.reviewActivity?.chatId, f.id);
  f.research.update(f.id, 'paper', paper + '\nA revision.');
  f.ws.selectProject('launch');
  f.ws.setDraft('ordinary chat');
  f.ws.send();
  assert.equal(f.ws.state.attempt, null);
  f.step();
  assert.equal(f.session().rounds[0].reviewers[0].status, 'complete');
  assert.equal(
    f.ws.state.models.filter((m) => m.place === 'local' && m.state === 'ready').length,
    1,
  );
  f.flush();
  assert.equal(f.session().revision, 2);
  assert.equal(f.session().rounds[0].paper, paper);
  assert.equal(f.session().rounds[0].revision, 1);
  assert.equal(f.ws.state.chats.find((c) => c.id === f.id)?.messages.length, 2);
});

test('source changes before the next reviewer produce incomplete results without substitution', () => {
  const f = fixture();
  f.ws.toggleFile('brief');
  f.research.start(f.id);
  f.ws.removeFile('brief');
  f.flush();
  assert.equal(f.session().rounds[0].status, 'incomplete');
  assert.equal(f.research.state.busy, null);
  assert.equal(f.ws.state.reviewActivity, null);
});

test('whole-round cloud allowance includes the optional third check before admission', () => {
  const f = fixture();
  f.ws.setCloudAllowed(true);
  f.ws.state.connection.status = 'verified';
  f.research.reviewer(f.id, 0, 'claude');
  f.research.reviewer(f.id, 1, 'gpt');
  f.ws.setCap(0.25);
  assert.equal(researchPreview(f.ws.state, f.session(), 0).blocked, true);
  f.research.start(f.id);
  assert.equal(f.ws.state.profileUsed, 0);
  f.ws.setCap(10);
  f.research.start(f.id);
  assert.ok(Math.abs(f.ws.state.profileUsed - 0.36) < 1e-8);
  f.flush();
  assert.ok(Math.abs(f.ws.state.profileUsed - 0.16) < 1e-8);
  assert.ok(Math.abs(f.session().rounds[0].remaining - 0.12) < 1e-8);
});

test('cancel keeps dispatched cloud cost unknown, releases unstarted work and clears callbacks', () => {
  const f = fixture();
  f.ws.setCloudAllowed(true);
  f.ws.state.connection.status = 'verified';
  f.research.reviewer(f.id, 0, 'claude');
  f.research.reviewer(f.id, 1, 'gpt');
  f.research.start(f.id);
  f.research.stop();
  assert.ok(Math.abs(f.ws.state.profileUsed - 0.12) < 1e-8);
  assert.ok(Math.abs(f.session().rounds[0].unknownHold - 0.12) < 1e-8);
  assert.equal(f.jobs.size, 0);
  assert.equal(f.session().rounds[0].status, 'incomplete');
});

test('response checks require replies, preserve original critiques and never close findings automatically', () => {
  const f = fixture();
  f.research.start(f.id);
  f.flush();
  const r = f.session().rounds[0];
  const original = r.reviewers.flatMap((r) => r.findings).map((f) => f.critique);
  f.research.checkResponses(f.id, r.id);
  assert.equal(r.check, null);
  draftResponses(f);
  f.research.checkResponses(f.id, r.id);
  f.flush();
  assert.deepEqual(
    r.reviewers.flatMap((r) => r.findings).map((f) => f.critique),
    original,
  );
  const finding = r.reviewers[0].findings[0];
  assert.equal(
    findingStatus(r, finding, f.session(), f.currentContext()),
    'Ready for your decision',
  );
  assert.equal(finding.decision, null);
  f.research.closeFinding(f.id, r.id, finding.id);
  assert.equal(findingStatus(r, finding, f.session(), f.currentContext()), 'Rebuttal accepted');
  const check = r.check;
  f.research.checkResponses(f.id, r.id);
  assert.equal(r.check, check);
  assert.equal(f.jobs.size, 0);
});

test('missing evidence and disagreement remain unresolved and cannot be green-lit', () => {
  const f = fixture();
  f.research.start(f.id);
  f.flush();
  draftResponses(f, false);
  const r = f.session().rounds[0];
  f.research.checkResponses(f.id, r.id);
  f.flush();
  const finding = r.reviewers[0].findings[0];
  assert.equal(findingStatus(r, finding, f.session(), f.currentContext()), 'Evidence needed');
  f.research.closeFinding(f.id, r.id, finding.id);
  assert.equal(finding.decision, null);
  const g = fixture();
  g.research.start(g.id);
  g.flush();
  draftResponses(g);
  const mixed = g.session().rounds[0];
  g.research.checkResponses(g.id, mixed.id, 'mixed');
  g.flush();
  assert.equal(
    findingStatus(mixed, mixed.reviewers[0].findings[1], g.session(), g.currentContext()),
    'Still disputed',
  );
});

test('a claimed revision without a changed paper remains disputed', () => {
  const f = fixture();
  f.research.start(f.id);
  f.flush();
  draftResponses(f);
  const r = f.session().rounds[0];
  const finding = r.reviewers[0].findings[0];
  f.research.respond(f.id, r.id, finding.id, { action: 'revise' });
  f.research.checkResponses(f.id, r.id);
  f.flush();
  assert.equal(findingStatus(r, finding, f.session(), f.currentContext()), 'Still disputed');
});

test('edited paper or reply invalidates prior acceptance and does not rewrite v1 history', () => {
  const f = fixture();
  f.research.start(f.id);
  f.flush();
  draftResponses(f);
  const r = f.session().rounds[0];
  f.research.checkResponses(f.id, r.id);
  f.flush();
  const finding = r.reviewers[0].findings[0];
  f.research.closeFinding(f.id, r.id, finding.id);
  f.research.update(f.id, 'paper', f.session().paper + '\nChanged after the check.');
  assert.equal(f.session().revision, 2);
  assert.equal(findingStatus(r, finding, f.session(), f.currentContext()), 'Check stale');
  assert.equal(r.revision, 1);
  assert.equal(r.check?.revision, 1);
  f.research.respond(f.id, r.id, finding.id, { response: 'New reply' });
  assert.equal(finding.decision, null);
});

test('the demo needs explicit round two and never repeats until agreement', () => {
  const f = fixture();
  f.research.start(f.id);
  f.flush();
  assert.equal(f.research.state.totalRounds, 1);
  assert.equal(f.jobs.size, 0);
  f.research.start(f.id);
  f.flush();
  f.research.start(f.id);
  assert.equal(f.research.state.totalRounds, 2);
  assert.equal(f.session().rounds.length, 2);
  assert.equal(f.jobs.size, 0);
});

test('a second roast carries matching replies as drafts without carrying acceptance', () => {
  const f = fixture();
  f.research.start(f.id);
  f.flush();
  draftResponses(f);
  const first = f.session().rounds[0];
  f.research.checkResponses(f.id, first.id);
  f.flush();
  f.research.closeFinding(f.id, first.id, first.reviewers[0].findings[0].id);
  f.research.update(f.id, 'paper', f.session().paper + '\nRevised limitations.');
  f.research.start(f.id);
  f.flush();
  const second = f.session().rounds[1];
  const finding = second.reviewers[0].findings[0];
  assert.equal(finding.response, first.reviewers[0].findings[0].response);
  assert.ok(finding.carriedFrom);
  assert.equal(finding.decision, null);
  assert.equal(second.check, null);
  assert.equal(findingStatus(second, finding, f.session(), f.currentContext()), 'Response drafted');
});

test('new-round preview can quote release of an unused check without refunding or mutating it', () => {
  const f = fixture();
  f.ws.setCloudAllowed(true);
  f.ws.state.connection.status = 'verified';
  f.research.reviewer(f.id, 0, 'claude');
  f.research.start(f.id);
  f.flush();
  const before = structuredClone(f.ws.state);
  f.research.reviewer(f.id, 0, 'qwen');
  f.research.reviewer(f.id, 1, 'claude');
  f.ws.setCap(0.2);
  const p = researchPreview(f.ws.state, f.session(), f.research.state.totalRounds);
  assert.equal(p.blocked, false);
  assert.equal(f.session().rounds[0].remaining, 0.12);
  assert.equal(f.ws.state.profileUsed, before.profileUsed);
  f.research.start(f.id);
  f.flush();
  assert.ok(Math.abs(f.ws.state.profileUsed - 0.04) < 1e-8);
});

test('busy deletion is blocked; idle deletion clears research content and releases only unused allowance', () => {
  const f = fixture();
  f.ws.setCloudAllowed(true);
  f.ws.state.connection.status = 'verified';
  f.research.reviewer(f.id, 0, 'claude');
  f.research.start(f.id);
  f.ws.deleteChat(f.id);
  assert.ok(f.research.state.sessions[f.id]);
  f.research.stop();
  const used = f.ws.state.profileUsed;
  f.ws.deleteChat(f.id);
  assert.equal(f.research.state.sessions[f.id], undefined);
  assert.equal(f.ws.state.profileUsed, used);
});

test('standalone rebuttal records unknown provenance and never invents model acceptance', () => {
  const s = newResearchSession('temporary');
  s.importedComments = 'Please explain the sampling bias.';
  s.importedResponse = 'We added a sampling limitation in Methods §2.';
  assert.match(rebuttalLetter(s), /provenance unknown/);
  assert.match(rebuttalLetter(s), /No original reviewer acceptance/);
});

test('saving Temporary preserves research history, resets future role intent and releases only unused checks', () => {
  const f = fixture();
  f.ws.newChat(null);
  const id = f.ws.state.activeChatId;
  f.research.open(id);
  f.ws.setCloudAllowed(true);
  f.ws.state.connection.status = 'verified';
  f.research.reviewer(id, 0, 'claude');
  f.research.start(id);
  f.flush();
  assert.ok(f.ws.state.profileUsed > 0.12);
  f.ws.saveTemporary('launch');
  const copyId = f.ws.state.activeChatId;
  const copied = f.research.state.sessions[copyId];
  assert.ok(copied);
  assert.equal(f.research.state.sessions[id], undefined);
  assert.equal(copied.rounds[0].origin, 'Temporary chat');
  assert.equal(copied.rounds[0].remaining, 0);
  assert.deepEqual(copied.reviewerIds, ['qwen', 'gemma']);
  assert.equal(f.ws.state.projects.find((p) => p.id === 'launch')?.cloudAllowed, false);
  assert.ok(Math.abs(f.ws.state.profileUsed - 0.02) < 1e-8);
});

test('disposing research stops owned work and releases unstarted reservations', () => {
  const f = fixture();
  f.research.start(f.id);
  assert.ok(f.jobs.size);
  f.research.dispose();
  assert.equal(f.jobs.size, 0);
  assert.equal(f.ws.state.reviewActivity, null);
});
