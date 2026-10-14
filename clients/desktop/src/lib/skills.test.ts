import test from 'node:test';
import assert from 'node:assert/strict';
import { MockWorkspace } from './workspace.ts';
import { preview, activeChat } from './planner.ts';
import { activeSkills, skills } from './skills.ts';
import { exportProject, inspectImport } from './workflows.ts';

test('skills are opt-in instructions activated only by explicit leading commands', () => {
  assert.deepEqual(activeSkills([], '/roast paper'), []);
  assert.deepEqual(activeSkills(['paper-roast'], 'Please roast this'), []);
  assert.equal(activeSkills(['paper-roast'], '/ROAST spicy: paper')[0].id, 'paper-roast');
  assert.deepEqual(activeSkills(['paper-roast'], '/roast-extra'), []);
  assert.equal(skills.length, 3);
  assert.ok(skills.every((s) => s.instructions.length < 8000));
});
test('preview composes skill instructions without calls, loads, mutations or new timers', () => {
  let scheduled = 0;
  const service = new MockWorkspace(undefined, { after: () => ++scheduled, clear: () => {} });
  service.toggleSkill('paper-roast');
  service.setDraft('/roast scorched: a supplied sample');
  const before = structuredClone(service.state);
  const count = scheduled;
  assert.match(preview(service.state).instructions, /Skill: Paper roast.*v1/);
  assert.match(preview(service.state).instructions, /never the\s+severity/i);
  assert.deepEqual(service.state, before);
  assert.equal(scheduled, count);
  service.dispose();
});
test('skill context follows destination inheritance and Temporary stays separately scoped', () => {
  const service = new MockWorkspace(undefined, { after: () => 0, clear: () => {} });
  service.toggleSkill('paper-roast');
  service.setDraft('/roast paper');
  assert.match(preview(service.state).instructions, /Skill: Paper roast/);
  service.moveChat(activeChat(service.state).id, 'launch', false);
  assert.doesNotMatch(preview(service.state).instructions, /Skill: Paper roast/);
  service.newChat(null);
  service.toggleSkill('paper-rebuttal');
  service.setDraft('/rebuttal supplied review');
  assert.match(preview(service.state).instructions, /Skill: Paper rebuttal/);
  service.selectProject('research');
  assert.deepEqual(activeSkills(activeChat(service.state).skillIds, '/rebuttal'), []);
  service.dispose();
});
test('export does not silently restore executable or selected skill bindings', () => {
  const service = new MockWorkspace(undefined, { after: () => 0, clear: () => {} });
  service.toggleSkill('paper-roast');
  const output = exportProject(service.state, 'research');
  assert.ok(!output.includes('skillIds'));
  assert.ok(!inspectImport(output, service.state).error);
  assert.ok(!inspectImport(output, service.state).bundle?.project.hasOwnProperty('skillIds'));
  service.dispose();
});
test('a submitted skill version stays frozen when availability changes during output', () => {
  const service = new MockWorkspace(undefined, { after: () => 0, clear: () => {} });
  service.toggleSkill('paper-roast');
  service.setDraft('/roast supplied claim');
  service.send();
  const receipt = activeChat(service.state).messages.at(-1)!.receipt!;
  const submitted = receipt.instructions;
  assert.match(submitted, /Skill: Paper roast.*v1/);
  service.toggleSkill('paper-roast');
  assert.equal(receipt.instructions, submitted);
  assert.ok(service.state.attempt);
  service.dispose();
});
