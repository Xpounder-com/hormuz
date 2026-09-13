import { CampaignLink } from '../../components/CampaignLink';
import { CodeBlock } from '../../components/CodeBlock';
import { PageFrame, PageHero } from '../../components/PageFrame';
import { pageMetadata } from '../../../lib/metadata';
import { sitePath, sourcePath } from '../../../lib/site.mjs';

export const metadata = pageMetadata(
  'Connect Codex & Claude Code to an AI Gateway | Hormuz',
  'Connect Codex and Claude Code through a self-hosted Hormuz AI gateway. Generate client settings, use individual identities, and verify policy and usage.',
  '/guides/codex-claude-code-gateway/',
);

const codexConfig = `hormuz --config /etc/hormuz/hormuz.json client config codex \\
  --url https://hormuz.example.com`;
const claudeConfig = `hormuz --config /etc/hormuz/hormuz.json client config claude \\
  --url https://hormuz.example.com`;

export default function ClientGatewayGuide() {
  return <PageFrame active="guides">
    <PageHero eyebrow="Practical guide / Client setup" title={<>Connect Codex and Claude Code.<br /><span>Keep policy at the gateway.</span></>}>
      <p>A self-hosted Hormuz AI gateway sits between your coding client and its model provider. Each person authenticates to Hormuz; the gateway applies model access, budget, and secret controls before forwarding an allowed request.</p>
      <p><CampaignLink href={sitePath('/resources/#tutorials')}>All practical guides →</CampaignLink></p>
    </PageHero>
    <div className="docs-shell">
      <aside className="docs-sidebar" aria-label="Guide navigation"><div><strong>In this guide</strong><a href="#prepare">Prepare the gateway</a><a href="#codex">Connect Codex</a><a href="#claude">Connect Claude Code</a><a href="#verify">Verify the route</a><a href="#troubleshoot">Troubleshoot setup</a></div></aside>
      <article className="docs-content">
        <section id="prepare" className="docs-section">
          <p className="section-label">01 / Prepare the gateway</p><h2>Start with one configured workflow.</h2>
          <p>If you are evaluating Hormuz for the first time, run the <CampaignLink href={sitePath('/docs/#quickstart')}>provider-free demo</CampaignLink>. To connect a real coding client, a gateway operator must then configure provider accounts, model routes and rates, policies, and a unique identity for each person.</p>
          <p>The commands in this guide run in that configured operator environment. Replace <code>/etc/hormuz/hormuz.json</code> with its configuration path and <code>https://hormuz.example.com</code> with its actual HTTPS address. The example hostname is not a running service. Required configuration environment variables must already be supplied by your deployment’s credential tooling.</p>
          <p>Employees receive their own Hormuz identity credential. Company OpenAI and Anthropic provider keys remain on the gateway. For multiple people, use explicit individual bootstrap identities or the deployment’s configured OIDC flow; do not share one employee token.</p>
          <div className="docs-callout"><span aria-hidden="true">i</span><p><strong>Joining a team on Mac?</strong> The <CampaignLink href={sitePath('/docs/#mac')}>Mac companion setup</CampaignLink> provides a connection and client launcher. The direct CLI configuration below is another path and does not enable the Mac app’s local context optimization.</p></div>
        </section>
        <section id="codex" className="docs-section">
          <p className="section-label">02 / Connect Codex</p><h2>Generate the provider settings.</h2>
          <CodeBlock code={codexConfig} label="Generate Codex configuration" />
          <p>Review the generated block and merge it into the employee’s user-level <code>~/.codex/config.toml</code>, preserving unrelated settings. It selects Hormuz as the model provider, points <code>base_url</code> at the gateway’s <code>/v1</code> path, uses the Responses protocol, and reads the employee credential from <code>HORMUZ_TOKEN</code>.</p>
          <p>Supply that employee-specific credential through your organization’s secrets or endpoint-management tooling, then launch <code>codex</code> with an approved model. Provider configuration only in a project-local file is not sufficient for the supported setup. Use the generated native model ID so Codex can retain its bundled model metadata.</p>
          <p>Generating settings does not establish a connection or prove that requests are governed. Complete the verification below.</p>
        </section>
        <section id="claude" className="docs-section">
          <p className="section-label">03 / Connect Claude Code</p><h2>Point the client at the same gateway.</h2>
          <CodeBlock code={claudeConfig} label="Generate Claude Code configuration" />
          <p>Review the generated shell settings. They set <code>ANTHROPIC_BASE_URL</code> to the gateway origin and <code>ANTHROPIC_AUTH_TOKEN</code> from the person’s Hormuz credential. Apply them in the employee’s intended shell or managed launcher, then run the generated <code>claude --model</code> command with a model your policy permits.</p>
          <p>Keep the company’s <code>ANTHROPIC_API_KEY</code> on the gateway. For short-lived OIDC access tokens, follow the <a href={sourcePath('docs/CLIENTS.md') + '#generic-oidc-credentials'}>client authentication reference ↗</a> rather than treating a static token example as automatic refresh.</p>
          <p>The pinned client compatibility baseline and the current release’s live-provider evidence have different scopes. Hormuz 1.2.0 has OpenAI-only live-provider qualification; same-candidate Claude Code/Anthropic live qualification remains open. Check the <CampaignLink href={sitePath('/integrations/')}>integration support details</CampaignLink> for your client version and deployment.</p>
        </section>
        <section id="verify" className="docs-section">
          <p className="section-label">04 / Verify the route</p><h2>Check an allowed request and a denial.</h2>
          <ol className="numbered-list">
            <li>Use a non-production workflow and an approved model. Send one small synthetic request from the configured client.</li>
            <li>Ask the operator to confirm that the gateway recorded the expected person, team, client, model, and request outcome. A successful client answer alone does not prove the route.</li>
            <li>Exercise an agreed policy denial. Confirm it was recorded and that the request did not reach a provider; account for any configured fallback when choosing the test.</li>
            <li>Read the current UTC-month usage report from the configured gateway environment.</li>
          </ol>
          <CodeBlock label="Inspect the configured client’s usage" code={'hormuz --config /etc/hormuz/hormuz.json status --group-by person\nhormuz --config /etc/hormuz/hormuz.json status --group-by client\nhormuz --config /etc/hormuz/hormuz.json status --group-by model --team engineering'} />
          <p>Replace <code>engineering</code> with the configured team ID. Reports describe requests captured by Hormuz and cost estimates from configured rates. They do not measure all activity on a machine or establish employee productivity. <a href={sourcePath('docs/USAGE.md')}>Read the report definitions ↗</a></p>
        </section>
        <section id="troubleshoot" className="docs-section">
          <p className="section-label">05 / Troubleshoot setup</p><h2>Trace the missing step.</h2>
          <div className="table-scroll"><table className="comparison-table"><caption>Checks to make before widening a rollout</caption><thead><tr><th scope="col">Symptom</th><th scope="col">Next check</th></tr></thead><tbody>
            <tr><th scope="row">Settings generation fails</th><td>Check the configuration path and required identity environment variables in the operator environment. Do not copy server credentials onto employee machines.</td></tr>
            <tr><th scope="row">Client gets an authentication error</th><td>Check the individual Hormuz credential, token expiry/audience where applicable, gateway URL, and configured client allowlist.</td></tr>
            <tr><th scope="row">Model is denied or replaced</th><td>Inspect the model ID, protocol, inherited allowlists, and configured fallback.</td></tr>
            <tr><th scope="row">No usage appears</th><td>Check for a completed captured request, the selected UTC month and team, the correct usage store, and an accidental direct-provider route.</td></tr>
            <tr><th scope="row">Model discovery warning</th><td>Hormuz does not implement the optional gateway model catalog. Use an explicit supported native model ID; leave Claude’s optional gateway model discovery disabled.</td></tr>
          </tbody></table></div>
          <p>The <a href={sourcePath('docs/CLIENTS.md')}>maintained client reference ↗</a> contains the pinned versions and generated configuration examples. Use <a href={sourcePath('SUPPORT.md')}>support guidance ↗</a> for reproducible, sanitized error reports.</p>
        </section>
        <section className="docs-next"><div><span>Next guide</span><h2>Set the limits for your team.</h2></div><CampaignLink className="button button-primary" href={sitePath('/guides/team-ai-budgets/')}>Set team AI budgets →</CampaignLink></section>
      </article>
    </div>
  </PageFrame>;
}
