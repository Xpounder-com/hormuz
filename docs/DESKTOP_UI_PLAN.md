# Hormuz desktop UI plan

Planning scope confirmed on 2026-10-13. Parent: [desktop epic #479](https://github.com/Xpounder-com/hormuz/issues/479).
This is the canonical UI scope for the full local project workspace. It refines
the [desktop roadmap](ELECTRON_DESKTOP_ROADMAP.md): the earlier ephemeral-chat-only
first scope is superseded by saved local projects/chats plus a Temporary chat
option. Existing gateway, native-client and installed qualification evidence
keeps its original boundary.

This document authorizes no application or backend implementation, real model
downloads, provider calls, service activation or infrastructure shutdown. UI
simulation and later implementation each need their explicit work order.
No release version, delivery date or new platform support is assigned here.

## Confirmed experience

Hormuz owns projects, chats, files, instructions and model/Auto preferences in
one Electron + Svelte/TypeScript app. A normal project window supports continuing
work; a minimal global prompt bar provides quick access to the active project.
Both use the same cohesive glossy visual language: smooth curved edges, refined
type, restrained highlights, depth and motion. The exact finish is unselected.
Readable opaque, reduced-motion and high-contrast alternatives are required;
native desktop blur is an optional platform enhancement.

The project window contains project/chat navigation, a conversation and composer,
and progressively disclosed context and settings. Models, Connections and app
preferences have clear destinations. The quick bar keeps the prompt, Send,
compact model picker, small Auto control and visible active project or Temporary
chat destination. Typing previews the full chosen model and Local/Cloud; manual
selection pins it and selecting Auto explicitly resumes ranking.

The bar and main window share the same destination, draft, selected context and
route intent. Opening the main window continues that work. Switching project
with an existing draft must preserve its origin or present an explicit move;
it must not silently send the draft under another project's instructions.
Navigation, hiding a window and canceling a response are distinct operations.

Local project setup needs no organization enrollment. Optional personal
paid-provider connections and optional existing managed-workspace access remain
distinct. A local project is an organization container for the person's work,
not a Hosted Pro tenant or an enterprise workspace.

## UI-first dependencies

The UI review completes before backend integration. Early product decisions and
mock service envelopes are enough to design the UI; finalizing a database,
hosting provider or privileged process boundary must not block the mock flows.

| Slice | UI prerequisites | Reviewable outcome |
| --- | --- | --- |
| [#481 shared visual system](https://github.com/Xpounder-com/hormuz/issues/481), initial checkpoint | Confirmed product scope | Initial material/type/curvature/motion tokens and reference direction for the full app and bar. Final finish acceptance occurs after integrated screens exist. |
| [#487 navigation, main window and minimal bar](https://github.com/Xpounder-com/hormuz/issues/487) | Product scope and initial #481 tokens | Destinations, active project, app/bar handoff, draft ownership, first-run/return navigation and keyboard/focus behavior. |
| [#488 projects, saved chats, history and search](https://github.com/Xpounder-com/hormuz/issues/488) | #487 | Project/chat lifecycle, locally saved return flow, Temporary chat, scoped search, archive/delete and import/export mock states. |
| [#489 files, instructions and context](https://github.com/Xpounder-com/hormuz/issues/489) | #487 and product/data decisions | Project membership versus request inclusion, instructions inheritance, effective context, permissions and unsupported/missing/changed/oversized states. Can proceed alongside #488. |
| [#490 onboarding, library and connections](https://github.com/Xpounder-com/hormuz/issues/490) | #487 and product mode decisions | Local-first onboarding, download/load/remove state distinctions, optional paid connection and visible readiness/custody/connectivity errors. Can proceed alongside #488/#489. |
| [#491 inherited preferences, Auto and pin](https://github.com/Xpounder-com/hormuz/issues/491) | #488/#489/#490 mock contracts | Effective defaults/overrides, privacy/cap/objective, prediction versus committed route, blocked pin and continuation constraints. |
| #481 final visual checkpoint; [#338 early UI fixtures](https://github.com/Xpounder-com/hormuz/issues/338) | Integrated #487–#491 flows | Cohesive selected finish, rendered state coverage and ordered interaction fixtures. Early fixture acceptance is separate from #338's later runtime integration closure. |
| [#492 integrated UI review gate](https://github.com/Xpounder-com/hormuz/issues/492) | #487–#491, final #481 and early #338 checkpoint | One navigable mock experience, approved journeys, explicit adverse states and recorded decisions. No live backend or installed-platform claims. |

After #492, finalize [#480's full architecture/support/migration contract](https://github.com/Xpounder-com/hormuz/issues/480)
and plan [#493 backend storage/context](https://github.com/Xpounder-com/hormuz/issues/493)
alongside the existing authorized-later host, engine, routing and execution
slices #482–#485. #480 can record product-scope inputs early, but its full
backend contract is not a completion prerequisite for #492. #338's final
closure is likewise not required for its early UI fixture checkpoint.
An approved mock is an implementation input, not authorization to start runtime
work. This separation prevents UI/backend dependency cycles.

## Acceptance by product area

### Project window, bar and navigation — #487

- Start with a flat project list and chats inside a project. Make new project,
  new chat, recent/pinned work, search, Models, Connections and Settings easy
  to find. Nested project trees, split views and browser tabs are later scope.
- Show the active project/chat or Temporary destination in both surfaces.
  Preserve draft text, context selections and overrides across handoff and
  reopen. Cover navigation away from a busy chat without relabeling its result.
- Separate launcher shortcut hints from Send hints. Provide keyboard navigation,
  Escape/back/reopen behavior and an ordinary-window/tray/menu recovery path.
  Mock unavailable shortcuts/focus rather than assuming every Linux desktop
  can position and activate a floating bar identically.
- Include empty, loading, no-selection, narrow-window, long-name and host-error
  states; provide accessible full names where visible labels truncate.

### Projects, chats, history and search — #488

- Create, rename, pin, archive and delete projects/chats; start and reopen chats;
  move chats with explicit context/inheritance consequences. Distinguish archive,
  delete, clear conversation and close tab/window. Preserve busy-chat ownership.
- Named project chats save locally, with a clear first-use disclosure and
  visible saved/saving/failed states. Temporary chat is unsaved; define what
  happens to its in-memory draft/response on close and app quit.
- Search within the active profile with explicit project and archive filters;
  distinguish title search and content search. Include no-results, large lists
  and unavailable/corrupt/disk-full storage states. Saved history is not
  automatically all included in a model request.
- Mock import/export/delete with scope preview, conflicts, invalid/unsupported
  imports, partial failure and recovery. Export contains only the selected local
  project data, never credentials. Profile isolation and migration/rollback are
  visible decisions, with real persistence implemented later in #493.
- Moving/importing a chat defines which instructions, file bindings and
  model/privacy/cap defaults travel or reset. Review destination inheritance
  and context before the next Send; preserve original submitted snapshots.
  Never import credentials or implicitly reopen access to source linked files.

### Files, instructions and context — #489

- Project file membership does not mean request inclusion. Show added files,
  selected chat/request context, exclusions and the effective instructions
  before Send. Removing a file from context is separate from removing its
  project entry or deleting an app-owned imported copy.
- Distinguish imported app-owned copies from links to user-owned originals;
  choose the first supported mode before accepting the mocks. Show permission,
  missing/changed/unsupported/too-large and read-failed states. Folder selection
  does not imply recursive reading, continuous indexing, writing or execution.
- Project instructions and chat overrides expose their effective scope. Show
  context limits and honest unknown estimates; model changes may change what
  fits. Define the first supported file types/size limits in the UI contract;
  formats not yet qualified remain visibly unsupported.
- Local-only and cloud-allowed states explain where selected content would be
  processed. Cloud eligibility must include the selected file/instruction policy.
  Retrieval, embeddings and full-document parsing are future backend decisions,
  not presumed capabilities of a context chip.

### Onboarding, model library and connections — #490

- Local setup works without sign-in. Let a person create a project before
  choosing optional paid access. Separate download/network disclosure from
  inference privacy and optional telemetry/update settings.
- Discover and library states distinguish offered, downloading, canceled,
  verified/downloaded, loading, loaded/ready, stopping, unavailable and too large.
  Show progress, storage needs, retry/recovery, remove/unload consequences and
  safe app-owned file removal. File size alone must not promise model fit.
- Mock configured versus verified-working cloud connections, invalid credentials,
  locked/unavailable secure store, offline/rate-limit/provider error and cap
  exhaustion. Connection add/test/edit/remove has explicit effects on saved
  preferences and active work. Never put real keys in the mock.
- Keep advanced model/runtime settings progressively disclosed. An unavailable
  selected model remains selected and blocked, with an explicit setup/recovery
  action; it does not silently become a cloud request.

### Preferences, Auto, composer and response — #491

- Show app defaults, project defaults, chat overrides and explicit request pin,
  with inherit/reset and an effective-value view. Record the default choices in
  the mocks. Managed privacy/budget ceilings always constrain these choices;
  project preferences cannot loosen them.
- Preview is a pure bounded read of available simulated state: no classifier or
  provider call, model load/download, budget reservation, ledger write or prompt
  storage. Scoped draft persistence is a separate, debounced local-data operation
  for saved chats; its scope/cancellation and save status are explicit. Temporary
  drafts do not use durable saving. Preview must remain side-effect-free in both.
- Eligibility precedes ranking: privacy, cap, context/capability, readiness and
  connection availability. Show an approved baseline or blocked state when
  evidence is insufficient, not a claim that Auto identifies the best model.
  Manual pin remains authoritative intent, subject to those same constraints.
- Send revalidates current state and discloses a changed prediction. Commit the
  source project/chat, context/instruction snapshot, route and attempt identity.
  These remain immutable during a response even if navigation, preferences or
  availability change. Opaque/provider continuation preserves affinity or
  requires an explicit new conversation; arbitrary state translation is absent.
- Local-only never fails over to cloud. Show estimates, actuals and unknown costs
  distinctly, including no external inference charge for local execution rather
  than a claim of free hardware/energy. Partial or uncertain attempts are not
  automatically replayed.
- Include streaming/scroll-follow, code/copy, cancel, partial/failed response,
  explicit retry intent and actual model/location receipt. Busy-state focus and
  navigation must preserve origin. Temporary response handling is visibly
  different from saving a project conversation.

## Integrated review journeys — #492

Use synthetic projects/files/models/history, simulated streams and a mock host.
Record a selected design reference, accepted interactions and unresolved product
decisions; do not silently substitute real service behavior for a mock.

1. Fresh launch → create named project → see local-save disclosure → add
   instructions → simulate local setup → chat → close/reopen → find the chat.
2. Open quick bar in the active project → type with Auto preview → pin a blocked
   model → recover or resume Auto → open the main window with the same draft,
   context and destination. Repeat with Temporary chat and no durable saving.
3. Add a project file → inspect/include/exclude request context → switch privacy
   preference → see eligibility and egress consequences → remove safely.
4. Change project defaults → override a chat → pin one request → reset to inherit;
   see effective settings and an opaque-continuation block/new-chat choice.
5. Begin stream → switch project/chat → return to its immutable source → cancel
   → inspect partial/unknown receipt. Exercise deleting/moving busy work without
   orphaning or relabeling the result.
6. Search history → rename/pin/archive/move → export/import conflict → delete with
   clear scope. Exercise unavailable storage, lost file permission and failed
   save/recovery without false “Saved” or “Ready” labels.
7. Optional cloud onboarding → invalid key/locked store/offline/cap exhausted
   → visible recovery while local work stays available. No required organization
   account, Hosted Pro activation or actual paid call.
8. Keyboard/IME and screen-reader walk through sidebar, search, composer, picker,
   context, progress and dialogs; verify focus restoration, long names, narrow
   layout, high DPI, opaque/high-contrast materials and reduced motion.

The earlier #338 checkpoint records ordered mock traces. Final installed
keyboard, accessibility, platform activation, real failure/recovery and clean
setup/return evidence remains in #340. Packaging/update/data migration proof
remains in #346; release-build project/search/chat responsiveness and separately
accounted UI/inference resources remain in #347. No UI simulation closes them.

## Data, services and scope limits

Local project history and draft content are separate from metadata-only gateway
audit/work-learning records and credential custody. #493 later implements the
approved retention, deletion, import/export, isolation, migration and context
contract. It must not turn the renderer into a credential/filesystem authority.
Local storage does not establish encryption, prompt retention terms for a paid
provider or compliance; the mocks must label concrete selected behavior.

Render, hosted provisioning, billing, remote sync and team collaboration are not
required for local project setup, local inference or personal direct-provider
access. A configured managed gateway remains optional. Existing services cannot
be declared unused or shut down from this UI plan; dependency/usage/data/owner
and recovery evidence is a separate review. Preserve shipped Mac/CLI and gateway
paths until replacement and opt-in migration/rollback are qualified.

First scope stays text-focused, with a curated local model path and an optional
qualified paid connection. Agent/MCP/computer control, arbitrary file editing or
execution, autonomous background tasks, voice, image/audio inference, remote
model sharing, broad marketplace loaders and collaborative cloud projects are
later decisions. A project file list is not authorization for those actions.

## Official workflow references

LM Studio's classic app describes multiple chats grouped in folders, chat
management and supplied context; its preset system groups instructions and
parameters. These inform Hormuz's organization and reusable preferences, not a
requirement for split views or preset publishing. [Manage chats](https://lmstudio.ai/docs/app/basics/chat),
[Config Presets](https://lmstudio.ai/docs/app/presets)

Current Bionic projects group sessions and files, with session lifecycle and
tabs. Borrow that simple project/session organization. Its agent, voice, file
editing and background execution features are outside this UI scope.
[Projects and Sessions](https://lmstudio.ai/docs/bionic/projects-and-sessions)

Document attachment and model selection references inform visible context and
location/readiness states. Hormuz defines its own file processing, Auto, custody
and cloud terms; neither retrieval nor LM Studio's remote/cloud service terms are
inherited. [Chat with Documents](https://lmstudio.ai/docs/app/basics/rag),
[Choose a Cloud, Local, or Remote Model](https://lmstudio.ai/docs/bionic/models)
