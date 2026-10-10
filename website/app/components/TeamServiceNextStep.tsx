import { CampaignLink } from './CampaignLink';
import { SOFTWARE_PRICE, CLOUD_PRICE, MANAGED_SITE_PRICE } from '../../lib/commercial.mjs';
import { sitePath } from '../../lib/site.mjs';

export function TeamServiceNextStep() {
  return <section id="team-next" className="docs-section team-service-next" aria-labelledby="team-next-title">
    <p className="section-label">YOUR TEAM’S NEXT STEP</p>
    <h2 id="team-next-title">Keep the software free.<br />Choose who operates it.</h2>
    <p>Start with one supported workflow and verify its attributed usage and spending controls. Then choose the operating setup your team needs.</p>
    <dl className="team-service-options">
      <div><dt>Free software · {SOFTWARE_PRICE}</dt><dd>Keep running it yourself when your team can host, update, and operate the gateway. Documentation and community support are included.</dd></div>
      <div><dt>Cloud · {CLOUD_PRICE}</dt><dd>Per workspace per month, in USD. Accepting inquiries for a planned hosted gateway and dashboard. Your team operates its applications and provider accounts.</dd></div>
      <div><dt>Managed site · {MANAGED_SITE_PRICE}</dt><dd>Per site per month, in USD. Defined monitoring, updates, recovery guidance, and the stated support allowance for one agreed gateway deployment. Its Cloud workspace is included; hardware is separate.</dd></div>
    </dl>
    <div className="resource-actions"><CampaignLink className="button button-primary" href={sitePath('/plans/#offers')}>Compare Cloud and managed sites →</CampaignLink><CampaignLink className="text-link" href={sitePath('/contact/?interest=review')}>Discuss your team’s setup →</CampaignLink></div>
    <p className="field-hint">Bring your team size, agents and providers, expected traffic, and the person who operates the setup. Provider usage is separate. Confirm paid-service scope and activation before checkout; monthly billing starts at checkout. An inquiry takes no payment.</p>
  </section>;
}
