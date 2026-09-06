import mark from '../../lib/brand-mark.json';

/** The approved passage H, shared by navigation and product illustrations. */
export function BrandMark({ className = '' }: { className?: string }) {
  return <svg className={`hormuz-mark ${className}`} viewBox="0 0 64 64" fill="none" aria-hidden="true" focusable="false">
    <g fill="currentColor">{mark.paths.map(d => <path key={d} d={d} />)}</g>
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
