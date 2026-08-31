import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { LEGACY_BASE_PATH, SITE_ROUTES, siteUrl } from '../lib/site.mjs';

const defaultRoot = fileURLToPath(new URL('../out/', import.meta.url));

export function legacyPage(route, { redirect = true } = {}) {
  if (!SITE_ROUTES.includes(route)) throw new Error('Unknown website route');
  const target = siteUrl(route);
  // Only a fixed, known destination and the fragment are forwarded. Query
  // strings can contain private text, so they never cross to the new host.
  const navigation = redirect ? `<noscript><meta http-equiv="refresh" content="0;url=${target}"></noscript>
<script>window.location.replace(${JSON.stringify(target)} + window.location.hash);</script>` : '';
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hormuz has moved</title>${redirect ? '' : '<meta name="robots" content="noindex, follow">'}<link rel="canonical" href="${target}">
${navigation}
<style>body{margin:0;background:#f5f5f0;color:#14252b;font:1.15rem/1.6 system-ui,sans-serif}main{max-width:42rem;margin:15vh auto;padding:2rem}a{color:#06675f;text-underline-offset:.2em}a:focus-visible{outline:3px solid #06675f;outline-offset:5px}</style>
</head><body><main id="content"><h1>Hormuz has a new home.</h1>
<p>The open-source project, documentation, and enterprise evaluation are now at <a href="${target}">${target}</a>.</p>
<p>${redirect ? 'This page will take you there automatically. You can also follow the link above.' : 'Follow the link above to visit the new website.'}</p>
<p>Existing downloads remain available at their original addresses.</p></main></body></html>\n`;
}

export async function prepareLegacyExport(rootPath = defaultRoot) {
  const root = path.resolve(rootPath);
  const pages = SITE_ROUTES.map(route => ({ route, file: path.join(root, route, 'index.html') }));
  // Fail before changing any page if the complete verified export is absent.
  await Promise.all(pages.map(({ file }) => readFile(file)));
  await Promise.all(pages.map(({ route, file }) => writeFile(file, legacyPage(route))));
  await writeFile(path.join(root, '404.html'), legacyPage('/', { redirect: false }));
  await writeFile(path.join(root, 'robots.txt'), `User-agent: *\nAllow: ${LEGACY_BASE_PATH}/\nSitemap: ${siteUrl('/sitemap.xml')}\n`);
  // Downloads, demo recordings, and all other static assets are left intact.
  return { verdict: 'passed', redirects: pages.length, target: siteUrl('/'), query_strings_forwarded: false };
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  console.log(JSON.stringify(await prepareLegacyExport(), null, 2));
}
