'use client';

import { useEffect, useState } from 'react';
import { buildInquiry, campaignSource, INTERESTS } from '../../lib/contact.mjs';
import { CONTACT_EMAIL } from '../../lib/site.mjs';

export function ContactForm() {
  const [interest, setInterest] = useState('pilot');
  const [source, setSource] = useState('');
  const [includeSource, setIncludeSource] = useState(false);
  const [draft, setDraft] = useState<{ subject: string; body: string; mailto: string } | null>(null);
  const [status, setStatus] = useState('');
  useEffect(() => {
    const search = window.location.search;
    const selected = new URLSearchParams(search).get('interest');
    if (selected && Object.hasOwn(INTERESTS, selected)) setInterest(selected);
    setSource(campaignSource(search));
  }, []);
  function prepare(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = Object.fromEntries(new FormData(event.currentTarget));
    try {
      setDraft(buildInquiry({ ...fields, interest }, includeSource ? source : ''));
      setStatus('Draft prepared below. Nothing has been sent. Open your email app or copy the draft, then send it yourself.');
    } catch (error) { setStatus(error instanceof Error ? error.message : 'Please check the fields.'); }
  }
  async function copy() {
    if (!draft) return;
    try {
      await navigator.clipboard.writeText(`To: ${CONTACT_EMAIL}\nSubject: ${draft.subject}\n\n${draft.body}`);
      setStatus('Draft copied. Nothing has been sent. Paste it into your email app and send when ready.');
    } catch { setStatus('Clipboard unavailable. Select and copy the draft below; nothing has been sent.'); }
  }
  return <div className="contact-flow">
    <form className="contact-form" onSubmit={prepare} onChange={() => { setDraft(null); setStatus(''); }}>
      <label>What would you like to discuss?<select name="interest" value={interest} onChange={e => setInterest(e.target.value)}>{Object.entries(INTERESTS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <div className="form-row"><label>Your name <span>(required)</span><input name="name" autoComplete="name" required maxLength={100} /></label><label>Organization <span>(optional)</span><input name="organization" autoComplete="organization" maxLength={150} /></label></div>
      <label>Workflow or question <span>(required)</span><textarea name="workflow" required maxLength={1200} rows={5} aria-describedby="inquiry-safety" placeholder="For example: evaluate Codex access for one engineering team, with model limits and metadata-only usage reporting." /></label>
      <p id="inquiry-safety" className="field-hint">Do not include tokens, prompts, customer data, configurations, or other secrets.</p>
      <label>Timing <span>(optional)</span><input name="timeframe" maxLength={100} placeholder="For example: exploring this quarter" /></label>
      {source && <label className="checkbox-label"><input type="checkbox" checked={includeSource} onChange={e => setIncludeSource(e.target.checked)} />Include campaign source in my email draft: {source}</label>}
      <button className="button button-primary" type="submit">Prepare email draft →</button>
    </form>
    <p role="status" className="form-status">{status}</p>
    {draft && <section className="draft-panel" aria-label="Prepared email draft"><h2>Review, then send from your inbox.</h2><p>To: <strong>{CONTACT_EMAIL}</strong><br />Subject: {draft.subject}</p><textarea aria-label="Email draft text" readOnly value={draft.body} rows={15} /><div className="resource-actions"><a className="button button-primary" href={draft.mailto}>Open email app ↗</a><button type="button" className="button button-outline" onClick={copy}>Copy draft</button></div><p>This website cannot confirm email delivery. If your mail app does not open, use Copy draft or email the address directly.</p></section>}
  </div>;
}
