import { CampaignLink } from '../components/CampaignLink';
import { commercial, SOFTWARE_PRICE, CLOUD_PRICE, APPLIANCE_PRICE, ONBOARDING_PRICE, MANAGED_SITE_PRICE, RESERVATION_PRICE, pricing } from '../../lib/commercial.mjs';
import { pageMetadata } from '../../lib/metadata';
import { CONTACT_EMAIL, OWNER_NAME, OWNER_URL, sourcePath, sitePath } from '../../lib/site.mjs';
import { PageFrame, PageHero } from '../components/PageFrame';
import { ContactForm } from '../components/ContactForm';
import { LeadForm } from '../components/LeadForm';

export const metadata = pageMetadata('Cloud, appliance & managed-site inquiries — Hormuz', 'Discuss Cloud, an SSD-equipped appliance, scoped onboarding, or a managed site with a defined support allowance.', '/contact/');

export default function ContactPage() {
  return <PageFrame active="contact">
    <PageHero eyebrow="Cloud · Appliances · Managed sites" title={<>Bring your AI workflow.<br /><span>Let’s find the next step.</span></>}>
      <p>Discuss Cloud, an appliance, scoped onboarding, or site management. Mehrdad Zaker will reply personally. {commercial.formEndpoint ? 'Share a few details below.' : 'Prepare your inquiry below, then send it from your email app.'}</p>
    </PageHero>
    <section className="section contact-layout">
      <div className="contact-form-panel">
        <div className="inquiry-heading"><span className="section-label">Let’s start with your team</span><h2>Your workflow, in a few words.</h2><p>{commercial.formEndpoint ? 'Choose an offer to pay directly on Stripe, or send your workflow below. Submitting an inquiry takes no payment.' : 'Prepare → Review → Send from your inbox'}</p><CampaignLink className="text-link" href={sitePath('/plans/')}>See every offer and checkout →</CampaignLink></div>
        {commercial.formEndpoint ? <LeadForm endpoint={commercial.formEndpoint} bookingUrl={commercial.bookingUrl} /> : <ContactForm />}
        {commercial.bookingUrl && <div className="booking-card"><h2>Prefer to choose a time first?</h2><p>30 minutes on Google Meet. Wednesdays and Thursdays, 10 am–3 pm Central, subject to calendar availability.</p><a className="button button-outline" href={commercial.bookingUrl} rel="noreferrer">Book a review ↗</a></div>}
      </div>
      <div className="contact-guide">
        <div className="contact-person"><span className="contact-avatar" aria-hidden="true">MZ</span><div><strong>Mehrdad Zaker</strong><span>Hormuz founder · Director, <a href={OWNER_URL}>{OWNER_NAME} ↗</a></span></div></div>
        <p className="section-label">START WITH CLARITY</p><h2>A conversation first.<br />A clear scope next.</h2>
        <p>Share your API or client, expected traffic and concurrent streams, site count, and the controls you need. We can then confirm compatibility, capacity, and delivery.</p>
        <dl className="contact-offer-summary">
          <div><dt>Software</dt><dd>{SOFTWARE_PRICE}</dd></div>
          <div><dt>Cloud</dt><dd>{CLOUD_PRICE}<small>USD/workspace/month</small></dd></div>
          <div><dt>Appliance</dt><dd>{APPLIANCE_PRICE}<small>USD/unit, one time · SSD included</small></dd></div>
          <div><dt>Appliance with onboarding</dt><dd>{ONBOARDING_PRICE}<small>USD/unit, one time · SSD and scoped setup included</small></dd></div>
          <div><dt>Appliance reservation</dt><dd>{RESERVATION_PRICE}<small>USD/unit · refundable deposit credited toward purchase</small></dd></div>
          <div><dt>Managed site</dt><dd>{MANAGED_SITE_PRICE}<small>USD/site/month · Cloud workspace included</small></dd></div>
        </dl>
        <p className="field-hint">Managed sites include {pricing.managedSite.supportHoursPerMonth} hour of remote assistance per site per billing month, with a response within {pricing.managedSite.responseBusinessDays} business days. <CampaignLink href={sitePath('/enterprise/#support')}>Read the allowance and exclusions →</CampaignLink>.</p>
        <p>Appliances are coming soon, with rollout planned for {pricing.reservation.plannedRollout}. <CampaignLink href={sitePath('/enterprise/#reserve')}>Reserve yours with a refundable deposit →</CampaignLink>. All payments use Stripe. Compatibility, scope, operating capacity, and delivery are confirmed before the balance or service payment is collected. Model-provider usage and applicable taxes are separate. Sending an inquiry does not create a subscription or a paid reservation.</p>
        <ol className="contact-steps"><li><span>01</span><div><strong>Share the workflow</strong><small>Your client, expected traffic, and site requirements.</small></div></li><li><span>02</span><div><strong>Discuss fit together</strong><small>Compatibility, deployment, and capacity.</small></div></li><li><span>03</span><div><strong>Agree before you pay</strong><small>Service terms and a confirmed next step.</small></div></li></ol>
        <p>Prefer direct email?<br /><a className="text-link" href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a></p>
        <p>For vulnerabilities, use the <a href={sourcePath('SECURITY.md')}>private disclosure instructions</a>. For public troubleshooting, use <a href={sourcePath('SUPPORT.md')}>Support</a>.</p>
        <p><CampaignLink href={sitePath('/privacy/')}>How this website handles data →</CampaignLink></p>
      </div>
    </section>
  </PageFrame>;
}
