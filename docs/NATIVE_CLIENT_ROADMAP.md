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

## First implementation slice

Issue #330 starts with identity and personal-usage validation in a small Rust
crate and shared compatibility vectors consumed by Rust, the existing Swift
models and the existing Python gateway validators. The gateway and client have
different validation responsibilities; expected differences must be recorded
rather than silently changing either implementation. See
[the fixture contract](../clients/contracts/README.md).

Connection profiles, session transitions, freshness, settings, client status,
the complete safe-error catalog and native integrations remain subsequent #330
or dependent-issue slices. The initial library is unpublished and is not loaded
by the shipped app. The gateway and app version remain v1.2.0.
