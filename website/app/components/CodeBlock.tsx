'use client';

import { useState } from 'react';

export function CodeBlock({ code, label = 'Terminal' }: { code: string; label?: string }) {
  const [status, setStatus] = useState('');
  async function copy() {
    try {
      await navigator.clipboard.writeText(code);
      setStatus('Copied to clipboard.');
    } catch {
      setStatus('Copy unavailable. Select and copy the commands below.');
    }
  }
  return <div className="code-window"><div className="code-window-bar"><span>{label}</span><button type="button" onClick={copy} aria-label={`Copy ${label}`}>Copy commands</button></div><pre tabIndex={0}><code>{code}</code></pre><p className="copy-status" role="status">{status}</p></div>;
}
