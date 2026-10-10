'use client';

import { useEffect, useRef, type ReactNode } from 'react';

const anchors = new Set(['compaction', 'policy', 'companion', 'recording', 'policy-recording', 'evidence']);

export function TechnicalDemo({ children }: { children: ReactNode }) {
  const disclosure = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    function revealAnchor() {
      const id = window.location.hash.slice(1);
      if (!anchors.has(id)) return;
      const section = document.getElementById(id);
      if (!disclosure.current?.contains(section)) return;
      disclosure.current.open = true;
      requestAnimationFrame(() => section?.scrollIntoView());
    }
    revealAnchor();
    window.addEventListener('hashchange', revealAnchor);
    return () => window.removeEventListener('hashchange', revealAnchor);
  }, []);
  return <details ref={disclosure} className="section disclosure"><summary>Technical examples: compaction, policy, and recorded gateway controls</summary>{children}</details>;
}
