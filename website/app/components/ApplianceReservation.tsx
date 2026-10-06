import { APPLIANCE_PRICE, ONBOARDING_PRICE, RESERVATION_PRICE, commercial, pricing } from '../../lib/commercial.mjs';
import { CONTACT_EMAIL, sitePath } from '../../lib/site.mjs';
import { CampaignLink } from './CampaignLink';
import { StripeCheckoutLink } from './StripeCheckoutLink';

export function ApplianceReservation() {
  return <section className="section appliance-reservation" id="reserve" aria-labelledby="reserve-title">
    <div className="section-heading">
      <p className="section-label">COMING SOON · PLANNED ROLLOUT: {pricing.reservation.plannedRollout.toUpperCase()}</p>
      <h2 id="reserve-title">Reserve yours — {RESERVATION_PRICE}.</h2>
      <p className="offer-intro">An Hormuz appliance for supported AI traffic inside your network. Choose the {APPLIANCE_PRICE} appliance or the {ONBOARDING_PRICE} package with scoped onboarding when delivery is confirmed.</p>
    </div>
    <div className="reservation-details">
      <div>
        <p><strong>One-time, fully refundable deposit per appliance.</strong> Your {RESERVATION_PRICE} is credited toward the appliance purchase. Stripe displays any applicable tax at checkout. There is no reservation subscription or automatic balance charge.</p>
        <p><strong>Cancel anytime before fulfillment for a full refund.</strong> Email <a href={`mailto:${CONTACT_EMAIL}?subject=Cancel%20Hormuz%20appliance%20reservation`}>{CONTACT_EMAIL}</a> with your Stripe receipt reference. We refund the full reservation payment to the original payment method and cancel the reservation.</p>
        <p className="field-hint">Rollout is planned for {pricing.reservation.plannedRollout}; this is a target, not a guaranteed shipping date. We confirm compatibility, delivery arrangements, applicable taxes, and your chosen package before collecting the balance through Stripe. You can cancel if the timing or scope does not suit you.</p>
      </div>
      <div className="reservation-action">
        {commercial.reservationPaymentUrl
          ? <><StripeCheckoutLink offer="reservation">Reserve yours — {RESERVATION_PRICE}</StripeCheckoutLink><p className="field-hint">Secure checkout on Stripe. Your reservation is confirmed after successful payment, with a Stripe receipt.</p></>
          : <><CampaignLink className="button button-primary" href={sitePath('/contact/?interest=reservation')}>Request a reservation <span aria-hidden="true">↗</span></CampaignLink><p className="field-hint">Stripe checkout is being prepared. This request takes no payment and does not confirm a paid reservation.</p></>}
        <CampaignLink className="text-link" href={sitePath('/enterprise/#onboarding')}>Compare appliance packages →</CampaignLink>
      </div>
    </div>
  </section>;
}
