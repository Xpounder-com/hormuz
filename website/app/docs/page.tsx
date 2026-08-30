import { pageMetadata } from '../../lib/metadata';
import { REPOSITORY, sitePath, sourcePath } from '../../lib/site.mjs';
import { PageFrame, PageHero } from '../components/PageFrame';
import { CodeBlock } from '../components/CodeBlock';

export const metadata = pageMetadata('Quickstart & downloads — Hormuz', 'Install the stable source release, run the real gateway without provider keys, and connect a supported AI client.', '/docs/');

const quickstart = `git clone --branch v1.0.0 --depth 1 https://github.com/Xpounder-com/hormuz.git
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
      <div className="hero-actions"><a className="button button-primary" href="#quickstart">Run the quickstart ↓</a><a className="button button-ghost" href={sitePath('/demo/')}>See the recording →</a></div>
    </PageHero>
    <div className="docs-shell">
      <aside className="docs-sidebar" aria-label="Documentation navigation">
        <div><strong>Get started</strong><a href="#quickstart">Provider-free demo</a><a href="#downloads">Source & OCI versions</a><a href={sitePath('/integrations/')}>Connect a client</a><a href="#policy-tour">Try a policy change</a></div>
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
          <p className="section-label">02 / Select a distribution</p><h2>Two version streams. Explicit evidence.</h2>
          <div className="table-scroll"><table className="comparison-table"><caption>Current published distribution boundaries</caption><thead><tr><th scope="col">Distribution</th><th scope="col">Version</th><th scope="col">Use and boundary</th></tr></thead><tbody>
            <tr><th scope="row">Source</th><td>v1.0.0</td><td>Stable CLI, policy, and evidence contracts. The quickstart checks out this tag. <a href={REPOSITORY + '/releases/tag/v1.0.0'}>Release & canonical archive ↗</a></td></tr>
            <tr><th scope="row">Signed OCI reference</th><td>v0.1.3</td><td>Separately pinned linux/amd64 reference image. Do not relabel it v1.0.0. <a href={sourcePath('docs/OCI.md')}>Image and signature verification ↗</a></td></tr>
          </tbody></table></div>
          <p>The immutable final source release points to its original custody archive; it does not duplicate or rebuild those bytes. For exact-artifact evaluation, use the archive and verification instructions linked from the release, not a fresh source build.</p>
          <p><a href={REPOSITORY + '/releases/download/candidate-v1.0.0-2c3b16c1742ee76032a33f3714492a8d8515c5291d4d57520441882cd8bc5b5a/hormuz-1.0.0.tar.gz'}>Download canonical hormuz-1.0.0.tar.gz ↗</a></p>
          <details className="disclosure"><summary>Canonical archive SHA-256</summary><code className="hash">2c3b16c1742ee76032a33f3714492a8d8515c5291d4d57520441882cd8bc5b5a</code></details>
          <p>Documentation links follow the public main branch and can evolve beyond a release. Use the tagged source when reproducing a version-specific result. No PyPI installation or package ownership is implied by this guide.</p>
        </section>
        <section id="policy-tour" className="docs-section">
          <p className="section-label">03 / Change a policy</p><h2>See the administrative workflow offline.</h2>
          <CodeBlock code="hormuz policy demo" label="Offline policy tour" />
          <p>This separate, zero-network tour exercises policy templates, checks, simulation, and evidence. It cleans up by default. It does not prove a live PostgreSQL administrative deployment.</p>
          <p>Then follow the <a href={sourcePath('marketing/tutorials/policy-and-evidence.md')}>policy-and-evidence walkthrough</a> to inspect a proposed change and the <a href={sourcePath('docs/POLICY_CONTROL.md')}>governed policy guide</a> before staging or activating policies in a managed environment.</p>
        </section>
        <section className="docs-section"><p className="section-label">Reference library</p><h2>Go deeper by control surface.</h2><div className="doc-reference-grid">{references.map(([tag, title, copy, path]) => <a href={sourcePath(path)} key={tag}><span>{tag}</span><h3>{title}</h3><p>{copy}</p><strong>Open reference ↗</strong></a>)}</div></section>
        <section className="docs-next"><div><span>Next</span><h2>Connect one supported client.</h2></div><a className="button button-primary" href={sitePath('/integrations/')}>Client walkthrough →</a></section>
      </article>
    </div>
  </PageFrame>;
}
