# Run-to-outcome linkage preflight for #221

Status: design preflight only. This document adds no route, migration, authority,
association runtime, or release evidence. The product target is v1.3.0.

## Current evidence boundary

The frozen `hormuz.governed-run-attribution-event` identifies an immutable
`request_attempt_id` and one versioned use case. The frozen
`hormuz.external-work-binding-event` identifies a connector work object and
one versioned use case. A normalized `hormuz.work-outcome-event` identifies the
connector, work object, source revision, and source event. None of these records
asserts that a particular request attempt worked on a particular object or
revision. The planned `hormuz.run-outcome-association-event` contains both
identities, but it is a *result*, not evidence that supplies the missing link.

For example, if two governed attempts share use case U and one accepted pull
request is bound to U, organization, scope, and time-window equality leaves
two candidates. Even one candidate would establish only coincident scope and
time. Neither situation justifies an `associated` record. The evaluator must
retain the candidate denominator and emit `ambiguous` for multiple eligible
candidates or `unmatched` when no accepted linkage exists. It must never use
prompt text, code, file paths, actor identity, or a current mutable binding to
guess the missing edge.

## Proposed additive source of the edge

Before implementation, review a versioned, metadata-only run-to-work link
contract. An authenticated portfolio administrator would submit an exact
tenant-local `request_attempt_id`, `connector_id`, `external_object_id`, and
verified `source_revision` (or an explicit unknown revision), plus the current
external-work-binding event ID and the expected prior link event ID for
compare-and-set. The server would resolve the tenant and actor, reauthorize the
attempt and work object within one tenant transaction, and append an immutable
link, correction, or tombstone with a fixed reason. It would never alter the v1
attempt, attribution, outcome, usage, cost, or external observation rows.

The link contract needs an explicit eligibility rule for which principal may
attest the relationship, how that principal acquired the object/revision ID,
and what evidence quality the attestation warrants. An administrator assertion
alone cannot be presented as connector-verified authorship or causal proof.
Connector source revision must come from the verified source event, not from an
untrusted free-text request field. A missing or conflicting revision leaves
the observation unmatched or ambiguous. A link spanning tenants, a stale
binding or attribution version, a superseded source event, or an unauthorized
scope fails closed.

## Evaluation and accounting contract

Evaluate a fixed rule version and predeclared window against a consistent
snapshot of current immutable attribution, work binding, source observation,
and link events. Sort by source revision where the connector has an authorized
ordering rule, then ingestion time and opaque event ID; do not order revisions
lexically or use source event time as authority. Store each decision as an
append-only association event, including rule version, candidate count,
state, reason, and supersedes ID. Re-evaluation after a late event or
correction appends a new decision instead of rewriting previous evidence.

`associated` requires one eligible, explicit, tenant-qualified link whose
attempt, use-case version, binding event, connector, object, and verified
revision all agree. Multiple eligible links remain `ambiguous`; no link is
`unmatched`; tombstoned, unsupported, or out-of-policy observations are
`excluded`. Production connector evidence remains at most `associated`.

Aggregate at unique attempt and unique external-work-object grains. Charge an
attempt's cost at most once within its use case and preserve its original
provider-final, provider-aggregate, estimate, allocation, credit, or
unavailable basis. Count an accepted work object once even if retries,
redeliveries, reopenings, or several observations exist. Emit separate
denominators for eligible attempts, priced attempts, eligible outcome events,
unique work objects, eligible association candidates, ambiguous/excluded
records, and connector health. Never turn an undefined cost-per-accepted-item
denominator into zero.

## Required proof before #221 can close

- Freeze the additive link request/event wire and operator authority in a
  reviewed successor contract without changing frozen v1 payloads.
- Name successor SQLite/PostgreSQL migrations only after the finance and
  connector migration ownership is settled; prove tenant isolation and the
  least-privilege PostgreSQL runtime grants before activation.
- Exercise exact reference vectors for zero, one, and multiple links; stale
  versions; cross-tenant collisions; duplicate delivery; late and superseding
  source events; revisions, retries, reopenings, reversions, and cost-basis
  separation on both storage adapters.
- Prove read/export authorization before query planning, deterministic replay,
  pagination, old-binary refusal and quiesced backup/restore. Scan all routine
  outputs for work content, employee ranking, credentials, and source payloads.
- Run packaged source/wheel checks and protected-main CI against the final
  implementation. This preflight alone satisfies none of those gates.
