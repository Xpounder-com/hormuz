import { commercialConfig } from './commercial-config.mjs';
import pricing from './pricing.json' with { type: 'json' };

export { pricing };
const money = amount => '$' + amount.toLocaleString('en-US', { maximumFractionDigits: 2 });
export const SOFTWARE_PRICE = money(pricing.software.amount);
export const CLOUD_PRICE = money(pricing.cloud.amount);
export const APPLIANCE_PRICE = money(pricing.appliance.amount);
export const RESERVATION_PRICE = money(pricing.reservation.amount);
export const ONBOARDING_PRICE = money(pricing.onboarding.amount);
export const MANAGED_SITE_PRICE = money(pricing.managedSite.amount);
export const PAYMENT_KEYS = Object.freeze(['cloudPaymentUrl', 'appliancePaymentUrl', 'onboardingPaymentUrl', 'managedSitePaymentUrl', 'reservationPaymentUrl']);

export function validateCommercialConfig(config) {
  /** @type {Record<string, string>} */
  const result = {};
  for (const key of ['formEndpoint', 'bookingUrl', ...PAYMENT_KEYS]) {
    const value = config[key];
    if (typeof value !== 'string') throw new Error('Invalid commercial setting: ' + key);
    if (!value) { result[key] = ''; continue; }
    const url = new URL(value);
    if (url.protocol !== 'https:' || url.username || url.password || url.port || url.hash || url.search) throw new Error('Expected a clean public HTTPS URL: ' + key);
    if (key === 'formEndpoint' && (url.hostname !== 'formspree.io' || !/^\/f\/[a-zA-Z0-9]+$/.test(url.pathname))) throw new Error('Expected a Formspree form endpoint');
    if (key.endsWith('PaymentUrl') && (url.hostname !== 'buy.stripe.com' || !/^\/[a-zA-Z0-9]+$/.test(url.pathname) || url.pathname.startsWith('/test_'))) throw new Error('Expected a live public Stripe payment link');
    if (key === 'bookingUrl' && !(['calendly.com', 'cal.com'].includes(url.hostname) ||
      (url.hostname === 'calendar.google.com' && /^\/calendar\/u\/0\/appointments\/schedules\/[a-zA-Z0-9_-]+$/.test(url.pathname)))) throw new Error('Expected a public scheduling URL');
    result[key] = url.href;
  }
  return Object.freeze(result);
}

export const commercial = validateCommercialConfig(commercialConfig);
export const managedSiteSupport = Object.freeze({
  price: MANAGED_SITE_PRICE,
  paymentUrl: commercial.managedSitePaymentUrl,
  hoursPerMonth: pricing.managedSite.supportHoursPerMonth,
  responseBusinessDays: pricing.managedSite.responseBusinessDays,
});
