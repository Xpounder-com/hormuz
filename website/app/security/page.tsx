import { CampaignLink } from '../components/CampaignLink';
import { pageMetadata } from '../../lib/metadata';
import { sitePath, sourcePath, SOURCE_VERSION } from '../../lib/site.mjs';
import { PageFrame } from '../components/PageFrame';

export const metadata = pageMetadata('Security & data handling — Hormuz', 'Review the implemented controls, metadata-only evidence, deployment responsibilities, and security checks still needed.', '/security/');

const handlingRows = [
  ['Prompts & responses', 'Relayed transiently in memory', 'Not written to routine usage or security telemetry'],
  ['Employee credentials', 'Validated at Hormuz', 'Never forwarded to model providers'],
  ['Provider credentials', 'Server-side environment or managed custody boundary', 'Never distributed to employees'],
  ['Usage & cost', 'Bounded identity, model, token, status, and cost metadata', 'No prompt or response body'],
  ['AI Work ledger', 'SQLite work ownership, supplied labels/title, budgets, attempts and observations', 'No automatic prompt capture, response body, patch, or tool output'],
  ['Exact answer reuse', 'Separately opt-in, bounded process memory with expiry', 'Response content stays in memory; no cache body in SQLite; restart clears it'],
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
    <PageFrame active="security">

      <section className="subpage-hero security-hero">
        <div className="subpage-grid" aria-hidden="true" />
        <div className="subpage-hero-inner wide">
          <p className="eyebrow"><span className="pulse-dot" aria-hidden="true" />Security & trust</p>
          <h1>Know how your data<br /><span>moves through Hormuz.</span></h1>
          <p>See how the published {SOURCE_VERSION} release and newer AI Work source handle your data, which controls they provide, and which security responsibilities belong to your deployment.</p>
          <div className="hero-actions">
            <a className="button button-primary" href="#data-handling">Review data handling <span aria-hidden="true">↓</span></a>
            <a className="button button-ghost" href={sourcePath('SECURITY.md')} target="_blank" rel="noreferrer">Read SECURITY.md <span aria-hidden="true">↗</span></a>
          </div>
        </div>
      </section>

      <section className="security-status section" id="status">
        <div><span>Source release</span><strong>{SOURCE_VERSION}</strong><i className="status-amber">Check your deployment’s security</i></div>
        <div><span>Routine telemetry</span><strong>Content-free</strong><i>Schema-bound</i></div>
        <div><span>Provider keys</span><strong>Server-side</strong><i>Not sent to employees</i></div>
        <div><span>Independent review</span><strong>Pending</strong><i className="status-amber">Not yet completed</i></div>
      </section>

      <section className="data-section section" id="data-handling">
        <div className="section-heading narrow">
          <p className="section-label">Data handling</p>
          <h2>Keep the control record. Leave the conversation out.</h2>
          <p>Hormuz inspects request material in memory where policy requires it. Routine ledgers retain bounded operational metadata; optional exact answer reuse has a separate, explicit in-memory retention boundary.</p>
        </div>
        <div className="data-table">
          <div className="data-table-head"><span>Data class</span><span>Current handling</span><span>Routine evidence boundary</span></div>
          {handlingRows.map(([dataClass, handling, boundary]) => (
            <div key={dataClass}><strong>{dataClass}</strong><span>{handling}</span><span>{boundary}</span></div>
          ))}
        </div>
        <p className="after-grid">AI Work titles and context references are caller-supplied metadata; they are not extracted from prompts. Exact answer reuse is Off by default, uses bounded process memory, and expires or clears on restart. <a href={sourcePath('docs/AI_WORK_RUNTIME.md')}>Inspect the full ledger and cache contract ↗</a>.</p>
      </section>

      <section className="security-controls" id="controls">
        <div className="security-controls-inner">
          <div>
            <p className="section-label light">Current controls</p>
            <h2>Designed to fail closed at the boundary.</h2>
          </div>
          <div className="security-control-grid">
            <article><span>01</span><h3>Strict configuration</h3><p>Bounded parsing rejects duplicate members, malformed encoding, non-standard numbers, and unknown fields before startup.</p></article>
            <article><span>02</span><h3>Origin-bound egress</h3><p>Remote provider endpoints require HTTPS, credential-bearing URLs are rejected, and provider redirects are never followed.</p></article>
            <article><span>03</span><h3>Pre-provider enforcement</h3><p>Identity, model policy, budgets, privacy settings, and configured deterministic secret rules are checked before the governed provider call. Secret modes are redact, deny, or off.</p></article>
            <article><span>04</span><h3>OIDC and sessions</h3><p>Validate issuer, audience, expiry, asymmetric signatures, and explicit subject mapping. The opt-in broker adds browser login and revocable Hormuz sessions without retaining identity-provider access or refresh tokens. Check this sign-in flow with your identity provider and deployment.</p></article>
          </div>
        </div>
      </section>

      <section className="gates-section section" id="gates">
        <div className="gates-copy">
          <p className="section-label">Security checks for your deployment</p>
          <h2>Check the requirements your organization relies on.</h2>
          <p>These checks are specific to your infrastructure and policies. Availability and recovery need operational testing; security assessment requires an independent reviewer. They are not established by the software’s local tests.</p>
          <div className="security-links">
            <a href={sourcePath('docs/ARCHITECTURE.md')} target="_blank" rel="noreferrer">Architecture & boundaries ↗</a>
            <a href={sourcePath('docs/OPERATIONS.md')} target="_blank" rel="noreferrer">Operations contract ↗</a>
          </div>
        </div>
        <ol className="gate-list">
          {openGates.map((gate, index) => <li key={gate}><span>0{index + 1}</span>{gate}</li>)}
        </ol>
      </section>

      <section className="section prose-section">
        <p className="section-label">A concise review packet</p><h2>Review the complete data path.</h2>
        <p>Clients send request content to Hormuz; the gateway inspects it transiently and forwards allowed content to the configured provider. Providers still process that content under your provider agreement. Metadata-only Hormuz ledgers do not make provider processing disappear.</p>
        <p>With the planned Cloud service, request content would pass through infrastructure operated by Hormuz. Metadata-only retention does not mean Hormuz has no access to content in transit. Cloud is accepting inquiries. We agree credential isolation, retention, recovery, and operating responsibilities with you before activation.</p>
        <p>Operators own reverse-proxy logging, backups, access to metadata, retention, credentials, and deployment configuration. Do not enable infrastructure body logging. Treat identity and usage metadata as sensitive organizational data.</p>
        <p>Secret controls are not comprehensive semantic DLP. Credential custody approvals are separate from model requests. Hormuz does not provide human approval for each inference request. Estimated spend is not reconciled provider billing.</p>
        <div className="resource-actions"><CampaignLink className="button button-primary" href={sitePath('/downloads/hormuz-trust-brief.pdf')}>Download trust brief ↓</CampaignLink><CampaignLink className="text-link" href={sitePath('/demo/#evidence')}>Inspect synthetic evidence →</CampaignLink></div>
      </section>

      <section className="page-cta">
        <div><p className="section-label light">Security review</p><h2>Discuss your security requirements.</h2><p>We can help you review the available controls, your deployment responsibilities, and requirements that Hormuz does not yet meet.</p></div>
        <CampaignLink className="button button-light" href={sitePath('/contact/?interest=security')}>Discuss your requirements <span aria-hidden="true">→</span></CampaignLink>
      </section>

      </PageFrame>
  );
}
