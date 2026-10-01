import { CampaignLink } from '../components/CampaignLink';
import { commercial, PILOT_PRICE, SUPPORT_PRICE, PRO_PRICE } from '../../lib/commercial.mjs';
import { pageMetadata } from '../../lib/metadata';
import { CONTACT_EMAIL, OWNER_NAME, OWNER_URL, sourcePath, sitePath } from '../../lib/site.mjs';
import { PageFrame, PageHero } from '../components/PageFrame';
import { ContactForm } from '../components/ContactForm';
import { LeadForm } from '../components/LeadForm';

export const metadata = pageMetadata(
  'Hosted Pro access & Enterprise inquiries — Hormuz',
  'Request hosted Pro access for production API workflows, discuss a custom Enterprise proposal, or ask about implementation and support.',
  '/contact/',
);

export default function ContactPage() {
  return <PageFrame active="contact">
    <PageHero eyebrow="Hosted Pro · Enterprise · Team services" title={<>Bring your AI workflow.<br /><span>Let’s find the next step.</span></>}>
      <p>Request hosted Pro access, discuss custom Enterprise capacity and support, or ask about a pilot or implementation. Mehrdad Zaker will reply personally. {commercial.formEndpoint ? 'Share a few details below.' : 'Prepare your inquiry below, then send it from your email app.'}</p>
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
        <p>Share your API or client, expected traffic and concurrent streams, and the controls you need. We can then discuss compatibility, capacity, and an appropriate next step.</p>
        <dl className="contact-offer-summary"><div><dt>Hosted Pro</dt><dd>{PRO_PRICE} <small>USD/workspace/month + gateway usage</small></dd></div><div><dt>Enterprise</dt><dd>Custom quote <small>capacity, deployment, and support</small></dd></div><div><dt>AI governance review</dt><dd>Free</dd></div></dl><p className="field-hint">Pro includes 100,000 monthly gateway requests, then $10 per additional 100,000. Administrative users are included. Pro is accepting inquiries; access and service terms are confirmed before payment. Model-provider usage and applicable taxes are separate.</p>
        <p>Separate self-hosted services: a scoped 90-day pilot is {PILOT_PRICE}; support for one gateway and up to four hours per billing month is {SUPPORT_PRICE}/month. These services have their own delivery and infrastructure responsibilities.</p>
        <p>Ready for ongoing help? <CampaignLink href={sitePath('/enterprise/#support')}>Review the fixed support terms and start directly →</CampaignLink></p><p>For a pilot or custom engagement, we agree scope, delivery capacity, and terms before payment. Sending an inquiry does not create a subscription or reserve a start date.</p>
        <ol className="contact-steps"><li><span>01</span><div><strong>Share the workflow</strong><small>Your API or client, expected traffic, and control requirements.</small></div></li><li><span>02</span><div><strong>Discuss fit together</strong><small>Compatibility, deployment, and capacity.</small></div></li><li><span>03</span><div><strong>Agree before you pay</strong><small>Service terms and a confirmed next step.</small></div></li></ol>
        <p>Prefer direct email?<br /><a className="text-link" href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a></p>
        <p>For vulnerabilities, use the <a href={sourcePath('SECURITY.md')}>private disclosure instructions</a>. For public troubleshooting, use <a href={sourcePath('SUPPORT.md')}>Support</a>.</p>
        <p><CampaignLink href={sitePath('/privacy/')}>How this website handles data →</CampaignLink></p>
      </div>
    </section>
  </PageFrame>;
}
