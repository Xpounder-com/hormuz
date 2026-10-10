import { CampaignLink } from '../components/CampaignLink';
import { commercial, SOFTWARE_PRICE, CLOUD_PRICE, MANAGED_SITE_PRICE } from '../../lib/commercial.mjs';
import { pageMetadata } from '../../lib/metadata';
import { CONTACT_EMAIL, OWNER_NAME, OWNER_URL, sourcePath, sitePath } from '../../lib/site.mjs';
import { PageFrame, PageHero } from '../components/PageFrame';
import { ContactForm } from '../components/ContactForm';
import { LeadForm } from '../components/LeadForm';

export const metadata = pageMetadata('Discuss your SME team’s AI setup — Hormuz', 'Confirm a supported AI workflow for your small or growing team. Discuss Cloud hosting or a managed site with defined operating help.', '/contact/');

export default function ContactPage() {
  return <PageFrame active="contact">
    <PageHero eyebrow="For small and growing teams" title={<>Tell us about your team’s AI workflow.<br /><span>Let’s confirm the right fit.</span></>}>
      <p>Share the applications and providers your team uses, expected traffic, and who operates the setup. Tell us whether you want Cloud hosting or managed-site operating help. Mehrdad Zaker will reply personally. {commercial.formEndpoint ? 'Share a few details below.' : 'Prepare your inquiry below, then send it from your email app.'}</p>
    </PageHero>
    <section className="section contact-layout">
      <div className="contact-form-panel">
        <div className="inquiry-heading"><span className="section-label">Let’s start with your team</span><h2>Your workflow, in a few words.</h2><p>{commercial.formEndpoint ? 'Describe one supported workflow and the result you need. Submitting an inquiry takes no payment.' : 'Prepare → Review → Send from your inbox'}</p><CampaignLink className="text-link" href={sitePath('/plans/')}>See every offer and checkout →</CampaignLink></div>
        {commercial.formEndpoint ? <LeadForm endpoint={commercial.formEndpoint} bookingUrl={commercial.bookingUrl} /> : <ContactForm />}
        {commercial.bookingUrl && <div className="booking-card"><h2>Prefer to choose a time first?</h2><p>A 30-minute workflow discussion on Google Meet. Wednesdays and Thursdays, 10 am–3 pm Central, subject to calendar availability.</p><a className="button button-outline" href={commercial.bookingUrl} rel="noreferrer">Choose a discussion time ↗</a></div>}
      </div>
      <div className="contact-guide">
        <div className="contact-person"><span className="contact-avatar" aria-hidden="true">MZ</span><div><strong>Mehrdad Zaker</strong><span>Hormuz founder · Director, <a href={OWNER_URL}>{OWNER_NAME} ↗</a></span></div></div>
        <p className="section-label">START WITH CLARITY</p><h2>A conversation first.<br />A clear scope next.</h2>
        <p>Share your current agents and providers, how your usage is billed, repository scope, and the limits you need. We can confirm which traffic Hormuz can observe and control before agreeing scope or activation.</p>
        <dl className="contact-offer-summary"><div><dt>Free software</dt><dd>{SOFTWARE_PRICE}</dd></div><div><dt>Cloud</dt><dd>{CLOUD_PRICE}<small>USD/workspace/month</small></dd></div><div><dt>Managed site</dt><dd>{MANAGED_SITE_PRICE}<small>USD/site/month · associated Cloud included</small></dd></div></dl>
        <p>Cloud is planned and accepting inquiries. We confirm compatibility, operating responsibilities, service coverage, and an activation date with you before you subscribe. Monthly billing starts at checkout and renews until canceled. <CampaignLink href={sitePath('/plans/#cloud')}>Cloud and cancellation terms →</CampaignLink>.</p>
        <p>Managed sites include their associated Cloud workspace and the stated operating support allowance. Provider usage is separate. We confirm your site’s compatibility, capacity, and coverage before you subscribe. Compare the services and optional appliances on <CampaignLink href={sitePath('/plans/')}>Pricing & pay</CampaignLink>. An inquiry creates no subscription or production gateway.</p>
        <ol className="contact-steps"><li><span>01</span><div><strong>Share the workflow</strong><small>Your agents, billing tools, and desired cost or speed priority.</small></div></li><li><span>02</span><div><strong>Discuss fit together</strong><small>Supported traffic, spending coverage, deployment, and capacity.</small></div></li><li><span>03</span><div><strong>Agree before you pay</strong><small>Service terms and a confirmed next step.</small></div></li></ol>
        <p>Prefer direct email?<br /><a className="text-link" href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a></p>
        <p>For vulnerabilities, use the <a href={sourcePath('SECURITY.md')}>private disclosure instructions</a>. For public troubleshooting, use <a href={sourcePath('SUPPORT.md')}>Support</a>.</p>
        <p><CampaignLink href={sitePath('/privacy/')}>How this website handles data →</CampaignLink></p>
      </div>
    </section>
  </PageFrame>;
}
