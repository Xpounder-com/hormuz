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
    <PageHero eyebrow="Documentation" title={<>Install free.<br /><span>Make the first request count.</span></>}>
      <p>Join an existing gateway with the Mac companion, or install the gateway for your organization. The provider-free demo is your first check; the client walkthrough takes you to real governed usage.</p>
      <div className="hero-actions"><a className="button button-primary" href="#mac">Install for Mac ↓</a><CampaignLink className="button button-ghost" href="#quickstart">Install the gateway →</CampaignLink></div>
    </PageHero>
    <div className="docs-shell">
      <aside className="docs-sidebar" aria-label="Documentation navigation">
        <div><strong>Get started</strong><a href="#mac">Mac companion</a><a href="#first-request">First governed request</a><a href="#quickstart">Provider-free demo</a><a href="#downloads">Source & OCI versions</a><CampaignLink href={sitePath('/integrations/')}>Connect a client</CampaignLink><a href="#policy-tour">Try a policy change</a></div>
        <div><strong>Go deeper</strong>{references.map(([tag, title, , path]) => <a key={tag} href={sourcePath(path)}>{title} ↗</a>)}<a href={sourcePath('SUPPORT.md')}>Support matrix ↗</a></div>
      </aside>
      <article className="docs-content">
        <section id="mac" className="docs-section"><p className="section-label">Join your existing team gateway</p><h2>Your connection. Your usage. On your Mac.</h2><p className="docs-lead">Hormuz 1.2.0 supports Apple Silicon on macOS 14 or later. The signed, notarized archive includes its context helper and tokenizer resources; no separate Python installation is needed.</p><div className="resource-actions"><a className="button button-primary" href={REPOSITORY + '/releases/download/v1.2.0/Hormuz-1.2.0-notarized.zip'}>Download Hormuz 1.2.0 for Mac ↓</a><a href={REPOSITORY + '/releases/tag/v1.2.0'}>Release & verification evidence ↗</a></div><ol className="numbered-list"><li>Extract the archive and move Hormuz.app to Applications.</li><li>Get your gateway URL, organization, and unique identity or invitation from your administrator. The app does not provision a hosted gateway.</li><li>Connect and sign in using the gateway’s configured identity flow. Review and save the supported client launcher.</li><li>Launch your coding client through Hormuz, choose an approved model, and make one small authorized request. Inspect your usage and connection state.</li></ol><p>Context optimization is Off by default under <strong>Client → Context optimization</strong>. It changes the next request on this device and connection. <CampaignLink href={sitePath('/demo/#compaction')}>Try exactly what it does →</CampaignLink></p><details className="disclosure"><summary>Verify the Mac archive</summary><p>Expected SHA-256:</p><code className="hash">0a18536765245a3b2510644303a0a4ce253af2ab4d08168e129feb9d7e2501f0</code><p>Compare with <a href={REPOSITORY + '/releases/download/v1.2.0/SHA256SUMS.txt'}>the published checksums</a>. Keep normal Gatekeeper checks enabled. Intel Macs are unsupported. Upgrades use versioned archives; there is no automatic updater.</p></details><div className="docs-callout"><span aria-hidden="true">i</span><p><strong>No team gateway yet?</strong> Follow the source quickstart below or <CampaignLink href={sitePath('/enterprise/')}>choose help setting it up</CampaignLink>. A download alone does not create a provider account or a team connection.</p></div></section>
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
          <p className="section-label">02 / Select a distribution</p><h2>Choose a verified 1.2.0 distribution.</h2>
          <div className="table-scroll"><table className="comparison-table"><caption>Current published distribution boundaries</caption><thead><tr><th scope="col">Distribution</th><th scope="col">Version</th><th scope="col">Use and boundary</th></tr></thead><tbody>
            <tr><th scope="row">Source</th><td>v1.2.0</td><td>CLI, gateway, and opt-in local context optimization. <a href={REPOSITORY + '/releases/download/v1.2.0/hormuz-1.2.0.tar.gz'}>Source archive ↗</a> · <a href={REPOSITORY + '/releases/tag/v1.2.0'}>Release evidence ↗</a></td></tr>
            <tr><th scope="row">Apple Silicon companion</th><td>v1.2.0</td><td>Developer ID signed and notarized for Apple Silicon, macOS 14+. <a href="#mac">Install and connect →</a></td></tr>
            <tr><th scope="row">Signed OCI reference</th><td>v1.2.0</td><td>Linux AMD64 image at ghcr.io/xpounder-com/hormuz:v1.2.0. Pin the digest in <a href={REPOSITORY + '/releases/download/v1.2.0/oci-release-summary.json'}>release evidence ↗</a> and follow <a href={sourcePath('docs/OCI.md')}>signature verification ↗</a>.</td></tr>
          </tbody></table></div>
          <p>The 1.2.0 release has OpenAI-only live-provider qualification. Claude Code/Anthropic retains its protocol contract; same-candidate live qualification remains open. Use the checksums and exact release evidence for your chosen artifact.</p>
          <p><a href={REPOSITORY + '/releases/download/v1.2.0/SHA256SUMS.txt'}>Download release checksums ↓</a></p>
          <p>Documentation links follow the public main branch and can evolve beyond a release. Use the tagged source when reproducing a version-specific result. No PyPI installation or package ownership is implied by this guide.</p>
        </section>
        <section id="first-request" className="docs-section"><p className="section-label">From a demo to your workflow</p><h2>Connect one client. See its first usage.</h2><ol className="numbered-list"><li>Name a gateway operator and start with a non-production client workflow. Configure server-side provider accounts, model aliases/rates, unique identities, and policy using the <a href={sourcePath('README.md') + '#configure-providers-and-clients'}>gateway configuration guide</a>.</li><li>Generate the client settings for your organization-controlled gateway URL. Keep company provider credentials on the gateway.</li><li>Use the generated launcher or configuration to run one small authorized request with an approved model. Test an agreed policy denial and confirm that it made no provider call.</li><li>Run the team/model report below. Confirm the attributed identity, current UTC month, token categories, estimated cost, and request outcome.</li></ol><CodeBlock label="Generate Codex settings · example hostname, not a running service" code={'hormuz --config hormuz.json client config codex --url https://hormuz.example.com'} /><CodeBlock label="Inspect governed usage" code={'hormuz --config hormuz.json status --group-by team\nhormuz --config hormuz.json status --group-by model --team engineering'} /><p>Use your configured team ID instead of engineering. Empty reports can mean no completed captured requests in the current month, a mismatched identity/team, or traffic taking a different route. <CampaignLink href={sitePath('/integrations/')}>Client-specific setup, limits, and verification →</CampaignLink></p></section>
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
