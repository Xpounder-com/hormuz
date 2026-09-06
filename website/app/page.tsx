import { EnterprisePlans } from './components/EnterprisePlans';
import { CampaignLink } from './components/CampaignLink';
import { EvidenceShowcase } from './components/EvidenceShowcase';
import { ControlIllustration } from './components/ControlIllustration';
import { GatewayPreview } from './components/GatewayPreview';
import { pageMetadata } from '../lib/metadata';
import { sitePath, REPOSITORY } from '../lib/site.mjs';
import { PILOT_PRICE } from '../lib/commercial.mjs';
import { SiteFooter } from './components/SiteFooter';
import { SiteHeader } from './components/SiteHeader';
import { BrandMark } from './components/Brand';

export const metadata = pageMetadata('Hormuz — Control AI access, budgets, and provider keys', 'Keep Codex and Claude Code. Control model access, enforce budgets, and protect provider keys. Explore a founder-led 90-day paid pilot for your team.', '/');

const benefits = [
  ['01', 'Keep the tools your team loves.', 'Route supported Codex and Claude Code workflows through the gateway. Give people a governed path for the tools they already use.', 'Familiar workflow'],
  ['02', 'Company keys stay with you.', 'Employees authenticate to Hormuz. Your company’s model-provider credentials stay on the controlled server boundary.', 'Bring your own keys'],
  ['03', 'Set the budget before the request.', 'Apply model access, token ceilings, and spend allowances before a request reaches the provider.', 'Pre-provider controls'],
  ['04', 'Stop configured secrets at the door.', 'Redact or deny configured credentials and high-confidence secret formats before forwarding. Deterministic rules, not semantic DLP.', 'Redact or deny'],
  ['05', 'Put policy into practice.', 'Layer organization, team, and person rules. Lower scopes can tighten the rules, never weaken the controls above them.', 'Fail-closed policy'],
  ['06', 'Get evidence, without the conversation.', 'Attribute governed usage, estimated cost, policy, and security outcomes. Keep prompts and responses out of the routine ledger.', 'Content-free evidence'],
];
const faqs = [
  ['What am I paying for if Hormuz is open source?', 'The gateway remains Apache-2.0. The paid pilot adds founder-led workflow mapping, integration guidance, acceptance checks, an evidence review, and handoff for one team and one workflow. Ongoing support is a separate engagement.'],
  ['What does the 90-day pilot include?', 'For $15,000 USD, we agree one team, one workflow, the control boundaries, and acceptance criteria. We work through a non-production integration, review allowed and denied requests, and deliver an evidence pack and go / no-go handoff. Scope and capacity are confirmed before payment.'],
  ['Can I start with a conversation?', 'Yes. Request a free AI governance review to discuss your workflow and what you need to control. It is a fit conversation, with no obligation to purchase a pilot.'],
  ['Is this a hosted AI subscription?', 'The current offer supports a self-hosted gateway. You operate the infrastructure and bring your own provider accounts. Provider usage, infrastructure, and applicable taxes are additional. Managed hosting and 24/7 operations are not included.'],
  ['Does a pilot turn into an ongoing subscription?', 'No. The pilot does not automatically renew. Enterprise support starts at $2,000 USD/month under a separate agreement, with defined hours, response targets, and cancellation terms.'],
  ['Will Hormuz cover all of our AI traffic?', 'Hormuz governs supported requests routed through the gateway. Traffic that bypasses it is outside its coverage. Your environment’s TLS, credential custody, recovery, availability, and security review must be qualified before a production rollout.'],
];

export default function Home() {
  return <div className="landing-home">
    <SiteHeader active="platform" />
    <main id="content" tabIndex={-1}>
      <section className="landing-hero" id="top">
        <div className="landing-hero-copy">
          <p className="landing-eyebrow"><span className="mini-dot" /> AI GOVERNANCE, WITH A HUMAN ON YOUR SIDE</p>
          <h1>Give your team AI.<br /><em>Keep control.</em></h1>
          <p className="landing-deck">Keep Codex and Claude Code. Set the policies, protect your provider keys, and put budgets around the AI your team already uses.</p>
          <div className="landing-actions"><CampaignLink className="button landing-primary" href={sitePath('/contact/?interest=review')}>Get a free governance review <span aria-hidden="true">↗</span></CampaignLink><CampaignLink className="button landing-secondary" href="/#plans">Explore the plans <span aria-hidden="true">↓</span></CampaignLink></div>
          <p className="landing-reassurance">Founder-led · Open-source core · Your infrastructure</p><a className="hero-evidence-link" href="#evidence">Explore the evidence <span aria-hidden="true">↓</span></a>
        </div>
        <GatewayPreview />
        <div className="landing-integrations"><p>Built for your existing AI workflow</p><div><span>⌘ <strong>Codex</strong></span><span>✳ <strong>Claude Code</strong></span><i /><span>OpenAI</span><span>Anthropic</span></div><a href={sitePath('/integrations/')}>Explore supported integrations ↗</a></div>
      </section>

      <section className="passage-story" aria-labelledby="passage-title">
        <figure className="passage-art"><img src={sitePath('/brand/controlled-passage.webp')} width="1536" height="1024" loading="lazy" alt="Layered sage and cream contours around a clear, open channel" /><figcaption><BrandMark /><span>ROOM TO MOVE. CLEAR BOUNDARIES.</span><span aria-hidden="true">↗</span></figcaption></figure>
        <div className="passage-story-copy"><p className="landing-eyebrow">THE HORMUZ APPROACH</p><h2 id="passage-title">Let good work<br /><em>move forward.</em></h2><p>Give supported AI requests a clear path through your organization’s controls. Keep the workflow familiar and make the decisions inspectable.</p><dl className="passage-principles"><div><dt><span>01</span> Your tools</dt><dd>Start with Codex and Claude Code.</dd></div><div><dt><span>02</span> Your boundaries</dt><dd>Apply policy, budgets, and secret controls before egress.</dd></div><div><dt><span>03</span> Your evidence</dt><dd>Review the outcome without routine prompt logging.</dd></div></dl><a href={sitePath('/demo/')}>See how the request path works <span aria-hidden="true">↗</span></a></div>
      </section>

      <section className="landing-section" id="control-plane">
        <div className="landing-heading"><p className="landing-eyebrow">HOW WE WORK TOGETHER</p><h2>A clear path from<br /><em>question to evidence.</em></h2><p>One team. One workflow. A practical decision about what comes next.</p></div>
        <div className="pilot-process">
          <article><div className="process-visual process-map"><span className="visual-label">YOUR STARTING POINT</span><div><span>Client</span><strong>Codex / Claude Code</strong></div><div><span>Owner</span><strong>Your engineering team</strong></div><div><span>Goal</span><strong>Defined together <i>✓</i></strong></div></div><span className="step-label">01 / ALIGN</span><h3>Bring the workflow.</h3><p>Start with a free review. Map the client, identities, policies, and success criteria before committing to the pilot.</p></article>
          <article><div className="process-visual process-prove"><span className="visual-label">IN YOUR TEST ENVIRONMENT</span><div><span className="round-check">✓</span><strong>Approved request</strong><small>Forwarded</small></div><div><span className="round-deny">×</span><strong>Forbidden request</strong><small>Stopped</small></div><span className="visual-caption">Illustrative acceptance checks</span></div><span className="step-label">02 / EVALUATE</span><h3>Put controls to the test.</h3><p>Integrate one non-production path. Check policies, budgets, and evidence with hands-on guidance from the founder.</p></article>
          <article><div className="process-visual process-deliver"><div className="handoff-paper"><span>HORMUZ / PILOT HANDOFF</span><strong>Your next step,<br />backed by evidence.</strong><p>✓ Control map & acceptance results<br />✓ Operating gaps & named owners<br />✓ Go / no-go recommendation</p></div></div><span className="step-label">03 / DECIDE</span><h3>Leave with a decision.</h3><p>Get an evidence pack and operational handoff. Know what works and what needs to happen before a wider rollout.</p></article>
        </div>
        <div className="process-link"><CampaignLink href={sitePath('/enterprise/#pilot')}>See the full 90-day pilot plan <span aria-hidden="true">↗</span></CampaignLink></div>
      </section>

      <EvidenceShowcase />

      <section className="landing-section benefits-section" id="capabilities">
        <div className="landing-heading"><p className="landing-eyebrow">LESS GUESSWORK. MORE CONTROL.</p><h2>The tools stay familiar.<br /><em>The boundaries get clearer.</em></h2></div>
        <div className="landing-benefits">{benefits.map(([number, title, copy, tag], index) => <article key={number}><ControlIllustration index={index} /><div className="benefit-caption"><span>{number}</span><small>{tag}</small></div><h3>{title}</h3><p>{copy}</p></article>)}</div>
        <div className="evidence-banner" id="boundary"><div className="evidence-banner-icon" aria-hidden="true">⌘</div><div><h3>See the control path for yourself.</h3><p>A recorded CLI run with synthetic inputs and inspectable evidence.</p></div><a className="button landing-secondary" href={sitePath('/demo/')}>Watch the real demo <span aria-hidden="true">↗</span></a></div>
      </section>

      <EnterprisePlans />

      <section className="landing-section founder-section" id="review">
        <div className="founder-intro"><div className="founder-monogram" aria-hidden="true">MZ<span>↗</span></div><p className="landing-eyebrow">DIRECT ACCESS TO THE BUILDER</p><h2>Work with the person<br /><em>building Hormuz.</em></h2><p>Bring your engineering questions straight to Mehrdad Zaker. We’ll work through your workflow, agree a useful scope, and make the evaluation concrete.</p><CampaignLink className="button landing-primary" href={sitePath('/contact/?interest=review')}>Let’s talk about your team <span aria-hidden="true">↗</span></CampaignLink></div>
        <div className="founder-note"><span className="note-label">A NOTE ON HOW WE WORK</span><p>Start with one real workflow. Define what good looks like. Then let the evidence guide the next step.</p><div><strong>Mehrdad Zaker</strong><span>Founder & maintainer, Hormuz</span></div><a href={REPOSITORY}>Explore the open-source project ↗</a></div>
      </section>

      <section className="landing-section faq-section" id="faq"><div className="landing-heading"><p className="landing-eyebrow">BEFORE WE GET STARTED</p><h2>Good questions.<br /><em>Straight answers.</em></h2><p>Have a different question? <CampaignLink href={sitePath('/contact/?interest=review')}>Ask Mehrdad ↗</CampaignLink></p></div><div className="landing-faq">{faqs.map(([question, answer]) => <details key={question}><summary>{question}<span aria-hidden="true">+</span></summary><p>{answer}</p></details>)}</div></section>
      <section className="landing-final"><p className="landing-eyebrow">YOUR NEXT AI WORKFLOW, GOVERNED.</p><h2>Move forward.<br /><em>With clear boundaries.</em></h2><p>Start with a free review. Build toward a {PILOT_PRICE} pilot scoped to your team.</p><div className="landing-actions"><CampaignLink className="button landing-primary" href={sitePath('/contact/?interest=review')}>Get a free governance review <span aria-hidden="true">↗</span></CampaignLink><CampaignLink className="button landing-secondary" href="/#plans">See pricing <span aria-hidden="true">↑</span></CampaignLink></div></section>
    </main>
    <SiteFooter />
    <CampaignLink className="founder-float" href={sitePath('/contact/?interest=review')} aria-label="Talk to Mehrdad about a free AI governance review"><span className="float-avatar" aria-hidden="true">MZ</span><span>Talk to the founder<small>Free governance review</small></span><span aria-hidden="true">↗</span></CampaignLink>
  </div>;
}
