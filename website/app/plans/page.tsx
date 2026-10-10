import { OfferingPaths } from '../components/OfferingPaths';
import { EnterprisePlans } from '../components/EnterprisePlans';
import { CampaignLink } from '../components/CampaignLink';
import { PageFrame, PageHero } from '../components/PageFrame';
import { pageMetadata } from '../../lib/metadata';
import { SOFTWARE_PRICE, CLOUD_PRICE, MANAGED_SITE_PRICE } from '../../lib/commercial.mjs';
import { CONTACT_EMAIL, sitePath } from '../../lib/site.mjs';
import { StripeCheckoutLink } from '../components/StripeCheckoutLink';

export const metadata = pageMetadata('SME pricing — Free software, Cloud & managed sites | Hormuz', 'Start with free Hormuz software at ' + SOFTWARE_PRICE + '. Choose Cloud at ' + CLOUD_PRICE + ' per workspace per month or a managed site at ' + MANAGED_SITE_PRICE + ' per month with Cloud included. Confirm fit and activation before subscribing.', '/plans/');

export default function PlansPage() {
  return <PageFrame active="plans">
    <PageHero eyebrow="Plans for small and medium teams" title={<>Start with free software.<br /><span>Add hosting or operating help.</span></>}>
      <p>For software agencies, engineering teams, and IT teams using supported AI tools: start with one workflow, then choose who operates the gateway. Cloud adds hosting. A managed site includes Cloud and a defined support allowance.</p>
      <p>You keep your provider accounts and pay providers directly for model usage. Confirm paid-service scope, capacity, and activation before checkout; monthly billing starts when you subscribe.</p>
      <div className="hero-actions"><CampaignLink className="button button-primary" href={sitePath('/docs/')}>Start free →</CampaignLink><a className="button button-ghost" href="#offers">Compare software, Cloud & managed sites ↓</a></div>
    </PageHero>
    <OfferingPaths />
    <section className="section prose-section" id="activation"><h2>Confirm. Subscribe. Activate.</h2><ol className="numbered-list"><li>Confirm your supported workflow, coverage, capacity, and activation date with Hormuz.</li><li>Use the matching Stripe checkout after that agreement. Monthly billing starts immediately at checkout.</li><li>Hormuz verifies your payment and completes the agreed setup checks, then activates your workspace. Checkout does not activate it automatically.</li></ol></section>
    <section className="section prose-section" id="cloud"><p className="section-label">CLOUD</p><h2>One workspace subscription.</h2>
      <p><strong>{CLOUD_PRICE} USD per workspace per month.</strong> You connect your own provider accounts and pay providers directly for model usage. Supported routes, traffic limits, and service coverage are confirmed for your workload before activation.</p>
      <p><strong>Cloud is accepting inquiries.</strong> The hosted service is planned. Contact us to confirm support for your tools, traffic, and an activation date before subscribing. Sending an inquiry takes no payment and creates no workspace.</p>
      <StripeCheckoutLink offer="cloud">Subscribe to Cloud — {CLOUD_PRICE}/month</StripeCheckoutLink>
      <p className="field-hint">Monthly billing begins at checkout and renews until canceled. Email <a href={`mailto:${CONTACT_EMAIL}?subject=Cancel%20Hormuz%20Cloud`}>{CONTACT_EMAIL}</a> before the next renewal with your subscription reference to stop renewal; your current paid period remains available.</p>
      <CampaignLink className="text-link" href={sitePath('/contact/?interest=cloud')}>Confirm your workflow and activation →</CampaignLink>
    </section>
    <section className="section prose-section" id="managed"><p className="section-label">MANAGED SITES</p><h2>Cloud and defined operating help in one fee.</h2>
      <p><strong>{MANAGED_SITE_PRICE} USD per managed site per month.</strong> Choose this when your agreed site needs the stated monitoring, updates tested for your setup, recovery guidance, and support allowance. Its associated Cloud workspace is included, so you do not pay a separate Cloud subscription for that workspace.</p>
      <p>Agree the workloads, appliances, operating capacity, monitoring coverage, and activation date first. Appliance hardware is purchased separately. Review the business-hours support allowance and exclusions before subscribing.</p>
      <div className="resource-actions"><CampaignLink className="text-link" href={sitePath('/contact/?interest=managed')}>Confirm managed-site fit →</CampaignLink><CampaignLink className="text-link" href={sitePath('/enterprise/#support')}>Support scope and cancellation →</CampaignLink></div>
    </section>
    <EnterprisePlans includeManagedSite={false} />
    <section className="section prose-section" id="details"><p className="section-label">WHAT YOUR PURCHASE COVERS</p><h2>Your provider accounts remain yours.</h2>
      <p>The software has no Hormuz license fee. An appliance purchase includes the stated hardware package. The onboarding package includes that appliance and the defined remote installation scope.</p>
      <p>Managed-site service includes its associated Cloud workspace. The monthly site fee covers the stated monitoring, update, recovery, and support allowance. Provider usage, customer networking, electricity, and applicable taxes are separate.</p>
      <p>Every paid offer has a Stripe checkout button on this page. Reservations are one-time payments; Cloud and managed sites are monthly subscriptions. The onboarding package includes the appliance, and managed-site service includes its associated Cloud workspace.</p>
      <p>Already reserved? Use the Stripe invoice provided for your remaining appliance balance. The full-price appliance buttons are for purchases without a reservation deposit. An inquiry takes no payment, and a return page is not proof of payment; Hormuz verifies the Stripe payment before fulfillment or activation.</p>
      <p>We check compatibility and capacity before Cloud activation or hardware delivery. Your provider bill determines actual charges; local token estimates and smaller tool blocks are not billed savings.</p>
    </section>
  </PageFrame>;
}
