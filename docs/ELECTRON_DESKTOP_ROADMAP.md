# Hormuz Electron desktop roadmap

Planning baseline: 2026-10-13, main
`46449d2a3f05de09d7d0a18c9688e189a6c3a68e`.
Track delivery in [epic #479](https://github.com/Xpounder-com/hormuz/issues/479).
The [UI plan](DESKTOP_UI_PLAN.md),
[UI-facing backend boundaries](DESKTOP_BACKEND_BOUNDARIES.md),
[Research Review contract](RESEARCH_REVIEW_MODE.md),
[account cleanup](DESKTOP_ACCOUNT_CLEANUP.md),
[infrastructure audit](DESKTOP_INFRASTRUCTURE_AUDIT.md)
and [backlog reconciliation](DESKTOP_BACKLOG_RESET_2026-10-13.md) define the
active scope and the disposition of the original open issues.

Application work remains planning and simulated UI exploration. This roadmap
does not implement the host/backend, read real project context, download models,
call providers or publish a release. A separate explicit application work order
starts real implementation. Account cleanup has its own authorized operations
and evidence below; it is not covered by a blanket claim of no service changes.
Release identity, dates and implementation owners remain unassigned.

README cleanup is part of the planning/documentation scope in #479. The
[repository introduction](../README.md) now leads with the planned desktop
workspace, minimal bar, local/cloud Auto and optional Research Review. It labels
simulation versus available gateway/legacy clients, retains runnable quickstart
anchors, and directs operational detail to the existing guides. Keep this
overview aligned with later accepted UI/runtime/release evidence; the rewrite
does not claim that the desktop has shipped or replace the gateway contracts.

Local cleanup [#504](https://github.com/Xpounder-com/hormuz/issues/504) is complete
within its audited scope: old merged checkouts and test exports were retired
with verified recovery, while unique work and release assets remain preserved.
See the [local checkpoint](DESKTOP_LOCAL_CLEANUP.md). This does not start the UI
implementation, host/backend, model setup or a release.

## Product outcome

One shared Electron + Svelte/TypeScript interface for declared Windows, Linux
and macOS targets. The user confirmed a **full project workspace**: each project
owns its chats, files, instructions and model/Auto preferences. A normal desktop
window provides project navigation, continuing conversations, history/search,
visible context, model management, connections and settings.

The global quick bar remains small and glossy, with curved edges, refined type,
depth and restrained motion. Its primary controls are prompt, Send, compact
model picker and small Auto control, plus a compact destination indicator. It
uses the active project/chat or visibly Temporary chat. Opening the project
window preserves the draft, included context and model intent.

While typing, Auto predicts an eligible model and shows Local or Cloud. Selecting
a model pins that choice; explicitly selecting Auto resumes ranking. Setup,
reasons and preferences appear when needed. Installed models and secure provider
connections are shared resources; projects choose effective settings rather
than duplicating weights or credentials.

Named projects save chats locally with clear disclosure. Temporary chat is
unsaved. A project's file list does not silently include every file in every
request: selected context, instructions, exclusions and potential cloud egress
are visible before Send. Project/profile/chat identity and content remain
separate from hosted tenant, Portfolio repository and AI Work authority.

[LM Studio classic chat organization](https://lmstudio.ai/docs/app/basics/chat)
and [Bionic Projects and Sessions](https://lmstudio.ai/docs/bionic/projects-and-sessions)
are workflow references. They do not commit Hormuz to agents, code/file editing,
voice, browser tabs, remote sync or collaboration.

The exact glossy finish is not selected. Existing prompt-bar simulation and
glossy image concepts are references, not an approved full project UI or
installed platform qualification. #481 selects the shared visual reference;
#492 reviews the integrated simulation. Native desktop blur is optional; the
shared surface needs a beautiful legible opaque fallback.

## UI first: ready to begin simulation

The scope is sufficient to start the bounded UI simulation with synthetic
projects, sources, models, costs and responses. #480 checkpoint A now records
the [product/mock boundaries](DESKTOP_BACKEND_BOUNDARIES.md): saved/Temporary
lifetime, imported text-copy ownership, explicit context, inherited restrictions,
Auto/pin intent, governed Send, one active inference attempt, setup/cancel/recovery
and version-specific Review. These planning defaults are inputs to the UI,
not rendered approval or runtime support. Finish the selected material and
integrated journeys before #492 approval. Before actual host/backend work,
assign a named owner and freeze exact model/provider versions, protocol and
storage/context/output/time limits, custody and first OS/architecture/desktop
qualification rows in #480 checkpoint B. A mock or research paper is not runtime
readiness. #480 stays open until B; its full closure never blocks the UI.

| Stage | Owned issue | Dependencies and exit |
| --- | --- | --- |
| U0 | Confirmed product boundary in #479; #480 A recorded | Full local projects plus Temporary chat; concrete product defaults and logical mock operations in DESKTOP_BACKEND_BOUNDARIES.md. No database/hosting choice or #480 final closure is required to explore the UI. |
| U1 | [#481 shared glossy visual system](https://github.com/Xpounder-com/hormuz/issues/481), [#487 app navigation and bar handoff](https://github.com/Xpounder-com/hormuz/issues/487) | Initial visual tokens and information architecture feed the following slices. Final #481 visual acceptance happens before U4, not before drawing every flow. |
| U2 | [#488 projects, chats, history/search](https://github.com/Xpounder-com/hormuz/issues/488), [#489 files/instructions/context](https://github.com/Xpounder-com/hormuz/issues/489), [#490 onboarding/models/connections](https://github.com/Xpounder-com/hormuz/issues/490) | #487 and initial visual tokens. Proceed in parallel with synthetic storage, files, inventory, credentials, prices and responses. Cover normal, empty, blocked and recovery states. |
| U3 | [#491 inherited preferences, Auto and overrides](https://github.com/Xpounder-com/hormuz/issues/491) | U2 mock contracts. Show effective app/project/chat/request settings, pure preview, explicit pin/Auto intent, changed-route disclosure and continuation limits. |
| U3r | [#502 Research Review UI/spec/simulation](https://github.com/Xpounder-com/hormuz/issues/502) | U2/U3 mock context/preferences and selected visual language. Review an existing frozen draft with two distinct blind reviewers; optionally author/reconcile within the bounded call plan. Show coverage, disagreement, failed reviews and revision-specific human decision. No real model prerequisite. |
| U4 | [#492 integrated simulation approval](https://github.com/Xpounder-com/hormuz/issues/492) | #487–#491, #502 mock review journeys, final #481 and early #338 ordered UI fixtures. One navigable project/window/bar experience, rendered accessibility/recovery journeys, user review and recorded unresolved decisions. |

U4 precedes final backend contract acceptance and host/runtime implementation.
Read-only feasibility can inform UI work without installing or implementing
services. #338 has an early UI checkpoint and a later runtime checkpoint; its
full closure cannot become a circular prerequisite for the simulation.

The [UI plan](DESKTOP_UI_PLAN.md) maps every first-scope screen and journey to
an issue. Nested folders, split views and advanced organization can follow after
the simple create/return/search workflow is usable.

## Backend and installed qualification after UI approval

```text
approved project window / quick bar / setup UI
    -> narrow validated Electron host bridge
    -> isolated local project store + selected request context
    -> qualified Hormuz policy, custody, admission and execution adapters
    -> owned llama.cpp process OR optional direct paid provider
```

| Stage | Work | Completion prerequisites | Exit evidence |
| --- | --- | --- | --- |
| B0 | [#480 B host/data/support/migration acceptance](https://github.com/Xpounder-com/hormuz/issues/480) | U4; reconcile recorded A defaults with approved UI | One authority per state machine, typed UI-to-host seam, content/credential isolation, exact resource/protocol choices, supported-target candidates and legacy coexistence. |
| B1 | [#482 approved UI in Electron host](https://github.com/Xpounder-com/hormuz/issues/482) | U4, B0 | Isolated renderer, narrow IPC, private owned instance, scoped callbacks, activation/focus/recovery and packaged resources. |
| B2a | [#493 local project/history/context storage](https://github.com/Xpounder-com/hormuz/issues/493) | B0, B1 | Explicit saved/temporary modes; bounded search and file subset; permission/ownership, export/delete, isolation, recovery and immutable request context. |
| B2b | [#483 managed llama.cpp and local setup](https://github.com/Xpounder-com/hormuz/issues/483) | B0, B1 | Verified engine/model packages, actual fit, recoverable owned download/load/unload and exact local protocol/process proof. |
| B2c | [#485 direct paid baseline and streams](https://github.com/Xpounder-com/hormuz/issues/485) | B0, B1 | One named provider; qualified request/stream/cancel and existing custody/admission/accounting/unknown-outcome boundaries. Baseline synthetic context is sufficient while B2a advances. |
| B3 | [#484 real pure Auto and pin integration](https://github.com/Xpounder-com/hormuz/issues/484) | Applicable B2a contract and qualified B2b/B2c baselines | Real eligibility snapshots, policy-bounded intent, send-time revalidation, immutable origin/context/model and truthful receipts. |
| B3r — optional | [#503 bounded Research Review runtime](https://github.com/Xpounder-com/hormuz/issues/503) | #502/#492 UI approval and qualified single-response #493/#483/#485/#484 contracts | Exact distinct role/model qualification, frozen scope, blind verdicts, whole-round cap plus per-attempt accounting, disagreement/failure and stale-approval proof. Required before exposing real Review; not a plain-chat prerequisite. |
| B4 | [#337 refresh/power](https://github.com/Xpounder-com/hormuz/issues/337), [#338 ordered fixtures](https://github.com/Xpounder-com/hormuz/issues/338), [#339 owned lifecycle](https://github.com/Xpounder-com/hormuz/issues/339) | Applicable frozen inputs and integrations | Project/view switching, suspend/hide/reopen/cancel/quit/update and helper ownership. Their contracts inform earlier work; closure is not a starting prerequisite. |
| B5 | [#346 packages/update/data migration](https://github.com/Xpounder-com/hormuz/issues/346), [#347 responsiveness/footprint](https://github.com/Xpounder-com/hormuz/issues/347) | Integrated candidates | Per-target trust/update/project-data compatibility, measured project/chat/search/bar interaction and separate UI/inference resources. |
| B6 | [#340 installed desktop journeys](https://github.com/Xpounder-com/hormuz/issues/340) | All applicable earlier gates | Exact declared OS rows pass installed project/create/return/search/context/setup/Auto/pin/stream/cancel/recovery/accessibility. Human evidence is distinct. |

B2a/B2b/B2c can advance in parallel. Candidate packaging and measurement precede
final #340 closure; neither #346 nor #347 requires #340 closure. A stage is not
a release version, support claim or automatic implementation authorization.
Optional B3r contributes applicable package/lifecycle/installed evidence when
real Review is exposed; otherwise label the mode simulated or unavailable. The
one-curated-local-model baseline does not qualify two distinct local reviewers:
local-only Review requires additional exact model qualification, not hidden
cloud substitution or same-version self-review.

## Reuse boundaries

Preserve the shipped Mac app, CLI/agent adapters and gateway contracts until
replacement installation, explicit migration and rollback are proven. Electron
changes presentation; it does not require a gateway rewrite, inference compiler
or general Rust port.

Qualified Rust profile/custody/transport/session/snapshot/lifecycle contracts
can be reused selectively. They are not a drop-in model manager or project
store. Control-plane bounded JSON is not multi-GB download or streaming transport.
The existing external-agent relay is not the llama.cpp model host. B0 inventories
useful donor PR #458 fixes separately from superseded GTK/Swift rendering.

Current source handles OpenAI Responses, Chat Completions and Anthropic Messages.
Qualify the selected local/cloud envelopes, rather than assume complete parity
from “OpenAI compatible.” Pin upstream engine/UI compatibility and notices;
adapt useful components through Hormuz services and avoid private /tools.

Credentials, filesystem/process access, policy and storage authority stay in
qualified owners outside the renderer. Model output, Markdown and catalog
metadata are untrusted. Heavy inference stays outside the UI process. Supported
installation needs no global developer Python, Node, Rust or separate model app.

## Auto and project-data contract

Reuse [ADR 0014](decisions/0014-customer-local-ai-work-and-exact-reuse.md), its
approved baseline and continuing-session affinity; do not promise an objectively
best model or create a second learning ledger.

1. Preview reads a bounded snapshot and is pure: no provider/classifier call,
   download/load, budget/exploration reservation, ledger write or prompt
   persistence. Existing stateful `choose_route` cannot run per keystroke.
   Scoped draft saving is a separate debounced local-storage operation.
2. Policy, identity, privacy, cap, capability/context, engine/model readiness
   and verified provider availability filter before ranking. Effective settings
   have explicit app/project/chat/request precedence.
3. Pin is explicit execution intent, never an authorization bypass. A model
   alias alone is insufficient where the backend can rerank. Blocked pin remains
   pinned; selecting Auto explicitly resumes ranking.
4. Send revalidates policy/inventory/budget/context and visibly discloses a
   changed preview before egress, requiring renewed intent for changed route or
   disclosure boundaries. It captures one immutable origin and context/model
   intent for streaming/cancel/receipt.
5. Switching project/view changes presentation, not the submitted response's
   identity. Later instructions/context edits affect future requests. Opaque
   tool/provider state preserves affinity or requires an explicit new chat.
6. Local-only/private work cannot fail over to cloud. Other fallback stays
   within existing explicit qualified one-hop rules; partial or uncertain
   attempts are not replayed. Estimates, actuals and unknown costs stay distinct.

Saved project history follows the
[recorded product/default contract](DESKTOP_BACKEND_BOUNDARIES.md), with separate
retention/export/delete/isolation acceptance in #488/#489/#493. First UI file
scope is imported app-owned UTF-8 text/Markdown copies; live linked files remain
deferred/unbound. Temporary content remains in memory across window hiding and
navigation, then clears on explicit discard/app quit; crashes do not restore it.
The first plan permits one active inference attempt per application and no
hidden queue. It does not expand ADR0014 exact reuse or metadata-only
gateway evidence. Imported files and linked originals have separate ownership;
request context is selected and bounded. No automatic RAG, recursive indexing,
file modification, sync or storage-encryption claim follows from this plan.
Moving/importing a chat reviews destination instructions, file bindings and
model/privacy/cap inheritance before the next Send, preserving original submitted
snapshots. Credentials are never imported and linked-file access is never
implicitly reopened.
Catalog, downloads, updates and telemetry have controls separate from inference
egress.

The [Research Review contract](RESEARCH_REVIEW_MODE.md) applies these same
boundaries to an explicit role plan. Default review starts from an existing
draft and two blind reviewers; optional authoring plus two reviewers and one
reconciliation has at most four calls across three selected identities in one
pass. Whole-project review needs an explicit manifest and coverage limits.
Edited or reconciled v2 does not inherit reviewers' approval of v1. The final
decision is human and version-specific; consensus is not scientific validation
or permission to implement/deploy. The UI mock permits two explicitly started
rounds, never a loop until agreement.

## Qualification candidates and bounded first scope

Electron supplies one UI codebase; platform services, CPU/GPU, drivers,
compositor and installer behavior still need exact evidence.

| Dimension | First scope to qualify | Later/conditional |
| --- | --- | --- |
| Mac | Apple Silicon arm64, exact OS/Keychain/notarized package | Intel after engine/package/clean-machine proof. |
| Windows | Windows 11 x64, exact build/custody/focus/trust path | Other Windows/ARM64 after maintenance/runtime decisions. |
| Linux | Ubuntu 24.04 LTS x64 GNOME, X11 and Wayland separately, Secret Service/window recovery | KDE/distributions/compositors/ARM64 are separate rows. |
| Engine | CPU baseline, one curated small text GGUF, one loaded model | Metal/CUDA/Vulkan only for exact qualified configurations. |
| Model/context | Weights, KV cache, available RAM/VRAM/headroom and bounded supported text file subset | File size alone cannot establish fit, speed or document coverage. |
| Cloud | One qualified direct provider/protocol with OS custody and visible costs/caps | More providers or managed profiles are separate. |
| UI | Full local project workflow plus minimal bar; gloss/type/motion and legible opaque fallback | Native materials/shortcuts/placement vary by OS/desktop. |

Downloads are atomic, recoverable and finite, with explicit timeouts, at most
three retries with backoff and bounded concurrency (default <=8). Only app-owned
files/processes are removed. No unauthorized stress/load tests or orphaned servers.

First useful candidate supports project creation/return, saved text chats,
history/search, instructions and bounded selected text context, local setup,
optional one paid connection, stream/cancel, conservative Auto, override and
visible recovery. All-three-platform publication needs all three qualified rows.

Defer image/audio/video inference, arbitrary marketplace loaders, agents/MCP/
computer control, autonomous jobs, remote sync/team collaboration, fleet
management, a full Rust rewrite and broad hardware support. #341 agent relay
is required only if that path is exposed; optional #342 optimizer work is not
a first-chat prerequisite.

## Infrastructure and other programs

The desktop needs a local host, local storage, OS custody, verified engine/model
packages and optional provider/catalog/download/update endpoints. **Render,
PostgreSQL, Okta, Stripe, hosted provisioning and public gateway are not required
for local projects or direct BYOK.** Keep static docs/site/release distribution
and appropriate CI/signing.

The [infrastructure assessment](DESKTOP_INFRASTRUCTURE_AUDIT.md) preserves the
dated control-plane inventory; the [account cleanup ledger](DESKTOP_ACCOUNT_CLEANUP.md)
records the separate current operations. [#494–#501](https://github.com/Xpounder-com/hormuz/issues/494)
belong to `track:cleanup` and milestone 16, independently of desktop UI and
deferred Hosted Pro. Cleanup examines actual consumers, retained data/custody,
payment obligations, billed costs and verified recovery before final retirement.

Separately authorized operations on 2026-10-13 suspended the remaining active
Render pilot; all three inventoried web services are now suspended. It removed
two obsolete GitHub environment secrets and
disabled the live pilot workflow. PostgreSQL and attached disks remain retained;
archive/export/restore and data/custody/recovery obligations are still pending.
These bounded operations do not establish complete account retirement, zero
cost or qualified replacement of legacy hosted client access. Preserve shipped
artifacts and code contracts; record paused endpoints and recovery/transition
conditions explicitly. Optional managed work #437–#443 remains deferred, and
historical commercial terms require reconciliation before revival.

Portfolio #226 and enterprise #9/#11/#13 remain independent programs with real
unresolved gates. Numerical performance budgets and dates follow actual local
release-build measurements and team capacity, not mockups or old native ceilings.

## Closure and references

Each slice needs exact source/artifact evidence, appropriate checks and ordinary
protected review. Simulation approval, compile/test success, installed support,
independent user value and release publication remain separate claims.

Framework references: [Electron process model](https://www.electronjs.org/docs/latest/tutorial/process-model),
[window materials](https://www.electronjs.org/docs/latest/api/browser-window),
[global shortcuts](https://www.electronjs.org/docs/latest/api/global-shortcut),
[llama.cpp UI](https://github.com/ggml-org/llama.cpp/blob/master/tools/ui/README.md)
and [engine backends](https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md).
