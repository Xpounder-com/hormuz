import { sitePath } from '../../lib/site.mjs';
import { PILOT_PRICE, SUPPORT_PRICE } from '../../lib/commercial.mjs';
import { CampaignLink } from './CampaignLink';

export function EnterprisePlans() {
  return (
    <section className="section offer-section" id="plans" aria-labelledby="plans-title">
        <div className="section-heading">
          <p className="section-label">A clear way to start</p>
          <h2 id="plans-title">Put AI controls to work<br />for your team.</h2>
          <p className="offer-intro">For engineering and platform leads adopting Codex or Claude Code under company provider accounts.</p>
        </div>
        <div className="offer-grid">
          <article className="offer-card offer-featured">
            <p className="section-label">Founder-led evaluation</p>
            <h3>90-day paid pilot</h3>
            <p className="offer-price">{PILOT_PRICE} <span>for 90 days · USD</span></p>
            <p>Evaluate one workflow with direct help from the founder. Get a defined scope, a working control path, and evidence to make your rollout decision.</p>
            <ul>
              <li>Workflow and policy mapping with agreed acceptance criteria</li>
              <li>Integration guidance in your non-production environment</li>
              <li>Allowed and blocked request checks, with content-free evidence</li>
              <li>Go / no-go review and an operational handoff</li>
            </ul>
            <CampaignLink className="button button-primary" href={sitePath('/contact/?interest=pilot')}>Apply for a paid pilot <span aria-hidden="true">→</span></CampaignLink>
            <p className="field-hint">Request a scope discussion. For one team and one workflow. Scope, availability, and terms are agreed before payment or work begins.</p>
            <CampaignLink className="text-link" href={sitePath('/enterprise/#pilot')}>See the 90-day plan →</CampaignLink>
          </article>
          <article className="offer-card">
            <p className="section-label">After your evaluation</p>
            <h3>Enterprise support</h3>
            <p className="offer-price">From {SUPPORT_PRICE}<span> / month · USD</span></p>
            <p>Keep a direct line to the founder as your team operates Hormuz.</p>
            <ul><li>Policy and configuration guidance</li><li>Upgrade planning and troubleshooting assistance</li><li>Review of operating questions and evidence</li><li>Defined support hours and response targets in your agreement</li></ul>
            <CampaignLink className="button button-outline" href={sitePath('/contact/?interest=support')}>Discuss enterprise support →</CampaignLink>
            <p className="field-hint">A separate recurring agreement. The pilot does not automatically renew into a subscription. Hosting, 24/7 operations, and unlimited support are not included.</p>
          </article>
          <article className="offer-card">
            <p className="section-label">Build it yourself</p>
            <h3>Open source</h3>
            <p className="offer-price">$0 <span>software license</span></p>
            <p>Run the same gateway yourself. Your team owns deployment, configuration, and operations.</p>
            <ul>
              <li>Apache-2.0 gateway and policy controls</li>
              <li>Bring your own provider accounts and keys</li>
              <li>Documentation and a provider-free demo</li>
              <li>Community support on a best-effort basis</li>
            </ul>
            <CampaignLink className="button button-outline" href={sitePath('/docs/#quickstart')}>Start with open source <span aria-hidden="true">→</span></CampaignLink>
            <p className="field-hint">Provider usage and infrastructure are paid separately.</p>
          </article>
        </div>
        <p className="field-hint">Provider usage, infrastructure, and applicable taxes are additional.</p><div className="offer-next"><CampaignLink className="text-link" href={sitePath('/contact/?interest=review')}>Not sure where to start? Request a free AI governance review →</CampaignLink><p><strong>What happens after you apply?</strong></p><p>Share your workflow → Discuss fit and scope → Agree a proposal → Begin the evaluation.</p></div>
      </section>
  );
}
