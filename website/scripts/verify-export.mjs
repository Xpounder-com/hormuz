import { readdir, readFile, stat } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import assert from 'node:assert/strict';
import { BASE_PATH, SITE_ORIGIN, SITE_ROUTES, siteUrl } from '../lib/site.mjs';

const siteRoot = fileURLToPath(new URL('../', import.meta.url));
const out = path.join(siteRoot, 'out');
const repo = path.resolve(siteRoot, '..');
const routes = SITE_ROUTES;
const pages = new Map();
const failures = [];
const sourceLinks = new Set();
let localLinks = 0;
const decode = text => text.replaceAll('&amp;', '&').replaceAll('&#x27;', "'").replaceAll('&quot;', '"');
for (const route of routes) {
  const html = await readFile(path.join(out, route, 'index.html'), 'utf8');
  pages.set(route, html);
  if (!html.includes(`href="${siteUrl(route)}"`)) failures.push(`${route}: missing correct canonical`);
  if (!html.includes('property="og:image"') || !html.includes(siteUrl('/og.png'))) failures.push(`${route}: missing canonical social image`);
  if ((html.match(/<h1[ >]/g) || []).length !== 1) failures.push(`${route}: expected one h1`);
  if (!html.includes('id="content"')) failures.push(`${route}: missing skip-link target`);
  for (const stale of ['xpounder-com.github.io', 'mehrdadz@neralint.io', 'hormuz-control.mehrdadz.chatgpt.site', 'POLICY_ADMIN_API.md', 'USAGE_ADMIN_API.md', 'COMPATIBILITY.md', 'THREAT_MODEL.md']) if (html.includes(stale)) failures.push(`${route}: stale target ${stale}`);
}
for (const [route, html] of pages) {
  for (const match of html.matchAll(/<(?:a|link|script|img|source)\b[^>]*?\b(?:href|src)="([^"]+)"/g)) {
    const href = decode(match[1]);
    if (/^(mailto:|data:)/.test(href)) continue;
    const url = new URL(href, siteUrl(route));
    if (url.origin !== SITE_ORIGIN) {
      if (url.hostname === 'github.com' && url.pathname.startsWith('/Xpounder-com/hormuz/blob/main/')) sourceLinks.add(decodeURIComponent(url.pathname.slice('/Xpounder-com/hormuz/blob/main/'.length)));
      continue;
    }
    if (!url.pathname.startsWith(`${BASE_PATH}/`)) { failures.push(`${route}: escaped site basePath: ${href}`); continue; }
    const target = decodeURIComponent(url.pathname.slice(BASE_PATH.length));
    const disk = path.join(out, target, target.endsWith('/') ? 'index.html' : '');
    try { if (!(await stat(disk)).isFile()) throw new Error('not a file'); } catch { failures.push(`${route}: missing local target ${href}`); }
    if (url.hash && pages.has(target)) {
      const id = decodeURIComponent(url.hash.slice(1));
      if (!pages.get(target).includes(`id="${id}"`)) failures.push(`${route}: missing fragment ${href}`);
    }
    localLinks++;
  }
}
for (const source of sourceLinks) {
  try { if (!(await stat(path.join(repo, source))).isFile()) throw new Error('not a file'); } catch { failures.push(`Missing GitHub source target: ${source}`); }
}
const sitemap = await readFile(path.join(out, 'sitemap.xml'), 'utf8');
for (const route of routes) if (!sitemap.includes(`<loc>${siteUrl(route)}</loc>`)) failures.push(`Sitemap missing ${route}`);
for (const asset of ['icon.svg', 'og.png', 'robots.txt', '.nojekyll', '404.html']) await stat(path.join(out, asset));
const robots = await readFile(path.join(out, 'robots.txt'), 'utf8');
assert.match(robots, /^Allow: \/$/m);
assert.ok(robots.includes(`Sitemap: ${siteUrl('/sitemap.xml')}`));
const contactSource = await readFile(path.join(siteRoot, 'app/components/ContactForm.tsx'), 'utf8');
assert.ok(contactSource.includes('Nothing has been sent.'));
assert.doesNotMatch(contactSource, /fetch\(|sendBeacon|localStorage|sessionStorage/);
assert.ok((await readdir(path.join(out, 'downloads'))).length >= 4, 'Missing buyer downloads');
if (failures.length) { console.error(failures.join('\n')); process.exitCode = 1; }
else console.log(JSON.stringify({ verdict: 'passed', pages: pages.size, local_link_occurrences: localLinks, source_targets: sourceLinks.size, tracking: 'off', contact: 'local_email_draft_only' }, null, 2));
