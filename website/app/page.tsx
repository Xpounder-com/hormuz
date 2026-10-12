import Image from 'next/image';
import { CampaignLink } from './components/CampaignLink';
import { pageMetadata } from '../lib/metadata';
import { REPOSITORY, REPOSITORY_QUICKSTART, sitePath } from '../lib/site.mjs';
import { SiteFooter } from './components/SiteFooter';
import { SiteHeader } from './components/SiteHeader';

export const metadata = pageMetadata('Hormuz — Free, open-source AI controls', 'Keep your AI tools. Set budgets, apply secret controls, and inspect usage. Try Hormuz free on GitHub; optional hosting and operating help are available by inquiry.', '/');

export default function Home() {
  return <div className="simple-home"><SiteHeader active="platform" /><main id="content" tabIndex={-1}>
    <section className="simple-hero" id="top">
      <div className="simple-hero-copy">
        <p className="section-label">FREE, OPEN-SOURCE AI CONTROLS</p>
        <h1>Keep your agents.<br /><em>Take control of AI work.</em></h1>
        <p className="simple-deck">Set budgets, apply secret controls, and see where usage goes across your supported AI tools. Run Hormuz yourself, inspect the code, and keep your provider accounts.</p>
        <div className="simple-actions"><a className="button landing-primary" href={REPOSITORY_QUICKSTART}>Try free on GitHub <span aria-hidden="true">→</span></a><CampaignLink className="text-link" href={sitePath('/demo/#work-demo')}>See it in action →</CampaignLink></div>
        <p className="simple-caption">Apache-2.0 · No Hormuz subscription · Your provider accounts</p>
        <CampaignLink className="text-link simple-compatibility" id="setup" href={sitePath('/integrations/#clients')}>Check supported tools →</CampaignLink>
      </div>
      <figure className="simple-preview">
        <CampaignLink href={sitePath('/demo/#work-demo')} aria-label="Watch the recorded Hormuz AI Work demonstration">
          <Image src={sitePath('/demo/ai-work-desktop.png')} alt="Hormuz AI Work gateway showing connection status, captured jobs and usage in a recorded local example" width={1440} height={1000} priority />
        </CampaignLink>
        <figcaption>Actual gateway UI with a fictional team and simulated providers. <CampaignLink href={sitePath('/demo/#work-demo')}>Watch the recorded walkthrough →</CampaignLink></figcaption>
      </figure>
    </section>
    <section className="simple-section" id="benefits" aria-labelledby="benefits-title">
      <p className="section-label">WHAT YOU CAN DO FOR FREE</p>
      <h2 id="benefits-title">Use your tools with clearer boundaries.</h2>
      <div className="simple-paths">
        <article id="spend"><p className="section-label">BUDGETS</p><h3>Set limits before work runs.</h3><p>Apply scoped budgets and output caps. See when a request is stopped instead of leaving every request unbounded.</p><CampaignLink className="text-link" href={sitePath('/guides/software-agency-ai-budgets/')}>Try a budget walkthrough →</CampaignLink></article>
        <article id="policy"><p className="section-label">SECRET CONTROLS</p><h3>Check what leaves your gateway.</h3><p>Redact or block configured secrets before provider requests. Keep credentials on the gateway and inspect the control rules.</p><CampaignLink className="text-link" href={sitePath('/security/')}>Read the security boundaries →</CampaignLink></article>
        <article id="compaction"><p className="section-label">USAGE RECORDS</p><h3>See where usage goes.</h3><p>Inspect request attempts, model choices, tokens and configured cost estimates. Usage records omit prompt and response content.</p><CampaignLink className="text-link" href={sitePath('/evidence/#work-proof')}>Inspect example evidence →</CampaignLink></article>
      </div>
    </section>
    <section className="simple-section simple-free" id="free" aria-labelledby="free-title">
      <div><p className="section-label">START WITH THE OPEN-SOURCE PRODUCT</p><h2 id="free-title">Try it locally. Then use it on your work.</h2><p>The GitHub quickstart runs the real gateway with simulated provider replies. No API key, account or payment is required for the tour. Then follow the examples to connect a supported agent or SDK with your own provider account.</p><p className="simple-caption">The software is free to self-host. Your infrastructure and any live model-provider usage are separate.</p></div>
      <div className="simple-free-actions"><a className="button landing-primary" href={REPOSITORY_QUICKSTART}>Try free on GitHub <span aria-hidden="true">→</span></a><CampaignLink className="text-link" href={sitePath('/docs/')}>Installation and supported platforms →</CampaignLink><a className="text-link" href={REPOSITORY + '/discussions'}>Ask a question or share your workflow ↗</a></div>
    </section>
    <section className="simple-section simple-help" id="hosting" aria-labelledby="hosting-title">
      <div><p className="section-label">OPTIONAL HOSTING AND OPERATING HELP</p><h2 id="hosting-title">Prefer someone else to run it?</h2><p>Keep using the free software, or ask about Cloud hosting and managed support. Cloud is planned; we confirm availability, compatibility and scope before payment.</p></div>
      <CampaignLink className="text-link" href={sitePath('/plans/#cloud')}>Explore hosting and support →</CampaignLink>
    </section>
    <span id="growth" className="anchor-target" /><span id="proof" className="anchor-target" /><span id="install" className="anchor-target" />
  </main><SiteFooter /></div>;
}
