# Desktop backend boundaries for UI planning

Planning checkpoint: 2026-10-13. Parent: [#479](https://github.com/Xpounder-com/hormuz/issues/479).
The user requested backend boundaries and roadmap/issue updates before starting
the UI, with no application implementation. This document records checkpoint A
of [#480](https://github.com/Xpounder-com/hormuz/issues/480). It supplies concrete
product behavior and mock service responsibilities to the
[UI plan](DESKTOP_UI_PLAN.md); checkpoint B accepts the actual host, protocols,
storage, custody and supported-target candidates after
[#492](https://github.com/Xpounder-com/hormuz/issues/492).

These are planning defaults for the first UI work order, not approved rendered
UI, executable schemas or qualified runtime limits. No code, database, real
project-file access, model download, provider call or deployment is introduced.
Changing a default during UI review updates this document and its issue fixtures
before runtime acceptance. Existing public/durable contracts remain unchanged.

## Decisions made now and decisions kept for later

| Record now — #480 A | Accept after UI approval — #480 B |
| --- | --- |
| Saved versus Temporary work, file ownership, selected context, export/delete, effective settings and privacy defaults | Content-store technology/schema, exact file/token/storage limits, retention mechanics, migration, backup and rollback |
| Pure Auto preview, persistent chat pin, explicit request override, revalidation and immutable submitted origin | Qualified ranking/admission adapter, legitimate execution authority, protocol framing and compatible versions |
| Local setup states, one active inference attempt and one loaded local model, explicit network actions | Exact llama.cpp/curated GGUF/cloud model versions, provider/prices, fit, transport, cancellation and process ownership |
| Stream failure/cancel/unknown states and bounded revision-specific Research Review | Real time/resource/budget envelopes, secure custody on each target and installed qualification |
| Logical service responsibilities and bounded mock snapshots/events | Exact IPC/schema versions, sender validation, subscriptions, recovery and implementation owners |

UI issues consume A without waiting for #480 to close. #482–#485 and #493 consume
B. The rendered visual choice remains #481, integrated user acceptance remains
#492, and package/installed support remains #346/#347/#340. Account retirement
#494–#501 is independent. Local/direct-provider operation requires no Render,
PostgreSQL, hosted login, Hormuz subscription or mandatory managed gateway.

## 1. Local projects, saved content and Temporary work

Named projects retain chats, drafts, instructions, preferences, imported copies
and submitted context/response records locally until explicit deletion. Archive
hides work from the default list; it does not delete or expire it. Show Saving,
Saved and Save failed; a failed write cannot produce a Saved badge. Durable
content belongs to one profile-qualified local store owner outside the renderer.
Credential custody and content-free usage/admission evidence are separate owners.
No cloud sync, answer cache or encryption claim follows from local saving.

Temporary chat/review stays in memory, including drafts and partial responses.
Hiding, navigating or closing a window retains it while the application remains
alive. Explicitly closing/discarding the Temporary chat or quitting the app
clears it; with content or active work, offer Save to project, Discard or Cancel.
Save is an explicit conversion/copy into a named project with a destination and
context/settings preview. If work is active, Stop first, record terminal/unknown
outcome, then save a copy preserving original attempt provenance. Clear Temporary
bodies only after save acknowledgment; a failed save leaves the view open for
recovery or explicit Discard. App/process crash has no Temporary recovery promise.
Temporary prompts, context and responses never enter durable content, search,
diagnostic logs or a crash snapshot; any required content-free attempt/cost
evidence may remain without those bodies.

Draft saving is a separate debounced operation for saved chats, never part of
Auto preview. Handoff between bar and window carries the same draft, context and
intent. A project/profile change does not move that draft silently. Quitting
stops owned work gracefully; interrupted attempts retain partial/unknown state
and never resume provider work automatically on restart.

### Delete, move, export and import

- Delete removes selected local project/chat content and search entries. Copies
  belong to a project: deleting a chat removes its selections/snapshots, preserving
  project copies used elsewhere. Copies are removed only by explicit file removal
  or deletion of their owning project. It does not delete external originals, shared model
  weights, provider connections, exports already made or separate accounting
  records. Do not promise secure erasure from filesystem backups; B defines
  content-store backup/retention mechanics and cleanup of active sidecars.
- Moving/deleting a busy chat/project is blocked until the person explicitly
  stops its work, its terminal or unknown outcome is recorded and owned work is
  quiescent. Unknown financial holds may remain separately; deletion cannot
  erase incurred/uncertain usage or grant another request free allowance.
- Move retains conversation content, draft and original submitted snapshots.
  Preview destination instructions/defaults and the disposition of selected
  imported copies. Future requests adopt accepted destination settings;
  restrictive privacy flags survive. Reset route/instruction overrides and
  context selections only through the explicit move preview. A past attempt
  keeps its original origin even when the chat's current project changes.
- Export previews selected local content, imported copies, portable preferences
  and revision/provenance metadata. Exclude credentials, private handles,
  continuation tokens and execution authority. Import creates a new project
  with new local IDs; it cannot overwrite existing work or restore authority.
  Imported text/metadata is untrusted and bounded. Imported connections are
  unbound, cloud access is off until explicitly enabled, and model intent is
  unresolved until matched/accepted against local inventory. Historical model
  receipts remain historical; they cannot authorize a new request.

## 2. Files, instructions and request context

First scope uses explicitly imported **app-owned UTF-8 `.txt` and `.md` copies**.
Adding a file means an explicit chooser/import operation later; mocks use fake
files. Existing originals remain untouched. Replacing a copy creates a new
revision; changes to the original do not silently update the imported copy.
Live links, folders/watchers, recursive scans, PDF/office parsing, RAG/embeddings,
source retrieval, executable loaders and arbitrary file writes are deferred.
Legacy/imported linked entries, if shown, remain unsupported/unbound; the remedy
is an explicit supported-copy import, not an implicit reopen of a private path.

Project membership is separate from request inclusion. A new chat starts with
no selected files; the person selects copies/excerpts for that chat. Instructions
combine visible app defaults, project instructions and an explicit chat layer;
reset removes that layer. Instructions/context are untrusted model input, never
application permissions. The effective preview shows included revisions,
history range, exclusions and where the selected content would be processed.

Default history uses up to the latest ten complete exchanges of the current
chat, displayed as a selected range. Other chats and older exchanges are
excluded unless selected explicitly. File/context changes affect future
requests; committed attempts retain their frozen included content and origin.
Oversize/unknown-token/unsupported context blocks Send with a reduce/select/setup
remedy. There is no silent truncation, summarization, retrieval or dropping of
instructions to make a model fit. A changed model recomputes the same packet's
eligibility; it does not silently select a different packet.

## 3. Settings, privacy and budget intent

Defaults are **Auto**, **balanced objective with a local preference when the
qualified evidence does not distinguish candidates**, and **Local-only**.
First launch needs no cloud connection or organization account. Adding/testing a
connection does not enable prompt egress; enabling Cloud-allowed for a project
is a deliberate separate action with content/retention disclosure. Missing
quality evidence chooses a documented baseline or blocked state, never a claim
that the selected model is objectively best.

Effective defaults follow app → project → chat → request. A default is distinct
from a restriction: the app's default Local-only can be changed by explicit
project opt-in, while an enforced profile/project Local-only restriction cannot
be loosened by a chat, request pin or review role. Chat/source restrictions may
tighten the project setting. Managed policy, if explicitly configured, is an
additional authority ceiling. A model change cannot remove a restriction.

Cloud attempts require a configured connection, verified eligibility, explicit
cloud permission, usable price/upper-cost bound and finite authorized allowance.
The UI shows app/profile and project ceilings, request estimate, actual and
unknown/held costs separately. More specific settings may tighten a cap, never
raise a parent ceiling. No paid allowance is implied by adding a connection.
These caps govern attempts admitted through Hormuz; they are not a provider's
account-wide bill cap or a promise that interrupted work is free. Currency,
window/expiry rules and concrete custody/accounting adapters are accepted in B.

Temporary chat has no parent project and starts Local-only independently of app
defaults. Its full-window preferences offer an explicit **Allow cloud for this
Temporary chat** action; adding a connection or pinning a cloud model cannot
perform that opt-in. The permission lasts only for that Temporary chat and applies
to its disclosed chat/review context, subject to enforced profile/source
restrictions. It needs a finite lifetime allowance under the profile allowance
and the same per-request/per-round ceilings; missing consent, price or allowance
blocks Send. No invented project ID confers execution/budget authority.
The mock uses a USD 10 Temporary lifetime ceiling beneath the fictional USD 25
profile/UTC-month ceiling, with USD 1 request and USD 3 round ceilings.
Discard/quit clears Temporary content, preferences and cloud consent; incurred
usage and unknown holds remain in the separate content-free accounting owner
and still count against the profile cap. A new Temporary chat does not refund
them. Saving to a named project previews destination settings and requires its
own cloud opt-in; session consent never silently transfers. B qualifies the
actual personal-authority binding, allowance window and settlement contract.

Prompt egress is separate from catalog lookup, model downloads, connection test,
update checks and telemetry. First-run telemetry is off. Catalog/test/download
and update-check actions require their own explicit controls; the mock preview
never performs them. A local-only chat can use an already installed model
without contacting a catalog. Any model-output link is untrusted and opening it
is a separate user action, not an automatic research/network operation.

## 4. Auto, manual override and the Send boundary

The compact picker changes **chat-level** Auto/model intent by default. A manual
pin persists until the person chooses Auto or resets to inherited intent. Its
blocked state stays pinned and gives a remedy. If the full window offers a
one-request override, label it; retain it through an admission failure or
changed-plan confirmation and consume it only when that attempt dispatches.
After dispatch, later prompts use the chat intent; the active response keeps
its captured intent. Navigation and model setup do not silently reset either.

Preview is a pure bounded read of inventory, policy, context, prices and existing
routing evidence. It makes no classifier/provider/network request, model
download/load, allowance/exploration reservation, ledger write or prompt save.
Snapshots carry profile/project/chat/context and generation identity so stale
callbacks cannot update another draft. Filter policy/privacy/capability/context,
readiness/connection and budget before ranking. Auto may propose Ready or
verified/downloaded/qualified loadable local candidates; disclose needed
preparation as Local, loads on Send without adding a setup control to the bar.
Send authorizes that exact bounded load/swap; only inference dispatch requires
Ready and revalidation. Missing downloads, unsupported/unknown fit or no eligible
candidate stay blocked with a setup remedy, never cloud substitution.

Send is the only transition from prediction to a governed execution attempt:

1. Validate the current origin, context revisions, route intent, consent and
   inventory/policy/budget snapshot. A changed source/context requires refreshing
   the packet; a changed model/destination/egress or price/allowance boundary
   refreshes the visible proposal and requires renewed Send intent before
   dispatch. A purely updated status that leaves the plan and boundaries intact
   does not require another confirmation.
2. Freeze origin, included context/instructions, effective restrictions, actual
   model/version/location, request/attempt identity and applicable authority.
   Local project IDs are UI/content identities, not trusted AI Work actors/jobs.
   B must bind them to legitimate local/direct or managed execution authority.
   If the disclosed local plan needs a load/swap, enter cancelable Preparing,
   make that exact owned model Ready, then revalidate the frozen packet and current
   policy/fit/budget before inference. Loading does not change the chosen route
   or authorize a download; preparation failure stays blocked/failed.
3. Admit through the existing qualified policy/custody/reservation owner. A
   double click/repeated submission of the same command returns that attempt;
   it must not reserve twice or create two provider calls. Exact idempotency and
   transport/schema rules are accepted in B.
4. Dispatch the frozen plan. Changing views/preferences/context later cannot
   relabel it. Opaque/provider continuation keeps affinity or requires an
   explicit new chat; there is no arbitrary state translation.

Local-only never falls back to cloud. First UI baseline uses no automatic model
fallback: unavailable/failed work offers explicit recovery or a new attempt.
Existing qualified one-hop fallback may be exposed later only with its separate
intent/admission/lineage contract. Partial or ambiguous work is never replayed
automatically, and this plan adds no new answer/context cache or learning ledger.

## 5. Model setup, connections and bounded work ownership

Engine and model resources are installation resources; project settings do not
duplicate weights. Inventory access and provider connections are profile
qualified; no cross-profile prompt, key or private connection sharing follows.
The first plan permits **one loaded local model and one active inference attempt
per application**. A second Send shows Busy and a return/stop remedy; there is
no hidden queue or speculative parallel answer. Review roles execute sequentially
under this same scheduler; future concurrency needs a separate qualified limit.

The library distinguishes Offered → Downloading → Verifying → Downloaded →
Loading → Ready, plus canceled/interrupted/failed/unsupported/too-large states.
Downloaded is not Ready. Explicit Download and Load are separate actions; the
typing preview, project switch and Auto selection start neither. Load in Models
is optional preparation; Send can authorize the disclosed load/swap of an
already downloaded, verified and qualified model. Only a Ready model can dispatch
inference. Missing downloads remain blocked until explicit setup completes,
preserving their pin; a failed planned load never substitutes a cloud route.

Unload/remove an active model is blocked until owned work stops. Remove deletes
only app-owned weights and exposes affected pins/preferences; it does not change
them into cloud requests. Closing setup hides its view without orphaning the
owned operation. Quit/cancel/update must release owned workers/handles; recovery
never marks an interrupted package verified or repeats inference automatically.
Download/catalog/update and inference transports have separate resource controls.
Later runtime work follows the repository's bounded concurrency, timeout,
finite-retry and cleanup rules; inference ambiguity has no automatic retry.

A connection is configured, testing, verified, unavailable/locked, invalid,
offline or rate-limited; a saved key alone is not a verified working connection.
Tests are explicit and disclose any provider request/cost; UI mocks use no keys
or network. Editing/removing a busy connection requires stopping its governed
work first. Removing it deletes its credential binding and blocks affected
cloud intents while preserving chat history and usage receipts. Secrets stay in
the custody owner; the renderer receives opaque connection IDs/status only.

## 6. Streaming, cancellation and restart

Events identify immutable attempt/origin and ordered sequence/generation. The UI
may change the visible chat while work continues; hide/window close/navigation
does not cancel it. The compact status can return to the originating work.

The lifecycle distinguishes Preparing, Blocked, Running, Stop requested,
Completed, Failed, Canceled and Outcome unknown. Partial/truncated output is
marked partial, with actual model/location and estimated/actual/unknown receipt.
Stop prevents new work and requests bounded cancellation; it does not assert
provider-side cancellation or erase incurred/uncertain cost. A failed save is
separate from inference success: show the unsaved result and recovery instead
of rerunning the model. Explicit Retry creates a new admitted attempt linked to
the earlier one, with disclosed possible duplicate cost where outcome is unknown.

Host/renderer restart reconstructs saved state and terminal/unknown receipts,
not a resumed paid request. Temporary bodies are lost. Corrupt/incompatible store,
disk full, denied access, missing credential store and lost engine are explicit
recoverable/blocking states; no automatic reset/delete of user content. Update
waits for quiescence; migration, recovery and rollback are accepted in B/#346.

## 7. Research Review uses the same boundaries

Follow the [Research Review contract](RESEARCH_REVIEW_MODE.md). Default: existing
draft + explicit frozen source/criteria packet + two distinct blind reviewers.
Existing-draft review has at most three calls with optional reconciliation;
author + two reviewers + optional reconciliation has at most four. Roles run
sequentially, with one loaded local model; distinct local reviewers require two
separately qualified model identities, not two sessions of the one-model baseline.

Existing-draft reviewers share the frozen target/source/criteria packet and
effective restrictions. For authoring, freeze question/sources first; the author
creates v1, then freeze v1 and give both reviewers identical v1 review packets.
Only afterward may reconciliation see recorded verdicts. Show exact role
identities/locations, coverage/exclusions, whole-round upper allowance and
per-attempt costs before Start. A pure role preview neither loads nor runs a
model. Missing reviewer, context/cap/privacy failure, cancellation and unknown
outcome stay incomplete; disagreement stays visible. Freeze blind initial
verdicts before exposing peer conclusions. Reconciliation does not rewrite them.

Review-plan eligibility is separate from immediate inference readiness. A
verified, downloaded, qualified model may be eligible for a disclosed load/swap
step even while another model is loaded. Start explicitly authorizes that finite
sequence, without authorizing downloads; each role must reach Ready and pass
current policy/context/budget checks before its own dispatch. A failed load or
changed plan leaves the round incomplete or requires renewed intent, never a
silent substitute. This permits local-only review with one model resident.

An edited/reconciled v2 is unreviewed/stale relative to v1 findings. Another round
requires explicit intent and fresh admission. Human sign-off names revision and
scope, with disclosed limitations; it is not model proof or permission to deploy,
publish, edit files or run agents. Research bodies use the local content owner,
Temporary remains memory-only, and accounting stays content-free. #502 feeds the
UI gate; optional #503 follows a useful qualified single-response baseline.

## 8. Logical UI-to-host seam

These are mock responsibilities, not executable/public API definitions. B must
version the actual schemas/events, validate sender, authority and bounded payloads,
and define compatibility before implementing them. The renderer presents bounded
snapshots and sends narrow intent; owners outside it commit authoritative state.

| Responsibility | UI requests / receives | Authority outside renderer |
| --- | --- | --- |
| Workspace | List/create/update/archive/move/delete; save draft; scoped search; explicit export/import; saving/error/revision snapshots | Profile-scoped content owner; validates origin/revision and bounded mutation scope |
| Context | Explicit supported-copy import/replace/remove; selected packet/effective instructions, limits and egress preview | File/content owner; opaque selected-file handles, no arbitrary paths or ambient permissions |
| Inventory/setup | Inventory/status; explicit download/load/unload/remove/cancel; progress and readiness | Owned engine/package lifecycle owner, curated verified manifests and finite resources |
| Connections | Add/test/edit/remove; opaque binding, custody/availability status and price snapshot | One qualified custody/transport owner per mode; never returns raw secrets |
| Preview | Effective settings + current packet/intent; eligible prediction/reasons/block and snapshot generation | Pure policy/ranking adapter over bounded available state; no admission or side effects |
| Execution | Submit/stop/query captured attempt; ordered progress/partial/result/receipt | Existing qualified admission/accounting plus owned scheduler/transport; immutable origin and separate content save |
| Review | Preview/start/stop bounded plan; frozen revisions, role findings, dispositions and scoped human decision | Same policy/execution owners plus revision-bound review orchestration/content records |

Every failure identifies its origin/operation, stable category, recoverability
and next action without leaking raw credentials, prompts or private paths into
diagnostics. Mock categories cover invalid/stale context, blocked privacy/cap,
no model, busy, setup/storage/custody failure, unavailable connection, timeout,
partial/unknown outcome and incompatible import/store. Subscribe/unsubscribe and
cancel are scoped; a late event cannot cross profile/project/chat generations.
No generic filesystem API, shell/process launcher, arbitrary fetch proxy,
unrestricted Electron capability or model-output action dispatcher is exposed.

## 9. Finite mock fixtures and readiness

Use these synthetic constants so UI states can be compared. They are fixture
inputs, not installed hardware fit, provider pricing or production performance
claims; #480 B must replace or explicitly accept them after qualification.

| Mock input | Fixture value |
| --- | --- |
| Supported file / selected files | UTF-8 `.txt`/`.md`; 256 KiB per copy; at most 20 selected copies/excerpts and 1 MiB total selected text |
| Token capacity | Fictional 8,192-token model window; at most 6,144 input tokens including all instructions/history and 2,048 output tokens; smaller/unknown model cases block separately |
| History / search | Latest ten complete exchanges selected visibly; 50 search results per page; no unbounded all-project read |
| Work residency | One active inference attempt/application, one loaded local model, no hidden queue; roles sequential |
| Time | Simulated 60-second attempt ceiling and five-minute review-round ceiling, with explicit timeout/cancel states; animations need not consume those durations |
| Budget | Fictional USD 25 profile ceiling per UTC month, USD 10 project ceiling in the same window or USD 10 per Temporary lifetime, USD 1 request and USD 3 review-round upper allowance; lower/zero/unknown cases exercise refusal |
| Review | Two explicit mock rounds maximum; two reviewers, optional author/reconciler within the documented three/four-call path |

Byte ceilings do not guarantee token fit. Include synthetic over-limit, missing
price/usage, stale snapshot, cancel/save failure and unknown-token cases, not just
the success fixture. Real prices, limits and a finance schema are not created by
these examples. One round budget admission must reserve the whole plan while
settling individual attempts without double-counting or releasing unknown holds.

Checkpoint A is recorded when this document and consuming issue criteria agree.
That prepares UI work; it does not complete #480, select the final finish or
approve #492. Before B/runtime: assign owners, qualify the minimal reused backend,
exact engine/model/provider/protocol, content/custody/migration, real limits and
OS/architecture/desktop rows. No all-machines support claim follows from Electron.
