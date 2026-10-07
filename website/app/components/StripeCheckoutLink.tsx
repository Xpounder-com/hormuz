import { commercial } from '../../lib/commercial.mjs';

const checkoutUrls = {
  cloud: commercial.cloudPaymentUrl,
  appliance: commercial.appliancePaymentUrl,
  onboarding: commercial.onboardingPaymentUrl,
  managed: commercial.managedSitePaymentUrl,
  reservation: commercial.reservationPaymentUrl,
};

export type PaidOffer = keyof typeof checkoutUrls;

export function StripeCheckoutLink({
  offer,
  children,
  className = 'button button-primary',
}: {
  offer: PaidOffer;
  children: React.ReactNode;
  className?: string;
}) {
  const href = checkoutUrls[offer];
  if (!href) return null;
  return <a className={className} href={href} data-stripe-offer={offer} rel="noreferrer">
    {children} <span aria-hidden="true">↗</span>
  </a>;
}
