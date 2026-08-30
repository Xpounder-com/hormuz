import { pageMetadata } from '../../lib/metadata';
import { sitePath, sourcePath } from '../../lib/site.mjs';
import { PageFrame, PageHero } from '../components/PageFrame';

export const metadata = pageMetadata('Open source & enterprise support — Hormuz', 'A useful Apache-2.0 core, with bounded evaluation and deployment support. Compare responsibilities and review a proposed 90-day pilot.', '/enterprise/');

const comparison = [
  ['Gateway, identity, policy, budgets, secret controls, evidence', 'Included in the Apache-2.0 core', 'The same open-source capabilities'],
  ['Installation and configuration', 'Self-service documentation and examples', 'Scoped integration and configuration assistance'],
  ['Support', 'Public, best-effort community channels', 'Named engagement contact; hours and response targets agreed in writing'],
  ['Evaluation evidence', 'Reproducible demos and reference tests', 'A workflow-specific evidence pack and gap review'],
  ['Infrastructure, provider accounts, credentials, retention', 'Operated by you', 'Still operated by you unless explicitly contracted otherwise'],
  ['Managed hosting, 24/7 SLA, certification', 'Not included', 'Not an established offering or implied commitment'],
];

export default function EnterprisePage() {
  return <PageFrame active="enterprise">
    <PageHero eyebrow="Enterprise evaluation & support" title={<>An open core.<br /><span>A supported path to evaluation.</span></>}>
      <p>Hormuz’s gateway controls stay open source. The initial enterprise offer is a scoped engagement to evaluate and integrate that same product—not a separate proprietary edition or a promise of production certification.</p>
      <div className="hero-actions"><a className="button button-primary" href={sitePath('/contact/?interest=pilot')}>Discuss a pilot →</a><a className="button button-ghost" href="#comparison">Compare the paths ↓</a></div>
    </PageHero>
    <section className="section" id="comparison">
      <div className="section-heading narrow"><p className="section-label">What is free. What is paid.</p><h2>The value is help making it work in your environment.</h2><p>Start independently, or agree a bounded engagement with the maintainer. Existing open-source controls are not being moved behind a paywall.</p></div>
      <div className="table-scroll"><table className="comparison-table"><caption>Open-source product versus proposed enterprise services</caption><thead><tr><th scope="col">Area</th><th scope="col">Open source</th><th scope="col">Enterprise engagement</th></tr></thead><tbody>{comparison.map(([area, oss, paid]) => <tr key={area}><th scope="row">{area}</th><td>{oss}</td><td>{paid}</td></tr>)}</tbody></table></div>
      <p className="after-grid">An engagement depends on fit and delivery capacity. Scope, price, support hours, response targets, data handling, and commercial terms must be agreed before work begins. This page is not a service agreement.</p>
    </section>
    <section className="enterprise-control-section"><div className="enterprise-control-inner"><div className="enterprise-control-heading"><p className="section-label light">A practical starting point</p><h2>One team. One client path. Named owners.</h2></div><div className="enterprise-control-list">
      <div><span>01</span><h3>Good initial fit</h3><p>A platform or engineering lead introducing Codex or Claude Code under company provider accounts, with a security reviewer and a named gateway operator.</p></div>
      <div><span>02</span><h3>Your prerequisites</h3><p>A non-production environment, authorized provider account, unique identities, approved test inputs, and someone empowered to decide policy and acceptance criteria.</p></div>
      <div><span>03</span><h3>Not a fit yet</h3><p>A turnkey hosted service, fleet-wide monitoring, employee productivity scoring, semantic DLP guarantees, or a certification-backed 24/7 service requirement.</p></div>
    </div></div></section>
    <section className="pilot-section section" id="pilot"><div className="pilot-heading"><p className="section-label">Proposed 90-day paid pilot</p><h2>Prove one workflow before widening the route.</h2><p>Begin with a short fit and scope discussion. A shorter evaluation can be agreed before a 90-day commitment; no work or calendar slot is confirmed by an inquiry.</p></div><div className="pilot-steps">
      <article><span>Days 1–15</span><h3>Map</h3><p>Name the client, identity, provider credential, policy, secret, budget, and evidence boundaries. Agree prerequisites and pass/fail criteria.</p><strong>Control map + acceptance plan</strong></article>
      <article><span>Days 16–45</span><h3>Prove</h3><p>Run the non-production workflow. Inspect allowed and denied requests, identity attribution, policy changes, and metadata exports.</p><strong>Evidence pack + issue log</strong></article>
      <article><span>Days 46–90</span><h3>Decide</h3><p>Review usability and operating effort. Test agreed recovery boundaries and list remaining production gates, owners, and costs.</p><strong>Go / no-go memo + handoff</strong></article>
    </div><div className="docs-callout"><span aria-hidden="true">✓</span><p><strong>Acceptance is agreed, not implied:</strong> the named client works; a forbidden request makes no upstream call; policies and identity are attributable; exports contain no prompt/response bodies; operational gaps have owners. Targets for latency, scale, availability, and cost accuracy require separate evidence.</p></div>
      <div className="resource-actions"><a className="button button-primary" href={sitePath('/downloads/hormuz-pilot-brief.pdf')}>Read the pilot brief ↓</a><a className="text-link" href={sourcePath('marketing/PILOT.md')}>Editable scope & responsibilities ↗</a></div>
    </section>
    <section className="enterprise-truth section"><div><span className="status-ring" aria-hidden="true"><i /></span><strong>v1.0.0 source contracts</strong></div><h2>Engineering evidence is not enterprise certification.</h2><p>Reference checks do not establish your TLS, credential custody, retention, recovery, high availability, compliance, or independent security review. No customer endorsements, certifications, invoice reconciliation, or production SLA are claimed.</p><a href={sitePath('/security/')}>Review the security boundary →</a></section>
    <section className="page-cta"><div><p className="section-label light">Founder-led · Mehrdad Zaker</p><h2>Bring one workflow and its constraints.</h2><p>The next step is a fit discussion, not a checkout or a deployment commitment.</p></div><a className="button button-light" href={sitePath('/contact/?interest=pilot')}>Prepare an inquiry →</a></section>
  </PageFrame>;
}
