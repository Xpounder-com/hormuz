/** Actual fixture journey. Browser plugin not available; use installed Playwright.
 * Set HORMUZ_QA_PYTHON and HORMUZ_QA_PLAYWRIGHT_MODULE to installed runtimes.
 * Every browser request is fulfilled from the local fixture, including Stripe.
 */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = fileURLToPath(new URL('../../', import.meta.url));
const python = process.env.HORMUZ_QA_PYTHON || 'python3';
const modulePath = process.env.HORMUZ_QA_PLAYWRIGHT_MODULE;
if (!modulePath) throw new Error('Set HORMUZ_QA_PLAYWRIGHT_MODULE to installed Playwright index.mjs');
const { chromium } = await import(pathToFileURL(modulePath).href);
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'hormuz-browser-qa-'));
const fixturePath = path.join(temporary, 'fixture.json');
const processFixture = spawn(python, ['tools/ai_work_browser_fixture.py', fixturePath], { cwd: root, stdio: ['pipe', 'pipe', 'pipe'] });
const backendAgent = new http.Agent({ keepAlive: true, maxSockets: 4, maxTotalSockets: 4, maxFreeSockets: 4, timeout: 10000 });
let stderr = ''; processFixture.stderr.on('data', value => { stderr = (stderr + value.toString()).slice(-65536); });
let pending = ''; const events = []; const waiters = [];
processFixture.stdout.on('data', value => {
  pending += value.toString();
  while (pending.includes('\n')) {
    const end = pending.indexOf('\n'); const line = pending.slice(0, end); pending = pending.slice(end + 1);
    const event = JSON.parse(line); const waiter = waiters.shift(); waiter ? waiter.resolve(event) : events.push(event);
  }
});
async function nextEvent() {
  if (events.length) return events.shift();
  return new Promise((resolve, reject) => {
    const waiter = { resolve: value => { clearTimeout(timer); resolve(value); } };
    const timer = setTimeout(() => {
      const index = waiters.indexOf(waiter); if (index >= 0) waiters.splice(index, 1);
      reject(new Error('Fixture timeout: ' + stderr.slice(-1000)));
    }, 20000);
    timer.unref(); waiters.push(waiter);
  });
}
async function control(operation, values = {}) {
  processFixture.stdin.write(JSON.stringify({ operation, ...values }) + '\n'); const event = await nextEvent();
  assert.equal(event.event, operation); assert.equal(event.ok, true, event.error); return event;
}
function backend(fixture, requestPath, method, headers, body) {
  return new Promise((resolve, reject) => {
    const target = new URL(fixture.url); const clean = { ...headers, host: new URL(fixture.public_origin).host };
    delete clean['content-length']; delete clean['connection']; delete clean['accept-encoding'];
    if (body) clean['content-length'] = Buffer.byteLength(body);
    const deadline = setTimeout(() => request.destroy(new Error('Local fixture deadline exceeded')), 15000);
    const request = http.request({ hostname: target.hostname, port: target.port, method, path: requestPath, headers: clean, agent: backendAgent }, response => {
      let bytes = 0; const chunks = [];
      response.on('data', chunk => { bytes += chunk.length; if (bytes > 4 * 1024 * 1024) response.destroy(new Error('Local fixture response too large')); else chunks.push(chunk); });
      response.on('error', error => { clearTimeout(deadline); reject(error); });
      response.on('end', () => { clearTimeout(deadline); resolve({ status: response.statusCode, headers: response.headers, body: Buffer.concat(chunks) }); });
    });
    request.setTimeout(10000, () => request.destroy(new Error('Local fixture socket timeout')));
    request.on('error', error => { clearTimeout(deadline); reject(new Error('Local fixture transport failed at ' + requestPath + ': ' + (error.code || error.message))); }); if (body) request.write(body); request.end();
  });
}
async function waitForFixtureExit(timeoutMs) {
  if (processFixture.exitCode !== null || processFixture.signalCode !== null) return;
  await new Promise(resolve => {
    const finish = () => { clearTimeout(timer); processFixture.off('exit', finish); resolve(); };
    const timer = setTimeout(finish, timeoutMs); processFixture.once('exit', finish);
  });
}
let browser;
try {
  assert.equal((await nextEvent()).event, 'ready');
  const fixture = JSON.parse(fs.readFileSync(fixturePath, 'utf8'));
  browser = await chromium.launch({ headless: true, channel: 'chrome', timeout: 20000 });
  const context = await browser.newContext({ serviceWorkers: 'block', viewport: { width: 1440, height: 1000 }, recordVideo: { dir: temporary, size: { width: 1440, height: 1000 } } });
  context.setDefaultTimeout(15000); context.setDefaultNavigationTimeout(20000);
  await context.addCookies([{ ...fixture.cookie, domain: new URL(fixture.public_origin).hostname, path: '/', secure: true, httpOnly: true, sameSite: 'Lax' }]);
  const failed = [], errors = [], external = []; let checkoutIntercepts = 0; const page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', event => { if (event.type() === 'error') errors.push(event.text()); });
  page.on('requestfailed', request => failed.push(request.url()));
  await context.route('**/*', async route => {
    const request = route.request(); const url = new URL(request.url());
    if (url.origin === fixture.public_origin) {
      const response = await backend(fixture, url.pathname + url.search, request.method(), await request.allHeaders(), request.postDataBuffer());
      const headers = { ...response.headers }; delete headers['content-length']; delete headers['transfer-encoding']; delete headers['connection'];
      if (response.status === 303 && headers.location?.startsWith('https://checkout.stripe.com/c/')) {
        // Playwright does not intercept later hops of a server redirect chain.
        // Preserve the validated real 303 target, then start a separate local
        // browser navigation that the fixture route can actually intercept.
        assert.equal(headers.location, 'https://checkout.stripe.com/c/pay/LocalBrowserFixture');
        await route.fulfill({ status: 200, contentType: 'text/html', body: '<meta http-equiv="refresh" content="0;url=https://checkout.stripe.com/c/pay/LocalBrowserFixture"><title>Local checkout handoff</title>' });
      } else await route.fulfill({ status: response.status, headers, body: response.body });
    } else if (url.hostname === 'checkout.stripe.com') {
      checkoutIntercepts += 1;
      await route.fulfill({ status: 200, contentType: 'text/html', body: '<title>Local fixture checkout</title><h1>Synthetic checkout transport</h1><p>No payment or Stripe network call occurs.</p>' });
    } else { external.push(url.origin); await route.abort(); }
  });
  const artifactRoot = path.join(root, 'docs/evidence/ai-work-functional'); fs.mkdirSync(artifactRoot, { recursive: true });
  const checks = []; const record = label => { checks.push(label); console.log('Passed: ' + label); };
  const go = async url => { const response = await page.goto(fixture.public_origin + url, { waitUntil: 'domcontentloaded' }); assert.equal(response.status(), 200, await page.locator('body').innerText()); };
  const state = async () => JSON.parse((await backend(fixture, '/v1/work/state', 'GET', { cookie: fixture.cookie.name + '=' + fixture.cookie.value }, null)).body);
  const inference = async (workId, input, max = 32) => {
    const response = await backend(fixture, '/v1/responses', 'POST', { authorization: 'Bearer ' + fixture.token, 'x-hormuz-work-id': workId, 'content-type': 'application/json' }, JSON.stringify({ model: 'safe-openai', input, max_output_tokens: max, temperature: 0 }));
    return { status: response.status, body: JSON.parse(response.body), headers: response.headers };
  };
  await go('/work/acquisition?utm_source=localqa&utm_medium=validation&utm_campaign=aiwork');
  assert.equal(await page.locator('[name=analytics_consent]').isChecked(), false);
  await page.getByRole('checkbox').check(); await page.getByRole('button', { name: 'Confirm source and open AI work' }).click();
  await page.waitForURL('**/v1/work/acquisition'); record('consented campaign handoff persists privately');
  assert.equal((await state()).onboarding.state, 'qualification_required');
  await page.getByRole('button', { name: 'Request qualification for safe-openai' }).click();
  await page.waitForURL('**/activation/request'); assert.equal((await state()).onboarding.state, 'requested');
  await page.getByText('Operator qualification review', { exact: true }).click();
  await page.locator('#qualification-reference').fill('local-fixture/provider-and-enrollment');
  await page.getByRole('button', { name: 'Record qualification decision' }).click();
  await page.waitForURL('**/activation/review'); assert.equal((await state()).onboarding.state, 'qualified'); record('actual requested and operator-qualified state transitions');
  await page.getByRole('button', { name: 'Continue to Stripe checkout' }).click();
  await page.waitForURL('https://checkout.stripe.com/**');
  await page.waitForLoadState('domcontentloaded'); assert.equal(await page.title(), 'Local fixture checkout'); assert.equal(checkoutIntercepts, 1);
  await control('checkout'); assert.equal((await state()).onboarding.state, 'payment_unverified');
  await control('payment'); assert.equal((await state()).onboarding.state, 'active'); record('server-owned checkout and signed synthetic payment events gate activation');
  await go('/work');
  const shared = page.locator('form').filter({ has: page.locator('[name=scope_type][value=workspace]') });
  await shared.locator('[name=budget_usd]').fill('1.00'); await shared.locator('[name=objective]').selectOption('speed'); await shared.locator('[name=exploration_enabled]').selectOption('false');
  await shared.getByRole('button', { name: 'Save plan', exact: true }).click(); await page.waitForURL('**/v1/work/policies');
  assert.equal((await state()).plans.find(plan => plan.scope_type === 'workspace').objective, 'speed'); record('workspace budget, speed priority and exploration choice save');
  const create = page.locator('.create-job form');
  await create.locator('[name=repository]').fill('example/checkout-service'); await create.locator('[name=title]').fill('Repair the checkout integration check');
  await create.locator('[name=task_type]').fill('ci-repair'); await create.locator('[name=context_revision]').fill('fixture-commit-1');
  await create.locator('[name=completion_condition]').selectOption('github.pull_request.merged.v1');
  await create.getByRole('button', { name: 'Create job' }).click(); await page.waitForURL('**/v1/work/jobs');
  let current = await state(); const job = current.works.find(work => work.title === 'Repair the checkout integration check'); assert.ok(job);
  let card = page.locator('.job-card').filter({ hasText: job.title });
  await card.getByText('Bind an existing workflow for automatic outcomes', { exact: true }).click();
  await card.locator('.workflow-binding [name=container_id]').selectOption('456'); await card.locator('.workflow-binding [name=object_id]').fill('1001');
  await card.locator('.workflow-binding').getByRole('button').click(); await page.waitForURL('**/bindings'); record('explicit signed GitHub object association is saved from allowed choices');
  const prompt = 'Repair the local checkout integration check using fixture commit 1.';
  assert.equal((await inference(job.work_id, prompt)).status, 200); assert.equal((await inference(job.work_id, prompt)).status, 200);
  current = await state(); let captured = current.works.find(work => work.work_id === job.work_id);
  assert.equal(captured.costs.cache_hits, 1); assert.equal(captured.state, 'active');
  await control('corrected', { work_id: job.work_id }); assert.equal((await inference(job.work_id, prompt)).status, 200);
  current = await state(); captured = current.works.find(work => work.work_id === job.work_id);
  assert.ok(captured.attempts.some(attempt => attempt.cache_bypass_reason)); record('ordinary Responses API requests route, exact reuse skips provider, corrective recurrence bypasses reuse');
  await go('/work'); card = page.locator('.job-card').filter({ hasText: job.title });
  await card.getByText('Job budget and continuation', { exact: true }).click();
  const plan = card.locator('form').filter({ has: page.locator('[name=scope_type][value=job]') });
  await plan.locator('[name=budget_usd]').fill('0'); await plan.getByRole('button', { name: 'Save job plan' }).click(); await page.waitForURL('**/v1/work/policies');
  const capped = await inference(job.work_id, 'A separate local request after the cap.');
  assert.equal(capped.status, 402); assert.match(capped.body.error.code, /budget_exhausted/);
  await go('/work'); card = page.locator('.job-card').filter({ hasText: job.title });
  await card.getByText('Job budget and continuation', { exact: true }).click();
  await card.locator('.continuation [name=budget_usd]').fill('1'); await card.getByRole('button', { name: 'Approve allowance' }).click(); await page.waitForURL('**/actions');
  assert.equal((await inference(job.work_id, 'Continue the local checkout integration check after approved capacity.')).status, 200); record('job cap denies before provider work and explicit allowance continues the same job');
  const merged = await control('signed_merge', { work_id: job.work_id });
  await go('/work/jobs/' + job.work_id); assert.equal(await page.locator('.job-card > .section-heading > .status').innerText(), 'completed');
  await page.getByText('Attempts and route decisions', { exact: false }).click(); await page.getByText('Evidence confidence and comparison scope', { exact: true }).click(); await page.getByText('Outcome evidence', { exact: true }).click();
  record('verified signed GitHub merge supplies declared completion condition');
  assert.match(await page.locator('body').innerText(), /verified_github/);
  assert.match(await page.locator('body').innerText(), /Gateway overhead/); assert.match(await page.locator('body').innerText(), /Provider-confirmed cost\s+Unknown/);
  await page.screenshot({ path: path.join(artifactRoot, 'AI_WORK_JOB_DESKTOP.png'), fullPage: true });
  const receipt = await backend(fixture, '/v1/work/jobs/' + job.work_id, 'GET', { cookie: fixture.cookie.name + '=' + fixture.cookie.value }, null);
  assert.equal(receipt.status, 200); assert.equal(JSON.parse(receipt.body).state, 'completed');
  const support = await backend(fixture, '/v1/work/support/receipt', 'GET', { cookie: fixture.cookie.name + '=' + fixture.cookie.value }, null);
  assert.equal(support.status, 200); assert.equal(JSON.parse(support.body).jobs.length, 1);
  assert.doesNotMatch(support.body.toString(), /example\/checkout-service|Repair the checkout/); record('owned job JSON and bounded content-free support receipts are available');
  await go('/work');
  await page.locator('.provider-account-evidence > summary').click();
  assert.match(await page.locator('.provider-account-evidence').innerText(), /No amount is assumed/);
  record('provider account evidence remains explicitly unavailable for the loopback provider, with no allocation to jobs');
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
  assert.equal(await page.evaluate(() => scrollY), 0);
  await page.screenshot({ path: path.join(artifactRoot, 'AI_WORK_DESKTOP.png') });
  await page.screenshot({ path: path.join(artifactRoot, 'AI_WORK_DASHBOARD_FULL.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
  assert.equal(await page.evaluate(() => scrollY), 0);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.screenshot({ path: path.join(artifactRoot, 'AI_WORK_MOBILE.png') });
  await go('/work/jobs/' + job.work_id); await page.locator('.job-card').screenshot({ path: path.join(artifactRoot, 'AI_WORK_JOB.png') });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false); record('desktop and mobile rendering without horizontal overflow');
  assert.deepEqual(errors, []); assert.deepEqual(failed, []); assert.deepEqual(external, []); record('no console errors, failed requests or external network calls');
  current = await state(); const costs = current.works.find(work => work.work_id === job.work_id).costs;
  const validation = { schema_id: 'hormuz.ai-work-browser-validation', schema_version: 1, generated_at: new Date().toISOString(), fixture: 'actual gateway with synthetic local identity, provider, Stripe transport and signed GitHub fixture', provider_calls: merged.provider_fixture_calls + ' local simulated provider responses; zero real or paid provider calls', viewports: ['1440x1000', '390x844'], checks, seeded_cost_microusd: 0, observed_configured_cost_microusd: costs.committed_microusd, real_provider_calls: 0, real_payments: 0, external_network_calls: 0, browser_errors: errors, failed_requests: failed, horizontal_overflow: false, source_files: fixture.source_files, artifacts: ['AI_WORK_DEMO.webm', 'AI_WORK_DESKTOP.png', 'AI_WORK_MOBILE.png', 'AI_WORK_JOB.png', 'AI_WORK_JOB_DESKTOP.png', 'AI_WORK_DASHBOARD_FULL.png'], limitations: ['local provider timings and configured estimates do not establish billed savings or customer latency', 'synthetic signed Stripe events are not real payment or live billing qualification', 'synthetic signed GitHub delivery is not live customer connector qualification', 'API fixture work does not qualify a particular installed native coding agent version'] };
  fs.writeFileSync(path.join(artifactRoot, 'AI_WORK_BROWSER_QA.json'), JSON.stringify(validation, null, 2) + '\n');
  const video = page.video(); await context.close(); fs.copyFileSync(await video.path(), path.join(artifactRoot, 'AI_WORK_DEMO.webm'));
  console.log(JSON.stringify({ checks: checks.length, provider_fixture_calls: merged.provider_fixture_calls, real_provider_calls: 0, real_payments: 0, screenshots: validation.artifacts, errors, failures: failed }));
} finally {
  try { if (browser) await browser.close(); }
  finally {
    backendAgent.destroy();
    try {
      if (processFixture.exitCode === null && processFixture.signalCode === null) {
        if (!processFixture.stdin.destroyed) processFixture.stdin.end('{"operation":"stop"}\n');
        await waitForFixtureExit(5000);
        if (processFixture.exitCode === null && processFixture.signalCode === null) { processFixture.kill('SIGTERM'); await waitForFixtureExit(3000); }
        if (processFixture.exitCode === null && processFixture.signalCode === null) { processFixture.kill('SIGKILL'); await waitForFixtureExit(3000); }
      }
    } finally { fs.rmSync(temporary, { recursive: true, force: true }); }
  }
}
