import { sitePath } from '../../lib/site.mjs';
import { PILOT_PRICE, SUPPORT_PRICE } from '../../lib/commercial.mjs';
import { CampaignLink } from './CampaignLink';
import { PriceAmount } from './PriceCard';

export function EnterprisePlans() {
  return <section className="section offer-section" id="plans" aria-labelledby="plans-title">
    <div className="section-heading">
      <p className="section-label">THE PRICE. THE SCOPE. THE NEXT STEP.</p>
      <h2 id="plans-title">Install free.<br />Choose the help you need.</h2>
      <p className="offer-intro">The controls stay open source. Add ongoing support or a guided pilot for integration, evaluation, and operating help.</p>
    </div>
    <div className="offer-grid conversion-offers">
      <article className="offer-card">
        <p className="section-label">01 / INSTALL AND EXPLORE</p><h3>Open source</h3>
        <PriceAmount amount="$0" period="Software license · Apache-2.0" />
        <p>Use the product with your own gateway and provider accounts. Start with a local demo or join your team on Mac.</p>
        <ul><li>Gateway, policy, model access, and budgets</li><li>Token, estimated-cost, and outcome reports</li><li>Mac companion and local context optimization</li><li>Documentation and community support</li></ul>
        <CampaignLink className="button button-primary" href={sitePath('/docs/')}>Install free <span aria-hidden="true">↗</span></CampaignLink>
        <p className="field-hint">No card required. You operate your infrastructure and pay your providers.</p>
      </article>
      <article className="offer-card offer-featured">
        <p className="section-label">02 / PUT IT TO THE TEST</p>
        <h3>90-day pilot</h3>
        <PriceAmount amount={PILOT_PRICE} period="USD · one-time fee for 90 days" />
        <p>Evaluate one workflow with founder-led guidance and evidence for your rollout decision.</p>
        <ul><li>Policy map and agreed acceptance criteria</li><li>Non-production integration guidance</li><li>Allowed and blocked request checks</li><li>Evidence pack, go / no-go review, and handoff</li></ul>
        <CampaignLink className="button button-primary" href={sitePath('/contact/?interest=pilot')}>Discuss my pilot <span aria-hidden="true">↗</span></CampaignLink>
        <p className="field-hint">One team, one workflow. Scope, availability, and terms agreed before payment. No automatic renewal.</p>
        <CampaignLink className="text-link" href={sitePath('/enterprise/#pilot')}>See the 90-day plan →</CampaignLink>
      </article>
      <article className="offer-card">
        <p className="section-label">03 / KEEP MOVING</p>
        <h3>Self-service support</h3>
        <PriceAmount amount={SUPPORT_PRICE} period="USD / month · cancel before renewal" />
        <p>Buy ongoing help for one self-hosted gateway. The product stays free; this plan adds direct founder support.</p>
        <ul><li>One self-hosted gateway</li><li>Up to 4 support hours per billing month</li><li>Response within 2 business days</li><li>Policy, upgrade, and troubleshooting help</li></ul>
        <CampaignLink className="button button-outline" href={sitePath('/enterprise/#support')}>See terms & start support <span aria-hidden="true">↗</span></CampaignLink>
        <p className="field-hint">Monthly renewal. Cancel before the next renewal. Provider usage, hosting, and 24/7 operations are separate.</p>
      </article>
    </div>
    <div className="open-source-option"><div><span className="section-label">WANT TO TALK IT THROUGH?</span><h3>Bring one workflow and one question.</h3><p>A free fit discussion with Mehrdad Zaker. No obligation to buy a pilot.</p></div><CampaignLink className="button button-outline" href={sitePath('/contact/?interest=review')}>Ask the founder <span aria-hidden="true">↗</span></CampaignLink></div>
    <p className="pricing-cost-note">All paid prices are in USD. Provider usage, infrastructure, and applicable taxes are additional.</p>
  </section>;
}
