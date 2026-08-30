import { AUTHOR, CONTACT_EMAIL, REPOSITORY, sitePath, sourcePath } from '../../lib/site.mjs';

export function SiteFooter() {
  return (
    <footer className="site-footer">
      <div className="footer-intro">
        <a className="brand footer-brand" href={sitePath('/')} aria-label="Hormuz home">
          <span className="brand-mark" aria-hidden="true">
            <span />
            <span />
            <span />
          </span>
          <span className="brand-name">HORMUZ</span>
        </a>
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
        <a href={sitePath('/enterprise/')}>OSS & enterprise support</a>
        <a href={sitePath('/security/')}>Security & boundaries</a>
        <a href={sitePath('/resources/')}>Buyer & project resources</a>
        <a href={REPOSITORY}>GitHub ↗</a>
        <a href={sitePath('/privacy/')}>Website privacy</a>
      </div>

      <div className="footer-column">
        <strong>{AUTHOR}</strong>
        <a href={sitePath('/contact/')}>Discuss a pilot</a>
        <a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a>
        <a href={sourcePath('SECURITY.md')}>Report a vulnerability ↗</a>
      </div>
    </footer>
  );
}
