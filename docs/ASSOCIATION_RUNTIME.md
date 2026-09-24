# Run-to-outcome association runtime for #221

This runtime implements the provider-free association slice reserved by
[association-transition-plan-v1.json](association-transition-plan-v1.json).
SQLite schema 16 and PostgreSQL schema 21 add an explicit administrator link,
a deterministic association decision, bounded pagination, and an internal
metric-reference join over metadata already held by Hormuz. The fixed source
record is [association-runtime-plan-v1.json](association-runtime-plan-v1.json).

The public administrator routes are:

* `POST /v1/admin/portfolio/run-work-links`
* `GET /v1/admin/portfolio/run-work-links`
* `POST /v1/admin/portfolio/associations`
* `GET /v1/admin/portfolio/associations`

`AssociationRepository.metric_reference` is an authenticated and audited
internal reference operation. It is not a public scorecard route and does not
implement issue #222.

## Authorization and evidence boundary

Every operation authorizes a typed `PortfolioPrincipal` as a portfolio
administrator before opening a storage transaction. Link creation and
association evaluation also authorize the configured connector before reading
connector evidence. Tenant identity comes from the authorized principal; it is
not accepted from a request body or cursor.

A link is an explicit administrator assertion. It must bind one immutable
request attempt, attribution event, connector source event, external object,
verified source revision, historical scope version, and historical binding.
The selected source event must still be the connector-authoritative current
state for that external object. A later authoritative revision makes a stale
link ineligible until an administrator appends a correction.

This evidence can support `associated`; it does not prove that a model run
caused an external outcome. Temporal proximity, current actor identity,
mutable bindings, or matching text never create or break a link.

## Append-only links and decisions

The link request uses a tenant, actor, and versioned domain-separated HMAC for
idempotency. Repeating the exact request returns the committed event. Reusing
the key for different content is rejected. A correction or tombstone must name
the current prior event and appends one compare-and-set successor; concurrent
or stale corrections fail without rewriting history.

The evaluator uses the fixed `explicit-run-work-link-v1` rule and a request
window whose start is inclusive and end is exclusive. For each source object:

* zero eligible links produces `unmatched`;
* one eligible link produces `associated`;
* multiple eligible links, or an unresolved equal or incomparable source
  authority conflict, produces `ambiguous`;
* unsupported, tombstoned, stale, or superseded evidence produces `excluded`.

Reevaluation appends a decision with its rule digest, window digest and bounds,
evaluation time, snapshot sequence, candidate count, and prior decision. It
never updates an older decision. Late observations, retries, reopenings,
reversions, and later authoritative revisions therefore remain replayable.

List operations bind the authorized actor, authority digest, filters, as-of
time, snapshot sequence, and final sort key into an opaque server-side cursor.
Cursors expire after one hour. A cursor cannot be replayed by another tenant or
actor or with changed filters.

## Storage and audit boundary

The five schema-16/21 tables are tenant keyed:

* `portfolio_association_audit_events`
* `portfolio_run_work_link_events`
* `portfolio_run_work_link_idempotency`
* `portfolio_run_outcome_association_events`
* `portfolio_run_outcome_association_cursors`

Link and decision rows store canonical, validated metadata evidence. They do
not store provider payloads, issue titles, descriptions, comments, prompts,
employee profiles, or credentials. SQLite triggers reject update and delete.
PostgreSQL enables and forces tenant RLS, grants the runtime role only `SELECT`
and `INSERT`, revokes PUBLIC, and rejects update, delete, and truncate. The
accepted complete schema-21 ACL boundary is 232 entries with SHA-256
`038e670f801c9b1a0d6b96829d8cdfbb89a66eaf8114f69cb1a9909b98cd1859`.
It was measured twice from independent clean managed-role bootstraps after the
bootstrap revoked PostgreSQL's default PUBLIC function grants. The earlier
252-entry proposal measurement retained those default grants and remains only
the immutable transition-review record.

Each committed link or association event also appends a version-2 custody audit
chain entry in the same transaction. Both SQLite and PostgreSQL verify that the
audit entry exactly matches the referenced source row. Read operations append
bounded, content-free association audit metadata.

## Accounting reference

The internal metric reference uses a unique request-attempt grain and a unique
external-work-object grain at the selected authoritative snapshot. It rejects
duplicate attempt-and-basis cost evidence and keeps these cost bases separate:

* provider final;
* configured rate-card estimate;
* allocated estimate;
* provider aggregate;
* credit or discount;
* unavailable.

Provider aggregates, credits, and discounts are never assigned to an attempt
or included in cost per accepted work item. An unavailable or zero denominator
stays unavailable. The reference emits explicit denominators for eligible and
priced attempts, eligible outcome events, unique work objects, eligible
association candidates, each association state, and connector delivery health.
When exact delivery bytes recover after a durable dead letter, the accepted
receipt is the delivery-health state; the earlier failure remains append-only
audit evidence but is not counted as a second delivery. The state is selected
as of the metric evaluation cutoff and remains in the delivery's original
cohort, so a later recovery cannot rewrite an earlier historical vector.

Cost per accepted work item, cycle time, throughput, first-pass success,
retries, rework, reversions, and defects are returned only when their declared
source history is complete. A webhook receipt or snapshot page alone does not
prove completeness, so ordinary runtime event streams leave history-dependent
measures inconclusive. The frozen synthetic GitHub and Linear fixture exercises
retry, reopen, equal-revision conflict, later revert, unsupported and unmatched
events, every cost basis, deduplication, undefined denominators, and exact
decimal rounding. Retry counts use only the current corrected decision for each
source event, so a superseded mistaken association cannot create phantom work.

## Verification and nonclaims

Run the provider-free evidence from a clean source tree:

```bash
python3 tools/verify_association_runtime_plan.py
python3 -m unittest -v \
  tests.test_association_runtime_plan \
  tests.test_association_runtime \
  tests.test_association_metrics
```

With an owned disposable PostgreSQL service, also run:

```bash
HORMUZ_TEST_POSTGRES_DSN=postgresql://... \
  python3 -m unittest -v tests.test_postgres_association_runtime
```

These checks are provider-free. They do not authorize a live GitHub or Linear
workspace, prove complete provider history, establish causal impact, implement
a scorecard or recommendation route, establish savings, accept the final v1.3
candidate, or publish a release.
