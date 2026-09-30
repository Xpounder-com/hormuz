import { OfferingPaths } from './components/OfferingPaths';
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

export const metadata = {
  ...pageMetadata('Hormuz | Personal AI Optimizer & Team Gateway', 'Start with the free Personal Optimizer on your Mac, or self-host an open-source gateway for team policy and AI usage.', '/'),
  verification: { google: 'qauQXsmCOtWfE0ar7muWwlqPmMfW5F7uaI28QGz30v4' },
};

export default function Home() {
  return <div className="landing-home"><SiteHeader active="platform" /><main id="content" tabIndex={-1}>
    <section className="landing-hero customer-hero" id="top">
      <div className="customer-hero-grid"><div className="landing-hero-copy">
        <p className="landing-eyebrow"><span className="mini-dot" /> PERSONAL OPTIMIZER + TEAM GATEWAY</p>
        <h1>Start with your own work.<br /><em>See what Hormuz improves.</em></h1>
        <p className="landing-deck">Use the free Personal Optimizer on a supported Mac with your own provider account and measure qualified local context reductions. When your team needs governance, self-host the open-source gateway to set policies and inspect captured usage.</p>
        <div className="landing-actions"><CampaignLink className="button landing-primary" href={sitePath('/docs/#personal')}>Set up personal use <span aria-hidden="true">↗</span></CampaignLink><a className="button landing-secondary" href="#spend">Explore the team example <span aria-hidden="true">↓</span></a></div>
        <p className="landing-reassurance">No Hormuz software fee · Provider charges separate · No personal account required</p>
      </div><SpendHookPreview /></div>
      <p className="hero-owner">A product of <a href={OWNER_URL}>{OWNER_NAME} ↗</a> · Led by Mehrdad Zaker</p>
    </section>
    <OfferingPaths />
    <CustomerExperience />
    <section className="passage-story" aria-labelledby="passage-title"><figure className="passage-art"><img src={sitePath('/brand/controlled-passage.webp')} width="1536" height="1024" loading="lazy" alt="Layered sage and cream contours around a clear, open channel" /><figcaption><BrandMark /><span>YOUR TOOLS. YOUR BOUNDARIES. YOUR EVIDENCE.</span><span aria-hidden="true">↗</span></figcaption></figure><div className="passage-story-copy"><p className="landing-eyebrow">FROM INSIGHT TO ACTION</p><h2 id="passage-title">A budget is useful<br /><em>when it can act.</em></h2><p>A manager can explain the spend. A policy administrator can set the boundary. An employee can keep using their coding tool through the governed connection.</p><dl className="passage-principles"><div><dt><span>01</span> See the driver</dt><dd>Break down captured usage by team, person, model, client, and provider.</dd></div><div><dt><span>02</span> Change a control</dt><dd>Set model access, output caps, scoped budgets, and secret handling.</dd></div><div><dt><span>03</span> Inspect the result</dt><dd>Trace request outcomes and estimates without routine prompt or response logging.</dd></div></dl><CampaignLink href={sitePath('/demo/#policy')}>Try setting up a policy <span aria-hidden="true">↗</span></CampaignLink></div></section>
    <CompanionPreview />
    <ContextOptimizationPreview />
    <section className="landing-section" id="install"><div className="landing-heading"><p className="landing-eyebrow">FOR TEAMS · YOUR FIRST GOVERNED REQUEST</p><h2>Start with your role.<br /><em>Then connect your tools.</em></h2><p>The team Mac app connects to a configured gateway. The gateway applies your organization’s policies. For direct use on your own Mac, follow the <CampaignLink href={sitePath('/docs/#personal')}>Personal Optimizer setup</CampaignLink>.</p></div><div className="install-paths"><article><p className="landing-eyebrow">JOIN AN EXISTING TEAM</p><h3>Get the Mac companion.</h3><p>Install Hormuz 1.3.0 on Apple Silicon, macOS 14+. Use your team’s gateway address and identity, review the client launcher, then see your own usage.</p><CampaignLink className="button landing-primary" href={sitePath('/docs/#mac')}>Install free for Mac ↗</CampaignLink></article><article><p className="landing-eyebrow">SET UP YOUR ORGANIZATION</p><h3>Run your own gateway.</h3><p>Install from source or a signed OCI image. Start with the provider-free demo, configure identities and provider accounts, and verify your first governed client request.</p><CampaignLink className="button landing-secondary" href={sitePath('/docs/#quickstart')}>Install the gateway →</CampaignLink></article></div><p className="after-grid">Using Ollama or another endpoint? <CampaignLink href={sitePath('/demo/#setup')}>Check your exact stack and its current compatibility status →</CampaignLink></p>
    <p className="after-grid">Practical guides: <CampaignLink href={sitePath('/guides/team-ai-budgets/')}>Manage AI costs and team budgets</CampaignLink> · <CampaignLink href={sitePath('/guides/codex-claude-code-gateway/')}>Connect Codex and Claude Code</CampaignLink></p></section>
    <QuestionIndex compact />
    <section className="landing-final"><p className="landing-eyebrow">TRY IT WITH YOUR WORKFLOW</p><h2>Your tools.<br /><em>Your control.</em></h2><p>Start with the free Personal Optimizer or set up the open-source gateway for your team. Paid team services have a separate scope.</p><div className="landing-actions"><CampaignLink className="button landing-primary" href={sitePath('/docs/#personal')}>Set up personal use ↗</CampaignLink><CampaignLink className="button landing-secondary" href={sitePath('/plans/')}>See plans →</CampaignLink></div></section>
  </main><SiteFooter /></div>;
}
