import { sitePath } from '../../lib/site.mjs';
import { APPLIANCE_PRICE, ONBOARDING_PRICE, MANAGED_SITE_PRICE, pricing } from '../../lib/commercial.mjs';
import { CampaignLink } from './CampaignLink';
import { PriceAmount } from './PriceCard';
import { ApplianceReservation } from './ApplianceReservation';
import { StripeCheckoutLink } from './StripeCheckoutLink';

export function EnterprisePlans() {
  return <><section className="section offer-section" id="plans" aria-labelledby="plans-title">
    <div className="section-heading"><p className="section-label">APPLIANCE AND SITE MANAGEMENT</p>
      <h2 id="plans-title">Local processing.<br />Choose the setup and support you need.</h2>
      <p className="offer-intro">Coming soon: a preconfigured gateway appliance for supported requests inside your network. Rollout is planned for {pricing.reservation.plannedRollout}. Local compaction and secret redaction are part of the planned appliance. Hardware and workflow qualification must pass before delivery.</p>
    </div>
    <div className="offer-grid conversion-offers">
      <article className="offer-card"><p className="section-label">COMING SOON · CUSTOMER IT INSTALLS</p><h3>Appliance</h3>
        <PriceAmount amount={APPLIANCE_PRICE} period="USD / appliance · one-time" />
        <p>An assembled appliance including SSD, enclosure, cooling, power supply, and a tested Hormuz installation.</p>
        <ul><li>SSD included</li><li>Setup guide and supported configuration</li><li>Local processing for qualified workflows</li><li>Your IT team handles deployment</li></ul>
        <a className="button button-primary" href="#reserve">Reserve yours <span aria-hidden="true">↓</span></a>
        <div className="confirmed-order"><StripeCheckoutLink offer="appliance" className="button button-outline">Pay for appliance — {APPLIANCE_PRICE}</StripeCheckoutLink>
          <p className="field-hint">For a confirmed delivery without a reservation deposit. Coming soon; full-price checkout does not establish a shipping date. Reserved buyers pay their balance through the Stripe invoice Hormuz provides.</p></div>
      </article>
      <article className="offer-card offer-featured"><p className="section-label">COMING SOON · ASSISTED INSTALLATION</p><h3>Appliance with scoped onboarding</h3>
        <PriceAmount amount={ONBOARDING_PRICE} period="USD / appliance · one-time" />
        <p>The same appliance with SSD, plus remote assistance for the agreed installation.</p>
        <ul><li>Up to {pricing.onboarding.remoteHours} hours of remote onboarding</li><li>One provider and one supported application</li><li>Initial policy configuration and acceptance checks</li><li>Administrator handoff</li></ul>
        <a className="button button-primary" href="#reserve">Reserve yours <span aria-hidden="true">↓</span></a>
        <div className="confirmed-order"><StripeCheckoutLink offer="onboarding" className="button button-outline">Pay for onboarding package — {ONBOARDING_PRICE}</StripeCheckoutLink>
          <p className="field-hint">Includes the appliance and replaces its standalone purchase. Use after scope and delivery are confirmed, without a reservation deposit. Reserved buyers pay the Stripe balance invoice.</p></div>
      </article>
      <article className="offer-card"><p className="section-label">OPTIONAL ONGOING SERVICE</p><h3>Managed site</h3>
        <PriceAmount amount={MANAGED_SITE_PRICE} period="USD / managed site / month" />
        <p>Management for one agreed site, including the associated Cloud workspace.</p>
        <ul><li>Dashboard and appliance health monitoring</li><li>Qualified updates and recovery guidance</li><li>Up to {pricing.managedSite.supportHoursPerMonth} hour of remote assistance per billing month</li><li>Support response within {pricing.managedSite.responseBusinessDays} business days</li></ul>
        <StripeCheckoutLink offer="managed">Subscribe to managed site — {MANAGED_SITE_PRICE}/month</StripeCheckoutLink>
        <p className="field-hint">Business-hours support. The included workspace has no separate Cloud subscription charge. Appliance hardware is purchased separately.</p>
        <p className="field-hint">Confirm the site, operating coverage, capacity, and activation date before subscribing. Monthly billing starts at checkout and renews until canceled. <CampaignLink href={sitePath('/enterprise/#support')}>Support allowance and cancellation →</CampaignLink></p>
      </article>
    </div>
    <p className="pricing-cost-note">Prices are in USD. Model-provider usage, customer infrastructure, and applicable taxes are separate. Availability and supported workloads are confirmed before payment.</p>
  </section><ApplianceReservation /></>;
}
