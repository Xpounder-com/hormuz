import { CompanionPreview } from '../components/CompanionPreview';
import { AiWorkDemo } from '../components/AiWorkDemo';
import { CompactionExample } from '../components/CompactionExample';
import { PolicyExample } from '../components/PolicyExample';
import { TechnicalDemo } from '../components/TechnicalDemo';
import { CampaignLink } from '../components/CampaignLink';
import { pageMetadata } from '../../lib/metadata';
import { REPOSITORY, sitePath, sourcePath } from '../../lib/site.mjs';
import { PageFrame, PageHero } from '../components/PageFrame';
import { DemoPlayer } from '../components/DemoPlayer';
import { CodeBlock } from '../components/CodeBlock';
import gateway from '../../public/demo/gateway.json';
import policy from '../../public/demo/policy.json';

export const metadata = pageMetadata('Try Hormuz — AI Work and control mechanics', 'Watch an actual authenticated AI Work recording under declared local fixture conditions. Inspect its receipt and optional technical control examples.', '/demo/');

export default function DemoPage() {
  return <PageFrame active="demo"><PageHero eyebrow="An actual AI Work recording" title={<>Keep your agents.<br /><span>See the work in action.</span></>}><p>Follow a work item through the authenticated gateway: check a connection, choose budgets, run ordinary API work, reach a limit, and inspect a signed workflow result and receipt. This recording uses local simulated providers. Your costs, model results, and deployment behavior depend on your setup.</p><div className="hero-actions"><a className="button button-primary" href="#work-demo">Watch AI Work ↓</a><CampaignLink className="button button-ghost" href={sitePath('/evidence/#work-proof')}>Inspect the execution receipt →</CampaignLink></div></PageHero>
    <AiWorkDemo />
    <TechnicalDemo>
    <section id="compaction" className="section"><div className="section-heading narrow"><p className="section-label">Eligible context compaction</p><h2>Inspect the original, selected, and restored forms.</h2><p>This browser example uses a declared synthetic tool result. Its byte measurement does not forecast your bill.</p></div><CompactionExample /></section>
    <section id="policy" className="section section-tinted"><div className="section-heading narrow"><p className="section-label">Policy mechanics</p><h2>Compare a control before applying it.</h2><p>These teaching controls use fictional rates and example state. They do not apply a policy to a gateway.</p></div><PolicyExample /></section>
    <CompanionPreview />
    <section id="recording" className="section"><div className="section-heading narrow"><p className="section-label">The gateway tour</p><h2>Allow. Reroute. Redact. Deny.</h2><p>The six PASS lines come from an actual execution. The recording is intentionally short; pause it, scrub it, or show the full output.</p></div><DemoPlayer recording={gateway} /><details className="disclosure"><summary>Read the complete gateway transcript</summary><pre tabIndex={0}><code>{gateway.transcript}</code></pre></details><p className="after-grid">Recorded {gateway.recorded_at.slice(0, 10)} with Python {gateway.python}, exit code {gateway.exit_code}, source revision <a href={`${REPOSITORY}/tree/${gateway.source_revision}`}>{gateway.source_revision.slice(0, 12)} ↗</a>. The product module was unchanged from that revision.</p><div className="resource-actions"><CampaignLink className="text-link" href={sitePath('/demo/gateway.cast')} download>Download asciicast</CampaignLink><CampaignLink className="text-link" href={sitePath('/demo/gateway.txt')} download>Plain-text transcript</CampaignLink><CampaignLink className="text-link" href={sitePath('/demo/gateway.json')} download>Recording & provenance</CampaignLink></div></section>
    <section className="section section-tinted" id="policy-recording"><div className="section-heading narrow"><p className="section-label">The administration tour</p><h2>Inspect a change before applying it.</h2><p>This separate zero-network run compares a baseline and stricter candidate, evaluates two scenarios, and makes no managed policy mutations.</p></div><CodeBlock code="hormuz policy demo" label="Reproduce the policy tour" /><details className="disclosure"><summary>Read the real policy-demo transcript</summary><pre tabIndex={0}><code>{policy.transcript}</code></pre></details><div className="resource-actions"><CampaignLink className="text-link" href={sitePath('/demo/policy.cast')} download>Download policy asciicast</CampaignLink><a className="text-link" href={sourcePath('marketing/tutorials/policy-and-evidence.md')}>Follow the policy walkthrough ↗</a></div></section>
    <section id="evidence" className="section prose-section"><p className="section-label">Synthetic evidence pack</p><h2>Five events. No conversation archive.</h2><p>Download four usage events and one secret-control event exported from a separate provider-free run. The gateway’s schema checks and forbidden-content checks passed before export. The records contain synthetic identities, model and policy outcomes, token/cost metadata, and bounded secret-control results—not prompt or response bodies.</p><div className="resource-actions"><CampaignLink className="button button-primary" href={sitePath('/demo/synthetic-evidence.jsonl')} download>Download synthetic JSONL ↓</CampaignLink><a className="text-link" href={sourcePath('docs/AUDIT.md')}>Read the audit contract ↗</a></div><p>This sample is not a customer case study, independent security review, invoice-reconciliation result, or performance test. These recorded executions do not count as independent people in the <a href={REPOSITORY + '/issues/110'}>external onboarding study</a>. That study has its own exact-archive protocol.</p></section>
    </TechnicalDemo>
  </PageFrame>;
}
