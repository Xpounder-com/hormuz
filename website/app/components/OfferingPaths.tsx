import { CampaignLink } from './CampaignLink';
import { PriceAmount } from './PriceCard';
import { sitePath } from '../../lib/site.mjs';
import { PRO_PRICE, PRO_INCLUDED_REQUESTS, PRO_OVERAGE_PRICE, PRO_OVERAGE_REQUESTS } from '../../lib/commercial.mjs';

export function OfferingPaths() {
  return <section className="section offer-section" id="offers" aria-labelledby="offers-title">
    <div className="section-heading">
      <p className="section-label">CHOOSE HOW YOU USE HORMUZ</p>
      <h2 id="offers-title">Personal starts free.<br />Pro brings hosting to your API workflows.</h2>
      <p className="offer-intro">Use the Personal Optimizer locally, request access to hosted Pro, or discuss custom Enterprise requirements. Model-provider usage is billed separately on every path.</p>
    </div>
    <div className="offer-grid conversion-offers">
      <article className="offer-card offer-featured">
        <p className="section-label">FOR ONE DEVELOPER</p>
        <h3>Personal Optimizer</h3>
        <PriceAmount amount="$0" period="Hormuz software · no card or subscription" />
        <p>Keep using a supported coding agent. Hormuz applies a qualified local optimization when it helps and shows you the measured result.</p>
        <ul><li>Runs locally on a supported Apple Silicon Mac</li><li>Connects directly with your own provider API credential</li><li>No organization or Hormuz gateway required</li><li>Content-free benefit view and an Off switch</li></ul>
        <CampaignLink className="button button-primary" href={sitePath('/docs/#personal')}>Set up personal use <span aria-hidden="true">↗</span></CampaignLink>
        <p className="field-hint">Provider API charges are separate. Supported agent and optimization scope are listed in the setup guide.</p>
      </article>
      <article className="offer-card">
        <p className="section-label">FOR PRODUCTION API WORKFLOWS · EARLY ACCESS</p>
        <h3>Pro</h3>
        <PriceAmount amount={PRO_PRICE} period="USD / workspace / month + gateway usage" />
        <p>A planned Hormuz-hosted gateway for your applications’ model API traffic. Connect your own provider accounts; Hormuz operates the gateway.</p>
        <ul><li>{PRO_INCLUDED_REQUESTS} gateway requests included each month</li><li>{PRO_OVERAGE_PRICE} per additional {PRO_OVERAGE_REQUESTS} requests</li><li>Administrative users included</li><li>Team controls, usage reporting, and email support</li></ul>
        <CampaignLink className="button button-outline" href={sitePath('/contact/?interest=pro')}>Request Pro access <span aria-hidden="true">↗</span></CampaignLink>
        <p className="field-hint">Accepting inquiries. API compatibility, traffic limits, support coverage, and activation are confirmed before payment. <CampaignLink href={sitePath('/plans/#pro-billing')}>See Pro pricing details →</CampaignLink></p>
      </article>
      <article className="offer-card">
        <p className="section-label">FOR CUSTOM ORGANIZATIONAL NEEDS</p>
        <h3>Enterprise</h3>
        <PriceAmount amount="Custom" period="Pricing by proposal" />
        <p>Discuss higher traffic, reserved capacity, deployment requirements, integration, and operating support. We will define the scope and price together before any commitment.</p>
        <ul><li>Custom capacity and deployment proposal</li><li>Scoped integration and procurement support</li><li>Support and availability terms agreed in writing</li></ul>
        <CampaignLink className="button button-outline" href={sitePath('/contact/?interest=enterprise')}>Contact sales <span aria-hidden="true">↗</span></CampaignLink>
        <p className="field-hint">Deployment, availability, and response commitments depend on the agreed scope and operating capacity.</p>
      </article>
    </div>
    <div className="open-source-option"><div><span className="section-label">OPEN SOURCE · APACHE-2.0</span><h3>Operate your own gateway free.</h3><p>The existing gateway, controls, and reporting remain open source. You supply the infrastructure and provider accounts. Founder-led implementation and support have a separate scope.</p></div><CampaignLink className="button button-outline" href={sitePath('/docs/#quickstart')}>Explore open source <span aria-hidden="true">↗</span></CampaignLink></div>
  </section>;
}
