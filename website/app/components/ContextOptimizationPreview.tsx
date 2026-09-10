import { ContextFold } from './ContextFold';
import { CampaignLink } from './CampaignLink';
import { sitePath } from '../../lib/site.mjs';

export function ContextOptimizationPreview() {
  return <section className="context-optimization-section" id="context-optimization" aria-labelledby="context-optimization-title">
    <div className="context-optimization-copy">
      <p className="landing-eyebrow">HORMUZ 1.2 · CLIENT-SIDE CONTEXT OPTIMIZATION</p>
      <h2 id="context-optimization-title">Keep the signal.<br /><em>Fold the repetition.</em></h2>
      <p className="context-optimization-deck">Turn it on for a saved connection. Hormuz can replace eligible repetitive tool results with a smaller, lossless structure on your Mac before the request goes through your gateway. Inspect a complete 127-path example and restore every line.</p>
      <dl className="context-optimization-points">
        <div><dt>Your control</dt><dd>Off by default. One switch for one saved connection on one device.</dd></div>
        <div><dt>Local first</dt><dd>Selection, reconstruction checks, and token estimates stay on your device.</dd></div>
        <div><dt>Still governed</dt><dd>Identity, policy, budget, accounting, and secret controls still apply.</dd></div>
      </dl>
      <CampaignLink className="button context-optimization-cta" href={sitePath('/demo/#compaction')}>Try the before & after <span aria-hidden="true">↗</span></CampaignLink>
      <p className="context-optimization-status">Available in 1.2.0. Automatic selection is narrow; unsupported or non-beneficial results pass through unchanged. Savings depend on the workload. Release live-provider qualification is OpenAI-only.</p>
    </div>

    <ContextFold />
  </section>;
}
