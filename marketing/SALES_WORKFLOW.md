# Hormuz inquiry to paid pilot

Owner: Mehrdad Zaker. Operating response target: one business day. This is a founder-operated workflow, not an automated CRM or a promise that a lead has been qualified.

## Incoming inquiries and notifications

1. Formspree stores applications and emails **mehrdadz@neuralint.io** (recipient observed in the live form settings on September 6, 2026). Email Notifications and Submission Archive are enabled. Review the inbox and Formspree submissions each business day; include the spam folder when checking a missing inquiry.
2. A successful website submission shows a receipt and random `HZ-…` reference. It offers Google Calendar booking for review, pilot, and support interests. It explicitly does not promise an automatic applicant email. Formspree's paid autoresponder has not been purchased.
3. Reply personally within the operating target, acknowledge the reference, and confirm the next step. Draft below; do not send without reviewing the person's actual request.
4. A completed Google Calendar booking sends both parties an invitation with Google Meet details. The attendee receives a one-day reminder. Booking availability: Wednesday and Thursday, 10:00–15:00 **America/Chicago**, 30-minute appointments, 60-day horizon, four-hour minimum notice. The organizer calendar is checked for busy events; email verification is required for unsigned visitors.
5. Save the reference in the private ledger. A direct booking without a website inquiry gets a local `CAL-…` reference linked to the actual calendar event. Do not count a booking-link click as a booking.

[Public review booking](https://calendar.google.com/calendar/u/0/appointments/schedules/AcZssZ2tGl0VwnWcSXzbxkYkEuryVb1t4EH-xZOk65SPpVJNdCAnmPHPhwjixF-XJp8PRhOUj03zmav1)

## Private pipeline

The working ledger is `marketing/private/leads.csv`, excluded from Git. Its directory is mode 700 and file mode 600. Keep backups private and encrypted; Git ignore is only an accidental-publication guard. Never put secrets, conversation bodies, card details, or customer test data in this ledger. Store only necessary sales notes. Review closed inquiry retention after 90 days; retain contract/payment records under the business's separate record policy.

```sh
python3 scripts/sales_pipeline.py check
python3 scripts/sales_pipeline.py report
# Prepare a reviewed, private JSON record using the columns in leads.csv, then:
python3 scripts/sales_pipeline.py upsert --input marketing/private/reviewed-inquiry.json
```

`upsert` merges on request_reference, so repeated notifications for one submission do not become duplicate leads. This is a manual import: the script neither polls Formspree nor sends email. Remove temporary import files after checking the saved record. Existing Formspree submissions should be reviewed individually before import; no prospects are fabricated or automatically qualified.

| Stage | Entry evidence | Next action |
| --- | --- | --- |
| new | Non-spam inquiry, reference and contact basis recorded | Mehrdad reviews and replies on a dated business day |
| contacted | Personal acknowledgment actually sent | Ask for booking or the missing qualification detail |
| review_booked | Calendar event confirmed | Review the workflow before the call |
| qualified | Outcome, measurable success, technical owner, decision owner, budget evidence and timing documented | Agree evaluation boundaries and proposal date |
| proposal | Written scope and unique proposal reference delivered | Dated decision conversation |
| agreed | Written acceptance and agreement reference | Verify capacity and send the corresponding payment link |
| paid_pilot | Successful Stripe payment matched to the agreement | Kickoff, day-60 review and day-90 decision scheduled |
| support | Separate support agreement and subscription/payment verified | Record renewal/cancellation owner and next review |
| closed_won / closed_lost / not_fit | Actual decision and reason documented | Record retention review in notes |
| qa / spam | Explicit test or reviewed spam classification | Excluded from sales reports |

Every open record requires an owner, next action and date. Qualification and commercial stages have required evidence fields. The ledger does not estimate close probability or recognize revenue from an inquiry.

## Review agenda and value case

Use the free 30 minutes to decide whether a scoped pilot is useful:

- 0–5 minutes: team, AI clients/providers, current route, and the concrete trigger for this inquiry.
- 5–15: one material control problem; current baseline and cost of the problem. Ask who operates the system and who decides on a purchase.
- 15–25: choose one measurable result, approved test data, current environment and readiness gaps. Confirm customer staff time and a realistic start window.
- 25–30: agree a named next step and date, or say why the current offer is not a fit.

Record business value as a customer estimate with assumptions: for example, hours per month spent preparing usage evidence multiplied by an agreed loaded hourly cost. Keep cost savings, risk exposure, and control coverage separate. Do not invent expected savings or convert a technical PASS into ROI.

## Pricing validation

Keep the displayed **$15,000 USD / 90-day pilot** and **support from $2,000 USD/month** while collecting the next 5–10 qualified conversations. These are offer hypotheses, not market-validated prices. Ask about the cost of the problem, approved budget, buying process and alternatives before asking whether the offer feels expensive. Record an explicit objection, proposal outcome, loss reason, agreed scope and delivery hours.

Review evidence at five conversations and again at ten. Separate weak fit, missing proof, insufficient urgency, procurement friction and price resistance. A pilot with excessive delivery work may need narrower scope rather than a lower price. Discount only against a concrete scope or contractual tradeoff agreed by the owner; never change live prices from click-through rate or a handful of unqualified inquiries.

## Proposal and pilot operation

Before requesting payment, record: one team/workflow; deliverables; exclusions; dependencies; named customer operator; approved synthetic/non-sensitive test data; customer staff-time budget; delivery cadence; acceptance measures; kickoff date; cancellation/refund terms; and the legal contracting entity. Explain Neuralint's relationship to Hormuz and match the seller name to the actual legal/Stripe records. The current checkout merchant and statement descriptor are disclosed in [commercial setup](COMMERCIAL_SETUP.md); resolve any mismatch in the written proposal before sending a payment request.

Preserve the [90-day pilot plan](PILOT.md). Add these commercial checkpoints to each signed mutual action plan:

- By day 15: agreed baseline, controlled workflow, readiness blockers, and a dated first evidence demonstration.
- By day 30: early evidence review against the agreed success measure; identify remaining risks while there is time to adjust.
- Day 60: review results, remaining work and whether separately priced support is useful. No automatic conversion to a subscription.
- Day 90: record acceptance evidence and a go/no-go decision. Close, extend under a written change, or start separately agreed support.

## Reviewed message drafts

**Acknowledgment** — Subject: Hormuz inquiry [reference]

Hi [name], thanks for describing [specific workflow]. I have your inquiry [reference]. The next step is a free 30-minute review to understand [control need] and decide whether a pilot would help. You can choose a Wednesday or Thursday time here: [booking link]. If that schedule does not work, reply with a suitable time. Please keep credentials and customer data out of email. — Mehrdad

**After the review** — Subject: Hormuz — agreed next step for [organization]

We agreed to evaluate [one workflow] against [baseline and success measure]. [Customer owner] will confirm [environment, approved data and staff time] by [date]. I will send [scope/proposal] by [date]. The proposed pilot fee is $15,000 USD for 90 days, with provider usage, infrastructure and applicable taxes additional. Scope, capacity and terms must be agreed before payment. Our next decision point is [date].

**Follow-up** — Send only when due and relevant, ordinarily after three business days and once more after seven. Stop after a decline or opt-out; do not subscribe the person to marketing.

Hi [name], checking whether [agreed next step] is still useful. Is [specific blocker] the open question, or should we close this for now? — Mehrdad

## Weekly operating review

Review real inquiries, time to personal reply, booked and attended reviews, qualified conversations, proposals, agreements, verified payments and lost reasons. Break out source/campaign/creative only when supplied, and label missing attribution as unknown. Compare X spend to verified business outcomes; an X Lead event is only a consented sales inquiry. See [measurement](MEASUREMENT.md) for QA and event boundaries.
