# Run-to-outcome linkage preflight for #221

Status: design preflight only. This document adds no route, migration, authority,
association runtime, or release evidence. The product target is v1.3.0.

## Current evidence boundary

The frozen `hormuz.governed-run-attribution-event` identifies an immutable
`request_attempt_id` and one versioned use case. The frozen
`hormuz.external-work-binding-event` authorizes a connector container
(GitHub repository or Linear project) for one versioned use case. The internal
source observation has separate container and work-object (issue or pull
request) IDs; its immutable context records the container and historical
binding. The closed public `hormuz.work-outcome-event` carries the work-object,
source revision, and source-event IDs, but no container ID. None of these records
asserts that a particular request attempt worked on a particular object or
revision. The planned `hormuz.run-outcome-association-event` contains both
identities, but it is a *result*, not evidence that supplies the missing link.

For example, if two governed attempts share use case U and a repository with an
accepted pull request is bound to U, organization, scope, and time-window equality suggests
two possible runs, but neither is an eligible association candidate. Even a
sole run would establish only coincident scope and time. Without an accepted
explicit link, the eligible candidate count is zero and the decision is
`unmatched`. Two eligible, conflicting explicit links produce `ambiguous`.
The evaluator must retain scope-only observations in coverage without
promoting them to eligible links. It must never use prompt text, code, file
paths, actor identity, or a current mutable binding to guess the missing edge.

## Proposed additive source of the edge

Before implementation, review a versioned, metadata-only run-to-work link
contract. An authenticated portfolio administrator would submit an exact
tenant-local `request_attempt_id`, `connector_id`, `source_event_id`, and an
idempotency identity. The server would bind that identity to the canonical
request digest and
resolve the referenced immutable observation, including its distinct
`container_id`, `external_object_id`, verified `source_revision` (or explicit
unknown revision), and historical external-work-binding event ID captured in
the immutable outcome context. It
would compare that historical binding with the observation's recorded context,
not require the currently active binding to have the same event ID. The request
also carries the expected prior link event ID for compare-and-set. An exact
retry of a committed identity returns the original receipt before current-state
CAS; reuse with different content fails closed, including across replicas.
The server would resolve the tenant and actor, reauthorize the attempt and
work object within one tenant transaction, and append an immutable link,
correction, or tombstone with a fixed reason. It would never alter the v1
attempt, attribution, outcome, usage, cost, or external observation rows.

The link contract needs an explicit eligibility rule for which principal may
attest the relationship, how that principal acquired the object/revision ID,
and what evidence quality the attestation warrants. An administrator assertion
alone cannot be presented as connector-verified authorship or causal proof.
Connector source revision must come from the verified source event, not from an
untrusted free-text request field. A missing or conflicting revision leaves
the observation unmatched or ambiguous. A link spanning tenants, a mismatched
historical binding or attribution version, a superseded source event, or
an unauthorized scope fails closed. A later registry replacement cannot retarget
or invalidate an otherwise eligible historical observation.

## Evaluation and accounting contract

Evaluate a fixed rule version and predeclared window against a consistent
snapshot of current immutable attribution, historical work binding, source
observation, and link events. Select authoritative current object state only by
the connector's approved revision/order rule. Equal conflicting revisions and
incomparable revisions remain ambiguous; ingestion time and opaque event ID may
stabilize presentation order but never break an authority tie. Do not order
revisions lexically or use source event time as authority. Store each decision
as an
append-only association event, including rule version, candidate count,
state, reason, and supersedes ID. Re-evaluation after a late event or
correction appends a new decision instead of rewriting previous evidence.

`associated` requires one eligible, explicit, tenant-qualified link whose
attempt, use-case version, historical binding event, connector, exact
`source_event_id`, object, and verified revision all agree with the selected
authoritative current observation. Multiple eligible links remain `ambiguous`;
no link is
`unmatched`; tombstoned, unsupported, or out-of-policy observations are
`excluded`. Production connector evidence remains at most `associated`.

Aggregate at unique attempt and unique external-work-object grains. Charge an
attempt's cost at most once within its use case only when the evidence is
genuinely attempt-grained: provider-final at proven attempt scope, the original
configured estimate, or a separately approved allocated estimate. Keep provider
aggregates, credits, discounts, and unavailable cost as separate evidence and
coverage; never assign an aggregate to an attempt by coincidence. Count an
accepted work object once only when its selected authoritative state at the
evaluation snapshot remains accepted. Prior accepted observations stay in
coverage, but reopenings or reversions remove that object from the current
accepted denominator. Emit separate denominators for eligible attempts, priced
attempts, eligible outcome events,
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
  versions; cross-tenant collisions; exact replay after a lost response and
  changed-content idempotency conflicts; late and superseding source events;
  equal conflicting revisions, retries, reopenings, reversions, and cost-basis
  separation on both storage adapters.
- Prove read/export authorization before query planning, deterministic replay,
  pagination, old-binary refusal and quiesced backup/restore. Scan all routine
  outputs for work content, employee ranking, credentials, and source payloads.
- Run packaged source/wheel checks and protected-main CI against the final
  implementation. This preflight alone satisfies none of those gates.
