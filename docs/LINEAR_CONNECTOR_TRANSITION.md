# Linear successor transition checkpoint for #220 and #214

This checkpoint assigns the next available storage versions for the Linear
connector: SQLite 13 to 14 and PostgreSQL 18 to 19. The assignment starts from
merged main `72df12391fa33c7a53d45fabf4899c1346139196`, whose exact-main CI run
`35892367877` completed successfully after the real finance account-binding
13/18 migrations merged. The machine-readable record is
[linear-transition-plan-v1.json](linear-transition-plan-v1.json).

The earlier [Linear connector preflight](LINEAR_CONNECTOR_PREFLIGHT.md) and its
digest-pinned JSON remain frozen. This document resolves only the successor
version ordering that preflight intentionally left unassigned. It does not add
SQLite or PostgreSQL migrations, a receiver, a normalizer, an HTTP route, a
credential, a workspace connection, or live delivery evidence.

## Baselines and version ownership

The immutable v1.2.0 source and wheel are still the release predecessor. They
use SQLite 12 and PostgreSQL 17. Current merged main keeps package version
`1.2.0` while installing the reviewed finance account-binding migrations at
SQLite 13 and PostgreSQL 18. Linear therefore receives 14 and 19. If either
number is consumed before the connector implementation merges, stop and
publish a superseding plan; never renumber an applied ledger or silently share
a migration version.

The two SQL files in this checkpoint are review-only proposals. No production
migration loader references them. Tests inject them into a patched migration
slot only after the real 13/18 baseline is present. That lets rollback,
compatibility and restore behavior fail before runtime work starts without
making a proposed schema available to an application process.

## Proposed metadata boundary

The proposal names four append-only, tenant-keyed families:

1. immutable Linear source-binding versions;
2. committed delivery receipts keyed by the unsigned delivery hint, a
   tenant/connector/key-version HMAC of the exact signed body, and a separate
   keyed fingerprint of the stable source fact;
3. metadata-only context observations using the existing
   `hormuz.linear-context-event` version-1 contract; and
4. separate operator retention markers.

Every primary and foreign key includes `organization_id`. PostgreSQL enables
and forces tenant RLS on all four tables. The runtime role receives only
`SELECT` and `INSERT`; statement triggers reject update, delete and truncate,
PUBLIC receives no privileges, and no grant option is proposed. SQLite has
equivalent update/delete rejection triggers. Raw payloads, plain payload
hashes, names, titles, descriptions, comments, attachments, label text,
credentials and credential-value hashes have no proposed column.

This is a column, RLS and ACL review surface, not the exact runtime storage
contract. The implementation must still serialize and enforce one Linear
workspace to one Hormuz organization and one webhook to one route, extend the
finite audit-chain source union, and recheck binding and typed enrollment
inside the commit transaction. Two independent clean disposable schema-19
bootstraps measured 233 canonical non-owner ACL entries at
`0e51b64f26df25b78877e071695568a463d58cc1845d1b4751016ff3642f2adc`;
an added DELETE grant changed that fingerprint, tenant RLS hid rows across
organizations, and runtime UPDATE was denied. The eventual accepted migration
must reproduce this boundary or publish and review a replacement. The exact
storage/audit and connector preimplementation gates remain false.

## Durable acknowledgment boundary

The future service gets four seconds internally within Linear's documented
five-second response deadline. The internal clock begins with the first body
read and ends only when response bytes are ready after a committed transaction.
HTTP 200 is allowed only after one atomic receipt, context, coverage and audit
commit, or after a valid signature maps to an exact prior tenant-bound receipt.
Timeout, storage outage, uncertain commit, capacity denial or failed binding
recheck returns non-200. It never acknowledges in-memory work and never
automatically replays governed provider work.

The provider documentation does not establish whether delayed retry body
bytes, signed timestamp and delivery header remain stable. The transition
harness therefore cannot prove receipt replay, concurrency or HTTP timing.
Those cases remain required runtime tests. Unknown stale bodies fail closed
until an owner-authorized test workspace supplies content-free retry evidence.

## Executable transition cases

The source and isolated-wheel development matrix runs five Linear cases for
each backend after its existing nine cases. It begins with the exact published
v1.2.0 predecessor, advances through the real 13/18 migrations, and exposes
14/19 only through the test-only proposal loader.

1. A populated real 13/18 baseline refuses a missing 14/19 migration without
   changing rows, objects or the migration ledger.
2. A forced error after the exact proposal DDL rolls back all new objects and
   ledger state. Retrying applies the proposal once and a second retry is a
   no-op.
3. Published 12/17 and current 13/18 binaries refuse newer or partial 14/19
   state without repair or writes.
4. A quiesced backup with verified zero later writes restores the exact
   published app/config/database pair into a separate destination and replays
   the original receipt. It never replaces the candidate database.
5. A post-checkpoint witness write stays in a separately restored candidate
   snapshot for forward recovery. The older pair remains separate and cannot
   overwrite or decrement candidate history.

The runner refuses missing artifacts, digest changes, dirty source, source and
wheel runtime differences, missing selected tests, and every skip. PostgreSQL
mode additionally requires an owned disposable database and matching backup
container. No production DSN, provider credential or customer data belongs in
the matrix.

Both clean development modes completed all fourteen SQLite and fourteen
PostgreSQL cases with zero skips and the same candidate-wheel digest. This
satisfies the published-predecessor source/wheel transition gate only; it is
not final-candidate, runtime, live-provider or release evidence.

## Remaining connector and release gates

This checkpoint leaves the following work open:

- exact source binding, global route cardinality, audit-source and schema-shape
  contract acceptance;
- binding the measured PostgreSQL-19 ACL to the accepted migration and runtime
  deployment verifier;
- raw-byte signature rotation, receipt conflict/concurrency and delayed retry
  runtime tests;
- complete four-second HTTP durable-acknowledgment tests with forced outage and
  uncertain commit;
- source/wheel SQLite/PostgreSQL connector behavior and protected-main CI;
- setup, rotation, retry, reconciliation, disablement, deletion and coverage
  runbooks; and
- an owner-selected, authorized Linear test workspace with live delivery and
  redelivery evidence.

Run the provider-free checks from a clean source tree:

```bash
python3 tools/verify_linear_connector_preflight.py
python3 tools/verify_linear_transition_plan.py
python3 -m unittest -v tests.test_linear_transition_plan \
  tests.test_linear_transition_preflight
git diff --check
```

Use [V13_DEVELOPMENT_TRANSITION_MATRIX.md](V13_DEVELOPMENT_TRANSITION_MATRIX.md)
for the published predecessor source/wheel commands and the optional disposable
PostgreSQL path.
