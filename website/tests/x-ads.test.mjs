import test from 'node:test';
import assert from 'node:assert/strict';
import { AD_CONSENT_KEY, CONSENT_DURATION, X_LEAD_EVENT_ID, readAdConsent, saveAdConsent, startXPixel, trackConfirmedApplication } from '../lib/x-ads.mjs';
import { submitLead } from '../lib/lead.mjs';

function browser({ origin = 'https://usehormuz.github.io', signal = false, storageBlocked = false } = {}) {
  const storage = new Map();
  const scripts = [];
  return {
    scripts, storage, location: { origin }, navigator: { globalPrivacyControl: signal },
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
