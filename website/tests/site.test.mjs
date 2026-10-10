import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { sitePath, siteUrl, resolveSourceRevision, CONTACT_EMAIL, SOURCE_VERSION, OCI_VERSION, MACOS_VERSION } from '../lib/site.mjs';
import { buildInquiry, campaignSource } from '../lib/contact.mjs';

test('published core and notarized Mac downloads agree with the release record while Windows stays preview', () => {
  // A reviewed source candidate can advance before its public artifacts ship.
  const release = readFileSync(new URL(`../../docs/releases/${SOURCE_VERSION}-notarized-mac.md`, import.meta.url), 'utf8');
  const publishedVersion = release.match(/^# Hormuz (v\d+\.\d+\.\d+):/m)?.[1];
  assert.equal(SOURCE_VERSION, publishedVersion);
  assert.equal(OCI_VERSION, SOURCE_VERSION);
  assert.equal(MACOS_VERSION, SOURCE_VERSION);
  assert.ok(release.includes(`releases/download/${SOURCE_VERSION}/hormuz-${SOURCE_VERSION.slice(1)}-py3-none-any.whl`));
  assert.ok(release.includes(`releases/download/${MACOS_VERSION}/Hormuz-${MACOS_VERSION.slice(1)}-notarized.zip`));
  assert.ok(release.includes(`releases/download/${MACOS_VERSION}/SHA256SUMS.txt`));
  const docs = readFileSync(new URL('../app/docs/page.tsx', import.meta.url), 'utf8');
  assert.match(docs, /Windows development preview/);
  assert.match(docs, /Unsigned, unsupported/);
  assert.ok(docs.includes('releases/download/${MACOS_VERSION}/Hormuz-${MACOS_VERSION.slice(1)}-notarized.zip'));
  assert.ok(docs.includes('releases/download/${MACOS_VERSION}/SHA256SUMS.txt'));
  assert.doesNotMatch(docs, /77d463869f35c5bd|releases\/download\/v1\.3\.0/);
  assert.match(docs, /\/issues\/340/);
  assert.match(docs, /reviewed source candidate on both the gateway and CLI client/);
  assert.match(docs, /published v1\.8\.0 installers do not include these new AI Work commands/);
  const workEntry = readFileSync(new URL('../app/work/page.tsx', import.meta.url), 'utf8');
  assert.ok(workEntry.includes("href={sitePath('/docs/#ai-work')}>AI Work setup path"));
  const workGuide = readFileSync(new URL('../../docs/AI_WORK_AGENT_INTEGRATION.md', import.meta.url), 'utf8');
  assert.match(workGuide, /v1\.8\.0 source, signed Mac, and OCI artifacts do not include these new commands/);
  assert.match(docs, /comparison-table release-downloads/);
  const styles = readFileSync(new URL('../app/globals.css', import.meta.url), 'utf8');
  assert.match(styles, /\.release-downloads\s*\{\s*min-width:\s*720px;/);
});

test('current Mac labels and links derive from the explicit notarized release version', () => {
  for (const file of ['app/page.tsx', 'app/docs/page.tsx', 'app/components/SiteFooter.tsx', 'app/components/CompanionPreview.tsx', 'app/components/SetupExample.tsx', 'lib/customer-questions.mjs']) {
    const source = readFileSync(new URL(`../${file}`, import.meta.url), 'utf8');
    assert.match(source, /MACOS_VERSION/, file);
    assert.doesNotMatch(source, /releases\/(?:download|tag)\/v1\.3\.0|Mac (?:companion )?1\.3\.0/, file);
  }
});

test('native paths and metadata use the dedicated organization root', () => {
  assert.equal(sitePath('/'), '/');
  assert.equal(sitePath('/docs/#quickstart'), '/docs/#quickstart');
  assert.equal(siteUrl('/contact/'), 'https://usehormuz.github.io/contact/');
  assert.throws(() => sitePath('https://example.com'));
  assert.throws(() => sitePath('//example.com'));
});
test('source anchors use a validated immutable build revision and retain the local main fallback', () => {
  const revision = '9af53c79d1671638a57dba9d758482c7d4f88ef8';
  assert.equal(resolveSourceRevision(undefined), 'main');
  assert.equal(resolveSourceRevision(''), 'main');
  assert.equal(resolveSourceRevision(revision), revision);
  for (const invalid of ['main', 'v1.8.0', revision.toUpperCase(), revision.slice(0, 7), `${revision}\n`, ' '.repeat(40), null, 123]) {
    assert.throws(() => resolveSourceRevision(invalid));
  }
  const moduleUrl = new URL('../lib/site.mjs', import.meta.url).href;
  for (const value of ['', revision]) {
    const child = spawnSync(process.execPath, ['--input-type=module', '-e', `import { sourcePath } from ${JSON.stringify(moduleUrl)}; console.log(sourcePath('docs/AI_WORK_AGENT_INTEGRATION.md'));`], { env: { ...process.env, NEXT_PUBLIC_HORMUZ_SOURCE_REVISION: value }, encoding: 'utf8' });
    assert.equal(child.status, 0, child.stderr);
    assert.equal(child.stdout.trim(), `https://github.com/Xpounder-com/hormuz/blob/${value || 'main'}/docs/AI_WORK_AGENT_INTEGRATION.md`);
  }
});
test('shared current-release labels derive from the source version', () => {
  for (const file of ['app/components/SiteFooter.tsx', 'app/components/SetupExample.tsx', 'app/enterprise/page.tsx', 'app/docs/page.tsx', 'app/integrations/page.tsx', 'app/security/page.tsx', 'app/resources/page.tsx', 'app/guides/codex-claude-code-gateway/page.tsx']) {
    const source = readFileSync(new URL(`../${file}`, import.meta.url), 'utf8');
    assert.match(source, /\{SOURCE_VERSION(?:\.slice\(1\))?\}/, file);
  }
});
test('compatibility guidance matches the Chat route while preserving native qualification limits', () => {
  const gateway = readFileSync(new URL('../../hormuz/server.py', import.meta.url), 'utf8');
  assert.match(gateway, /"\/v1\/chat\/completions": \("openai", "codex", True\)/);
  for (const file of ['app/components/SetupExample.tsx', 'lib/customer-questions.mjs']) {
    const source = readFileSync(new URL(`../${file}`, import.meta.url), 'utf8');
    assert.match(source, /AI Work supports OpenAI Responses, OpenAI-compatible Chat Completions at \/v1\/chat\/completions, and Anthropic Messages/, file);
    assert.match(source, /does not expose Ollama’s native \/api\/chat and \/api\/generate routes/, file);
    assert.match(source, /Exact native-client versions, models, streaming, and tool behavior still require qualification/, file);
    assert.doesNotMatch(source, /does not expose (?:\/v1\/chat\/completions|Chat Completions)/, file);
  }
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
    assert.equal(buildInquiry({ name: 'n', workflow: 'w', interest }).subject, 'Hormuz — Engineering AI work');
  }
  assert.equal(buildInquiry({ name: 'n', workflow: 'w', interest: 'enterprise' }).subject, 'Hormuz — Appliance');
  assert.equal(buildInquiry({ name: 'n', workflow: 'w', interest: 'pro' }).subject, 'Hormuz — Cloud workspace');
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
test('published AI Work proof matches the executed receipt and keeps its conditions explicit', () => {
  const published = readFileSync(new URL('../public/downloads/ai-work-proof.json', import.meta.url));
  const executed = readFileSync(new URL('../../docs/evidence/ai-work-functional/receipt.json', import.meta.url));
  assert.deepEqual(published, executed, 'Copy the regenerated execution receipt before publishing');
  const proof = JSON.parse(published);
  assert.equal(proof.schema_id, 'hormuz.ai-work-proof');
  assert.equal(proof.schema_version, 1);
  assert.equal(proof.conditions.provider, 'loopback_synthetic_fixture');
  assert.equal(proof.conditions.real_provider_calls, 0);
  assert.equal(proof.conditions.real_payments, 0);
  assert.equal(proof.conditions.production_quality_validated, false);
  assert.equal(proof.conditions.customer_savings_validated, false);
  assert.ok(proof.checks.length > 0 && proof.checks.every(check => check.passed === true));
  assert.match(proof.source_commit, /^[a-f0-9]{40}$/);
  const sourceFiles = ['hormuz/work_runtime.py', 'hormuz/work_learning.py', 'hormuz/work_accounting.py', 'hormuz/work_provider_costs.py', 'hormuz/work_gateway.py', 'hormuz/work_workflow.py', 'hormuz/work_workflow_http.py', 'hormuz/work_http.py', 'hormuz/work_billing.py', 'hormuz/work_activation.py', 'hormuz/work_recovery.py', 'hormuz/work_pages.py', 'hormuz/work.css', 'hormuz/work_client.py', 'hormuz/commands/work.py', 'hormuz/config.py', 'hormuz/_config_input.py', 'hormuz/_config_work.py', 'hormuz/server.py', 'hormuz/usage.py', 'hormuz/_hosted_server.py', 'hormuz/_hosted_state.py', 'hormuz/_hosted_config.py', 'hormuz/_hosted_backup.py', 'hormuz/hosted.py', 'tools/ai_work_proof.py', 'tools/ai_work_browser_fixture.py', 'website/scripts/ai-work-browser-qa.mjs'];
  assert.deepEqual(Object.keys(proof.source_files).sort(), [...sourceFiles].sort());
  for (const path of sourceFiles) {
    const digest = proof.source_files[path];
    assert.match(digest, /^[a-f0-9]{64}$/);
    const actual = createHash('sha256').update(readFileSync(new URL(`../../${path}`, import.meta.url))).digest('hex');
    assert.equal(digest, actual, `${path}: regenerate the receipt after changing executed source`);
  }
  const forbidden = new Set(['prompt', 'messages', 'response_body', 'content', 'api_key', 'access_token', 'authorization']);
  function checkMetadata(value) {
    if (!value || typeof value !== 'object') return;
    for (const [key, child] of Object.entries(value)) {
      assert.equal(forbidden.has(key.toLowerCase()), false, key);
      checkMetadata(child);
    }
  }
  checkMetadata(proof.covered_work);
  for (const [publishedName, originalName] of [['ai-work-demo.webm', 'AI_WORK_DEMO.webm'], ['ai-work-desktop.png', 'AI_WORK_DESKTOP.png'], ['ai-work-browser-qa.json', 'AI_WORK_BROWSER_QA.json']]) {
    const publishedMedia = readFileSync(new URL(`../public/demo/${publishedName}`, import.meta.url));
    const originalMedia = readFileSync(new URL(`../../docs/evidence/ai-work-functional/${originalName}`, import.meta.url));
    assert.ok(publishedMedia.equals(originalMedia), `${publishedName}: public media must match the actual validation artifact`);
  }
  const browser = JSON.parse(readFileSync(new URL('../public/demo/ai-work-browser-qa.json', import.meta.url), 'utf8'));
  assert.equal(browser.real_provider_calls, 0);
  assert.equal(browser.real_payments, 0);
  assert.equal(browser.external_network_calls, 0);
  assert.deepEqual(browser.browser_errors, []);
  assert.deepEqual(browser.failed_requests, []);
  assert.equal(browser.horizontal_overflow, false);
  const browserSources = sourceFiles;
  assert.deepEqual(Object.keys(browser.source_files).sort(), [...browserSources].sort());
  for (const path of browserSources) assert.equal(browser.source_files[path], createHash('sha256').update(readFileSync(new URL(`../../${path}`, import.meta.url))).digest('hex'), `${path}: rerun browser qualification after source changes`);

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
  assert.match(page, /Optional Google Analytics and X Ads measurement each stay off until you allow that service/);
  assert.match(page, /An earlier choice to allow X does not allow Google Analytics/);
  assert.match(page, /pricing and inquiry-link clicks including fixed Cloud or managed-site interest labels/);
  assert.match(page, /you must make a new choice under this notice before Analytics starts/);
  const choices = readFileSync(new URL('../app/components/AdConsent.tsx', import.meta.url), 'utf8');
  assert.match(choices, /install and pricing links, Cloud or managed-site inquiry links/);
  assert.match(page, /Safari and browsers on iPhone and iPad do not load X Ads code/);
  assert.match(page, /process or save it before you send/);
});

test('the application form retains a browser-native submission fallback', () => {
  const form = readFileSync(new URL('../app/components/LeadForm.tsx', import.meta.url), 'utf8');
  assert.match(form, /<form[^>]+action=\{endpoint\}[^>]+method="post"/);
  assert.match(form, /<input type="hidden" name="_subject"/);
  assert.match(form, /form will submit directly to Formspree/);
});

test('the shared main element clips decoration without breaking sticky descendants', () => {
  const css = readFileSync(new URL('../app/globals.css', import.meta.url), 'utf8');
  assert.match(css, /main\s*\{\s*overflow:\s*clip;/);
  assert.doesNotMatch(css, /main\s*\{[^}]*overflow:\s*(hidden|auto|scroll)/);
});
