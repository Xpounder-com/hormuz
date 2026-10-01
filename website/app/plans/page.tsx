import { OfferingPaths } from '../components/OfferingPaths';
import { CampaignLink } from '../components/CampaignLink';
import { PageFrame, PageHero } from '../components/PageFrame';
import { pageMetadata } from '../../lib/metadata';
import { PRO_PRICE, PRO_INCLUDED_REQUESTS, PRO_OVERAGE_PRICE, PRO_OVERAGE_REQUESTS } from '../../lib/commercial.mjs';
import { sitePath } from '../../lib/site.mjs';

export const metadata = pageMetadata('Personal, Pro & Enterprise plans — Hormuz', 'Personal use is free. Hosted Pro is accepting inquiries at $99 per workspace per month, including 100,000 gateway requests, plus $10 per additional 100,000. Enterprise pricing is custom. Model usage is separate.', '/plans/');

export default function PlansPage() {
  return <PageFrame active="plans">
    <PageHero eyebrow="Personal · Pro · Enterprise" title={<>Personal use starts free.<br /><span>Pro is planned for your production APIs.</span></>}>
      <p>Keep personal optimization local, request a gateway operated by Hormuz for your API workflows, or discuss a custom Enterprise deployment. Connect your own model-provider accounts on every path.</p>
      <div className="hero-actions"><a className="button button-primary" href="#offers">Compare plans ↓</a><CampaignLink className="button button-ghost" href={sitePath('/contact/?interest=pro')}>Request Pro access →</CampaignLink></div>
    </PageHero>
    <OfferingPaths />
    <section className="section prose-section" id="pro-billing">
      <p className="section-label">PRO · HOSTED GATEWAY PRICING</p>
      <h2>A workspace fee.<br />Gateway usage that grows with you.</h2>
      <p><strong>{PRO_PRICE} USD per workspace per month</strong> includes {PRO_INCLUDED_REQUESTS} gateway requests. Additional traffic is priced at <strong>{PRO_OVERAGE_PRICE} per {PRO_OVERAGE_REQUESTS} requests</strong>, prorated by request. Administrative users are included; there is no per-member fee.</p>
      <p>For example, 1,000,000 gateway requests in one billing month would cost <strong>$189 for Hormuz</strong>: $99 base plus $90 for the additional 900,000 requests. Your model-provider bill and applicable taxes are separate.</p>
      <h3>What counts as a request?</h3>
      <p>The planned billing unit is one accepted client request to the model API gateway. Streaming chunks and internal retries do not add billable requests. The handling of failed, canceled, and rejected requests is confirmed in the Pro terms before activation.</p>
      <h3>Traffic and support coverage</h3>
      <p>Monthly usage is separate from capacity. Supported API routes, request rates, concurrent streams, payload limits, and email support coverage are confirmed for your workload. Usage alerts and customer-approved overages are part of the planned service. Higher capacity and custom availability or incident-response requirements follow an <CampaignLink href={sitePath('/contact/?interest=enterprise')}>Enterprise proposal</CampaignLink>.</p>
      <h3>Availability</h3>
      <p><strong>Pro is accepting inquiries.</strong> Hosted onboarding, subscriptions, request metering, and production qualification must be completed before activation. Requesting access does not activate a production gateway or create a subscription. Compatibility, capacity, and commercial terms are agreed before payment.</p>
      <CampaignLink className="button button-primary" href={sitePath('/contact/?interest=pro')}>Request Pro access →</CampaignLink>
    </section>
    <section className="section prose-section" id="details">
      <p className="section-label">HOW PAYMENT WORKS</p>
      <h2>Your provider bill remains yours.</h2>
      <p>Personal Optimizer and the self-hosted Apache-2.0 gateway have no Hormuz software license fee. Pro is a hosted service operated by Hormuz; its base subscription covers shared gateway hosting. You connect your own provider accounts and pay providers directly for model usage.</p>
      <p>Personal use has no paid subscription or included model usage. Local token estimates and byte reductions are not a promise of lower provider charges. Existing <CampaignLink href={sitePath('/enterprise/')}>implementation, support, and pilot services</CampaignLink> have separate terms. Enterprise capacity, deployment, and support are priced by written proposal.</p>
    </section>
  </PageFrame>;
}
