import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { validateSourcePin } from './verify-source-pin.mjs';

export const LIVE_ORIGIN = 'https://usehormuz.github.io';
export const LIVE_ROUTES = Object.freeze(['/', '/docs/', '/demo/', '/integrations/', '/enterprise/', '/security/', '/resources/', '/contact/', '/privacy/']);
export const LIVE_DOWNLOADS = Object.freeze(['hormuz-overview.pdf', 'hormuz-pilot-brief.pdf', 'hormuz-trust-brief.pdf', 'hormuz-buyer-briefing.pptx']);

export async function verifyLiveSite(sourcePin, fetcher = fetch) {
  const revision = validateSourcePin(sourcePin);
  async function request(route) {
    let response;
    try {
      response = await fetcher(`${LIVE_ORIGIN}${route}`, {
        redirect: 'error', cache: 'no-store', signal: AbortSignal.timeout(15_000),
      });
    } catch {
      throw new Error(`Public request failed: ${route}`);
    }
    assert.equal(response.status, 200, `Expected HTTP 200: ${route}`);
    return response;
  }

  const manifest = await request('/site-source.json');
  let publishedRevision;
  try { publishedRevision = validateSourcePin(await manifest.json()); }
  catch { throw new Error('Invalid public source manifest'); }
  assert.equal(publishedRevision, revision, 'Published source revision does not match the reviewed pin');

  for (const route of LIVE_ROUTES) {
    const html = await (await request(route)).text();
    assert.ok(html.includes(`<link rel="canonical" href="${LIVE_ORIGIN}${route}"`), `Canonical mismatch: ${route}`);
    assert.equal((html.match(/<h1[ >]/g) || []).length, 1, `Expected one heading: ${route}`);
  }
  for (const name of LIVE_DOWNLOADS) {
    const bytes = Buffer.from(await (await request(`/downloads/${name}`)).arrayBuffer());
    const signature = name.endsWith('.pdf') ? Buffer.from('%PDF-') : Buffer.from([0x50, 0x4b, 0x03, 0x04]);
    assert.ok(bytes.subarray(0, signature.length).equals(signature), `Invalid download: ${name}`);
  }
  const robots = await (await request('/robots.txt')).text();
  assert.match(robots, /^Allow: \/$/m, 'Robots must allow the root site');
  assert.ok(robots.includes(`Sitemap: ${LIVE_ORIGIN}/sitemap.xml`), 'Robots sitemap mismatch');
  const sitemap = await (await request('/sitemap.xml')).text();
  for (const route of LIVE_ROUTES) assert.ok(sitemap.includes(`<loc>${LIVE_ORIGIN}${route}</loc>`), `Sitemap mismatch: ${route}`);
  return { verdict: 'passed', source_revision: revision, pages: LIVE_ROUTES.length, downloads: LIVE_DOWNLOADS.length };
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  try {
    const sourcePin = JSON.parse(await readFile('site-source.json', 'utf8'));
    console.log(JSON.stringify(await verifyLiveSite(sourcePin), null, 2));
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
