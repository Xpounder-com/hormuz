import { pageMetadata } from '../lib/metadata';
import { sitePath } from '../lib/site.mjs';
import { SiteFooter } from './components/SiteFooter';
import { SiteHeader } from './components/SiteHeader';

export const metadata = pageMetadata('Hormuz — Open-source AI policy gateway', 'Keep Codex and Claude Code. Add self-hosted policy, budgets, secret controls, and metadata-only evidence. Apache-2.0, with a provider-free demo.', '/');

const decisionSteps = [
  { label: 'Identity', value: 'Verified', tone: 'cyan' },
  { label: 'Policy', value: 'Matched', tone: 'green' },
  { label: 'Secrets', value: 'Clear', tone: 'green' },
  { label: 'Budget', value: 'Reserved', tone: 'amber' },
];

const capabilities = [
  {
    index: '01',
    title: 'Policy that only gets tighter',
    copy: 'Layer organization, team, and person rules without letting a lower scope weaken the controls above it.',
    tag: 'Fail closed',
  },
  {
    index: '02',
    title: 'Provider keys stay put',
    copy: 'Employees authenticate to Hormuz. Company provider credentials remain on the controlled server boundary.',
    tag: 'BYOK custody',
  },
  {
    index: '03',
    title: 'Budgets before egress',
    copy: 'Check model access, token ceilings, and spend allowances before a request reaches a provider.',
    tag: 'Pre-provider',
  },
  {
    index: '04',
    title: 'Secrets stopped before egress',
    copy: 'Redact or deny configured credentials and high-confidence secret formats before forwarding the request. Deterministic controls, not semantic DLP.',
    tag: 'Redact or deny',
  },
  {
    index: '05',
    title: 'Identity from your issuer',
    copy: 'Verify OIDC JWT access tokens and explicitly map subjects to organization, team, and person. Token issuance and refresh stay with your identity tooling.',
    tag: 'OIDC JWT',
  },
  {
    index: '06',
    title: 'Evidence without the payload',
    copy: 'Attribute governed usage, estimated cost, policy version, and security outcomes while keeping prompts and responses out of the ledger.',
    tag: 'Content-free',
  },
];

const boundaryRows = [
  ['Identity & access', 'Who is making the request and which controls apply'],
  ['Policy & budgets', 'Which models, limits, and spend envelopes are allowed'],
  ['Egress controls', 'Whether configured secret rules redact or deny a request'],
  ['Evidence', 'What happened, under which policy, without retaining the conversation'],
];

export default function Home() {
  return (
    <>
      <SiteHeader active="platform" overlay />
      <main id="content" tabIndex={-1}>
      <section className="hero" id="top">
        <div className="hero-grid" aria-hidden="true" />
        <div className="hero-orbit hero-orbit-one" aria-hidden="true" />
        <div className="hero-orbit hero-orbit-two" aria-hidden="true" />

        <div className="hero-inner">
          <div className="hero-copy">
            <p className="eyebrow reveal reveal-one">
              <span className="pulse-dot" aria-hidden="true" />
              Open-source AI policy gateway
            </p>
            <h1 className="reveal reveal-two">
              Every AI request.
              <span>One governed route.</span>
            </h1>
            <p className="hero-deck reveal reveal-three">
              Hormuz sits between the AI tools your teams already use and the
              model providers you already buy—enforcing policy, protecting
              credentials, and producing evidence for every governed request.
            </p>
            <div className="hero-actions reveal reveal-four">
              <a className="button button-primary" href={sitePath('/docs/#quickstart')}>
                Try open source
                <span aria-hidden="true">→</span>
              </a>
              <a
                className="button button-ghost"
                href={sitePath('/demo/')}
              >
                Watch the real demo
                <span aria-hidden="true">↗</span>
              </a>
            </div>
            <div className="hero-proof reveal reveal-four">
              <span>Apache-2.0 · Self-hosted</span>
              <strong>Codex</strong>
              <i aria-hidden="true" />
              <strong>Claude Code</strong>
              <i aria-hidden="true" />
              <strong>OpenAI + Anthropic</strong>
            </div>
          </div>

          <div className="decision-wrap reveal reveal-three">
            <div className="decision-glow" aria-hidden="true" />
            <article className="decision-card" aria-label="Illustrative Hormuz policy decision">
              <header className="decision-header">
                <div>
                  <span className="card-kicker">Illustrative policy decision</span>
                  <h2>Request hmx_4f81</h2>
                </div>
                <span className="live-pill">
                  <span aria-hidden="true" /> Example
                </span>
              </header>

              <div className="request-meta">
                <div>
                  <span>Actor</span>
                  <strong>alice@engineering</strong>
                </div>
                <div>
                  <span>Client</span>
                  <strong>codex</strong>
                </div>
                <div>
                  <span>Model</span>
                  <strong>gpt-5.5</strong>
                </div>
              </div>

              <div className="decision-route">
                {decisionSteps.map((step, index) => (
                  <div className="route-step" key={step.label}>
                    <div className={`route-icon route-${step.tone}`}>
                      <span>{index + 1}</span>
                    </div>
                    <div>
                      <small>{step.label}</small>
                      <strong>{step.value}</strong>
                    </div>
                    {index < decisionSteps.length - 1 && (
                      <span className="route-line" aria-hidden="true" />
                    )}
                  </div>
                ))}
              </div>

              <div className="decision-result">
                <div className="result-mark" aria-hidden="true">✓</div>
                <div>
                  <span>Decision</span>
                  <strong>Forward to provider</strong>
                </div>
                <span className="result-time">Illustration</span>
              </div>

              <footer className="decision-footer">
                <div>
                  <span>Prompt retained</span>
                  <strong>0 bytes</strong>
                </div>
                <div>
                  <span>Policy version</span>
                  <strong>hpv_v1_8ab…</strong>
                </div>
                <div>
                  <span>Evidence</span>
                  <strong>Recorded</strong>
                </div>
              </footer>
            </article>
          </div>
        </div>

        <div className="trust-rail" aria-label="Hormuz product principles">
          <span>APACHE-2.0 OPEN SOURCE</span>
          <i aria-hidden="true" />
          <span>BRING YOUR OWN KEYS</span>
          <i aria-hidden="true" />
          <span>CONTENT-FREE EVIDENCE</span>
          <i aria-hidden="true" />
          <span>FAIL-CLOSED CONTROLS</span>
        </div>
      </section>

      <section className="intro section" id="control-plane">
        <div className="section-heading">
          <p className="section-label">The control point</p>
          <h2>Your AI stack already has models. It needs a governed route.</h2>
        </div>
        <div className="intro-copy">
          <p className="lead">
            Teams adopt AI one client and one provider at a time. Access
            fragments. Provider keys spread. Policy becomes a document instead
            of an enforcement point.
          </p>
          <p>
            Hormuz turns the request path itself into the control plane—without
            asking employees to abandon the tools that make them productive.
          </p>
        </div>
      </section>

      <section className="route-section" aria-label="Hormuz request architecture">
        <div className="route-canvas">
          <div className="route-column route-sources">
            <p>Employee tools</p>
            <div className="source-card">
              <span className="source-monogram">C</span>
              <div><strong>Codex</strong><small>OpenAI protocol</small></div>
            </div>
            <div className="source-card">
              <span className="source-monogram source-monogram-alt">C</span>
              <div><strong>Claude Code</strong><small>Anthropic protocol</small></div>
            </div>
          </div>

          <div className="connector connector-left" aria-hidden="true">
            <span />
          </div>

          <div className="hormuz-node">
            <div className="node-halo" aria-hidden="true" />
            <span className="node-kicker">CONTROL PLANE</span>
            <div className="node-mark" aria-hidden="true">H</div>
            <h3>Hormuz</h3>
            <div className="node-pills">
              <span>Identity</span><span>Policy</span><span>Secrets</span><span>Budgets</span>
            </div>
          </div>

          <div className="connector connector-right" aria-hidden="true">
            <span />
          </div>

          <div className="route-column route-providers">
            <p>Model providers</p>
            <div className="provider-card">
              <span className="provider-dot" aria-hidden="true" />
              <div><strong>OpenAI</strong><small>Company account</small></div>
            </div>
            <div className="provider-card">
              <span className="provider-dot provider-dot-alt" aria-hidden="true" />
              <div><strong>Anthropic</strong><small>Company account</small></div>
            </div>
          </div>
        </div>

        <div className="evidence-rail">
          <span className="evidence-label">Content-free evidence</span>
          <div><span>Identity</span><strong>Attributed</strong></div>
          <div><span>Policy</span><strong>Version-pinned</strong></div>
          <div><span>Usage</span><strong>Measured</strong></div>
          <div><span>Cost</span><strong>Rate-card estimate</strong></div>
          <div><span>Payload</span><strong>Not retained</strong></div>
        </div>
      </section>

      <section className="capabilities section" id="capabilities">
        <div className="section-heading narrow">
          <p className="section-label">Built into the path</p>
          <h2>Control that travels with every request.</h2>
          <p>
            A single enforcement boundary for who can use AI, what they can
            access, what may leave, and how the organization proves it later.
          </p>
        </div>

        <div className="capability-grid">
          {capabilities.map((item) => (
            <article className="capability-card" key={item.index}>
              <div className="capability-top">
                <span className="capability-index">{item.index}</span>
                <span className="capability-tag">{item.tag}</span>
              </div>
              <h3>{item.title}</h3>
              <p>{item.copy}</p>
              <span className="card-arrow" aria-hidden="true">↗</span>
            </article>
          ))}
        </div>
      </section>

      <section className="boundary-section" id="boundary">
        <div className="boundary-inner">
          <div className="boundary-copy">
            <p className="section-label light">A deliberate boundary</p>
            <h2>Hormuz governs AI traffic. It does not pretend to be everything else.</h2>
            <p>
              The product owns the runtime gateway and its evidence. Document
              ingestion, knowledge quality, memory lifecycle, and accounting
              remain separate systems with separate responsibilities.
            </p>
            <blockquote>
              Narrow enough to trust.<br />Strong enough to enforce.
            </blockquote>
          </div>

          <div className="boundary-table">
            <div className="boundary-table-head">
              <span>Hormuz owns</span>
              <span>The enforced question</span>
            </div>
            {boundaryRows.map(([title, description]) => (
              <div className="boundary-row" key={title}>
                <strong>{title}</strong>
                <span>{description}</span>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="honesty section">
        <div className="honesty-card">
          <div className="honesty-status">
            <span className="status-ring" aria-hidden="true"><i /></span>
            <div><span>Source release</span><strong>v1.0.0 · stable contracts</strong></div>
          </div>
          <div className="honesty-copy">
            <p className="section-label">Honest by construction</p>
            <h2>Proof is not production readiness.</h2>
            <p>
              Hormuz has executable evidence for its gateway control path.
              Reference deployment evidence is not customer qualification.
              Your TLS, custody, recovery, availability, and security review
              must be validated for your environment. Traffic that bypasses
              Hormuz is outside its coverage.
            </p>
            <a
              href="https://github.com/Xpounder-com/hormuz"
              target="_blank"
              rel="noreferrer"
            >
              Inspect the public evidence <span aria-hidden="true">↗</span>
            </a>
          </div>
        </div>
      </section>

      <section className="review-section" id="review">
        <div className="review-grid" aria-hidden="true" />
        <div className="review-inner">
          <p className="section-label light">Open core. Supported evaluation.</p>
          <h2>Try it yourself. Evaluate it together.</h2>
          <p>
            The gateway and its controls are Apache-2.0. Need help fitting
            them to a company workflow? Explore a bounded, founder-led pilot
            around the same open-source product.
          </p>
          <div className="review-actions">
            <a
              className="button button-light"
              href={sitePath('/enterprise/')}
            >
              Compare OSS & enterprise support
              <span aria-hidden="true">→</span>
            </a>
            <span>Same open core · Scope agreed before work</span>
          </div>
        </div>
        <div className="review-number" aria-hidden="true">01</div>
      </section>

      </main>
      <SiteFooter />
    </>
  );
}
