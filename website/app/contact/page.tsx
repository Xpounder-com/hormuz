import { CampaignLink } from '../components/CampaignLink';
import { commercial, PILOT_PRICE, SUPPORT_PRICE } from '../../lib/commercial.mjs';
import { pageMetadata } from '../../lib/metadata';
import { CONTACT_EMAIL, OWNER_NAME, OWNER_URL, sourcePath, sitePath } from '../../lib/site.mjs';
import { PageFrame, PageHero } from '../components/PageFrame';
import { ContactForm } from '../components/ContactForm';
import { LeadForm } from '../components/LeadForm';

export const metadata = pageMetadata(
  'Free AI review & enterprise inquiries — Hormuz',
  'Request a free AI governance review, apply for a 90-day enterprise pilot, or discuss ongoing support.',
  '/contact/',
);

export default function ContactPage() {
  return <PageFrame active="contact">
    <PageHero eyebrow="Founder-led enterprise support" title={<>Bring your AI workflow.<br /><span>Let’s find the next step.</span></>}>
      <p>Request a free AI governance review, discuss a pilot, or ask about support. Mehrdad Zaker will reply personally. {commercial.formEndpoint ? 'Share a few details below.' : 'Prepare your inquiry below, then send it from your email app.'}</p>
    </PageHero>
    <section className="section contact-layout">
      <div className="contact-form-panel">
        <div className="inquiry-heading"><span className="section-label">Let’s start with your team</span><h2>Your workflow, in a few words.</h2><p>{commercial.formEndpoint ? 'Choose your next step. No payment is taken here.' : 'Prepare → Review → Send from your inbox'}</p></div>
        {commercial.formEndpoint
          ? <LeadForm endpoint={commercial.formEndpoint} bookingUrl={commercial.bookingUrl} />
          : <ContactForm />}
        {commercial.bookingUrl && <div className="booking-card">
          <h2>Prefer to choose a time first?</h2>
          <p>30 minutes on Google Meet. Wednesdays and Thursdays, 10 am–3 pm Central, subject to calendar availability.</p>
          <a className="button button-outline" href={commercial.bookingUrl} rel="noreferrer">Book a free review ↗</a>
        </div>}
      </div>
      <div className="contact-guide">
        <div className="contact-person"><span className="contact-avatar" aria-hidden="true">MZ</span><div><strong>Mehrdad Zaker</strong><span>Hormuz founder · Director, <a href={OWNER_URL}>{OWNER_NAME} ↗</a></span></div></div>
        <p className="section-label">START WITH CLARITY</p>
        <h2>A conversation first.<br />A clear scope next.</h2>
        <p>Share the client, the control you need, and the environment you would evaluate in. We can then discuss scope, capacity, and an appropriate next step.</p>
        <dl className="contact-offer-summary"><div><dt>AI governance review</dt><dd>Free</dd></div><div><dt>90-day pilot</dt><dd>{PILOT_PRICE} <small>USD one-time</small></dd></div><div><dt>Ongoing support</dt><dd>From {SUPPORT_PRICE} <small>USD/month</small></dd></div></dl><p className="field-hint">The pilot covers one team and one workflow. Support is a separate agreement. Provider usage, infrastructure, and applicable taxes are additional.</p>
        <p>Scope, delivery capacity, and terms are agreed before you pay. Sending an inquiry does not create a subscription or reserve a start date.</p>
        <ol className="contact-steps"><li><span>01</span><div><strong>Share the workflow</strong><small>Your team, AI client, and control requirements.</small></div></li><li><span>02</span><div><strong>Discuss fit together</strong><small>Scope, prerequisites, and capacity.</small></div></li><li><span>03</span><div><strong>Agree before you pay</strong><small>A written proposal and a clear next step.</small></div></li></ol>
        <p>Prefer direct email?<br /><a className="text-link" href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a></p>
        <p>For vulnerabilities, use the <a href={sourcePath('SECURITY.md')}>private disclosure instructions</a>. For public troubleshooting, use <a href={sourcePath('SUPPORT.md')}>Support</a>.</p>
        <p><CampaignLink href={sitePath('/privacy/')}>How this website handles data →</CampaignLink></p>
      </div>
    </section>
  </PageFrame>;
}
