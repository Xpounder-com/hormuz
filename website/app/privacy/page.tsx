import { commercial } from '../../lib/commercial.mjs';
import { pageMetadata } from '../../lib/metadata';
import { CONTACT_EMAIL } from '../../lib/site.mjs';
import { PageFrame, PageHero } from '../components/PageFrame';
import { AdPreferencesButton } from '../components/AdConsent';

export const metadata = pageMetadata('Website privacy — Hormuz', 'How this website handles enterprise inquiries, campaign tags, booking, and payments.', '/privacy/');

export default function PrivacyPage() {
  return <PageFrame active="privacy">
    <PageHero eyebrow="Website privacy" title={<>Your inquiry.<br /><span>Your choice to share.</span></>}>
      <p>Last updated September 9, 2026. This notice describes this static website, not your self-hosted Hormuz deployment or a future services agreement.</p>
    </PageHero>
    <section className="section prose-section">
      <h2>What happens when you visit</h2>
      <p>Fonts and the recorded demo are served with the site; no third-party video player is embedded. Optional X Ads measurement stays off until you allow it using the privacy choices on this website. Safari and browsers on iPhone and iPad do not load X Ads code.</p>
      <p>GitHub Pages hosts the site and receives the requested URL, including its path and query string, in the initial HTTP request. Campaign parameters in a URL therefore reach the host before this application reads them. Requested URLs may be processed in hosting/security logs.</p>
      <p>GitHub states that Pages logs visitor IP addresses for security, including visits by people who are not signed in. See <a href="https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages">GitHub Pages documentation</a> and <a href="https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement">GitHub’s privacy statement</a> for its processing. Declining ad measurement does not disable hosting logs.</p>
      {commercial.formEndpoint ? <>
        <h2>When you submit an application</h2>
        <p>The application sends your name, work email, organization, workflow, interest, a random request reference, and optional timing to Formspree for Hormuz to review and respond. Formspree processes the submission and may apply spam filtering. See <a href="https://formspree.io/legal/privacy-policy/">Formspree’s privacy policy</a>. This is an inquiry, not consent to a marketing email subscription.</p>
        <p>Your entries stay in temporary page state while you complete the form. This application does not store them in cookies or browser storage. The submission request identifies the website origin to Formspree for domain filtering; its referrer header excludes the page path and query parameters. A receipt message appears only after the service acknowledges the submission; it does not mean a meeting, payment, or service agreement is confirmed.</p>
        <p>To request access to or deletion of an inquiry, email <a href={'mailto:' + CONTACT_EMAIL}>{CONTACT_EMAIL}</a>. Do not submit credentials, prompts, customer data, or confidential configurations.</p>
      </> : <>
        <h2>You choose when to move a draft out of this page</h2>
        <p>The contact form uses temporary page state. It does not submit form entries, save them to a server or browser storage, or confirm email delivery. Preparing a draft creates a mailto link and a copyable message. When you choose Open email app or paste the draft into another service, that app or service may process or save it before you send. Its privacy settings and policies apply.</p>
      </>}
      <p>Internal page links retain only bounded utm_source, utm_medium, utm_campaign, and utm_content parameters from the current page URL. The application includes them with your inquiry only if you select the unchecked campaign-source checkbox. This choice is separate from optional X Ads measurement and does not change the initial URL request already received by GitHub Pages. Do not put personal information or secrets in campaign URLs.</p>
      <h2 id="advertising">Optional X Ads measurement</h2>
      <p>In supported browsers, if you choose Allow measurement, the X Pixel measures site visits and sales inquiries (free reviews, pilots, and support) acknowledged by Formspree. Safari and browsers on iPhone and iPad keep measurement off for site reliability. X may use browser and network information, cookies, and an X ad click identifier to associate activity with ads and improve advertising. General integration, security, and community inquiries, marked QA tests, and filled spam traps do not trigger this sales event. An acknowledged inquiry is not a qualified buyer or a sale. An application event contains no name, email address, organization, workflow text, payment amount, or other form entry. We enable X’s page-location suppression and do not create website activity audiences for this event.</p>
      <p>We remember your allow or decline choice in browser storage for up to 180 days, without a visitor identifier. If storage is unavailable, the choice lasts only for the current page. We respect Global Privacy Control and Do Not Track signals by keeping measurement off. Declining does not prevent submitting an inquiry, booking, or paying.</p>
      <p>You can change your choice below or using Ad privacy choices in any page footer. If X’s code is active, turning measurement off reloads the page to unload it; copy unfinished form entries first. It stops future measurement on this website but does not remove data already sent to X. Cookies already set may remain until they expire or you clear them in your browser.</p>
      <AdPreferencesButton />
      <p>For X’s data practices, cookie lifetimes, and broader advertising opt-outs, see <a href="https://x.com/en/privacy">X’s privacy policy</a>, <a href="https://help.x.com/en/rules-and-policies/x-cookies">X’s cookie policy</a>, and <a href="https://help.x.com/en/safety-and-security/privacy-controls-for-tailored-ads">X’s personalized-ad privacy controls</a>.</p>
      {(commercial.bookingUrl || commercial.pilotPaymentUrl || commercial.supportPaymentUrl) && <><h2>Booking and payment services</h2><p>Booking and payment links open external services only when you choose them. Those services receive the details you enter and apply their own privacy policies and terms. Hormuz’s static website does not collect card details or confirm payment from a return URL. Review the named merchant, amount, billing interval, and cancellation terms before paying.</p></>}
      <h2>Review bookings</h2><p>Google Calendar manages the review schedule, checks the organizer’s calendar for conflicts, and sends invitation and reminder emails. It collects your name, email, and any optional organization or request reference you supply. It requires email verification for visitors who are not signed in to Google. Booking details stay with Google and the organizer; this site does not report bookings as confirmed X Ads conversions.</p><h2>After you choose to email</h2>
      <p>Your email may contain contact details and a workflow description. Send only information needed for the conversation. Do not include credentials, prompts, customer data, or other secrets. Ask Mehrdad Zaker at <a href={'mailto:' + CONTACT_EMAIL}>{CONTACT_EMAIL}</a> about deleting an inquiry or changing contact preferences.</p>
      <h2>External destinations</h2>
      <p>GitHub source, release, and community links leave this website. Their services apply their own terms. Downloading a synthetic demo or opening a link is not counted as a verified user, customer, or successful evaluation.</p>
    </section>
  </PageFrame>;
}
