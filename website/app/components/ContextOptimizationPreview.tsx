import { ContextFold } from './ContextFold';
import { CampaignLink } from './CampaignLink';
import { sitePath } from '../../lib/site.mjs';

export function ContextOptimizationPreview() {
  return <section className="context-optimization-section" id="context-optimization" aria-labelledby="context-optimization-title">
    <div className="context-optimization-copy">
      <p className="landing-eyebrow">HORMUZ 1.2 · CLIENT-SIDE CONTEXT OPTIMIZATION</p>
      <h2 id="context-optimization-title">Keep the signal.<br /><em>Fold the repetition.</em></h2>
      <p className="context-optimization-deck">When you turn it on, Hormuz can replace eligible repetitive tool results with a smaller, lossless structure on your Mac before the request goes through your gateway. Everything else passes through unchanged.</p>
      <dl className="context-optimization-points">
        <div><dt>Your control</dt><dd>Off by default and scoped to one saved connection on one device.</dd></div>
        <div><dt>Local first</dt><dd>Selection, reconstruction checks, and token estimates stay on your device.</dd></div>
        <div><dt>Still governed</dt><dd>The request path keeps identity, policy, budget, accounting, and secret controls in place.</dd></div>
      </dl>
      <CampaignLink className="button context-optimization-cta" href={sitePath('/contact/?interest=review')}>Discuss context optimization <span aria-hidden="true">↗</span></CampaignLink>
      <p className="context-optimization-status">Available in Hormuz 1.2.0 and Off by default. Release evidence uses bounded synthetic tasks; it does not establish billed-cost savings, statistical model-quality equivalence, or an SLA.</p>
    </div>

    <ContextFold />
  </section>;
}
