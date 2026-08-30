import { CONTACT_EMAIL } from './site.mjs';

export const INTERESTS = Object.freeze({ pilot: 'Enterprise pilot', integration: 'Client integration', security: 'Security requirements', community: 'Open-source feedback' });

function clean(value, max) {
  return String(value ?? '').replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, '').trim().slice(0, max);
}

/** Attribution stays in the browser and is only included in a user-reviewed email draft. */
export function campaignSource(search) {
  const params = new URLSearchParams(search);
  return ['utm_source', 'utm_medium', 'utm_campaign'].map(key => {
    const value = params.get(key);
    return value && /^[a-zA-Z0-9_.-]{1,64}$/.test(value) ? `${key}=${value}` : '';
  }).filter(Boolean).join(' · ');
}

export function buildInquiry(fields, source = '') {
  const interest = Object.hasOwn(INTERESTS, String(fields.interest)) ? INTERESTS[fields.interest] : INTERESTS.pilot;
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
