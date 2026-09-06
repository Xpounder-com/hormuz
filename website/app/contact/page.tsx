import { commercial, PILOT_PRICE, SUPPORT_PRICE } from '../../lib/commercial.mjs';
import { pageMetadata } from '../../lib/metadata';
import { CONTACT_EMAIL, sourcePath, sitePath } from '../../lib/site.mjs';
import { PageFrame, PageHero } from '../components/PageFrame';
import { ContactForm } from '../components/ContactForm';
import { LeadForm } from '../components/LeadForm';

export const metadata = pageMetadata(
  'Enterprise application — Hormuz',
  'Request a free AI governance review, apply for a 90-day enterprise pilot, or discuss ongoing support.',
  '/contact/',
);

export default function ContactPage() {
  return <PageFrame active="contact">
    <PageHero eyebrow="Founder-led enterprise support" title={<>One team. One workflow.<br /><span>Start your conversation.</span></>}>
      <p>Tell Mehrdad Zaker what your team needs to control. Start with a free governance review, a scoped pilot, or ongoing support. {commercial.formEndpoint ? 'Submit your application below.' : 'Prepare your inquiry below, then send it from your email app.'}</p>
    </PageHero>
    <section className="section contact-layout">
      <div className="contact-guide">
        <div className="contact-person"><span className="contact-avatar" aria-hidden="true">MZ</span><div><strong>Mehrdad Zaker</strong><span>Founder & maintainer</span></div></div>
        <p className="section-label">Your path to a pilot</p>
        <h2>Know what you are evaluating.</h2>
        <p>Share the client, the control you need, and the environment you would evaluate in. We can then discuss scope, capacity, and an appropriate next step.</p>
        <p>The 90-day pilot is {PILOT_PRICE} USD for one team and one workflow. Ongoing enterprise support starts at {SUPPORT_PRICE} USD/month under a separate agreement. Provider usage, infrastructure, and applicable taxes are additional.</p>
        <p>Scope, delivery capacity, and terms are agreed before you pay. Sending an inquiry does not create a subscription or reserve a start date.</p>
        <ol className="contact-steps"><li><span>01</span><div><strong>Share the workflow</strong><small>Your team, AI client, and control requirements.</small></div></li><li><span>02</span><div><strong>Discuss fit together</strong><small>Scope, prerequisites, and capacity.</small></div></li><li><span>03</span><div><strong>Agree before you pay</strong><small>A written proposal and a clear next step.</small></div></li></ol>
        <p>Prefer direct email?<br /><a className="text-link" href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a></p>
        <p>For vulnerabilities, use the <a href={sourcePath('SECURITY.md')}>private disclosure instructions</a>. For public troubleshooting, use <a href={sourcePath('SUPPORT.md')}>Support</a>.</p>
        <p><a href={sitePath('/privacy/')}>How this website handles data →</a></p>
      </div>
      <div className="contact-form-panel">
        <div className="inquiry-heading"><span className="section-label">Let’s start with your team</span><h2>Your workflow, in a few words.</h2><p>{commercial.formEndpoint ? 'Submit an application for a fit discussion.' : 'Prepare → Review → Send from your inbox'}</p></div>
        {commercial.bookingUrl && <div className="booking-card">
          <h2>Start with a free governance review.</h2>
          <p>Discuss your workflow, controls, and fit before committing to a pilot.</p>
          <a className="button button-primary" href={commercial.bookingUrl} rel="noreferrer">Book a free review ↗</a>
        </div>}
        {commercial.formEndpoint
          ? <LeadForm endpoint={commercial.formEndpoint} bookingUrl={commercial.bookingUrl} />
          : <ContactForm />}
      </div>
    </section>
  </PageFrame>;
}
