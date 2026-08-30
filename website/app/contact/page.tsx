import { pageMetadata } from '../../lib/metadata';
import { CONTACT_EMAIL, sourcePath, sitePath } from '../../lib/site.mjs';
import { PageFrame, PageHero } from '../components/PageFrame';
import { ContactForm } from '../components/ContactForm';

export const metadata = pageMetadata('Talk to Mehrdad Zaker — Hormuz', 'Prepare a private email inquiry about one workflow, a bounded enterprise pilot, or open-source feedback. No account required.', '/contact/');

export default function ContactPage() {
  return <PageFrame active="contact"><PageHero eyebrow="Talk to the maintainer" title={<>Bring one workflow.<br /><span>Start a conversation.</span></>}><p>Reach Mehrdad Zaker about fit, integration, or an enterprise pilot. This form prepares an email locally; it does not submit data, book a meeting, or confirm a service.</p></PageHero><section className="section contact-layout"><div><p className="section-label">A small, useful inquiry</p><h2>What should the first conversation resolve?</h2><p>Share the client, the control you need, and the environment you would evaluate in. We can then discuss scope, capacity, and an appropriate next step.</p><p>Founder-led, best-effort replies. No response-time commitment or support SLA is established by this form.</p><p>Prefer direct email?<br /><a className="text-link" href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a></p><p>For vulnerabilities, use the <a href={sourcePath('SECURITY.md')}>private disclosure instructions</a>. For public troubleshooting, use <a href={sourcePath('SUPPORT.md')}>Support</a>.</p><p><a href={sitePath('/privacy/')}>How this website handles data →</a></p></div><ContactForm /></section></PageFrame>;
}
