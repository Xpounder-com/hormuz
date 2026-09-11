import { EnterprisePlans } from './components/EnterprisePlans';
import { CampaignLink } from './components/CampaignLink';
import { pageMetadata } from '../lib/metadata';
import { sitePath, OWNER_NAME, OWNER_URL } from '../lib/site.mjs';
import { SiteFooter } from './components/SiteFooter';
import { SiteHeader } from './components/SiteHeader';
import { BrandMark } from './components/Brand';
import { CompanionPreview } from './components/CompanionPreview';
import { CustomerExperience } from './components/CustomerExperience';
import { SpendHookPreview } from './components/SpendHookPreview';
import { ContextOptimizationPreview } from './components/ContextOptimizationPreview';
import { QuestionIndex } from './components/QuestionIndex';

export const metadata = pageMetadata('Hormuz — Understand AI spend. Control the next request.', 'Explore a $1M AI budget: see team and token costs, try policy changes, inspect local context compaction, then install Hormuz free or choose a paid engagement.', '/');

export default function Home() {
  return <div className="landing-home"><SiteHeader active="platform" /><main id="content" tabIndex={-1}>
    <section className="landing-hero customer-hero" id="top">
      <div className="customer-hero-grid"><div className="landing-hero-copy">
        <p className="landing-eyebrow"><span className="mini-dot" /> OPEN-SOURCE AI CONTROL</p>
        <h1>Spending $1M a year on AI?<br /><em>See what drives the bill.</em></h1>
        <p className="landing-deck">Follow a team’s spend from the bill to the tokens. Try a budget or output cap, then see how to connect your own tools.</p>
        <div className="landing-actions"><CampaignLink className="button landing-primary" href={sitePath('/docs/')}>Install free <span aria-hidden="true">↗</span></CampaignLink><a className="button landing-secondary" href="#spend">Try the $1M example <span aria-hidden="true">↓</span></a></div>
        <p className="landing-reassurance">Apache-2.0 · No card required · Your provider accounts</p>
      </div><SpendHookPreview /></div>
      <p className="hero-owner">A product of <a href={OWNER_URL}>{OWNER_NAME} ↗</a> · Led by Mehrdad Zaker</p>
    </section>
    <CustomerExperience />
    <section className="passage-story" aria-labelledby="passage-title"><figure className="passage-art"><img src={sitePath('/brand/controlled-passage.webp')} width="1536" height="1024" loading="lazy" alt="Layered sage and cream contours around a clear, open channel" /><figcaption><BrandMark /><span>YOUR TOOLS. YOUR BOUNDARIES. YOUR EVIDENCE.</span><span aria-hidden="true">↗</span></figcaption></figure><div className="passage-story-copy"><p className="landing-eyebrow">FROM INSIGHT TO ACTION</p><h2 id="passage-title">A budget is useful<br /><em>when it can act.</em></h2><p>A manager can explain the spend. A policy administrator can set the boundary. An employee can keep using their coding tool through the governed connection.</p><dl className="passage-principles"><div><dt><span>01</span> See the driver</dt><dd>Break down captured usage by team, person, model, client, and provider.</dd></div><div><dt><span>02</span> Change a control</dt><dd>Set model access, output caps, scoped budgets, and secret handling.</dd></div><div><dt><span>03</span> Inspect the result</dt><dd>Trace request outcomes and estimates without routine prompt or response logging.</dd></div></dl><CampaignLink href={sitePath('/demo/#policy')}>Try setting up a policy <span aria-hidden="true">↗</span></CampaignLink></div></section>
    <CompanionPreview />
    <ContextOptimizationPreview />
    <section className="landing-section" id="install"><div className="landing-heading"><p className="landing-eyebrow">YOUR FIRST GOVERNED REQUEST</p><h2>Start with your role.<br /><em>Then connect your tools.</em></h2><p>The Mac app connects to a gateway. The gateway applies your organization’s policies.</p></div><div className="install-paths"><article><p className="landing-eyebrow">JOIN AN EXISTING TEAM</p><h3>Get the Mac companion.</h3><p>Install Hormuz 1.2.0 on Apple Silicon, macOS 14+. Use your team’s gateway address and identity, review the client launcher, then see your own usage.</p><CampaignLink className="button landing-primary" href={sitePath('/docs/#mac')}>Install free for Mac ↗</CampaignLink></article><article><p className="landing-eyebrow">SET UP YOUR ORGANIZATION</p><h3>Run your own gateway.</h3><p>Install from source or a signed OCI image. Start with the provider-free demo, configure identities and provider accounts, and verify your first governed client request.</p><CampaignLink className="button landing-secondary" href={sitePath('/docs/#quickstart')}>Install the gateway →</CampaignLink></article></div><p className="after-grid">Using Ollama or another endpoint? <CampaignLink href={sitePath('/demo/#setup')}>Check your exact stack and its current compatibility status →</CampaignLink></p></section>
    <EnterprisePlans />
    <QuestionIndex compact />
    <section className="landing-final"><p className="landing-eyebrow">TRY IT WITH YOUR WORKFLOW</p><h2>Your tools.<br /><em>Your control.</em></h2><p>Install the open-source product free. Add a paid engagement when you want help bringing it to your team.</p><div className="landing-actions"><CampaignLink className="button landing-primary" href={sitePath('/docs/')}>Install free ↗</CampaignLink><CampaignLink className="button landing-secondary" href={sitePath('/enterprise/')}>See plans →</CampaignLink></div></section>
  </main><SiteFooter /></div>;
}
