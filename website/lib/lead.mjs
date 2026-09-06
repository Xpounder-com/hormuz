import { INTERESTS, campaignSource } from './contact.mjs';
import { validateCommercialConfig } from './commercial.mjs';

const TAGS = ['utm_source', 'utm_medium', 'utm_campaign'];
export function campaignLink(href, search) {
  if (!href.startsWith('/') || href.startsWith('//')) return href;
  const url = new URL(href, 'https://local.invalid');
  const source = new URLSearchParams(search);
  for (const key of TAGS) {
    const value = source.get(key);
    if (value && /^[a-zA-Z0-9_.-]{1,64}$/.test(value)) url.searchParams.set(key, value);
  }
  return `${url.pathname}${url.search}${url.hash}`;
}

function clean(value, max) {
  return String(value ?? '').replace(/[\u0000-\u001f\u007f]/g, ' ').trim().slice(0, max);
}

export function buildLead(fields, source = '') {
  const name = clean(fields.name, 100);
  const email = clean(fields.email, 254);
  const organization = clean(fields.organization, 150);
  const workflow = clean(fields.workflow, 1200);
  if (!name || !organization || !workflow || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) throw new Error('Add your name, work email, organization, and a short workflow description.');
  const interest = Object.hasOwn(INTERESTS, fields.interest) ? INTERESTS[fields.interest] : INTERESTS.pilot;
  return { name, email, organization, workflow, interest, timeframe: clean(fields.timeframe, 100),
    ...(source ? { campaign: campaignSource(source) } : {}),
    _subject: `Hormuz — ${interest}`, _gotcha: clean(fields._gotcha, 100) };
}

/** A transport failure is ambiguous. Never retry automatically or claim delivery. */
export async function submitLead(endpoint, payload, fetcher = fetch) {
  validateCommercialConfig({ formEndpoint: endpoint, bookingUrl: '', pilotPaymentUrl: '', supportPaymentUrl: '' });
  if (!endpoint) throw new Error('Application submission is not connected. Please use email.');
  try {
    const response = await fetcher(endpoint, {
      method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(payload), credentials: 'omit', referrerPolicy: 'no-referrer',
      signal: AbortSignal.timeout(15000), redirect: 'error',
    });
    const data = await response.json();
    if (!response.ok || data?.ok !== true) throw new Error('unconfirmed');
  } catch {
    throw new Error('We could not confirm receipt. Your application may have arrived. Your entries are still here; email us to check before submitting again.');
  }
}
