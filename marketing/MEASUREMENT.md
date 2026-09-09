# Measurement without hidden telemetry

Current implementation: **X Ads measurement requires visitor opt-in and is disabled
in Safari and browsers on iPhone and iPad**. The site
uses the owner's X pixel `rf0s7` and Lead event `tw-rf0s7-rf0s8` ("Hormuz
application received"). Formspree accepts enterprise inquiries through the
separate Hormuz project. No product telemetry is collected by this website.
GitHub Pages may retain hosting/security logs; see the website privacy notice.
X counts consented browser events, not all inquiries or qualified customers.

## Useful signals and exact definitions

| Signal | Count only when | Do not substitute |
| --- | --- | --- |
| Independent demo completion | The existing study accepts a qualifying exact-archive session with appropriate evidence | A website click, recording play, assisted demo, or internal run |
| Returning evaluator | A qualifying evaluator completes the study's returning-user criterion | Repeat pageviews |
| Qualified buyer conversation | An actual conversation identifies a workflow, responsible owner, material control need, and possible next step | An email draft, generic interest, or star |
| Pilot agreed | Scope, capacity, commercial terms, and acceptance are agreed by the parties | Download, inquiry, or proposed brief |
| Pilot outcome | Agreed acceptance evidence and go/no-go decision are recorded | Unmeasured ROI or token consumption |

Suggested weekly review: source of each actual conversation, one named problem,
next step and owner, blockers, and evaluator friction. Do not infer individual
productivity from usage data. Keep study evidence separate from sales notes.

## Consent-aware source handling

Campaign URLs may use bounded `utm_source`, `utm_medium`, `utm_campaign`, and `utm_content`
values, for example `?interest=pilot&utm_source=x&utm_medium=paid_social&utm_campaign=enterprise_pilot`.
The browser sends the requested URL, including its query string, to GitHub Pages
when it loads the page; those values can be processed in hosting/security logs.
The contact page then reads them locally and offers an **unchecked** checkbox to
include them in the Formspree application. This choice is independent of X Ads
consent. Internal page links retain only those four bounded campaign tags in the
URL, without storing them. The site does not forward `twclid` in its CTA links;
after consent, X's SDK handles its own click identifier and cookies.

## Private lead ledger

Use `marketing/private/leads.csv` with `scripts/sales_pipeline.py`; see
[sales workflow](SALES_WORKFLOW.md) for stages, required evidence, and follow-up drafts.
`marketing/private/` is gitignored as an accidental-publication guard, not a
security boundary. Never commit real contacts, customer notes, or credentials.
Store only consented/necessary contact information, source, workflow,
qualification, next action, and retention-review date. Agree access and retention
before use. No real lead records were created by this task.

## X Ads consent and event contract

- No X SDK, cookies, or queued events before affirmative opt-in. Decline and
  browser Global Privacy Control/Do Not Track keep measurement off.
- Safari and browsers on iPhone and iPad never load the X SDK. This reliability
  boundary keeps optional third-party code out of the WebKit process serving the
  application path; the form, booking links, and payment links remain available.
- A non-identifying consent preference is stored for up to 180 days. With blocked
  storage, a choice lasts only for the page. Footer controls reopen preferences.
- Withdrawal reloads the page to unload the SDK. The UI warns about unsent form
  entries before the visitor chooses withdrawal. Existing X cookies/data may
  remain; the privacy notice links X's cookie and advertising opt-out controls.
- Only the canonical origin reports. Local previews cannot pollute production.
- Page-location suppression is set before pixel configuration. No application
  fields, hashed identities, prices, or campaign tags are passed as event data.
- A single Lead event follows `submitLead` resolving with HTTP success and
  `ok: true` for a review, pilot, or support inquiry. General integration,
  security, and community inquiries and explicit QA mode do not trigger it.
  Validation failures, rejected/ambiguous responses, honeypot input,
  button clicks, and repeated renders do not create conversion events.
- A blocked pixel cannot prevent saving an inquiry. The event is not retroactively
  queued for a visitor who grants consent after submitting.
- X's default attribution is 30-day post-engagement and 1-day post-view; website
  activity audience is off. Existing ad budgets and campaigns are unchanged.

Implementation follows [X's conversion documentation](https://business.x.com/en/help/campaign-measurement-and-analytics/conversion-tracking-for-websites)
and [privacy controls](https://business.x.com/en/help/campaign-measurement-and-analytics/conversion-tracking-for-websites/about-conversion-tracking).

## Commercial funnel activation

The approved offer is a $15,000 USD 90-day pilot for one team/workflow and
ongoing enterprise support from $2,000 USD/month under a separate agreement.
See [activation and verification](COMMERCIAL_SETUP.md) for public destination configuration.

- Application received: the form service acknowledges the submission; verify a
  non-spam record in the private dashboard before counting it as an actual lead.
- Review booked: a booking appears in the calendar service, not a booking-link click.
- Pilot paid: a successful live Stripe payment for the agreed pilot, not a return URL.
- Support subscribed: an active subscription and successful initial payment in Stripe.

The X Lead event measures consented, acknowledged sales inquiries. Explicit
`?qa=1` submissions are visibly marked, excluded from the event, and must be
classified `qa` in the private ledger. Unflagged service-accepted spam can still
trigger an event; exclude it from the sales ledger after review.
Inspect Events Manager activity after publication; a browser request alone does
not prove ad attribution. No purchase, booking, or subscription pixel is installed.
