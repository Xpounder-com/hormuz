'use client';

import { useState } from 'react';

/** An interactive illustration, not a connected console or live customer data. */
export function GatewayPreview() {
  const [blocked, setBlocked] = useState(false);
  return <div className="gateway-stage" id="gateway-preview">
    <div className="gateway-stage-caption"><span className="mini-dot" /> One request. A clear decision.<span>Interactive illustration</span></div>
    <div className="gateway-gallery">
      <article className="gateway-side policy-mini">
        <div className="mini-top"><span className="mini-symbol">≡</span><span>01 / Set the policy</span></div>
        <h3>Your team.<br />Your rules.</h3>
        <div className="mini-team"><span>EN</span><div><strong>Engineering</strong><small>Team policy · Example</small></div></div>
        <dl><div><dt>Model access</dt><dd>Approved models</dd></div><div><dt>Provider keys</dt><dd>Server-side</dd></div><div><dt>Spend limits</dt><dd>Enforced</dd></div></dl>
        <p className="mini-bottom"><span className="mini-dot" /> Policy checked before forwarding</p>
      </article>
      <article className={`gateway-console${blocked ? ' gateway-blocked' : ''}`}>
        <header><div className="console-dots" aria-hidden="true"><i /><i /><i /></div><span>HORMUZ / REQUEST GATEWAY</span><span className="console-example">EXAMPLE</span></header>
        <div className="gateway-switch" role="group" aria-label="Illustrative request scenario">
          <button type="button" aria-pressed={!blocked} onClick={() => setBlocked(false)}>Allowed request</button>
          <button type="button" aria-pressed={blocked} onClick={() => setBlocked(true)}>Blocked request</button>
        </div>
        <div className="gateway-path" aria-hidden="true"><div><span>⌘</span><small>AI client</small></div><i /><div className="gateway-h"><span>H</span><small>Hormuz</small></div><i className={blocked ? 'path-stopped' : ''} /><div><span>{blocked ? '⊘' : '↗'}</span><small>Provider</small></div></div>
        <div className="gateway-outcome" aria-live="polite" aria-atomic="true">
          <p><span className="outcome-check">{blocked ? '×' : '✓'}</span>{blocked ? 'Stopped before the provider.' : 'Checks passed. Request forwarded.'}</p>
          <div className="gateway-checks"><span>✓ Identity</span><span>{blocked ? '× Model policy' : '✓ Model policy'}</span><span>{blocked ? '— Budget not reserved' : '✓ Budget reserved'}</span></div>
          <div className="console-log"><span>decision</span><strong>{blocked ? 'deny' : 'allow'}</strong><span>upstream calls</span><strong>{blocked ? '0' : '1'}</strong><span>prompt retained</span><strong>0 bytes</strong></div>
        </div>
        <footer>Illustrative behavior · No requests are sent</footer>
      </article>
      <article className="gateway-side evidence-mini">
        <div className="mini-top"><span className="mini-symbol">↗</span><span>02 / Keep the evidence</span></div>
        <h3>Know what happened.<br /><em>Keep the content out.</em></h3>
        <div className="evidence-paper"><div className="paper-heading"><span>REQUEST RECORD</span><span>↗</span></div><div><span>Identity</span><strong>Attributed</strong></div><div><span>Policy</span><strong>Versioned</strong></div><div><span>Decision</span><strong>{blocked ? 'Denied' : 'Allowed'}</strong></div><div className="paper-zero"><strong>0 bytes</strong><span>of prompt or response content<br />in the routine ledger</span></div></div>
      </article>
    </div>
  </div>;
}
