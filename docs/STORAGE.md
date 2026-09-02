# Usage-evidence storage

Hormuz stores only the metadata needed to enforce policy and account for governed requests. It does not store prompts, provider response bodies, matched secret values, provider credentials, or context/memory records. Before provider egress, it also records a content-free immutable request-attempt root and its reservation so uncertain provider activity cannot be silently dropped.

This document describes the storage compatibility gate for the core gateway. It proves the SQLite and PostgreSQL usage/evidence repositories have the same narrow contract and documents disposable logical backup-and-restore and point-in-time-recovery drills. It does **not** establish production PostgreSQL operations, production backup/PITR, HA/DR, KMS/BYOK, immutable audit retention, or multi-instance coordination. Those remain separate release gates in [ROADMAP.md](ROADMAP.md).

## Supported modes

| Mode | Intended use | Default |
| --- | --- | --- |
| SQLite | Local single-tenant development and deterministic tests | Yes |
| PostgreSQL | Explicitly configured usage/evidence repository; optional shared immutable policy control with transaction-local organization isolation | No |

SQLite remains the simplest CLI-first setup. PostgreSQL is optional so the core wheel and normal local installation do not need a database driver or server.

## PostgreSQL configuration

Install the optional driver only on the Hormuz host:

~~~bash
python3 -m pip install '.[postgres]'
~~~

Keep connection strings out of the JSON configuration. Configure only environment-variable names:

~~~json
{
  "usage_storage": {
    "backend": "postgresql",
    "postgres_dsn_env": "HORMUZ_POSTGRES_DSN",
    "postgres_migration_dsn_env": "HORMUZ_POSTGRES_MIGRATION_DSN",
    "postgres_schema": "hormuz",
    "postgres_runtime_role": "hormuz_runtime",
    "postgres_pool": {
      "min_connections": 1,
      "max_connections": 8,
      "acquire_timeout_seconds": 5,
      "max_waiting": 16,
      "max_lifetime_seconds": 3600,
      "max_idle_seconds": 300
    }
  },
  "policy_control": {
    "mode": "postgresql",
    "postgres_control_dsn_env": "HORMUZ_POLICY_CONTROL_DSN",
    "postgres_control_role": "hormuz_policy_control",
    "bootstrap_administrators": [
      {"organization_id": "xpounder", "actor_id": "alice"}
    ]
  },
  "custody_control": {
    "mode": "postgresql",
    "postgres_control_dsn_env": "HORMUZ_CUSTODY_CONTROL_DSN",
    "postgres_control_role": "hormuz_custody_control",
    "bootstrap_administrators": [
      {"organization_id": "xpounder", "actor_id": "alice"}
    ]
  },
  "custody_retention": {
    "retention_days": 365,
    "legal_hold": false
  }
}
~~~

HORMUZ_POSTGRES_MIGRATION_DSN is an operator credential that can create or update the Hormuz schema. HORMUZ_POSTGRES_DSN is the long-running gateway credential. HORMUZ_POLICY_CONTROL_DSN and HORMUZ_CUSTODY_CONTROL_DSN are separate service credentials used for their respective administration surfaces. They must name four different credentials in an operational deployment. Put all four in the deployment secret manager, not the configuration file, command history, or client environment. Managed custody also requires a `key_custody` profile; see [CUSTODY_CONTROL.md](CUSTODY_CONTROL.md).

### Runtime-pool boundary

`hormuz serve` opens exactly one bounded Psycopg pool for the runtime DSN in a
process. The usage/evidence repository and managed-policy **read** path share
that pool; migration and policy-control credentials do not. Startup waits for
the configured `min_connections`, `max_connections` is the hard connection
ceiling, and `max_waiting` is a hard queue ceiling. A checkout waits at most
`acquire_timeout_seconds`; saturation, a closed pool, a missing pool driver,
or a broken database connection fail closed as content-free storage failures.
That short foreground deadline is deliberately separate from the pool's fixed
15-second background reconnect horizon. If that bounded cycle cannot connect,
Hormuz re-arms a later cycle while remaining unready, so a returning database
does not require a gateway-process restart.

The reconnect callback is maintenance-only: it never retries, reclassifies, or
turns a failed governed request into success. A bounded queue rejection remains
ordinary fail-closed saturation and is not a connection-recovery signal.

The defaults above are conservative rather than workload-sizing advice. Every
value is strictly bounded; `max_waiting: 0` is rejected because an unbounded
queue can turn database pressure into unbounded request latency. Connections
above the minimum may be retired after `max_idle_seconds` and all connections
are rotated no later than `max_lifetime_seconds`.

Pool reuse never carries a tenant setting from one request into another. Each
repository operation opens a new transaction and applies the restricted role,
configured search path, and organization ID with `SET LOCAL`; PostgreSQL
resets those values when that transaction commits or rolls back. On graceful
shutdown, the listener stops accepting new requests, waits for its active
handler threads to finish, and only then closes its owned pool. This is
single-process pool safety, not evidence of database failover or production
HA.

### Runtime credential rotation

Hormuz does not watch, reload, or mutate a PostgreSQL DSN in a running
process. Rotate the customer-controlled runtime credential with a replacement
deployment:

1. Keep `postgres_runtime_role` as the stable, restricted database role that
   receives the Hormuz grants. A rotation-friendly deployment uses a separate
   `NOINHERIT` login role as the runtime DSN principal and grants it membership
   in that stable role.
2. Create a new login and inject its DSN only into a replacement gateway
   process. Validate its `/ready` endpoint before sending it traffic.
3. Move traffic through the customer-controlled load balancer or ingress, then
   drain the old gateway. Its pool closes only after accepted work has finished.
4. Revoke or disable the old login only after the old process has stopped. If
   the replacement cannot become ready, keep the old login enabled and roll
   back the replacement deployment instead.

The restricted role, schema, and transaction-local tenant boundary remain the
same across both logins. The replacement therefore uses the ordinary policy,
budget, evidence, and RLS paths; it receives no new authority from rotation.
Hormuz's PostgreSQL conformance suite proves this sequence with two disposable
`NOINHERIT` logins: the replacement becomes ready, the old pool drains, the
old login subsequently fails closed, and the replacement continues to preserve
tenant isolation. The proof is not a secret-manager integration, automatic
live reload, coordinated load-balancer rollout, database failover, or customer
deployment certification.

`GET /ready` performs its PostgreSQL evidence check through that same bounded
runtime pool, then verifies the active managed policy when managed policy
control is enabled. It performs no provider request and returns only a
content-free readiness result; see [OPERATIONS.md](OPERATIONS.md).

Before migration, an operator creates the restricted runtime,
policy-control, custody-control, and custody-executor roles. All must be distinct non-owner roles
with no superuser, database-creation, role-creation, inheritance, or BYPASSRLS
capability. The migration grants each only its owned surface and the shared
migration ledger. Existing deployments must create the configured custody roles
before applying PostgreSQL schema v7; migrations grant privileges to
pre-existing principals rather than creating customer database roles.

~~~sql
CREATE ROLE hormuz_runtime
  LOGIN
  NOSUPERUSER
  NOCREATEDB
  NOCREATEROLE
  NOINHERIT
  NOBYPASSRLS
  PASSWORD 'managed-out-of-band-secret';

CREATE ROLE hormuz_policy_control
  LOGIN
  NOSUPERUSER
  NOCREATEDB
  NOCREATEROLE
  NOINHERIT
  NOBYPASSRLS
  PASSWORD 'managed-out-of-band-secret';

CREATE ROLE hormuz_custody_control
  LOGIN
  NOSUPERUSER
  NOCREATEDB
  NOCREATEROLE
  NOINHERIT
  NOBYPASSRLS
  PASSWORD 'managed-out-of-band-secret';

CREATE ROLE hormuz_custody_executor
  NOLOGIN
  NOSUPERUSER
  NOCREATEDB
  NOCREATEROLE
  NOINHERIT
  NOBYPASSRLS;
~~~

For rolling runtime-credential rotation, retain `hormuz_runtime` as the stable
restricted role and create a separate login member for each runtime credential:

~~~sql
CREATE ROLE hormuz_runtime_login_20260823
  LOGIN
  NOSUPERUSER
  NOCREATEDB
  NOCREATEROLE
  NOINHERIT
  NOBYPASSRLS
  PASSWORD 'managed-out-of-band-secret';

GRANT hormuz_runtime TO hormuz_runtime_login_20260823;
~~~

Use the login role only in `HORMUZ_POSTGRES_DSN`; keep
`postgres_runtime_role` set to `hormuz_runtime`. The runtime transaction sets
that role locally before it can access Hormuz data. Existing deployments that
use a direct runtime login remain supported, but the separate-login pattern is
the safer credential-rotation boundary.

The password above is illustrative. Use the deployment's managed-secret mechanism instead of pasting a real value into a shell or checked-in file.

## Migration and verification

An operator applies migrations explicitly; a gateway process only verifies the configured schema. This prevents a normal runtime identity from changing durable schema state.

~~~bash
export HORMUZ_POSTGRES_MIGRATION_DSN='stored-by-your-secret-manager'
export HORMUZ_POSTGRES_DSN='stored-by-your-secret-manager'
export HORMUZ_POLICY_CONTROL_DSN='stored-by-your-secret-manager'
export HORMUZ_CUSTODY_CONTROL_DSN='stored-by-your-secret-manager'

hormuz --config hormuz.json storage migrate
hormuz --config hormuz.json storage verify
hormuz --config hormuz.json doctor
~~~

The current PostgreSQL migrations create the usage-events, secret-events, active budget-reservations, immutable request-attempt roots and events, policy-tenant, policy-administrator, immutable policy-version, active-policy-pointer, and policy-control-event tables; organization/time indexes; forced row-level security policies; and a versioned migration ledger. Every runtime repository operation:

1. sets the restricted runtime role locally;
2. sets the configured schema search path locally;
3. binds one authenticated organization_id to the transaction;
4. applies an explicit organization predicate as well as PostgreSQL row-level security.

An absent organization context therefore sees no tenant rows. A configured organization ID that the repository does not recognize fails closed before querying.

When `policy_control.mode` is `postgresql`, a gateway or `hormuz doctor` also fails closed until every configured tenant has an active immutable policy version. Run the one-time bootstrap, stage a policy document, and activate it through the governed CLI service; do not insert or modify policy rows manually. See [POLICY_CONTROL.md](POLICY_CONTROL.md).

When `custody_control.mode` is `postgresql`, `hormuz doctor` verifies the
dedicated migration and custody-table shape through the custody-control role.
Normal gateway startup does not initialize or use this control credential.
Bootstrap and lifecycle authorization run only through the governed custody
service; see [CUSTODY_CONTROL.md](CUSTODY_CONTROL.md). The separately deployed
executor verifies its own restricted credential and schema surface at startup.
Normal gateway startup never receives or initializes that credential. When
`custody_lifecycle` is configured, the runtime reads only registered asset
fingerprints, the derived projection, and prepared content-free barriers. It
registers an opaque replica lease, acknowledges a barrier only after local
installation, and keeps the request path database-free and fail-closed.

SQLite upgrades happen on normal store initialization. Its ledger records the same supported migration state and refuses a partial or newer-than-binary schema.

### Schema v3 request-attempt upgrade

For a supported v2-to-v3 upgrade, retain the existing usage, secret-evidence,
and budget-reservation records unchanged. Version 3 adds a nullable
`attempt_id` to reservations and creates the content-free request-attempt root
and event tables. Existing reservations remain legacy expiry-based holds;
only reservations linked to a new attempt use the durable
`pending`/`outcome_unknown` retention rule.

During an SQLite upgrade with an existing v2 ledger, version-3 objects are
created only after that ledger has recorded v3 as applying. On PostgreSQL, use
the explicit migration command above with the operator credential. If either
runtime sees a partial v3 state,
an applied ledger missing its required durable columns, or a noncontiguous
migration ledger, the gateway fails closed before provider egress. Do not
manually add the v3 tables,
alter the migration ledger, or attempt a down-migration. Restore the tested
v2 application/database pair if rollback is required.

### Schema v4 commit-time audit-chain upgrade

Version 4 adds four tenant-qualified evidence objects:

- `gateway_audit_chain_epochs`: an initial adoption row or an explicit
  restore/migration link to a trusted predecessor checkpoint;
- `gateway_audit_chain_heads`: one current chain version, epoch, sequence, and
  digest per organization;
- `gateway_audit_chain_entries`: canonical current audit events and their
  ordered digest links;
- `gateway_audit_chain_checkpoints`: local receipts for successful external
  Object Lock writes.

The usage/secret event insert, chain-entry insert, and chain-head advance are
one transaction. A failure rolls back all three. PostgreSQL takes a row lock on
the tenant head so multiple gateway instances serialize the next sequence;
SQLite obtains its write transaction before it creates the event. Runtime
database access is intentionally append-only for historical entries: the
runtime role has `SELECT, INSERT` on chain entries and no direct `UPDATE` or
`DELETE` privilege. SQLite installs no-update/no-delete triggers for epochs,
entries, and checkpoint receipts; PostgreSQL uses restricted grants. Those
guards constrain the normal gateway runtime, not a database owner or a host
root administrator able to alter the schema or database file. The
migration/operator role remains distinct from the runtime role.

Existing audit evidence remains available after the migration but is not
pretended to have been commit-chain protected before v4. A partial v4 ledger,
missing table, noncontiguous migration ledger, or newer binary/database mismatch
fails closed. Do not create the tables by hand or edit the migration ledger.

For a restore or migration that resumes from a protected checkpoint, start a
new explicit epoch with `hormuz audit chain epoch`; then verify using the same
canonical checkpoint artifact. A recovered older backup that lacks the
checkpointed event cannot pass verification without that explicit external
bridge. This is deliberate evidence of the recovery boundary, not a missing
fallback behavior.

### PostgreSQL schema v5 custody-control upgrade

Version 5 is PostgreSQL-only. It adds forced-RLS custody tenants and
administrators, immutable content-free operation targets, append-only approval
rows, and immutable control events. SQLite remains at schema v4 because local
custody commands do not implement shared root authority.

Create the configured restricted custody-control role before migration. The
operator migration validates the complete v5 shape. Runtime verification still
checks only runtime evidence it is permitted to see; custody-service
verification separately checks only custody objects. This family-specific
verification prevents either service from receiving cross-boundary table read
permission merely to inspect schema metadata.

There is no down-migration. An older binary encountering PostgreSQL schema v5
fails closed. Rollback requires restoring the previously tested application and
database pair. Schema v5 alone also blocks legacy direct KMS lifecycle commands;
it does not create a custody executor or customer KMS authority.

### PostgreSQL schema v6 routine-executor upgrade

Version 6 is PostgreSQL-only. It adds forced-RLS
`custody_execution_attempts` and append-only `custody_execution_events` tables.
The executor claims an already authorized routine intent by atomically writing
one immutable attempt root and its `pending` event before the external effect.
The root's operation, type, target hash, parameter hash, and protected-input
reference hash must match the active authorization even at the database insert
boundary and cannot be rewritten; exactly one terminal event records
`succeeded`, `failed`, or `outcome_unknown`.

Create the configured restricted custody-executor role before migration. It
gets tenant-scoped read access to metadata-only custody authorization facts and
`SELECT`/`INSERT` only on its own attempt/event tables. It has no write access
to administrators, intents, approvals, usage evidence, policy state, or
customer KMS/IAM configuration. The custody-control role may read the execution
metadata for authorized status output but cannot rewrite it.

There is no down-migration. An older binary encountering PostgreSQL schema v6
fails closed. Rollback requires the previously tested application/database
pair. The migration does not create a human execution command, destructive
lifecycle capability, break-glass recovery, or customer KMS authority.

### PostgreSQL schema v7 governed lifecycle upgrade

Version 7 is PostgreSQL-only. It extends the executor root to v2 for governed
destructive operations and adds these forced-RLS, tenant-qualified objects:

- `custody_lifecycle_asset_identities`: immutable asset ID/generation and
  binding fingerprint registry. Envelope rows additionally retain only their
  linked key asset ID/generation/fingerprint, never a local path, key
  reference, or credential;
- `custody_lifecycle_events` and `custody_lifecycle_chain_heads`: append-only
  metadata-only destructive lifecycle history and a per-organization chain
  head;
- `custody_runtime_projection_heads` and
  `custody_runtime_projection_restrictions`: derived, monotonic gateway
  selection restrictions;
- `custody_runtime_replicas`, `custody_runtime_projection_barriers`, and
  `custody_runtime_projection_acks`: five-second replica authority, prepared
  affected-asset admission barriers, and acknowledgements installed before
  activation;
- `custody_envelope_attestations`: successful rewrap and restore-verification
  proof used to gate a write-key retirement.

The executor can register a configuration catalog and append immutable events,
but PostgreSQL triggers alone update the chain head and projection in the same
transaction. The runtime role can only read fingerprint identities and the
projection; it has no lifecycle insert, update, or delete privilege. The
custody-control role can read the metadata-only history for status/audit
purposes but cannot mutate it. A small security-definer function returns only
whether exactly two destructive approvers remain active; the executor does not
receive a broad read grant over approval history.

Before starting a gateway with lifecycle enforcement, run the machine-only
catalog registration under the executor deployment credential:

~~~bash
hormuz --config /etc/hormuz/hormuz.json custody executor register assets
~~~

Startup verifies every configured identity, synchronizes the active projection
and prepared barriers, and acknowledges only after local enforcement is in
place. PostgreSQL notification plus a durable background scan coordinates
replicas; normal egress uses local immutable state without a PostgreSQL read per
request. Activation is rejected while any replica has an unexpired lease and
lacks an acknowledgement. A replica that loses coordination becomes unready
immediately and its five-second local monotonic lease expires no later than the
database lease used to exclude it. Thus a committed restriction is already
blocked on acknowledged replicas, while disconnected replicas have been
fenced from new admission. Version 7 serializes this path to one prepared
restriction per organization; another restriction waits until activation or an
explicit governed recovery resolution.

There is no down-migration. An older binary encountering PostgreSQL schema v7
fails closed. Rollback requires restoring the previously tested
application/database pair. Version 7 does not alter customer KMS/IAM policy,
revoke a provider credential outside Hormuz, delete ciphertext or keys, create
break-glass recovery, or externally anchor the lifecycle chain.

### PostgreSQL schema v8 custody-evidence retention

Version 8 keeps existing v1 gateway-chain entries untouched and adds strict v2
chain entries only for a finite, metadata-only custody-source union. Managed
custody requires `custody_retention` before tenant bootstrap. PostgreSQL records
each custody source timestamp with its own clock and persists the derived
`retain_until` plus legal-hold state; later configuration edits cannot shorten
an existing record.

Each new custody control, execution attempt/event, lifecycle event, envelope
attestation, or deletion-denial record commits with its exact v2 chain entry and
updated tenant chain head in one transaction. A missing source/entry pair rolls
back. The runtime role cannot write or delete custody source records, shorten
custody retention, or bypass the v2 source checks. A tenant-scoped custody-evidence export is available through the
authenticated custody-control service; there is no delete endpoint. A
`custody evidence deletion check` records `deletion_blocked` with the retention, legal-hold, or
strong-approval reason without authorizing destructive deletion.

Version 8 remains PostgreSQL-only because custody authority and retention
enforcement require forced RLS and restricted roles. An older binary fails
closed on the newer ledger; rollback requires the previously tested
application/database pair. See [CUSTODY_CONTROL.md](CUSTODY_CONTROL.md) and
[CONTRACTS.md](CONTRACTS.md) for the exact contract and compatibility rules.

## Upgrade, rollback, and recovery

Use a stopped or drained gateway and a tested backup/snapshot as the starting point for every durable-store change:

1. Record the current Hormuz package version and configuration revision.
2. Create a recoverable SQLite copy or PostgreSQL backup/snapshot using the database platform's supported procedure.
3. Run the candidate package against an isolated copy first: `storage migrate`, `storage verify`, a tenant-scoped `policy check`, and metadata-only audit validation.
4. Apply the PostgreSQL migration with the migration credential, then verify with the runtime credential.
5. Start a bounded canary only after verification succeeds.

Hormuz has no automatic destructive down-migration. If an older binary encounters a schema newer than it supports, it fails closed with storage_schema_newer_than_binary; a partial ledger state fails closed with storage_schema_partial_upgrade. Do not edit the migration ledger to bypass either condition.

Rollback means returning to a schema-compatible application version, or restoring the previously tested application/database pair into an isolated recovery environment before an operator-controlled promotion. It does not mean running ad hoc SQL against a live shared database. The compatibility tests verify that an unsafe rollback attempt leaves durable evidence unchanged.

### Disposable PostgreSQL logical recovery drill

The repository contains one reproducible, **disposable** logical recovery
check:

~~~bash
python3 -m pip install '.[postgres]'
./tools/verify_postgres_backup_restore.sh
~~~

It starts isolated source, recovery, and quarantine PostgreSQL 16.14
containers from the digest-pinned test image. It creates four restricted
non-owner roles (runtime, policy-control, custody-control, and custody-executor),
applies Hormuz's normal PostgreSQL migrations to the source, and seeds only
fixed two-tenant metadata: one usage record, one secret-egress record, one
active budget reservation, one `outcome_unknown` request attempt with its
retained reservation, and one managed-policy lifecycle per tenant. No
provider call, customer database, customer role, prompt, response, secret
value, or provider credential is used.

The source is backed up with `pg_dump` custom format. `pg_restore` first
attempts a deliberately truncated copy in the separate quarantine database;
that restore and a subsequent Hormuz verification must fail. The valid archive
is then restored into a clean recovery database. The verifier uses the
restricted runtime and policy-control roles to require the migration ledger,
tenant-scoped repository behavior, active policy versions, active budget
reservations (including uncertain attempt holds), request-attempt event state,
and RLS denial without an organization context. It computes a
SHA-256 state fingerprint over the restored metadata in memory and requires it
to exactly match the source before writing evidence.

Only `summary.json` is retained. It is schema-versioned as
`hormuz.postgresql-recovery-drill-summary` v1 and contains the pinned database
image/version, custom-dump checksum and byte count, content-free state
fingerprints/counts, passed checks, and measured durations. It contains no
connection string, role, database name, policy document, event row, or dump.
The raw dump and intermediate state are removed with the disposable containers.

This is a recovery **exercise**, not an automatic restore or promotion path.
It does not prove WAL/PITR, a production RPO/RTO, cloud backups, encryption of
customer backups, live customer restore, HA/failover, retention operations,
tenant export/delete, or DR certification. Production operators must retain
and rehearse their database platform's supported backup and recovery procedure
separately.

### Disposable PostgreSQL point-in-time recovery drill

The repository also contains an explicitly acknowledged, disposable physical
WAL/PITR check:

~~~bash
python3 -m pip install '.[postgres]'
HORMUZ_POSTGRES_PITR_ACKNOWLEDGEMENT=I_UNDERSTAND_DISPOSABLE_POSTGRESQL_PITR \
  ./tools/verify_postgres_pitr_recovery.sh
~~~

It creates a Docker-labelled PostgreSQL 16.14 source container from a
digest-pinned image, seeds the same fixed two-tenant Hormuz metadata fixture,
and takes a physical `pg_basebackup`. Only after that base backup succeeds it
commits a fixed pre-target marker, creates a named PostgreSQL restore point,
commits a fixed post-target marker, and waits for the exact switched WAL files
to enter the local archive. A recovered physical copy must contain the
pre-target marker, omit the post-target marker, and be promoted from the named
restore point. Connection readiness alone is insufficient: the positive path
polls `pg_is_in_recovery()` for a bounded 45 attempts and fails with the
content-free `recovery_target_promotion_timeout` code unless PostgreSQL reports
promotion. Only then does it assert marker state and run the ordinary Hormuz
restricted runtime/control verification against the original metadata-only
state, including its migration ledger, active policy,
request-attempt/reservation state, and RLS isolation.

Two independent negative recoveries must fail without promotion: one requests
an unreachable named restore point with a complete archive; the other requests
the real target with an empty WAL archive. These negative cases never use the
positive promotion wait and must still exit nonzero. The runner can target only
containers that it created with its fixed Docker label and exact pinned image.
It removes source, recovery, negative-recovery containers, the network, base
backup, WAL archive, and fixture state after completion.

Only an owner-readable `hormuz.postgresql-pitr-recovery` v1 `summary.json` is
retained. It contains the pinned database identity, fixed boolean checks, and
durations. It excludes container names, ports, database names, roles,
connection strings, marker values, fixture policy, event rows, archive files,
and credentials. A failing run writes no summary.

This proves a **disposable local WAL/PITR mechanism**, not production backup
retention, customer recovery, production RPO/RTO, backup encryption,
managed-database operations, HA/failover, or disaster-recovery certification.

### Disposable PostgreSQL interruption-and-recovery drill

The repository also contains a separate, explicitly opt-in proof of one
gateway process across one abrupt database interruption:

~~~bash
python3 -m pip install '.[postgres]'
./tools/verify_postgres_interruption_recovery.sh
~~~

The wrapper creates one local PostgreSQL 16.14 container from the same
digest-pinned image, labels it as disposable, fixes one selected loopback host
port to that named container for its lifetime, and supplies ephemeral
operator/runtime/policy-control credentials only through the subprocess
environment. The Python verifier independently checks both the generated
container name and the Docker label before it can stop the container. It
starts a managed-policy gateway and a fixed local provider fixture, confirms
readiness and one governed request, then abruptly stops that container.

During the interruption, `/ready` must withdraw readiness with the existing
content-free `dependency_unavailable` contract and a new governed request must
return the stable `hormuz_storage_unavailable` error before provider egress.
After the same disposable container restarts, the same live gateway process
and open `PostgresConnectionPool`—not a replacement process—must regain
readiness and serve a later, new governed request. The drill verifies that the
provider saw exactly the two successful requests, that the durable usage
evidence remains available, and that an empty second tenant remains isolated.

Only an owner-readable `hormuz.postgresql-interruption-recovery` v1
`summary.json` is retained. It contains the pinned image/version, fixed check
results, and measured durations; it excludes container names, ports, database
names, roles, connection strings, credentials, policies, requests, responses,
and provider payloads. Failure writes no summary.

This is a **single disposable database interruption/restart proof**. It does
not establish production database failover, HA, automatic promotion,
multi-instance coordination, production RPO/RTO, customer topology, automatic
provider replay, or an incident-response program.

### SQLite schema v11 / PostgreSQL schema v15 attempt finance evidence

The native-attempt finance migration adds one append-only,
tenant-qualified `gateway_finance_attempt_evidence` row for every new terminal
provider attempt and five immutable configured-rate binding columns on the
attempt root. PostgreSQL forces RLS and grants its runtime role only select and
insert. SQLite enforces the same tenant/link/cardinality invariants with
foreign keys, unique keys, and mutation guards.

The sidecar is a reviewed commit-audit-chain version-2 source. SQLite 11
transactionally rebuilds the historical entry table to add source identity
columns while copying every old entry unchanged. PostgreSQL 15 extends the
existing finite source constraint and security-definer guard by exactly
`hormuz.finance-attempt-evidence` version 1; arbitrary source JSON remains
rejected. Pre-migration attempts keep explicit legacy coverage and are not
backfilled or repriced.

## Failure behavior

Before provider egress, Hormuz atomically persists a content-free `pending` request attempt and its conservative budget reservation. If that transaction cannot commit, the gateway returns a content-free 503 with the stable classification `hormuz_storage_unavailable`; the provider is not called. The same classification appears in the metadata-only gateway error envelope for control-plane reads.

After a provider-compatible response body has begun, Hormuz never injects a different JSON shape into that response or buffers the full body before relaying it. A reliable result atomically appends a terminal attempt event and the linked usage audit event. If that finalization cannot commit, the pre-existing `pending` row remains. A later startup or pre-egress sweep marks expired pending rows `outcome_unknown`; an ambiguous network failure or interrupted successful stream is marked `outcome_unknown` immediately when storage is available. The associated estimated cost remains an uncertain reservation regardless of its original expiry until a later governed reconciliation or administrator-resolution capability is introduced. Hormuz never automatically replays a provider request.

## Verification

The core suite runs SQLite migration, rollback, tenant-scope, historical-evidence, and fail-closed-path checks:

~~~bash
python3 -m unittest -v tests.test_contracts tests.test_cli tests.test_gateway tests.test_store
~~~

The PostgreSQL compatibility suite uses an operator-supplied disposable database and creates an isolated schema and restricted role for the test run:

~~~bash
HORMUZ_TEST_POSTGRES_DSN='postgresql://operator@host:5432/hormuz_test' \
  python3 -m unittest -v tests.test_postgres
~~~

CI runs this PostgreSQL suite separately against a pinned disposable PostgreSQL service. It proves the adapter's migration idempotency, runtime/control-role separation, RLS behavior, tenant isolation, append-only request-attempt evidence, conservative unknown-outcome reservations, pooled checkout reuse with tenant-state reset, bounded saturation, broken-connection replacement, rolling replacement with a separately authenticated runtime login, policy bootstrap/activation/rollback, contract fixtures, malformed-evidence failure, rollback/partial-schema failure, and competing budget reservation behavior.

A separate PostgreSQL recovery job runs the disposable logical drill (against
source, recovery, and quarantine containers), the labelled
interruption-and-recovery drill, and the explicitly acknowledged physical PITR
drill against the same digest-pinned image. A successful run uploads only their
content-free summaries for seven days. A failed run may have no summary, but
it never uploads a dump, WAL archive, intermediate state, request, response,
credential, or database data. These gates are not substitutes for
managed-database PITR, HA/failover, credential rotation, load testing, cloud
backup, customer secret-manager integration, or DR certification.
