"use client";

import { useEffect, useRef, useState } from 'react';
import { pixelStarted, privacySignal, readAdConsent, saveAdConsent, startXPixel } from '../../lib/x-ads.mjs';
import { sitePath } from '../../lib/site.mjs';

const OPEN_PREFERENCES = 'hormuz:ad-preferences';

export function AdPreferencesButton() {
  return <button className="ad-preferences-link" onClick={() => window.dispatchEvent(new Event(OPEN_PREFERENCES))}>Ad privacy choices</button>;
}

export function AdConsent() {
  const [open, setOpen] = useState(false);
  const [signal, setSignal] = useState(false);
  const [choice, setChoice] = useState('unset');
  const [reopened, setReopened] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  useEffect(() => {
    const saved = readAdConsent();
    setSignal(privacySignal()); setChoice(saved); setOpen(saved === 'unset');
    if (saved === 'allowed') startXPixel();
    const show = () => {
      opener.current = document.activeElement as HTMLElement | null;
      setChoice(readAdConsent()); setSignal(privacySignal()); setReopened(true); setOpen(true);
    };
    window.addEventListener(OPEN_PREFERENCES, show);
    return () => window.removeEventListener(OPEN_PREFERENCES, show);
  }, []);
  useEffect(() => { if (open && reopened) heading.current?.focus(); }, [open, reopened]);
  function choose(value: 'allowed' | 'declined') {
    const saved = saveAdConsent(value);
    setChoice(saved); setOpen(false);
    if (saved === 'allowed') startXPixel();
    // Reload unloads the vendor SDK completely after withdrawal.
    else if (pixelStarted()) { window.location.reload(); return; }
    opener.current?.focus();
  }
  if (!open) return null;
  return <section className="ad-consent" aria-labelledby="ad-consent-title">
    <div><span className="section-label">Your privacy choices</span><h2 id="ad-consent-title" ref={heading} tabIndex={-1}>Help us understand what works.</h2>
      <p>Allow X to use cookies and visit signals to measure our ads and completed applications. Your form entries are never included. You can apply either way. <a href={sitePath('/privacy/#advertising')}>Details & choices</a>.</p>
      {signal && <p>We respect your browser’s privacy signal. Ad measurement is off.</p>}
      {choice === 'allowed' && <p>Turning measurement off reloads this page. Copy any unfinished application first.</p>}
    </div>
    <div className="ad-consent-actions"><button type="button" onClick={() => choose('declined')}>{signal ? 'Keep measurement off' : 'Decline'}</button>{!signal && <button type="button" onClick={() => choose('allowed')}>Allow measurement</button>}</div>
  </section>;
}
