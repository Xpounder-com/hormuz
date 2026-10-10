import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { commercial, PAYMENT_KEYS, pricing } from '../lib/commercial.mjs';
import { customerQuestions } from '../lib/customer-questions.mjs';
import { siteUrl } from '../lib/site.mjs';

const allowedPrices = new Set(['$0', '$49.99', '$999', '$1,499', '$499', '$49']);
const pricePattern = /\$(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?/g;

test('the no-JavaScript walkthrough contains every current FAQ answer and destination', () => {
  const walkthrough = readFileSync(new URL('../public/demo/walkthrough.txt', import.meta.url), 'utf8');
  for (const [category, question, answer, href] of customerQuestions) {
    assert.ok(walkthrough.includes(`${category}: ${question}\n${answer}\n${siteUrl(href)}`), `Stale or missing answer: ${question}. Run npm run prepare:walkthrough.`);
  }
});

test('the catalog contains the approved offers and bounded support', () => {
  assert.equal(pricing.currency, 'USD');
  assert.deepEqual(Object.fromEntries(Object.entries(pricing).filter(([, v]) => typeof v === 'object').map(([key, v]) => [key, v.amount])), {
    software: 0, cloud: 49.99, appliance: 999, reservation: 49, onboarding: 1499, managedSite: 499,
  });
  assert.equal(pricing.cloud.unit, 'workspace/month');
  assert.equal(Object.hasOwn(pricing.appliance, 'includesSSD'), false);
  assert.equal(pricing.reservation.type, 'refundable-deposit');
  assert.equal(pricing.reservation.creditedToPurchase, true);
  assert.equal(pricing.reservation.plannedRollout, 'early 2027');
  assert.equal(Object.hasOwn(pricing.onboarding, 'includesSSD'), false);
  assert.equal(pricing.onboarding.remoteHours, 3);
  assert.equal(pricing.managedSite.includesCloudWorkspace, true);
  assert.equal(pricing.managedSite.supportHoursPerMonth, 1);
  assert.equal(pricing.managedSite.responseBusinessDays, 2);
});

test('current sales copy and billing answers cannot introduce another offer price or model', () => {
  const files = ['../marketing/COMMERCIAL_SETUP.md', '../marketing/OFFER.md', '../marketing/PILOT.md', '../marketing/SALES_WORKFLOW.md', '../marketing/MEASUREMENT.md', 'README.md'];
  const sources = files.map(file => [file, readFileSync(new URL('../' + file, import.meta.url), 'utf8')]);
  const billing = customerQuestions.filter(([category]) => category === 'Billing').map(row => row.join(' ')).join('\n');
  const walkthroughSource = readFileSync(new URL('../public/demo/walkthrough.txt', import.meta.url), 'utf8');
  const walkthrough = walkthroughSource.slice(walkthroughSource.indexOf('Billing:'));
  sources.push(['billing answers', billing], ['walkthrough billing', walkthrough]);
  for (const [name, text] of sources) {
    for (const amount of text.match(pricePattern) || []) assert.ok(allowedPrices.has(amount), name + ': ' + amount);
    assert.doesNotMatch(text, /Hosted Pro|custom (?:quote|pricing|proposal)|90.day (?:paid )?pilot|self.service support|\boverage(?:s)?\b/i, name);
  }
  assert.deepEqual(new Set(billing.match(pricePattern)), allowedPrices);
});

test('each paid offer has its own constrained Stripe destination setting', () => {
  assert.deepEqual(PAYMENT_KEYS, ['cloudPaymentUrl', 'appliancePaymentUrl', 'onboardingPaymentUrl', 'managedSitePaymentUrl', 'reservationPaymentUrl']);
  const configured = PAYMENT_KEYS.map(key => commercial[key]).filter(Boolean);
  assert.equal(new Set(configured).size, configured.length, 'Each checkout must use the price and interval of its own offer');
  for (const url of configured) assert.match(url, /^https:\/\/buy\.stripe\.com\/[a-zA-Z0-9]+$/);
});
