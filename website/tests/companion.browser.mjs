// Optional rendered regression check. Uses an already-installed Playwright
// runtime; the normal website unit suite needs no browser dependency.
import test from 'node:test';
import assert from 'node:assert/strict';

const { chromium, webkit } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = (process.argv[2] || 'http://127.0.0.1:3100').replace(/\/$/, '');
const targets = (process.env.COMPANION_BROWSERS || 'chromium,webkit').split(',');
for (const name of targets) {
  assert.ok(['chrome', 'chromium', 'webkit'].includes(name), `Unknown browser: ${name}`);
  const browser = await (name === 'webkit' ? webkit : chromium).launch({ headless: true, ...(name === 'chrome' ? { channel: 'chrome' } : {}) });
  try {
    for (const width of [1440, 390, 320]) await test(`${name}: companion at ${width}px`, async () => {
      const context = await browser.newContext({ viewport: { width, height: width > 1000 ? 1000 : 844 }, reducedMotion: width === 320 ? 'reduce' : 'no-preference' });
      try {
        const page = await context.newPage();
        page.setDefaultTimeout(10_000);
        const errors = [], tracking = [];
        page.on('pageerror', error => errors.push(error.message));
        page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
        page.on('requestfailed', request => errors.push(`Failed request: ${request.url()}`));
        page.on('request', request => { if (/googletagmanager|google-analytics|analytics\.twitter|ads-twitter/.test(request.url())) tracking.push(request.url()); });
        const card = page.getByRole('region', { name: 'Hormuz example controls', exact: true });
        const gear = page.getByRole('button', { name: 'Settings & setup: open Hormuz controls', exact: true });
        const launch = page.getByRole('button', { name: 'Explore the controls', exact: false });
        const waitPanel = async () => {
          await card.waitFor({ state: 'visible' });
          await page.waitForFunction(() => document.activeElement === document.querySelector('.companion-card'));
        };
        const close = async () => { await card.press('Escape'); await card.waitFor({ state: 'detached' }); };
        const consent = () => page.evaluate(() => ['hormuz.x-ads-consent.v1', 'hormuz.analytics-consent.v1'].map(key => localStorage.getItem(key)));

        await page.goto(`${base}/?qa=1#companion`, { waitUntil: 'networkidle' });
        assert.match(await page.title(), /Hormuz.*AI Gateway/);
        assert.equal(await page.locator('main').count(), 1);
        assert.equal(await page.locator('nextjs-portal').count(), 0);
        if (width === 1440) {
          // A first-visit prompt must not conceal the panel or turn opening a
          // product demo into a saved advertising/analytics consent decision.
          await page.locator('.ad-consent').waitFor({ state: 'visible' });
          assert.deepEqual(await consent(), [null, null]);
          await launch.click();
          await waitPanel();
          await page.locator('.ad-consent').waitFor({ state: 'detached' });
          assert.deepEqual(await consent(), [null, null]);
          await close();
        } else await page.getByRole('button', { name: 'Decline', exact: true }).click();

        const cost = page.getByRole('button', { name: 'Cost details: view estimated cost', exact: true });
        await cost.locator('.companion-view-label').click();
        await waitPanel();
        assert.match(await card.innerText(), /\$73.00 this month/);
        if (width > 620) await gear.click();
        else await card.getByRole('button', { name: 'Open Hormuz controls', exact: true }).click();
        await waitPanel();
        assert.match(await card.innerText(), /Your AI, within reach/);
        assert.ok(await card.locator('.companion-card-content').evaluate(element => element.scrollHeight <= element.clientHeight + 1), 'All settings rows should fit');

        await card.getByRole('button', { name: /Connection Session/ }).click();
        await waitPanel();
        await card.getByRole('button', { name: 'Preview expired session', exact: true }).click();
        assert.equal(await page.locator('.companion-reading').count(), 0);
        await card.getByRole('button', { name: 'Restore connected example', exact: true }).click();
        assert.equal(await page.locator('.companion-reading').count(), 3);
        await card.getByRole('button', { name: '← All controls', exact: true }).click();
        await card.getByRole('button', { name: /Appearance Size/ }).click();
        await waitPanel();
        for (const size of ['100%', '125%', '150%']) {
          const option = card.getByRole('button', { name: size, exact: true });
          await option.click();
          assert.equal(await option.getAttribute('aria-pressed'), 'true');
          assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'No page overflow');
        }
        if (width === 320) assert.equal(await card.evaluate(element => getComputedStyle(element).animationName), 'none');
        await close();

        for (const name of ['Cost details: view estimated cost', 'Token usage: view tokens', 'Request activity: view requests']) {
          const button = page.getByRole('button', { name, exact: true });
          await button.focus();
          await button.press('Enter');
          await waitPanel();
          await close();
          await page.waitForFunction(label => document.activeElement?.getAttribute('aria-label') === label, name);
        }
        // Folding must focus the restore tab regardless of which entry point
        // opened the panel; an external launcher is not a folded-widget target.
        for (const opener of [gear, launch]) {
          await opener.click();
          await waitPanel();
          await card.getByRole('button', { name: /Appearance Size/ }).click();
          await card.getByRole('button', { name: 'Fold the widget', exact: true }).click();
          await card.waitFor({ state: 'detached' });
          await page.waitForFunction(() => document.activeElement === document.querySelector('.companion-peek'));
          await page.getByRole('button', { name: 'Show Hormuz widget', exact: true }).press('Enter');
          await waitPanel();
          await close();
          await page.waitForFunction(() => document.activeElement === document.querySelector('.companion-gear'));
        }
        await launch.click();
        await waitPanel();
        await page.getByRole('button', { name: 'Privacy choices', exact: true }).click();
        await card.waitFor({ state: 'detached' });
        await page.locator('.ad-consent').waitFor({ state: 'visible' });
        assert.equal(await page.locator('.companion-edge').getAttribute('data-folded'), 'false');
        assert.equal(await gear.evaluate(element => document.activeElement === element), false, 'Outside click must not steal focus');
        for (const checkbox of await page.locator('.ad-consent input[type="checkbox"]').all()) assert.equal(await checkbox.isChecked(), false);
        if (width === 1440) assert.deepEqual(await consent(), [null, null]);
        assert.deepEqual(errors, []);
        assert.deepEqual(tracking, []);
      } finally { await context.close(); }
    });
  } finally { await browser.close(); }
}
