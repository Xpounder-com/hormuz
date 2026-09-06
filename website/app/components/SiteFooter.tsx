import { AUTHOR, CONTACT_EMAIL, OWNER_NAME, OWNER_URL, REPOSITORY, sitePath, sourcePath } from '../../lib/site.mjs';
import { AdPreferencesButton } from './AdConsent';
import { BrandLockup } from './Brand';

export function SiteFooter() {
  return (
    <footer className="site-footer">
      <div className="footer-intro">
        <a className="brand footer-brand" href={sitePath('/')} aria-label="Hormuz home">
          <BrandLockup />
        </a>
        <p className="footer-owner">A product of <a href={OWNER_URL}>{OWNER_NAME} ↗</a></p>
        <p>Open-source AI policy and evidence, at the request boundary.</p>
        <span>Apache-2.0 · v1.0.0 source contracts<br />Production qualification remains deployment-specific.</span>
      </div>

      <div className="footer-column">
        <strong>Build with Hormuz</strong>
        <a href={sitePath('/docs/')}>Quickstart</a>
        <a href={sitePath('/demo/')}>Recorded demo</a>
        <a href={sitePath('/integrations/')}>Codex & Claude Code</a>
        <a href={`${REPOSITORY}/discussions`}>Community ↗</a>
        <a href={sourcePath('CONTRIBUTING.md')}>Contribute ↗</a>
      </div>

      <div className="footer-column">
        <strong>Evaluate</strong>
        <a href={sitePath('/enterprise/')}>Pricing & pilot</a>
        <a href={sitePath('/security/')}>Security & boundaries</a>
        <a href={sitePath('/resources/')}>Buyer & project resources</a>
        <a href={sitePath('/brand/')}>Brand & assets</a>
        <a href={REPOSITORY}>GitHub ↗</a>
        <a href={sitePath('/privacy/')}>Website privacy</a>
        <AdPreferencesButton />
      </div>

      <div className="footer-column">
        <strong>{AUTHOR}</strong>
        <span>Hormuz founder · Director, Neuralint</span>
        <a href={sitePath('/contact/?interest=review')}>Get a free AI review</a>
        <a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a>
        <a href={sourcePath('SECURITY.md')}>Report a vulnerability ↗</a>
      </div>
    </footer>
  );
}
