# Measurement without hidden telemetry

Current implementation: **marketing tracking is off**. The static site has no
analytics SDK, ad pixel, visitor ID, or product-telemetry collector. A Formspree
application integration is available but inactive until a verified endpoint is configured.
GitHub Pages may retain hosting/security logs; see the website privacy notice.
No conversion rate is available from this implementation.

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

Campaign URLs may use bounded `utm_source`, `utm_medium`, and `utm_campaign`
values, for example `?interest=pilot&utm_source=linkedin&utm_medium=founder&utm_campaign=oss_evaluation`.
The browser sends the requested URL, including its query string, to GitHub Pages
when it loads the page; those values can be processed in hosting/security logs.
The contact page then reads them locally and offers an **unchecked** checkbox to
include them in the user-reviewed email draft. The application sends no analytics event;
UTM support is not an installed analytics system. Application CTAs retain only those three bounded campaign tags in the URL,
without browser storage. When Formspree is connected, the same unchecked
choice includes tags in the application payload instead of an email draft.

## Private lead ledger

Copy `templates/leads.example.csv` to an access-controlled, private location.
`marketing/private/` is gitignored as an accidental-publication guard, not a
security boundary. Never commit real contacts, customer notes, or credentials.
Store only consented/necessary contact information, source, workflow,
qualification, next action, and retention-review date. Agree access and retention
before use. No real lead records were created by this task.

## Before enabling an analytics provider

The owner must choose the service/account, define lawful/appropriate collection
and consent requirements, retention and access, and approve the event schema.
Suggested minimal events: anonymous page category and explicit CTA action,
without form text, identity, prompts, credentials, persistent cross-site IDs, or
product usage. Verify opt-out/consent and the privacy notice before deployment.
Do not label `email_draft_prepared` as `lead_submitted` or `meeting_booked`.

## Commercial funnel activation

The approved offer is a $15,000 USD 90-day pilot for one team/workflow and
ongoing enterprise support from $2,000 USD/month under a separate agreement.
See [activation and verification](COMMERCIAL_SETUP.md) for public destination configuration.

- Application received: the form service acknowledges the submission; verify a
  non-spam record in the private dashboard before counting it as an actual lead.
- Review booked: a booking appears in the calendar service, not a booking-link click.
- Pilot paid: a successful live Stripe payment for the agreed pilot, not a return URL.
- Support subscribed: an active subscription and successful initial payment in Stripe.

There is no ad-platform conversion integration yet. Do not optimize ads against
email drafts or payment-link clicks. Connect the actual advertising account and
approve its consent/data flow before importing verified lead or purchase events.
