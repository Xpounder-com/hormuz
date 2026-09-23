# Run-to-outcome association transition checkpoint for #221 and #214

This checkpoint reserves SQLite 16 and PostgreSQL 21 for the run-to-outcome
association work. It starts from merged main `794de6dcd300f76236a101eb3c904986c2046c38`, whose
exact-main CI run is `https://github.com/Xpounder-com/hormuz/actions/runs/35936276457`. That baseline has the Linear snapshot
and reconciliation storage at SQLite 15 and PostgreSQL 20. The machine-readable
record is [association-transition-plan-v1.json](association-transition-plan-v1.json).

The accepted [association linkage preflight](ASSOCIATION_LINKAGE_PREFLIGHT.md)
remains the semantic boundary. This checkpoint supplies assigned versions,
review-only storage proposals, measured PostgreSQL ACL evidence, and executable
upgrade/rollback tests. It adds no bundled migration, link endpoint,
association evaluator, scorecard, recommendation, connector authority, or
release evidence.

## Version and migration boundary

The immutable v1.2.0 source and wheel remain the published predecessor at
SQLite 12 and PostgreSQL 17. Current main advances that populated predecessor
through the real SQLite 13–15 and PostgreSQL 18–20 migrations before a test
injects the exact proposed 16/21 DDL. If another change consumes either version
before implementation, stop and publish a superseding plan. Never renumber an
applied ledger or combine unrelated schemas in the reserved slot.

The proposal creates five append-only tenant-keyed families: association audit
events, explicit run-to-work link events, request-idempotency bindings,
association decisions, and opaque pagination cursor bindings. Every primary
and foreign key is tenant qualified. Link corrections and tombstones append a
single successor; association reevaluation appends a decision under an exact
rule version, window digest and bounds, evaluation time, and snapshot sequence.

The proposal has no raw provider payload, title, body, description, comment,
prompt, credential, free-form evidence JSON, authority JSON, or filter JSON.
Cursor rows retain only authority and filter digests. The future runtime must
still validate every typed identifier against one tenant transaction, bind the
historical source context and attribution version, restrict reason fields to
their enums, and extend the finite custody audit-source union atomically.

## Deterministic association and accounting boundary

Only an explicit authorized link can be eligible. The attempt, immutable
attribution, historical work binding, connector, exact source event, external
object and verified source revision must all agree. Zero eligible links is
`unmatched`; one is `associated`; more than one is `ambiguous`; unsupported,
tombstoned, superseded or out-of-policy evidence is `excluded`. Scope equality,
time proximity, actor identity and mutable current bindings never break ties.
Production connector evidence cannot exceed `associated`.

The selected source state must follow the connector-approved revision ordering.
Equal or incomparable conflicting revisions remain ambiguous. Corrections append
new link and decision events; they never rewrite attempts, costs, source
observations or earlier decisions.

Accounting uses unique request attempts within one versioned scope and unique
external work objects at the selected evaluation snapshot. Attempt costs may be
used only at a proven attempt grain and retain provider-reported, estimated,
allocated and unavailable bases separately. Provider aggregates cannot be
assigned to attempts by coincidence. An undefined denominator stays
unavailable. The runtime must emit separate denominators for eligible and
priced attempts, eligible outcome events, unique work objects, eligible
candidates, every association state, and connector health.

## PostgreSQL least privilege

All five proposed PostgreSQL tables enable and force tenant RLS. The runtime
role receives `SELECT` and `INSERT`; PUBLIC receives nothing; no grant option is
present; statement triggers reject update, delete and truncate. Two independent
clean schema-21 bootstraps measure 252 canonical non-owner ACL entries at
`a0296c1b3bdad58acfc2bd89c4c020fa6a0af75fba7ef895a798fb3ec4fcd3dc`.
Runtime inserts are visible only to the selected tenant and runtime mutation is
denied. The accepted migration must reproduce this exact boundary or publish a
reviewed replacement.

## Executable transition cases

The source and isolated-wheel development matrix adds five SQLite and five
PostgreSQL cases after the existing v1.3 cases:

1. A populated real 15/20 baseline refuses a missing 16/21 successor without
   changing its rows, objects or migration ledger.
2. A forced failure after the exact proposal DDL rolls back completely; retry
   applies once and a second retry is a no-op.
3. Published 12/17 and current 15/20 binaries refuse newer and partial 16/21
   state without repair.
4. A quiesced published pair restores into a separate destination and replays
   its original synthetic receipts while the candidate database remains intact.
5. A post-checkpoint association witness survives a separate forward restore;
   the older pair cannot overwrite or decrement candidate history.

The PostgreSQL matrix additionally checks two clean ACL measurements, tenant
isolation and denied mutation. The runner refuses digest changes, dirty source,
source/wheel runtime differences, missing selected tests and every skip. All
fixtures are synthetic and all database work uses an owned disposable service.

## Gates left for the runtime change

The exact link request/event wire and association output contract remain to be
frozen. The implementation must prove authorization before query planning,
idempotent replay and changed-content conflicts, compare-and-set corrections,
late and conflicting revisions, retries, reopenings and reversions, pagination,
SQLite/PostgreSQL parity, coverage and cost reference vectors, audit-chain
integration, package evidence and content scans. Live GitHub and Linear
evidence requires separately authorized test organizations.

Run the provider-free checkpoint from a clean source tree:

```bash
python3 tools/verify_association_transition_plan.py
python3 -m unittest -v tests.test_association_transition_plan \
  tests.test_association_transition_preflight
git diff --check
```

Use [V13_DEVELOPMENT_TRANSITION_MATRIX.md](V13_DEVELOPMENT_TRANSITION_MATRIX.md)
for the published-predecessor source/wheel commands and the optional disposable
PostgreSQL path.
