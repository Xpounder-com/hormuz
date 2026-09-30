import { CampaignLink } from './CampaignLink';
import { PriceAmount } from './PriceCard';
import { sitePath } from '../../lib/site.mjs';
import { PILOT_PRICE, SUPPORT_PRICE } from '../../lib/commercial.mjs';

export function OfferingPaths() {
  return <section className="section offer-section" id="offers" aria-labelledby="offers-title">
    <div className="section-heading">
      <p className="section-label">CHOOSE HOW YOU USE HORMUZ</p>
      <h2 id="offers-title">Start with your own work.<br />Bring your team when you need to.</h2>
      <p className="offer-intro">The software is open source. Personal use needs no Hormuz account or team gateway. Team services are priced separately for the help involved.</p>
    </div>
    <div className="offer-grid offer-grid-two conversion-offers">
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
        <p className="section-label">FOR A TEAM</p>
        <h3>Self-hosted gateway</h3>
        <PriceAmount amount="$0" period="Software license · Apache-2.0" />
        <p>Operate the gateway with your own provider accounts and infrastructure. Route supported coding clients through your team’s policies and usage reporting.</p>
        <ul><li>Identity, model access, budgets, and secret controls</li><li>Metadata-only usage and evidence</li><li>Mac companion for an existing team gateway</li><li>Documentation and community support</li></ul>
        <CampaignLink className="button button-outline" href={sitePath('/docs/#quickstart')}>Explore team setup <span aria-hidden="true">↗</span></CampaignLink>
        <p className="field-hint">Your organization operates the gateway and pays its provider and infrastructure charges.</p>
      </article>
    </div>
    <div className="open-source-option"><div><span className="section-label">FOUNDER-LED TEAM SERVICES</span><h3>Pay for defined help.</h3><p>Support for one self-hosted gateway is {SUPPORT_PRICE}/month. A scoped 90-day pilot for one team and workflow is {PILOT_PRICE}. Review the terms and delivery boundaries before paying.</p></div><CampaignLink className="button button-outline" href={sitePath('/enterprise/')}>See team services <span aria-hidden="true">↗</span></CampaignLink></div>
  </section>;
}
