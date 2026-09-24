# Evidence-qualified model scorecards for #222

This provider-free runtime builds reproducible model scorecards for one
declared organization and use-case version. SQLite schema 17 and PostgreSQL
schema 22 persist immutable snapshots and build audit facts. The pure kernel
accepts only closed, metadata-only evidence and emits the public
`hormuz.model-scorecard` wire shape. It does not expose a route; role-scoped
decision views and recommendations remain issue #223.

## KPI dictionary

Every cohort is keyed by organization, exact use-case version, half-open time
window, actual provider/model/version, client/application, policy version, rate
card version, selected cost basis, currency, connector set, and association
rule version. Requested model IDs, routed model IDs, and provider-reported
actual model identity remain separate dimensions. Missing actual version makes
the comparison inconclusive, and an attempt whose actual model differs from
its cohort is rejected.

The primary readiness KPI is **use-case-attributed spend coverage**:

`eligible governed spend with the required attribution / all eligible governed spend`

The scorecard also reports separate pricing, attribution, linked-outcome,
association, connector, excluded, governed-attempt, governed-spend, and
external-outcome coverage. Missing evidence and a zero denominator remain
inconclusive; neither becomes a fabricated numeric zero.

The primary economics KPI is **quality-qualified cost per accepted work item**:

`sum of selected-basis cost for every eligible attempt, retry, failure, and denial / distinct source-qualified accepted work items`

Requests are never treated as independent quality samples. Each attempt must
carry exactly one component for the selected item-level cost basis. The output
keeps provider-final, configured-rate-card estimate, allocated estimate,
provider aggregate, credit/discount, and unavailable evidence in distinct
components with their own provenance. Provider aggregates and credits are
display evidence and are not silently reassigned to individual work items.

The primary decision KPI is **optimization lift versus the declared baseline**:

`(baseline quality-qualified cost - cohort quality-qualified cost) / baseline quality-qualified cost`

The baseline is versioned input, and lift is emitted only when both cohorts
clear their evidence policy, currency and stratum comparability, cost
denominator, and uncertainty requirements. The result is always labelled
`associated`. `controlled_design` is null; this runtime never upgrades
observational evidence to verified causal lift.

Driver evidence includes actual model mix, input/output/cached/reasoning token
counts, p50/p95 latency, retries, first-pass success, fallback, and denial
rates. These drivers do not replace the primary KPIs or guardrails.

## Minimum evidence and uncertainty

The eligibility policy pins its rule digest, declaration time, minimum
coverage, minimum distinct-work-item sample, maximum staleness, and required
strata. Quality, reversion/escaped-defect, reliability, privacy, budget,
coverage/sample, freshness, and model comparability are mandatory guardrails.
Any failed stratum fails its cohort; missing, inconsistent, or incomplete
strata remain inconclusive. A high-volume or low-cost cohort therefore cannot
hide a guarded stratum failure.

Quality and first-pass intervals use 95% Wilson intervals over distinct work
items. Cost per accepted work item uses a work-item cluster jackknife that
keeps all retries and failures inside their source item. Latency uses a
work-item delete-one range. Coverage ratios retain exact source numerators and
denominators. Optimization-lift bounds conservatively propagate the two cost
intervals. Fewer than three independent work-item clusters produces an
inconclusive interval.

The Pareto set covers cost, accepted-work quality, p95 latency, and first-pass
reliability. One cohort dominates another only when its complete 95% interval
is no worse on every axis and strictly better on at least one. Cohorts with an
inconclusive axis cannot eliminate another cohort. The kernel does not emit a
composite rank, hidden weight, or universal leaderboard.

## Reproducibility and lineage

Every metric, aggregation rule, eligibility rule, guardrail, association rule,
policy, rate card, scope, source fact, and cohort has an exact version or
digest. The frozen fixture at
`tests/fixtures/scorecard/runtime-v1.json` contains the full input and expected
evaluation. Reordering cohorts, strata, work items, attempts, or connector IDs
does not change the result or lineage digest.

Adversarial tests cover missing and zero coverage, duplicate source facts,
duplicate attempt identities, non-contiguous retries, pooled actual models,
missing model versions, cost-basis relabelling, failed guarded strata,
noncanonical finance values, late/out-of-window attempts, invalid version
lineage, and forbidden content/person fields. Earlier association fixtures
cover duplicate deliveries, retries, reopenings, reversions, late evidence,
unsupported events, and corrected decisions before facts reach this kernel.

## Storage and authorization

Only a configured `portfolio_admin` may build a snapshot. Authorization occurs
before parsing input or opening storage. The referenced use-case version must
exist, be active, and belong to the authorized organization. A scorecard family
cannot change its use-case version; each successor must increment exactly one
version and name the prior version. Replaying the exact input returns the
existing snapshot without appending a second audit fact. Reusing a version for
different input fails closed.

The tenant-keyed tables are:

* `portfolio_scorecard_audit_events`
* `portfolio_model_scorecard_snapshots`

They store the canonical metadata-only input and evaluation, their digests,
source-set digest, scope/window identity, state, expiry, decision owner, and
version lineage. This makes a stored result independently recomputable. SQLite
triggers reject update and delete. PostgreSQL enables and forces tenant RLS,
revokes PUBLIC, grants the runtime role only `SELECT` and `INSERT`, and rejects
update, delete, and truncate. The accepted complete schema-22 ACL boundary is
236 entries with SHA-256
`4aef5982da3f81a352f813554de5721580f0ad5f0a93dda529199b545fee5a20`,
measured twice from clean managed-role bootstraps after revoking PostgreSQL's
default PUBLIC function grants.

The input and output reject prompt/response text, issue titles, descriptions,
comments, paths, credentials, raw provider payloads, employee identities,
individual quality scores, and person ranks. The decision owner is an opaque
accountable owner ID, not an evaluated employee dimension.

## Verification and nonclaims

Run the provider-free evidence from a clean source tree:

```bash
python3 tools/verify_scorecard_runtime_plan.py
python3 -m unittest -v \
  tests.test_scorecard_evidence_reference \
  tests.test_scorecard_kernel \
  tests.test_scorecard_runtime \
  tests.test_scorecard_runtime_plan
```

With an owned disposable PostgreSQL service, also run:

```bash
HORMUZ_TEST_POSTGRES_DSN=postgresql://... \
  python3 -m unittest -v tests.test_postgres_scorecard_runtime
```

These checks do not authorize a live connector or customer workspace, prove
complete external history, establish causality, provide a public recommendation
route, prove realized savings, accept the final v1.3 candidate, or publish a
release.
