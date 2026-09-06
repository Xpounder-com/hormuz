import { SITE_ORIGIN } from './site.mjs';

// Public identifiers from the owner's X Ads Events Manager. No credentials.
export const X_PIXEL_ID = 'rf0s7';
export const X_LEAD_EVENT_ID = 'tw-rf0s7-rf0s8';
export const AD_CONSENT_KEY = 'hormuz.x-ads-consent.v1';
export const CONSENT_DURATION = 180 * 24 * 60 * 60 * 1000;
const sessions = new WeakMap();
function session(win) {
  if (!sessions.has(win)) sessions.set(win, { consent: null, started: false, converted: false });
  return sessions.get(win);
}

export function privacySignal(win = window) {
  return win.navigator.globalPrivacyControl === true || win.navigator.doNotTrack === '1';
}

/** @returns {'allowed' | 'declined' | 'unset'} */
export function readAdConsent(win = window, now = Date.now()) {
  if (privacySignal(win)) return 'declined';
  try {
    const saved = JSON.parse(win.localStorage.getItem(AD_CONSENT_KEY) || 'null');
    if (saved && Number.isFinite(saved.expires) && saved.expires > now &&
      saved.expires <= now + CONSENT_DURATION && ['allowed', 'declined'].includes(saved.choice)) return saved.choice;
  } catch { /* Storage may be unavailable. Consent can still last for this page. */ }
  return session(win).consent || 'unset';
}

/** @param {'allowed' | 'declined'} choice */
export function saveAdConsent(choice, win = window, now = Date.now()) {
  const effective = privacySignal(win) ? 'declined' : choice;
  session(win).consent = effective;
  try { win.localStorage.setItem(AD_CONSENT_KEY, JSON.stringify({ choice: effective, expires: now + CONSENT_DURATION })); } catch { /* Page-only choice. */ }
  return effective;
}

/** No SDK request, cookies, or queued events before consent; previews never report. */
export function startXPixel(win = window) {
  const state = session(win);
  if (readAdConsent(win) !== 'allowed' || win.location.origin !== SITE_ORIGIN) return false;
  if (state.started) return true;
  try {
    const twq = win.twq || function (...args) {
      if (twq.exe) twq.exe.apply(twq, args);
      else twq.queue.push(args);
    };
    if (!win.twq) { twq.version = '1.1'; twq.queue = []; win.twq = twq; }
    // X's documented URL suppression, before config and all events.
    twq('set', { hide_page_location: true });
    twq('config', X_PIXEL_ID);
    const script = win.document.createElement('script');
    script.id = 'hormuz-x-pixel';
    script.async = true;
    script.src = 'https://static.ads-twitter.com/uwt.js';
    script.referrerPolicy = 'origin';
    win.document.head.appendChild(script);
    state.started = true;
    return true;
  } catch { return false; }
}

/** Called only after submitLead resolves. No form payload enters this function. */
export function trackConfirmedApplication(win = window) {
  const state = session(win);
  if (state.converted || !startXPixel(win)) return false;
  try {
    win.twq('event', X_LEAD_EVENT_ID, {});
    state.converted = true;
    return true;
  } catch { return false; } // Measurement must never turn a saved lead into an error.
}

export function pixelStarted(win = window) { return session(win).started; }
