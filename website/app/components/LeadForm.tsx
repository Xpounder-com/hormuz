"use client";

import { CampaignLink } from './CampaignLink';


import { useEffect, useRef, useState, type FormEvent } from 'react';
import { INTERESTS, campaignSource, isSalesInquiry, normalizeInterest } from '../../lib/contact.mjs';
import { prepareLeadAttempt, submitLead } from '../../lib/lead.mjs';
import { trackConfirmedApplication } from '../../lib/x-ads.mjs';
import { trackAnalyticsLead } from '../../lib/analytics.mjs';
import { CLOUD_PRICE, APPLIANCE_PRICE, ONBOARDING_PRICE, MANAGED_SITE_PRICE, RESERVATION_PRICE, commercial, pricing } from '../../lib/commercial.mjs';
import { CONTACT_EMAIL, sitePath } from '../../lib/site.mjs';
import { StripeCheckoutLink, type PaidOffer } from './StripeCheckoutLink';

const offers: Record<string, { price: string; detail: string; action: string }> = {
  reservation: { price: RESERVATION_PRICE, detail: `USD/appliance · one-time refundable deposit · credited toward purchase · planned rollout: ${pricing.reservation.plannedRollout}`, action: 'Send my reservation question →' },
  cloud: { price: `${CLOUD_PRICE}/month`, detail: 'USD/workspace · hosting and administrative users included · accepting inquiries', action: 'Discuss my Cloud workspace →' },
  appliance: { price: APPLIANCE_PRICE, detail: `USD/appliance, one time · Preconfigured gateway · coming soon: ${pricing.reservation.plannedRollout}`, action: 'Discuss my appliance →' },
  onboarding: { price: ONBOARDING_PRICE, detail: `USD/appliance, one time · Appliance and up to ${pricing.onboarding.remoteHours} hours of scoped remote onboarding included · coming soon: ${pricing.reservation.plannedRollout}`, action: 'Discuss my onboarding →' },
  managed: { price: `${MANAGED_SITE_PRICE}/month`, detail: `USD/site · Cloud workspace and ${pricing.managedSite.supportHoursPerMonth} hour of remote assistance per billing month included`, action: 'Discuss my managed site →' },
};

const paymentLabels: Record<PaidOffer, string> = {
  reservation: `Reserve yours — ${RESERVATION_PRICE}`,
  cloud: `Subscribe to Cloud — ${CLOUD_PRICE}/month`,
  appliance: `Pay for appliance — ${APPLIANCE_PRICE}`,
  onboarding: `Pay for onboarding package — ${ONBOARDING_PRICE}`,
  managed: `Subscribe to managed site — ${MANAGED_SITE_PRICE}/month`,
};

export function LeadForm({ endpoint, bookingUrl }: { endpoint: string; bookingUrl: string }) {
  const [interest, setInterest] = useState('work');
  const [search, setSearch] = useState('');
  const [includeSource, setIncludeSource] = useState(false);
  const [state, setState] = useState<'idle' | 'sending' | 'success' | 'error'>('idle');
  const [error, setError] = useState('');
  const [reference, setReference] = useState('');
  const requestReference = useRef('');
  const testSubmission = new URLSearchParams(search).get('qa') === '1';
  const selectedOffer = offers[interest];
  const selectedPayment = Object.hasOwn(paymentLabels, interest) ? interest as PaidOffer : null;
  const pending = useRef(false);
  const confirmation = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    setSearch(window.location.search);
    const selected = new URLSearchParams(window.location.search).get('interest');
    if (selected) setInterest(normalizeInterest(selected));
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
      if (!payload._gotcha) {
        trackConfirmedApplication(window, { interest, testSubmission });
        trackAnalyticsLead(window, { interest, testSubmission });
      }
    }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Receipt could not be confirmed. Please contact us by email.'); setState('error'); }
    finally { pending.current = false; }
  }
  if (state === 'success') return <section className="draft-panel" aria-label="Application received">
    <h2 ref={confirmation} tabIndex={-1}>{testSubmission ? 'Test inquiry received.' : 'Your inquiry is received.'}</h2>
    <p>Request reference: <strong>{reference}</strong>. Save this reference to help us find your inquiry.</p>
    <p>Mehrdad will review your workflow and reply personally. Response target: one business day. This page is your receipt; an automatic application email is not sent.</p>
    {bookingUrl && isSalesInquiry(interest) && <><p>Choose a 30-minute Google Meet review on Wednesday or Thursday, 10 am–3 pm Central. Include your request reference when booking.</p><a className="button button-primary" href={bookingUrl} rel="noreferrer">Choose my discussion time ↗</a><p>Google Calendar emails both of us an invitation after you complete the booking.</p></>}
    <p>No payment has been taken. A meeting is confirmed only after booking; any paid scope is agreed separately. Questions? <a href={`mailto:${CONTACT_EMAIL}?subject=${encodeURIComponent(`Hormuz inquiry ${reference}`)}`}>Email Mehrdad</a>.</p>
    {interest === 'reservation' && <p>This is a received inquiry. A paid appliance reservation is confirmed separately through Stripe after successful deposit payment. <CampaignLink href={sitePath('/enterprise/#reserve')}>Reservation details →</CampaignLink>.</p>}
  </section>;
  return <div className="contact-flow">
    {testSubmission && <p role="status" className="form-status">QA test mode: this sends a clearly marked test inquiry to the owner. It does not count as an X Ads lead.</p>}
    <form className="contact-form" action={endpoint} method="post" onSubmit={submit} aria-busy={state === 'sending'}>
      <fieldset disabled={state === 'sending'} className="lead-fields">
        <legend className="sr-only">Hormuz inquiry</legend>
        <input type="hidden" name="_subject" value="Hormuz website inquiry" />
        <label>I am interested in<select name="interest" value={interest} onChange={e => setInterest(e.target.value)}>{Object.entries(INTERESTS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        {selectedOffer && <div className="selected-offer" aria-live="polite"><strong>{selectedOffer.price}</strong><span>{selectedOffer.detail}</span>{selectedPayment && <StripeCheckoutLink offer={selectedPayment}>{paymentLabels[selectedPayment]}</StripeCheckoutLink>}<p className="field-hint">The checkout button opens Stripe directly. You can pay without submitting this inquiry form. Payment is confirmed separately by Stripe.</p></div>}
        {interest === 'reservation' && <div className="reservation-inquiry"><p>Cancel your reservation anytime before fulfillment for a full refund. The deposit is credited toward either appliance package. This form sends a question and takes no payment.</p>{!commercial.reservationPaymentUrl && <p className="field-hint">Stripe checkout is being prepared. We will reply when a verified reservation checkout is available.</p>}<p className="field-hint"><CampaignLink href={sitePath('/enterprise/#reserve')}>Read reservation and cancellation terms →</CampaignLink>.</p></div>}
        {['appliance', 'onboarding'].includes(interest) && <p className="field-hint">Coming soon: {pricing.reservation.plannedRollout}. Use full-price checkout only after scope and delivery are confirmed, without a reservation deposit. If you have reserved, pay the Stripe balance invoice provided by Hormuz. <CampaignLink href={sitePath('/enterprise/#reserve')}>Reservation details →</CampaignLink>.</p>}
        {interest === 'cloud' && <p className="field-hint">Confirm compatibility, traffic limits, support coverage, and activation before subscribing. Provider usage is separate. Monthly billing starts at checkout and renews until canceled. <CampaignLink href={sitePath('/plans/#cloud')}>Cloud and cancellation details →</CampaignLink>.</p>}
        {interest === 'managed' && <p className="field-hint">Confirm site scope, operating capacity, and activation before subscribing. Monthly billing starts at checkout and renews until canceled. Includes the associated Cloud workspace. <CampaignLink href={sitePath('/enterprise/#support')}>Support allowance and cancellation →</CampaignLink>.</p>}
        <label>Your name<input name="name" autoComplete="name" required maxLength={100} /></label>
        <label>Work email<input name="email" type="email" autoComplete="email" required maxLength={254} /></label>
        <label>Organization<input name="organization" autoComplete="organization" required maxLength={150} /></label>
        <label>Your agents, spending concern, and priority<textarea name="workflow" required maxLength={1200} rows={4} aria-describedby="lead-safety" placeholder="For example: Codex across several repositories, API-billed usage, and a monthly spending limit; speed matters within that limit." /></label>
        <p className="field-hint" id="lead-safety">Do not include credentials, prompts, customer data, or other secrets.</p>
        <label>Timing <span>(optional)</span><input name="timeframe" maxLength={100} placeholder="For example: this quarter" /></label>
        <div className="lead-trap" aria-hidden="true"><label>Leave this empty<input name="_gotcha" tabIndex={-1} autoComplete="off" /></label></div>
        {campaignSource(search) && <label className="checkbox-label"><input type="checkbox" checked={includeSource} onChange={e => setIncludeSource(e.target.checked)} />Include campaign source with my application: {campaignSource(search)}</label>}
        <p className="field-hint">Submitting sends these details to Hormuz through Formspree so we can respond to this inquiry. It does not subscribe you to marketing emails. <CampaignLink href={sitePath('/privacy/')}>Privacy details</CampaignLink>.</p>
        <p className="field-hint">Personal reply from Mehrdad. Response target: one business day. {bookingUrl && isSalesInquiry(interest) ? 'After submitting, choose a 30-minute workflow discussion.' : ''}</p>
        <button className="button button-primary" type="submit">{state === 'sending' ? 'Sending…' : selectedOffer?.action || 'Send my inquiry →'}</button>
      </fieldset>
    </form>
    {state === 'sending' && <p role="status">Sending your application…</p>}
    {state === 'error' && <p role="alert" className="form-status">{error} {reference && <>Reference: <strong>{reference}</strong>. </>}<a href={`mailto:${CONTACT_EMAIL}?subject=${encodeURIComponent(`Check Hormuz inquiry ${reference}`)}`}>Email Mehrdad</a>.</p>}
    <noscript><p>The form will submit directly to Formspree. You can also email <a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a>.</p></noscript>
  </div>;
}
