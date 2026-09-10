# Hormuz desktop companion implementation plan

Status: the visual prototype and existing-client integration are implemented
locally; release and customer qualification remain separate gates.
Prepared: 2026-09-07. Integrated baseline: repository `main` at `5815081`.

**Revised execution entry point:** [Visual specification](DESKTOP_COMPANION_VISUAL_SPEC.md).
The user prioritized fidelity to the Codenotch side-edge UI. The visual spec
overrides presentation, order, and first-deliverable acceptance in this backlog.
Use the [copy-ready agent prompt](DESKTOP_COMPANION_AGENT_PROMPT.md) for handoff.

## Outcome and scope

Use the native macOS side-edge companion as Hormuz's primary surface so activity is
understandable in seconds: whose workspace am I connected to, is the gateway ready,
what usage has it recorded, and eventually why was my request blocked?

Start with the right-edge black notch, rings, hover card, and motion specified in
the visual handoff. A menu-bar item provides recovery. The gateway remains independently
installed and operated. The app calls supported HTTP APIs; it does not read the
gateway database, import server configuration, proxy traffic, or duplicate policy
evaluation.

The fixture-driven visual build and the existing native client's supported
functionality are now combined in one executable. Budget percentages and recent
request details still require additive gateway contracts.

## Repository placement

Use the standalone Hormuz repository at `hormuz/`, not the parent MemoWiki Swift
package. The production implementation is integrated into the existing client:

```text
clients/macos/
  Sources/Hormuz/                # companion panels, control center, presentation adapter
  Sources/HormuzClientCore/      # gateway, Keychain, connector, companion values
  Tests/HormuzClientCoreTests/   # existing client and companion behavior
  Resources/
  README.md
apps/macos/HormuzCompanion/      # retained fixture-only visual prototype
```

The integrated app uses SwiftUI with narrow AppKit side-panel hosts, the existing
URLSession transport, and the existing Keychain-backed session controller. The
deployment target remains macOS 14 and no third-party runtime dependency was added.
The app build remains independent of Python wheel and container builds.

## Verified API coverage and gaps

These observations come from `hormuz/server.py`, `docs/USAGE.md`,
`docs/CONTRACTS.md`, and `docs/OIDC.md` in the local checkout; they do not establish
live deployment or current remote-main behavior.

| UI information | Existing source | Implementation decision |
| --- | --- | --- |
| Actor, team, organization | `GET /v1/gateway/whoami` | Use authenticated identity; do not infer a workspace from a hostname. |
| Service reachable / gateway ready | `GET /health`, `GET /ready` | Show separately from authentication and provider health. A ready gateway does not prove a provider credential works. |
| Current-month requests, token categories, estimated USD, denied/rate-limited counts | `GET /v1/gateway/usage` | Ready for the first connected prototype; label gateway-only coverage and estimated cost. |
| Budget limit, remaining amount, percentage | CLI usage report has budget fields; gateway usage endpoint does not | Requires an HTTP contract. Do not manufacture a progress-ring denominator or shell out to an operator CLI. |
| Recent requests and decision explanations | Metadata exists in usage evidence; the inspected gateway routes do not expose a recent-request feed | Requires a bounded, authorized read API before live UI can show it. |
| Active model/profile | No such field in the inspected identity or usage response | Defer; later show an explicitly identified recent request's model instead of implying one global active model. |
| Desktop login and refresh | Existing native `SessionController` enrollment, browser callback, refresh, and revocation flow | Integrated unchanged; the companion derives state from the same `ConnectionModel` and Keychain session. |

## Ordered implementation checklist

### Milestone 1 — Complete the visual specification

- [ ] Confirm the macOS build baseline and existing repository instructions at implementation time.
- [ ] Create the independent app package and document local run/build commands.
- [ ] Complete all five execution steps in DESKTOP_COMPANION_VISUAL_SPEC.md:
      right-edge shape, three fixture rings, hover card, settings orb, folding,
      and motion. Use its fixed geometry and content before adding networking.
- [ ] Provide explicit preview fixtures for connected, empty, loading, disconnected,
      expired credential, not ready, stale data, and incompatible API states.
- [ ] Label fixture mode visibly as Demo. Use only synthetic data.
- [ ] Give the status icon a textual accessibility label; never use color alone to
      communicate failure. Support keyboard navigation, dark/light appearance, and
      reduced motion. Keep the first version free of persistent animation.

Acceptance: deliver the native demo, screenshots, reference comparison, motion
evidence, and QA report required by DESKTOP_COMPANION_VISUAL_SPEC.md. Fidelity is
a first-deliverable requirement, not a later polish pass.

### Milestone 2 — Connect to the existing gateway APIs

- [ ] Add a typed API client for identity, usage, health, readiness, and error envelopes.
- [ ] Validate schema IDs/versions and reject unsupported contracts visibly.
- [ ] Add one endpoint configuration and one credential slot; use Keychain for the
      credential, exclude it from logs, and clear cached identity/data on disconnect
      or endpoint/account change. Do not accept provider API keys as gateway credentials.
- [ ] Require HTTPS for remote endpoints; explicitly allow loopback HTTP for local
      development. Do not forward authorization on cross-origin redirects.
- [ ] Explain that the app performs reads but an existing ingress credential is not
      necessarily restricted to read-only access. Do not claim a new read-only scope exists.
- [ ] Refresh on opening and manual request; initially poll every 60 seconds while
      open and every 5 minutes while closed, then measure and tune. Cancel work on
      sleep, refresh on wake, prevent overlapping refreshes, and back off on failures.
- [ ] Stop authentication retry loops on 401/403 and show a reconnect action. The
      prototype does not mint or renew OIDC credentials.
- [ ] Preserve last-known data with a stale label on temporary transport errors;
      clear private data on authorization failure or identity change.
- [ ] Preserve decimal money precision and token category meanings. Label spend
      as an estimate, scope as this actor's gateway-captured traffic, and period as
      the UTC calendar month. Do not add token subcategories twice.

Acceptance: against a controlled gateway, an allowed request and a denied request
produce the expected aggregate changes after refresh. Disconnect, expired token,
unready gateway, and zero usage each produce distinct honest UI states. No model
request is generated merely by opening or refreshing the app.

### Milestone 3 — Prototype decision gate

- [ ] Run a short demonstration with 3–5 intended users or pilot prospects.
- [ ] Ask them to identify the workspace, explain the spend number, and diagnose
      connection or authorization failure without coaching.
- [ ] Observe whether they reopen the widget during real work and what they expect
      when a request is blocked. Record qualitative feedback; do not add telemetry
      infrastructure for this small experiment.
- [ ] Continue if the interface improves understanding and users want it available
      during work. If it is only decorative, keep the demo and stop UI expansion.

Acceptance: record a continue/revise/stop decision and the evidence. Budget and
request-detail work starts only after this review shows a useful next interaction.

### Milestone 4 — Add budget and request explanations

- [ ] Define separate additive, versioned read contracts for budget summary and
      recent request metadata; route names and exact schemas are design work.
- [ ] Scope every result to the authenticated actor and organization; define any
      visibility into shared team/organization caps explicitly. Add cross-actor and
      cross-organization denial tests before exposing new data.
- [ ] Define budget period/reset, configured versus absent caps, limiting scopes,
      estimated usage, in-flight reservations, and unavailable values. Use existing
      server policy/accounting logic. The UI must not promise that an apparent
      remaining balance guarantees the next request will be admitted.
- [ ] Bound the recent-request response and pagination. Allowlist timestamp,
      request identifier, client/model, status, estimated cost when known, policy
      action, and sanitized reason. Exclude prompts, responses, secrets, and raw errors.
- [ ] Cover denied requests whose cost is unknown or not applicable; do not invent
      savings or turn unknown provider outcomes into successful requests.
- [ ] Add a budget ring only when a valid cap and usage basis are available. Show
      no configured cap, exhausted, and unavailable as distinct states.
- [ ] Add request detail: what happened, the server-supplied reason, and an
      appropriate next step such as contacting the workspace administrator. No
      automatic retry, model switch, policy override, or budget change.
- [ ] Preserve compatibility for existing clients and old servers; absence of a
      new API disables the new UI feature while aggregate usage still works.

Acceptance: a controlled request blocked by a real configured budget can be found
and understood in the app. Concurrent reservations and shared caps cannot result
in a misleading claim of guaranteed available spend. Tenant-isolation and contract
tests pass for the new server APIs.

### Milestone 5 — Package a pilot build

- [ ] Add an app-specific macOS CI build and meaningful client tests using shared
      fixtures. Respect repository workflow and release governance; scope expensive
      app checks to app/contract changes without weakening required gateway checks.
- [ ] Test keyboard/VoiceOver operation, menu-bar overflow, external displays,
      offline recovery, wake/reconnect, and repeated endpoint changes.
- [ ] Measure idle CPU, refresh count, and memory over an extended session; verify
      the app does not hold a sleep-prevention assertion or accumulate polling tasks.
- [ ] Produce a versioned app bundle with a declared minimum compatible API and
      Apple Silicon architecture support. Intel Macs are outside this product's
      supported, built, and tested distribution boundary.
- [ ] Add signing/notarization and download packaging for external distribution;
      verify install, first launch, credential entry/removal, and uninstall on a
      clean machine. Signing credentials remain outside the repository.
- [ ] Document manual upgrade for the pilot. Defer automatic updates, launch at
      login, and notifications until users demonstrate a need.

Acceptance: a pilot user can install, connect, inspect live usage, and disconnect
using the documented flow. App distribution evidence, gateway readiness, provider
qualification, and customer activation are recorded as separate outcomes.

## Suggested pull-request sequence

1. Complete visual demo, fixed geometry/motion, native app build, and visual evidence.
2. Existing API integration, credential handling, refresh lifecycle, and client tests.
3. Prototype feedback and an explicit scope decision.
4. Budget/read-event contracts and server implementation with authorization tests.
5. Budget ring and recent-request explanation UI with old-server fallback.
6. Packaging, CI, installation documentation, and pilot verification evidence.

The first coding-agent handoff covers Milestone 1 only, using the detailed visual
specification. Milestones 2–5 are subsequent work requiring their own implementation
contracts. This entire backlog is not a low-reasoning execution specification or
a 2–3 day delivery promise.

## Deferred features

Top hardware-notch integration, other edge placements, Windows/Linux companions, subscription scraping for Codex
or Claude, chat, dashboards, policy editing, provider-key management, automatic
retries, full desktop SSO, multi-workspace switching, updater infrastructure, and
employee rankings are outside this initial scope.

The first signature interaction is the right-edge shape and rings revealing a
fluid hover card. The later functional journey is request blocked → open Hormuz
→ see the exact decision and a useful next step. Both preserve the small visual
surface established by the reference.
