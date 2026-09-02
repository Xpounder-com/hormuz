# Native attempt finance transition checkpoint

Hormuz 1.1.0 needs a finance fact at the same grain as the action management
can control: one governed provider attempt. This checkpoint freezes that fact
before runtime code changes. It extends the accepted rate-card and work-budget
foundations; it does not claim that provider invoices, reconciliation, shared
cost allocation, or live finance validation exist.

The machine-readable decision is split between
[`finance-transition-plan-v3.json`](finance-transition-plan-v3.json) and
[`finance-attempt-evidence-contract-v1.json`](finance-attempt-evidence-contract-v1.json).
The former binds the exact accepted predecessor and transition procedure. The
latter freezes the information semantics that accounting, analytics, and
management will consume.

## Management result

Each post-migration request attempt will freeze its exact configured-route
price-table coordinates in the attempt root before provider egress. Each
terminal attempt will then have one immutable sidecar
that can be rolled up by organization, team, actor, application, policy, work
scope, configured route, provider-reported model, and rate card. The row keeps
three things separate:

1. allowlisted provider-native usage as bounded canonical JSON;
2. nullable normalized fields for safe queries; and
3. Hormuz's configured-rate estimate and exact rate-card provenance.

`null` means unknown or not returned. Zero means the provider explicitly
reported zero. Components such as cache creation by TTL remain distinct from
their totals so reporting cannot double count them. Service tier and inference
location remain queryable dimensions when the provider returns them. A
configured estimate is never relabeled as provider-final cost.

The estimate has an explicit available/unavailable state. Missing or incomplete
native usage produces a null amount with a bounded reason; it never becomes a
zero. The conservative budget reservation remains a separate hold and is never
substituted for terminal cost. The frozen attempt binding survives a crash, so
a later stale-attempt sweep retains the exact rate-card ID, version, digest and
currency even when no native usage was observed.

## Attempt and coverage semantics

Pending attempts have no finance sidecar. Successful, failed, and rate-limited
terminal attempts have one sidecar linked to their usage event. An ambiguous
outcome has one sidecar with a null usage link and keeps the existing
conservative budget hold. A stale-attempt sweep records an absent observation
without contacting the provider.

Attempts completed before the migration are an explicit coverage gap. Hormuz
will not invent native details, reprice old usage, or backfill guessed zeros.
Every retry is a new attempt and therefore a new sidecar; no persistence or
recovery path may automatically replay provider work.

## Parsing seam

The runtime successor should make one contained refactor: the existing response
usage parser should produce a typed provider-native observation and the
unchanged version-1 `Usage` projection from the same parsed value. This avoids
two parsers drifting while leaving the public usage contract intact. Provider
profiles are versioned independently, bounded, and ignore fields outside their
allowlist.

The initial profiles cover the currently documented OpenAI Responses usage and
actual service-tier fields, plus Anthropic Messages usage, cache-creation,
thinking, server-tool, service-tier, and inference-location fields. These fields
are observations, not invoice authority. A reviewed successor contract is
required to add or change a profile.

## Storage and transaction boundary

The implementation plan reserves SQLite schema 11 and PostgreSQL schema 15 for
one append-only `gateway_finance_attempt_evidence` table plus five additive
price-binding columns on `gateway_request_attempts`. Legacy roots carry an
explicit `legacy_unavailable` state with four null coordinates and remain a
coverage gap; every post-migration root must carry `configured` plus all four
coordinates before provider egress. PostgreSQL uses forced
row-level security and the existing tenant transaction context. The sidecar is
also a versioned source event in the commit audit chain.

Finalizing an attempt must commit its terminal event, usage event when one is
required, finance sidecar, work-budget reconciliation, provider timing, and
audit-chain entries in one transaction. Any failure rolls the whole transition
back. No runtime table creation is allowed.

## What this preflight changes

- binds merged main `4e3133f19db4c34d7a181848ebc36754bce164ea`
  as the real predecessor by deterministic archive digest;
- reserves the additive SQLite 10-to-11 and PostgreSQL 14-to-15 migrations,
  including the begin-time price binding needed for crash-safe analytics;
- provides red-first missing-migration, rollback, retry, old-binary refusal,
  backup/restore, forward-recovery, and package-boundary witnesses; and
- freezes the sidecar, provider profile, coverage, provenance, and atomicity
  decisions.

It changes no runtime schema version, database table, parser, provider request,
HTTP route, CLI command, public wire format, active budget, tag, release, or
deployment.

## Acceptance boundary

This source checkpoint still requires exact-head technical review, every
protected check, a normal merge, and exact merged-main transition evidence
before its preflight can be accepted on issue #214. Runtime implementation then
requires a separate gated successor. Issue #8 stays open for provider billing
imports, reconciliation, allocation, accounting-period policy, management
delivery, and live finance validation.

## Local verification

From the repository root:

```console
python3 tools/verify_finance_native_attempt_transition_plan.py
python3 -m unittest -v tests.test_finance_native_attempt_transition_plan
python3 -m unittest -v tests.test_sqlite_finance_native_attempt_transition
python3 -m unittest -v tests.test_finance_native_attempt_packaging
```

PostgreSQL and actual-predecessor cases run in the protected CI transition job
with disposable storage and the digest-bound predecessor interpreter.
