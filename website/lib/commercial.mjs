import { commercialConfig } from './commercial-config.mjs';

export const PILOT_PRICE = '$15,000';
export const SUPPORT_PRICE = '$2,000';

export function validateCommercialConfig(config) {
  const result = { formEndpoint: '', bookingUrl: '', pilotPaymentUrl: '', supportPaymentUrl: '' };
  for (const key of ['formEndpoint', 'bookingUrl', 'pilotPaymentUrl', 'supportPaymentUrl']) {
    const value = config[key];
    if (typeof value !== 'string') throw new Error(`Invalid commercial setting: ${key}`);
    if (!value) { result[key] = ''; continue; }
    const url = new URL(value);
    if (url.protocol !== 'https:' || url.username || url.password || url.port || url.hash || url.search) throw new Error(`Expected a clean public HTTPS URL: ${key}`);
    if (key === 'formEndpoint' && (url.hostname !== 'formspree.io' || !/^\/f\/[a-zA-Z0-9]+$/.test(url.pathname))) throw new Error('Expected a Formspree form endpoint');
    if (key.endsWith('PaymentUrl') && (url.hostname !== 'buy.stripe.com' || !/^\/[a-zA-Z0-9]+$/.test(url.pathname) || url.pathname.startsWith('/test_'))) throw new Error('Expected a live public Stripe payment link');
    if (key === 'bookingUrl' && !['calendly.com', 'cal.com'].includes(url.hostname)) throw new Error('Expected a public Calendly or Cal.com booking URL');
    result[key] = url.href;
  }
  return Object.freeze(result);
}

export const commercial = validateCommercialConfig(commercialConfig);
