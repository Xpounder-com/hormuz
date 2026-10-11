import { CampaignLink } from './components/CampaignLink';
import { pageMetadata } from '../lib/metadata';
import { sitePath } from '../lib/site.mjs';
import { SiteFooter } from './components/SiteFooter';
import { SiteHeader } from './components/SiteHeader';
import { SOFTWARE_PRICE, CLOUD_PRICE, MANAGED_SITE_PRICE } from '../lib/commercial.mjs';

export const metadata = pageMetadata('Team AI budgets — Hormuz', 'Set spending limits and track usage across your team’s supported AI tools. Start with free software, choose Cloud hosting, or add managed-site support.', '/');

export default function Home() {
  return <div className="simple-home"><SiteHeader active="platform" /><main id="content" tabIndex={-1}>
    <section className="simple-hero" id="top">
      <p className="section-label">AI BUDGETS FOR YOUR TEAM</p>
      <h1>Keep your agents.<br /><em>Set your AI budget.</em></h1>
      <p className="simple-deck">Set spending limits and track usage across your team’s supported AI tools. Start free, then add hosting or operating help.</p>
      <div className="simple-actions"><CampaignLink className="button landing-primary" href={sitePath('/docs/#examples')}>Start free <span aria-hidden="true">→</span></CampaignLink><CampaignLink className="text-link" href={sitePath('/plans/#offers')}>See pricing →</CampaignLink></div>
      <p className="simple-caption">Open source · Your provider accounts</p>
      <ul className="simple-capabilities" id="spend"><li id="policy">Team budgets</li><li>Request controls</li><li>Usage reports</li></ul>
      <span id="compaction" className="anchor-target" /><CampaignLink className="text-link simple-compatibility" id="setup" href={sitePath('/integrations/#clients')}>Check supported tools →</CampaignLink>
    </section>
    <section className="simple-section" id="growth" aria-labelledby="growth-title">
      <h2 id="growth-title">Choose who runs it.</h2>
      <div className="simple-paths">
        <article><p className="section-label">SELF-HOSTED</p><h3>Free software</h3><p className="simple-price"><strong>{SOFTWARE_PRICE}</strong><span>software license</span></p><p>Run Hormuz on your own infrastructure. Documentation and community support included.</p><CampaignLink className="text-link" href={sitePath('/docs/')}>Install free →</CampaignLink></article>
        <article><p className="section-label">HOSTED</p><h3>Cloud</h3><p className="simple-price"><strong>{CLOUD_PRICE}</strong><span>/ workspace / month</span></p><p>A planned hosted gateway and dashboard. Confirm availability and your setup first.</p><CampaignLink className="text-link" href={sitePath('/plans/#cloud')}>Explore Cloud →</CampaignLink></article>
        <article><p className="section-label">MANAGED</p><h3>Managed site</h3><p className="simple-price"><strong>{MANAGED_SITE_PRICE}</strong><span>/ site / month</span></p><p>Cloud included, with monitoring and up to 1 hour of remote assistance each month. Hardware sold separately.</p><CampaignLink className="text-link" href={sitePath('/plans/#managed')}>Explore managed sites →</CampaignLink></article>
      </div>
      <p className="simple-caption">Prices in USD. Provider usage and applicable taxes are separate.</p>
    </section>
    <section className="simple-section simple-guide" id="proof">
      <div><p className="section-label">FOR SOFTWARE AGENCIES</p><h2>Try team budgets locally.</h2><p>Follow a runnable walkthrough for setting budgets and inspecting usage before connecting your provider account.</p></div>
      <CampaignLink className="text-link" href={sitePath('/guides/software-agency-ai-budgets/')}>Read the walkthrough →</CampaignLink>
    </section>
    <section className="simple-section simple-help" id="install">
      <div><h2>Want help running Hormuz?</h2><p>Tell us about your team and applications. We’ll confirm scope and activation before payment.</p></div>
      <CampaignLink className="button landing-primary" href={sitePath('/contact/?interest=work')}>Discuss your setup <span aria-hidden="true">→</span></CampaignLink>
    </section>
  </main><SiteFooter /></div>;
}
