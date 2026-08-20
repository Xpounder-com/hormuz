# PostgreSQL tenancy foundation

Hormuz has an accepted shared-schema PostgreSQL tenancy contract and an
executable schema-version-4 accounting, identity-projection, policy-projection,
human-session, and DLP/security backend. A deployment can opt into PostgreSQL
for usage, cost evidence, usage-read audit, atomic budget reservations,
multi-instance human sessions, one-time DLP approvals, and security evidence.
The deprecated built-in context experiment is deliberately excluded from the
PostgreSQL production-persistence plan under ADR 0008.

## Boundary proved by this checkpoint

The packaged PostgreSQL migrations create tenant, workspace, project, team,
principal, external-identity, role, capability, team-membership, desired-state
projection, usage, provider-cost, usage-read-audit, budget-reservation,
enrollment, session, consumed-refresh, and session-security-event tables.
Schema version 4 adds a secret-free canonical policy projection plus DLP
approval-request, approval-event, and security-event tables.
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
hormuz --config /etc/hormuz/hormuz.json identities sync
hormuz --config /etc/hormuz/hormuz.json policies sync
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

Run `identities sync` with the owner DSN after every approved identity mapping
change. It projects configured organizations, people, teams, OIDC subjects,
capabilities, and allowed clients transactionally. It increments only affected
principal authorization versions, revokes their active sessions, and is
idempotent when desired state is unchanged.

Run `policies sync` with the owner DSN after every approved model, budget,
redaction, or DLP policy change. It writes a tenant-scoped,
canonical projection containing policy metadata and a fingerprint. The
projection excludes identity and provider credentials, resolved custom-secret
values, DLP dictionary values, the approval fingerprint key, prompts,
responses, matched values, filenames, and source content. Runtime access is
read-only, and a PostgreSQL-backed gateway fails startup with
`policy_projection_stale` when any configured tenant does not match.

ADR 0008 changes the canonical document from
`hormuz.policy-projection.v1` to `hormuz.policy-projection.v2`. Version 2 omits
deprecated built-in context-injection configuration. Deployments upgrading
from version 1 must run `hormuz policies sync` through the schema-owner path
before starting the replacement runtime; the expected startup failure before
that sync is `policy_projection_stale`. The database schema does not change.

The live gateway never receives schema-owner credentials. Identity and policy
projection tables are read-only, while the previously accepted tenant-scoped
workspace/project DML and the gateway-owned accounting, session, security, and
approval DML remain available to the runtime role.

After migration plus identity and policy synchronization, inject the distinct
runtime-role DSN under the environment name selected in configuration:

```json
{
  "database": "./hormuz-security.sqlite3",
  "usage_storage": {
    "backend": "postgresql",
    "postgres_dsn_env": "HORMUZ_POSTGRES_DSN",
    "postgres_schema": "hormuz",
    "postgres_runtime_role": "hormuz_runtime"
  },
  "authentication": {
    "session_broker": {
      "enabled": true,
      "backend": "postgresql",
      "public_base_url": "https://hormuz.example.com",
      "master_key_env": "HORMUZ_SESSION_MASTER_KEY"
    }
  }
}
```

`database` is retained as the pre-cutover SQLite usage/security audit source;
new security events and DLP approvals use PostgreSQL when
`usage_storage.backend` is `postgresql`. The DSN value never belongs in JSON.
Both desired-state commands can provision configured organizations; manual
tenant-row creation is not the normal path. When the session backend is omitted
it follows `usage_storage.backend`, so this example's explicit value documents
rather than changes the default.

Switching the backend does not backfill old SQLite usage, security, or approval
rows. New reports, budget calculations, security totals, and approvals use
PostgreSQL from the cutover onward; `audit-export` continues to combine
pre-cutover SQLite evidence with current PostgreSQL evidence. Pending or
approved SQLite grants do not authorize a PostgreSQL retry and must be requested
again after cutover. Perform a separately verified backfill before using a
mid-month cutover for enforceable monthly limits or complete-period reporting.

The current CLI rollout contract is coordinated replacement, not zero-downtime
policy rollout: migrate, sync identities, sync the candidate policy, start the
candidate configuration, and readiness-check it. Rollback requires syncing the
retained prior policy configuration before starting the prior binary/config.
Multi-version policy activation and an administrator change-approval API remain
open rather than being implied by this checkpoint.

## Reproduce the bounded integration proof

The integration runner accepts only the committed digest-pinned PostgreSQL
image, disables image pulling during the run, creates disposable owner/runtime
roles and two synthetic tenants, then proves:

- empty-to-current migration and idempotent reapplication;
- exact migration checksum, role, privilege, policy, trigger, and forced-RLS
  verification;
- no rows with missing or transaction-cleared tenant context;
- correct tenant switching on one reused runtime connection;
- denied cross-tenant read/write and composite foreign-key access;
- denied tenant-key mutation, including for the forced-RLS table owner;
- rejected an unexpected accounting column during schema verification;
- tenant-isolated usage writes, reads, reports, and usage-read audit;
- one allowed and one denied request under a two-writer competing budget test;
- idempotent provider-cost import plus aggregate reconciliation;
- idempotent configuration-seeded identity synchronization;
- enrollment completion and authentication across two independent repository
  instances;
- exactly one rotation under a two-instance refresh race, replay denial, and
  family revocation; and
- immediate invalidation after an affected identity mapping changes.
- idempotent secret-free policy synchronization and stale-projection startup
  rejection;
- hidden cross-tenant approval requests and denied self-approval;
- exactly one consumed grant under two competing gateway retries, with the
  second retry blocked behind a new pending request;
- exact actor/provider/model/policy/payload-fingerprint/rule binding, shared
  metadata-only security evidence, and actual-model mismatch audit; and
- rejection of unexpected accounting or security columns during schema
  verification.

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

Schema v4 is a real accounting, human-session, and DLP/security persistence
slice, not the completed hosted product. The repository currently opens a fresh
PostgreSQL connection per operation. Configuration is the desired-state
identity and policy source; SCIM and administrative identity/policy APIs are not
implemented. Removing a person
or mapping while retaining its configured organization increments the affected
authorization version and revokes active sessions. Removing the entire
organization from configuration is not a deprovisioning operation: the sync
command intentionally will not discover or scan tenants outside the supplied
desired state. Keep the organization present while removing its people, and do
not consider tenant deletion complete until a separately verified explicit
deprovision/SCIM workflow exists. A keyed routing
tag lets an opaque credential select one RLS tenant without exposing the raw
organization ID or querying a global credential index, but production custody
and rotation of that key remain open. Issue #6 remains open until governed
context has a tested PostgreSQL contract; policy notification/queue UX,
representative DLP evaluation, usage/security backfill, export/delete,
backup/restore, pooling, KMS, HA, operations, and independent security review
remain separate gates. Do not describe this checkpoint as the completed
enterprise storage plane.
