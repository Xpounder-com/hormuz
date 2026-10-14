import test from 'node:test';
import assert from 'node:assert/strict';
import { MockWorkspace } from './workspace.ts';
import { initialState } from './fixtures.ts';
import { activeChat, preview } from './planner.ts';
import { exportProject, inspectImport, movePreview, searchHistory } from './workflows.ts';
import { textBlocks } from './markdown.ts';
import { sendsOnEnter } from './keyboard.ts';
import type { Timer } from './types.ts';

function fixture() {
  let seq = 0;
  const jobs = new Map<number, () => void>();
  const timer: Timer = {
    after: (fn) => {
      jobs.set(++seq, fn);
      return seq;
    },
    clear: (id) => {
      jobs.delete(id as number);
    },
  };
  const service = new MockWorkspace(initialState(), timer);
  const flush = () => {
    let count = 0;
    while (jobs.size) {
      assert.ok(count++ < 200, 'bounded fixture callbacks');
      const [id, fn] = jobs.entries().next().value!;
      jobs.delete(id);
      fn();
    }
  };
  return { service, jobs, flush };
}

test('portable historical instructions accept the full app/project/chat/skill packet', () => {
  const { service } = fixture();
  service.setAppInstructions('a'.repeat(8000));
  service.setProjectInstructions('p'.repeat(8000));
  activeChat(service.state).instructions = 'c'.repeat(8000);
  service.toggleSkill('paper-roast');
  service.setDraft('/roast supplied paper');
  const frozen = preview(service.state).instructions;
  assert.ok(frozen.length > 24000);
  activeChat(service.state).messages.at(-1)!.receipt!.instructions = frozen;
  const imported = inspectImport(exportProject(service.state, 'research'), service.state);
  assert.equal(imported.error, '');
  assert.equal(imported.bundle?.chats[0].messages.at(-1)?.receipt?.instructions, frozen);
});

test('portable chat caps remain restrictive and absent legacy caps default to zero', () => {
  const { service } = fixture();
  service.setChatCap(0.05);
  const exported = exportProject(service.state, 'research');
  assert.ok(service.importProject(exported, true));
  assert.equal(activeChat(service.state).cap, 0.05);
  const legacy = JSON.parse(exported);
  for (const c of legacy.chats) delete c.cap;
  assert.equal(inspectImport(JSON.stringify(legacy), service.state).bundle?.chats[0].cap, 0);
  for (const cap of [-1, 11, '10']) {
    legacy.chats[0].cap = cap;
    assert.ok(inspectImport(JSON.stringify(legacy), service.state).error);
  }
});

test('project creation and empty import cannot mutate a full chat profile', () => {
  const { service } = fixture();
  const exported = JSON.parse(exportProject(service.state, 'launch'));
  exported.chats = [];
  while (service.state.chats.length < 500)
    service.state.chats.push({
      ...structuredClone(activeChat(service.state)),
      id: `limit-${service.state.chats.length}`,
    });
  const projects = structuredClone(service.state.projects);
  const active = service.state.activeChatId;
  service.createProject('No room');
  assert.deepEqual(service.state.projects, projects);
  assert.equal(service.state.activeChatId, active);
  assert.equal(service.importProject(JSON.stringify(exported), true), false);
  assert.deepEqual(service.state.projects, projects);
});

test('opening a project prefers an active chat to its first archived chat', () => {
  const { service } = fixture();
  const first = activeChat(service.state);
  service.archiveChat(first.id);
  service.selectProject('launch');
  service.selectProject('research');
  assert.equal(activeChat(service.state).id, 'chat-outline');
  assert.equal(Boolean(activeChat(service.state).archived), false);
});

test('multibyte replacement refusal leaves the previous copy and revision intact', () => {
  const { service } = fixture();
  const before = structuredClone(service.state.projects[0].files[0]);
  assert.equal(service.replaceFile(before.id, '雪'.repeat(90000)), false);
  assert.deepEqual(service.state.projects[0].files[0], before);
  assert.equal(service.replaceFile(before.id, 'Smaller replacement'), true);
  assert.equal(service.state.projects[0].files[0].revision, before.revision + 1);
});

test('refused restart preserves an active response when another profile has unsaved work', () => {
  const { service } = fixture();
  service.switchProfile('sandbox');
  service.newChat(null);
  service.setDraft('Keep this Temporary draft');
  service.switchProfile('personal');
  service.setDraft('Write an active response');
  service.send();
  const attempt = service.state.attempt?.id;
  assert.ok(attempt);
  assert.equal(service.restartMock(false, true), false);
  assert.equal(service.state.attempt?.id, attempt);
  service.dispose();
});

test('search distinguishes content/title, project/archive scope and excludes Temporary bodies and drafts', () => {
  const { service } = fixture();
  const first = activeChat(service.state);
  service.setDraft('unsubmitted secret fixture');
  assert.equal(searchHistory(service.state, 'quick way', 'research', 'title', 'all').length, 0);
  assert.equal(
    searchHistory(service.state, 'quick way', 'research', 'content', 'all')[0]?.id,
    first.id,
  );
  assert.equal(searchHistory(service.state, 'quick way', 'launch', 'content', 'all').length, 0);
  assert.equal(
    searchHistory(service.state, 'unsubmitted secret fixture', 'profile', 'content', 'all').length,
    0,
  );
  service.archiveChat(first.id);
  assert.equal(
    searchHistory(service.state, 'quick way', 'research', 'content', 'active').length,
    0,
  );
  assert.equal(
    searchHistory(service.state, 'quick way', 'research', 'content', 'archived').length,
    1,
  );
  service.newChat(null);
  service.setDraft('temporary fixture');
  service.send();
  assert.ok(
    searchHistory(service.state, 'temporary fixture', 'profile', 'content', 'all').length === 0,
  );
});
test('unavailable history is explicit and search stays bounded with large local lists', () => {
  const { service } = fixture();
  service.seedHistory();
  service.seedHistory();
  assert.equal(searchHistory(service.state, '', 'research', 'title', 'all').length, 100);
  service.setStorageScenario('corrupt');
  assert.deepEqual(searchHistory(service.state, '', 'profile', 'content', 'all'), []);
  assert.ok(service.state.chats.length > 100);
});
test('export whitelist excludes connections, usage, continuation, route overrides and other profiles', () => {
  const { service } = fixture();
  const chat = activeChat(service.state);
  chat.continuationModel = 'opaque-private-handle';
  chat.requestIntent = 'claude';
  service.state.connection.label = 'PRIVATE_BINDING';
  service.state.usage.private = 7;
  const output = exportProject(service.state, 'research');
  assert.ok(!output.includes('PRIVATE_BINDING'));
  assert.ok(!output.includes('opaque-private-handle'));
  assert.ok(!output.includes('requestIntent'));
  assert.ok(!output.includes('profileUsed'));
  const bundle = JSON.parse(output);
  assert.equal(bundle.chats.length, 2);
  assert.equal(bundle.files.length, 1);
  assert.equal(bundle.chats[0].messages[1].receipt.origin, 'Research notes');
});
test('import creates fresh identities, resolves name conflicts without overwrite and restores no authority', () => {
  const { service } = fixture();
  service.setCloudAllowed(true);
  service.state.connection.status = 'verified';
  service.state.profileUsed = 2;
  const output = exportProject(service.state, 'research');
  const before = structuredClone(service.state.projects[0]);
  assert.equal(inspectImport(output, service.state).conflict, true);
  assert.equal(service.importProject(output, false), true);
  const chat = activeChat(service.state);
  const project = service.state.projects.find((p) => p.id === chat.projectId)!;
  assert.notEqual(project.id, before.id);
  assert.notEqual(project.files[0].id, before.files[0].id);
  assert.match(project.name, /imported/);
  assert.equal(project.cloudAllowed, false);
  assert.equal(project.modelIntent, 'unbound');
  assert.deepEqual(chat.selectedFiles, []);
  assert.equal(preview(service.state).blocked, true);
  assert.equal(service.state.profileUsed, 2);
  assert.deepEqual(service.state.projects[0], before);
  service.pin('auto');
  assert.equal(preview(service.state).model?.place, 'local');
});
test('imports reject malformed versions, credentials, live handles, duplicate identities and oversized input', () => {
  const { service } = fixture();
  const bundle = JSON.parse(exportProject(service.state, 'research'));
  assert.ok(inspectImport('invalid', service.state).error);
  assert.ok(inspectImport(JSON.stringify({ ...bundle, version: 2 }), service.state).error);
  assert.ok(
    inspectImport(JSON.stringify({ ...bundle, apiKey: 'FAKE_SECRET' }), service.state).error,
  );
  for (const field of ['archived', 'pinned']) {
    bundle.chats[0][field] = 'false';
    assert.ok(inspectImport(JSON.stringify(bundle), service.state).error);
    bundle.chats[0][field] = false;
  }
  bundle.chats[0].messages[1].receipt.continuationToken = 'FAKE_HANDLE';
  assert.ok(inspectImport(JSON.stringify(bundle), service.state).error);
  delete bundle.chats[0].messages[1].receipt.continuationToken;
  bundle.files.push({ ...bundle.files[0] });
  assert.ok(inspectImport(JSON.stringify(bundle), service.state).error);
  assert.match(inspectImport('x'.repeat(1024 * 1024 + 1), service.state).error, /1 MiB/);
});
test('unsupported import entries need deliberate partial consent; failed write never creates a project', () => {
  const { service } = fixture();
  service.addSampleFile('paper');
  const output = exportProject(service.state, 'research');
  assert.equal(inspectImport(output, service.state).warnings.length, 1);
  const count = service.state.projects.length;
  assert.equal(service.importProject(output, false), false);
  assert.equal(service.state.projects.length, count);
  service.setStorageScenario('full');
  assert.equal(service.importProject(output, true), false);
  assert.equal(service.state.projects.length, count);
  service.setStorageScenario('available');
  assert.equal(service.importProject(output, true), true);
  assert.equal(service.state.projects.at(-1)?.files.length, 1);
});
test('move previews inheritance, preserves restrictive privacy and original receipts, and copies only explicitly', () => {
  const { service } = fixture();
  const chat = activeChat(service.state);
  service.toggleFile('brief');
  service.setDraft('Keep the origin draft');
  service.pin('gemma');
  service.setChatInstructions('source-only guidance');
  service.state.projects.find((p) => p.id === 'launch')!.cloudAllowed = true;
  assert.equal(movePreview(service.state, chat.id, 'launch')?.localOnly, true);
  assert.equal(service.moveChat(chat.id, 'launch', true), true);
  assert.equal(chat.draft, 'Keep the origin draft');
  assert.equal(chat.messages.at(-1)?.receipt?.origin, 'Research notes');
  assert.equal(chat.instructions, '');
  assert.equal(chat.inheritIntent, true);
  assert.equal(chat.localOnly, true);
  assert.deepEqual(chat.selectedFiles, []);
  assert.equal(preview(service.state).cloudAllowed, false);
  assert.equal(service.state.projects[0].files.length, 1);
  assert.equal(service.state.projects[1].files.length, 1);
  assert.notEqual(service.state.projects[1].files[0].id, 'brief');
});
test('busy or failed-storage moves leave origin intact; clearing retains copies and usage', () => {
  const { service, flush } = fixture();
  const id = service.state.activeChatId;
  service.setDraft('code');
  service.send();
  assert.equal(service.moveChat(id, 'launch', false), false);
  assert.equal(activeChat(service.state).projectId, 'research');
  service.stop();
  flush();
  service.setStorageScenario('write-failed');
  assert.equal(service.moveChat(id, 'launch', false), false);
  service.state.usage.research = 0.12;
  service.clearConversation(id);
  assert.equal(activeChat(service.state).messages.length, 0);
  assert.equal(service.state.projects[0].files.length, 1);
  assert.equal(service.state.usage.research, 0.12);
  assert.equal(service.state.storage.save, 'failed');
});
test('failed Temporary save retains content and consent until an acknowledged copy', () => {
  const { service } = fixture();
  service.newChat(null);
  service.setDraft('Temporary work');
  service.setCloudAllowed(true);
  const id = service.state.activeChatId;
  service.setStorageScenario('full');
  service.saveTemporary('launch');
  assert.equal(service.state.activeChatId, id);
  assert.equal(activeChat(service.state).draft, 'Temporary work');
  assert.equal(activeChat(service.state).cloudAllowed, true);
  assert.equal(service.state.storage.save, 'failed');
  service.setStorageScenario('available');
  service.saveTemporary('launch');
  assert.ok(!service.state.chats.some((c) => c.id === id));
  assert.equal(activeChat(service.state).draft, 'Temporary work');
  assert.equal(preview(service.state).cloudAllowed, false);
  assert.equal(service.state.storage.save, 'saved');
});
test('named draft saves are scoped/debounced; restart retains journal and requires a Temporary decision', () => {
  const { service, jobs, flush } = fixture();
  service.setDraft('Saved first');
  service.setDraft('Saved latest');
  assert.equal(jobs.size, 1);
  assert.equal(service.state.storage.save, 'saving');
  flush();
  assert.equal(service.state.storage.save, 'saved');
  const id = service.state.activeChatId;
  service.newChat(null);
  service.setDraft('Must decide');
  assert.equal(service.restartMock(), false);
  assert.equal(activeChat(service.state).draft, 'Must decide');
  assert.equal(service.restartMock(false, true), true);
  assert.equal(service.state.chats.find((c) => c.id === id)?.draft, 'Saved latest');
  assert.ok(!service.state.chats.some((c) => c.projectId === null));
});
test('failed named save has recovery without replay; explicit discard restores last journal', () => {
  const { service, flush } = fixture();
  service.setDraft('Journal version');
  flush();
  service.setStorageScenario('write-failed');
  service.setDraft('Unsaved revision');
  assert.equal(service.state.storage.save, 'failed');
  assert.equal(service.restartMock(), false);
  assert.equal(activeChat(service.state).draft, 'Unsaved revision');
  assert.equal(service.restartMock(true, true), true);
  assert.equal(activeChat(service.state).draft, 'Journal version');
  assert.equal(service.state.storage.save, 'failed');
  service.setStorageScenario('available');
  service.retrySave();
  assert.equal(service.state.storage.save, 'saved');
  assert.equal(service.state.attempt, null);
});
test('profile switch isolates search, connections, models and usage, preserving the previous memory profile', () => {
  const { service, flush } = fixture();
  service.setDraft('Personal draft');
  flush();
  service.state.connection.status = 'verified';
  service.state.profileUsed = 2;
  service.switchProfile('sandbox');
  assert.equal(service.state.projects.length, 0);
  assert.equal(service.state.connection.status, 'absent');
  assert.equal(service.state.profileUsed, 0);
  assert.deepEqual(searchHistory(service.state, '', 'profile', 'content', 'all'), []);
  assert.ok(
    service.state.models.filter((m) => m.place === 'local').every((m) => m.state === 'offered'),
  );
  service.createProject('Sandbox');
  flush();
  service.switchProfile('personal');
  assert.equal(activeChat(service.state).draft, 'Personal draft');
  assert.equal(service.state.connection.status, 'verified');
  assert.equal(service.state.profileUsed, 2);
  assert.ok(!service.state.projects.some((p) => p.name === 'Sandbox'));
});
test('one-request intent survives refusal and is consumed only at dispatch; receipt stays captured', () => {
  const { service, flush } = fixture();
  service.setDraft('Research');
  service.setRequestIntent('claude');
  service.send();
  assert.equal(activeChat(service.state).requestIntent, 'claude');
  assert.equal(service.state.attempt, null);
  service.setCloudAllowed(true);
  service.state.connection.status = 'verified';
  service.send();
  assert.equal(activeChat(service.state).requestIntent, undefined);
  const receipt = activeChat(service.state).messages.at(-1)?.receipt;
  service.pin('qwen');
  flush();
  assert.equal(receipt?.model, 'Claude · cloud demo');
  assert.equal(preview(service.state).model?.id, 'qwen');
});
test('changed Send route requires renewal with no dispatch, reservation or lost draft', () => {
  const { service, flush } = fixture();
  service.setDraft('A question');
  const before = preview(service.state);
  service.setFixture('sendScenario', 'route-change');
  service.send(before.fingerprint);
  assert.equal(service.state.attempt, null);
  assert.equal(activeChat(service.state).draft, 'A question');
  assert.equal(service.state.profileUsed, 0);
  assert.ok(service.state.sendConfirmation);
  assert.equal(preview(service.state).model?.id, 'gemma');
  service.send(preview(service.state).fingerprint);
  assert.equal(service.state.attempt, null);
  service.send(preview(service.state).fingerprint, true);
  flush();
  assert.equal(activeChat(service.state).messages.at(-1)?.receipt?.model, 'Gemma 3 · 12B');
});
test('changed cloud upper allowance retains request intent until explicitly confirmed', () => {
  const { service, flush } = fixture();
  service.setCloudAllowed(true);
  service.state.connection.status = 'verified';
  service.setRequestIntent('claude');
  service.setDraft('Research');
  const before = preview(service.state);
  service.setFixture('sendScenario', 'price-change');
  service.send(before.fingerprint);
  assert.equal(service.state.profileUsed, 0);
  assert.equal(activeChat(service.state).requestIntent, 'claude');
  assert.match(service.state.sendConfirmation!.after, /0.24/);
  service.send(preview(service.state).fingerprint, true);
  flush();
  assert.equal(activeChat(service.state).requestIntent, undefined);
  assert.ok(Math.abs(service.state.profileUsed - 0.02) < 1e-8);
});
test('opaque continuation never translates to another model; explicit new chat has no history', () => {
  const { service } = fixture();
  service.pin('qwen');
  service.setContinuation(true);
  service.pin('gemma');
  service.setDraft('Continue');
  assert.equal(preview(service.state).blocked, true);
  const id = service.state.activeChatId;
  service.newConversation();
  assert.notEqual(service.state.activeChatId, id);
  assert.equal(activeChat(service.state).messages.length, 0);
  assert.equal(activeChat(service.state).draft, 'Continue');
  assert.equal(activeChat(service.state).continuationModel, undefined);
});
test('profile/project ceilings and provider binding constrain every manual request', () => {
  const { service } = fixture();
  service.setCloudAllowed(true);
  service.state.connection.status = 'verified';
  service.pin('claude');
  service.setProfileRestriction(true);
  assert.equal(preview(service.state).blocked, true);
  service.setProfileRestriction(false);
  service.setProjectRestriction(true);
  assert.equal(preview(service.state).blocked, true);
  service.setProjectRestriction(false);
  service.setChatCap(0);
  assert.equal(preview(service.state).blocked, true);
  service.setChatCap(10);
  service.state.connection.provider = 'gpt';
  assert.equal(preview(service.state).blocked, true);
  service.pin('gpt');
  assert.equal(preview(service.state).blocked, false);
});
test('file replacements freeze earlier receipts and missing/unbound copies cannot dispatch', () => {
  const { service, flush } = fixture();
  service.toggleFile('brief');
  service.setDraft('Research');
  service.send();
  service.replaceFile('brief', 'New copy');
  flush();
  assert.deepEqual(activeChat(service.state).messages.at(-1)?.receipt?.context, [
    'Research brief.md · v1',
  ]);
  assert.equal(service.state.projects[0].files[0].revision, 2);
  service.setFileStatus('brief', 'denied');
  service.setDraft('next');
  assert.equal(preview(service.state).blocked, true);
  service.toggleFile('brief');
  service.addSampleFile('link');
  service.setFileStatus('link', 'available');
  service.toggleFile('link');
  assert.ok(!activeChat(service.state).selectedFiles.includes('link'));
});
test('setup failures never become Ready or trigger fallback; explicit retry recovers', () => {
  const { service, flush } = fixture();
  service.setFixture('setupScenario', 'verify-failed');
  service.downloadModel('mistral');
  flush();
  assert.equal(service.state.models.find((m) => m.id === 'mistral')?.state, 'failed');
  service.setFixture('setupScenario', 'normal');
  service.downloadModel('mistral');
  flush();
  assert.equal(service.state.models.find((m) => m.id === 'mistral')?.state, 'downloaded');
  service.setFixture('setupScenario', 'load-failed');
  service.loadModel('mistral');
  flush();
  assert.equal(service.state.models.find((m) => m.id === 'mistral')?.state, 'downloaded');
  assert.equal(service.state.attempt, null);
  assert.equal(service.state.profileUsed, 0);
});
test('partial/unknown stream retains holds and only prepares a separately admitted retry', () => {
  const { service, flush } = fixture();
  service.setCloudAllowed(true);
  service.state.connection.status = 'verified';
  service.setDraft('Research');
  service.setFixture('sendScenario', 'unknown');
  service.send();
  flush();
  const chat = activeChat(service.state);
  const message = chat.messages.at(-1)!;
  assert.equal(message.receipt?.status, 'unknown');
  assert.ok(message.text);
  assert.equal(service.state.profileUsed, 0.12);
  assert.equal(service.state.attempt, null);
  service.retryResponse(message.id);
  assert.equal(chat.draft, 'Research');
  assert.equal(service.state.attempt, null);
  service.setFixture('sendScenario', 'normal');
  service.send();
  flush();
  assert.equal(chat.messages.at(-1)?.receipt?.retryOf, message.receipt?.attemptId);
  assert.ok(Math.abs(service.state.profileUsed - 0.14) < 1e-8);
});
test('text rendering parses code and headings while leaving hostile HTML inert text', () => {
  const input =
    '### Example\n\n<script>alert(1)</script>\n\n```ts\nthrow new Error("do not execute");\n```\n- A limit';
  const blocks = textBlocks(input);
  assert.deepEqual(
    blocks.map((b) => b.kind),
    ['heading', 'paragraph', 'code', 'list'],
  );
  assert.equal(blocks[1].text, '<script>alert(1)</script>');
  assert.ok(blocks[2].text.includes('do not execute'));
});

test('Enter respects IME composition and Shift+Enter rather than dispatching prematurely', () => {
  assert.equal(sendsOnEnter({ key: 'Enter', shiftKey: false, isComposing: false }, false), true);
  assert.equal(sendsOnEnter({ key: 'Enter', shiftKey: true, isComposing: false }, false), false);
  assert.equal(sendsOnEnter({ key: 'Enter', shiftKey: false, isComposing: true }, false), false);
  assert.equal(sendsOnEnter({ key: 'Enter', shiftKey: false, isComposing: false }, true), false);
});
test('locked/expired/rate-limited/provider error connections stay unverified without disabling local work', () => {
  const { service, flush } = fixture();
  service.configureConnection();
  service.setCloudAllowed(true);
  for (const status of ['locked', 'expired', 'rate-limited', 'provider-error'] as const) {
    service.setConnectionScenario(status);
    service.testConnection();
    flush();
    assert.equal(service.state.connection.status, status);
    service.pin('claude');
    assert.equal(preview(service.state).blocked, true);
    service.pin('qwen');
    assert.equal(preview(service.state).blocked, false);
  }
});
test('quit does not silently drop Temporary work in an inactive profile', () => {
  const { service } = fixture();
  service.newChat(null);
  service.setDraft('Personal Temporary');
  service.switchProfile('sandbox');
  assert.equal(service.restartMock(false, true), false);
  service.switchProfile('personal');
  assert.equal(activeChat(service.state).draft, 'Personal Temporary');
  service.deleteChat(service.state.activeChatId);
  assert.equal(service.restartMock(false, true), true);
  service.switchProfile('sandbox');
  assert.ok(activeChat(service.state));
});

test('cost accounting is content-free and survives content deletion independently', () => {
  const { service, flush } = fixture();
  service.setCloudAllowed(true);
  service.state.connection.status = 'verified';
  service.setDraft('Private paper fixture body');
  service.pin('claude');
  service.setFixture('sendScenario', 'unknown');
  service.send();
  flush();
  const attempt = activeChat(service.state).messages.at(-1)?.receipt?.attemptId!;
  assert.equal(service.state.accounting[attempt].held, 0.12);
  assert.ok(!JSON.stringify(service.state.accounting).includes('Private paper fixture body'));
  service.deleteChat(service.state.activeChatId);
  assert.equal(service.state.accounting[attempt].held, 0.12);
  assert.equal(service.state.profileUsed, 0.12);
});
test('a recovered partial journal receipt uses retained actual/hold evidence and cannot look completed', () => {
  const { service, jobs } = fixture();
  service.setCloudAllowed(true);
  service.state.connection.status = 'verified';
  service.setDraft('Research');
  service.send();
  const journalResponse = activeChat(service.state).messages.at(-1)!;
  service.retrySave(); // Capture an intentionally incomplete body in the mock journal.
  let steps = 0;
  while (service.state.attempt) {
    assert.ok(steps++ < 100);
    const [id, fn] = jobs.entries().next().value!;
    jobs.delete(id);
    fn();
  } // Leave the terminal content-save timer pending.
  service.setStorageScenario('write-failed');
  service.setDraft('Unsaved follow-up');
  assert.equal(service.restartMock(true, true), true);
  const restored = activeChat(service.state).messages.at(-1)!;
  assert.equal(service.state.accounting[journalResponse.receipt!.attemptId!].actual, 0.02);
  assert.equal(restored.receipt?.status, 'unknown');
  assert.equal(restored.receipt?.restored, true);
  assert.equal(restored.receipt?.actual, 0.02);
  assert.equal(restored.receipt?.held, 0);
  assert.equal(service.state.attempt, null);
});
