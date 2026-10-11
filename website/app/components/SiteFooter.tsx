import { CampaignLink } from './CampaignLink';
import { OWNER_NAME, OWNER_URL, REPOSITORY, SOURCE_VERSION, MACOS_VERSION, sitePath } from '../../lib/site.mjs';
import { AdPreferencesButton } from './AdConsent';
import { BrandLockup } from './Brand';

export function SiteFooter() {
  return <footer className="site-footer simple-footer">
    <div className="simple-footer-main">
      <div className="footer-intro"><CampaignLink className="brand footer-brand" href={sitePath('/')} aria-label="Hormuz home"><BrandLockup /></CampaignLink><p>Open-source AI controls for teams.</p></div>
      <nav className="simple-footer-links" aria-label="Footer navigation">
        <CampaignLink href={sitePath('/docs/')}>Docs</CampaignLink>
        <CampaignLink href={sitePath('/plans/')}>Pricing</CampaignLink>
        <CampaignLink href={sitePath('/work/')}>AI Work</CampaignLink>
        <CampaignLink href={sitePath('/evidence/')}>Evidence</CampaignLink>
        <CampaignLink href={sitePath('/security/')}>Security</CampaignLink>
        <CampaignLink href={sitePath('/resources/')}>Resources</CampaignLink>
        <a href={REPOSITORY}>GitHub ↗</a>
        <CampaignLink href={sitePath('/contact/?interest=work')}>Contact</CampaignLink>
      </nav>
    </div>
    <div className="simple-footer-bottom"><span>Apache-2.0 · Core {SOURCE_VERSION.slice(1)} · Mac {MACOS_VERSION.slice(1)} · By <a href={OWNER_URL}>{OWNER_NAME}</a></span><div><CampaignLink href={sitePath('/privacy/')}>Privacy</CampaignLink><AdPreferencesButton /></div></div>
  </footer>;
}
