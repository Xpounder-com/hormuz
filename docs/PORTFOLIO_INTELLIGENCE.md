# v1.1 portfolio intelligence contract

Hormuz v1.1.0 is designed to help authorized organizations align AI budgets
and select models for declared use cases. The governing decision is
[ADR 0010](decisions/0010-v1.1-portfolio-intelligence-contract.md); the strict
[machine-readable contract](portfolio-intelligence-contract-v1.json) and its
verifier are the release gate.

This document describes the accepted implementation boundary. The
[#215 registry](REGISTRY.md) now exists in source with six additive routes and
SQLite/PostgreSQL persistence. Attribution, budgets, connectors, scorecards,
recommendations and the external pilot remain separately gated. This is not
a claim that v1.1.0 is released.

## Decision loop

```text
versioned budget plan
        |
        v
governed request + immutable v1 request/usage evidence
        |
        v
append-only primary use-case attribution
        |
        +-----------------------------+
                                      v
signed GitHub/Linear observations -> deterministic association
                                      |
                                      v
                     evidence-qualified model scorecard
                                      |
                                      v
                         reviewable recommendation
                                      |
                                      v
                  existing policy compare/preview/approval
```

One governed request has at most one active primary use case. Unattributed and
ambiguous records remain visible. A correction supersedes an attribution or
association; it does not rewrite the v1 request, usage, policy, cost, or source
event.

## Data boundary

The portfolio plane may retain:

- opaque tenant, actor/team, application, work-scope, external-object,
  provider/model, policy, rate-card, connector, request-attempt, and event IDs;
- one bounded administrator-supplied work-scope display name;
- versioned hierarchy, lifecycle, budget, metric, association, and
  recommendation metadata;
- timestamps, counts, durations, token categories, exact cost components,
  coverage, fixed outcome/quality states, and safe reason codes; and
- keyed digests and content-free provenance.

It excludes prompts, responses, source code, patches, filenames, paths,
ticket/project/initiative titles or descriptions, comments, review text,
commit messages, external repository/workspace/branch names, free-text labels,
attachments, raw webhook payloads, matched detector values, credentials, and
secret values.

An external system's numeric or opaque ID is not authority. Tenant and
connector scope come only from an authenticated server-side binding.

Canonical identities are tenant-qualified. Work scopes, budget plans,
scorecards, and recommendations use an opaque ID plus immutable version;
governed runs reference the existing request-attempt ID; external objects use
connector plus opaque source-object ID; attribution, outcome, and association
changes each have their own opaque event ID. IDs are never reassigned or
accepted across organizations.

The fixed lifecycles distinguish active, superseded, expired, invalidated,
rejected, archived, tombstoned, unmatched, ambiguous, excluded, and
inconclusive states as applicable to each entity. The exact per-entity mapping
is frozen in ADR 0010 and the machine-readable contract. A tombstone never
erases linked financial or audit facts.

Event time, connector observation time, and database ingestion time are stored
separately. Ingestion time governs receipt and retention; source time never
authorizes scope. Late data is retained and deterministically supersedes or
recomputes derived snapshots without rewriting history. Idempotent redelivery
returns the prior result only for the same canonical request; a conflicting
payload under one identity fails closed.

## API boundary

Administrator resources are additive under `/v1/admin/portfolio`; connector
ingress is additive under `/v1/connectors`. Existing v1 routes and their
request, response, authentication, error, ordering, retry, and behavior
contracts stay unchanged.

The machine-readable map freezes 21 planned routes and names each Hormuz-owned
request and response schema at version 1. It includes external-work bindings,
budget-plan activation, and explicit recommendation decisions. GitHub and
Linear webhook bodies remain provider-owned signed bytes; Hormuz owns only the
normalized metadata event and content-free ingestion receipt.

The separate [wire-schema bundle](portfolio-intelligence-wire-v1.json) defines
the actual version-1 fields, requiredness, nullability, scalar and collection
bounds, fixed enums, and field semantics. Select a payload through
`#/$defs/<schema-id>`; all references resolve within the same file. Its digest
is pinned in the accepted contract and verifier, so removing a field, weakening
a bound, broadening an enum, or changing a meaning cannot silently pass.

| Planned payload family | Shapes | Boundary |
| --- | ---: | --- |
| Typed GET query | 1 | Route-specific allowlisted filters; no tenant/actor/team authority from parameters |
| JSON mutation requests | 7 | Versioned envelope, exact references, compare-and-set/idempotency rules |
| Entity and evidence records | 8 | Closed metadata-only fields and immutable/versioned identities |
| Collection pages | 8 | At most 100 items, frozen `as_of`, explicit cursor continuation |
| Ingestion receipt and error | 2 | Content-free fixed classifications; no rejected-value reflection |

JSON mutation/response envelopes carry `schema_id` and `schema_version`;
query parameters do not. Unknown fields and duplicate JSON members are rejected.
Money is an exact decimal string with explicit currency and original cost
basis, not a binary float. Missing dimensions or evidence use explicit nulls
where specified, never fabricated zeros or silent omission.

Creating a budget-plan version only persists it. Its activation lifecycle is
null until an explicit activation establishes one; a separate active-version
pointer and generation record enforcement state. Recording an accepted
recommendation also does not activate policy. Existing authorized activation
remains a separate action after the required checks.

The bundle's transport and domain-rule sections freeze header/query parsing,
status/error behavior, version comparisons, scope resolution, evidence
eligibility, and privacy semantics alongside structural shapes. The offline
fixture checker supports only the bundle's bounded JSON Schema vocabulary and
rejects unknown keywords or remote references. It is not a runtime API or
authorization validator. The 52 minimal/populated examples are synthetic
structural fixtures, not evidence of implemented endpoints, valid live
references, customer value, or release readiness. Child issues must implement
and test the relational, temporal, authorization, and policy gates.

Collection APIs use opaque frozen-window cursors with deterministic
time-plus-ID ordering. A cursor is bound to organization, role, filters,
window, ordering, and schema. Mutations require an idempotency key. Connector
redelivery uses a verified source delivery identity instead. Unknown fields,
unsupported filters, malformed times, invalid cursors, and unauthorized scopes
fail before domain work.

Reads are safe to retry. Mutations and connector deliveries replay only through
their verified idempotency identity; provider work is never retried from this
portfolio plane.

The optional `X-Hormuz-Work-Scope` request header is Hormuz-owned metadata. It
is processed only after employee/workload authentication, authorized against
the server-side registry, and stripped before provider egress. Missing metadata
is explicitly unattributed unless policy requires it; invalid or unauthorized
metadata fails before budget reservation and provider egress.

## KPI boundary

The primary KPIs are:

1. use-case-attributed spend coverage;
2. quality-qualified cost per accepted work item; and
3. optimization lift against a declared baseline.

Each KPI needs a versioned formula, numerator, denominator, eligibility rule,
source lineage, cost basis, decision owner, review cadence, and guardrails.
Scorecards also expose attribution, pricing, connector, association, sample,
and freshness coverage.

A decisive scorecard also needs explicit, versioned per-use-case minimum
coverage and sample thresholds declared before the measurement window. Hormuz
has no universal numeric threshold. Missing thresholds or a result below them
is `inconclusive`.

The model-selection output is a Pareto frontier for one use case across cost,
quality, latency, and reliability. There is no universal model leaderboard.
Insufficient or incomparable evidence is `inconclusive`, not a forced winner.
Observational comparisons are `associated`; only a separately approved,
predeclared controlled design may report verified lift.

## Threat and failure model

| Threat or failure | Required control | Fail behavior / evidence |
| --- | --- | --- |
| Body-supplied tenant, actor, team, or connector scope | Resolve scope from authenticated identity or server-side connector binding before parsing/querying | Deny before data access; content-free stable classification |
| Forged or proxy-mutated webhook | Verify exact raw-byte signature before JSON parsing; rotate secrets explicitly | No normalized event, no raw-payload retention |
| Exact replay or conflicting delivery identity | Idempotent exact redelivery; fail closed on conflicting content for one source identity | Append safe replay/conflict status; never duplicate an outcome |
| Out-of-order, late, deleted, or superseded source state | Immutable event time, observation time, ingestion time, source revision, and supersession | Preserve both observations and deterministic current-state rule |
| Work content smuggled into metadata | Strict allowlists, byte/depth/member bounds, fixed enums, no raw payload persistence | Reject before storage; scan logs, DB, errors, exports, wheel, and evidence |
| Cross-use-case double counting | One active primary use case per attempt plus versioned deterministic association rules | Ambiguous records stay unmatched; no guessed allocation |
| Provider aggregate relabeled as final granular cost | Preserve provider-final, aggregate, estimate, allocated estimate, credit/discount, and unavailable bases | Scorecard/export carries basis and coverage; invalid relabeling fails validation |
| Budget race across replicas | Tenant-scoped atomic reservation before provider egress and all applicable ceilings | Deny before provider call; ambiguous outcomes retain uncertain reservation |
| Low-coverage or survivor-biased model winner | Frozen eligibility, coverage/sample/freshness thresholds, quality/reliability guardrails, uncertainty | Return inconclusive; never drop failed or unmatched denominator members silently |
| Employee surveillance | Role-scoped aggregate resources; no employee rank or quality score | Authorization denial and audited privileged read |
| Correlation presented as causality | Descriptive/associated/controlled labels with controlled upgrade separately gated | Observational output cannot emit a controlled or causal claim |
| Stale or manipulated recommendation | Bind immutable recommendation to policy, budget, scorecard, metrics, models, and expiry | Drift invalidates; no automatic policy application |
| Partial migration or incompatible rollback | #214 preflight, exact v1.0.0 fixture, additive schemas, explicit application/database pair rule | Fail readiness/operation closed; no provider replay or silent reinterpretation |
| Query or webhook resource exhaustion | Bounded bytes, depth, members, page size, windows, timeouts, concurrency, and retries | Reject or shed before expensive work; no partial authoritative commit |

## Authorization views

- `portfolio_admin`: authorized organization registry and budget mutation.
- `finance_viewer`: authorized financial aggregates, cost bases, exceptions, and
  coverage.
- `platform_viewer`: authorized model/policy/routing/reliability and connector
  aggregates.
- `team_lead`: one authorized team and descendant work scopes.

Existing self access remains the actor's own usage view and cannot join work
outcomes, peers, or employee comparisons.

None grants provider access, inference entitlement, policy-root authority, or
another tenant's data. Privileged reads are audited before result delivery.

## Implementation gates

The ordered release plan is [epic #226](https://github.com/Xpounder-com/hormuz/issues/226):

1. accepted contract and feature-free persistence seam (#212–#213), followed
   by accepted per-feature compatibility plans and red-first tests in #214;
2. finance evidence, work registry, attribution, budgets, and outcome contract
   (#8 and #215–#218);
3. GitHub/Linear connectors and deterministic associations (#219–#221);
4. scorecards, role views, and reviewable recommendations (#222–#224); and
5. exact final-candidate transition proof to close #214 and the pre-registered
   external candidate proof (#225).

The first #214 checkpoint unblocks the corresponding feature work only after
#212 and #213 close; feature PRs must pass their applicable transition tests.
#214 remains open until the final candidate satisfies its complete artifact,
migration, rollback, and recovery gate before the v1.1.0 tag.

The [registry transition preflight](REGISTRY_TRANSITION.md) freezes #215's
SQLite 4-to-5 / PostgreSQL 8-to-9 plan, released-v1 baseline, red-first
transition probes, and quiesced old-application/database pair rollback rule.
Its acceptance was registry-specific. The version-2 transition plan and real
registry migration tests now replace the missing-migration probes without
claiming #214's final-candidate gate.

Browser sessions, content inspection/persistence, HA/SLA claims, independent
security certification, and broad multi-profile upgrades are conditional gates,
not permissions hidden inside this contract.

## Verify the frozen contract

```bash
python3 tools/verify_portfolio_intelligence_contract.py
python3 -m unittest -v tests.test_portfolio_intelligence_contract tests.test_portfolio_wire_contract
```

Passing proves only that the accepted plan, v1.0.0 fixture, and additive-change
guard are internally consistent. It does not prove any v1.1 feature is
implemented.
