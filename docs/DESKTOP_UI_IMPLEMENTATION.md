# Simulated desktop UI implementation candidate

Work order: review and merge planning PR
[#486](https://github.com/Xpounder-com/hormuz/pull/486), then start the glossy
project workspace and minimal Auto prompt bar with no backend or model calls.
#486 merged as `20b8ff04659e9b4ab40c67b429f1def37e115092`; its reviewed head
`72ff23d2c353905b1b33826b9cc4667b81961efa` passed the live required CI set,
Website checks and CI aggregate, with all three review findings addressed.
Post-merge main CI and Website runs also passed for that merge commit.

The UI candidate lives in `clients/desktop` and uses Svelte/TypeScript,
Vite and local assets. A mock workspace owns synthetic state and finite timers;
a separate pure planner previews eligibility and model choice. These are
renderer fixtures, not the future durable schema, public API or host authority.

To explore from a checkout containing the UI slice, use Node 24+:

```sh
cd clients/desktop
npm ci --ignore-scripts --no-fund --no-audit --fetch-retries=2 --fetch-timeout=20000 --maxsockets=4
npm run dev
```

Open `http://127.0.0.1:5173`, then stop that one loopback server with Ctrl+C when
finished. Dependency installation uses npm; the rendered app makes no model or
provider calls. Development HMR stays on loopback. Reload resets its in-memory
state, including named projects. “Saved” is always labeled simulated.

## Implemented simulation

- Glossy dark/light workspace, curved composer, opaque/contrast/reduced-motion
  alternatives and a narrow-window drawer; sidebar projects/chats, title search,
  create/rename/archive/delete and in-session return.
- Shared main-window/quick-bar draft, destination, selected files and model pin;
  browser Cmd/Ctrl+K handoff, minimal Auto control and Local/Cloud preview.
- Sample imported project text copies, explicit per-chat inclusion, unsupported
  fixtures, project/chat instructions and effective packet/history disclosure.
- Project objective, chat override, cloud opt-in and Local-only restriction;
  Temporary-specific consent, finite fictional profile/project/session caps,
  explicit save preview, and separate content-free usage holds.
- Explicit simulated model download/verify/load/unload/remove and connection
  configured/tested/invalid/offline states. Downloads are not readiness. Auto
  can propose a downloaded fixture with a disclosed load-on-Send step.
- Eligibility before keyword-based fixture ranking; blocked pins remain pinned,
  no hidden fallback, no mutation during preview, one inference at a time,
  captured response context/origin, scripted streaming and explicit Stop.
- Research **Roast my paper** with selectable heat and two distinct model
  fixtures, blind initial results, immutable v1 scope and sequential load/cancel.
  **Rebuttal** tracks response/evidence per finding, an optional disclosed third
  check, explicit human resolution and stale v2 changes; pasted external reviews
  produce a draft letter with unknown reviewer provenance. Two explicit rounds
  are allowed; every result remains scripted sample output.

All fit, model versions, sizes, cost and quality evidence are synthetic. No
Electron app, database, real file chooser, OS-wide shortcut, engine process,
provider key, deployment, release or installed support is introduced.

## Completed mock journeys and remaining acceptance

The user requested continued issue implementation with best judgment on
2026-10-13. This checkpoint completes the renderer implementation of the
recorded #487–#491 and #502 mock journeys; it is not full #492 user acceptance.
The selected working visual reference is #518's glossy source at `9a0191b`:
the user called it a great start, and this extension retains its palette,
curvature, material/type and minimal Auto bar. Final visual/assistive-technology
review remains a separate acceptance checkpoint.

| Issue | Implemented behavior and ordered fixtures |
| --- | --- |
| #487 | Sidebar collapse, History, pinned work, main/bar handoff, IME dispatch guard, unavailable launcher remedy, distinct hide/window-close/chat-view/quit actions, Temporary save/discard/cancel and busy-origin return. |
| #488 | Pin/archive/restore/clear/delete, title/content/project/archive search (bounded first 100), large-list fixture, move inheritance/copy preview, bounded JSON export/import, conflict/new identities, invalid/partial/failed-write recovery and two isolated demo profiles. |
| #489 | Preview/replace/remove app-owned copies; new revisions and missing/changed/denied/read-failed/unknown/oversize/unbound states; deselect stale context, inherited app/project/chat instructions, explicit bytes/tokens and immutable submitted provenance. |
| #490 | Fresh Sandbox onboarding without sign-in, offered/downloaded/ready distinction, model details with unqualified source/license/fit metadata, catalog/offline/disk/verify/load failure fixtures, safe removal and configured/tested/expired/locked/rate-limited/provider-error bindings. |
| #491 | App/project/chat intent and defaults, enforceable profile/project/source privacy, child cap, consumed-on-dispatch request override, pure preview, changed route/price confirmation, opaque continuation/new chat, partial/unknown receipts, explicit retry and separate metadata-only cost records. |
| #502 | Heat, role Auto/explicit selection, aliases/known-author lineage, selected/custom/whole-project manifest, source/question then authored-target freezes, two blind reviewers, incomplete/failed/disagreement/missing evidence, optional check OR reconciliation, unreviewed v2, bounded round two and version/source/response-bound human decisions. |

The ordered tests in `workspace.test.ts`, `workflows.test.ts` and
`research.test.ts` are the early mock fixture checkpoint for #338. They cover
preview → intent → preparation → dispatch → navigation → Stop → terminal/cost
and save/recovery, plus source freeze → author (optional) → identical blind
review packets → optional response check or reconciliation → version-specific
human decision. They use injected finite timers, synthetic content and no
network. This does not close #338's actual runtime/lifecycle integration scope.

The mock named-work journal is debounced separately from preview and includes
named research sessions. A simulated quit/reopen returns that journal, loses
Temporary bodies/permission and does not resume work. Failed save preserves
memory. Inactive-profile Temporary or failed-save work must be resolved before
app quit; profile change unloads resident models. Browser reload resets the
whole simulation, including both profiles. This is not durable storage.

Export v1 explicitly covers a selected project's chats/drafts, text copies,
instructions and portable preferences/historical receipts. It excludes research
sessions, Temporary, other profiles, credentials, live model/connection intent,
weights, continuation/authority and the accounting ledger. Import cannot
restore any of those bindings; historical metadata is unverified, cloud is off
and model intent is unbound until chosen locally. Unsupported entries need
explicit partial-import consent. The 1 MiB format is a fixture, not #493's
production portable/durable schema.

Scripted author/reconciliation output never overwrites changed working text;
proposals and frozen reviewed targets remain inspectable. Reconciliation uses
the original author when present and preserves known model-author lineage.
The optional-call upper allowance covers either choice; an explicit Finish
round releases unused allowance and closes that extra call. At most two
explicit rounds run. A known author/alias cannot be an independent reviewer;
no model consensus or human mock decision authorizes execution/publication.

## Validation and gate

73 provider-free mock tests passed on Node 25.2.1; Svelte types (0 errors/warnings),
build and format are checked for the final candidate. Browser evidence is recorded in
#518 separately from its new exact-head CI. Keyboard/native-dialog focus,
labels, responsive/material states and actual rendered journeys are distinct
from actual IME, screen-reader, installed/high-DPI platform and runtime
qualification, which remain manual/later checks. Do not mark those proven from
unit tests or simulated host controls.

The in-app browser pass used 1280×900, 1280×720 and 390×844 windows. It exercised
search/move/import scope, failed Temporary save, renewed Send plan, background
stream return/Stop, fresh-profile local setup, bar handoff, revoked-copy
deselection, mock restart and Temporary quit/cancel/save, authored blind review,
unreviewed reconciliation and failed second reviewer. Light/opaque/contrast and
reduced-motion alternatives were inspected. A fresh final tab had no console
warnings/errors or framework overlay; transient development HMR errors in an
older editing tab were not final-candidate runtime failures. Narrow controls
retain Local/Cloud and load-on-Send labels while only model names may truncate.

#481's final finish/accessibility review and #492's integrated user decision
remain open. #480 B/#482–#485/#493 and optional #503 require the accepted UI,
exact runtime/model/provider/storage/support choices and a separate work order.
No Electron host, keys, filesystem grants, database, model downloads/calls,
release or deployment is introduced here. Account cleanup remains independent.
