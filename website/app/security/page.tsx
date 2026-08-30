import { pageMetadata } from '../../lib/metadata';
import { sitePath } from '../../lib/site.mjs';
import { SiteFooter } from '../components/SiteFooter';
import { SiteHeader } from '../components/SiteHeader';

export const metadata = pageMetadata('Security & data handling — Hormuz', 'Review the implemented controls, metadata-only evidence, deployment responsibilities, and open security-review boundaries.', '/security/');

const handlingRows = [
  ['Prompts & responses', 'Relayed transiently in memory', 'Not written to routine usage or security telemetry'],
  ['Employee credentials', 'Validated at Hormuz', 'Never forwarded to model providers'],
  ['Provider credentials', 'Server-side environment or managed custody boundary', 'Never distributed to employees'],
  ['Usage & cost', 'Bounded identity, model, token, status, and cost metadata', 'No prompt or response body'],
  ['Secret-control evidence', 'Rule, action, outcome, and bounded counts', 'No matched value or raw request material'],
];

const openGates = [
  'Customer-specific TLS and network boundary',
  'Deployment-specific custody and retention',
  'HA, failover, backup, restore, RPO, and RTO proof',
  'Representative customer DLP evaluation',
  'Independent security assessment and penetration test',
];

export default function SecurityPage() {
  return (
    <>
      <SiteHeader active="security" />
      <main id="content" tabIndex={-1}>

      <section className="subpage-hero security-hero">
        <div className="subpage-grid" aria-hidden="true" />
        <div className="subpage-hero-inner wide">
          <p className="eyebrow"><span className="pulse-dot" aria-hidden="true" />Security & trust</p>
          <h1>Controls, evidence,<br /><span>and the gaps between them.</span></h1>
          <p>Hormuz documents what the v1.0.0 source contracts enforce, what your deployment must own, and what still requires independent proof.</p>
          <div className="hero-actions">
            <a className="button button-primary" href="#data-handling">Review data handling <span aria-hidden="true">↓</span></a>
            <a className="button button-ghost" href="https://github.com/Xpounder-com/hormuz/blob/main/SECURITY.md" target="_blank" rel="noreferrer">Read SECURITY.md <span aria-hidden="true">↗</span></a>
          </div>
        </div>
      </section>

      <section className="security-status section">
        <div><span>Source contracts</span><strong>v1.0.0</strong><i className="status-amber">Deployment qualification required</i></div>
        <div><span>Routine telemetry</span><strong>Content-free</strong><i>Schema-bound</i></div>
        <div><span>Provider keys</span><strong>Server-side</strong><i>Not sent to employees</i></div>
        <div><span>Independent review</span><strong>Open gate</strong><i className="status-amber">Not yet completed</i></div>
      </section>

      <section className="data-section section" id="data-handling">
        <div className="section-heading narrow">
          <p className="section-label">Data handling</p>
          <h2>Keep the control record. Leave the conversation out.</h2>
          <p>Hormuz inspects request material transiently where policy requires it, while routine ledgers retain bounded operational evidence rather than the content itself.</p>
        </div>

        <div className="data-table">
          <div className="data-table-head"><span>Data class</span><span>Current handling</span><span>Routine evidence boundary</span></div>
          {handlingRows.map(([dataClass, handling, boundary]) => (
            <div key={dataClass}><strong>{dataClass}</strong><span>{handling}</span><span>{boundary}</span></div>
          ))}
        </div>
      </section>

      <section className="security-controls">
        <div className="security-controls-inner">
          <div>
            <p className="section-label light">Current controls</p>
            <h2>Designed to fail closed at the boundary.</h2>
          </div>
          <div className="security-control-grid">
            <article><span>01</span><h3>Strict configuration</h3><p>Bounded parsing rejects duplicate members, malformed encoding, non-standard numbers, and unknown fields before startup.</p></article>
            <article><span>02</span><h3>Origin-bound egress</h3><p>Remote provider endpoints require HTTPS, credential-bearing URLs are rejected, and provider redirects are never followed.</p></article>
            <article><span>03</span><h3>Pre-provider enforcement</h3><p>Identity, model policy, budgets, privacy settings, and configured deterministic secret rules are checked before the governed provider call. Secret modes are redact, deny, or off.</p></article>
            <article><span>04</span><h3>OIDC JWT verification</h3><p>Validate issuer, audience, expiry, asymmetric signatures, and explicit subject mapping. Hormuz does not currently provide browser login, refresh-token custody, or its own session-revocation endpoint.</p></article>
          </div>
        </div>
      </section>

      <section className="gates-section section">
        <div className="gates-copy">
          <p className="section-label">Open enterprise gates</p>
          <h2>No trust badge can close these.</h2>
          <p>They require real customer-environment work, operational proof, or an independent party—not another marketing claim.</p>
          <div className="security-links">
            <a href="https://github.com/Xpounder-com/hormuz/blob/main/docs/ARCHITECTURE.md" target="_blank" rel="noreferrer">Architecture & boundaries ↗</a>
            <a href="https://github.com/Xpounder-com/hormuz/blob/main/docs/OPERATIONS.md" target="_blank" rel="noreferrer">Operations contract ↗</a>
          </div>
        </div>
        <ol className="gate-list">
          {openGates.map((gate, index) => <li key={gate}><span>0{index + 1}</span>{gate}</li>)}
        </ol>
      </section>

      <section className="section prose-section">
        <p className="section-label">A concise review packet</p><h2>Review the complete data path.</h2>
        <p>Clients send request content to Hormuz; the gateway inspects it transiently and forwards allowed content to the configured provider. Providers still process that content under your provider agreement. Metadata-only Hormuz ledgers do not make provider processing disappear.</p>
        <p>Operators own reverse-proxy logging, backups, access to metadata, retention, credentials, and deployment configuration. Do not enable infrastructure body logging. Treat identity and usage metadata as sensitive organizational data.</p>
        <p>Secret controls are not comprehensive semantic DLP. Custody-lifecycle approvals are separate from inference requests; no per-inference human-approval workflow is claimed. Estimated spend is not reconciled provider billing.</p>
        <div className="resource-actions"><a className="button button-primary" href={sitePath('/downloads/hormuz-trust-brief.pdf')}>Download trust brief ↓</a><a className="text-link" href={sitePath('/demo/#evidence')}>Inspect synthetic evidence →</a></div>
      </section>

      <section className="page-cta">
        <div><p className="section-label light">Security review</p><h2>Bring your actual control requirements.</h2><p>We will separate existing Hormuz evidence, customer-owned controls, and genuinely open engineering work.</p></div>
        <a className="button button-light" href={sitePath('/contact/?interest=security')}>Discuss your requirements <span aria-hidden="true">→</span></a>
      </section>

      </main>
      <SiteFooter />
    </>
  );
}
