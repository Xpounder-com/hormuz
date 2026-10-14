# Hormuz desktop UI

The approved glossy project workspace and minimal Auto bar use Svelte/TypeScript.
The browser preview remains a memory simulation. The owner approved the core
UI direction on 2026-10-14 and authorized Electron/llama.cpp work after #518 merges.
Dedicated research modes are removed; specialised workflows use skills in chats.

With Node 24+:

```sh
npm ci --ignore-scripts --no-fund --no-audit --fetch-retries=2 --fetch-timeout=20000 --maxsockets=4
npm run dev
```

Open `http://127.0.0.1:5173`. Use the single loopback server; stop it with Ctrl+C
when finished. The browser makes no model/provider calls. Reload resets all
memory state. “Saved · simulated” does not mean durable saving.

Try projects/chats, History search, Files/Instructions/Preferences, local setup,
explicit provider consent, pins/Auto, changed-plan confirmation, stream Stop,
move/import/export and Temporary save/recovery. Cmd/Ctrl+K opens the browser
quick bar; an OS-wide launcher belongs to the following Electron slice.

Context → Skills makes an instruction pack available for the active destination.
Use `/roast spicy`, `/rebuttal` or `/review` with supplied work in the normal prompt.
Skills add visible, frozen instructions; they do not run other models or grant
file/tool/spending permissions. There is no Research screen or special review
state machine. Model replies in the browser are still synthetic fixtures.

```sh
npm run skills:compile
npm run skills:check
npm run check
npm test
npm run format:check
npm run build
```

The bounded catalog is compiled from committed `skills/*/SKILL.md` files. There
is no marketplace installer or arbitrary code loader. Export excludes skill
bindings and live credentials/authority; import needs explicit skill reselection.
The early ordered fixtures remain separate from actual IPC, persistence,
filesystem/engine/provider and installed-platform acceptance.

See [implementation evidence](../../docs/DESKTOP_UI_IMPLEMENTATION.md),
[backend boundaries](../../docs/DESKTOP_BACKEND_BOUNDARIES.md) and
[skills contract](../../docs/RESEARCH_REVIEW_MODE.md). Actual IME, screen-reader,
high-DPI and installed platform qualification remain later checks. The owner has
explicitly authorized starting the bounded backend slice; broad support, packages,
paid inference and release readiness do not follow from that work order.
