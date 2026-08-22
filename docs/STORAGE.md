# Usage-evidence storage

Hormuz stores only the metadata needed to enforce policy and account for governed requests. It does not store prompts, provider response bodies, matched secret values, provider credentials, or context/memory records.

This document describes the storage compatibility gate for the core gateway. It proves the SQLite and PostgreSQL usage/evidence repositories have the same narrow contract. It does **not** by itself establish production PostgreSQL operations, pooling, backup/PITR, HA/DR, KMS/BYOK, immutable audit retention, or multi-instance coordination. Those remain separate release gates in [ROADMAP.md](ROADMAP.md).

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
    "postgres_runtime_role": "hormuz_runtime"
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

Before the first migration, an operator creates the restricted runtime and policy-control roles. Both must be non-owner roles with no superuser, database-creation, role-creation, inheritance, or BYPASSRLS capability, and they must be different roles. The migration grants the runtime role only the usage/evidence surface plus read-only active-policy access. It grants the policy-control role only the policy-administration tables and migration ledger; it cannot update tenant initialization state, immutable policy versions, or control events. An existing schema-v1 deployment must create its configured policy-control role before applying the schema-v2 migration, because migrations grant permissions to that pre-existing restricted role rather than creating database principals.

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

The current PostgreSQL migration creates the usage-events, secret-events, active budget-reservations, policy-tenant, policy-administrator, immutable policy-version, active-policy-pointer, and policy-control-event tables; organization/time indexes; forced row-level security policies; and a versioned migration ledger. Every runtime repository operation:

1. sets the restricted runtime role locally;
2. sets the configured schema search path locally;
3. binds one authenticated organization_id to the transaction;
4. applies an explicit organization predicate as well as PostgreSQL row-level security.

An absent organization context therefore sees no tenant rows. A configured organization ID that the repository does not recognize fails closed before querying.

When `policy_control.mode` is `postgresql`, a gateway or `hormuz doctor` also fails closed until every configured tenant has an active immutable policy version. Run the one-time bootstrap, stage a policy document, and activate it through the governed CLI service; do not insert or modify policy rows manually. See [POLICY_CONTROL.md](POLICY_CONTROL.md).

SQLite upgrades happen on normal store initialization. Its ledger records the same supported migration state and refuses a partial or newer-than-binary schema.

## Upgrade, rollback, and recovery

Use a stopped or drained gateway and a tested backup/snapshot as the starting point for every durable-store change:

1. Record the current Hormuz package version and configuration revision.
2. Create a recoverable SQLite copy or PostgreSQL backup/snapshot using the database platform's supported procedure.
3. Run the candidate package against an isolated copy first: storage migrate, storage verify, a tenant-scoped policy-check, and metadata-only audit validation.
4. Apply the PostgreSQL migration with the migration credential, then verify with the runtime credential.
5. Start a bounded canary only after verification succeeds.

Hormuz has no automatic destructive down-migration. If an older binary encounters a schema newer than it supports, it fails closed with storage_schema_newer_than_binary; a partial ledger state fails closed with storage_schema_partial_upgrade. Do not edit the migration ledger to bypass either condition.

Rollback means returning to a schema-compatible application version, or restoring the previously tested application/database pair into an isolated recovery environment before an operator-controlled promotion. It does not mean running ad hoc SQL against a live shared database. The compatibility tests verify that an unsafe rollback attempt leaves durable evidence unchanged.

## Failure behavior

Before provider egress, a storage interruption results in a content-free 503 with the stable classification hormuz_storage_unavailable; the provider is not called. The same classification appears in the metadata-only gateway error envelope for control-plane reads.

After a provider-compatible response body has begun, Hormuz never injects a different JSON shape into that response. It logs only a stable storage failure classification and closes the connection. A durable post-relay audit gap therefore remains an operational incident to investigate; it is not silently recast as successful accounting.

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

CI runs this PostgreSQL suite separately against a pinned disposable PostgreSQL service. It proves the adapter's migration idempotency, runtime/control-role separation, RLS behavior, tenant isolation, policy bootstrap/activation/rollback, contract fixtures, malformed-evidence failure, rollback/partial-schema failure, and competing budget reservation behavior. It is not a substitute for a managed-database restore/PITR or HA drill.
