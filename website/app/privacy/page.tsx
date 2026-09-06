import { commercial } from '../../lib/commercial.mjs';
import { pageMetadata } from '../../lib/metadata';
import { CONTACT_EMAIL } from '../../lib/site.mjs';
import { PageFrame, PageHero } from '../components/PageFrame';

export const metadata = pageMetadata('Website privacy — Hormuz', 'How this website handles enterprise inquiries, campaign tags, booking, and payments.', '/privacy/');

export default function PrivacyPage() {
  return <PageFrame active="privacy">
    <PageHero eyebrow="Website privacy" title={<>Your inquiry.<br /><span>Your choice to share.</span></>}>
      <p>Last updated September 6, 2026. This notice describes this static website, not your self-hosted Hormuz deployment or a future services agreement.</p>
    </PageHero>
    <section className="section prose-section">
      <h2>What happens when you visit</h2>
      <p>This build contains no marketing analytics SDK, advertising pixel, cookie banner, visitor identifier, or product-telemetry collector. Fonts and the recorded demo are served with the site; no third-party video player is embedded.</p>
      <p>GitHub Pages hosts the site and receives the requested URL, including its path and query string, in the initial HTTP request. Campaign parameters in a URL therefore reach the host before this application reads them. Requested URLs may be processed in hosting/security logs.</p>
      <p>GitHub states that Pages logs visitor IP addresses for security, including visits by people who are not signed in. See <a href="https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages">GitHub Pages documentation</a> and <a href="https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement">GitHub’s privacy statement</a> for its processing. “No marketing analytics” does not mean no hosting logs.</p>
      {commercial.formEndpoint ? <>
        <h2>When you submit an application</h2>
        <p>The application sends your name, work email, organization, workflow, interest, and optional timing to Formspree for Hormuz to review and respond. Formspree processes the submission and may apply spam filtering. See <a href="https://formspree.io/legal/privacy-policy/">Formspree’s privacy policy</a>. This is an inquiry, not consent to a marketing email subscription.</p>
        <p>Your entries stay in temporary page state while you complete the form. This application does not store them in cookies or browser storage. A receipt message appears only after the service acknowledges the submission; it does not mean a meeting, payment, or service agreement is confirmed.</p>
        <p>To request access to or deletion of an inquiry, email <a href={'mailto:' + CONTACT_EMAIL}>{CONTACT_EMAIL}</a>. Do not submit credentials, prompts, customer data, or confidential configurations.</p>
      </> : <>
        <h2>You choose when to move a draft out of this page</h2>
        <p>The contact form uses temporary page state. It does not submit form entries, save them to a server or browser storage, or confirm email delivery. Preparing a draft creates a mailto link and a copyable message. When you choose Open email app or paste the draft into another service, that app or service may process or save it before you send. Its privacy settings and policies apply.</p>
      </>}
      <p>Selected application links retain only bounded utm_source, utm_medium, and utm_campaign parameters from the current page URL. The application includes them with your inquiry only if you select the unchecked campaign-source checkbox. It sends no separate analytics or conversion event. This choice does not change the initial URL request already received by GitHub Pages. Do not put personal information or secrets in campaign URLs.</p>
      {(commercial.bookingUrl || commercial.pilotPaymentUrl || commercial.supportPaymentUrl) && <><h2>Booking and payment services</h2><p>Booking and payment links open external services only when you choose them. Those services receive the details you enter and apply their own privacy policies and terms. Hormuz’s static website does not collect card details or confirm payment from a return URL. Review the named merchant, amount, billing interval, and cancellation terms before paying.</p></>}
      <h2>After you choose to email</h2>
      <p>Your email may contain contact details and a workflow description. Send only information needed for the conversation. Do not include credentials, prompts, customer data, or other secrets. Ask Mehrdad Zaker at <a href={'mailto:' + CONTACT_EMAIL}>{CONTACT_EMAIL}</a> about deleting an inquiry or changing contact preferences.</p>
      <h2>External destinations</h2>
      <p>GitHub source, release, and community links leave this website. Their services apply their own terms. Downloading a synthetic demo or opening a link is not counted as a verified user, customer, or successful evaluation.</p>
    </section>
  </PageFrame>;
}
