import { CampaignLink } from './CampaignLink';
import { SOFTWARE_PRICE, CLOUD_PRICE, MANAGED_SITE_PRICE } from '../../lib/commercial.mjs';
import { sitePath } from '../../lib/site.mjs';

export function TeamServiceNextStep() {
  return <section id="team-next" className="docs-section team-service-next" aria-labelledby="team-next-title">
    <p className="section-label">YOUR TEAM’S NEXT STEP</p>
    <h2 id="team-next-title">Keep the software free.<br />Choose who operates it.</h2>
    <p>The software is free. Choose self-hosting, Cloud hosting, or managed-site support according to how your team wants to run its gateway.</p>
    <dl className="team-service-options">
      <div><dt>Free software · {SOFTWARE_PRICE}</dt><dd>Your team hosts, updates, and operates the gateway on its own infrastructure. Documentation and community support are included.</dd></div>
      <div><dt>Cloud · {CLOUD_PRICE}</dt><dd>USD per workspace per month. Want hosting? Ask about the planned Hormuz-hosted gateway and dashboard. Cloud is accepting inquiries; your team runs its applications and provider accounts.</dd></div>
      <div><dt>Managed site · {MANAGED_SITE_PRICE}</dt><dd>USD per site per month. Want operating help? Get defined monitoring, updates, recovery guidance, and the stated remote support allowance for one agreed deployment. Cloud is included; hardware is separate.</dd></div>
    </dl>
    <div className="resource-actions"><CampaignLink className="button button-primary" href={sitePath('/plans/#offers')}>Compare Cloud and managed sites →</CampaignLink><CampaignLink className="text-link" href={sitePath('/contact/?interest=review')}>Discuss your team’s setup →</CampaignLink></div>
    <p className="field-hint">You pay model providers separately. We confirm compatibility, service coverage, and activation with you before you subscribe. Monthly billing starts at checkout. Sending an inquiry takes no payment.</p>
  </section>;
}
