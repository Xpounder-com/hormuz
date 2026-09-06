import { SiteFooter } from './SiteFooter';
import { SiteHeader } from './SiteHeader';
import { CampaignLink } from './CampaignLink';
import { sitePath } from '../../lib/site.mjs';
import { commercial } from '../../lib/commercial.mjs';

const sections: Record<string, [string, string][]> = {
  enterprise: [['Plans', '#plans'], ['Compare', '#comparison'], ['90-day process', '#pilot'], ['Agreed payments', '#payment']],
  demo: [['Gateway recording', '#recording'], ['Policy walkthrough', '#policy-recording'], ['Evidence files', '#evidence']],
  security: [['At a glance', '#status'], ['Data handling', '#data-handling'], ['Controls', '#controls'], ['Open gates', '#gates']],
  integrations: [['Client setup', '#clients'], ['Protocols', '#protocols'], ['Verify your route', '#verification']],
  resources: [['Buyer materials', '#downloads'], ['Tutorials', '#tutorials'], ['Contribute', '#contribute']],
};

export function PageFrame({ active, children }: { active: string; children: React.ReactNode }) {
  return <div className="inner-page" data-page={active}>
    <SiteHeader active={active} />
    {sections[active] && <nav className="page-section-nav" aria-label="On this page">{sections[active].filter(([, href]) => href !== '#payment' || commercial.pilotPaymentUrl || commercial.supportPaymentUrl).map(([label, href]) => <a href={href} key={href}>{label}</a>)}</nav>}
    <main id="content" tabIndex={-1}>{children}
      {['demo', 'integrations', 'resources'].includes(active) && <section className="next-conversation">
        <div><p className="section-label">Make it relevant to your team</p><h2>Bring your workflow.<br /><em>We’ll work through it together.</em></h2><p>A free governance review with Mehrdad Zaker, the person building Hormuz.</p></div>
        <CampaignLink className="button landing-primary" href={sitePath('/contact/?interest=review')}>Discuss your workflow <span aria-hidden="true">↗</span></CampaignLink>
      </section>}
    </main><SiteFooter />
  </div>;
}

export function PageHero({ eyebrow, title, children }: { eyebrow: string; title: React.ReactNode; children: React.ReactNode }) {
  return <section className="subpage-hero"><div className="subpage-grid" aria-hidden="true" /><div className="subpage-hero-inner wide"><p className="eyebrow"><span className="pulse-dot" aria-hidden="true" />{eyebrow}</p><h1>{title}</h1>{children}</div></section>;
}
