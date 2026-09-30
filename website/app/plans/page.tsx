import { OfferingPaths } from '../components/OfferingPaths';
import { CampaignLink } from '../components/CampaignLink';
import { PageFrame, PageHero } from '../components/PageFrame';
import { pageMetadata } from '../../lib/metadata';
import { sitePath } from '../../lib/site.mjs';

export const metadata = pageMetadata('Plans for personal use and teams — Hormuz', 'Use the Personal Optimizer or self-hosted team gateway free. Provider usage is separate. Fixed team services and custom enterprise pricing have distinct scopes.', '/plans/');

export default function PlansPage() {
  return <PageFrame active="plans">
    <PageHero eyebrow="Hormuz plans" title={<>Personal use starts free.<br /><span>Team help has a clear scope.</span></>}>
      <p>Use the open-source product with your own provider account. Choose fixed-scope team help, or contact sales to discuss custom enterprise requirements and pricing.</p>
      <div className="hero-actions"><a className="button button-primary" href="#offers">Compare the paths ↓</a><CampaignLink className="button button-ghost" href={sitePath('/docs/#personal')}>Personal setup →</CampaignLink></div>
    </PageHero>
    <OfferingPaths />
    <section className="section prose-section" id="details"><p className="section-label">HOW PAYMENT WORKS</p><h2>Your provider bill remains yours.</h2><p>Personal Optimizer and the self-hosted gateway have no Hormuz software license fee. Direct personal use requires a provider API credential; your provider bills any model usage under its terms. Team deployments also pay for their own infrastructure.</p><p>Hormuz does not currently offer a paid personal subscription or include model usage in a plan. Local token estimates and byte reductions are not a promise of lower provider charges. The <CampaignLink href={sitePath('/enterprise/')}>team services page</CampaignLink> lists the fixed support and pilot terms. Enterprise pricing follows a requirements discussion and written proposal.</p></section>
  </PageFrame>;
}
