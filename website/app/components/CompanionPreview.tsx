'use client';

import { useEffect, useRef, useState, type CSSProperties } from 'react';
import { BrandMark } from './Brand';
import { sitePath } from '../../lib/site.mjs';

type Panel = 'home' | 'connection' | 'client' | 'appearance' | 'cost' | 'tokens' | 'requests';
const metrics = [
  { id: 'cost', label: 'Estimated cost', glyph: '$', value: '$73', detail: '$73.00 this month', copy: 'Based on the team’s configured rate card. This is an estimate, not a provider invoice.' },
  { id: 'tokens', label: 'Tokens', glyph: '#', value: '210K', detail: '210,000 tokens this month', copy: '150,000 input · 60,000 output. Only requests routed through the Hormuz gateway are counted.' },
  { id: 'requests', label: 'Requests', glyph: '↕', value: '520', detail: '520 requests this month', copy: '508 allowed · 12 denied. Routine evidence records outcomes without prompt or response content.' },
] as const;

/** Isolated, synthetic UI demonstration. Never reads a session or calls a gateway. */
export function CompanionPreview() {
  const [panel, setPanel] = useState<Panel | null>(null);
  const [folded, setFolded] = useState(false);
  const [scale, setScale] = useState(1);
  const [expired, setExpired] = useState(false);
  const [inView, setInView] = useState(false);
  const section = useRef<HTMLElement>(null);
  const controls = useRef<HTMLDivElement>(null);
  const edge = useRef<HTMLDivElement>(null);
  const gear = useRef<HTMLButtonElement>(null);
  const peek = useRef<HTMLButtonElement>(null);
  const trigger = useRef<HTMLButtonElement | null>(null);
  const metric = metrics.find(item => item.id === panel);
  function close() {
    setPanel(null);
    const source = trigger.current;
    (source?.offsetParent && !source.closest('[inert]') ? source : gear.current)?.focus();
  }
  function open(next: Panel, button: HTMLButtonElement) {
    trigger.current = button;
    setFolded(false);
    setPanel(next);
  }
  useEffect(() => {
    if (panel) controls.current?.focus({ preventScroll: true });
  }, [panel]);
  useEffect(() => { if (folded) peek.current?.focus({ preventScroll: true }); }, [folded]);
  useEffect(() => {
    if (!panel) return;
    function dismissOutside(event: PointerEvent) {
      const target = event.target;
      if (!(target instanceof Node) || controls.current?.contains(target) || edge.current?.contains(target)) return;
      // Keep the widget expanded and let the clicked destination receive focus.
      setPanel(null);
      setFolded(false);
    }
    document.addEventListener('pointerdown', dismissOutside, true);
    return () => document.removeEventListener('pointerdown', dismissOutside, true);
  }, [panel]);
  useEffect(() => {
    const observer = new IntersectionObserver(([entry]) => setInView(entry.isIntersecting));
    if (section.current) observer.observe(section.current);
    return () => observer.disconnect();
  }, []);

  return <section className="companion-section" aria-labelledby="companion-title" id="companion" ref={section} data-in-view={inView}>
    <div className="companion-intro">
      <p className="landing-eyebrow">HORMUZ FOR MAC · INTERACTIVE PREVIEW</p>
      <h2 id="companion-title">A little presence.<br /><em>Everything within reach.</em></h2>
      <p>Usage at the edge of your screen. Your connection, client setup, and session controls a click away.</p>
      <p className="companion-hint">Try a ring, then open the gear. The preview uses example data.</p>
      <a className="button landing-secondary" href={sitePath('/integrations/')}>Explore client setup <span aria-hidden="true">↗</span></a>
    </div>
    <div className="companion-desktop" style={{ '--companion-scale': scale } as CSSProperties}
      onKeyDown={event => { if (event.key === 'Escape') { event.preventDefault(); close(); } }}>
      <div className="companion-desktop-bar"><BrandMark /><span>Hormuz</span><span>EXAMPLE WORKSPACE</span></div>
      <div className="companion-wallpaper" aria-hidden="true"><BrandMark /><span>Stay in your flow.</span></div>
      <div className="companion-edge" data-folded={folded} ref={edge}>
        <button className="companion-peek" type="button" aria-label="Show Hormuz widget" hidden={!folded} ref={peek}
          onClick={event => open('home', event.currentTarget)} />
        <div className="companion-expanded" inert={folded}>
          {/* Codenotch geometry adaptation, Copyright (c) 2026 Vinz. MIT: /licenses/codenotch.txt */}
          <svg className="companion-silhouette" viewBox="0 0 70 399" preserveAspectRatio="none" aria-hidden="true"><path d="M70 0 A39 39 0 0 1 31 39 H30 A30 30 0 0 0 0 69 V330 A30 30 0 0 0 30 360 H31 A39 39 0 0 1 70 399Z" /></svg>
          <div className="companion-rings">
            {metrics.map(item => <button key={item.id} type="button" className="companion-metric" aria-label={`View ${item.label.toLowerCase()}`}
              aria-pressed={panel === item.id} onClick={event => open(item.id, event.currentTarget)}>
              <span className="companion-ring"><span aria-hidden="true">{item.glyph}</span><i className={expired ? 'is-expired' : ''} /></span>
              {!expired && <span className="companion-reading">{item.value}</span>}
            </button>)}
          </div>
          <button type="button" className="companion-gear" aria-label="Open Hormuz controls" aria-expanded={panel !== null} ref={gear}
            onClick={event => panel ? close() : open('home', event.currentTarget)}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="m9 3-.6 2.4-2 .9-2.2-.7-2 3.4 1.7 1.8v2.4L2.2 15l2 3.4 2.2-.7 2 .9L9 21h4l.6-2.4 2-.9 2.2.7 2-3.4-1.7-1.8v-2.4L19.8 9l-2-3.4-2.2.7-2-.9L13 3Z"/><circle cx="11" cy="12" r="3"/></svg>
          </button>
        </div>
      </div>
      <div className="companion-card" data-open={panel !== null} inert={panel === null} ref={controls} tabIndex={-1}
        role="region" aria-label="Hormuz example controls">
        <header><BrandMark /><strong>{metric?.label ?? (panel === 'home' ? 'Hormuz' : panel ? panel[0].toUpperCase() + panel.slice(1) : 'Hormuz')}</strong>
          <button type="button" aria-label="Close example controls" onClick={close}>×</button></header>
        <div className="companion-card-content" key={panel}>
          <p className="companion-example">ILLUSTRATION · NO CONNECTED SESSION</p>
          {panel === 'home' && <><h3>Your AI, governed.</h3><p>Engineering · Example team</p><p className={expired ? 'companion-warning' : 'companion-status'}>{expired ? 'Session expired' : 'Connected in this example'}</p>
            {(['connection', 'client', 'appearance'] as const).map((page, index) => <button type="button" className="companion-row" key={page} onClick={() => setPanel(page)}><span><strong>{['Connection', 'Client setup', 'Appearance'][index]}</strong><small>{['Session & Keychain', 'Review your client configuration', 'Size & visibility'][index]}</small></span><span aria-hidden="true">›</span></button>)}</>}
          {panel === 'connection' && <><h3>{expired ? 'Session expired' : 'Your access, kept close.'}</h3><dl><div><dt>Organization</dt><dd>Example team</dd></div><div><dt>Client</dt><dd>Codex</dd></div><div><dt>Session storage</dt><dd>macOS Keychain</dd></div></dl><p>The Mac app stores your revocable Hormuz session in Keychain. Provider keys stay with your team.</p><button type="button" className="companion-action" onClick={() => setExpired(!expired)}>{expired ? 'Restore connected example' : 'Preview expired session'}</button><small>This only changes the illustration. This website cannot access your Keychain.</small></>}
          {panel === 'client' && <><h3>Keep your tools.</h3><p>The Mac app helps you review client settings before saving and launching a governed session.</p><dl><div><dt>Example client</dt><dd>Codex</dd></div><div><dt>Provider</dt><dd>OpenAI</dd></div><div><dt>Scope</dt><dd>Launched session</dd></div></dl><a className="companion-action" href={sitePath('/integrations/')}>Read the setup guide ↗</a></>}
          {panel === 'appearance' && <><h3>Make room for your work.</h3><p>Widget size</p><div className="companion-sizes" role="group" aria-label="Example widget size">{[1, 1.25, 1.5].map(value => <button type="button" key={value} aria-pressed={value === scale} onClick={() => setScale(value)}>{value * 100}%</button>)}</div><p>Visibility</p><button type="button" className="companion-action" onClick={() => { close(); setFolded(true); }}>Fold the widget</button><small>Click the edge tab to bring it back.</small></>}
          {metric && <><h3>{expired ? 'Session expired' : metric.detail}</h3><p>{expired ? 'Reconnect in the Mac app to refresh your usage. Unavailable totals are hidden rather than shown as zero.' : metric.copy}</p><small>Example totals · No percentage is implied without a configured limit.</small><button type="button" className="companion-action" onClick={() => setPanel('home')}>Open Hormuz controls</button></>}
          {panel && panel !== 'home' && <button type="button" className="companion-back" onClick={() => setPanel('home')}>← All controls</button>}
        </div>
        <footer>Gateway requests only</footer>
      </div>
      <p className="companion-stage-caption">Click the rings to explore usage. Open the gear for controls.</p>
    </div>
  </section>;
}
