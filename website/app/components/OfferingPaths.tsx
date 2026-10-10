import { CampaignLink } from './CampaignLink';
import { PriceAmount } from './PriceCard';
import { sitePath } from '../../lib/site.mjs';
import { SOFTWARE_PRICE, CLOUD_PRICE } from '../../lib/commercial.mjs';
import { StripeCheckoutLink } from './StripeCheckoutLink';
import { ManagedSitePlan } from './ManagedSitePlan';

export function OfferingPaths() {
  return <section className="section offer-section" id="offers" aria-labelledby="offers-title">
    <div className="section-heading"><p className="section-label">FOR SMALL AND GROWING TEAMS</p>
      <h2 id="offers-title">Start free.<br />Choose the help your team needs.</h2>
      <p className="offer-intro">Run the software yourself, choose Cloud when you want hosting, or discuss a managed site when you need defined operating help. You can choose the service that fits your supported workflow directly. Your provider accounts remain yours, and you pay providers for model usage.</p>
    </div>
    <div className="offer-grid conversion-offers">
      <article className="offer-card offer-featured">
        <p className="section-label">FOR TEAMS THAT OPERATE IT THEMSELVES</p><h3>Free software</h3>
        <PriceAmount amount={SOFTWARE_PRICE} period="Software license · Apache-2.0" />
        <p>Use the Personal Optimizer locally or operate your own gateway. Existing software capabilities remain open source.</p>
        <ul><li>Qualified local context optimization on supported clients</li><li>Gateway policy, secret controls, and usage reporting</li><li>Your provider accounts and infrastructure</li><li>Documentation and community support</li></ul>
        <CampaignLink className="button button-primary" href={sitePath('/docs/')}>Start with free software <span aria-hidden="true">↗</span></CampaignLink>
        <p className="field-hint">No card or Hormuz subscription required. Supported platforms and workflow boundaries are listed in the setup guide.</p>
      </article>
      <article className="offer-card">
        <p className="section-label">FOR TEAMS THAT WANT HOSTING</p><h3>Cloud</h3>
        <PriceAmount amount={CLOUD_PRICE} period="USD / workspace / month" />
        <p>A planned Hormuz-hosted gateway and dashboard for your supported application traffic. Your team operates its applications and provider accounts.</p>
        <ul><li>One hosted workspace</li><li>Team controls and usage reporting</li><li>Administrative users included</li><li>Provider accounts remain yours</li></ul>
        <CampaignLink className="button button-primary" href={sitePath('/contact/?interest=cloud')}>Confirm Cloud fit <span aria-hidden="true">→</span></CampaignLink>
        <p className="field-hint">Accepting inquiries. Confirm your application, provider, expected traffic, retention needs, service coverage, and activation before subscribing. Cloud qualification must pass before activation.</p>
        <StripeCheckoutLink offer="cloud" className="button button-outline">Subscribe to Cloud — {CLOUD_PRICE}/month</StripeCheckoutLink>
        <p className="field-hint">Monthly billing starts at checkout and renews until canceled. <CampaignLink href={sitePath('/plans/#cloud')}>Cloud and cancellation details →</CampaignLink></p>
      </article>
      <ManagedSitePlan />
    </div>
    <p className="pricing-cost-note">Prices are in USD. Model-provider usage, customer infrastructure, and applicable taxes are separate. Managed sites include their associated Cloud workspace. Confirm paid-service scope and activation before checkout.</p>
  </section>;
}
