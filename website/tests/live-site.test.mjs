import test from 'node:test';
import assert from 'node:assert/strict';
import { SITE_ROUTES, SITE_ORIGIN } from '../lib/site.mjs';
import { LIVE_ORIGIN, LIVE_ROUTES, LIVE_DOWNLOADS, LIVE_SOURCE_LINKS, verifyLiveSite } from '../deployment/verify-live-site.mjs';

const pin = { repository: 'Xpounder-com/hormuz', revision: '9af53c79d1671638a57dba9d758482c7d4f88ef8' };

function publishedSite() {
  const bodies = new Map([
    ['/site-source.json', JSON.stringify(pin)],
    ['/robots.txt', `User-agent: *\nAllow: /\nSitemap: ${LIVE_ORIGIN}/sitemap.xml\n`],
    ['/sitemap.xml', LIVE_ROUTES.map(route => `<loc>${LIVE_ORIGIN}${route}</loc>`).join('')],
  ]);
  for (const route of LIVE_ROUTES) bodies.set(route, `<link rel="canonical" href="${LIVE_ORIGIN}${route}"/><h1>Hormuz</h1>`);
  bodies.set('/demo/', bodies.get('/demo/') + '<section id="work-demo"><video src="/demo/ai-work-demo.webm"></video></section>');
  bodies.set('/evidence/', bodies.get('/evidence/') + '<section id="work-proof"><a href="/downloads/ai-work-proof.json">Receipt</a></section>');
  for (const [route, sources] of Object.entries(LIVE_SOURCE_LINKS)) bodies.set(route, bodies.get(route) + sources.map(source => `<a href="https://github.com/${pin.repository}/blob/${pin.revision}/${source}">Source</a>`).join(''));
  bodies.set('/docs/', bodies.get('/docs/') + `<pre><code>python -m pip install 'hormuz[client,context] @ git+https://github.com/${pin.repository}.git@${pin.revision}'</code></pre>`);
  for (const name of LIVE_DOWNLOADS) bodies.set(`/downloads/${name}`, name.endsWith('.pdf') ? '%PDF-fixture' : name.endsWith('.json') ? JSON.stringify({ schema_id: 'hormuz.ai-work-proof', schema_version: 1, conditions: { real_provider_calls: 0, real_payments: 0, customer_savings_validated: false, production_quality_validated: false }, checks: [{ check: 'synthetic_verifier_fixture', passed: true }] }) : Buffer.from([0x50, 0x4b, 0x03, 0x04, 0]));
  const requests = [];
  return {
    bodies, requests,
    fetcher: async (url, options) => {
      const parsed = new URL(url);
      assert.equal(parsed.origin, LIVE_ORIGIN);
      assert.equal(parsed.search, '');
      assert.equal(options.redirect, 'error');
      assert.equal(options.cache, 'no-store');
      assert.ok(options.signal instanceof AbortSignal);
      requests.push(parsed.pathname);
      return new Response(bodies.get(parsed.pathname) ?? 'Missing', { status: bodies.has(parsed.pathname) ? 200 : 404 });
    },
  };
}

test('post-deploy verification checks the pinned source, all routes, metadata, and five downloads', async () => {
  assert.equal(LIVE_ORIGIN, SITE_ORIGIN);
  assert.deepEqual(LIVE_ROUTES, SITE_ROUTES);
  const site = publishedSite();
  assert.deepEqual(await verifyLiveSite(pin, site.fetcher), { verdict: 'passed', source_revision: pin.revision, pages: 16, downloads: 5 });
  assert.equal(site.requests.length, 24);
});

test('a stale or invalid deployed pin fails before any page is accepted', async () => {
  for (const body of [JSON.stringify({ ...pin, revision: 'a'.repeat(40) }), JSON.stringify({ ...pin, repository: 'other/project' }), '<html>Not JSON</html>']) {
    const site = publishedSite();
    site.bodies.set('/site-source.json', body);
    await assert.rejects(verifyLiveSite(pin, site.fetcher));
    assert.deepEqual(site.requests, ['/site-source.json']);
  }
});
test('missing, mutable, or stale source anchors cannot satisfy exact-pin publication checks', async () => {
  for (const [route, sources] of Object.entries(LIVE_SOURCE_LINKS)) {
    for (const linkedRevision of ['', 'main', 'a'.repeat(40)]) {
      const site = publishedSite();
      const expected = `https://github.com/${pin.repository}/blob/${pin.revision}/${sources[0]}`;
      site.bodies.set(route, site.bodies.get(route).replace(expected, linkedRevision ? expected.replace(pin.revision, linkedRevision) : '#missing-source'));
      await assert.rejects(verifyLiveSite(pin, site.fetcher), /source link does not match/);
    }
  }
  const site = publishedSite();
  site.bodies.set('/security/', site.bodies.get('/security/') + '<a href="https://github.com/Xpounder-com/hormuz/blob/main/SECURITY.md">Security</a>');
  await assert.rejects(verifyLiveSite(pin, site.fetcher), /source link does not match/);
  for (const reference of ['main', 'v1.8.0', 'a'.repeat(40)]) {
    const installation = publishedSite();
    installation.bodies.set('/docs/', installation.bodies.get('/docs/').replace(`hormuz.git@${pin.revision}`, `hormuz.git@${reference}`));
    await assert.rejects(verifyLiveSite(pin, installation.fetcher), /candidate installation does not match/);
  }
});

test('missing routes, wrong canonicals, HTML downloads, and incomplete metadata fail verification', async () => {
  for (const [route, replacement] of [
    ['/docs/', undefined],
    ['/plans/', undefined],
    ['/workspace/', undefined],
    ['/guides/team-ai-budgets/', undefined],
    ['/guides/codex-claude-code-gateway/', '<h1>Guide</h1><link rel="canonical" href="https://wrong.example/"/>'],
    ['/enterprise/', '<h1>Hormuz</h1><link rel="canonical" href="https://wrong.example/"/>'],
    ['/downloads/hormuz-overview.pdf', '<html>Error</html>'],
    ['/downloads/hormuz-appliance-brief.pdf', undefined],
    ['/downloads/hormuz-buyer-briefing.pptx', '<html>Error</html>'],
    ['/downloads/ai-work-proof.json', '<html>Error</html>'],
    ['/downloads/ai-work-proof.json', '{"schema_id":"invented-proof"}'],
    ['/robots.txt', 'User-agent: *\nDisallow: /'],
    ['/sitemap.xml', `<loc>${LIVE_ORIGIN}/</loc>`],
  ]) {
    const site = publishedSite();
    if (replacement === undefined) site.bodies.delete(route); else site.bodies.set(route, replacement);
    await assert.rejects(verifyLiveSite(pin, site.fetcher));
  }
});

test('redirects and network failures cannot be accepted as a successful publication', async () => {
  await assert.rejects(verifyLiveSite(pin, async () => new Response('', { status: 302 })), /Expected HTTP 200/);
  await assert.rejects(verifyLiveSite(pin, async () => { throw new Error('network detail is not emitted'); }), /Public request failed: \/site-source\.json/);
});


test('configured gateway destination is checked without probing the private backend', async () => {
  const site = publishedSite();
  const options = { dashboardOrigin: 'https://gateway.example.test' };
  await assert.rejects(verifyLiveSite(pin, site.fetcher, options), /configured gateway/);
  site.bodies.set('/work/', site.bodies.get('/work/') + '<a href="https://gateway.example.test/work">Open AI Work</a>');
  assert.equal((await verifyLiveSite(pin, site.fetcher, options)).verdict, 'passed');
  for (const origin of ['http://gateway.example.test', 'https://user:pass@gateway.example.test', 'https://gateway.example.test/private', 'https://usehormuz.github.io']) {
    await assert.rejects(verifyLiveSite(pin, site.fetcher, { dashboardOrigin: origin }));
  }
  assert.ok(site.requests.every(route => !route.startsWith('https://gateway')));
});

test('a generic demo or missing proof link cannot satisfy work publication checks', async () => {
  for (const route of ['/demo/', '/evidence/']) {
    const site = publishedSite();
    const sources = (LIVE_SOURCE_LINKS[route] || []).map(source => `<a href="https://github.com/${pin.repository}/blob/${pin.revision}/${source}">Source</a>`).join('');
    site.bodies.set(route, `<link rel="canonical" href="${LIVE_ORIGIN}${route}"/><h1>Hormuz</h1>${sources}`);
    await assert.rejects(verifyLiveSite(pin, site.fetcher), /missing the/);
  }
});
