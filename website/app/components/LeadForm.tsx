"use client";

import { CampaignLink } from './CampaignLink';


import { useEffect, useRef, useState, type FormEvent } from 'react';
import { INTERESTS, campaignSource, isSalesInquiry } from '../../lib/contact.mjs';
import { prepareLeadAttempt, submitLead } from '../../lib/lead.mjs';
import { trackConfirmedApplication } from '../../lib/x-ads.mjs';
import { PILOT_PRICE, SUPPORT_PRICE } from '../../lib/commercial.mjs';
import { CONTACT_EMAIL, sitePath } from '../../lib/site.mjs';

export function LeadForm({ endpoint, bookingUrl }: { endpoint: string; bookingUrl: string }) {
  const [interest, setInterest] = useState('review');
  const [search, setSearch] = useState('');
  const [includeSource, setIncludeSource] = useState(false);
  const [state, setState] = useState<'idle' | 'sending' | 'success' | 'error'>('idle');
  const [error, setError] = useState('');
  const [reference, setReference] = useState('');
  const requestReference = useRef('');
  const testSubmission = new URLSearchParams(search).get('qa') === '1';
  const selectedOffer = interest === 'review' ? { price: '$0', detail: 'Free AI governance review · no obligation', action: 'Request my free review →' } : interest === 'pilot' ? { price: PILOT_PRICE, detail: 'USD · one-time fee for a scoped 90-day pilot', action: 'Discuss my pilot →' } : interest === 'support' ? { price: `From ${SUPPORT_PRICE}/mo`, detail: 'USD · separately agreed enterprise support', action: 'Request support details →' } : null;
  const pending = useRef(false);
  const confirmation = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    setSearch(window.location.search);
    const selected = new URLSearchParams(window.location.search).get('interest');
    if (selected && Object.hasOwn(INTERESTS, selected)) setInterest(selected);
  }, []);
  useEffect(() => { if (state === 'success') confirmation.current?.focus(); }, [state]);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending.current || state === 'success') return;
    let payload;
    try {
      // Reuse this reference if the visitor retries an ambiguous transport result.
      payload = prepareLeadAttempt(
        { ...Object.fromEntries(new FormData(event.currentTarget)), interest },
        includeSource ? search : '',
        { reference: requestReference.current, testSubmission },
      );
      requestReference.current = payload.request_reference;
      setReference(requestReference.current);
    }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Check your entries.'); setState('error'); return; }
    pending.current = true;
    setState('sending'); setError('');
    try {
      await submitLead(endpoint, payload);
      setState('success');
      if (!payload._gotcha) trackConfirmedApplication(window, { interest, testSubmission });
    }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Receipt could not be confirmed. Please contact us by email.'); setState('error'); }
    finally { pending.current = false; }
  }
  if (state === 'success') return <section className="draft-panel" aria-label="Application received">
    <h2 ref={confirmation} tabIndex={-1}>{testSubmission ? 'Test inquiry received.' : 'Your inquiry is received.'}</h2>
    <p>Request reference: <strong>{reference}</strong>. Save this reference to help us find your inquiry.</p>
    <p>Mehrdad will review your workflow and reply personally. Response target: one business day. This page is your receipt; an automatic application email is not sent.</p>
    {bookingUrl && isSalesInquiry(interest) && <><p>Choose a 30-minute Google Meet review on Wednesday or Thursday, 10 am–3 pm Central. Include your request reference when booking.</p><a className="button button-primary" href={bookingUrl} rel="noreferrer">Choose my review time ↗</a><p>Google Calendar emails both of us an invitation after you complete the booking.</p></>}
    <p>No payment has been taken. A meeting is confirmed only after booking; any paid scope is agreed separately. Questions? <a href={`mailto:${CONTACT_EMAIL}?subject=${encodeURIComponent(`Hormuz inquiry ${reference}`)}`}>Email Mehrdad</a>.</p>
  </section>;
  return <div className="contact-flow">
    {testSubmission && <p role="status" className="form-status">QA test mode: this sends a clearly marked test inquiry to the owner. It does not count as an X Ads lead.</p>}
    <form className="contact-form" onSubmit={submit} aria-busy={state === 'sending'}>
      <fieldset disabled={state === 'sending'} className="lead-fields">
        <legend className="sr-only">Enterprise application</legend>
        <label>I am interested in<select name="interest" value={interest} onChange={e => setInterest(e.target.value)}>{Object.entries(INTERESTS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        {selectedOffer && <div className="selected-offer" aria-live="polite"><strong>{selectedOffer.price}</strong><span>{selectedOffer.detail}</span></div>}
        <label>Your name<input name="name" autoComplete="name" required maxLength={100} /></label>
        <label>Work email<input name="email" type="email" autoComplete="email" required maxLength={254} /></label>
        <label>Organization<input name="organization" autoComplete="organization" required maxLength={150} /></label>
        <label>What does your team need to control?<textarea name="workflow" required maxLength={1200} rows={4} aria-describedby="lead-safety" placeholder="For example: Codex model access and budgets for one engineering team." /></label>
        <p className="field-hint" id="lead-safety">Do not include credentials, prompts, customer data, or other secrets.</p>
        <label>Timing <span>(optional)</span><input name="timeframe" maxLength={100} placeholder="For example: this quarter" /></label>
        <div className="lead-trap" aria-hidden="true"><label>Leave this empty<input name="_gotcha" tabIndex={-1} autoComplete="off" /></label></div>
        {campaignSource(search) && <label className="checkbox-label"><input type="checkbox" checked={includeSource} onChange={e => setIncludeSource(e.target.checked)} />Include campaign source with my application: {campaignSource(search)}</label>}
        <p className="field-hint">Submitting sends these details to Hormuz through Formspree so we can respond to this inquiry. It does not subscribe you to marketing emails. <CampaignLink href={sitePath('/privacy/')}>Privacy details</CampaignLink>.</p>
        <p className="field-hint">Personal reply from Mehrdad. Response target: one business day. {bookingUrl && isSalesInquiry(interest) ? 'After submitting, choose a free 30-minute review time.' : ''}</p>
        <button className="button button-primary" type="submit">{state === 'sending' ? 'Sending…' : selectedOffer?.action || 'Send my inquiry →'}</button>
      </fieldset>
    </form>
    {state === 'sending' && <p role="status">Sending your application…</p>}
    {state === 'error' && <p role="alert" className="form-status">{error} {reference && <>Reference: <strong>{reference}</strong>. </>}<a href={`mailto:${CONTACT_EMAIL}?subject=${encodeURIComponent(`Check Hormuz inquiry ${reference}`)}`}>Email Mehrdad</a>.</p>}
    <noscript><p>Application submission needs JavaScript. Please email <a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a>.</p></noscript>
  </div>;
}
