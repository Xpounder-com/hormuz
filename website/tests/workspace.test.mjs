import test from 'node:test';
import assert from 'node:assert/strict';
import { workspaceConfig, workEntryConfig } from '../lib/workspace.mjs';

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
