# Usage-evidence storage

Hormuz stores only the metadata needed to enforce policy and account for governed requests. It does not store prompts, provider response bodies, matched secret values, provider credentials, or context/memory records. Before provider egress, it also records a content-free immutable request-attempt root and its reservation so uncertain provider activity cannot be silently dropped.

This document describes the storage compatibility gate for the core gateway. It proves the SQLite and PostgreSQL usage/evidence repositories have the same narrow contract and documents one disposable logical backup-and-restore drill. It does **not** establish production PostgreSQL operations, production backup/PITR, HA/DR, KMS/BYOK, immutable audit retention, or multi-instance coordination. Those remain separate release gates in [ROADMAP.md](ROADMAP.md).

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
  }
}
~~~

HORMUZ_POSTGRES_MIGRATION_DSN is an operator credential that can create or update the Hormuz schema. HORMUZ_POSTGRES_DSN is the long-running gateway credential. HORMUZ_POLICY_CONTROL_DSN is the service credential used for administration. They must name different credentials in an operational deployment. Put all three in the deployment secret manager, not the configuration file, command history, or client environment.

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

Before the first migration, an operator creates the restricted runtime and policy-control roles. Both must be non-owner roles with no superuser, database-creation, role-creation, inheritance, or BYPASSRLS capability, and they must be different roles. The migration grants the runtime role only the usage/evidence surface plus read-only active-policy access. It grants the policy-control role only the policy-administration tables and migration ledger; it cannot update tenant initialization state, immutable policy versions, or control events. The runtime role has only `SELECT, INSERT` on request-attempt roots and events: an advisory lock serializes their append-only transitions without granting rewrite permission. An existing schema-v1 deployment must create its configured policy-control role before applying the schema-v2 migration, because migrations grant permissions to that pre-existing restricted role rather than creating database principals.

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

## Upgrade, rollback, and recovery

Use a stopped or drained gateway and a tested backup/snapshot as the starting point for every durable-store change:

1. Record the current Hormuz package version and configuration revision.
2. Create a recoverable SQLite copy or PostgreSQL backup/snapshot using the database platform's supported procedure.
3. Run the candidate package against an isolated copy first: storage migrate, storage verify, a tenant-scoped policy-check, and metadata-only audit validation.
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
containers from the digest-pinned test image. It creates two non-owner roles,
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

A separate PostgreSQL recovery job runs both the disposable logical drill
(against source, recovery, and quarantine containers) and the labelled
interruption-and-recovery drill against the same digest-pinned image. A
successful run uploads only their content-free summaries for seven days. A
failed run may have no summary, but it never uploads a dump, intermediate
state, request, response, credential, or database data. Neither gate is a
substitute for managed-database PITR, HA/failover, credential rotation, load
testing, cloud backup, customer secret-manager integration, or DR
certification.
