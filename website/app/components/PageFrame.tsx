import { SiteFooter } from './SiteFooter';
import { SiteHeader } from './SiteHeader';
import { CampaignLink } from './CampaignLink';
import { sitePath } from '../../lib/site.mjs';
import { PassageLines } from './Brand';

const sections: Record<string, [string, string][]> = {
  evidence: [['AI Work in action', '#work-demo'], ['Work receipt', '#work-proof'], ['Recorded gateway', '#recording'], ['Qualification', '#qualification']],
  plans: [['Software and Cloud', '#offers'], ['Appliances and sites', '#plans'], ['Reserve yours', '#reserve'], ['Cloud details', '#cloud'], ['How payment works', '#details']],
  enterprise: [['Prices', '#plans'], ['Reserve yours', '#reserve'], ['Compare', '#comparison'], ['Onboarding', '#onboarding'], ['Support allowance', '#support'], ['Activation', '#activate']],
  demo: [['Actual AI Work', '#work-demo'], ['Compaction', '#compaction'], ['Policy', '#policy'], ['Recorded gateway', '#recording']],
  security: [['At a glance', '#status'], ['Data handling', '#data-handling'], ['Controls', '#controls'], ['Open gates', '#gates']],
  integrations: [['My stack', '#my-stack'], ['Client setup', '#clients'], ['AI Work agents', '#ai-work'], ['Protocols', '#protocols'], ['Verify your route', '#verification']],
  resources: [['Buyer materials', '#downloads'], ['Tutorials', '#tutorials'], ['Contribute', '#contribute']],
  brand: [['Concept', '#concept'], ['Identity', '#identity'], ['Color & type', '#system'], ['Downloads', '#assets']],
};

export function PageFrame({ active, children }: { active: string; children: React.ReactNode }) {
  return <div className="inner-page" data-page={active}>
    <SiteHeader active={active} />
    {sections[active] && <nav className="page-section-nav" aria-label="On this page">{sections[active].map(([label, href]) => <a href={href} key={href}>{label}</a>)}</nav>}
    <main id="content" tabIndex={-1}>{children}
      {['demo', 'integrations', 'resources'].includes(active) && <section className="next-conversation">
        <PassageLines />
        <div><p className="section-label">Choose your next step</p><h2>Keep your agents.<br /><em>Choose your priorities.</em></h2><p>{active === 'demo' ? 'Confirm your exact agents, request paths, and spending concern before connecting a workflow.' : <>Connect supported requests to Hormuz, or <CampaignLink href={sitePath('/contact/?interest=work')}>discuss your engineering workflow</CampaignLink>.</>}</p></div>
        <CampaignLink className="button landing-primary" href={sitePath(active === 'demo' ? '/contact/?interest=work' : '/docs/')}>{active === 'demo' ? 'Discuss your workflow' : 'Install free'} <span aria-hidden="true">↗</span></CampaignLink>
      </section>}
    </main><SiteFooter />
  </div>;
}

export function PageHero({ eyebrow, title, children }: { eyebrow: string; title: React.ReactNode; children: React.ReactNode }) {
  return <section className="subpage-hero"><div className="subpage-grid" aria-hidden="true" /><PassageLines /><div className="subpage-hero-inner wide"><p className="eyebrow"><span className="pulse-dot" aria-hidden="true" />{eyebrow}</p><h1>{title}</h1>{children}</div></section>;
}
