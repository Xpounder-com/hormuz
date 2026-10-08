import { CampaignLink } from './CampaignLink';
import { AUTHOR, CONTACT_EMAIL, OWNER_NAME, OWNER_URL, REPOSITORY, SOURCE_VERSION, MACOS_VERSION, sitePath, sourcePath } from '../../lib/site.mjs';
import { AdPreferencesButton } from './AdConsent';
import { BrandLockup } from './Brand';

export function SiteFooter() {
  return (
    <footer className="site-footer">
      <div className="footer-intro">
        <CampaignLink className="brand footer-brand" href={sitePath('/')} aria-label="Hormuz home">
          <BrandLockup />
        </CampaignLink>
        <p className="footer-owner">A product of <a href={OWNER_URL}>{OWNER_NAME} ↗</a></p>
        <p>Keep your agents. Choose your budget and pace.</p>
        <span>Apache-2.0 · Core {SOURCE_VERSION.slice(1)} · Mac {MACOS_VERSION.slice(1)}<br />Production qualification remains deployment-specific.</span>
      </div>

      <div className="footer-column">
        <strong>Build with Hormuz</strong>
        <CampaignLink href={sitePath('/docs/')}>Install free</CampaignLink>
        <CampaignLink href={sitePath('/work/')}>Open AI Work</CampaignLink>
        <CampaignLink href={sitePath('/evidence/')}>Inspect the evidence</CampaignLink>
        <CampaignLink href={sitePath('/demo/#policy')}>Try a policy</CampaignLink>
        <CampaignLink href={sitePath('/demo/#compaction')}>See context compaction</CampaignLink>
        <CampaignLink href={sitePath('/integrations/')}>Check my stack</CampaignLink>
        <a href={`${REPOSITORY}/discussions`}>Community ↗</a>
        <a href={sourcePath('CONTRIBUTING.md')}>Contribute ↗</a>
      </div>

      <div className="footer-column">
        <strong>Evaluate</strong>
        <CampaignLink href={sitePath('/plans/')}>Pricing & pay</CampaignLink>
        <CampaignLink href={sitePath('/enterprise/')}>Enterprise & services</CampaignLink>
        <CampaignLink href={sitePath('/security/')}>Security & boundaries</CampaignLink>
        <CampaignLink href={sitePath('/resources/')}>Buyer & project resources</CampaignLink>
        <CampaignLink href={sitePath('/brand/')}>Brand & assets</CampaignLink>
        <a href={REPOSITORY}>GitHub ↗</a>
        <CampaignLink href={sitePath('/privacy/')}>Website privacy</CampaignLink>
        <AdPreferencesButton />
      </div>

      <div className="footer-column">
        <strong>{AUTHOR}</strong>
        <span>Hormuz founder · Director, Neuralint</span>
        <CampaignLink href={sitePath('/contact/?interest=work')}>Discuss your workflow</CampaignLink>
        <a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a>
        <a href={sourcePath('SECURITY.md')}>Report a vulnerability ↗</a>
      </div>
    </footer>
  );
}
