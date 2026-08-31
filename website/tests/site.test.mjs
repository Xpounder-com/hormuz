import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { sitePath, siteUrl, CONTACT_EMAIL } from '../lib/site.mjs';
import { buildInquiry, campaignSource } from '../lib/contact.mjs';

test('native paths and metadata use the dedicated organization root', () => {
  assert.equal(sitePath('/'), '/');
  assert.equal(sitePath('/docs/#quickstart'), '/docs/#quickstart');
  assert.equal(siteUrl('/contact/'), 'https://usehormuz.github.io/contact/');
  assert.throws(() => sitePath('https://example.com'));
  assert.throws(() => sitePath('//example.com'));
});
test('inquiries encode user text as body, never additional recipients or headers', () => {
  const value = buildInquiry({ name: 'A & B', workflow: 'Budget? &bcc=someone@example.com\n#test', interest: 'pilot' });
  const url = new URL(value.mailto);
  assert.equal(url.pathname, CONTACT_EMAIL);
  assert.deepEqual([...url.searchParams.keys()], ['subject', 'body']);
  assert.match(url.searchParams.get('body'), /&bcc=someone@example.com/);
  assert.match(value.body, /inquiry, not a booking/);
});
test('required fields, limits, unknown and prototype interest values are safe', () => {
  assert.throws(() => buildInquiry({ name: ' ', workflow: 'hello' }));
  assert.throws(() => buildInquiry({ name: 'name', workflow: '' }));
  for (const interest of ['bad', '__proto__', 'constructor']) {
    assert.equal(buildInquiry({ name: 'n', workflow: 'w', interest }).subject, 'Hormuz — Enterprise pilot');
  }
  const result = buildInquiry({ name: 'n'.repeat(400), workflow: 'w'.repeat(4000), interest: 'security' });
  assert.ok(result.body.length < 1800);
  assert.equal(result.subject, 'Hormuz — Security requirements');
});
test('campaign attribution is bounded, whitelisted, and optional', () => {
  assert.equal(campaignSource('?email=private@example.com&utm_source=linkedin&utm_medium=founder'), 'utm_source=linkedin · utm_medium=founder');
  assert.equal(campaignSource('?utm_source=%3Cscript%3E&utm_campaign=' + 'x'.repeat(70)), '');
  const fields = { name: 'n', workflow: 'w' };
  assert.doesNotMatch(buildInquiry(fields).body, /campaign source/);
  assert.match(buildInquiry(fields, 'utm_source=linkedin').body, /Optional campaign source/);
});
for (const name of ['gateway', 'policy']) test(`${name} recording matches its unmodified transcript and provenance`, () => {
  const root = new URL(`../public/demo/${name}`, import.meta.url);
  const recording = JSON.parse(readFileSync(`${root.pathname}.json`, 'utf8'));
  const transcript = readFileSync(`${root.pathname}.txt`, 'utf8');
  const cast = readFileSync(`${root.pathname}.cast`, 'utf8').trimEnd().split('\n').map(JSON.parse);
  assert.equal(recording.exit_code, 0);
  assert.match(recording.source_revision, /^[a-f0-9]{40}$/);
  assert.equal(recording.transcript, transcript);
  assert.equal(recording.events.map(e => e[2]).join(''), transcript);
  assert.deepEqual(cast.slice(1), recording.events);
  assert.equal(createHash('sha256').update(transcript).digest('hex'), recording.transcript_sha256);
  assert.ok(recording.events.every((e, i) => e[0] >= 0 && e[0] <= recording.duration_seconds && (i === 0 || e[0] >= recording.events[i - 1][0])));
  if (name === 'gateway') { assert.equal((transcript.match(/PASS /g) || []).length, 6); assert.match(transcript, /external provider calls: 0/); }
});
test('synthetic evidence has the expected bounded outcomes and no content fields', () => {
  const lines = readFileSync(new URL('../public/demo/synthetic-evidence.jsonl', import.meta.url), 'utf8').trim().split('\n');
  const events = lines.map(JSON.parse);
  assert.equal(events.length, 5);
  const usage = events.filter(e => e.event_type === 'usage');
  assert.equal(usage.length, 4);
  assert.deepEqual(new Set(usage.map(e => e.policy_action)), new Set(['allowed', 'fallback+capped', 'allowed+redacted', 'denied']));
  for (const event of events) {
    for (const forbidden of ['prompt', 'response', 'messages', 'content', 'api_key', 'token']) assert.equal(Object.hasOwn(event, forbidden), false);
  }
});
test('claim ledger sources exist and social/commercial boundaries remain explicit', () => {
  const ledger = JSON.parse(readFileSync(new URL('../../marketing/claims-v1.json', import.meta.url), 'utf8'));
  assert.equal(ledger.public_author, 'Mehrdad Zaker');
  assert.equal(ledger.contact, CONTACT_EMAIL);
  assert.equal(ledger.social_outreach_status, 'draft_not_sent');
  assert.equal(ledger.commercial_terms_status, 'not_agreed');
  for (const claim of ledger.claims) for (const path of claim.sources) assert.ok(existsSync(new URL(`../../${path}`, import.meta.url)), path);
});

test('README and marketing relative links resolve, including Markdown fragments', () => {
  const files = ['README.md', 'marketing/README.md', 'marketing/OFFER.md', 'marketing/PILOT.md', 'marketing/TRUST.md', 'marketing/MEASUREMENT.md', 'marketing/CHANNELS.md', 'marketing/CONTRIBUTOR_STARTERS.md', 'marketing/tutorials/client-integration.md', 'marketing/tutorials/policy-and-evidence.md'];
  for (const file of files) {
    const source = new URL(`../../${file}`, import.meta.url);
    const text = readFileSync(source, 'utf8');
    for (const match of text.matchAll(/\[[^\]]+\]\(([^)]+)\)/g)) {
      if (/^[a-z]+:/i.test(match[1])) continue;
      const target = new URL(match[1], source);
      assert.ok(existsSync(target), `${file}: ${match[1]}`);
      if (!target.hash || !target.pathname.endsWith('.md')) continue;
      const destination = readFileSync(target, 'utf8');
      const ids = [...destination.matchAll(/^#{1,6}\s+(.+)$/gm)].map(m => m[1].toLowerCase().replace(/[^\p{L}\p{N}_ -]/gu, '').replaceAll(' ', '-'));
      ids.push(...[...destination.matchAll(/\bid="([^"]+)"/g)].map(m => m[1]));
      assert.ok(ids.includes(decodeURIComponent(target.hash.slice(1))), `${file}: missing fragment ${match[1]}`);
    }
  }
});

test('linked source-document changes cannot skip website verification', () => {
  const workflow = readFileSync(new URL('../../.github/workflows/website.yml', import.meta.url), 'utf8');
  assert.match(workflow, /  pull_request:\n/);
  assert.doesNotMatch(workflow, /^\s+paths(?:-ignore)?:/m);
});

test('privacy notices distinguish host URL processing from application analytics', () => {
  const page = readFileSync(new URL('../app/privacy/page.tsx', import.meta.url), 'utf8');
  const measurement = readFileSync(new URL('../../marketing/MEASUREMENT.md', import.meta.url), 'utf8');
  for (const text of [page, measurement]) {
    assert.match(text, /query string/);
    assert.match(text, /hosting\/security logs/);
  }
  assert.match(page, /no separate analytics or conversion event/);
  assert.match(page, /process or save it before you send/);
});

test('the shared main element clips decoration without breaking sticky descendants', () => {
  const css = readFileSync(new URL('../app/globals.css', import.meta.url), 'utf8');
  assert.match(css, /main\s*\{\s*overflow:\s*clip;/);
  assert.doesNotMatch(css, /main\s*\{[^}]*overflow:\s*(hidden|auto|scroll)/);
});
