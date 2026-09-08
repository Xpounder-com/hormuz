import { BrandMark } from './Brand';
import { CampaignLink } from './CampaignLink';
import { sitePath } from '../../lib/site.mjs';

export function ContextOptimizationPreview() {
  return <section className="context-optimization-section" id="context-optimization" aria-labelledby="context-optimization-title">
    <div className="context-optimization-copy">
      <p className="landing-eyebrow">HORMUZ 1.2 DESIGN PREVIEW · CLIENT-SIDE CONTEXT OPTIMIZATION</p>
      <h2 id="context-optimization-title">Keep the signal.<br /><em>Fold the repetition.</em></h2>
      <p className="context-optimization-deck">The design goal: when you turn it on, Hormuz would replace eligible repetitive tool results with a smaller, lossless structure on your Mac before the request goes through your gateway. Everything else would pass through unchanged.</p>
      <dl className="context-optimization-points">
        <div><dt>Your control</dt><dd>Designed to be off by default and scoped to one saved connection on one device.</dd></div>
        <div><dt>Local first</dt><dd>Planned selection, reconstruction checks, and token estimates stay on your device.</dd></div>
        <div><dt>Still governed</dt><dd>The proposed request path keeps identity, policy, budget, accounting, and secret controls in place.</dd></div>
      </dl>
      <CampaignLink className="button context-optimization-cta" href={sitePath('/contact/?interest=review')}>Discuss context optimization <span aria-hidden="true">↗</span></CampaignLink>
      <p className="context-optimization-status">Proposed for Hormuz 1.2. This is an unshipped design preview. Savings depend on eligible structure; current evidence is synthetic, not billed-cost or model-quality proof.</p>
    </div>

    <div className="context-optimization-stage" role="img" aria-label="Design preview: four identical worker retrying lines would become one local line-run structure containing the same value and a count of four. The proposed path is designed for exact reconstruction, normal Hormuz policy checks, and no content cache.">
      <header><span><BrandMark /> HORMUZ / CONTEXT</span><span>LOCAL DESIGN</span></header>
      <div className="context-optimization-board">
        <article className="context-result context-result-before">
          <span>TOOL RESULT · BEFORE</span>
          <code><i>worker: retrying</i><i>worker: retrying</i><i>worker: retrying</i><i>worker: retrying</i></code>
          <footer><strong>4</strong><small>repeated lines</small></footer>
        </article>
        <div className="context-transform"><span><BrandMark /></span><b>LOCAL</b><i /></div>
        <article className="context-result context-result-after">
          <span>STRUCTURAL-V1 · AFTER</span>
          <code><i>line_run</i><span><b>value</b> worker: retrying</span><span><b>count</b> 4</span></code>
          <footer><strong>1</strong><small>lossless block</small></footer>
        </article>
      </div>
      <footer className="context-optimization-proof"><span><i>✓</i> Exact reconstruction</span><span><i>✓</i> Policies still apply</span><b>NO CONTENT CACHE</b></footer>
    </div>
  </section>;
}
