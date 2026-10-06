# Hormuz commercial setup

Approved October 6, 2026. The website price catalog is
[website/lib/pricing.json](../website/lib/pricing.json). Pages, inquiry summaries,
FAQs, and buyer-download builders consume that catalog.

## The complete offer

| Offer | Price in USD | Included scope |
| --- | --- | --- |
| Software | **$0** | Personal Optimizer and Apache-2.0 gateway; customer operates their infrastructure |
| Cloud | **$49.99 per workspace per month** | One hosted workspace and dashboard, administrative users included |
| Appliance | **$999 per appliance, one time** | SSD, enclosure, cooling, power supply, and installed Hormuz gateway |
| Appliance with scoped onboarding | **$1,499 per appliance, one time** | The same SSD-equipped appliance plus up to three hours of remote onboarding |
| Managed site | **$499 per site per month** | Associated Cloud workspace, device-health monitoring, qualified updates, recovery guidance, and the support allowance below |

The onboarding bundle includes the appliance; it replaces the standalone
appliance purchase for that unit. The managed-site fee includes its associated
Cloud workspace, with no additional Cloud subscription for that workspace.
Additional appliances at the same agreed site do not add a site subscription.
Confirm supported workloads and capacity before adding devices.

Model-provider usage is billed by the customer's provider. Customer networking,
electricity, self-hosted infrastructure, and applicable taxes remain separate.
No additional Hormuz usage, seat, software-edition, or separately priced support
tier is offered.

## Appliance reservations

Appliances are **coming soon**, with rollout planned for **early 2027**. This is
a target, not a guaranteed shipping date. The **$49 USD one-time deposit per
appliance** is fully refundable and credited toward either appliance package.
It is a reservation deposit, not another hardware tier or a subscription.
No automatic balance charge is authorized.

Customers can **cancel anytime before fulfillment for a full refund** by emailing
**mehrdadz@neuralint.io** with their Stripe receipt reference. Verify the payment,
refund the full reservation payment, including any tax collected, to the original payment method through Stripe, and mark
the reservation canceled. Refund settlement depends on the processor and payment
method. Never retain a fee from the advertised full refund.

Stripe's existing automatic tax collection remains enabled. The checkout
displays any applicable tax separately. The catalog deposit amount is credited
toward the hardware purchase; reconcile any tax already collected on the final
invoice instead of charging it twice.

Confirm compatibility, the chosen package, delivery arrangements, applicable
taxes, and the customer's decision to proceed before collecting the balance
through Stripe. Use a Stripe invoice that explicitly credits the verified
deposit against the catalog purchase price. Do not send a reserved customer a
full-price appliance Payment Link and collect the deposit twice. If delivery
cannot proceed, refund the deposit. Track payment and refund references privately;
never put customer-specific receipt or invoice URLs in public source.

The deposit-credit workflow is manual. Match the successful, unrefunded Stripe
payment to the customer and chosen package, create that customer's draft
appliance invoice, and add a negative `Reservation deposit applied` invoice item
to that specific draft invoice. Check the remaining balance and any previously
collected tax before finalizing and sending its Stripe payment page. Record the
deposit as applied once; a canceled or refunded reservation receives no credit.
Do not add a generic customer credit balance: Stripe applies that balance to the
next finalized invoice, which could be a Cloud renewal. Stripe documents the
[invoice-specific item](https://docs.stripe.com/api/invoiceitems/create) and
[customer balance behavior](https://docs.stripe.com/invoicing/customer/balance).
No automatic deposit reconciliation or fulfillment is implemented by the static
website.

## Scoped onboarding

Up to three hours of remote assistance for one provider and one supported
application: prerequisites, initial identity and policy configuration,
allowed/denied request acceptance checks, and administrator handoff.
The customer supplies a ready network, power, authorized provider account,
approved test inputs, and a named operator. Custom integration, network redesign,
data migration, on-site visits, and ongoing operations are excluded.
Document open issues when the included allowance is exhausted. Confirm scope,
acceptance, prerequisites, delivery arrangements, and the start date before payment.

## Managed-site support allowance

One agreed physical location or isolated customer deployment, recorded at
activation, includes up to **one hour of remote assistance per billing month**.
Count calls, troubleshooting, investigation, and configuration work against this
allowance. Unused time does not roll over.

Support hours: Monday–Friday, 9 am–5 pm America/Chicago, excluding local public
holidays. Response target: **within two business days**, meaning acknowledgment
and a next step, with no guaranteed resolution time.

Included: configuration and policy guidance, troubleshooting, recovery guidance,
and assistance with qualified updates. Excluded: custom development, on-site
work, round-the-clock response, replacement hardware, and work beyond the
allowance. Document unresolved work and agree the next action before proceeding.
This catalog does not authorize extra charges.

## Availability and activation

Software remains available through supported installation paths.
Cloud and managed-site services are accepting inquiries. Appliances and
onboarding are coming soon; refundable reservations do not establish delivery
readiness. Confirm appliance ARM64 packaging,
capacity, thermal behavior, updates, and recovery before hardware delivery.
Cloud onboarding, billing, compatibility, traffic limits, and operating coverage
must be qualified before activation. Managed monitoring must be implemented and
validated before promising coverage to a paying site.

The static website submits inquiries through Formspree and offers a public
review booking. It does not provision accounts, verify payments, or activate
subscriptions. The founder verifies confirmed payment, records the covered
site/appliances, and supplies the support channel. Never infer payment from a
return URL or charge again after an ambiguous result without checking.

## Public destination configuration

[website/lib/commercial-config.mjs](../website/lib/commercial-config.mjs) owns
public destination URLs. Inquiry and booking destinations remain configured.
All paid offers use Stripe. Payment destinations stay blank until their merchant,
catalog amount, billing interval, and terms are verified. The website's
**Pricing & pay** page exposes every paid offer's checkout; the matching service
cards and inquiry selector also link directly to Stripe. Customers do not need
to submit an inquiry to open checkout. Software remains free with an install
button and no card requirement.

Reservation checkout is available before appliance rollout. Full-price appliance
checkouts are for confirmed orders without a reservation deposit; reserved
buyers use their specific Stripe balance invoice. Cloud and managed-site billing
starts at checkout, so confirm compatibility, scope, operating capacity, start
date, and applicable taxes before the customer subscribes. Checkout does not
provision a workspace or confirm shipping. Public configuration URLs do not
enforce activation eligibility.

| Setting | Destination | Verification before enabling |
| --- | --- | --- |
| formEndpoint | Formspree form | Correct owner, domain restriction, positive acknowledgment, privacy behavior |
| bookingUrl | Public scheduling page | Correct organizer, timezone, availability, invitation behavior |
| reservationPaymentUrl | Live Stripe Payment Link | USD 49 one time per appliance; fully refundable, credited toward purchase; early 2027 target and cancellation contact disclosed |
| cloudPaymentUrl | Live Stripe Payment Link | USD 49.99 monthly per workspace; confirmed activation and cancellation terms |
| appliancePaymentUrl | Live Stripe Payment Link | USD 999 one time per SSD-equipped appliance; confirmed delivery arrangements |
| onboardingPaymentUrl | Live Stripe Payment Link | USD 1,499 one time per appliance with the stated onboarding bundle |
| managedSitePaymentUrl | Live Stripe Payment Link | USD 499 monthly per managed site; included Cloud and support allowance disclosed |

Superseded payment links are removed from the website source. New Stripe
products and Payment Links use this catalog; existing Stripe products, active
subscriptions, and customer agreements have not been repriced. Only enable a
new destination after verifying its merchant,
amount, quantity unit, billing interval, scope, and cancellation terms.

Cloud and managed sites renew monthly until canceled. The customer emails
**mehrdadz@neuralint.io** before the next renewal with their subscription reference.
Cancellation stops the next renewal; the current paid period remains available.
Appliance ownership and free software access remain available after cancellation.

## Verification and publication

Run website tests, type checking, production export, link verification, and buyer
download checks. Render revised PDFs and the editable deck. Check every current
commercial surface against the catalog, including inquiry options and the demo
walkthrough. Keep provider-spend teaching examples separate from Hormuz prices.

A product-source update does not publish the canonical website. Publish only
through the dedicated website repository's reviewed source pin and verify the
live pin and downloads afterward. Do not claim payment setup, delivery readiness,
customer demand, or measured savings from a pricing-copy change.
