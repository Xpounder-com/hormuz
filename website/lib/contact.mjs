import { CONTACT_EMAIL } from './site.mjs';

export const INTERESTS = Object.freeze({ reservation: 'Appliance reservation', cloud: 'Cloud workspace', appliance: 'SSD-equipped appliance', onboarding: 'Appliance with scoped onboarding', managed: 'Managed site', review: 'Workflow fit discussion', integration: 'Client integration', security: 'Security requirements', community: 'Open-source feedback' });
const LEGACY_INTERESTS = Object.freeze({ pro: 'cloud', pilot: 'onboarding', support: 'managed', enterprise: 'appliance' });
export function normalizeInterest(value) {
  const key = String(value ?? '');
  if (Object.hasOwn(INTERESTS, key)) return key;
  return Object.hasOwn(LEGACY_INTERESTS, key) ? LEGACY_INTERESTS[key] : 'onboarding';
}
export const CAMPAIGN_TAGS = Object.freeze(['utm_source', 'utm_medium', 'utm_campaign', 'utm_content']);
export const isSalesInquiry = interest => ['reservation', 'cloud', 'appliance', 'onboarding', 'managed', 'review', ...Object.keys(LEGACY_INTERESTS)].includes(interest);

function clean(value, max) {
  return String(value ?? '').replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, '').trim().slice(0, max);
}

/** Only bounded, explicitly selected campaign labels enter an inquiry. */
export function campaignSource(search) {
  const params = new URLSearchParams(search);
  return CAMPAIGN_TAGS.map(key => {
    const value = params.get(key);
    return value && /^[a-zA-Z0-9_.-]{1,64}$/.test(value) ? `${key}=${value}` : '';
  }).filter(Boolean).join(' · ');
}

export function buildInquiry(fields, source = '') {
  const interest = INTERESTS[normalizeInterest(fields.interest)];
  const name = clean(fields.name, 100);
  const organization = clean(fields.organization, 150);
  const workflow = clean(fields.workflow, 1200);
  const timeframe = clean(fields.timeframe, 100);
  if (!name || !workflow) throw new Error('Add your name and a short workflow description.');
  const subject = `Hormuz — ${interest}`;
  const body = [
    'Hi Mehrdad,', '', `I would like to discuss: ${interest}.`, '',
    `Name: ${name}`, `Organization: ${organization || 'Not specified'}`,
    `Timing: ${timeframe || 'To discuss'}`, '', 'Workflow / question:', workflow, '',
    ...(source ? [`Optional campaign source: ${clean(source, 250)}`, ''] : []),
    'I understand this is an inquiry, not a booking or service agreement.',
  ].join('\n');
  return { subject, body, mailto: `mailto:${CONTACT_EMAIL}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}` };
}
