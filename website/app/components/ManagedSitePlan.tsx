import { CampaignLink } from './CampaignLink';
import { PriceAmount } from './PriceCard';
import { StripeCheckoutLink } from './StripeCheckoutLink';
import { MANAGED_SITE_PRICE, pricing } from '../../lib/commercial.mjs';
import { sitePath } from '../../lib/site.mjs';

export function ManagedSitePlan() {
  return <article className="offer-card">
    <p className="section-label">FOR TEAMS THAT WANT OPERATING HELP</p><h3>Managed site</h3>
    <PriceAmount amount={MANAGED_SITE_PRICE} period="USD / managed site / month" />
    <p>Defined operating help for one agreed site, with its associated Cloud workspace included.</p>
    <ul><li>Dashboard and appliance health monitoring</li><li>Qualified updates and recovery guidance</li><li>Up to {pricing.managedSite.supportHoursPerMonth} hour of remote assistance per billing month</li><li>Support response within {pricing.managedSite.responseBusinessDays} business days</li></ul>
    <CampaignLink className="button button-primary" href={sitePath('/contact/?interest=managed')}>Confirm managed-site fit <span aria-hidden="true">→</span></CampaignLink>
    <p className="field-hint">Cloud is included: no separate Cloud subscription for this workspace. Appliance hardware is purchased separately. Business-hours support; the allowance does not include custom development or round-the-clock response.</p>
    <p className="field-hint">Confirm the site, workloads, device count, capacity, monitoring coverage, and activation date before subscribing. Operating coverage requires qualification. Monthly billing starts at checkout and renews until canceled.</p>
    <StripeCheckoutLink offer="managed" className="button button-outline">Subscribe to managed site — {MANAGED_SITE_PRICE}/month</StripeCheckoutLink>
    <p className="field-hint"><CampaignLink href={sitePath('/enterprise/#support')}>Support allowance and cancellation →</CampaignLink></p>
  </article>;
}
