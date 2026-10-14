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

The first slice contributes to #481 and #487–#491. Integrated acceptance #492
remains open: full adverse-state coverage, project move/import/export, persistence
failure fixtures and Research Review #502 are follow-up UI work. Runtime
contracts #480 B and Electron/storage/llama.cpp/providers/real Auto follow that
gate under a separate work order.
