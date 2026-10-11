import { CampaignLink } from './CampaignLink';
import { sitePath } from '../../lib/site.mjs';

export function TeamServiceNextStep() {
  return <section id="team-next" className="docs-section team-service-next" aria-labelledby="team-next-title">
    <p className="section-label">YOUR NEXT STEP</p>
    <h2 id="team-next-title">Choose who operates it.</h2>
    <p>Run the free software on your infrastructure, ask about planned Cloud hosting, or choose managed-site operating help.</p>
    <div className="resource-actions"><CampaignLink className="button button-primary" href={sitePath('/plans/#offers')}>Compare plans →</CampaignLink><CampaignLink className="text-link" href={sitePath('/contact/?interest=review')}>Discuss your setup →</CampaignLink></div>
    <p className="field-hint">Provider usage is separate. Confirm scope and activation before subscribing; monthly billing starts at checkout.</p>
  </section>;
}
