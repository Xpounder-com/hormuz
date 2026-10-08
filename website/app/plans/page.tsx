import { OfferingPaths } from '../components/OfferingPaths';
import { EnterprisePlans } from '../components/EnterprisePlans';
import { CampaignLink } from '../components/CampaignLink';
import { PageFrame, PageHero } from '../components/PageFrame';
import { pageMetadata } from '../../lib/metadata';
import { SOFTWARE_PRICE, CLOUD_PRICE, APPLIANCE_PRICE, ONBOARDING_PRICE, MANAGED_SITE_PRICE, RESERVATION_PRICE, pricing } from '../../lib/commercial.mjs';
import { CONTACT_EMAIL, sitePath } from '../../lib/site.mjs';
import { StripeCheckoutLink } from '../components/StripeCheckoutLink';

export const metadata = pageMetadata('Pricing & Stripe checkout — Hormuz', 'Software ' + SOFTWARE_PRICE + '. Pay through Stripe for Cloud at ' + CLOUD_PRICE + ' per workspace per month, an appliance at ' + APPLIANCE_PRICE + ', scoped onboarding at ' + ONBOARDING_PRICE + ', or a managed site at ' + MANAGED_SITE_PRICE + ' per month. Reserve an appliance for ' + RESERVATION_PRICE + '.', '/plans/');

export default function PlansPage() {
  return <PageFrame active="plans">
    <PageHero eyebrow="Software · Cloud · Appliance" title={<>Free software.<br /><span>Clear prices for hosting and hardware.</span></>}>
      <p>Choose your offer below and pay securely through Stripe. Software is free. Appliances are coming soon, with rollout planned for {pricing.reservation.plannedRollout}; reserve with a fully refundable {RESERVATION_PRICE} deposit.</p>
      <p>For services and full appliance purchases, confirm your scope and start date with Hormuz before paying. Checkout does not automatically activate a workspace or establish a shipping date.</p>
      <div className="hero-actions"><CampaignLink className="button button-primary" href={sitePath('/contact/?interest=work')}>Confirm your workflow →</CampaignLink><a className="button button-ghost" href="#offers">Compare existing offers ↓</a></div>
    </PageHero>
    <section className="section prose-section" id="activation"><h2>Confirm. Subscribe. Activate.</h2><ol className="numbered-list"><li>Confirm your supported workflow, coverage, capacity, and activation date with Hormuz.</li><li>Use the matching Stripe checkout after that agreement. Monthly billing starts immediately at checkout.</li><li>Hormuz verifies the payment and agreed qualification before activating the workspace. Payment alone does not provision a production gateway.</li></ol></section>
    <OfferingPaths />
    <EnterprisePlans />
    <section className="section prose-section" id="cloud"><p className="section-label">CLOUD</p><h2>One workspace subscription.</h2>
      <p><strong>{CLOUD_PRICE} USD per workspace per month.</strong> You connect your own provider accounts and pay providers directly for model usage. Supported routes, traffic limits, and service coverage are confirmed for your workload before activation.</p>
      <p><strong>Cloud is accepting inquiries.</strong> Hosted onboarding, subscriptions, and production qualification must pass before activation. An inquiry creates no subscription or production gateway.</p>
      <StripeCheckoutLink offer="cloud">Subscribe to Cloud — {CLOUD_PRICE}/month</StripeCheckoutLink>
      <p className="field-hint">Monthly billing begins at checkout and renews until canceled. Email <a href={`mailto:${CONTACT_EMAIL}?subject=Cancel%20Hormuz%20Cloud`}>{CONTACT_EMAIL}</a> before the next renewal with your subscription reference to stop renewal; your current paid period remains available.</p>
      <CampaignLink className="text-link" href={sitePath('/contact/?interest=cloud')}>Confirm your workflow and activation →</CampaignLink>
    </section>
    <section className="section prose-section" id="details"><p className="section-label">WHAT YOUR PURCHASE COVERS</p><h2>Your provider accounts remain yours.</h2>
      <p>The software has no Hormuz license fee. An appliance purchase includes the stated hardware package. The onboarding package includes that appliance and the defined remote installation scope.</p>
      <p>Managed-site service includes its associated Cloud workspace. The monthly site fee covers the stated monitoring, update, recovery, and support allowance. Provider usage, customer networking, electricity, and applicable taxes are separate.</p>
      <p>Every paid offer has a Stripe checkout button on this page. Reservations are one-time payments; Cloud and managed sites are monthly subscriptions. The onboarding package includes the appliance, and managed-site service includes its associated Cloud workspace.</p>
      <p>Already reserved? Use the Stripe invoice provided for your remaining appliance balance. The full-price appliance buttons are for purchases without a reservation deposit. An inquiry takes no payment, and a return page is not proof of payment; Hormuz verifies the Stripe payment before fulfillment or activation.</p>
      <p>Cloud and hardware require completed technical qualification. Local token estimates and byte reductions do not establish a reduction in your provider bill.</p>
    </section>
  </PageFrame>;
}
