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
   The website request must retain `strict-origin-when-cross-origin`: Formspree's
   domain restriction uses the Referer header and marks submissions without it as
   spam. This sends only the HTTPS website origin on the cross-origin request,
   excluding paths and campaign query parameters. See [Formspree's domain
   restriction documentation](https://help.formspree.io/articles/form-and-project-settings/restrict-to-domain).
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
visitor selects the unchecked attribution checkbox. Separately, visitor consent
enables X Ads measurement of visits and acknowledged applications. Application contents
are never sent in an analytics event. Use verified non-spam submissions, actual
bookings, successful payments, and active subscriptions as distinct funnel stages.
See [measurement setup](MEASUREMENT.md) for consent and event details.

## Current activation status

Stripe products and live Payment Links were created and inspected in the owner's
signed-in Safari session on September 6, 2026:

- Pilot: USD 15,000 one-time, fixed quantity, no upsells or renewal.
- Support: USD 2,000 monthly, fixed quantity, no trial or upsells.
- Both require customer name, business name, and an agreed proposal reference.
- Both public checkout pages loaded with the expected product, amount, and interval.
- The existing automatic tax setting and preset product tax category were retained;
  this does not validate tax classification or registrations.
- Mercury was visibly listed as the default USD payments-balance payout account.
  No banking details were changed and no payout was initiated.
- Successful-payment email notifications were enabled for the signed-in Stripe user.
  Actual delivery has not been tested.
- The merchant is AI and Robotics Solutions; the existing statement descriptor is
  LINKEDFULL.COM. The draft website discloses this without rebranding other products.

The live Stripe links were published with the full-site design at source
`af578c4a9519614856f4c6bb4bb7919995476e82`. No live charge, test-mode transaction,
subscription lifecycle, or refund has been exercised.

Formspree configuration, September 6, 2026:

- Separate project `Hormuz`, form `Hormuz Enterprise Applications`, endpoint
  `https://formspree.io/f/xoeqnbyq`.
- Verified owner inbox `zaker.mehrdad@gmail.com`; email notifications, submission
  archive, and Formshield enabled. Domain restricted to `usehormuz.github.io`.
- Current free account quota: 50 submissions/month shared across forms; dashboard
  archive: 30 days. Existing unrelated forms were not changed.
- Applicant autoresponses require Professional: USD 30 monthly, or USD 240/year.
  No upgrade has been purchased; on-page receipt works without an upgrade.
- Booking remains blank until the owner's event/availability is supplied.
- Live form record, owner email delivery, and X event activity must be verified
  after this source revision is published. Configuration is not delivery proof.
