import { PILOT_PRICE } from '../../lib/commercial.mjs';
import { sitePath } from '../../lib/site.mjs';
import { CampaignLink } from './CampaignLink';

export function PriceAmount({ amount, period, from = false }: { amount: string; period: string; from?: boolean }) {
  return <div className="price-amount">{from && <span className="price-prefix">From</span>}<strong>{amount}</strong><span className="price-period">{period}</span></div>;
}

export function PilotPriceCard() {
  return <aside className="hero-price-card" aria-labelledby="hero-pilot-title">
    <div className="price-card-topline"><span className="section-label">WORK WITH THE FOUNDER</span><span className="price-duration">90 days</span></div>
    <h2 id="hero-pilot-title">Your workflow.<br />A working control path.</h2>
    <PriceAmount amount={PILOT_PRICE} period="USD · one-time pilot fee" />
    <p>One team. One workflow. Hands-on guidance from integration to a go / no-go decision.</p>
    <ul className="price-deliverables"><li>Policy map & agreed acceptance criteria</li><li>Non-production integration guidance</li><li>Evidence pack & operational handoff</li></ul>
    <CampaignLink className="button button-primary" href={sitePath('/contact/?interest=pilot')}>Discuss my pilot <span aria-hidden="true">↗</span></CampaignLink>
    <p className="price-card-note">Agree scope before paying. No automatic renewal.<br />Provider usage, infrastructure, and taxes additional.</p>
    <a className="price-card-details" href={sitePath('/enterprise/#pilot')}>What happens over the 90 days? <span aria-hidden="true">→</span></a>
  </aside>;
}
