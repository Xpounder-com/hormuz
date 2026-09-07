import { INTERESTS, CAMPAIGN_TAGS, campaignSource } from './contact.mjs';
import { validateCommercialConfig } from './commercial.mjs';
import { SITE_ROUTES, sitePath } from './site.mjs';

export function campaignLink(href, search) {
  if (!href.startsWith('/') || href.startsWith('//')) return href;
  const url = new URL(href, 'https://local.invalid');
  if (url.origin !== 'https://local.invalid' || !SITE_ROUTES.map(sitePath).includes(url.pathname)) return href;
  const source = new URLSearchParams(search);
  for (const key of CAMPAIGN_TAGS) {
    const value = source.get(key);
    if (value && /^[a-zA-Z0-9_.-]{1,64}$/.test(value)) url.searchParams.set(key, value);
  }
  return `${url.pathname}${url.search}${url.hash}`;
}

function clean(value, max) {
  return String(value ?? '').replace(/[\u0000-\u001f\u007f]/g, ' ').trim().slice(0, max);
}

export function createRequestReference(cryptoSource = globalThis.crypto) {
  return `HZ-${cryptoSource.randomUUID()}`;
}

export function buildLead(fields, source = '', { reference = '', testSubmission = false } = {}) {
  if (reference && !/^HZ-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(reference)) throw new Error('Invalid request reference');
  const name = clean(fields.name, 100);
  const email = clean(fields.email, 254);
  const organization = clean(fields.organization, 150);
  const workflow = clean(fields.workflow, 1200);
  if (!name || !organization || !workflow || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) throw new Error('Add your name, work email, organization, and a short workflow description.');
  const interest = Object.hasOwn(INTERESTS, fields.interest) ? INTERESTS[fields.interest] : INTERESTS.pilot;
  return { name, email, organization, workflow, interest, timeframe: clean(fields.timeframe, 100),
    ...(source ? { campaign: campaignSource(source) } : {}),
    ...(reference ? { request_reference: reference } : {}),
    ...(testSubmission ? { test_submission: true } : {}),
    _subject: `${testSubmission ? '[QA TEST] ' : ''}Hormuz — ${interest}${reference ? ` — ${reference}` : ''}`, _gotcha: clean(fields._gotcha, 100) };
}

/** Validate before allocating a reference that implies a transmission attempt. */
export function prepareLeadAttempt(fields, source = '', { reference = '', testSubmission = false, cryptoSource = globalThis.crypto } = {}) {
  buildLead(fields, source, { testSubmission });
  const requestReference = reference || createRequestReference(cryptoSource);
  return { ...buildLead(fields, source, { reference: requestReference, testSubmission }), request_reference: requestReference };
}

/** A transport failure is ambiguous. Never retry automatically or claim delivery. */
export async function submitLead(endpoint, payload, fetcher = fetch) {
  validateCommercialConfig({ formEndpoint: endpoint, bookingUrl: '', pilotPaymentUrl: '', supportPaymentUrl: '' });
  if (!endpoint) throw new Error('Application submission is not connected. Please use email.');
  try {
    const response = await fetcher(endpoint, {
      method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      // Formspree's domain restriction needs Referer. Cross-origin requests
      // disclose only the website origin, never its path or query parameters.
      body: JSON.stringify(payload), credentials: 'omit', referrerPolicy: 'strict-origin-when-cross-origin',
      signal: AbortSignal.timeout(15000), redirect: 'error',
    });
    const data = await response.json();
    if (!response.ok || data?.ok !== true) throw new Error('unconfirmed');
  } catch {
    throw new Error('We could not confirm receipt. Your application may have arrived. Your entries are still here; email us to check before submitting again.');
  }
}
