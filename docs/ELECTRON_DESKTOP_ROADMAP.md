# Hormuz Electron desktop roadmap

Planning baseline: 2026-10-13, main
`46449d2a3f05de09d7d0a18c9688e189a6c3a68e`.
Track delivery in [epic #479](https://github.com/Xpounder-com/hormuz/issues/479).
The [backlog reconciliation](DESKTOP_BACKLOG_RESET_2026-10-13.md) records the
disposition of every open issue at the audit boundary.

This is a roadmap and issue-planning change. It does not implement Electron,
download models, call providers, publish a release, activate a service, or
authorize application implementation. A separate explicit work order starts
implementation. Release identity and delivery dates remain unassigned.

## Product outcome

One shared Electron + Svelte/TypeScript interface for declared Windows, Linux
and macOS targets. The primary experience is a small glossy prompt bar with
smooth curved edges, refined type, depth and restrained motion. It contains a
prompt, Send, a model picker and a small Auto control. Setup, reasons,
preferences and expanded answers appear when needed.

While a person types, Auto predicts an eligible model and shows Local or Cloud.
Selecting a model pins that choice; explicitly selecting Auto resumes ranking.
The app manages local llama.cpp setup, downloads and model readiness, alongside
an optional qualified paid-provider connection. Local setup does not require
an organization account or a separate model-management app.

The exact glossy finish is not selected. The existing prompt-bar exploration
and the glossy image concepts are simulations/design references, not approved
runtime behavior or installer qualification. Stage D1 records the selected
reference before implementation. Native desktop blur is an optional enhancement;
the shared surface needs a beautiful legible opaque fallback.

## Reuse and architecture

```text
packaged Svelte prompt / picker / chat / setup
    -> narrow validated Electron host bridge
    -> existing Hormuz policy, admission, work routing and custody owners
    -> qualified managed llama.cpp process OR qualified paid provider
```

Preserve the shipped Mac app, CLI/agent adapters and existing gateway contracts
until replacement installation, opt-in migration and rollback are proven.
Electron changes presentation; it does not require a gateway rewrite, a new
inference compiler or a blanket Rust port.

Reuse the accepted Rust profile/custody/transport/session/snapshot/lifecycle
contracts where qualified. They are not automatically a drop-in Electron
backend: stage D0 chooses the process/IPC boundary and one authority for each
state machine. Existing control-plane JSON transport is not the multi-GB model
download or streaming inference transport. The existing agent relay launches
Codex/Claude; it is not the llama.cpp model host.

Current source already handles OpenAI Responses, Chat Completions and Anthropic
Messages. Qualify the exact local/cloud request and stream envelopes rather
than treating the phrase OpenAI compatible as complete conformance. Keep
upstream llama.cpp UI/engine versions matched, retain required notices, and
adapt useful components through Hormuz services. Avoid the private `/tools`
endpoint and unrestricted agent/MCP behavior.

The renderer receives bounded display data and typed operations. Credentials,
arbitrary filesystem/process access and policy authority remain in the qualified
host/backend. Model text, Markdown and catalog metadata are untrusted display
inputs. Heavy inference runs outside the UI process. Choose between packaging
the existing backend and a configured gateway binding in D0; users must not
need a global Python, Node, Rust or development toolchain for the supported
installed path.

## Auto contract

Reuse [ADR 0014](decisions/0014-customer-local-ai-work-and-exact-reuse.md), the
approved baseline, continuing-session affinity and existing work evidence.
This does not promise an objectively best model or create a second learning
ledger.

1. Preview reads a bounded available-state snapshot and is pure. It performs
   no provider/classifier calls, downloads, model loads, budget/exploration
   reservations, ledger writes or durable prompt storage. Existing
   `choose_route` has stateful exploration/selection behavior and cannot be
   used directly on each keystroke.
2. Policy, identity, privacy, cap, capability/context, engine/model readiness
   and verified provider availability filter candidates before ranking.
3. A pin is explicit execution intent, never an authorization bypass. Passing
   a model alias alone into an execution path that can rerank is insufficient.
   An unavailable pin remains visibly blocked and is never silently replaced.
4. Send revalidates current policy/inventory/budget, discloses a changed
   preview, then commits one route/attempt for streaming, cancellation and
   the receipt. Continuing opaque/tool/provider state preserves affinity.
5. Local-only/private work cannot fail over to cloud. Other fallback remains
   within existing explicit qualified one-hop rules; partial or uncertain
   attempts are not replayed. Estimates, actuals and unknown costs remain
   distinct. Local means inference on this machine, not free hardware/energy.

The existing opt-in exact answer reuse boundary remains intact. It does not
authorize indefinite conversation storage. First scope uses ephemeral chat;
any history or attachments require a separate explicit retention, export/delete,
profile isolation and migration contract. Catalog metadata, downloads, updates
and telemetry have separately disclosed network controls from prompt egress.

## Delivery sequence and gates

| Stage | Work | Completion prerequisites | Exit evidence |
| --- | --- | --- | --- |
| D0 | [#480 architecture, support and migration contract](https://github.com/Xpounder-com/hormuz/issues/480) | None | Product modes, backend/custody owners, protocol subset, support matrix, donor-PR disposition and coexistence are explicit. |
| D1 | [#481 glossy visual system and simulation](https://github.com/Xpounder-com/hormuz/issues/481) | D0 scope | Selected reference and tested idle/type/pin/blocked/setup/stream states; keyboard, accessibility, narrow layout and material fallback. |
| D2 | [#482 Electron launcher and host bridge](https://github.com/Xpounder-com/hormuz/issues/482) | D0, D1 | Packaged isolated renderer, narrow IPC, owned application instance and working platform activation/recovery. |
| D3a | [#483 local engine and model setup](https://github.com/Xpounder-com/hormuz/issues/483) | D0, D2 | Verified engine/model packages; bounded recoverable download/load/unload; exact owned-process and local protocol proof. |
| D3b | [#485 governed paid execution baseline](https://github.com/Xpounder-com/hormuz/issues/485) | D0, D2 | One named provider, qualified envelopes/stream/cancel, preserved custody/policy/accounting and unknown-outcome behavior. Runs alongside D3a; no Auto completion prerequisite. |
| D4 | [#484 pure Auto preview and model pin](https://github.com/Xpounder-com/hormuz/issues/484) | D3a, D3b qualified baseline | Typing has no execution effects; pin/Auto intent, eligibility races, revalidation and one committed route are tested. |
| D5 | [#337 scheduling](https://github.com/Xpounder-com/hormuz/issues/337), [#338 ordered interaction fixtures](https://github.com/Xpounder-com/hormuz/issues/338), [#339 owned lifecycle](https://github.com/Xpounder-com/hormuz/issues/339) | Their frozen contract inputs and relevant new integrations | Crosscutting power, stale callback, reopen/cancel/quit/update and helper ownership acceptance. Existing foundations can be reused earlier; closure is not a circular prerequisite to D2/D3. |
| D6 | [#346 packages and safe migration](https://github.com/Xpounder-com/hormuz/issues/346), [#347 responsiveness and footprint](https://github.com/Xpounder-com/hormuz/issues/347) | Integrated candidates from D2–D5 | Per-target candidate artifacts, trust/update/settings proof and separately measured UI/inference resources. Candidate evidence collection precedes final platform acceptance. |
| D7 | [#340 installed desktop journeys](https://github.com/Xpounder-com/hormuz/issues/340) | All applicable earlier gates | Every declared OS/architecture/desktop passes installed setup, Auto/pin, generation/cancel/recovery and accessibility. Independent user evidence is recorded separately. |

Candidate packaging and platform evidence can advance in parallel. Neither
#346 nor #347 requires #340 closure; #340 closes after their applicable evidence.
The crosscutting #337/#338/#339 contracts are integration inputs, not completed
prerequisites to starting the shell and runtime slices. Owners and dates are
assigned before implementation; stages are not automatic release versions.

## First qualification matrix proposal

These are candidates for D0, not newly supported targets. Electron provides a
shared UI codebase and automated packages; platform services, CPU/GPU, drivers,
desktop environment and installer behavior still require exact qualification.

| Dimension | First scope to qualify | Later/conditional scope |
| --- | --- | --- |
| Mac | Apple Silicon arm64; exact tested OS versions, Keychain and notarized package | Intel only after native engine/package/clean-machine proof. |
| Windows | Windows 11 x64; exact tested build, secure custody and launcher/window recovery | Other Windows versions/ARM64 only after runtime and maintenance decisions. |
| Linux | Ubuntu 24.04 LTS x64 GNOME; separately tested X11/Wayland, Secret Service and ordinary-window recovery | KDE/other distributions/compositors/ARM64 are separate rows. |
| Engine | CPU baseline on declared architectures; one curated small text GGUF and one loaded local model | Metal/CUDA/Vulkan acceleration only for exact independently qualified configurations. |
| Model fit | Actual weights, context/KV cache, RAM/VRAM and system headroom measured | File size alone does not guarantee fit or acceptable speed. |
| Cloud | One first qualified provider and protocol; clearly named personal or managed connection mode | More providers, subscription entitlement and hosted billing are separate. |
| UI | Shared gloss/curves/type/motion with opaque, reduced-motion and high-contrast fallback | Native backdrop blur/shortcut/focus/placement vary by OS/desktop capability. |

Model setup distinguishes offered, downloading, verified/downloaded, loaded,
ready, too large and unavailable. Installation is atomic and recoverable;
removal is limited to app-owned files. Network work follows bounded concurrency,
timeouts and finite retry/backoff rules. The shell, download and inference
process trees are finite owned resources.

## Scope and estimates

First useful candidate: text rewrite, private summary and code explanation;
local setup; optional one paid connection; streaming/cancel; conservative Auto;
manual override; visible recovery. A development vertical slice can start on
one OS while other target packages are prepared. An all-three-platform claim
requires all three qualified rows.

Defer image/audio/video inference, arbitrary marketplace loaders, agent/MCP or
computer control, autonomous background tasks, fleet management, general Rust
rewrites and broad hardware support. Optional Rust optimization #342 is not a
first-chat prerequisite. Existing agent-relay qualification #341 is required
only for the precise agent path the desktop chooses to expose.

Portfolio #226, enterprise #9/#11/#13 and Hosted Pro #437 remain independent
programs. Their real unresolved gates stay open. Desktop paid-model use is not
a Hormuz subscription, hosted-service activation or enterprise certification.

Do not assign a calendar from the mockups. D0 and baseline protocol/packaging
studies should establish team capacity and risks before dates. Numerical startup,
idle memory/CPU and interaction budgets follow exact Electron release-build
measurements; old small native-preview ceilings are not carried over blindly.

## Closure and source references

Each slice needs exact commit/artifact evidence, meaningful relevant checks,
normal protected review and explicit unsupported cases. Simulations, library
tests, compile success and narrow local requests are distinct from installed
acceptance, independent user results and release publication.

Current-main seams: `hormuz/server.py`, `hormuz/work_gateway.py`,
`hormuz/work_runtime.py`, `clients/contracts/`, `clients/rust/{core,platform,
transport,session,interaction,relay,windows,context-optimizer}`,
`clients/macos/`, `docs/AI_WORK_RUNTIME.md` and ADRs 0013/0014. Donor PR
[#458](https://github.com/Xpounder-com/hormuz/pull/458) is still a draft proposal;
its source/review/CI is not current-main acceptance. Dependency maintenance
[#460](https://github.com/Xpounder-com/hormuz/pull/460) remains a separate PR.

Framework references: [Electron process model](https://www.electronjs.org/docs/latest/tutorial/process-model),
[window materials](https://www.electronjs.org/docs/latest/api/browser-window),
[Linux/global shortcuts](https://www.electronjs.org/docs/latest/api/global-shortcut),
[llama.cpp UI](https://github.com/ggml-org/llama.cpp/blob/master/tools/ui/README.md)
and [engine backends](https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md).
