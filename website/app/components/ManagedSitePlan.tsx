import { CampaignLink } from './CampaignLink';
import { PriceAmount } from './PriceCard';
import { StripeCheckoutLink } from './StripeCheckoutLink';
import { MANAGED_SITE_PRICE, pricing } from '../../lib/commercial.mjs';
import { sitePath } from '../../lib/site.mjs';

export function ManagedSitePlan() {
  return <article className="offer-card" id="managed">
    <p className="section-label">ADD OPERATING HELP</p><h3>Managed site</h3>
    <PriceAmount amount={MANAGED_SITE_PRICE} period="USD / site / month" />
    <p>Cloud included, with operating help for one site.</p>
    <ul><li>Dashboard and appliance health monitoring</li><li>Tested updates and recovery guidance</li><li>Up to {pricing.managedSite.supportHoursPerMonth} hour of remote assistance per month</li><li>Response within {pricing.managedSite.responseBusinessDays} business days</li></ul>
    <CampaignLink className="button button-primary" href={sitePath('/contact/?interest=managed')}>Discuss a managed site <span aria-hidden="true">→</span></CampaignLink>
    <p className="field-hint">Hardware separate. Business-hours support. Scope and activation agreed first.</p>
    <details className="offer-checkout"><summary>Ready to subscribe?</summary><p>Monthly billing starts at checkout and renews until canceled. The included workspace has no separate Cloud fee. Provider usage and hardware are separate. Review <CampaignLink href={sitePath('/enterprise/#support')}>support hours, exclusions, and cancellation</CampaignLink> before subscribing.</p><StripeCheckoutLink offer="managed" className="button button-outline">Subscribe to managed site — {MANAGED_SITE_PRICE}/month</StripeCheckoutLink></details>
  </article>;
}
