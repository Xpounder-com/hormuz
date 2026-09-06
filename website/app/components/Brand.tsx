/** The existing Hormuz mark, shared by navigation and product illustrations. */
export function BrandMark({ className = '' }: { className?: string }) {
  return <svg className={`hormuz-mark ${className}`} viewBox="0 0 64 64" fill="none" aria-hidden="true" focusable="false">
    <circle cx="32" cy="32" r="29" stroke="currentColor" strokeWidth="1.5" />
    <g fill="currentColor"><rect x="15" y="25" width="8" height="14" rx="4" /><rect x="28" y="18" width="8" height="28" rx="4" /><rect x="41" y="22.5" width="8" height="19" rx="4" /></g>
  </svg>;
}

export function BrandLockup() {
  return <><BrandMark /><span className="brand-name">HORMUZ</span></>;
}

/** Decorative route lines. Product diagrams carry their own explicit labels. */
export function PassageLines({ className = '' }: { className?: string }) {
  return <svg className={`passage-lines ${className}`} viewBox="0 0 600 360" fill="none" aria-hidden="true" focusable="false">
    {[0, 1, 2, 3, 4, 5].map(line => <path key={line} d={`M -40 ${56 + line * 22} H 130 C 260 ${56 + line * 22}, 270 ${218 + line * 22}, 410 ${218 + line * 22} H 650`} stroke="currentColor" strokeWidth={line === 2 ? 2 : 1} />)}
    <circle cx="130" cy="100" r="5" fill="currentColor" /><circle cx="410" cy="262" r="5" fill="currentColor" />
  </svg>;
}
