import test from 'node:test';
import assert from 'node:assert/strict';
import { AD_CONSENT_KEY, CONSENT_DURATION, X_LEAD_EVENT_ID, readAdConsent, saveAdConsent, startXPixel, trackConfirmedApplication, xPixelSupported } from '../lib/x-ads.mjs';
import { submitLead } from '../lib/lead.mjs';

function browser({ origin = 'https://usehormuz.github.io', signal = false, storageBlocked = false, userAgent = '' } = {}) {
  const storage = new Map();
  const scripts = [];
  return {
    scripts, storage, location: { origin }, navigator: { globalPrivacyControl: signal, userAgent },
    localStorage: {
      getItem(key) { if (storageBlocked) throw Error('blocked'); return storage.get(key); },
      setItem(key, value) { if (storageBlocked) throw Error('blocked'); storage.set(key, value); },
    },
    document: { createElement: () => ({}), head: { appendChild: script => scripts.push(script) } },
  };
}

test('only acknowledged sales interests qualify for the lead event; general inquiries and QA never do', () => {
  for (const interest of ['integration', 'security', 'community', '', undefined, '__proto__']) {
    const win = browser(); saveAdConsent('allowed', win);
    assert.equal(trackConfirmedApplication(win, { interest }), false);
    assert.equal(win.twq, undefined);
  }
  for (const interest of ['review', 'pilot', 'support']) {
    const win = browser(); saveAdConsent('allowed', win);
    assert.equal(trackConfirmedApplication(win, { interest, testSubmission: true }), false);
    assert.equal(trackConfirmedApplication(win, { interest }), true);
  }
});

test('no scripts or conversion queues before consent, after decline, or with a privacy signal', () => {
  for (const state of ['unset', 'declined', 'privacy-signal', 'do-not-track']) {
    const win = browser({ signal: state === 'privacy-signal' });
    if (state === 'do-not-track') win.navigator.doNotTrack = '1';
    if (state !== 'unset') saveAdConsent(state === 'declined' ? 'declined' : 'allowed', win);
    assert.equal(startXPixel(win), false);
    assert.equal(trackConfirmedApplication(win, { interest: 'review' }), false);
    assert.equal(win.scripts.length, 0);
    assert.equal(win.twq, undefined);
  }
});

test('malformed, expired and far-future consent records cannot activate measurement', () => {
  const win = browser();
  for (const value of ['broken', '{}', JSON.stringify({ choice: 'allowed', expires: 1 }),
    JSON.stringify({ choice: 'allowed', expires: Date.now() + CONSENT_DURATION * 2 })]) {
    win.storage.set(AD_CONSENT_KEY, value);
    assert.equal(readAdConsent(win), 'unset');
    assert.equal(startXPixel(win), false);
  }
});

test('opted-in previews never report to the production ad account', () => {
  for (const origin of ['http://127.0.0.1:3188', 'https://preview.example.com']) {
    const win = browser({ origin });
    saveAdConsent('allowed', win);
    assert.equal(trackConfirmedApplication(win, { interest: 'review' }), false);
    assert.equal(win.scripts.length, 0);
  }
});

test('Safari and iPhone or iPad WebKit never load optional third-party measurement', () => {
  const userAgents = [
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/18.6 Safari/605.1.15',
    'Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 Version/18.6 Mobile/15E148 Safari/604.1',
    'Mozilla/5.0 (iPad; CPU OS 18_6 like Mac OS X) AppleWebKit/605.1.15 CriOS/140.0.0.0 Mobile/15E148 Safari/604.1',
  ];
  for (const userAgent of userAgents) {
    const win = browser({ userAgent });
    saveAdConsent('allowed', win);
    assert.equal(xPixelSupported(win), false);
    assert.equal(startXPixel(win), false);
    assert.equal(trackConfirmedApplication(win, { interest: 'review' }), false);
    assert.equal(win.scripts.length, 0);
    assert.equal(win.twq, undefined);
  }
});

test('desktop Chromium is not mistaken for Safari by its AppleWebKit token', () => {
  const win = browser({ userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/140.0.0.0 Safari/537.36' });
  saveAdConsent('allowed', win);
  assert.equal(xPixelSupported(win), true);
  assert.equal(startXPixel(win), true);
  assert.equal(win.scripts.length, 1);
});

test('one SDK initialization suppresses page URLs and one conversion contains no form data', () => {
  const win = browser();
  saveAdConsent('allowed', win);
  assert.equal(startXPixel(win), true);
  assert.equal(startXPixel(win), true);
  assert.equal(win.scripts.length, 1);
  assert.equal(win.scripts[0].src, 'https://static.ads-twitter.com/uwt.js');
  assert.equal(win.scripts[0].referrerPolicy, 'origin');
  assert.deepEqual(win.twq.queue[0], ['set', { hide_page_location: true }]);
  assert.equal(trackConfirmedApplication(win, { interest: 'review' }), true);
  assert.equal(trackConfirmedApplication(win, { interest: 'review' }), false);
  assert.deepEqual(win.twq.queue.filter(args => args[0] === 'event'), [['event', X_LEAD_EVENT_ID, {}]]);
});

test('withdrawal blocks subsequent application events and blocked storage supports page-only choices', () => {
  const win = browser({ storageBlocked: true });
  saveAdConsent('allowed', win);
  assert.equal(startXPixel(win), true);
  saveAdConsent('declined', win);
  assert.equal(trackConfirmedApplication(win, { interest: 'review' }), false);
  assert.equal(win.twq.queue.some(args => args[0] === 'event'), false);
  assert.equal(readAdConsent(browser({ storageBlocked: true })), 'unset');
});

test('a rejected form sends no conversion; a saved form can succeed even if X fails', async () => {
  const win = browser();
  saveAdConsent('allowed', win);
  const endpoint = 'https://formspree.io/f/testfixture';
  await assert.rejects(async () => {
    await submitLead(endpoint, {}, async () => new Response('{"ok":false}', { status: 429 }));
    trackConfirmedApplication(win, { interest: 'review' });
  });
  assert.equal(win.scripts.length, 0);
  await submitLead(endpoint, {}, async () => new Response('{"ok":true}'));
  startXPixel(win);
  win.twq = () => { throw Error('ad blocker'); };
  assert.equal(trackConfirmedApplication(win, { interest: 'review' }), false);
});
