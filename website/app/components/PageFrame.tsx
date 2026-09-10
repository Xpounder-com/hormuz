import { SiteFooter } from './SiteFooter';
import { SiteHeader } from './SiteHeader';
import { CampaignLink } from './CampaignLink';
import { sitePath } from '../../lib/site.mjs';
import { commercial } from '../../lib/commercial.mjs';
import { PassageLines } from './Brand';

const sections: Record<string, [string, string][]> = {
  enterprise: [['Pricing', '#plans'], ['Support', '#support'], ['Compare', '#comparison'], ['90-day process', '#pilot'], ['Agreed payments', '#payment']],
  demo: [['The $1M example', '#experience'], ['Questions', '#faq'], ['Technical evidence', '#recording']],
  security: [['At a glance', '#status'], ['Data handling', '#data-handling'], ['Controls', '#controls'], ['Open gates', '#gates']],
  integrations: [['My stack', '#my-stack'], ['Client setup', '#clients'], ['Protocols', '#protocols'], ['Verify your route', '#verification']],
  resources: [['Buyer materials', '#downloads'], ['Tutorials', '#tutorials'], ['Contribute', '#contribute']],
  brand: [['Concept', '#concept'], ['Identity', '#identity'], ['Color & type', '#system'], ['Downloads', '#assets']],
};

export function PageFrame({ active, children }: { active: string; children: React.ReactNode }) {
  return <div className="inner-page" data-page={active}>
    <SiteHeader active={active} />
    {sections[active] && <nav className="page-section-nav" aria-label="On this page">{sections[active].filter(([, href]) => href !== '#payment' || commercial.pilotPaymentUrl || commercial.supportPaymentUrl).map(([label, href]) => <a href={href} key={href}>{label}</a>)}</nav>}
    <main id="content" tabIndex={-1}>{children}
      {['demo', 'integrations', 'resources'].includes(active) && <section className="next-conversation">
        <PassageLines />
        <div><p className="section-label">Make it work for your team</p><h2>Start free.<br /><em>Get help when you need it.</em></h2><p>Install the open-source product or <CampaignLink href={sitePath('/enterprise/')}>choose a paid engagement</CampaignLink>.</p></div>
        <CampaignLink className="button landing-primary" href={sitePath('/docs/')}>Install free <span aria-hidden="true">↗</span></CampaignLink>
      </section>}
    </main><SiteFooter />
  </div>;
}

export function PageHero({ eyebrow, title, children }: { eyebrow: string; title: React.ReactNode; children: React.ReactNode }) {
  return <section className="subpage-hero"><div className="subpage-grid" aria-hidden="true" /><PassageLines /><div className="subpage-hero-inner wide"><p className="eyebrow"><span className="pulse-dot" aria-hidden="true" />{eyebrow}</p><h1>{title}</h1>{children}</div></section>;
}
