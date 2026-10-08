import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { validateSourcePin } from './verify-source-pin.mjs';

export const LIVE_ORIGIN = 'https://usehormuz.github.io';
export const LIVE_ROUTES = Object.freeze(['/', '/plans/', '/docs/', '/demo/', '/integrations/', '/enterprise/', '/security/', '/resources/', '/contact/', '/privacy/', '/brand/', '/workspace/', '/work/', '/evidence/', '/guides/team-ai-budgets/', '/guides/codex-claude-code-gateway/']);
export const LIVE_DOWNLOADS = Object.freeze(['hormuz-overview.pdf', 'hormuz-appliance-brief.pdf', 'hormuz-trust-brief.pdf', 'hormuz-buyer-briefing.pptx', 'ai-work-proof.json']);

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
    if (name === 'ai-work-proof.json') {
      let proof;
      try { proof = JSON.parse(bytes.toString('utf8')); } catch { throw new Error(`Invalid download: ${name}`); }
      assert.equal(proof.schema_id, 'hormuz.ai-work-proof', 'Invalid AI Work receipt schema');
      assert.equal(proof.schema_version, 1, 'Invalid AI Work receipt version');
      assert.equal(proof.conditions?.real_provider_calls, 0, 'Functional proof must declare zero real-provider calls');
      assert.equal(proof.conditions?.real_payments, 0, 'Functional proof must declare zero payments');
      assert.equal(proof.conditions?.customer_savings_validated, false, 'Functional proof must not claim customer savings');
      assert.equal(proof.conditions?.production_quality_validated, false, 'Functional proof must not claim production quality');
      assert.ok(Array.isArray(proof.checks) && proof.checks.length > 0 && proof.checks.every(check => check.passed === true), 'Functional proof checks failed or absent');
      continue;
    }
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
