import { CampaignLink } from '../components/CampaignLink';
import { commercial } from '../../lib/commercial.mjs';
import { pageMetadata } from '../../lib/metadata';
import { CONTACT_EMAIL, sourcePath, sitePath } from '../../lib/site.mjs';
import { PageFrame, PageHero } from '../components/PageFrame';
import { ContactForm } from '../components/ContactForm';
import { LeadForm } from '../components/LeadForm';

export const metadata = pageMetadata('Discuss your team’s AI setup — Hormuz', 'Tell us about your tools and operating needs. Confirm compatibility, scope, and activation for Cloud hosting or managed-site support.', '/contact/');

export default function ContactPage() {
  return <PageFrame active="contact">
    <PageHero eyebrow="LET’S TALK" title="Tell us about your setup.">
      <p>Share your tools and operating needs. We’ll help you choose the right setup and confirm next steps.</p>
    </PageHero>
    <section className="section contact-layout">
      <div className="contact-form-panel">
        <div className="inquiry-heading"><h2 className="sr-only">Your team’s workflow</h2><p>{commercial.formEndpoint ? 'Sending an inquiry takes no payment.' : 'Prepare your inquiry, then send it from your email app.'}</p></div>
        {commercial.formEndpoint ? <LeadForm endpoint={commercial.formEndpoint} bookingUrl={commercial.bookingUrl} /> : <ContactForm />}
      </div>
      <aside className="contact-guide">
        <h2>A personal reply.</h2><p>Mehrdad Zaker will reply personally. Response target: one business day.</p>
        <p><a className="text-link" href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a></p>
        {commercial.bookingUrl && <div className="booking-card"><h3>Prefer a quick call?</h3><p>A 30-minute Google Meet discussion. Wednesdays and Thursdays, 10 am–3 pm Central, subject to availability.</p><a className="text-link" href={commercial.bookingUrl} rel="noreferrer">Choose a time ↗</a></div>}
        <p><CampaignLink className="text-link" href={sitePath('/plans/#offers')}>Compare plans →</CampaignLink></p>
        <p className="field-hint">For vulnerabilities, use the <a href={sourcePath('SECURITY.md')}>private disclosure instructions</a>. For troubleshooting, read <a href={sourcePath('SUPPORT.md')}>Support</a>.</p>
      </aside>
    </section>
  </PageFrame>;
}
