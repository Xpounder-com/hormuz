import { pageMetadata } from '../../lib/metadata';
import { CONTACT_EMAIL } from '../../lib/site.mjs';
import { PageFrame, PageHero } from '../components/PageFrame';

export const metadata = pageMetadata('Website privacy — Hormuz', 'A static project site with local email drafting, no marketing analytics, and explicit hosting and email boundaries.', '/privacy/');

export default function PrivacyPage() {
  return <PageFrame active="privacy">
    <PageHero eyebrow="Website privacy" title={<>A project site.<br /><span>No hidden funnel.</span></>}>
      <p>Last updated August 30, 2026. This notice describes this static website, not your self-hosted Hormuz deployment or a future services agreement.</p>
    </PageHero>
    <section className="section prose-section">
      <h2>What happens when you visit</h2>
      <p>This build contains no marketing analytics SDK, advertising pixel, cookie banner, visitor identifier, or product-telemetry collector. Fonts and the recorded demo are served with the site; no third-party video player is embedded.</p>
      <p>GitHub Pages hosts the site and receives the requested URL, including its path and query string, in the initial HTTP request. Campaign parameters in a URL therefore reach the host before this application reads them. Requested URLs may be processed in hosting/security logs.</p>
      <p>GitHub states that Pages logs visitor IP addresses for security, including visits by people who are not signed in. See <a href="https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages">GitHub Pages documentation</a> and <a href="https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement">GitHub’s privacy statement</a> for its processing. “No marketing analytics” does not mean no hosting logs.</p>
      <h2>Contact drafts stay in your browser until you send</h2>
      <p>The contact form uses temporary page state. It does not submit form entries, save them to a server or browser storage, or confirm email delivery. Preparing a draft creates a mailto link and a copyable message. Your email service handles the message only when you choose to send it.</p>
      <p>The application reads campaign parameters locally and includes them in the draft only if you opt in using the visible checkbox. It sends no separate analytics or conversion event. This opt-in controls the email draft, not the initial URL request already received by GitHub Pages. Do not put personal information or secrets in campaign URLs.</p>
      <h2>After you choose to email</h2>
      <p>Your email may contain contact details and a workflow description. Send only information needed for the conversation. Do not include credentials, prompts, customer data, or other secrets. Ask Mehrdad Zaker at <a href={'mailto:' + CONTACT_EMAIL}>{CONTACT_EMAIL}</a> about deleting an inquiry or changing contact preferences.</p>
      <h2>External destinations</h2>
      <p>GitHub source, release, and community links leave this website. Their services apply their own terms. Downloading a synthetic demo or opening a link is not counted as a verified user, customer, or successful evaluation.</p>
    </section>
  </PageFrame>;
}
