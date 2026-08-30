import { SiteFooter } from './SiteFooter';
import { SiteHeader } from './SiteHeader';

export function PageFrame({ active, children }: { active: string; children: React.ReactNode }) {
  return <><SiteHeader active={active} /><main id="content" tabIndex={-1}>{children}</main><SiteFooter /></>;
}

export function PageHero({ eyebrow, title, children }: { eyebrow: string; title: React.ReactNode; children: React.ReactNode }) {
  return <section className="subpage-hero"><div className="subpage-grid" aria-hidden="true" /><div className="subpage-hero-inner wide"><p className="eyebrow"><span className="pulse-dot" aria-hidden="true" />{eyebrow}</p><h1>{title}</h1>{children}</div></section>;
}
