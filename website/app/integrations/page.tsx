import { pageMetadata } from '../../lib/metadata';
import { sitePath, sourcePath } from '../../lib/site.mjs';
import { PageFrame, PageHero } from '../components/PageFrame';
import { CodeBlock } from '../components/CodeBlock';

export const metadata = pageMetadata('Codex & Claude Code integrations — Hormuz', 'Connect the supported AI coding-client paths through Hormuz, with provider keys on the gateway and explicit compatibility limits.', '/integrations/');

export default function IntegrationsPage() {
  return <PageFrame active="integrations">
    <PageHero eyebrow="Client integrations" title={<>Keep the tools.<br /><span>Change the control point.</span></>}>
      <p>Route Codex and Claude Code model requests through a self-hosted policy boundary. Keep company provider keys on the gateway, not on employee laptops.</p>
      <div className="hero-actions"><a className="button button-primary" href={sitePath('/docs/')}>Start with the local demo →</a><a className="button button-ghost" href={sourcePath('docs/CLIENTS.md')}>Full client guide ↗</a></div>
    </PageHero>
    <section className="section">
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
      <p className="after-grid">These are the pinned v1 client baselines, not a claim that every newer release is supported. See <a href={sourcePath('SUPPORT.md')}>Support</a> and <a href={sourcePath('docs/LIVE_CLIENT_CONFORMANCE.md')}>live-client conformance</a> for exact protocol, version, and provider evidence boundaries.</p>
    </section>
    <section className="protocol-section"><div className="protocol-inner"><div><p className="section-label light">Provider protocols</p><h2>Model traffic, not every client action.</h2><p>Hormuz does not govern shell commands, MCP servers, browser requests, Git traffic, or requests that bypass it.</p></div><div className="protocol-table">
      <div className="protocol-head"><span>Surface</span><span>Current contract</span><span>Boundary</span></div>
      <div><strong>OpenAI</strong><span>Responses, streaming, compaction relay</span><i>HTTP / SSE</i></div>
      <div><strong>Anthropic</strong><span>Messages, token counts, streaming</span><i>HTTP / SSE</i></div>
      <div><strong>Identity</strong><span>Unique bootstrap credentials or OIDC JWT</span><i>No session broker</i></div>
      <div><strong>Evidence</strong><span>Identity, policy, usage, estimated cost, secret outcomes</span><i>Metadata only</i></div>
    </div></div></section>
    <section className="section prose-section"><p className="section-label">Verify before widening access</p><h2>Make the first integration reproducible.</h2><ol className="numbered-list"><li>Start with the local demo and a named, non-production client workflow.</li><li>Install the pinned client version and generate its configuration from the gateway.</li><li>Provision a unique employee token or an OIDC JWT supplied by your identity tooling.</li><li>Check an allowed request, a policy denial, attribution, and estimated usage. Confirm the denial made no upstream call.</li><li>Record the versions and remaining operational gaps before expanding scope.</li></ol><p><a href={sourcePath('marketing/tutorials/client-integration.md')}>Read the full walkthrough and acceptance checklist ↗</a></p></section>
  </PageFrame>;
}
