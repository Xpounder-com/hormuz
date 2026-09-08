'use client';

import { useId, useState } from 'react';
import { BrandMark } from './Brand';
import mark from '../../lib/brand-mark.json';

export function ContextFold() {
  const [optimized, setOptimized] = useState(true);
  const id = useId();
  const gradient = `${id}-paper`;
  const shade = `${id}-shade`;
  const shadow = `${id}-shadow`;
  const caption = `${id}-caption`;

  return <figure className="context-fold" data-optimized={optimized} aria-label="Interactive context optimization design preview">
    <header className="context-fold-header">
      <span><BrandMark /> THE CONTEXT FOLD</span>
      <span className="context-fold-preview">DESIGN PREVIEW</span>
    </header>
    <div className="context-fold-heading">
      <span className="context-fold-kicker">LESS REPETITION. SAME INFORMATION.</span>
      <p>Room for what matters.</p>
    </div>
    <div className="context-fold-art">
      <svg viewBox="0 0 640 382" role="img" aria-label={optimized
        ? 'Illustration: repeated tool output folds into a compact, reversible structure on your Mac. Only the selected request continues to the Hormuz gateway. Fold size is illustrative, not a measured saving.'
        : 'Illustration: the same repeated tool output is unfolded. Select Optimized to see a local, reversible fold. This control changes only the illustration.'}>
        <defs>
          <linearGradient id={gradient} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="#e1edb6" /><stop offset=".55" stopColor="#c9dd9d" /><stop offset="1" stopColor="#a9c17e" />
          </linearGradient>
          <linearGradient id={shade} x1="0" y1="0" x2="1" y2="0">
            <stop stopColor="#587441" /><stop offset="1" stopColor="#91ad69" />
          </linearGradient>
          <radialGradient id={shadow}>
            <stop stopColor="#324831" stopOpacity=".25" /><stop offset="1" stopColor="#324831" stopOpacity="0" />
          </radialGradient>
        </defs>
        <g aria-hidden="true">
          <path d="M32 76V47H510V76M32 312V341H510V312" fill="none" stroke="#b2b8a7" strokeWidth="1" />
          <text x="49" y="67" className="context-fold-svg-label">ON YOUR MAC</text>
          <circle cx="492" cy="60" r="3" fill="#66844d" />
          <path d="M34 212H552" fill="none" stroke="#8c9b7d" strokeDasharray="2 7" />
          <ellipse className="context-fold-shadow" cx="280" cy="300" rx={optimized ? 160 : 245} ry="36" fill={`url(#${shadow})`} />
          <g className="context-fold-paper">
            {Array.from({ length: 6 }, (_, index) => {
              const back = index % 2 === 1;
              return <g key={index} className="context-fold-panel" style={{ transform: optimized
                ? `matrix(.48, ${back ? '-.62' : '.62'}, 0, 1, ${178 + index * 34.56}, ${back ? 135.64 : 91})`
                : `matrix(1, ${back ? '-.055' : '.055'}, 0, 1, ${54 + index * 72}, ${back ? 134.96 : 131})` }}>
                <path d="M0 0H72V158H0Z" fill={optimized && back ? `url(#${shade})` : `url(#${gradient})`} stroke={optimized && back ? '#78925a' : '#a7bd85'} strokeWidth=".8" />
                <path d="M1 1H71M1 1V157" fill="none" stroke="#f5f9df" strokeOpacity={back && optimized ? '.2' : '.7'} />
                <g fill="none" stroke={optimized && back ? '#dce9bd' : '#527344'} strokeWidth="2" strokeLinecap="round" opacity=".72">
                  <path d="M19 29H51M19 37H43M19 59H51M19 67H43M19 89H51M19 97H43M19 119H51M19 127H43" />
                </g>
                <path d="M60 13V145" stroke="#456339" strokeOpacity=".18" strokeDasharray="2 4" />
              </g>;
            })}
          </g>
          <g className="context-fold-callout">
            <path d={optimized ? 'M282 103V76H351' : 'M274 132V95H351'} fill="none" stroke="#657957" />
            <circle cx="351" cy={optimized ? 76 : 95} r="2.5" fill="#657957" />
            <text x="361" y={optimized ? 80 : 99} className="context-fold-svg-label">{optimized ? 'A REVERSIBLE FOLD' : 'REPEATED TOOL OUTPUT'}</text>
          </g>
          <g className="context-fold-gateway" transform="translate(552 190)">
            <rect width="48" height="48" rx="14" fill="#263c2b" />
            <path d="M-22 23H-8M-12 19L-8 23L-12 27" fill="none" stroke="#57704a" strokeWidth="1.5" />
            <g transform="translate(10 10) scale(.4375)" fill="#dfedb6">{mark.paths.map(d => <path key={d} d={d} />)}</g>
            <text x="24" y="70" textAnchor="middle" className="context-fold-svg-label">HORMUZ</text>
          </g>
          <text x="49" y="325" className="context-fold-svg-label context-fold-wide-label">{optimized ? 'COMPACT LOCALLY → CONTINUE THROUGH YOUR CONTROLS' : 'ORIGINAL REPRESENTATION → SAME GOVERNED PATH'}</text>
          <text x="49" y="325" className="context-fold-svg-label context-fold-compact-label">LOCAL → GOVERNED</text>
        </g>
      </svg>
    </div>
    <div className="context-fold-controls" role="group" aria-label="Compare context representations" aria-describedby={caption}>
      <button type="button" aria-pressed={!optimized} onClick={() => setOptimized(false)}><span aria-hidden="true">↔</span> Original</button>
      <button type="button" aria-pressed={optimized} onClick={() => setOptimized(true)}><BrandMark /> Optimized</button>
    </div>
    <figcaption id={caption}>
      <div className="context-fold-caption">
        <p aria-live="polite" aria-atomic="true">{optimized ? 'The repetition folds. The information stays.' : 'Same information, with the repetition unfolded.'}</p>
        <span>Illustration only · No requests sent · No savings percentage implied</span>
      </div>
      <ol className="context-fold-steps">
        <li><span>01</span><div><strong>Find the repetition</strong><small>Eligible tool output</small></div></li>
        <li><span>02</span><div><strong>Fold on your Mac</strong><small>Designed to be reversible</small></div></li>
        <li><span>03</span><div><strong>Keep the controls</strong><small>Through your gateway</small></div></li>
      </ol>
    </figcaption>
  </figure>;
}
