"use client";

import { CampaignLink } from './CampaignLink';


import { useEffect, useRef, useState } from 'react';
import { pixelStarted, privacySignal, readAdConsent, saveAdConsent, startXPixel, xPixelSupported } from '../../lib/x-ads.mjs';
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
  const [supported, setSupported] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  useEffect(() => {
    const saved = readAdConsent();
    const canMeasure = xPixelSupported();
    setSupported(canMeasure); setSignal(privacySignal()); setChoice(saved); setOpen(canMeasure && saved === 'unset');
    if (canMeasure && saved === 'allowed') startXPixel();
    const show = () => {
      opener.current = document.activeElement as HTMLElement | null;
      setSupported(xPixelSupported()); setChoice(readAdConsent()); setSignal(privacySignal()); setReopened(true); setOpen(true);
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
      {supported
        ? <p>Allow X to use cookies and visit signals to measure our ads and submitted sales inquiries. Your form entries are never included. You can apply either way. <CampaignLink href={sitePath('/privacy/#advertising')}>Details & choices</CampaignLink>.</p>
        : <p>X Ads measurement is disabled in Safari and on iPhone and iPad browsers to keep optional third-party code out of this page’s runtime. No X code runs here. <CampaignLink href={sitePath('/privacy/#advertising')}>Details</CampaignLink>.</p>}
      {supported && signal && <p>We respect your browser’s privacy signal. Ad measurement is off.</p>}
      {supported && choice === 'allowed' && <p>Turning measurement off reloads this page. Copy any unfinished application first.</p>}
    </div>
    <div className="ad-consent-actions"><button type="button" onClick={() => choose('declined')}>{supported && !signal ? 'Decline' : 'Keep measurement off'}</button>{supported && !signal && <button type="button" onClick={() => choose('allowed')}>Allow measurement</button>}</div>
  </section>;
}
