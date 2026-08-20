# PostgreSQL tenancy foundation

Hormuz has an accepted shared-schema PostgreSQL tenancy contract and an
executable schema-version-1 foundation. The current gateway execution path
still uses the three local SQLite repositories; this page does not claim that
hosted usage, session, or governed-context persistence is complete.

## Boundary proved by this checkpoint

The packaged PostgreSQL migration creates tenant, workspace, project, team,
principal, external-identity, role, capability, and team-membership tables.
Every tenant-owned table has:

- a non-null tenant key in its primary and foreign-key relationships;
- row-level security enabled and forced;
- the same fail-closed tenant policy for reads and writes;
- an immutable-tenant-key trigger; and
- runtime privileges that exclude ownership, schema creation, truncation,
  references, and trigger changes.

The migration ledger is ordered and checksum-bound. Migration and verification
require a database role that owns the Hormuz schema and is distinct from the
configured runtime role. The runtime role must not be superuser and must lack
`CREATEDB`, `CREATEROLE`, and `BYPASSRLS`.

## Install and operate

Install the optional maintained PostgreSQL driver alongside Hormuz:

```bash
python -m pip install 'hormuz[postgres]'
```

Provision two database roles through the deployment platform or a database
administrator:

- `hormuz_owner`: owns the Hormuz schema and applies migrations;
- `hormuz_runtime`: a distinct login without ownership, superuser,
  `CREATEDB`, `CREATEROLE`, or `BYPASSRLS`.

Grant `hormuz_owner` permission to create the Hormuz schema in the target
database. Inject its DSN from the deployment secret manager; do not place the
DSN, password, or provider credentials in configuration or shell arguments.

```bash
export HORMUZ_POSTGRES_DSN='injected-by-your-secret-manager'
hormuz storage migrate
hormuz storage verify
```

Both commands print bounded JSON containing only the schema, runtime-role name,
target/applied versions, and verification result. Connection failures return a
stable content-free error code and never reflect the DSN.

Custom lowercase identifiers are supported when a deployment has established
its own role and schema names:

```bash
hormuz storage migrate \
  --dsn-env COMPANY_HORMUZ_MIGRATION_DSN \
  --schema company_hormuz \
  --runtime-role company_hormuz_runtime
```

Future PostgreSQL application repositories must enter through
`tenant_transaction`, which requires a validated tenant, principal, client, and
authorization version, verifies that the connection is the exact non-owner
runtime role without `BYPASSRLS`, and binds every field with PostgreSQL
transaction-local state. Missing context and state left after commit see no
rows. Repository methods must still include an explicit tenant predicate; RLS
is defense in depth.

## Reproduce the bounded integration proof

The integration runner accepts only the committed digest-pinned PostgreSQL
image, disables image pulling during the run, creates disposable owner/runtime
roles and two synthetic tenants, then proves:

- empty-to-current migration and idempotent reapplication;
- exact migration checksum, role, privilege, policy, trigger, and forced-RLS
  verification;
- no rows with missing or transaction-cleared tenant context;
- correct tenant switching on one reused runtime connection;
- denied cross-tenant read/write and composite foreign-key access; and
- denied tenant-key mutation, including for the forced-RLS table owner.

```bash
python -m pip install '.[postgres]'
docker pull postgres@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777
python scripts/postgres_foundation_integration.py \
  --output /tmp/hormuz-postgres-foundation.json
```

The result is private, content-free aggregate evidence. The checked-in local
observation is
[`evidence/postgres-foundation-integration-2026-08-20.json`](../evidence/postgres-foundation-integration-2026-08-20.json).

## Gates that remain open

Schema v1 is a real persistence and isolation foundation, not the completed
hosted product. Issue #6 remains open until the usage, session, policy, and
governed-context repositories share tested SQLite/PostgreSQL contracts; token
and spend reservations are correct across replicas; identity revocation is
shared; tenant export/delete and backup/restore are exercised; and production
pooling, KMS, HA, operations, and independent security review pass their own
gates. `hormuz serve` therefore continues to use SQLite and must not be
advertised as PostgreSQL-backed yet.
