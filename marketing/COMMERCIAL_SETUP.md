# Enterprise conversion setup

Approved offer, September 6, 2026:

- Free AI governance review to establish fit.
- 90-day pilot: $15,000 USD total, one team and one workflow.
- Ongoing enterprise support: from $2,000 USD/month, separately agreed scope.
- Provider usage, infrastructure, and applicable taxes are additional.
- No automatic conversion from pilot to subscription; no implied 24/7 support.

## Public configuration

Set only verified public destinations in `website/lib/commercial-config.mjs`:

| Setting | Purpose | Activation check |
| --- | --- | --- |
| formEndpoint | Formspree `/f/<id>` endpoint | Owner identity, recipient, domain restriction, spam filtering, and test receipt verified |
| bookingUrl | Public Calendly or Cal.com event | Correct owner, availability, timezone, duration, and notification recipient |
| pilotPaymentUrl | Public live Stripe Payment Link | Correct merchant, USD 15,000, one-time, fixed quantity, no optional upsells |
| supportPaymentUrl | Public live Stripe Payment Link | Correct merchant, USD 2,000 monthly, fixed quantity, disclosed recurring and cancellation terms |

Blank destinations remain unavailable. The contact page uses the existing email
fallback until an endpoint is connected. No credentials belong in this public
configuration. Customer-specific invoice links must be shared privately and must
never be committed here. Payments are shown under Enterprise only for customers
with agreed scope and start date; they do not provision a hosted service.

## Account setup and acceptance

1. Confirm the existing Stripe merchant is the appropriate seller for Hormuz.
   Review current account status before creating products. Do not change banking,
   legal identity, tax registrations, or accept new terms as part of website work.
2. Create or reuse `Hormuz Enterprise — 90-Day Pilot` at USD 15,000 one-time and
   `Hormuz Enterprise Support` at USD 2,000/month. State scope and exclusions in
   descriptions. Final support hours, cancellation/refund terms, and tax settings
   must match the agreement; do not invent them from the starting price.
3. Verify checkout in Stripe test mode, including a failure/cancel path, then
   inspect the live Payment Links without making a real charge. Only configure
   the matching live links after verification. A Stripe success redirect is not
   evidence of a completed payment; inspect Stripe’s payment/subscription record.
4. In the owner’s Formspree account, create a private Hormuz enterprise form.
   Configure the owner-approved notification inbox, domain allowlist, spam
   protection, appropriate account access, and retention policy. Submit a clearly
   marked synthetic inquiry; confirm the record and notification. Do not count it
   as a customer lead. Frontend tests alone do not verify delivery.
5. Connect an existing booking event or agree availability and timezone before
   creating one. Follow any authentication or terms prompts with the owner.
6. Run `npm test`, `npm run build`, `npm run typecheck`, and `npm run verify`.
   Check desktop/mobile CTA navigation, validation, pending/error states, receipt,
   booking destination, checkout amount/currency/interval, and privacy copy.
7. Publish via the repository’s review and Pages workflow. The canonical
   `usehormuz/usehormuz.github.io` deployment pins a source revision; updating the
   product repository alone does not publish the canonical site. Verify the live
   source pin and every enabled commercial destination after publication.

## Measurement

Application tags (`utm_source`, `utm_medium`, `utm_campaign`) are bounded and
forwarded along commercial CTAs. Form submission includes them only when the
visitor selects the unchecked attribution checkbox. No cookies, analytics SDK,
ad pixels, click IDs, or persistent visitor IDs are added. Application contents
are never sent in an analytics event. Use verified non-spam submissions, actual
bookings, successful payments, and active subscriptions as distinct funnel stages.
Ad-platform conversion import remains an account-dependent follow-up.

## Current activation status

Code prepared; external destinations blank. Stripe requires account authentication.
Formspree and booking account/destination are not yet identified. Live submission,
notification delivery, checkout, recurring billing, and publication remain unverified.
