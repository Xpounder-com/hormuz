import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { validateSourcePin } from '../deployment/verify-source-pin.mjs';

test('publication pins a fixed public repository and an exact source commit', () => {
  const revision = '9af53c79d1671638a57dba9d758482c7d4f88ef8';
  assert.equal(validateSourcePin({ repository: 'Xpounder-com/hormuz', revision }), revision);
  for (const value of [null, [], 'main', {}, { repository: 'other/project', revision }, { repository: 'Xpounder-com/hormuz', revision, script: 'extra' }]) {
    assert.throws(() => validateSourcePin(value));
  }
  for (const invalid of ['main', 'v1.0.0', revision.slice(0, 7), revision.toUpperCase(), `${revision}\nextra=bad`, 123]) {
    assert.throws(() => validateSourcePin({ repository: 'Xpounder-com/hormuz', revision: invalid }));
  }
});

test('dedicated publication uses read-only source builds and a main-only Pages boundary', () => {
  const workflow = readFileSync(new URL('../deployment/root-pages.yml', import.meta.url), 'utf8');
  assert.match(workflow, /permissions:\n  contents: read/);
  assert.equal((workflow.match(/pages: write/g) || []).length, 1);
  assert.equal((workflow.match(/id-token: write/g) || []).length, 1);
  const guard = "if: github.repository == 'usehormuz/usehormuz.github.io' && github.event_name != 'pull_request' && github.ref == 'refs/heads/main'";
  assert.equal(workflow.split(guard).length - 1, 3);
  assert.match(workflow, /ref: \$\{\{ steps\.source\.outputs\.revision \}\}/);
  assert.match(workflow, /repository: Xpounder-com\/hormuz/);
  assert.equal((workflow.match(/persist-credentials: false/g) || []).length, 3);
  assert.match(workflow, /cp site-source\.json product\/website\/out\/site-source\.json/);
  assert.match(workflow, /needs: deploy/);
  assert.match(workflow, /run: node scripts\/verify-live-site\.mjs/);
  const deploy = workflow.split('\n  deploy:\n')[1].split('\n  verify:\n')[0];
  assert.equal((deploy.match(/uses:/g) || []).length, 1);
  assert.doesNotMatch(deploy, /run:|checkout|setup-node/);
  assert.doesNotMatch(workflow, /contents: write|pull_request_target|prepare:legacy|secrets\./);
  for (const [, action] of workflow.matchAll(/uses: ([^\s]+)/g)) assert.match(action, /@[a-f0-9]{40}$/);
});
