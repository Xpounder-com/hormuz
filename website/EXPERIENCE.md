# Customer experience and conversion map

The public walkthrough starts with a fictional company planning $1M/year in AI,
then follows a manager's decision: spend → Engineering → token/model driver →
candidate policy → next request → install. Existing fonts, palette, illustrations,
brand files, and companion interaction are retained.

## Capability and availability map

| Promoted capability | User and actual interface | Prerequisites and evidence | Public representation |
| --- | --- | --- | --- |
| Team/person/model/client/provider usage, token categories, cost, outcomes | Administrator; `hormuz status` CLI, JSON export | Unique identities, configured rates, routed requests; `docs/USAGE.md` | Fictional report illustration; current UTC month; configured estimates, gateway coverage only |
| Organization/team browser usage and member administration | Explicit console roles; opt-in `/console` preview | Scoped access; `docs/ADMIN_CONSOLE_LOCAL.md` | Labeled preview; no policy-editor authority |
| Active work plan, committed/pending/uncertain/available, scope and plan changes | Internal work-budget owner/report | `docs/WORK_BUDGETS.md`, current internal implementation | Preview; no public CLI or HTTP delivery claimed |
| Model/client access, output caps, scoped budgets, secret handling | Policy administrator; configuration and managed policy CLI | Real Standard/Strict/Lockdown templates, validated document, scoped admin credential for activation; `docs/POLICY_CONTROL.md` | Teaching editor; apply/undo mutate only the example, with fixed historical usage |
| Connection, client launcher, personal counters | Team member; Mac companion | Configured gateway, unique identity, supported client; `docs/MACOS_CLIENT_LOCAL.md` | Existing companion illustration plus concrete joining steps |
| Local context optimization | Team member; Client → Context optimization | v1.2.0, supported mappings, matching helper/resources; `docs/CONTEXT_OPTIMIZATION.md` | Real 127-path transform, exact restore, default-Off switch, unchanged small result |
| Mac/source/OCI downloads | Member or gateway operator | Published v1.2.0 artifacts and checksums; release source d854a5a453fcbe20cb3f4c1e261e146f2da93855 | Mac Apple Silicon/macOS 14+; Python 3.11+ source; Linux AMD64 OCI |
| Codex/OpenAI | Member and operator | Documented protocol baseline; v1.2.0 OpenAI-only live qualification | Qualified release path with version boundaries |
| Claude Code/Anthropic | Member and operator | Messages/streaming/counting protocol contract | Same-candidate v1.2 live qualification explicitly open |
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
| Header/home hero | Understand the value and begin | Install free; See plans; Explore the $1M example |
| Spend chapter | Explain the cost driver | Try Engineering output cap; inspect usage CLI |
| Policy chapter | Compare and apply a safe example | Inspect real template/validation workflow |
| Compaction chapter | Inspect exactly what changes | Download Mac; read setup/status reference |
| Setup chapter/integrations | Recognize a stack and setup role | Mac joining guide or gateway quickstart |
| Docs | Download → configure → first governed request → report | Supported client instructions or paid help |
| Plans | Choose free software, ongoing support, or pilot | Install; support terms; pilot inquiry |
| Payment | Start fixed support or finish an agreed engagement | Separate self-service checkout; retained custom/pilot links; activation and cancellation instructions |
| FAQ | Answer 32 practical questions in context | Link to the relevant example or next action |
| Resources/security/footer | Resolve evaluation questions | Install, supported setup, or paid engagement |

## Commercial boundary

The owner approved a fixed self-service support offer during this task:
$2,000/month, one self-hosted gateway, up to four support hours per billing
month, response within two business days, cancellation before the next renewal.
It uses a separate Stripe checkout from the existing agreed-proposal support
link. The public paid benefit is human support, not a proprietary entitlement.

The activation page requires Stripe confirmation and a receipt reference;
it never trusts a return URL as payment evidence. The founder verifies payment
and provides the support channel. Cancellation is available through the named
seller's email before renewal. Existing custom-support agreements and the
$15,000 one-time 90-day pilot retain their own scoped payment links. Software,
provider costs, and infrastructure responsibilities remain distinct.

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
