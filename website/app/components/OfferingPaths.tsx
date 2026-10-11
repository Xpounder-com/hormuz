import { CampaignLink } from './CampaignLink';
import { PriceAmount } from './PriceCard';
import { CONTACT_EMAIL, sitePath } from '../../lib/site.mjs';
import { SOFTWARE_PRICE, CLOUD_PRICE } from '../../lib/commercial.mjs';
import { StripeCheckoutLink } from './StripeCheckoutLink';
import { ManagedSitePlan } from './ManagedSitePlan';

export function OfferingPaths() {
  return <section className="section offer-section simple-offers" id="offers" aria-labelledby="offers-title">
    <h2 className="sr-only" id="offers-title">Compare plans</h2>
    <div className="offer-grid conversion-offers">
      <article className="offer-card">
        <p className="section-label">RUN IT YOURSELF</p><h3>Free software</h3>
        <PriceAmount amount={SOFTWARE_PRICE} period="Software license · Apache-2.0" />
        <p>Operate Hormuz on your own infrastructure.</p>
        <ul><li>Gateway budgets and request controls</li><li>Usage reporting</li><li>Local optimization on supported clients</li><li>Documentation and community support</li></ul>
        <CampaignLink className="button button-primary" href={sitePath('/docs/')}>Start free <span aria-hidden="true">→</span></CampaignLink>
        <p className="field-hint">No card or subscription required.</p>
      </article>
      <article className="offer-card" id="cloud">
        <p className="section-label">ADD HOSTING</p><h3>Cloud</h3>
        <PriceAmount amount={CLOUD_PRICE} period="USD / workspace / month" />
        <p>A planned hosted gateway and dashboard.</p>
        <ul><li>One hosted workspace</li><li>Team controls and usage reporting</li><li>Administrative users included</li><li>Your own provider accounts</li></ul>
        <CampaignLink className="button button-primary" href={sitePath('/contact/?interest=cloud')}>Discuss Cloud <span aria-hidden="true">→</span></CampaignLink>
        <p className="field-hint">Accepting inquiries. Scope and activation agreed first.</p>
        <details className="offer-checkout"><summary>Ready to subscribe?</summary><p>Use checkout after agreeing your scope and activation. Monthly billing starts at checkout and renews until canceled. You pay model providers separately.</p><StripeCheckoutLink offer="cloud" className="button button-outline">Subscribe to Cloud — {CLOUD_PRICE}/month</StripeCheckoutLink><p>To stop renewal, email <a href={`mailto:${CONTACT_EMAIL}?subject=Cancel%20Hormuz%20Cloud`}>{CONTACT_EMAIL}</a> before the next renewal with your subscription reference. Your current paid period remains available.</p></details>
      </article>
      <ManagedSitePlan />
    </div>
    <p className="pricing-cost-note">Prices in USD. You pay model providers directly; provider usage, customer infrastructure, and applicable taxes are separate.</p>
  </section>;
}
