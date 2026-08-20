# PostgreSQL tenancy foundation

Hormuz has an accepted shared-schema PostgreSQL tenancy contract and an
executable schema-version-2 accounting backend. A deployment can opt into
PostgreSQL for usage, cost evidence, usage-read audit, and atomic budget
reservations. Sessions, DLP/security approvals, and governed context remain in
their explicitly identified SQLite stores during this transition.

## Boundary proved by this checkpoint

The packaged PostgreSQL migrations create tenant, workspace, project, team,
principal, external-identity, role, capability, team-membership, usage,
provider-cost, usage-read-audit, and budget-reservation tables.
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

The PostgreSQL accounting repository enters through
`tenant_transaction`, which requires a validated tenant, principal, client, and
authorization version, verifies that the connection is the exact non-owner
runtime role without `BYPASSRLS`, and binds every field with PostgreSQL
transaction-local state. Missing context and state left after commit see no
rows. Repository methods must still include an explicit tenant predicate; RLS
is defense in depth.

After applying migrations with the owner DSN, inject the distinct runtime-role
DSN under the environment name selected in configuration:

```json
{
  "database": "./hormuz-security.sqlite3",
  "usage_storage": {
    "backend": "postgresql",
    "postgres_dsn_env": "HORMUZ_POSTGRES_DSN",
    "postgres_schema": "hormuz",
    "postgres_runtime_role": "hormuz_runtime"
  }
}
```

`database` is deliberately still the SQLite security/approval ledger. The DSN
value never belongs in JSON. Each configured organization must be provisioned
in the PostgreSQL `tenants` table before the gateway starts writing accounting
events.

Switching the backend does not backfill old SQLite usage rows. New reports and
budget calculations use PostgreSQL from the cutover onward; `audit-export`
continues to combine pre-cutover SQLite evidence with current PostgreSQL
evidence. Perform a separately verified backfill before using a mid-month
cutover for enforceable monthly limits.

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
- denied tenant-key mutation, including for the forced-RLS table owner;
- rejected an unexpected accounting column during schema verification;
- tenant-isolated usage writes, reads, reports, and usage-read audit;
- one allowed and one denied request under a two-writer competing budget test;
  and
- idempotent provider-cost import plus aggregate reconciliation.

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

Schema v2 is a real accounting persistence slice, not the completed hosted
product. The repository currently opens a fresh PostgreSQL connection per
operation, and tenant transactions use authorization version `1` until the
identity/session migration supplies database-backed authorization versions.
Issue #6 remains open until sessions, policy approvals, and governed context
have their own tested PostgreSQL contracts; tenant provisioning and backfill,
export/delete, backup/restore, pooling, KMS, HA, operations, and independent
security review pass separate gates. Do not describe this checkpoint as the
completed enterprise storage plane.
