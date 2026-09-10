import { CampaignLink } from '../components/CampaignLink';
import { pageMetadata } from '../../lib/metadata';
import { REPOSITORY, sitePath, sourcePath } from '../../lib/site.mjs';
import { PageFrame, PageHero } from '../components/PageFrame';
import { CodeBlock } from '../components/CodeBlock';

export const metadata = pageMetadata('Quickstart & downloads — Hormuz', 'Install the stable source release, run the real gateway without provider keys, and connect a supported AI client.', '/docs/');

const quickstart = `git clone --branch v1.2.0 --depth 1 https://github.com/Xpounder-com/hormuz.git
cd hormuz
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --editable .
hormuz demo`;
const references = [
  ['Identity', 'OIDC JWT verification', 'Issuer, audience, signatures, and explicit subject mapping. Your identity tooling supplies and refreshes tokens.', 'docs/OIDC.md'],
  ['Control', 'Policy administration', 'Templates, preview, immutable versions, activation, and rollback. Privileged administration is separate from inference access.', 'docs/POLICY_CONTROL.md'],
  ['Evidence', 'Usage reporting', 'Current UTC-month reports by person, team, model, client, and provider. Captured gateway requests and estimated costs only.', 'docs/USAGE.md'],
  ['Operations', 'Deployment contract', 'Readiness, graceful drain, deadlines, and deployment-owned controls. Reference evidence is not customer certification.', 'docs/OPERATIONS.md'],
];

export default function DocsPage() {
  return <PageFrame active="docs">
    <PageHero eyebrow="Documentation" title={<>Start locally.<br /><span>No provider key required.</span></>}>
      <p>See one allowed request, one reroute, one redaction, and one denial through the real HTTP gateway—with disposable loopback providers.</p>
      <div className="hero-actions"><a className="button button-primary" href="#quickstart">Run the quickstart ↓</a><CampaignLink className="button button-ghost" href={sitePath('/demo/')}>See the recording →</CampaignLink></div>
    </PageHero>
    <div className="docs-shell">
      <aside className="docs-sidebar" aria-label="Documentation navigation">
        <div><strong>Get started</strong><a href="#quickstart">Provider-free demo</a><a href="#downloads">Source & OCI versions</a><CampaignLink href={sitePath('/integrations/')}>Connect a client</CampaignLink><a href="#policy-tour">Try a policy change</a></div>
        <div><strong>Go deeper</strong>{references.map(([tag, title, , path]) => <a key={tag} href={sourcePath(path)}>{title} ↗</a>)}<a href={sourcePath('SUPPORT.md')}>Support matrix ↗</a></div>
      </aside>
      <article className="docs-content">
        <section id="quickstart" className="docs-section">
          <p className="section-label">01 / Install and run</p><h2>The complete first-run path.</h2>
          <p className="docs-lead">Use Git and Python 3.11+ on macOS or Linux. Installation downloads Python dependencies; the demo itself contacts only local loopback simulators. Do not expose the demo to the internet.</p>
          <CodeBlock code={quickstart} label="Stable source quickstart" />
          <p>On Windows, activate the virtual environment with <code>.venv\Scripts\Activate.ps1</code> in PowerShell; consult the <a href={sourcePath('SUPPORT.md')}>support matrix</a> before choosing a deployment platform.</p>
          <div className="code-window"><div className="code-window-bar"><span>Expected successful result</span><span>Synthetic requests</span></div><pre tabIndex={0}><code>{`PASS allowed request reached the loopback provider simulator
PASS unapproved model was rerouted and output-capped
PASS detected secret was redacted before provider egress
PASS denied request made no provider call
PASS content-free evidence validated: 4 usage events, 1 security event
PASS external provider calls: 0 (3 loopback simulator calls)`}</code></pre></div>
          <p>Temporary gateway state is removed on exit. Your checkout and virtual environment remain; run <code>deactivate</code> when finished. This is a product tour, not a performance benchmark, live-provider test, or independent-user study.</p>
          <div className="docs-callout"><span aria-hidden="true">i</span><p><strong>Something failed?</strong> Confirm Python 3.11+, the active virtual environment, and permission to bind local ports. Share the command, version, and sanitized error via <a href={sourcePath('SUPPORT.md')}>Support</a>—never provider tokens, prompts, or customer configuration.</p></div>
        </section>
        <section id="downloads" className="docs-section">
          <p className="section-label">02 / Select a distribution</p><h2>Three distributions. Explicit evidence.</h2>
          <div className="table-scroll"><table className="comparison-table"><caption>Current published distribution boundaries</caption><thead><tr><th scope="col">Distribution</th><th scope="col">Version</th><th scope="col">Use and boundary</th></tr></thead><tbody>
            <tr><th scope="row">Source</th><td>v1.2.0</td><td>Stable v1 CLI, policy, and evidence contracts with default-Off local context optimization. The quickstart checks out this tag. <a href={REPOSITORY + '/releases/tag/v1.2.0'}>Release & evidence ↗</a></td></tr>
            <tr><th scope="row">Apple Silicon companion</th><td>v1.2.0</td><td>Developer ID signed and notarized for Apple Silicon on macOS 14 or later. Intel Macs are unsupported. <a href={REPOSITORY + '/releases/download/v1.2.0/Hormuz-1.2.0-notarized.zip'}>Download notarized ZIP ↓</a></td></tr>
            <tr><th scope="row">Signed OCI reference</th><td>v1.2.0</td><td>Public <code>linux/amd64</code> image at <code>ghcr.io/xpounder-com/hormuz:v1.2.0</code>. Pin the immutable digest from the release evidence. <a href={sourcePath('docs/OCI.md')}>Image and signature verification ↗</a></td></tr>
          </tbody></table></div>
          <p>The v1.2.0 release attaches the exact Mac archive, Python wheel and source archive, checksum manifest, and content-free qualification evidence. Use the tagged source and attached checksums when reproducing a version-specific result.</p>
          <div className="resource-actions"><a href={REPOSITORY + '/releases/download/v1.2.0/Hormuz-1.2.0-notarized.zip'}>Download signed Mac app ↓</a><a href={REPOSITORY + '/releases/download/v1.2.0/SHA256SUMS.txt'}>Download SHA256SUMS.txt ↓</a></div>
          <details className="disclosure"><summary>Notarized Mac archive SHA-256</summary><code className="hash">0a18536765245a3b2510644303a0a4ce253af2ab4d08168e129feb9d7e2501f0</code></details>
          <p>Documentation links follow the public main branch and can evolve beyond a release. Use the tagged source when reproducing a version-specific result. No PyPI installation or package ownership is implied by this guide.</p>
        </section>
        <section id="policy-tour" className="docs-section">
          <p className="section-label">03 / Change a policy</p><h2>See the administrative workflow offline.</h2>
          <CodeBlock code="hormuz policy demo" label="Offline policy tour" />
          <p>This separate, zero-network tour exercises policy templates, checks, simulation, and evidence. It cleans up by default. It does not prove a live PostgreSQL administrative deployment.</p>
          <p>Then follow the <a href={sourcePath('marketing/tutorials/policy-and-evidence.md')}>policy-and-evidence walkthrough</a> to inspect a proposed change and the <a href={sourcePath('docs/POLICY_CONTROL.md')}>governed policy guide</a> before staging or activating policies in a managed environment.</p>
        </section>
        <section className="docs-section"><p className="section-label">Reference library</p><h2>Go deeper by control surface.</h2><div className="doc-reference-grid">{references.map(([tag, title, copy, path]) => <a href={sourcePath(path)} key={tag}><span>{tag}</span><h3>{title}</h3><p>{copy}</p><strong>Open reference ↗</strong></a>)}</div></section>
        <section className="docs-next"><div><span>Next</span><h2>Connect one supported client.</h2></div><CampaignLink className="button button-primary" href={sitePath('/integrations/')}>Client walkthrough →</CampaignLink></section>
      </article>
    </div>
  </PageFrame>;
}
