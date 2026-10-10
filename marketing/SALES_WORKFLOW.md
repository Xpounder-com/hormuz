# Hormuz engineering workflow inquiry to delivery

Owner: Mehrdad Zaker. Operating response target: one business day. This is a founder-operated workflow, not an automated CRM or a promise that a lead has been qualified.

## Incoming inquiries and notifications

1. Formspree stores applications and emails **mehrdadz@neuralint.io** (recipient observed in the live form settings on September 6, 2026). Email Notifications and Submission Archive are enabled. Review the inbox and Formspree submissions each business day; include the spam folder when checking a missing inquiry.
2. A successful website submission shows a receipt and random `HZ-…` reference. It offers Google Calendar booking for engineering AI work, workflow review, Cloud, appliance, onboarding, and managed-site interests. It explicitly does not promise an automatic applicant email. Formspree's paid autoresponder has not been purchased.
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
| closed_lost / not_fit | Actual decision and reason documented | Record retention review in notes |
| qa / spam | Explicit test or reviewed spam classification | Excluded from sales reports |

Every open record requires an owner, next action and date. Use this ledger for
inquiries through written agreement, plus reviewed losses, not-fit decisions,
QA, and spam. It does not support the current catalog's reservation, appliance,
subscription, activation, or refund stages. Do not classify those payments under
its older paid stages or infer qualification from a payment. Keep verified
payments, deposits, credits, subscriptions, cancellations, and refunds in Stripe;
record agreed delivery and activation evidence in private operating notes. The
ledger does not estimate close probability or recognize revenue from an inquiry.

## Review agenda and value case

Use the 30-minute discussion to confirm one engineering workflow and its coverage before choosing an existing catalog offer:

- 0–5 minutes: team, AI clients/providers, current route, and the concrete trigger for this inquiry.
- 5–15: one material control problem; current baseline and cost of the problem. Ask who operates the system and who decides on a purchase.
- 15–25: choose one measurable result, approved test data, current environment and readiness gaps. Confirm customer staff time and a realistic start window.
- 25–30: agree a named next step and date, or say why the current offer is not a fit.

Record business value as a customer estimate with assumptions: for example, hours per month spent preparing usage evidence multiplied by an agreed loaded hourly cost. Keep cost savings, risk exposure, and control coverage separate. Do not invent expected savings or convert a technical PASS into ROI.

## Pricing and evidence

Use the approved catalog only: software **$0**, Cloud **$49.99/workspace/month**,
appliance **$999**, appliance with scoped onboarding
**$1,499**, and managed site **$499/site/month**. Managed sites include the associated
Cloud workspace, with no additional Cloud subscription charge, and one hour of
remote assistance per billing month. See [commercial setup](COMMERCIAL_SETUP.md)
for the complete support allowance, business hours, exclusions, and cancellation.

These prices are owner decisions, not evidence of willingness to pay or measured
customer savings. Record objections, agreed scope, customer outcomes, and delivery
effort from actual conversations. Do not introduce another price, discount,
usage fee, seat fee, or service tier through a sales message.

## Delivery and onboarding

Appliances are coming soon, with rollout planned for early 2027. A **$49**
one-time deposit per appliance is fully refundable and credited toward the
purchase. Cancel anytime before fulfillment for a full refund. Verify deposits
and refunds in Stripe, track reservations privately, and disclose that the date
is a target. All payments use Stripe. Send reserved buyers a balance invoice
crediting the verified deposit; never collect the full catalog price again.

Before requesting the appliance balance or service payment, record the selected catalog offer, site, appliance
quantity, supported application/provider, prerequisites, acceptance checks,
named operator, delivery capacity, start date, exclusions, and cancellation terms.
Match the seller to verified payment records. Payment destinations remain blank
until their catalog amounts and billing units are verified. Customers can open
all catalog checkouts directly from the website's **Pricing & pay** page or the
matching service card. An inquiry is not required to pay. Monthly billing starts
at checkout; confirm activation before the customer subscribes. Reserved buyers
use their specific balance invoice instead of a full-price appliance button.

Follow the [scoped onboarding brief](PILOT.md). The appliance bundle includes up
to three hours of remote setup for one provider and one supported application.
Confirm a ready network and approved test data, configure the initial policies,
run agreed acceptance checks, and hand over operator instructions and open issues.

A managed site includes one hour of remote assistance per billing month, a
response within two business days during the stated business hours, monitoring,
qualified updates, and recovery guidance. Unused time does not roll over.
Record the allowance used and unresolved work. Custom development, on-site work,
round-the-clock response, replacement hardware, and work beyond the allowance
are excluded. No extra charge is authorized by this catalog.

Cloud and managed services remain inquiry-stage until technical and operating
qualification pass. Never turn a reservation deposit, a booking, or a successful demonstration
into a claim of production readiness or a guaranteed delivery commitment.

## Reviewed message drafts

**Acknowledgment** — Subject: Hormuz inquiry [reference]

Hi [name], thanks for describing [specific workflow]. I have your inquiry [reference]. The next step is a 30-minute review to understand [control need] and decide which catalog offer fits. You can choose a Wednesday or Thursday time here: [booking link]. If that schedule does not work, reply with a suitable time. Please keep credentials and customer data out of email. — Mehrdad

**After the review** — Subject: Hormuz — agreed next step for [organization]

We agreed to evaluate [one workflow] against [baseline and success measure]. [Customer owner] will confirm [environment, approved data and staff time] by [date]. I will send [scope/proposal] by [date]. The selected offer is [approved catalog offer and price], with provider usage, customer infrastructure and applicable taxes separate. Scope, capacity, delivery and terms must be confirmed before payment. Our next decision point is [date].

**Follow-up** — Send only when due and relevant, ordinarily after three business days and once more after seven. Stop after a decline or opt-out; do not subscribe the person to marketing.

Hi [name], checking whether [agreed next step] is still useful. Is [specific blocker] the open question, or should we close this for now? — Mehrdad

## Weekly operating review

Review real inquiries, time to personal reply, booked and attended reviews, qualified conversations, proposals, agreements, verified payments and lost reasons. Break out source/campaign/creative only when supplied, and label missing attribution as unknown. Compare X spend to verified business outcomes; an X Lead event is only a consented sales inquiry. See [measurement](MEASUREMENT.md) for QA and event boundaries.
