"use client";

import { useEffect, useRef, useState, type FormEvent } from 'react';
import { INTERESTS, campaignSource } from '../../lib/contact.mjs';
import { buildLead, submitLead } from '../../lib/lead.mjs';
import { CONTACT_EMAIL, sitePath } from '../../lib/site.mjs';

export function LeadForm({ endpoint, bookingUrl }: { endpoint: string; bookingUrl: string }) {
  const [interest, setInterest] = useState('pilot');
  const [search, setSearch] = useState('');
  const [includeSource, setIncludeSource] = useState(false);
  const [state, setState] = useState<'idle' | 'sending' | 'success' | 'error'>('idle');
  const [error, setError] = useState('');
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
    try { payload = buildLead({ ...Object.fromEntries(new FormData(event.currentTarget)), interest }, includeSource ? search : ''); }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Check your entries.'); setState('error'); return; }
    pending.current = true;
    setState('sending'); setError('');
    try { await submitLead(endpoint, payload); setState('success'); }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Receipt could not be confirmed. Please contact us by email.'); setState('error'); }
    finally { pending.current = false; }
  }
  if (state === 'success') return <section className="draft-panel" aria-label="Application received">
    <h2 ref={confirmation} tabIndex={-1}>Application received.</h2>
    <p>Thank you. Mehrdad will review your workflow and follow up about fit and scope. No payment has been taken and no service or meeting is booked.</p>
    {bookingUrl && <a className="button button-primary" href={bookingUrl} rel="noreferrer">Book your free governance review ↗</a>}
  </section>;
  return <div className="contact-flow">
    <form className="contact-form" onSubmit={submit} aria-busy={state === 'sending'}>
      <fieldset disabled={state === 'sending'} className="lead-fields">
        <legend className="sr-only">Enterprise application</legend>
        <label>I am interested in<select name="interest" value={interest} onChange={e => setInterest(e.target.value)}>{Object.entries(INTERESTS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label>Your name<input name="name" autoComplete="name" required maxLength={100} /></label>
        <label>Work email<input name="email" type="email" autoComplete="email" required maxLength={254} /></label>
        <label>Organization<input name="organization" autoComplete="organization" required maxLength={150} /></label>
        <label>What does your team need to control?<textarea name="workflow" required maxLength={1200} rows={4} aria-describedby="lead-safety" placeholder="For example: Codex model access and budgets for one engineering team." /></label>
        <p className="field-hint" id="lead-safety">Do not include credentials, prompts, customer data, or other secrets.</p>
        <label>Timing <span>(optional)</span><input name="timeframe" maxLength={100} placeholder="For example: this quarter" /></label>
        <div className="lead-trap" aria-hidden="true"><label>Leave this empty<input name="_gotcha" tabIndex={-1} autoComplete="off" /></label></div>
        {campaignSource(search) && <label className="checkbox-label"><input type="checkbox" checked={includeSource} onChange={e => setIncludeSource(e.target.checked)} />Include campaign source with my application: {campaignSource(search)}</label>}
        <p className="field-hint">Submitting sends these details to Hormuz through Formspree so we can respond to this inquiry. It does not subscribe you to marketing emails. <a href={sitePath('/privacy/')}>Privacy details</a>.</p>
        <button className="button button-primary" type="submit">{state === 'sending' ? 'Sending application…' : 'Submit application →'}</button>
      </fieldset>
    </form>
    {state === 'sending' && <p role="status">Sending your application…</p>}
    {state === 'error' && <p role="alert" className="form-status">{error} <a href={`mailto:${CONTACT_EMAIL}`}>Email Mehrdad</a>.</p>}
    <noscript><p>Application submission needs JavaScript. Please email <a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a>.</p></noscript>
  </div>;
}
