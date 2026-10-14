# Hormuz desktop UI simulation

First UI slice for the glossy project workspace and minimal Auto prompt bar.
Svelte and TypeScript render the interface; an injected memory-only fixture
owner supplies state. This module does not start Electron, llama.cpp, the
gateway, storage, model downloads or paid-provider calls.

From this directory, with Node 24+:

```sh
npm ci --ignore-scripts --no-fund --no-audit --fetch-retries=2 --fetch-timeout=20000 --maxsockets=4
npm run dev
```

Open `http://127.0.0.1:5173`. One loopback server is used; an occupied port fails
instead of starting another instance. Stop it with Ctrl+C when finished.
Installing developer dependencies uses the npm registry. The app uses local
assets and no inference transport; development HMR uses a loopback WebSocket.

Try creating a project, switching chats with different drafts, selecting sample
copies in Files, and editing Instructions and Preferences. Type “write” to
preview the downloaded local writing fixture. Send simulates loading it before
streaming. Open Connections, add and test the demo, then separately allow cloud
in project Preferences; “compare evidence” can now preview the cloud fixture.
The picker pins a model even when blocked; its small Auto control resumes
ranking. A Local-only selected file or chat restriction blocks cloud.

Open Quick bar or use Cmd/Ctrl+K. It shares the active chat's draft, selection
and pin with the main workspace. Changing destination restores that destination's
own draft. Send returns to the workspace for the simulated response. Escape
returns from the bar; Shift+Enter adds a line and Enter sends outside IME composition.
This browser shortcut is not an OS-wide launcher.

Open Research for **Roast my paper**. Select Gentle/Spicy/Scorched heat and two
distinct model fixtures; Start freezes the sample paper, criteria and selected
copies. Reviews run sequentially and keep their origin across navigation. Each
finding has a response approach, point-by-point reply and evidence/change locator.
**Rebuttal** produces a copyable draft letter from those findings or pasted
external reviewer comments. One optional Check rebuttals pass may suggest
support, missing evidence or pushback; only your explicit decision closes a
supported resolution. Edited paper/replies make applicable checks stale. Two
explicit roast rounds are available, with no automatic reruns. Findings remain
scripted sample-paper fixtures even when the editor contains different text.

Models offers explicit simulated download/verify/load/unload/remove and canceled
states. Connections has working/invalid/offline test fixtures and collects no
keys. Only one response and one local loaded model are allowed. Navigation
preserves the captured response origin; Stop retains partial/unknown receipts
without replay. Temporary permission and content are session-only; explicit save
previews the named project's settings. Delete preserves separate usage and
project-owned copies when deleting a chat.

All sizes, fit, token estimates, routing evidence, prices and allowances are
fictional. “Saved · simulated” means memory in this browser session. **Reload
resets every project and chat.** No real file chooser, durable saving, secret
store, Electron host, OS shortcut or supported-platform qualification is present.
The UI never claims that its keyword fixture selects the objectively best model.

```sh
npm run check
npm test
npm run build
```

Routing/ownership tests use mock timers without network dependencies. The scoped
CI workflow uses Node 24 and pinned dependencies/actions. The UI's restrictive
CSP allows its own assets and loopback HMR, with no arbitrary external fetch or
model-output execution capability. This renderer mock is not an authoritative
production policy, accounting or host contract.

The renderer now covers the #487–#491 and #502 simulation journeys. Open History
for title/content/project/archive search and import/export. Workspace actions
include pin, archive, move, clear and delete. Files support preview/replacement
and failure states; Preferences has inherited intent and a one-request override.
Settings contains the fresh Sandbox profile, memory-journal quit/reopen,
appearance/host/recovery scenarios and independent network controls. A failed
Temporary save preserves its content; a changed Send plan asks for renewed
intent; partial work can prepare an explicit retry without automatic replay.

Research adds a selected/custom/whole-project source manifest, known-author
and alias checks, optional Draft + review, failed/dissenting reviewers and
reconciliation into unreviewed v2. Author output and proposals preserve unique
working edits. Initial reviewers see the same frozen target without current-pass
peer verdicts. Check or reconciliation uses one optional call, not both; Finish
round can release unused allowance. Human decisions bind to version/sources and
resolved findings and confer no execution or scientific authority.

The export preview excludes research sessions, Temporary and live bindings;
import is capped at 1 MiB and creates new identities/cloud-off/unbound model
intent. The named-work/research journal survives only a simulated restart;
browser reload resets everything. Profiles isolate content and consent, and
profile change unloads the resident model. All prices and cost records are fake.

See [implementation/gate evidence](../../docs/DESKTOP_UI_IMPLEMENTATION.md).
Final #481/#492 visual/accessibility/user acceptance remains open. Runtime
#480 B, Electron, durable storage, llama.cpp, providers and real Auto follow
under a separate work order. No installed-platform claim follows from the UI.
