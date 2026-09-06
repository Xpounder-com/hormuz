'use client';

import { useState } from 'react';
import { sitePath } from '../../lib/site.mjs';

const examples = [
  { label: 'Request controls', eyebrow: '01 / GOVERNED REQUEST', title: 'A decision before the provider.', copy: 'Follow an allowed request, a model fallback, a redacted secret, and a denied request through the recorded provider-free demo.', file: 'gateway.txt', lines: ['PASS allowed request reached the loopback provider simulator', 'PASS unapproved model was rerouted and output-capped', 'PASS detected secret was redacted before provider egress', 'PASS denied request made no provider call'], metric: '0', metricLabel: 'external provider calls', tags: ['Allow', 'Reroute + cap', 'Redact', 'Deny'] },
  { label: 'Policy changes', eyebrow: '02 / BEFORE YOU APPLY', title: 'See what a tighter policy changes.', copy: 'Compare a baseline and a strict candidate locally. The recording evaluates two scenarios without changing the active policy.', file: 'policy.txt', lines: ['PASS semantic comparison found 3 policy changes', 'PASS created 2 explicit policy scenarios', '8000-token request uncapped → capped at 4000', 'demo-deep allowed → denied'], metric: '0', metricLabel: 'active policy mutations', tags: ['Compare', 'Evaluate', 'Review', 'Then decide'] },
  { label: 'Audit evidence', eyebrow: '03 / INSPECT THE RECORD', title: 'Keep the decision. Leave out the conversation.', copy: 'The synthetic evidence export records identity, model routing, policy outcomes, and estimated usage. Inspect the original JSONL alongside the recording.', file: 'synthetic-evidence.jsonl', lines: ['actor_id          demo-engineer', 'client            codex', 'requested_model   demo-deep', 'policy_action     fallback+capped'], metric: '4 + 1', metricLabel: 'usage + security events', tags: ['Identity', 'Policy', 'Usage', 'No prompt bodies'] },
];

export function EvidenceShowcase() {
  const [selected, setSelected] = useState(0);
  const example = examples[selected];
  return <section className="landing-section proof-section" id="evidence" aria-labelledby="proof-title">
    <div className="landing-heading"><p className="landing-eyebrow">OPEN THE EVIDENCE</p><h2 id="proof-title">Less taking our word for it.<br /><em>More seeing for yourself.</em></h2><p>Three views into the same control boundary. Drawn from our recorded, synthetic demos.</p></div>
    <div className="proof-selectors" role="group" aria-label="Choose an evidence example">{examples.map((item, i) => <button key={item.label} type="button" aria-pressed={selected === i} aria-controls="proof-panel" onClick={() => setSelected(i)}><span aria-hidden="true">0{i + 1}</span>{item.label}</button>)}</div>
    <div className="proof-panel" id="proof-panel">
      <div className="proof-art"><div className="proof-orbit" aria-hidden="true" /><div className="proof-terminal"><div className="proof-window"><span aria-hidden="true">● ● ●</span><span>hormuz / {example.file}</span><span>↗</span></div><p className="proof-source-label">RECORDED DEMO · SELECTED {selected === 1 ? 'RESULTS' : 'EXCERPT'}</p><pre>{example.lines.join('\n')}</pre><div className="proof-tags">{example.tags.map(tag => <span key={tag}>{tag}</span>)}</div></div><div className="proof-receipt"><span>DEMO EVIDENCE</span><strong>{example.metric}</strong><p>{example.metricLabel}</p><small>Recorded locally · Synthetic inputs</small></div></div>
      <div className="proof-copy" aria-live="polite" aria-atomic="true"><p className="landing-eyebrow">{example.eyebrow}</p><h3>{example.title}</h3><p>{example.copy}</p><a className="proof-source" href={sitePath(`/demo/${example.file}`)}>Inspect the source <span aria-hidden="true">↗</span></a><a className="button landing-primary" href={sitePath('/demo/')}>Watch the recorded demos <span aria-hidden="true">→</span></a></div>
    </div><p className="proof-footnote">These are synthetic demonstrations, not customer results or production qualification.</p>
  </section>;
}
