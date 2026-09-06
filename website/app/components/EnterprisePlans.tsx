import { sitePath } from '../../lib/site.mjs';
import { PILOT_PRICE, SUPPORT_PRICE } from '../../lib/commercial.mjs';
import { CampaignLink } from './CampaignLink';
import { PriceAmount } from './PriceCard';

export function EnterprisePlans() {
  return <section className="section offer-section" id="plans" aria-labelledby="plans-title">
    <div className="section-heading">
      <p className="section-label">THE PRICE. THE SCOPE. THE NEXT STEP.</p>
      <h2 id="plans-title">Start with a conversation.<br />Pay for a defined engagement.</h2>
      <p className="offer-intro">The software is open source. Paid engagements add direct help from the person building it.</p>
    </div>
    <div className="offer-grid conversion-offers">
      <article className="offer-card">
        <p className="section-label">01 / EXPLORE THE FIT</p>
        <h3>AI governance review</h3>
        <PriceAmount amount="$0" period="Free conversation · no obligation" />
        <p>Bring the AI workflow you want to govern. Work out whether Hormuz is a useful fit before committing.</p>
        <ul><li>Discuss your client, team, and control needs</li><li>Identify the boundaries to evaluate</li><li>Choose a practical next step together</li></ul>
        <CampaignLink className="button button-outline" href={sitePath('/contact/?interest=review')}>Get a free AI review <span aria-hidden="true">↗</span></CampaignLink>
        <p className="field-hint">A fit discussion with Mehrdad. No card required.</p>
      </article>
      <article className="offer-card offer-featured">
        <p className="section-label">02 / PUT IT TO THE TEST</p>
        <h3>90-day pilot</h3>
        <PriceAmount amount={PILOT_PRICE} period="USD · one-time fee for 90 days" />
        <p>Evaluate one workflow with founder-led guidance and evidence for your rollout decision.</p>
        <ul><li>Policy map and agreed acceptance criteria</li><li>Non-production integration guidance</li><li>Allowed and blocked request checks</li><li>Evidence pack, go / no-go review, and handoff</li></ul>
        <CampaignLink className="button button-primary" href={sitePath('/contact/?interest=pilot')}>Discuss my pilot <span aria-hidden="true">↗</span></CampaignLink>
        <p className="field-hint">One team, one workflow. Scope, availability, and terms agreed before payment. No automatic renewal.</p>
        <a className="text-link" href={sitePath('/enterprise/#pilot')}>See the 90-day plan →</a>
      </article>
      <article className="offer-card">
        <p className="section-label">03 / KEEP MOVING</p>
        <h3>Enterprise support</h3>
        <PriceAmount amount={SUPPORT_PRICE} period="USD / month · separate agreement" from />
        <p>Keep a direct line to the founder as your team operates Hormuz.</p>
        <ul><li>Policy and configuration guidance</li><li>Upgrade and troubleshooting assistance</li><li>Operating questions and evidence review</li><li>Agreed hours and response targets</li></ul>
        <CampaignLink className="button button-outline" href={sitePath('/contact/?interest=support')}>Discuss my support needs <span aria-hidden="true">↗</span></CampaignLink>
        <p className="field-hint">Optional ongoing support. Hosting, 24/7 operations, and unlimited support are not included.</p>
      </article>
    </div>
    <div className="open-source-option"><div><span className="section-label">PREFER TO BUILD IT YOURSELF?</span><h3>Open source. $0 software license.</h3><p>Apache-2.0. Your provider accounts, infrastructure, and operations. Community support on a best-effort basis.</p></div><CampaignLink className="button button-outline" href={sitePath('/docs/#quickstart')}>Start with open source <span aria-hidden="true">↗</span></CampaignLink></div>
    <p className="pricing-cost-note">All paid prices are in USD. Provider usage, infrastructure, and applicable taxes are additional.</p>
  </section>;
}
