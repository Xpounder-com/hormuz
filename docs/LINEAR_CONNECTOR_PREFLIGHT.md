# Linear connector offline preflight for #220 and #214

This is a pre-implementation proposal from protected main
`7c8e5296329255bca35ef5e7d2cda9735885e7df`. It does not accept the #214
checkpoint, install a receiver, normalize a Linear event, reserve a schema
version, connect a workspace, or qualify v1.3.0. The machine-readable contract
is [linear-connector-preflight-v1.json](linear-connector-preflight-v1.json).
The accepted [ADR 0011](decisions/0011-additive-budget-reports-and-linear-context.md)
keeps typed Linear context separate from frozen issue/PR outcome records.

## Current boundary and rollout order

At the pinned base commit, main used SQLite 12 and PostgreSQL 17. The finance
account-binding preflight then proposed 13 and 18, without installing those
migrations. These are historical baseline facts, not assertions that future
main must keep those versions or that the finance plan's file bytes cannot
advance. Linear cannot claim the same numbers or skip over finance. Its
successor versions remain unassigned. After integration order is decided,
publish a reviewed successor plan with exact predecessor artifact hashes,
numbered migrations and executable transition tests. Do not treat this
preflight as permission to implement storage first.

The existing `portfolio-control` Linear connector binds a workspace and a set
of project IDs. It does not authorize every team, initiative, cycle, issue or
webhook. The prospective source binding is separate and versioned: one server
route identifies exactly one enabled Hormuz organization, connector, Linear
workspace and webhook, exact team IDs, and four **typed** object-ID sets.
Across routes, a workspace cannot be assigned to two Hormuz organizations or
a webhook ID to two routes. Empty enrollment for a kind denies that kind;
an issue-to-project or project-to-initiative relationship enrolls neither
endpoint. The final implementation must recheck current binding and both
relation endpoints inside the commit transaction, including moves and
revocation races. No body field selects the tenant or work scope.

## Source authentication and mapping

[Linear's webhook documentation](https://linear.app/developers/webhooks)
describes organization/team-scoped webhooks, an HMAC-SHA256 signature over the
exact raw body, delivery/event/timestamp headers, generic `create`, `update`
and `remove` actions for data-change events, a five-second response deadline,
and retries. It documents Issue, Project, Initiative and Cycle model streams.
The same reference does not prove every field, parent-set completeness,
archive/delete interpretation or a comparable source revision for each model.
The [#313 synthetic scenarios](../tests/fixtures/connectors/linear/README.md)
keep those mappings pending. This plan does not promote them to production
semantics.

The offline oracle in `tools/verify_linear_connector_preflight.py` first
selects server registration, bounds the exact bytes, verifies HMAC against
one active or one unexpired retiring secret, then parses bounded strict JSON.
It compares signed workspace and webhook claims against the registration,
compares the header event and timestamp against signed body fields, and checks
the typed object ID. A signed team claim may disprove scope when it mismatches;
an absent claim is `unproven` and does not establish team authority. This is a
test oracle, not an installed adapter. It does not return a lifecycle state,
relationship projection, source revision or work-outcome event. A generic
authenticated `remove` envelope cannot be labeled archive, delete or
tombstone without provider-specific evidence.

`Linear-Delivery` is a header outside the signed body. Changing only that
header must not create a second fact. The runtime needs a tenant-keyed,
domain-separated HMAC fingerprint of the exact signed body **and** a stable
source-fact identity, in addition to a delivery-ID uniqueness constraint.
An exact replay returns its original durable receipt; identical bytes with a
different unsigned delivery header also deduplicate. Same delivery identity
with different bytes is a conflict. Concurrent deliveries serialize at the
same tenant-bound receipt/observation transaction. The key version is
operator metadata; neither secret bytes, plain payload hash, raw JSON nor
free-text error can enter rows or logs. Explicit signing-secret overlap and
expiry are required for rotation; version labels cannot be chosen by payload.

The future normalizer must use an exact reviewed per-entity/action/state
allowlist. Initiative, project, cycle and issue remain in scope, but an
unsupported, malformed or mapping-pending event makes **no normalized
mutation**. Any accepted context record must use the separate version-1
context contract, retain only approved opaque metadata, set raw reader scope
to `portfolio_admin`, descriptive evidence and inconclusive association
eligibility. Only an issue in an independently enrolled project can enter the
unchanged issue outcome contract. Missing or partial relationships cannot
remove old links. UUIDs, delivery order and observed time cannot become a
source revision. Backfill needs a separate bounded authenticated snapshot
path and provenance; it cannot masquerade as a webhook.

## Durable acknowledgment and failure contract

Linear's five-second deadline includes HTTP ingress and response, not just a
database statement. Reserve an internal four-second end-to-end budget from
first body read through final durable commit and response construction. A
`200` is allowed only after an atomic receipt, normalized metadata, coverage
and audit commit, or after a verified exact prior receipt. A timeout, storage
outage, uncertain commit, capacity denial or failed source recheck returns
non-`200`; never acknowledge in-memory work. Redelivery may look up the
original receipt but must not replay governed provider work. Enforce existing
1 MiB body, JSON depth 16, 4,096 members, 100 events/delivery and eight
process slots, plus bounded HTTP headers and reads. The oracle checks only
in-memory authentication ordering, not the actual five-second service path.

## Required transition and recovery tests before a connector PR can merge

The JSON plan names seven deterministic cases. Each must run for SQLite and
PostgreSQL against an exact published v1.2.0 predecessor and the selected
numbered successor; source and isolated wheel must both exercise them.

1. A populated predecessor refuses an absent successor migration without
   changing its ledger, schema objects, usage, attempts, uncertain holds,
   audit, registry, attribution, outcome, finance or budget rows.
2. A test-only DDL failure inside the real migrator rolls back DDL and ledger;
   a retry succeeds once and leaves every predecessor row unchanged.
3. The published v1.2.0 and current binaries refuse partial/newer schema
   states without repair or writes.
4. With verified **zero** post-checkpoint writes, stop all writers/pools and
   restore an exact old app/config/database pair into a separate destination;
   original receipts replay byte-for-byte.
5. With nonzero or unknown writes, preserve the candidate snapshot and
   forward-recover; never overwrite it with an old backup, decrement a ledger,
   discard accepted context or release an uncertain reservation.
6. Exact delivery replay, changed unsigned delivery header, conflicting bytes
   and concurrent commit races return the original receipt or fail closed as
   appropriate, with no duplicate outcome/context fact.
7. Forced commit outage and deadline expiry return non-`200` without a false
   durable claim. Measure request read, HMAC, parse, binding recheck,
   transaction and HTTP response under the complete four-second budget.

These tests are **required, unexecuted implementation tests**. The present
oracle suite cannot satisfy database, PostgreSQL RLS/ACL, HTTP or live proof.
The eventual migration must be additive; preserve old request/response,
auth, error, pagination, ordering, idempotency, retry, policy, evidence and
CLI behavior. No in-place downgrade is allowed. Restoration requires an exact
verified old pair and zero writes; otherwise retain the candidate and recover
forward. Startup uses fresh processes after serialized migration and verifies
schema shape, tenant isolation, permissions and receipt/audit continuity.

## Operator and release gates

Before enabling a real integration, document setup, least-privilege team
selection, exact typed enrollment, rotation/overlap, failed delivery/retry,
bounded reconciliation, disablement, source deletion versus operator
retention, and coverage gaps. The owner must select and authorize a Linear
test workspace and webhook permissions. One live delivery and redelivery with
content-free evidence, source/wheel and SQLite/PostgreSQL proof, independent
review, protected-main CI and the complete #214 candidate transition remain
separate gates. No external credentials, content or workspace data are used
by this preflight.

Run the offline checks from this source tree:

```bash
python3 tools/verify_linear_connector_preflight.py
python3 -m unittest -v tests.test_linear_connector_preflight \
  tests.test_linear_connector_fixtures tests.test_portfolio_extensions \
  tests.test_outcome_contract
git diff --check
```
