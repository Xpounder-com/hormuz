import { GA_MEASUREMENT_ID } from './measurement-config.mjs';
import { SITE_ORIGIN, SITE_ROUTES, REPOSITORY } from './site.mjs';
import { isSalesInquiry } from './contact.mjs';
import { privacySignal, CONSENT_DURATION } from './x-ads.mjs';

export const ANALYTICS_CONSENT_KEY = 'hormuz.analytics-consent.v1';
const sessions = new WeakMap();
const session = win => {
  if (!sessions.has(win)) sessions.set(win, { consent: 'unset', started: false, id: '', lead: false });
  return sessions.get(win);
};
const campaignFields = Object.freeze({ utm_source: 'campaign_source', utm_medium: 'campaign_medium', utm_campaign: 'campaign_name', utm_content: 'campaign_content' });
const eventDetails = Object.freeze({
  demo_open: ['website_demo'],
  demo_interaction: ['spend', 'policy', 'compaction', 'setup'],
  install_click: ['setup_guide', 'mac_download'],
  inquiry_open: ['contact'],
});

export function analyticsConfigured(id = GA_MEASUREMENT_ID) {
  return /^G-[A-Z0-9]{6,20}$/.test(id);
}

export function readAnalyticsConsent(win = window, now = Date.now()) {
  if (privacySignal(win)) return 'declined';
  try {
    const saved = JSON.parse(win.localStorage.getItem(ANALYTICS_CONSENT_KEY) || 'null');
    if (saved && ['allowed', 'declined'].includes(saved.choice) && Number.isFinite(saved.expires) && saved.expires > now && saved.expires <= now + CONSENT_DURATION) return saved.choice;
  } catch { /* Fall back to this page's explicit choice. */ }
  return session(win).consent;
}

export function saveAnalyticsConsent(choice, win = window, now = Date.now()) {
  const effective = choice === 'allowed' && !privacySignal(win) ? 'allowed' : 'declined';
  session(win).consent = effective;
  try { win.localStorage.setItem(ANALYTICS_CONSENT_KEY, JSON.stringify({ choice: effective, expires: now + CONSENT_DURATION })); } catch { /* Page-only preference. */ }
  if (session(win).started) {
    // Stop collection immediately; the preferences UI also reloads to unload SDKs.
    win[`ga-disable-${session(win).id}`] = effective !== 'allowed';
    try { win.gtag('consent', 'update', { analytics_storage: effective === 'allowed' ? 'granted' : 'denied' }); } catch { /* Optional measurement. */ }
  }
  return effective;
}

/** Do not disclose arbitrary query strings, fragments, titles or referrer paths. */
export function analyticsContext(win = window) {
  const path = win.location.pathname;
  if (win.location.origin !== SITE_ORIGIN || !SITE_ROUTES.includes(path)) return null;
  const search = new URLSearchParams(win.location.search);
  if (search.get('qa') === '1') return null;
  const context = { page_location: `${SITE_ORIGIN}${path}`, page_title: `Hormuz ${path}`, page_referrer: '' };
  try {
    const referrer = new URL(win.document.referrer);
    if (['https:', 'http:'].includes(referrer.protocol)) context.page_referrer = `${referrer.origin}/`;
  } catch { /* Direct visit or unavailable referrer. */ }
  for (const [tag, field] of Object.entries(campaignFields)) {
    const value = search.get(tag);
    if (value && /^[a-zA-Z0-9_.-]{1,64}$/.test(value)) context[field] = value;
  }
  return context;
}

/** Basic consent mode: no Google requests, cookies or queue before opt-in. */
export function startAnalytics(win = window, id = GA_MEASUREMENT_ID) {
  const context = analyticsContext(win);
  if (!analyticsConfigured(id) || !context || readAnalyticsConsent(win) !== 'allowed') return false;
  const state = session(win);
  if (state.started) return state.id === id;
  try {
    win.dataLayer = win.dataLayer || [];
    win.gtag = win.gtag || function () { win.dataLayer.push(arguments); };
    win.gtag('consent', 'default', { analytics_storage: 'denied', ad_storage: 'denied', ad_user_data: 'denied', ad_personalization: 'denied' });
    win.gtag('consent', 'update', { analytics_storage: 'granted' });
    win.gtag('js', new Date());
    win.gtag('set', { allow_google_signals: false, allow_ad_personalization_signals: false, ads_data_redaction: true, ...context });
    win.gtag('config', id, { send_page_view: false, cookie_expires: CONSENT_DURATION / 1000, cookie_update: false, cookie_flags: 'SameSite=Lax;Secure', ...context });
    const script = win.document.createElement('script');
    script.id = 'hormuz-google-analytics';
    script.async = true;
    script.src = `https://www.googletagmanager.com/gtag/js?id=${id}`;
    script.referrerPolicy = 'origin';
    win.document.head.appendChild(script);
    state.started = true;
    state.id = id;
    win.gtag('event', 'page_view', { send_to: id, ...context });
    return true;
  } catch { return false; }
}

export function analyticsStarted(win = window) { return session(win).started; }

export function trackAnalyticsEvent(name, detail, win = window, id = GA_MEASUREMENT_ID) {
  if (!Object.hasOwn(eventDetails, name) || !eventDetails[name].includes(detail) || !startAnalytics(win, id)) return false;
  try {
    win.gtag('event', name, { send_to: id, action: detail, ...analyticsContext(win) });
    return true;
  } catch { return false; }
}

/** Call only after a positive form-service acknowledgement, never on submit/click. */
export function trackAnalyticsLead(win = window, { interest = '', testSubmission = false } = {}, id = GA_MEASUREMENT_ID) {
  if (!isSalesInquiry(interest) || testSubmission || session(win).lead || !startAnalytics(win, id)) return false;
  try {
    win.gtag('event', 'generate_lead', { send_to: id, lead_type: interest, ...analyticsContext(win) });
    session(win).lead = true;
    return true;
  } catch { return false; }
}

export function trackedDestination(href) {
  try {
    const url = new URL(href, SITE_ORIGIN);
    if (url.origin === SITE_ORIGIN) {
      if (url.pathname === '/demo/') return ['demo_open', 'website_demo'];
      if (url.pathname === '/' && ['#spend', '#policy', '#compaction', '#setup'].includes(url.hash)) return ['demo_open', 'website_demo'];
      if (url.pathname === '/docs/') return ['install_click', 'setup_guide'];
      if (url.pathname === '/contact/') return ['inquiry_open', 'contact'];
    }
    if (url.origin === 'https://github.com' && url.pathname.startsWith(new URL(REPOSITORY).pathname + '/releases/download/') && /^Hormuz-[\d.]+-notarized\.zip$/.test(url.pathname.split('/').at(-1))) return ['install_click', 'mac_download'];
  } catch { /* Unknown destinations do not become event data. */ }
  return null;
}
