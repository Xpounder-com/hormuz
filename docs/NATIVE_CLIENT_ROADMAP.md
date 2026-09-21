# Native companion roadmap

Track the program in [epic #329](https://github.com/Xpounder-com/hormuz/issues/329).
The architecture is one resident native app per OS containing a shared Rust
client library. A supervised relay and optional optimizer run only for active
AI sessions. The Python gateway remains server-side.

## Planned versions

The latest published product release at planning time is v1.2.0. The existing
v1.3.0 Portfolio Intelligence milestone remains independent. These additive
desktop improvements use new minor-release targets. A target is neither a
published release nor permission to change existing package/version identities.
Patch releases are reserved for compatible fixes after a feature release.

| Target | Improvement |
|---|---|
| v1.4.0 | [#330 — Define shared Rust client contracts and compatibility fixtures](https://github.com/Xpounder-com/hormuz/issues/330) |
| v1.4.0 | [#331 — Build a minimal resident Windows tray and companion panel](https://github.com/Xpounder-com/hormuz/issues/331) |
| v1.4.0 | [#332 — Measure current Mac and empty Windows companion footprint](https://github.com/Xpounder-com/hormuz/issues/332) |
| v1.5.0 | [#333 — Add secure platform services for the shared client core](https://github.com/Xpounder-com/hormuz/issues/333) |
| v1.5.0 | [#334 — Implement bounded cancellable gateway transport in Rust](https://github.com/Xpounder-com/hormuz/issues/334) |
| v1.5.0 | [#335 — Port enrollment identity refresh and sign-out state to Rust](https://github.com/Xpounder-com/hormuz/issues/335) |
| v1.5.0 | [#336 — Expose authoritative usage snapshots with explicit freshness](https://github.com/Xpounder-com/hormuz/issues/336) |
| v1.5.0 | [#337 — Centralize companion refresh scheduling and power behavior](https://github.com/Xpounder-com/hormuz/issues/337) |
| v1.5.0 | [#338 — Share companion interaction state and event-sequence fixtures](https://github.com/Xpounder-com/hormuz/issues/338) |
| v1.5.0 | [#339 — Coordinate single-instance lifecycle reopen and local helpers](https://github.com/Xpounder-com/hormuz/issues/339) |
| v1.5.0 | [#340 — Qualify the connected Windows companion end to end](https://github.com/Xpounder-com/hormuz/issues/340) |
| v1.6.0 | [#341 — Port governed client launch and on-demand streaming relay to Rust](https://github.com/Xpounder-com/hormuz/issues/341) |
| v1.7.0 | [#342 — Port the optional context optimizer to Rust with measured parity](https://github.com/Xpounder-com/hormuz/issues/342) |
| v1.8.0 | [#343 — Build the GTK4 Linux shell and desktop capability fallbacks](https://github.com/Xpounder-com/hormuz/issues/343) |
| v1.9.0 | [#344 — Expose a narrow Rust native UI API and owned Swift C bridge](https://github.com/Xpounder-com/hormuz/issues/344) |
| v1.9.0 | [#345 — Integrate the Rust core into the existing native Mac companion](https://github.com/Xpounder-com/hormuz/issues/345) |
| v1.9.0 | [#346 — Qualify platform packages safe updates and rollback-compatible settings](https://github.com/Xpounder-com/hormuz/issues/346) |
| v1.9.0 | [#347 — Enforce desktop footprint and interaction release acceptance](https://github.com/Xpounder-com/hormuz/issues/347) |

## Delivery and acceptance

1. v1.4.0 establishes shared contracts, a minimal Windows panel and measured baselines.
2. v1.5.0 is the first connected Windows milestone: secure sign-in, real scoped
   usage, complete interactions, reliable folding/reopening and a measured idle footprint.
3. v1.6.0 adds governed launch/relay; v1.7.0 separately qualifies a Rust optimizer.
4. v1.8.0 adds Linux and capability fallbacks; v1.9.0 integrates the existing Mac
   views with Rust and qualifies platform distribution and performance.

Issues contain the dependency graph, verification commands/evidence requirements,
and explicit closure boundaries. Work may proceed independently where the graph
allows it; release order remains a planning assumption, not a date commitment.
The current Mac footprint can be measured before the Windows baseline exists.

Preserve secure platform credential custody, serialized refresh and pending
recovery states, identity binding, authoritative gateway scope/cost labels and
active client lifetimes. Missing/stale usage must remain distinct from zero.
Display snapshots and public evidence must contain no credentials or model content.

No idle relay/tokenizer, no unchanged-view continuous redraw and no orphan helper
are structural acceptance goals. Numerical budgets follow measured release-build
baselines. Native keyboard/accessibility, focus, display, sleep/reopen and clean
install/upgrade evidence are required for each declared platform. Compilation
alone does not establish platform support or a measured footprint benefit.

## Implementation progress

Issue #330 establishes identity, personal usage, connection profiles, session
and reading status, context preferences and the complete safe-error catalog in
a small Rust crate. Compatibility vectors are consumed by Rust, the existing
Swift models and the existing Python validators. The gateway and client have
different validation responsibilities; expected differences must be recorded
rather than silently changing either implementation. See
[the fixture contract](../clients/contracts/README.md).

The #331 Windows preview is an explicitly synthetic native shell. Its CI
process and UI Automation observations do not establish manual platform
acceptance. The [#332 baseline checkpoint](evidence/native-client-baseline-2026-09-19/README.md)
records verified historical artifact sizes and preliminary Mac preview
measurements; #332 also records later short Windows CI observations.
Complete platform measurements are still required before setting budgets.

Issue #334 adds a separate unpublished `1.5.0-dev.1`
[Rust transport](../clients/rust/transport/README.md) with one bounded runtime,
verified TLS, request cancellation and no automatic replay after an ambiguous
outcome. It is not loaded by the shipped app. The gateway and app version remain
v1.2.0; a library test pass is not a connected-companion or release claim.

Issue #335 adds the unpublished `1.5.0-dev.1`
[session controller](../clients/rust/session/README.md): bounded browser
enrollment, serialized refresh, persisted recovery states and sign-out. Its
Swift-compatible credential codec and shared transitions preserve the existing
native record. #336 builds authoritative snapshots on that coordinated
session. These source milestones do not bump the shipping gateway/app version
or rename historical v1.1 portfolio contracts.

Issue #336 adds coordinated immutable usage snapshots to that same unpublished
session library. Identity/profile/credential changes clear cached data, obsolete
requests cannot publish, and a single change slot preserves explicit
current/stale/offline/authentication states with authoritative personal labels.
Issue #337 adds the shared event-driven scheduler to the same unpublished
library: one outstanding dashboard job and one wake-up deadline, tunable
45-second summary/7-second detail hypotheses, bounded completion debounce,
offline backoff, age-based staleness and independent lock/sleep gates. Synthetic
traces verify these library decisions and separate operation cancellation.
**#337 remains open** for native-shell qualification and measured idle/locked/resume
behavior. The existing Mac app does not consume this scheduler; the Windows
development shell now wires it, but native power/footprint and release acceptance
remain unproven. See the [scheduler integration contract](../clients/rust/session/README.md#central-dashboard-scheduler-337-source-checkpoint).

Issue #338 adds an unpublished `1.5.0-dev.1`
[interaction reducer](../clients/rust/interaction/README.md) with explicit
visibility, hover/pin, settings and observed keyboard-focus state. Shared traces
cover ordered inputs, stale timer cancellation, dismissal and reopening; seven
traces also exercise the existing Swift hover/navigation models. The shipping
Swift sources remain unchanged. The Windows development shell now consumes the
shared whole-panel visibility, pointer/focus and timer policy, including ordered
Hide/Reopen, explicit Fold and stale native callback cancellation. Synthetic
native timer/window tests and CI UI Automation exercise that integration.
**#338 remains open** for native metric/detail-card and settings wiring, physical
hover/focus/outside-click/reopen and keyboard/screen-reader acceptance. The
checkpoint does not complete #331, native interaction or release gates.

Issue #339 adds a separate native application-instance lease and worker-side
[helper lifecycle policy](../clients/rust/platform/README.md#application-ownership-and-helper-lifecycle)
to the unpublished platform library. Kernel-lock tests cover competing startup,
crash recovery and refresh-lock independence; deterministic helper tests cover
bounded restart, client draining and quit/update behavior. **#339 remains open**
for native activation/login registration, real helper supervision and crash
containment, sleep/resume and shell integration. No IPC or native process
adapter is supplied by this source checkpoint.

## Windows integration checkpoint

The unpublished Windows executable now implements #339 private single-instance
activation and #340 connected sign-in, scoped usage and sign-out through the
shared session controller. Native session/power/network events drive the shared
scheduler, and shutdown joins credential work before releasing app ownership.
The ordinary launch is connected; the explicit `--preview` mode remains
synthetic and is the scope of the existing automated footprint measurements.

See the [Windows development guide](../clients/rust/windows/README.md) for exact
implementation and test boundaries. Source integration and synthetic CI do not
complete #339/#340 or the outstanding v1.4.0 gates: login/helper adapters,
authorized-account end-to-end proof, native desktop acceptance, connected
measurements, signing and clean installation remain separate. The Windows
package is `1.5.0-dev.1`; the published product remains v1.2.0.

Issue #341 adds the separate unpublished `1.6.0-dev.1`
[governed relay](../clients/rust/relay/README.md). Its source tests exercise
authenticated loopback forwarding, exact Off bytes, streamed responses,
on-demand use of the existing Python optimizer, relay-owned first-party
optimizer cancellation and a fake client's listener lifetime. **#341 remains
open** for native-shell integration, installed-client proof, descendant
containment and platform quit/update acceptance. Neither the Windows panel nor
shipping Mac app launches this Rust relay yet.
The Linux library has a fail-closed source guard for a transient user systemd
service and a host-conditional detached-descendant fixture. It needs a real
user-manager run, native Linux secure-store integration, and shell lifecycle
wiring before Linux descendant containment or support can be accepted.
