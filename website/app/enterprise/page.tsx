import { EnterprisePlans } from '../components/EnterprisePlans';
import { StripeCheckoutLink } from '../components/StripeCheckoutLink';
import { CampaignLink } from '../components/CampaignLink';
import { CLOUD_PRICE, MANAGED_SITE_PRICE, ONBOARDING_PRICE, RESERVATION_PRICE, pricing } from '../../lib/commercial.mjs';
import { pageMetadata } from '../../lib/metadata';
import { CONTACT_EMAIL, sitePath, sourcePath, SOURCE_VERSION } from '../../lib/site.mjs';
import { PageFrame, PageHero } from '../components/PageFrame';

export const metadata = pageMetadata('Appliances & managed sites — Hormuz', 'Appliances coming soon, with rollout planned for early 2027. Reserve with a refundable ' + RESERVATION_PRICE + ' deposit, credited toward your purchase.', '/enterprise/');

const comparison = [
  ['Software controls', 'The Personal Optimizer and Apache-2.0 gateway', 'The same software on an appliance'],
  ['Hardware', 'Your own compatible device', 'enclosure, cooling, power supply, and installed gateway software'],
  ['Initial setup', 'Documentation and self-service configuration', 'The onboarding bundle includes one provider, one supported application, and initial policy setup'],
  ['Ongoing assistance', 'Public, best-effort community channels', `${pricing.managedSite.supportHoursPerMonth} hour of remote support per managed site per billing month; response within ${pricing.managedSite.responseBusinessDays} business days`],
  ['Cloud dashboard', `Optional Cloud at ${CLOUD_PRICE}/workspace/month`, 'One associated Cloud workspace included in the managed-site fee'],
  ['Provider accounts and network', 'Your responsibility', 'Your responsibility; model-provider usage is billed by your provider'],
];

export default function EnterprisePage() {
  return <PageFrame active="enterprise">
    <PageHero eyebrow="Appliances · Scoped onboarding · Managed sites" title={<>Your AI gateway.<br /><span>In your environment.</span></>}>
      <p>Coming soon: an appliance, with optional scoped onboarding and site management. Rollout is planned for {pricing.reservation.plannedRollout}. Reserve with a refundable deposit while hardware and operating capacity are qualified.</p>
      <div className="hero-actions"><a className="button button-primary" href="#reserve">Reserve yours — {RESERVATION_PRICE} ↓</a><CampaignLink className="button button-ghost" href={sitePath('/plans/')}>All prices →</CampaignLink></div>
    </PageHero>
    <EnterprisePlans />
    <section className="section" id="comparison">
      <div className="section-heading narrow"><p className="section-label">Software, hardware, and service</p><h2>Choose the help your site needs.</h2><p>Software controls remain available in the free product. An appliance packages the gateway for a customer network. A managed site adds the operating service described below.</p></div>
      <div className="table-scroll"><table className="comparison-table"><caption>Self-service software and appliance services</caption><thead><tr><th scope="col">Area</th><th scope="col">Self-service software</th><th scope="col">Appliance and managed site</th></tr></thead><tbody>{comparison.map(([area, software, service]) => <tr key={area}><th scope="row">{area}</th><td>{software}</td><td>{service}</td></tr>)}</tbody></table></div>
    </section>
    <section className="pilot-section section" id="onboarding">
      <div className="pilot-heading"><p className="section-label">Appliance with scoped onboarding · {ONBOARDING_PRICE}</p><h2>Connect one workflow and hand it over.</h2><p>The bundle includes the appliance and up to {pricing.onboarding.remoteHours} hours of remote onboarding. Confirm the supported application, provider, prerequisites, acceptance criteria, and start date before payment. It replaces the standalone appliance price for that unit.</p></div>
      <div className="pilot-steps">
        <article><span>01</span><h3>Prepare</h3><p>Name the site owner and review the customer network, power, authorized provider account, and approved test inputs.</p><strong>Prerequisites and agreed scope</strong></article>
        <article><span>02</span><h3>Connect</h3><p>Connect one provider and one supported application. Set initial identity, secret, and budget policies for the agreed workflow.</p><strong>Configured appliance and application</strong></article>
        <article><span>03</span><h3>Verify and hand over</h3><p>Check an allowed request, a denied request, and the available metadata. Record recovery steps, open issues, and the named operator.</p><strong>Acceptance record and operator handoff</strong></article>
      </div>
      <div className="docs-callout"><span aria-hidden="true">✓</span><p><strong>Scope is bounded:</strong> custom integrations, network redesign, data migration, on-site visits, and ongoing operations are excluded. Unfinished work at the allowance limit is documented before further work is considered.</p></div>
      <div className="resource-actions"><CampaignLink className="button button-primary" href={sitePath('/downloads/hormuz-appliance-brief.pdf')}>Read the appliance brief ↓</CampaignLink><a className="text-link" href={sourcePath('marketing/PILOT.md')}>Scope and responsibilities ↗</a></div>
    </section>
    <section className="section prose-section" id="support">
      <p className="section-label">Managed site · {MANAGED_SITE_PRICE}/month</p><h2>Defined operating help for one site.</h2>
      <p>The monthly fee includes one associated Cloud workspace, device-health monitoring, qualified software updates, recovery guidance, and up to <strong>{pricing.managedSite.supportHoursPerMonth} hour of remote assistance per site per billing month</strong>. Additional appliances at the same agreed site do not create another site subscription.</p>
      <p>Support hours are Monday–Friday, 9 am–5 pm America/Chicago, excluding local public holidays. The response target is <strong>within {pricing.managedSite.responseBusinessDays} business days</strong>; a response acknowledges the request and gives the next step. Resolution time depends on the issue. Unused support time does not roll over.</p>
      <p>Support covers configuration, policy questions, troubleshooting, and recovery guidance for the agreed deployment. The allowance includes calls, investigation, and configuration work. Custom development, on-site work, round-the-clock response, replacement hardware, and work beyond the allowance are excluded. Appliance purchases, model-provider charges, and applicable taxes are separate.</p>
      <p>A site is one agreed physical location or isolated customer deployment, recorded at activation. Appliances and workloads must fit the confirmed operating capacity. Cloud is included in this fee and is not charged again for that associated workspace.</p>
      <div className="resource-actions"><StripeCheckoutLink offer="managed">Subscribe to managed site — {MANAGED_SITE_PRICE}/month</StripeCheckoutLink><CampaignLink className="text-link" href={sitePath('/contact/?interest=managed')}>Confirm site coverage →</CampaignLink><a className="text-link" href="#activate">Activation and renewal ↓</a></div>
    </section>
    <section className="section prose-section" id="activate">
      <p className="section-label">Confirm delivery before the balance is due</p><h2>Agree the site and start date.</h2>
      <ol className="numbered-list"><li>Reserve an appliance with the refundable deposit, or send your site, supported workflow, expected traffic, and operator details through the inquiry form. Do not send provider keys, passwords, or customer prompts.</li><li>Hormuz confirms compatibility, capacity, delivery arrangements, service scope, and a start date. All payments use Stripe. Any reservation deposit is credited toward the chosen appliance package; the balance is collected only after delivery arrangements are confirmed.</li><li>After payment is verified, Hormuz records the site and appliances covered, activates the included Cloud workspace where applicable, and establishes the support channel. The support allowance belongs to the paid billing month.</li></ol>
      <h3 id="cancel">Monthly renewal and cancellation</h3><p>Managed sites and standalone Cloud subscriptions renew monthly until canceled. Email <a href={`mailto:${CONTACT_EMAIL}?subject=Cancel%20Hormuz%20subscription`}>{CONTACT_EMAIL}</a> before the next renewal with your subscription reference. Cancellation stops the next renewal; the current paid period remains available. Appliance ownership and free software access remain available after cancellation.</p>
    </section>
    <section className="enterprise-truth section"><div><span className="status-ring" aria-hidden="true"><i /></span><strong>{SOURCE_VERSION} core · preserved v1 contracts</strong></div><h2>Delivery depends on qualification.</h2><p>Appliance ARM64 packaging, capacity, thermal behavior, updates, and recovery require validation before delivery. Reference software checks do not establish your TLS, credential custody, retention, high availability, compliance, or independent security review. No production SLA or certification is claimed.</p><CampaignLink href={sitePath('/security/')}>Review the security boundary →</CampaignLink></section>
    <section className="page-cta"><div><p className="section-label light">Founder-led · Mehrdad Zaker</p><h2>Bring your site and its constraints.</h2><p>Confirm fit, scope, and delivery before payment.</p></div><CampaignLink className="button button-light" href={sitePath('/contact/?interest=onboarding')}>Discuss onboarding →</CampaignLink></section>
  </PageFrame>;
}
