"use client";

import { useEffect, useRef, useState } from 'react';
import { CampaignLink } from './CampaignLink';
import { pixelStarted, privacySignal, readAdConsent, saveAdConsent, startXPixel, xPixelSupported } from '../../lib/x-ads.mjs';
import { analyticsConfigured, analyticsStarted, readAnalyticsConsent, saveAnalyticsConsent, startAnalytics, trackAnalyticsEvent, trackedDestination } from '../../lib/analytics.mjs';
import { sitePath } from '../../lib/site.mjs';

const OPEN_PREFERENCES = 'hormuz:ad-preferences';

export function AdPreferencesButton() {
  return <button className="ad-preferences-link" onClick={() => window.dispatchEvent(new Event(OPEN_PREFERENCES))}>Privacy choices</button>;
}

export function AdConsent() {
  const [open, setOpen] = useState(false);
  const [signal, setSignal] = useState(false);
  const [allowAnalytics, setAllowAnalytics] = useState(false);
  const [allowX, setAllowX] = useState(false);
  const [running, setRunning] = useState(false);
  const [reopened, setReopened] = useState(false);
  const [supported, setSupported] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  const hasAnalytics = analyticsConfigured();
  useEffect(() => {
    function readChoices() {
      const x = readAdConsent();
      const analytics = readAnalyticsConsent();
      const canX = xPixelSupported();
      setSupported(canX); setSignal(privacySignal());
      setAllowX(canX && x === 'allowed'); setAllowAnalytics(analytics === 'allowed');
      setRunning(pixelStarted() || analyticsStarted());
      return (canX && x === 'unset') || (hasAnalytics && analytics === 'unset');
    }
    setOpen(readChoices());
    startXPixel(); startAnalytics();
    const show = () => {
      opener.current = document.activeElement as HTMLElement | null;
      readChoices(); setReopened(true); setOpen(true);
    };
    // Opening the interactive preview dismisses this non-modal prompt only.
    // Unset choices stay unset; measurement still requires explicit consent.
    const makeRoomForPreview = () => setOpen(false);
    const trackLink = (event: MouseEvent) => {
      if (!event.isTrusted || !(event.target instanceof Element)) return;
      const link = event.target.closest('a[href]');
      const measurement = link && trackedDestination(link.getAttribute('href'));
      if (measurement) trackAnalyticsEvent(measurement[0], measurement[1]);
    };
    window.addEventListener(OPEN_PREFERENCES, show);
    window.addEventListener('hormuz:open-companion-preview', makeRoomForPreview);
    document.addEventListener('click', trackLink);
    return () => {
      window.removeEventListener(OPEN_PREFERENCES, show);
      window.removeEventListener('hormuz:open-companion-preview', makeRoomForPreview);
      document.removeEventListener('click', trackLink);
    };
  }, [hasAnalytics]);
  useEffect(() => { if (open && reopened) heading.current?.focus(); }, [open, reopened]);

  function save(decline = false) {
    const x = saveAdConsent(!decline && allowX && supported ? 'allowed' : 'declined');
    const analytics = hasAnalytics ? saveAnalyticsConsent(!decline && allowAnalytics ? 'allowed' : 'declined') : 'declined';
    const withdraw = (pixelStarted() && x !== 'allowed') || (analyticsStarted() && analytics !== 'allowed');
    setOpen(false);
    if (withdraw) { window.location.reload(); return; }
    startXPixel(); startAnalytics();
    opener.current?.focus();
  }

  if (!open) return null;
  return <section className="ad-consent" aria-labelledby="ad-consent-title">
    <div><span className="section-label">Your privacy choices</span><h2 id="ad-consent-title" ref={heading} tabIndex={-1}>Help us understand what works.</h2>
      <p>Choose optional measurement. You can use the site and submit an inquiry either way. <CampaignLink href={sitePath('/privacy/#analytics')}>Details & choices</CampaignLink>.</p>
      <div className="measurement-options">
        {hasAnalytics && <label><input type="checkbox" checked={allowAnalytics} disabled={signal} onChange={event => setAllowAnalytics(event.target.checked)} /><span><strong>Website analytics</strong><small>Allow Google Analytics cookies to measure campaign visits, demo use, install links, and submitted inquiries. Form entries are never included.</small></span></label>}
        {supported && <label><input type="checkbox" checked={allowX} disabled={signal} onChange={event => setAllowX(event.target.checked)} /><span><strong>X ad measurement</strong><small>Allow X cookies and visit signals to measure ads and acknowledged sales inquiries.</small></span></label>}
      </div>
      {!supported && <p>X Ads measurement stays off in Safari and on iPhone and iPad browsers for site reliability.</p>}
      {signal && <p>We respect your browser’s privacy signal. Optional measurement is off.</p>}
      {running && <p>Turning active measurement off reloads this page. Copy any unfinished application first.</p>}
    </div>
    <div className="ad-consent-actions"><button type="button" onClick={() => save(true)}>Decline</button>{!signal && (hasAnalytics || supported) && <button type="button" onClick={() => save()}>Save choices</button>}</div>
  </section>;
}
