import test from 'node:test';
import assert from 'node:assert/strict';
import { validateCommercialConfig } from '../lib/commercial.mjs';
import { buildLead, campaignLink, createRequestReference, submitLead } from '../lib/lead.mjs';

const blank = { formEndpoint: '', bookingUrl: '', pilotPaymentUrl: '', supportPaymentUrl: '' };
const endpoint = 'https://formspree.io/f/testfixture';
const fields = { name: 'Test Person', email: 'test@example.com', organization: 'Example', workflow: 'Evaluate model limits', interest: 'pilot' };

test('unconfigured commercial paths stay absent and configured destinations are constrained', () => {
  assert.deepEqual(validateCommercialConfig(blank), blank);
  assert.equal(validateCommercialConfig({ ...blank, formEndpoint: endpoint }).formEndpoint, endpoint);
  for (const url of ['http://formspree.io/f/abc', 'https://evil.example/f/abc', 'https://formspree.io@evil.example/f/abc', 'https://formspree.io/f/abc?secret=x']) {
    assert.throws(() => validateCommercialConfig({ ...blank, formEndpoint: url }));
  }
  for (const url of ['https://buy.stripe.com/test_123', 'https://checkout.stripe.com/customer-specific', 'https://buy.stripe.com/abc?prefilled_email=a@example.com']) {
    assert.throws(() => validateCommercialConfig({ ...blank, pilotPaymentUrl: url }));
  }
  assert.throws(() => validateCommercialConfig({ ...blank, bookingUrl: 'https://evil.example' }));
});

test('campaign links keep intent and fragment, forward only bounded campaign tags, and never tag external links', () => {
  const search = '?utm_source=linkedin&utm_medium=cpc&utm_campaign=enterprise&email=private@example.com&gclid=identifier';
  assert.equal(campaignLink('/contact/?interest=support#content', search), '/contact/?interest=support&utm_source=linkedin&utm_medium=cpc&utm_campaign=enterprise#content');
  assert.equal(campaignLink('https://cal.com/example', search), 'https://cal.com/example');
  assert.equal(campaignLink('//other.example/', search), '//other.example/');
  assert.equal(campaignLink('/contact/', '?utm_source=%3Cscript%3E&utm_medium=' + 'a'.repeat(65)), '/contact/');
});

test('lead payload excludes unknown fields and unconsented campaign data', () => {
  const payload = buildLead({ ...fields, secret: 'do-not-forward', url: 'private', email: ' test@example.com ' });
  assert.equal(payload.email, 'test@example.com');
  assert.equal(payload.interest, 'Enterprise pilot');
  assert.equal(Object.hasOwn(payload, 'secret'), false);
  assert.equal(Object.hasOwn(payload, 'url'), false);
  assert.equal(Object.hasOwn(payload, 'campaign'), false);
  const tagged = buildLead(fields, '?utm_source=linkedin&email=private');
  assert.equal(tagged.campaign, 'utm_source=linkedin');
  assert.equal(buildLead({ ...fields, interest: 'support' }).interest, 'Enterprise support subscription');
});

test('creative attribution survives multiple page hops while downloads and external paths stay clean', () => {
  const tags = '?utm_source=x&utm_medium=paid_social&utm_campaign=hormuz_signal_202609&utm_content=h03_evidence&email=private';
  const pricing = campaignLink('/enterprise/#plans', tags);
  const inquiry = campaignLink('/contact/?interest=review', new URL(pricing, 'https://example.com').search);
  const payload = buildLead(fields, new URL(inquiry, 'https://example.com').search);
  assert.match(payload.campaign, /utm_content=h03_evidence/);
  assert.doesNotMatch(inquiry, /email|private/);
  for (const href of ['/demo/gateway.txt', '/downloads/brief.pdf', '#recording', '/\\evil.example/']) assert.equal(campaignLink(href, tags), href);
});

test('request reference identifies the submission and QA status cannot come from an unknown form field', () => {
  const reference = createRequestReference({ randomUUID: () => '11111111-2222-4333-8444-555555555555' });
  const payload = buildLead({ ...fields, test_submission: true }, '', { reference });
  assert.equal(payload.request_reference, reference);
  assert.match(payload._subject, new RegExp(reference));
  assert.equal(payload.test_submission, undefined);
  const qa = buildLead(fields, '', { reference, testSubmission: true });
  assert.equal(qa.test_submission, true);
  assert.match(qa._subject, /^\[QA TEST\]/);
  assert.throws(() => buildLead(fields, '', { reference: 'arbitrary contact data' }));
});

test('Google booking configuration allows public schedules but rejects private calendar pages', () => {
  assert.equal(validateCommercialConfig({ ...blank, bookingUrl: 'https://calendar.google.com/calendar/u/0/appointments/schedules/AcZss_test' }).bookingUrl, 'https://calendar.google.com/calendar/u/0/appointments/schedules/AcZss_test');
  for (const url of ['https://calendar.google.com/calendar/u/0/r', 'https://calendar.google.com/calendar/u/0/appointments/schedules/abc?email=private', 'https://calendar.google.com.evil.example/calendar/u/0/appointments/schedules/abc']) assert.throws(() => validateCommercialConfig({ ...blank, bookingUrl: url }));
});

test('lead validation requires reply contact and rejects invalid fields before submission', () => {
  for (const field of ['name', 'email', 'organization', 'workflow']) assert.throws(() => buildLead({ ...fields, [field]: ' ' }));
  assert.throws(() => buildLead({ ...fields, email: 'invalid' }));
  assert.equal(buildLead({ ...fields, workflow: 'w'.repeat(2000) }).workflow.length, 1200);
  assert.equal(buildLead({ ...fields, interest: '__proto__' }).interest, 'Enterprise pilot');
});

test('a lead is acknowledged only after a positive service response', async () => {
  const payload = buildLead(fields);
  let calls = 0;
  await submitLead(endpoint, payload, async (url, options) => {
    calls++;
    assert.equal(url, endpoint);
    assert.equal(options.method, 'POST');
    assert.equal(options.credentials, 'omit');
    assert.equal(options.referrerPolicy, 'strict-origin-when-cross-origin');
    assert.equal(options.redirect, 'error');
    assert.deepEqual(JSON.parse(options.body), payload);
    assert.ok(options.signal instanceof AbortSignal);
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  });
  assert.equal(calls, 1);
});

test('rejections, malformed responses, timeouts and network failures never become success or automatic retries', async () => {
  for (const result of [new Response('{"ok":false}', { status: 200 }), new Response('{"ok":true}', { status: 429 }), new Response('{}'), new Response('<html>error</html>'), new Error('network')]) {
    let calls = 0;
    await assert.rejects(submitLead(endpoint, buildLead(fields), async () => {
      calls++;
      if (result instanceof Error) throw result;
      return result;
    }), /could not confirm receipt/);
    assert.equal(calls, 1);
  }
});

test('missing or invalid endpoints never transmit lead data', async () => {
  for (const url of ['', 'https://evil.example/form']) {
    let calls = 0;
    await assert.rejects(submitLead(url, buildLead(fields), async () => { calls++; }));
    assert.equal(calls, 0);
  }
});
