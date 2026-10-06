import { CampaignLink } from './CampaignLink';
import { PriceAmount } from './PriceCard';
import { sitePath } from '../../lib/site.mjs';
import { SOFTWARE_PRICE, CLOUD_PRICE } from '../../lib/commercial.mjs';
import { StripeCheckoutLink } from './StripeCheckoutLink';

export function OfferingPaths() {
  return <section className="section offer-section" id="offers" aria-labelledby="offers-title">
    <div className="section-heading"><p className="section-label">SOFTWARE AND CLOUD</p>
      <h2 id="offers-title">Run Hormuz yourself.<br />Or choose a hosted workspace.</h2>
      <p className="offer-intro">Use the free Personal Optimizer or self-hosted gateway, or choose Hormuz Cloud. Paid services use secure Stripe checkout. You connect your own provider accounts and pay providers directly for model usage.</p>
    </div>
    <div className="offer-grid offer-grid-two conversion-offers">
      <article className="offer-card offer-featured">
        <p className="section-label">RUN ON YOUR INFRASTRUCTURE</p><h3>Software</h3>
        <PriceAmount amount={SOFTWARE_PRICE} period="Software license · Apache-2.0" />
        <p>Use the Personal Optimizer locally or operate your own gateway. Existing software capabilities remain open source.</p>
        <ul><li>Qualified local context optimization on supported clients</li><li>Gateway policy, secret controls, and usage reporting</li><li>Your provider accounts and infrastructure</li><li>Documentation and community support</li></ul>
        <CampaignLink className="button button-primary" href={sitePath('/docs/')}>Install the software <span aria-hidden="true">↗</span></CampaignLink>
        <p className="field-hint">No card or Hormuz subscription required. Supported platforms and workflow boundaries are listed in the setup guide.</p>
      </article>
      <article className="offer-card">
        <p className="section-label">A HOSTED WORKSPACE</p><h3>Cloud</h3>
        <PriceAmount amount={CLOUD_PRICE} period="USD / workspace / month" />
        <p>A planned Hormuz-hosted gateway and dashboard for supported application traffic.</p>
        <ul><li>One hosted workspace</li><li>Team controls and usage reporting</li><li>Administrative users included</li><li>Provider accounts remain yours</li></ul>
        <StripeCheckoutLink offer="cloud">Subscribe to Cloud — {CLOUD_PRICE}/month</StripeCheckoutLink>
        <p className="field-hint">One workspace. Confirm compatibility, traffic limits, service coverage, and activation before subscribing. Monthly billing starts at checkout and renews until canceled. <CampaignLink href={sitePath('/plans/#cloud')}>Cloud and cancellation details →</CampaignLink></p>
        <CampaignLink className="text-link" href={sitePath('/contact/?interest=cloud')}>Ask about your workflow →</CampaignLink>
      </article>
    </div>
  </section>;
}
