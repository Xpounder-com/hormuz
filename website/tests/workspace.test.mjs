import test from 'node:test';
import assert from 'node:assert/strict';
import { workspaceConfig, workEntryConfig, workAcquisitionLabels, workAcquisitionLink } from '../lib/workspace.mjs';

test('workspace entry stays unavailable until a hosted origin is configured', () => {
  assert.equal(workspaceConfig({}).dashboardUrl, null);
  assert.equal(workspaceConfig({ NEXT_PUBLIC_HORMUZ_DASHBOARD_ORIGIN: 'https://dashboard.example.com' }).dashboardUrl, 'https://dashboard.example.com/workspace');
});

test('AI Work entry uses a configured gateway origin without collecting credentials', () => {
  assert.equal(workEntryConfig({}).workUrl, null);
  assert.equal(workEntryConfig({ HORMUZ_DASHBOARD_ORIGIN: 'https://gateway.example.com/' }).workUrl, 'https://gateway.example.com/work');
  assert.equal(workEntryConfig({ NEXT_PUBLIC_HORMUZ_DASHBOARD_ORIGIN: 'https://gateway.example.com' }).workUrl, 'https://gateway.example.com/work');
  assert.throws(() => workEntryConfig({ HORMUZ_DASHBOARD_ORIGIN: 'https://one.example.com', NEXT_PUBLIC_HORMUZ_DASHBOARD_ORIGIN: 'https://two.example.com' }));
  for (const value of ['http://localhost:8000', 'https://user:secret@gateway.example.com', 'https://gateway.example.com/?token=secret', 'https://usehormuz.github.io']) assert.throws(() => workEntryConfig({ HORMUZ_DASHBOARD_ORIGIN: value }));
});

test('public workspace destination refuses static Pages and unsafe URLs', () => {
  for (const value of ['http://example.com', 'https://usehormuz.github.io', 'https://user:secret@example.com', 'https://example.com/app', 'https://example.com?token=secret', 'https://example.com#app']) {
    assert.throws(() => workspaceConfig({ NEXT_PUBLIC_HORMUZ_DASHBOARD_ORIGIN: value }));
  }
});

test('gateway acquisition handoff is explicit, bounded, and excludes private query fields', () => {
  const work = 'https://gateway.example.com/work';
  const search = '?utm_source=fixture&utm_medium=search&utm_campaign=work&utm_content=demo&token=secret&work_id=private&repository=private&email=private&qa=1';
  assert.equal(workAcquisitionLink(work, search, false), work);
  assert.equal(workAcquisitionLink(work, search, 'true'), work);
  assert.equal(workAcquisitionLink(work, search, true), 'https://gateway.example.com/work/acquisition?utm_source=fixture&utm_medium=search&utm_campaign=work&utm_content=demo');
  assert.deepEqual(workAcquisitionLabels('?utm_source=one&utm_source=two&utm_campaign=unsafe%20label&utm_content=valid'), { utm_content: 'valid' });
  assert.deepEqual(workAcquisitionLabels('?utm_source=' + 'a'.repeat(65)), {});
  for (const target of ['http://gateway.example.com/work', 'https://usehormuz.github.io/work', 'https://gateway.example.com/work?token=secret', 'https://gateway.example.com/work#token', 'https://user:secret@gateway.example.com/work', 'https://gateway.example.com/admin']) assert.throws(() => workAcquisitionLink(target, search, true));
});
