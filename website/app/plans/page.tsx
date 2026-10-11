import { OfferingPaths } from '../components/OfferingPaths';
import { EnterprisePlans } from '../components/EnterprisePlans';
import { CampaignLink } from '../components/CampaignLink';
import { PageFrame, PageHero } from '../components/PageFrame';
import { pageMetadata } from '../../lib/metadata';
import { SOFTWARE_PRICE, CLOUD_PRICE, MANAGED_SITE_PRICE, APPLIANCE_PRICE, ONBOARDING_PRICE, pricing } from '../../lib/commercial.mjs';
import { sitePath } from '../../lib/site.mjs';

export const metadata = pageMetadata('Pricing — Free software, Cloud & managed sites | Hormuz', 'Start with free Hormuz software at ' + SOFTWARE_PRICE + '. Cloud is ' + CLOUD_PRICE + ' per workspace per month. Managed sites are ' + MANAGED_SITE_PRICE + ' per month with Cloud included. Confirm scope and activation first.', '/plans/');

export default function PlansPage() {
  return <PageFrame active="plans">
    <PageHero eyebrow="PRICING" title="Choose your setup.">
      <p>Run Hormuz yourself, choose Cloud hosting, or add managed-site support. Your tools and provider accounts stay yours.</p>
    </PageHero>
    <OfferingPaths />
    <section className="section simple-terms" id="details" aria-labelledby="details-title">
      <h2 id="details-title">Before you subscribe.</h2>
      <p id="activation">Confirm your supported workflow, scope, capacity, and activation date with us first. Monthly billing starts at checkout; activation follows verified payment and the agreed setup checks.</p>
      <CampaignLink className="text-link" href={sitePath('/contact/?interest=work')}>Discuss your setup →</CampaignLink>
      <details className="simple-disclosure"><summary>What does managed-site support cover?</summary><p>One associated Cloud workspace, monitoring, tested updates, recovery guidance, and up to {pricing.managedSite.supportHoursPerMonth} hour of remote assistance per billing month. Hardware is purchased separately.</p><p>Support runs Monday–Friday, 9 am–5 pm America/Chicago, excluding local public holidays. Response target: within {pricing.managedSite.responseBusinessDays} business days. Unused hours do not roll over. Custom development, on-site work, replacement hardware, and 24/7 response are excluded.</p><CampaignLink className="text-link" href={sitePath('/enterprise/#support')}>Full support scope and cancellation →</CampaignLink></details>
      <details className="simple-disclosure"><summary>What costs are separate?</summary><p>Model-provider charges, customer infrastructure, networking, electricity, and applicable taxes. You keep your provider accounts and pay providers directly. Usage reports use configured cost estimates; your provider bill determines actual charges.</p></details>
    </section>
    <details className="simple-hardware">
      <summary><span>Need a local appliance?</span><span className="simple-caption">Coming soon · Planned for {pricing.reservation.plannedRollout}</span><span className="simple-caption">{APPLIANCE_PRICE} · {ONBOARDING_PRICE} with scoped onboarding</span></summary>
      <EnterprisePlans includeManagedSite={false} />
    </details>
  </PageFrame>;
}
