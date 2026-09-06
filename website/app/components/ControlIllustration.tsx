export function ControlIllustration({ index }: { index: number }) {
  return <div className={`control-art control-art-${index}`} aria-hidden="true">
    {index === 0 && <><span className="art-app">⌘<small>Codex</small></span><i>→</i><span className="art-hormuz">H</span><i>←</i><span className="art-app">✳<small>Claude Code</small></span></>}
    {index === 1 && <><span className="art-key">✳ ✳ ✳ ✳</span><span className="art-seal">⌑</span><small className="art-caption">PROVIDER KEYS / SERVER-SIDE</small></>}
    {index === 2 && <div className="art-budget"><span>Before forwarding <b>Policy check</b></span><div><i /><i /><i /><i /><i /><i /><i /><i /></div><small>Spend allowance <strong>Enforced</strong></small></div>}
    {index === 3 && <div className="art-redact"><span>Configured secret detected</span><code>api_key: <b>[REDACTED]</b></code><small>Check before provider egress ↗</small></div>}
    {index === 4 && <div className="art-policy"><span>Organization</span><span>↳ Team</span><span>↳ Person <b>Tighter only</b></span></div>}
    {index === 5 && <div className="art-ledger"><span>IDENTITY <b>POLICY</b> USAGE</span><i /><i /><i /><small>Content-free by design ↗</small></div>}
  </div>;
}
