import test from 'node:test';
import assert from 'node:assert/strict';
import { workspaceConfig } from '../lib/workspace.mjs';

test('workspace entry stays unavailable until a hosted origin is configured', () => {
  assert.equal(workspaceConfig({}).dashboardUrl, null);
  assert.equal(workspaceConfig({ NEXT_PUBLIC_HORMUZ_DASHBOARD_ORIGIN: 'https://dashboard.example.com' }).dashboardUrl, 'https://dashboard.example.com/workspace');
});

test('public workspace destination refuses static Pages and unsafe URLs', () => {
  for (const value of ['http://example.com', 'https://usehormuz.github.io', 'https://user:secret@example.com', 'https://example.com/app', 'https://example.com?token=secret', 'https://example.com#app']) {
    assert.throws(() => workspaceConfig({ NEXT_PUBLIC_HORMUZ_DASHBOARD_ORIGIN: value }));
  }
});
