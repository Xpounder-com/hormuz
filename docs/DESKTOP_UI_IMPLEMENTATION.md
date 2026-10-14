# First simulated desktop UI slice

Work order: review and merge planning PR
[#486](https://github.com/Xpounder-com/hormuz/pull/486), then start the glossy
project workspace and minimal Auto prompt bar with no backend or model calls.
#486 merged as `20b8ff04659e9b4ab40c67b429f1def37e115092`; its reviewed head
`72ff23d2c353905b1b33826b9cc4667b81961efa` passed the live required CI set,
Website checks and CI aggregate, with all three review findings addressed.
Post-merge main CI and Website runs also passed for that merge commit.

The first source slice lives in `clients/desktop` and uses Svelte/TypeScript,
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

## Delivered first-slice interactions

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

All fit, model versions, sizes, cost and quality evidence are synthetic. No
Electron app, database, real file chooser, OS-wide shortcut, engine process,
provider key, deployment, release or installed support is introduced.

## Remaining UI acceptance

This slice starts #481/#487–#491; it does not close those issues or #492. Complete
the recorded [UI journeys](DESKTOP_UI_PLAN.md), including project/chat move,
bounded import/export, content search/archive filters, storage/save-failure and
other adverse-state fixtures, changed-plan confirmation, advanced request
overrides and the full Research Review simulation #502. Confirm the selected
finish, accessible keyboard/focus recovery and early #338 fixtures in the
integrated review. Runtime #480 B/#482–#485/#493 remains after #492 and requires
separate authorization and qualification.
