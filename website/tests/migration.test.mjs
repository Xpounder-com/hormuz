import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
import { SITE_ROUTES, siteUrl } from '../lib/site.mjs';
import { legacyPage, prepareLegacyExport } from '../scripts/prepare-legacy-export.mjs';
import { createPreviewServer } from '../scripts/serve-preview.mjs';

async function fixture(t) {
  const root = await mkdtemp(path.join(os.tmpdir(), 'hormuz-pages-test-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  for (const route of SITE_ROUTES) {
    await mkdir(path.join(root, route), { recursive: true });
    await writeFile(path.join(root, route, 'index.html'), `<h1>${route}</h1>`);
  }
  await writeFile(path.join(root, '404.html'), '<h1>Not found</h1>');
  await mkdir(path.join(root, 'downloads'));
  await writeFile(path.join(root, 'downloads', 'brief.pdf'), Buffer.from([0, 37, 80, 68, 70, 255]));
  await mkdir(path.join(root, 'demo', 'recordings'));
  await writeFile(path.join(root, 'demo', 'recordings', 'sample.cast'), 'unchanged recording\n');
  return root;
}

async function preview(t, rootPath, basePath = '') {
  const server = createPreviewServer({ rootPath, basePath });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  t.after(() => new Promise(resolve => { server.closeAllConnections(); server.close(resolve); }));
  return `http://127.0.0.1:${server.address().port}`;
}

test('all known old routes use fixed new destinations and retain fragments only', () => {
  assert.equal(SITE_ROUTES.length, 9);
  assert.equal(new Set(SITE_ROUTES).size, SITE_ROUTES.length);
  for (const route of SITE_ROUTES) {
    const html = legacyPage(route);
    assert.ok(html.includes(`<link rel="canonical" href="${siteUrl(route)}">`));
    assert.ok(html.includes(`<noscript><meta http-equiv="refresh" content="0;url=${siteUrl(route)}"></noscript>`));
    assert.doesNotMatch(html, /noindex/);
    assert.ok(html.includes(`<a href="${siteUrl(route)}">`));
    const script = html.match(/<script>(.*?)<\/script>/s)[1];
    for (const hash of ['', '#quickstart', '#https://untrusted.example/']) {
      let destination;
      vm.runInNewContext(script, { window: { location: {
        hash,
        get search() { throw new Error('Private query strings must not be read'); },
        replace(value) { destination = value; },
      } } });
      assert.equal(destination, siteUrl(route) + hash);
      assert.equal(new URL(destination).origin, 'https://usehormuz.github.io');
    }
  }
  assert.throws(() => legacyPage('//untrusted.example/'));
  assert.doesNotMatch(legacyPage('/', { redirect: false }), /<script>|http-equiv="refresh"/);
});

test('legacy publication replaces every page but preserves existing download and recording bytes', async t => {
  const root = await fixture(t);
  const assets = ['downloads/brief.pdf', 'demo/recordings/sample.cast'];
  const originals = await Promise.all(assets.map(asset => readFile(path.join(root, asset))));
  assert.deepEqual(await prepareLegacyExport(root), {
    verdict: 'passed', redirects: 9, target: 'https://usehormuz.github.io/', query_strings_forwarded: false,
  });
  for (const route of SITE_ROUTES) assert.equal(await readFile(path.join(root, route, 'index.html'), 'utf8'), legacyPage(route));
  for (const [index, asset] of assets.entries()) assert.deepEqual(await readFile(path.join(root, asset)), originals[index]);
  assert.doesNotMatch(await readFile(path.join(root, '404.html'), 'utf8'), /<script>|http-equiv="refresh"/);
  assert.match(await readFile(path.join(root, 'robots.txt'), 'utf8'), /Sitemap: https:\/\/usehormuz.github.io\/sitemap.xml/);
});

test('an incomplete export fails before changing any page', async t => {
  const root = await fixture(t);
  await rm(path.join(root, 'privacy', 'index.html'));
  await assert.rejects(prepareLegacyExport(root), { code: 'ENOENT' });
  assert.equal(await readFile(path.join(root, 'index.html'), 'utf8'), '<h1>/</h1>');
});

test('root preview serves / without a redirect loop and handles methods, missing files, and traversal', async t => {
  const root = await fixture(t);
  const origin = await preview(t, root);
  for (const route of SITE_ROUTES) {
    const response = await fetch(origin + route, { redirect: 'manual' });
    assert.equal(response.status, 200);
    assert.equal(response.headers.get('location'), null);
    assert.equal(await response.text(), `<h1>${route}</h1>`);
  }
  const head = await fetch(origin + '/', { method: 'HEAD' });
  assert.equal(head.status, 200);
  assert.equal(await head.text(), '');
  const missing = await fetch(origin + '/missing/', { method: 'HEAD' });
  assert.equal(missing.status, 404);
  assert.equal(await missing.text(), '');
  const post = await fetch(origin + '/', { method: 'POST' });
  assert.equal(post.status, 405);
  assert.equal(post.headers.get('allow'), 'GET, HEAD');
  for (const route of ['/%2e%2e%2foutside', '/%zz', '/not-here/']) assert.equal((await fetch(origin + route)).status, 404);
});

test('legacy preview redirects only the mount point and keeps downloads available under /hormuz/', async t => {
  const root = await fixture(t);
  await prepareLegacyExport(root);
  const origin = await preview(t, root, '/hormuz');
  for (const route of ['/', '/hormuz']) {
    const response = await fetch(origin + route, { redirect: 'manual' });
    assert.equal(response.status, 302);
    assert.equal(response.headers.get('location'), '/hormuz/');
  }
  const page = await fetch(origin + '/hormuz/docs/');
  assert.equal(page.status, 200);
  assert.ok((await page.text()).includes(siteUrl('/docs/')));
  const download = await fetch(origin + '/hormuz/downloads/brief.pdf');
  assert.equal(download.status, 200);
  assert.equal(download.headers.get('content-type'), 'application/pdf');
  assert.deepEqual(Buffer.from(await download.arrayBuffer()), await readFile(path.join(root, 'downloads/brief.pdf')));
  assert.equal((await fetch(origin + '/docs/')).status, 404);
});
