# Customer experience and conversion map

The public walkthrough starts with a fictional company planning $1M/year in AI,
then follows a manager's decision: spend → Engineering → token/model driver →
candidate policy → next request → install. Existing fonts, palette, illustrations,
brand files, and companion interaction are retained. The opening report derives
Engineering’s rounded 13% output-token share and 55% cost share from the same
fixture as the tour, labels the data as fictional, and links directly to the
spend and policy chapters. It renders without JavaScript.

## Capability and availability map

| Promoted capability | User and actual interface | Prerequisites and evidence | Public representation |
| --- | --- | --- | --- |
| Team/person/model/client/provider usage, token categories, cost, outcomes | Administrator; `hormuz status` CLI, JSON export | Unique identities, configured rates, routed requests; `docs/USAGE.md` | Fictional report illustration; current UTC month; configured estimates, gateway coverage only |
| Organization/team browser usage and member administration | Explicit console roles; opt-in `/console` preview | Scoped access; `docs/ADMIN_CONSOLE_LOCAL.md` | Labeled preview; no policy-editor authority |
| Active work plan, committed/pending/uncertain/available, scope and plan changes | Internal work-budget owner/report | `docs/WORK_BUDGETS.md`, current internal implementation | Preview; no public CLI or HTTP delivery claimed |
| Model/client access, output caps, scoped budgets, secret handling | Policy administrator; configuration and managed policy CLI | Real Standard/Strict/Lockdown templates, validated document, scoped admin credential for activation; `docs/POLICY_CONTROL.md` | Teaching editor; apply/undo mutate only the example, with fixed historical usage |
| Connection, client launcher, personal counters | Team member; Mac companion | Configured gateway, unique identity, supported client; `docs/MACOS_CLIENT_LOCAL.md` | Existing companion illustration plus concrete joining steps |
| Local context optimization | Team member; Client → Context optimization | v1.3.0, supported mappings, matching helper/resources; `docs/CONTEXT_OPTIMIZATION.md` | Real 127-path transform, exact restore, default-Off switch, unchanged small result |
| Mac/source/OCI downloads | Member or gateway operator | v1.8.0 Mac archive, source and signed OCI with separate checksums and release evidence | Mac Apple Silicon/macOS 14+; Python 3.11+ source; Linux AMD64 OCI |
| Codex/OpenAI | Member and operator | Documented protocol baseline; v1.3.0 provider-free qualification plus maintained v1.2.0 OpenAI-only live evidence | Qualified release path with version boundaries |
| Claude Code/Anthropic | Member and operator | Messages/streaming/counting protocol contract | Same-candidate v1.3.0 live qualification explicitly open |
| Ollama/other endpoints | Operator evaluating compatibility | Ollama official Responses/Messages documentation and Hormuz configurable upstream routes | Candidate only; native Ollama/Chat Completions routes absent; no GPU cost accounting claim |

## Numeric and policy example

`public/demo/customer-example.json` is the shared fictional fixture. All team and
model drilldowns reconcile through `lib/customer-example.mjs`. Current estimates
are immutable while controls change. Straight-line pace is a teaching calculation,
not a released forecasting feature or a savings prediction. Cached input is counted
once. The policy comparison separates maximum output charge, conservative request
reservation, and provider calls. An unadmitted request cannot reduce available
budget. Tests include invalid input, model access, secrets, lockdown, and undo's
underlying unchanged historical fixture.

`public/demo/compaction-example.json` contains the full original and compact
representations. Its 3,108 → 1,679 byte block measurement and exact restoration were
reproduced with v1.2.0. It makes no whole-request token, quality, or invoice claim.
The default-Off interactive switch mirrors the real setting; sample/view selectors
are teaching controls. All four panels remain mounted so switching chapters
preserves the visitor's experiment. Arrow keys, Home, and End select tabs.

## Copy and CTA map

| Surface | Job | Next step |
| --- | --- | --- |
| Home hero | Recognize the $1M buyer and see the first cost finding | Install free; Try the $1M example; Try an output cap |
| Header | Reach installation and commercial options throughout the journey | Install free; Plans |
| Spend chapter | Explain the cost driver | Try Engineering output cap; inspect usage CLI |
| Policy chapter | Compare and apply a safe example | Inspect real template/validation workflow |
| Compaction chapter | Inspect exactly what changes | Download Mac; read setup/status reference |
| Setup chapter/integrations | Recognize a stack and setup role | Mac joining guide or gateway quickstart |
| Docs | Download → configure → first governed request → report | Supported client instructions or paid help |
| Plans | Choose software, Cloud, an appliance, or a managed site | Install; request Cloud; discuss appliance scope |
| Payment | Confirm the selected catalog offer | Qualified delivery; verified checkout; activation and cancellation instructions |
| FAQ | Answer 38 practical questions in context | Link to the relevant example or next action |
| Resources/security/footer | Resolve evaluation questions | Install, supported setup, or paid engagement |

## Commercial boundary

The canonical price catalog is `lib/pricing.json`: software $0, Cloud
$49.99/workspace/month, appliance $999, appliance with
scoped onboarding $1,499, and managed site $499/site/month. Onboarding includes
up to three hours of remote setup for one provider and one supported application.
Managed sites include the associated Cloud workspace and one hour of remote
assistance per billing month, with a response within two business days.

Business hours are Monday–Friday, 9 am–5 pm America/Chicago, excluding local
public holidays. Unused support time does not roll over. On-site work, custom
development, round-the-clock response, replacement hardware, and work beyond
the allowance are excluded. Provider usage, customer infrastructure, and
applicable taxes are separate. Monthly services renew until canceled before
the next renewal through the owner's email.

Appliances are coming soon, with rollout planned for early 2027. The $49
one-time reservation deposit is fully refundable and credited toward purchase;
customers can cancel anytime before fulfillment for a full refund. All payments
use Stripe. **Pricing & pay** links directly to all five paid checkouts, with
matching buttons on service cards and in the inquiry selector. Software has a
free install path. Submitting an inquiry is optional for checkout; activation
and full-price hardware purchases still require confirmed scope and delivery.
A checkout click or inquiry receipt is not a paid reservation.
Payment destinations stay blank until verified against this catalog. The static
site does not verify payment, provision accounts, or qualify hardware. Cloud,
ARM64 appliance delivery, and managed monitoring require qualification before
activation. See `marketing/COMMERCIAL_SETUP.md` for the complete scope.

## Measurement and verification

Existing opt-in X Ads inquiry measurement and native Formspree fallback remain.
The new tour and search do not send telemetry or customer content. Installation
intent, verified activation, checkout intent, verified payment, and service delivery
are different stages; the static site does not manufacture those conversions.
There is no new payment callback/backend or provider call in the tour.

Run the existing Node/Python website checks, production export/typecheck/link
verification, and the customer-example tests. Browser acceptance exercises team
and model filters, policy caps/denials/apply/undo, compaction/restore/unchanged,
Ollama/client mismatch, install and payment navigation, question search, keyboard
tabs, no-JavaScript fallback, desktop/mobile, Chromium, and WebKit. Publication
uses the normal source PR and an exact commit pin in the Pages repository.
