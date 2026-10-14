# Approved core UI and skills simplification

On 2026-10-14 the owner approved the glossy project workspace/minimal Auto bar,
requested specialised workflows through skills instead of dedicated feature UI,
and authorized review/merge of #518 followed by Electron/llama.cpp development.
Planning #486 previously merged as `20b8ff04659e9b4ab40c67b429f1def37e115092`.

The core renderer in `clients/desktop` keeps projects/chats/history, selected
files, instructions, preferences, local setup, optional cloud connection fixtures
and the shared minimal Auto/model bar. The Research screen, review editor,
scripted multi-reviewer engine and its dedicated styling/tests are removed.
The earlier exploration at `1eb59d6` remains historical Git evidence.

## Current core journeys

| Issue | Source behavior |
| --- | --- |
| #487 | Shared main/bar draft, destination, context and model intent; sidebar collapse/drawer, pins/History, first launch, distinct hide/close/quit and immutable busy origin. |
| #488 | Project/chat archive/restore/clear/delete/move, title/content/project/archive search, bounded import/export with conflicts/new IDs and partial consent, failed save and isolated demo profiles. |
| #489 | Project-owned text-copy preview/replace/remove and revisions; stale/revoked/unsupported/oversized context; selected inclusion and visible app/project/chat/skill instructions. |
| #490 | Explicit fake download/verify/load/unload/remove, downloaded versus Ready, fresh local-first setup and connection/configuration/recovery states. |
| #491 | Pure typing preview, Local/Cloud, pins/Auto, inherited and one-request intent, privacy/caps, changed-Send confirmation, continuation affinity, partial/unknown receipts and explicit retry. |
| #502 | Reusable instruction-only SKILL.md packs and explicit project/Temporary availability through Context → Skills. Leading /roast, /rebuttal or /review activates the pack in an ordinary request. |

Skills are compiled into a bounded catalog from committed packs; `skills:check`
verifies it matches the sources. They add frozen visible instructions and grant
no tool/file/spend authority or automatic model runs. They do not invent reviewer
identities or approvals. Imported projects require explicit skill reselection.
The [skills contract](RESEARCH_REVIEW_MODE.md) replaces the Research UI roadmap.

The browser remains a memory simulation. Named work uses a debounced mock journal
separate from preview; Temporary bodies never enter it. Mock quit/reopen restores
named work without resuming a request. Browser reload resets all state. Failed
save preserves content; imports restore no credentials/live binding/authority,
start cloud-off and leave model intent unbound. Historical receipts are unverified.
Export excludes skill bindings, Temporary, other profiles and the accounting ledger.

## Validation and approval boundary

The scope change retains the ordinary ordered tests and adds skill invocation,
pure-preview/context/provenance and import-isolation checks. Svelte diagnostics,
format/build and the bounded catalog check run on the exact replacement head;
GitHub exact-head CI and protected merge are separate evidence.

The owner selected the glossy #518 direction and authorized the first backend
slice. This records design/start approval, not a claim that actual IME, screen
readers, high-DPI/installed platforms, engine/model quality, persistence or
production readiness have passed. Those remain appropriate later #340/#346/#347
and runtime criteria. The earlier research-only #502/#503 UI gate is superseded.

First backend work is a narrow versioned Electron/local-text seam under #480 B,
#482 and #483: one owned local process/model and one bounded request/stream/Stop.
Real project persistence (#493), managed downloads/fit, paid-provider custody and
real local/cloud ranking (#485/#484), optional skill execution (#503) and Agent
adapters remain separately qualified work. Existing gateway, native clients,
release assets and the separate Agent roadmap are preserved.
