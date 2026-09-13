import test from 'node:test';
import assert from 'node:assert/strict';
import { analyticsConfigured, ANALYTICS_CONSENT_KEY, analyticsContext, readAnalyticsConsent, saveAnalyticsConsent, startAnalytics, trackAnalyticsEvent, trackAnalyticsLead, trackedDestination } from '../lib/analytics.mjs';
import { saveAdConsent, startXPixel, CONSENT_DURATION } from '../lib/x-ads.mjs';
import { submitLead } from '../lib/lead.mjs';

const id = 'G-TEST123456';
function browser({ url = 'https://usehormuz.github.io/', blockedStorage = false, signal = false, safari = false } = {}) {
  const storage = new Map(); const scripts = [];
  return { storage, scripts, location: new URL(url),
    navigator: { globalPrivacyControl: signal, userAgent: safari ? 'AppleWebKit/605.1.15 Version/18.6 Safari/605.1.15' : 'Chrome/140.0.0.0' },
    localStorage: { getItem(key) { if (blockedStorage) throw Error('blocked'); return storage.get(key); }, setItem(key, value) { if (blockedStorage) throw Error('blocked'); storage.set(key, value); } },
    document: { referrer: 'https://t.co/ad?private=value', createElement: () => ({}), head: { appendChild(script) { scripts.push(script); } } },
  };
}
const commands = win => (win.dataLayer || []).map(args => [...args]);

test('X consent never authorizes Analytics and unset, declined or privacy signals create no Google queue', () => {
  const win = browser(); saveAdConsent('allowed', win);
  assert.equal(startAnalytics(win, id), false);
  assert.equal(win.dataLayer, undefined);
  for (const mode of ['declined', 'gpc', 'dnt']) {
    const candidate = browser({ signal: mode === 'gpc' });
    if (mode === 'dnt') candidate.navigator.doNotTrack = '1';
    saveAnalyticsConsent(mode === 'declined' ? 'declined' : 'allowed', candidate);
    assert.equal(startAnalytics(candidate, id), false);
    assert.deepEqual(candidate.scripts, []);
    assert.equal(candidate.dataLayer, undefined);
  }
});

test('configuration, production origin, known routes, and QA exclusion are required', () => {
  for (const invalid of ['', 'G-', 'AW-1234567890', 'G-TEST123456?other=value']) assert.equal(analyticsConfigured(invalid), false);
  for (const url of ['http://localhost:3100/', 'https://preview.example.com/', 'https://usehormuz.github.io/contact/?qa=1', 'https://usehormuz.github.io/private-value/']) {
    const win = browser({ url }); saveAnalyticsConsent('allowed', win);
    assert.equal(startAnalytics(win, id), false); assert.equal(win.dataLayer, undefined);
  }
});

test('expired, malformed and excessive-duration saved preferences cannot enable Analytics', () => {
  const win = browser();
  for (const value of ['invalid', '{}', JSON.stringify({ choice: 'allowed', expires: 1 }), JSON.stringify({ choice: 'allowed', expires: Date.now() + CONSENT_DURATION * 2 })]) {
    win.storage.set(ANALYTICS_CONSENT_KEY, value);
    assert.equal(readAnalyticsConsent(win), 'unset'); assert.equal(startAnalytics(win, id), false);
  }
});

test('page context keeps bounded campaign labels while excluding form data, fragments, and referrer paths', () => {
  const win = browser({ url: 'https://usehormuz.github.io/contact/?utm_source=x&utm_medium=paid_social&utm_campaign=hormuz_2026&utm_content=ad_42&email=private%40example.com&twclid=private&workflow=secret#private' });
  assert.deepEqual(analyticsContext(win), { page_location: 'https://usehormuz.github.io/contact/', page_title: 'Hormuz /contact/', page_referrer: 'https://t.co/', campaign_source: 'x', campaign_medium: 'paid_social', campaign_name: 'hormuz_2026', campaign_content: 'ad_42' });
  win.location.search = '?utm_source=private%40example.com&utm_campaign=' + 'a'.repeat(65);
  assert.equal(analyticsContext(win).campaign_source, undefined);
  assert.equal(analyticsContext(win).campaign_name, undefined);
});

test('Safari can opt into Analytics independently while its X pixel remains disabled', () => {
  const win = browser({ safari: true }); saveAnalyticsConsent('allowed', win); saveAdConsent('allowed', win);
  assert.equal(startXPixel(win), false); assert.equal(startAnalytics(win, id), true); assert.equal(startAnalytics(win, id), true);
  assert.equal(win.scripts.length, 1);
  assert.equal(win.scripts[0].src, `https://www.googletagmanager.com/gtag/js?id=${id}`);
  assert.equal(win.scripts[0].referrerPolicy, 'origin');
  const all = commands(win);
  assert.deepEqual(all[0], ['consent', 'default', { analytics_storage: 'denied', ad_storage: 'denied', ad_user_data: 'denied', ad_personalization: 'denied' }]);
  assert.equal(all.filter(args => args[0] === 'event' && args[1] === 'page_view').length, 1);
  assert.equal(all.find(args => args[0] === 'config')[2].send_page_view, false);
  assert.equal(all.find(args => args[0] === 'set')[1].allow_google_signals, false);
});

test('only explicit event names and bounded actions can enter the Analytics payload', () => {
  const win = browser(); saveAnalyticsConsent('allowed', win);
  assert.equal(trackAnalyticsEvent('demo_interaction', 'policy', win, id), true);
  for (const [event, detail] of [['arbitrary', 'text'], ['__proto__', 'text'], ['demo_interaction', 'private workflow']]) assert.equal(trackAnalyticsEvent(event, detail, win, id), false);
  assert.deepEqual(commands(win).filter(args => args[0] === 'event').map(args => args[1]), ['page_view', 'demo_interaction']);
});

test('lead events follow acknowledged sales inquiries and exclude QA, failed receipts and duplicates', async () => {
  const win = browser(); saveAnalyticsConsent('allowed', win);
  assert.equal(trackAnalyticsLead(win, { interest: 'community' }, id), false);
  assert.equal(trackAnalyticsLead(win, { interest: 'pilot', testSubmission: true }, id), false);
  await assert.rejects(async () => {
    await submitLead('https://formspree.io/f/testfixture', {}, async () => new Response('{"ok":false}'));
    trackAnalyticsLead(win, { interest: 'pilot' }, id);
  });
  assert.equal(win.dataLayer, undefined);
  await submitLead('https://formspree.io/f/testfixture', {}, async () => new Response('{"ok":true}'));
  assert.equal(trackAnalyticsLead(win, { interest: 'pilot' }, id), true);
  assert.equal(trackAnalyticsLead(win, { interest: 'pilot' }, id), false);
  assert.equal(commands(win).filter(args => args[1] === 'generate_lead').length, 1);
  win.gtag = () => { throw Error('blocked vendor'); };
  assert.equal(trackAnalyticsEvent('demo_open', 'website_demo', win, id), false);
});

test('withdrawal immediately disables collection and blocked storage keeps an explicit page-only choice', () => {
  const win = browser({ blockedStorage: true }); saveAnalyticsConsent('allowed', win);
  assert.equal(startAnalytics(win, id), true);
  saveAnalyticsConsent('declined', win);
  assert.equal(win[`ga-disable-${id}`], true);
  assert.equal(trackAnalyticsLead(win, { interest: 'pilot' }, id), false);
  assert.equal(readAnalyticsConsent(browser({ blockedStorage: true })), 'unset');
});

test('link measurement identifies allowed destinations without passing URLs or arbitrary labels', () => {
  assert.deepEqual(trackedDestination('/demo/?utm_source=x#policy'), ['demo_open', 'website_demo']);
  assert.deepEqual(trackedDestination('/?utm_source=x#spend'), ['demo_open', 'website_demo']);
  assert.deepEqual(trackedDestination('/docs/#mac'), ['install_click', 'setup_guide']);
  assert.deepEqual(trackedDestination('https://github.com/Xpounder-com/hormuz/releases/download/v1.2.0/Hormuz-1.2.0-notarized.zip'), ['install_click', 'mac_download']);
  for (const href of ['mailto:someone@example.com', 'https://other.example/docs/', '/privacy/', 'javascript:alert(1)']) assert.equal(trackedDestination(href), null);
});
