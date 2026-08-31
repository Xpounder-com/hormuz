import test from 'node:test';
import assert from 'node:assert/strict';
import { SITE_ROUTES, SITE_ORIGIN } from '../lib/site.mjs';
import { LIVE_ORIGIN, LIVE_ROUTES, LIVE_DOWNLOADS, verifyLiveSite } from '../deployment/verify-live-site.mjs';

const pin = { repository: 'Xpounder-com/hormuz', revision: '9af53c79d1671638a57dba9d758482c7d4f88ef8' };

function publishedSite() {
  const bodies = new Map([
    ['/site-source.json', JSON.stringify(pin)],
    ['/robots.txt', `User-agent: *\nAllow: /\nSitemap: ${LIVE_ORIGIN}/sitemap.xml\n`],
    ['/sitemap.xml', LIVE_ROUTES.map(route => `<loc>${LIVE_ORIGIN}${route}</loc>`).join('')],
  ]);
  for (const route of LIVE_ROUTES) bodies.set(route, `<link rel="canonical" href="${LIVE_ORIGIN}${route}"/><h1>Hormuz</h1>`);
  for (const name of LIVE_DOWNLOADS) bodies.set(`/downloads/${name}`, name.endsWith('.pdf') ? '%PDF-fixture' : Buffer.from([0x50, 0x4b, 0x03, 0x04, 0]));
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

test('post-deploy verification checks the pinned source, all routes, metadata, and four downloads', async () => {
  assert.equal(LIVE_ORIGIN, SITE_ORIGIN);
  assert.deepEqual(LIVE_ROUTES, SITE_ROUTES);
  const site = publishedSite();
  assert.deepEqual(await verifyLiveSite(pin, site.fetcher), { verdict: 'passed', source_revision: pin.revision, pages: 9, downloads: 4 });
  assert.equal(site.requests.length, 16);
});

test('a stale or invalid deployed pin fails before any page is accepted', async () => {
  for (const body of [JSON.stringify({ ...pin, revision: 'a'.repeat(40) }), JSON.stringify({ ...pin, repository: 'other/project' }), '<html>Not JSON</html>']) {
    const site = publishedSite();
    site.bodies.set('/site-source.json', body);
    await assert.rejects(verifyLiveSite(pin, site.fetcher));
    assert.deepEqual(site.requests, ['/site-source.json']);
  }
});

test('missing routes, wrong canonicals, HTML downloads, and incomplete metadata fail verification', async () => {
  for (const [route, replacement] of [
    ['/docs/', undefined],
    ['/enterprise/', '<h1>Hormuz</h1><link rel="canonical" href="https://wrong.example/"/>'],
    ['/downloads/hormuz-overview.pdf', '<html>Error</html>'],
    ['/downloads/hormuz-buyer-briefing.pptx', '<html>Error</html>'],
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
