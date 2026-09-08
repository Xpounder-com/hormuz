import { BrandMark } from './Brand';
import { CampaignLink } from './CampaignLink';
import { sitePath } from '../../lib/site.mjs';

export function ContextOptimizationPreview() {
  return <section className="context-optimization-section" id="context-optimization" aria-labelledby="context-optimization-title">
    <div className="context-optimization-copy">
      <p className="landing-eyebrow">HORMUZ 1.2 PREVIEW · CLIENT-SIDE CONTEXT OPTIMIZATION</p>
      <h2 id="context-optimization-title">Keep the signal.<br /><em>Fold the repetition.</em></h2>
      <p className="context-optimization-deck">When you turn it on, Hormuz can replace eligible repetitive tool results with a smaller, lossless structure on your Mac before the request goes through your gateway. Everything else passes through unchanged.</p>
      <dl className="context-optimization-points">
        <div><dt>Your control</dt><dd>Off by default and scoped to one saved connection on one device.</dd></div>
        <div><dt>Local first</dt><dd>Selection, reconstruction checks, and token estimates stay on your device.</dd></div>
        <div><dt>Still governed</dt><dd>The selected request still passes through identity, policy, budget, accounting, and secret controls.</dd></div>
      </dl>
      <CampaignLink className="button context-optimization-cta" href={sitePath('/contact/?interest=review')}>Discuss context optimization <span aria-hidden="true">↗</span></CampaignLink>
      <p className="context-optimization-status">In development for Hormuz 1.2. Savings depend on eligible structure; current evidence is synthetic, not billed-cost or model-quality proof.</p>
    </div>

    <div className="context-optimization-stage" aria-hidden="true">
      <header><span><BrandMark /> HORMUZ / CONTEXT</span><span>LOCAL PREVIEW</span></header>
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
