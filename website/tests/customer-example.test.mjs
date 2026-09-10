import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { summarizeUsage, budgetBalance, monthPace, policyTemplate, evaluateExamplePolicy, restorePathExample } from '../lib/customer-example.mjs';

const fixture = JSON.parse(readFileSync(new URL('../public/demo/customer-example.json', import.meta.url)));
const compaction = JSON.parse(readFileSync(new URL('../public/demo/compaction-example.json', import.meta.url)));

test('all team/model/category drilldowns reconcile with the $1M example', () => {
  const totals = summarizeUsage(fixture);
  assert.equal(totals.cost, 50000);
  assert.equal(totals.tokens, 8990000000);
  for (const team of fixture.teams) {
    const summary = summarizeUsage(fixture, team.name);
    assert.equal(summary.cost, Number(team.expected_cost));
    assert.equal(summary.tokens, team.expected_tokens);
    const models = ['Advanced', 'Standard'].map(model => summarizeUsage(fixture, team.name, model));
    assert.equal(models.reduce((sum, row) => sum + row.cost, 0), summary.cost);
    assert.equal(models.reduce((sum, row) => sum + row.tokens, 0), summary.tokens);
  }
  assert.equal(fixture.teams.reduce((sum, team) => sum + Number(team.monthly_budget), 0), Number(fixture.monthly_budget));
  const engineering = summarizeUsage(fixture, 'Engineering');
  assert.equal(engineering.categories[2].cost, 16500);
  assert.equal(engineering.categories[2].cost / engineering.cost, .55);
  assert.equal((engineering.categories[2].tokens / engineering.tokens * 100).toFixed(2), '12.82');
  assert.equal(summarizeUsage(fixture, 'Engineering', 'Advanced').cost, 28800);
});
test('pace and available budget retain their distinct meanings, including negative remaining', () => {
  assert.equal(monthPace(50000, 15, 30), 100000);
  assert.equal(budgetBalance(83333.33, 50000, 2000, 500), 30833.33);
  assert.equal(budgetBalance(45000, 30000, 1200, 300), 13500);
  assert.equal(budgetBalance(100, 120, 5, 3), -28);
  assert.throws(() => monthPace(50000, 0, 30));
});
test('near-limit comparison conserves denied budget and charges no imaginary savings', () => {
  const before = JSON.stringify(fixture);
  const standard = evaluateExamplePolicy(policyTemplate('standard'), { nearLimit: true });
  const strict = evaluateExamplePolicy(policyTemplate('strict'), { nearLimit: true });
  assert.equal(standard.available, .5);
  assert.equal(standard.reservation, .7);
  assert.equal(standard.decision, 'denied');
  assert.equal(standard.calls, 0);
  assert.equal(standard.after, .5);
  assert.equal(standard.outputCeiling, .64);
  assert.equal(strict.outputCeiling, .16);
  assert.equal(strict.reservation, .22);
  assert.equal(strict.decision, 'allowed');
  assert.equal(strict.calls, 1);
  assert.equal(strict.after, .28);
  assert.equal(JSON.stringify(fixture), before);
});
test('model, secret, lockdown, and invalid candidates change the next decision safely', () => {
  for (const model of ['Advanced', 'Standard']) assert.equal(evaluateExamplePolicy(policyTemplate('lockdown'), { model }).calls, 0);
  assert.equal(evaluateExamplePolicy({ ...policyTemplate('standard'), advanced: false }).decision, 'denied');
  assert.equal(evaluateExamplePolicy(policyTemplate('standard'), { secret: true }).decision, 'allowed');
  assert.equal(evaluateExamplePolicy(policyTemplate('strict'), { secret: true }).decision, 'denied');
  for (const budget of [NaN, -1, 100000, Infinity]) assert.equal(evaluateExamplePolicy({ ...policyTemplate('standard'), budget }).decision, 'invalid');
  assert.equal(evaluateExamplePolicy({ ...policyTemplate('standard'), budget: 30000 }).decision, 'denied');
  assert.throws(() => policyTemplate('__proto__'));
});
test('the complete compact fixture reconstructs all paths, duplicates, and newline exactly', () => {
  const restored = restorePathExample(compaction.compact);
  assert.equal(restored, compaction.original);
  assert.equal(Buffer.byteLength(compaction.original), 3108);
  assert.equal(Buffer.byteLength(compaction.compact), 1679);
  const paths = restored.trimEnd().split('\n');
  assert.equal(paths.length, 127);
  assert.equal(paths.filter(path => path === 'src/generated/item_7.py').length, 6);
  assert.throws(() => restorePathExample('{"format":"unknown"}'));
});
