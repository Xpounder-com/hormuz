import { CampaignLink } from '../components/CampaignLink';
import { pageMetadata } from '../../lib/metadata';
import { sitePath, sourcePath, SOURCE_VERSION } from '../../lib/site.mjs';
import { PageFrame, PageHero } from '../components/PageFrame';
import { CodeBlock } from '../components/CodeBlock';
import { SetupExample } from '../components/SetupExample';

export const metadata = pageMetadata('Coding-client and API integrations — Hormuz', 'Check supported coding-client paths and the AI Work API attachment contract, with server-side provider keys and version-specific compatibility guidance.', '/integrations/');

export default function IntegrationsPage() {
  return <PageFrame active="integrations">
    <PageHero eyebrow="Client integrations" title={<>Keep the tools.<br /><span>Change the control point.</span></>}>
      <p>Connect documented coding-client paths or an API agent that supports a configurable endpoint and work header. Keep company provider keys on the gateway.</p>
      <div className="hero-actions"><a className="button button-primary" href="#my-stack">Check my stack ↓</a><CampaignLink className="button button-ghost" href={sitePath('/docs/')}>Install free →</CampaignLink></div>
    </PageHero>
    <section className="section" id="my-stack"><div className="experience-shell experience-panel"><SetupExample /></div></section>
    <section className="section" id="clients">
      <div className="section-heading narrow"><p className="section-label">After the provider-free demo</p><h2>One identity. One client. One verified route.</h2><p>First configure a gateway, unique employee credentials, and server-side provider keys using the repository guide. Outside local development, use TLS and an organization-controlled hostname. The example hostname below is not a running service.</p></div>
      <div className="integration-card-grid">
        <article className="integration-card"><div className="integration-card-head"><span>C</span><div><h3>Codex</h3><p>OpenAI Responses</p></div><i>0.147.0 baseline</i></div>
          <p>Generate a custom-provider configuration and add it to the employee’s user-level Codex configuration. Project-local provider settings alone are not sufficient.</p>
          <CodeBlock code={`hormuz --config /etc/hormuz/hormuz.json client config codex \\
  --url https://hormuz.example.com`} label="Codex configuration" />
          <p>Use a native model ID allowed by your policy. A custom model-catalog refresh warning can occur; Hormuz does not implement Codex’s private catalog schema.</p>
        </article>
        <article className="integration-card integration-amber"><div className="integration-card-head"><span>C</span><div><h3>Claude Code</h3><p>Anthropic Messages</p></div><i>2.1.233 baseline</i></div>
          <p>Generate the gateway environment configuration. Provision a unique Hormuz identity through your organization’s secrets tooling; do not copy the company Anthropic key to the client.</p>
          <CodeBlock code={`hormuz --config /etc/hormuz/hormuz.json client config claude \\
  --url https://hormuz.example.com`} label="Claude Code configuration" />
          <p>Leave optional gateway model discovery disabled and select an explicit supported model. Hormuz does not implement that optional discovery endpoint.</p>
        </article>
      </div>
      <p className="after-grid">Use the documented client versions as your starting point; newer versions need testing. Hormuz {SOURCE_VERSION} passes local release checks without external provider calls. The available live-provider tests cover OpenAI on v1.2.0; live Claude Code and Anthropic use has not been verified on the newer source version. See <a href={sourcePath('SUPPORT.md')}>Support</a> and <a href={sourcePath('docs/LIVE_CLIENT_CONFORMANCE.md')}>live-client conformance</a> for tested versions and setup details.</p>
    </section>
    <section className="section prose-section" id="ai-work"><h2>Attach API agent requests to a piece of work.</h2><p>The AI Work source path adds a durable work ID and scoped budgets to supported gateway requests. The Python client attaches <code>X-Hormuz-Work-Id</code> for non-streaming OpenAI Responses, OpenAI-compatible Chat Completions, and Anthropic Messages. Streaming integrations supply that header through their existing SDK and keep the usual gateway authentication.</p><p>AI Work budgets currently cover text inputs with validated configured upper cost bounds. Image, audio, document, and provider-resolved inputs are rejected when that bound is unavailable.</p><p>The operator enables AI Work, authorizes the application, and qualifies the exact model, protocol, tool, and streaming behavior. New workspace sign-up does not enable inference access. An agent without endpoint or header configuration needs an explicit adapter.</p><div className="resource-actions"><a className="text-link" href={sourcePath('docs/AI_WORK_AGENT_INTEGRATION.md')}>Agent attachment, checks, and budget walkthrough ↗</a><CampaignLink className="text-link" href={sitePath('/work/')}>Open your configured AI Work gateway →</CampaignLink><CampaignLink className="text-link" href={sitePath('/evidence/')}>Inspect functional evidence →</CampaignLink></div></section>
    <section className="protocol-section" id="protocols"><div className="protocol-inner"><div><p className="section-label light">Provider protocols</p><h2>Model traffic, not every client action.</h2><p>Hormuz does not govern shell commands, MCP servers, browser requests, Git traffic, or requests that bypass it.</p></div><div className="protocol-table">
      <div className="protocol-head"><span>Surface</span><span>Current contract</span><span>Boundary</span></div>
      <div><strong>OpenAI</strong><span>Responses, streaming, compaction relay</span><i>HTTP / SSE</i></div>
      <div><strong>OpenAI-compatible</strong><span>Chat Completions in the AI Work source path</span><i>Test with your workflow</i></div>
      <div><strong>Anthropic</strong><span>Messages, token counts, streaming</span><i>HTTP / SSE</i></div>
      <div><strong>Identity</strong><span>Unique credentials, OIDC JWT, or opt-in Hormuz sessions</span><i>Browser login Off by default</i></div>
      <div><strong>Evidence</strong><span>Identity, policy, usage, estimated cost, secret outcomes</span><i>Metadata only</i></div>
    </div></div></section>
    <section className="section prose-section" id="verification"><p className="section-label">Verify before widening access</p><h2>Make the first integration reproducible.</h2><ol className="numbered-list"><li>Start with the local demo and a named, non-production client workflow.</li><li>Install the pinned client version and generate its configuration from the gateway.</li><li>Provision a unique employee token, an OIDC JWT from your identity tooling, or an approved browser-login session.</li><li>Check an allowed request, a policy denial, attribution, and estimated usage. Confirm the denial made no upstream call.</li><li>Record the versions and remaining operational gaps before expanding scope.</li></ol><p><a href={sourcePath('marketing/tutorials/client-integration.md')}>Read the full walkthrough and acceptance checklist ↗</a></p></section>
  </PageFrame>;
}
