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
  reviewDecisionCurrent,
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
  f.flush(); // Only the separate named-work journal may settle after Stop.
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
  f.flush();
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
  f.flush();
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
  f.ws.dispose();
  assert.equal(f.jobs.size, 0);
  assert.equal(f.ws.state.reviewActivity, null);
});

test('known authors and same-version aliases do not count as independent reviewers', () => {
  const f = fixture();
  f.research.reviewer(f.id, 1, 'qwen-alias');
  assert.match(researchPreview(f.ws.state, f.session(), 0).reason, /distinct/);
  f.research.reviewer(f.id, 1, 'gemma');
  f.research.configure(f.id, { paperAuthor: 'qwen-alias' });
  assert.match(researchPreview(f.ws.state, f.session(), 0).reason, /known author/);
  f.research.start(f.id);
  assert.equal(f.session().rounds.length, 0);
  assert.equal(f.ws.state.profileUsed, 0);
});
test('explicit whole-project scope includes visible copies/transcripts and blocks omitted or unavailable sources', () => {
  const f = fixture();
  f.ws.addSampleFile('paper');
  f.research.scope(f.id, 'project');
  const p = researchPreview(f.ws.state, f.session(), 0);
  assert.ok(p.blocked);
  assert.ok(p.sources.some((s) => s.startsWith('Chat:')));
  assert.ok(f.session().fileIds.includes('paper'));
  f.research.source(f.id, 'files', 'paper');
  assert.equal(researchPreview(f.ws.state, f.session(), 0).blocked, false);
  f.research.configure(f.id, { chatIds: ['chat-launch'] });
  assert.match(researchPreview(f.ws.state, f.session(), 0).reason, /outside this project/);
  f.research.scope(f.id, 'selected');
  assert.deepEqual(f.session().chatIds, []);
  assert.deepEqual(f.session().fileIds, []);
});
test('authoring freezes question/sources before target, then gives identical v1 to two blind distinct reviewers', () => {
  const f = fixture();
  f.ws.state.models.find((m) => m.id === 'mistral')!.state = 'downloaded';
  f.research.configure(f.id, { mode: 'author', authorId: 'qwen', reviewerIds: ['auto', 'auto'] });
  f.research.update(
    f.id,
    'question',
    'Compare pilot evidence without claiming universal correctness.',
  );
  const p = researchPreview(f.ws.state, f.session(), 0);
  assert.equal(p.blocked, false);
  assert.equal(p.roles.length, 3);
  assert.equal(p.roles[0].kind, 'author');
  assert.equal(
    new Set(p.roles.map((r) => f.ws.state.models.find((m) => m.id === r.id)?.identity ?? r.id))
      .size,
    3,
  );
  f.research.start(f.id);
  const r = f.session().rounds[0];
  assert.equal(r.author?.status, 'authoring');
  assert.equal(r.reviewers[0].status, 'queued');
  assert.equal(r.question, 'Compare pilot evidence without claiming universal correctness.');
  f.ws.selectProject('launch');
  f.flush();
  assert.equal(r.author?.status, 'complete');
  assert.equal(r.status, 'complete');
  assert.equal(f.session().paperAuthor, 'qwen');
  assert.equal(r.context, f.currentContext());
  assert.equal(r.reviewers[0].input?.paper, r.paper);
  assert.deepEqual(r.reviewers[0].input, r.reviewers[1].input);
  assert.equal(r.reviewers[0].input?.peerVerdicts, false);
  assert.equal(f.ws.state.activeChatId, 'chat-launch');
  assert.equal(
    f.ws.state.models.filter((m) => m.place === 'local' && m.state === 'ready').length,
    1,
  );
});
test('author plus three-call cloud envelope is reserved once and price changes never expand it silently', () => {
  const f = fixture();
  f.ws.setCloudAllowed(true);
  f.ws.state.connection.status = 'verified';
  f.research.configure(f.id, { mode: 'author', authorId: 'claude', reviewerIds: ['qwen', 'gpt'] });
  const p = researchPreview(f.ws.state, f.session(), 0);
  assert.ok(Math.abs(p.upper - 0.36) < 1e-8);
  f.research.start(f.id);
  assert.ok(Math.abs(f.ws.state.profileUsed - 0.36) < 1e-8);
  f.ws.state.priceUpper = 0.24;
  f.flush();
  assert.equal(f.session().rounds[0].status, 'incomplete');
  assert.equal(f.research.state.busy, null);
  assert.ok(f.ws.state.profileUsed <= 0.24);
});
test('failed reviewer keeps completed findings and refuses human acceptance without reranking', () => {
  const f = fixture();
  f.research.configure(f.id, { scenario: 'reviewer-fails' });
  f.research.start(f.id);
  f.flush();
  const r = f.session().rounds[0];
  assert.equal(r.reviewers[0].status, 'complete');
  assert.equal(r.reviewers[1].status, 'failed');
  assert.equal(r.status, 'incomplete');
  assert.equal(r.reviewers[0].findings.length, 2);
  f.research.humanDecision(f.id, r.id, 'accept');
  assert.equal(f.session().signoff, null);
  assert.equal(f.research.state.totalRounds, 1);
});
test('failed planned load is incomplete and never substitutes a reviewer or cloud route', () => {
  const f = fixture();
  f.ws.setFixture('setupScenario', 'load-failed');
  f.research.start(f.id);
  f.flush();
  const r = f.session().rounds[0];
  assert.equal(r.status, 'incomplete');
  assert.equal(r.reviewers[0].status, 'complete');
  assert.equal(r.reviewers[1].status, 'failed');
  assert.equal(r.reviewers[1].modelId, 'gemma');
  assert.equal(f.ws.state.profileUsed, 0);
  assert.equal(f.ws.state.models.find((m) => m.id === 'gemma')?.state, 'downloaded');
});
test('reconciliation uses the sole optional call, creates unreviewed v2 and retains v1 critics', () => {
  const f = fixture();
  f.research.start(f.id);
  f.flush();
  const r = f.session().rounds[0];
  const original = structuredClone(r.reviewers);
  f.research.reconcile(f.id, r.id);
  f.flush();
  assert.equal(r.reconciliation?.status, 'complete');
  assert.equal(f.session().revision, 2);
  assert.notEqual(f.session().paper, r.paper);
  assert.deepEqual(r.reviewers, original);
  assert.equal(f.session().paperAuthor, 'qwen');
  assert.equal(f.session().signoff, null);
  f.research.checkResponses(f.id, r.id);
  assert.equal(r.check, null);
  assert.match(researchPreview(f.ws.state, f.session(), 1).reason, /known author/);
  f.research.humanDecision(f.id, r.id, 'accept');
  assert.equal(f.session().signoff, null);
});
test('human acceptance requires resolved current findings and becomes stale after any reply/source edit', () => {
  const f = fixture();
  f.research.start(f.id);
  f.flush();
  const r = f.session().rounds[0];
  f.research.humanDecision(f.id, r.id, 'accept');
  assert.equal(f.session().signoff, null);
  f.research.humanDecision(f.id, r.id, 'changes');
  assert.equal(reviewDecisionCurrent(f.ws.state, f.session()), true);
  draftResponses(f);
  assert.equal(reviewDecisionCurrent(f.ws.state, f.session()), false);
  f.research.checkResponses(f.id, r.id);
  f.flush();
  for (const finding of r.reviewers.flatMap((p) => p.findings))
    f.research.closeFinding(f.id, r.id, finding.id);
  f.research.humanDecision(f.id, r.id, 'accept');
  assert.equal(f.session().signoff?.decision, 'accept');
  assert.equal(reviewDecisionCurrent(f.ws.state, f.session()), true);
  f.ws.setProjectInstructions('Changed scope');
  assert.equal(reviewDecisionCurrent(f.ws.state, f.session()), false);
});
test('research journal restores named drafts/reviews but never Temporary bodies or an active pass', () => {
  const f = fixture();
  f.research.start(f.id);
  f.flush();
  f.research.update(f.id, 'paper', 'Saved named revision');
  f.flush();
  f.ws.newChat(null);
  const temp = f.ws.state.activeChatId;
  f.research.open(temp);
  f.research.update(temp, 'paper', 'Temporary paper must not survive');
  f.ws.restartMock(false, true);
  assert.ok(!f.research.state.sessions[temp]);
  assert.equal(f.research.state.sessions[f.id].paper, 'Saved named revision');
  assert.equal(f.research.state.sessions[f.id].rounds.length, 1);
  assert.equal(f.research.state.busy, null);
});
test('inactive-profile research stays isolated and deleted unused allowance refunds only its original profile', () => {
  const f = fixture();
  f.ws.setCloudAllowed(true);
  f.ws.state.connection.status = 'verified';
  f.research.reviewer(f.id, 0, 'claude');
  f.research.reviewer(f.id, 1, 'gpt');
  f.research.start(f.id);
  f.flush();
  assert.ok(f.ws.state.profileUsed > 0.15);
  f.ws.switchProfile('sandbox');
  assert.equal(f.ws.state.profileUsed, 0);
  assert.ok(f.research.state.sessions[f.id]);
  f.research.dispose();
  assert.equal(f.ws.state.profileUsed, 0);
  f.ws.switchProfile('personal');
  assert.ok(Math.abs(f.ws.state.profileUsed - 0.04) < 1e-8);
});

test('closing the optional call releases unused allowance and cannot later spend or refund it again', () => {
  const f = fixture();
  f.ws.setCloudAllowed(true);
  f.ws.state.connection.status = 'verified';
  f.research.reviewer(f.id, 0, 'claude');
  f.research.reviewer(f.id, 1, 'gpt');
  f.research.start(f.id);
  f.flush();
  const r = f.session().rounds[0];
  f.research.finishRound(f.id, r.id);
  f.flush();
  assert.equal(r.optionalClosed, true);
  assert.equal(r.remaining, 0);
  assert.ok(Math.abs(f.ws.state.profileUsed - 0.04) < 1e-8);
  draftResponses(f);
  f.research.checkResponses(f.id, r.id);
  f.research.reconcile(f.id, r.id);
  f.flush();
  assert.equal(r.check, null);
  assert.equal(r.reconciliation, null);
  assert.ok(Math.abs(f.ws.state.profileUsed - 0.04) < 1e-8);
});

test('late author/reconciliation output never overwrites unique edits to the working target', () => {
  const f = fixture();
  f.research.start(f.id);
  f.flush();
  const r = f.session().rounds[0];
  f.research.reconcile(f.id, r.id);
  f.research.update(f.id, 'paper', 'Unique human edit while a proposal is being prepared');
  f.flush();
  assert.equal(f.session().paper, 'Unique human edit while a proposal is being prepared');
  assert.ok(r.reconciliation?.paper);
  assert.equal(f.session().revision, 2);
  const a = fixture();
  a.ws.state.models.find((m) => m.id === 'mistral')!.state = 'downloaded';
  a.research.configure(a.id, {
    mode: 'author',
    authorId: 'qwen',
    reviewerIds: ['gemma', 'mistral'],
  });
  a.research.start(a.id);
  a.research.update(a.id, 'paper', 'Unique working draft');
  a.flush();
  assert.equal(a.session().paper, 'Unique working draft');
  assert.ok(a.session().rounds[0].paper.includes('Authored sample fixture'));
  assert.equal(a.session().rounds[0].status, 'complete');
});

test('author reconciliation uses the original author and preserves all known authors in later reviews', () => {
  const f = fixture();
  f.ws.state.models.find((m) => m.id === 'mistral')!.state = 'downloaded';
  f.research.configure(f.id, {
    mode: 'author',
    authorId: 'qwen',
    reviewerIds: ['gemma', 'mistral'],
  });
  f.research.start(f.id);
  f.flush();
  const r = f.session().rounds[0];
  f.research.reconcile(f.id, r.id);
  f.flush();
  assert.equal(r.reconciliation?.model, 'Qwen 3 · 8B');
  assert.deepEqual(f.session().paperAuthors, ['qwen']);
  assert.equal(researchPreview(f.ws.state, f.session(), 1).blocked, false);
  const other = fixture();
  other.ws.state.models.find((m) => m.id === 'mistral')!.state = 'downloaded';
  other.research.configure(other.id, { paperAuthor: 'qwen', reviewerIds: ['gemma', 'mistral'] });
  other.research.start(other.id);
  other.flush();
  other.research.reconcile(other.id, other.session().rounds[0].id);
  other.flush();
  assert.deepEqual(other.session().paperAuthors, ['qwen', 'gemma']);
  other.research.reviewer(other.id, 0, 'qwen-alias');
  assert.match(researchPreview(other.ws.state, other.session(), 1).reason, /known author/);
});
