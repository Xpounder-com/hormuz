# PostgreSQL tenancy foundation

Hormuz has an accepted shared-schema PostgreSQL tenancy contract and an
executable schema-version-13 accounting, identity-projection, policy-projection,
policy-version, human-session, DLP/security, identity-type usage,
tenant-lifecycle, and shared-SCIM-directory backend. A deployment can opt into PostgreSQL
for usage, cost evidence, administrative read audit, atomic budget reservations,
multi-instance human sessions, one-time DLP approvals, and security evidence.
The deprecated built-in context experiment is deliberately excluded from the
PostgreSQL production-persistence plan under ADR 0008.

## Boundary proved by this checkpoint

The packaged PostgreSQL migrations create tenant, workspace, project, team,
principal, external-identity, role, capability, team-membership, desired-state
projection, usage, provider-cost, administrative-read-audit, budget-reservation,
enrollment, session, consumed-refresh, and session-security-event tables.
Schema version 4 adds a secret-free canonical policy projection plus DLP
approval-request, approval-event, and security-event tables. Schema version 5
adds immutable policy snapshots, an atomic per-tenant active-version pointer,
and append-only policy administration events. Schema version 6 binds every
usage event to the exact immutable governance policy version evaluated for its
request. Schema version 7 adds the controlled event-time identity type used to
distinguish human, service-account, CI, and connector traffic without storing
request content.
Schema version 8 permits both immutable v2 and v3 policy projections. Schema
version 9 adds a fail-closed active-tenant runtime gate, owner-only encrypted
export receipts, a delayed hard-purge state machine, and an owner-only purge
tombstone. It does not add document or prompt storage.
Schema version 10 adds tenant-isolated SCIM resources, users, groups,
memberships, workloads, lifecycle events, and dynamic principal projections.
It uses a global table of HMAC routing tags only to discover the tenant for an
OIDC `(issuer, subject)` pair before a tenant RLS context exists. The runtime
cannot select that table directly; it can invoke exact-tag security-definer
functions and a projection function constrained to an existing managed
directory resource.
Schema version 11 permits immutable policy projection v4 documents, which carry
policy-owned SCIM authorization profiles and tenant-qualified group bindings.
It does not make SCIM payload fields an authorization source.
Schema version 12 adds the independently authorized audit-reader action to the
tenant-isolated administrative-access ledger. It records the bounded audit kind,
frozen query window, and result count without request content or credentials.
When a dynamic subject is next resolved after a profile change, the directory
reconciles its effective principal projection before it is returned. That
revokes sessions with a changed authorization mapping and lets a new enrollment
use the current mapping.
Schema version 13 permits immutable policy projection v5 documents. Version 5
adds a content-free provider-cache capability catalog bound to route protocol,
upstream model, and supported gateway operation. Older immutable v2, v3, and
v4 policy versions remain readable.
Every tenant-owned table has:

- a non-null tenant key in its primary and foreign-key relationships;
- row-level security enabled and forced;
- the same fail-closed tenant policy for reads and writes;
- an immutable-tenant-key trigger; and
- runtime privileges that exclude ownership, schema creation, truncation,
  references, and trigger changes. Policy versions and events are append-only;
  the runtime can update but not delete the active-version pointer.

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
transaction-local state. It holds a shared transaction-scoped advisory lock and
requires the tenant lifecycle row to be `active`; an owner deactivation takes
the matching exclusive lock before changing state. Missing context, a missing
lifecycle row, inactive state, and state left after commit see no rows.
Repository methods must still include an explicit tenant predicate; RLS is
defense in depth.

Run `identities sync` with the owner DSN after every approved identity mapping
change. It projects configured organizations, people, teams, OIDC subjects,
capabilities, and allowed clients transactionally. It increments only affected
principal authorization versions, revokes their active sessions, and is
idempotent when desired state is unchanged.

Run `policies sync` with the owner DSN after every approved model, budget,
provider-cache capability, redaction, or DLP policy change. It writes a tenant-scoped,
canonical projection containing policy metadata and a fingerprint. The
projection excludes identity and provider credentials, resolved custom-secret
values, DLP dictionary values, the approval fingerprint key, prompts,
responses, matched values, filenames, and source content. Runtime access is
read-only, and a PostgreSQL-backed gateway fails startup with
`policy_projection_stale` when any configured tenant does not match.

ADR 0008 changed the canonical document from
`hormuz.policy-projection.v1` to `hormuz.policy-projection.v2`. Version 2
omits deprecated built-in context-injection configuration. The provider-native
cache-policy slice advances new exports to
`hormuz.policy-projection.v3`; version 3 adds only content-free
provider-cache policy metadata. PostgreSQL migration 8 permits both immutable
v2 and v3 policy versions, so a running replacement can still validate an
existing v2 active pointer. Apply migration 8 before deploying a runtime that
stages v3 documents, then run `hormuz policies sync` through the schema-owner
path. The expected startup failure before the required projection sync is
`policy_projection_stale`.

Policy projection v4 adds policy-owned `authorization_profiles` and
tenant-qualified `team_bindings` for SCIM groups. PostgreSQL migration 11
permits immutable v2, v3, and v4 versions. A v4 group binding uses the stable
SCIM group `externalId`; its IdP-controlled display name and payload cannot
authorize models, budgets, clients, clearance, or capabilities. Apply migration
11 before staging v4 documents, then activate the reviewed policy version.

Policy projection v5 adds the reviewed, content-free provider-cache capability
catalog. PostgreSQL migration 13 permits immutable v2 through v5 versions, so
a rolling replacement can still validate an older active pointer. The catalog
must match the deployment-supported route/protocol/model/operation records;
policy administration cannot invent a provider guarantee. Apply migration 13
before staging v5 documents, then run `hormuz policies sync` through the
schema-owner path. A strict `provider_cache.mode: "disabled"` policy cannot be
exported or activated through a legacy projection schema.

## Tenant lifecycle (owner-only)

The lifecycle CLI has no gateway configuration dependency and accepts only a
schema-owner DSN from a named environment variable. Its output is bounded
metadata; it never prints provider credentials, raw session material, prompts,
responses, or exported rows.

The safe sequence is deactivate, export the now-frozen tenant, schedule a
retention period, then hard-purge with a second confirmation bound to the exact
encrypted-export digest. Deactivation blocks every PostgreSQL-backed gateway
request and revokes active human sessions in the same owner transaction.

```bash
# 1. Freeze access and revoke active human sessions.
hormuz storage tenant deactivate \
  --organization acme \
  --reason-code administrative \
  --confirm-organization acme

# 2. Supply a base64url 32-byte AES-256-GCM key from the deployment secret
#    manager, then create one new mode-0600 encrypted artifact.
hormuz storage tenant export \
  --organization acme \
  --output /secure-exports/acme.hormuz \
  --encryption-key-env HORMUZ_TENANT_EXPORT_KEY

# 3. Record the retention duration and bind it to the printed export receipt.
hormuz storage tenant schedule-purge \
  --organization acme \
  --export-id tex_... \
  --retention-days 30 \
  --confirm-organization acme

# 4. After the recorded time, require a second confirmation of the artifact.
hormuz storage tenant purge \
  --organization acme \
  --export-id tex_... \
  --confirm-ciphertext-sha256 <receipt-ciphertext-sha256> \
  --confirm-organization acme
```

`hormuz storage tenant restore-plan --input ... --encryption-key-env ...`
decrypts and integrity-checks an artifact, then prints only a non-mutating,
content-free restore plan. It intentionally does not automate restoration;
restore conflict handling, customer KMS/BYOK custody and rotation, database
backup/PITR, legal hold, and HA/DR remain separate operational gates. A hard
purge retains only an owner-only tombstone containing the organization ID and
export receipt, so a future re-onboarding requires the explicit
`hormuz storage tenant re-onboard ...` step before `identities sync` can create
the organization again.

The live gateway never receives schema-owner credentials. Identity and policy
projection tables are read-only, while the previously accepted tenant-scoped
workspace/project DML and the gateway-owned accounting, session, security, and
approval DML remain available to the runtime role.

After the owner synchronizes the deployment projection, an authenticated
`policy_admin` uses the runtime gateway to stage, activate, inspect, and roll
back immutable versions. Every provider request reads the active tenant pointer
before evaluation; all gateway instances using the same PostgreSQL database
therefore observe the committed pointer without restart. See
[POLICY_ADMIN_API.md](POLICY_ADMIN_API.md).

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
PostgreSQL from the cutover onward; `audit-export` and authenticated audit-event
reads continue to combine pre-cutover SQLite evidence with current PostgreSQL
evidence. Pending or
approved SQLite grants do not authorize a PostgreSQL retry and must be requested
again after cutover. Perform a separately verified backfill before using a
mid-month cutover for enforceable monthly limits or complete-period reporting.

Schema version 6 and the authenticated policy API/CLI provide tenant-scoped
staging, compare-and-swap activation, and rollback to a previously active
version. Provider requests read and validate the active pointer at request
time, and usage events retain the exact evaluated version. Initial deployment
still requires migrate, identity sync, and policy sync before starting the
runtime. Production rollout coordination, notification, two-person approval,
emergency access, and multi-replica operational drills remain separate gates.

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
- tenant-isolated usage writes, reads, reports, bounded coverage summaries,
  usage-read audit, independently authorized audit-read evidence, and
  bounded-window tenant audit queries;
- one allowed and one denied request under a two-writer competing budget test;
- idempotent provider-cost import plus aggregate reconciliation;
- idempotent configuration-seeded identity synchronization;
- enrollment completion and authentication across two independent repository
  instances;
- exactly one rotation under a two-instance refresh race, replay denial, and
  family revocation; and
- immediate invalidation after an affected identity mapping changes.
- shared SCIM user/group/workload provisioning, generic OIDC subject resolution
  through keyed exact routing tags, denied raw global-route reads, denied
  cross-tenant subject collisions, and immediate session revocation after an
  unassignment; and
- idempotent secret-free policy synchronization and stale-projection startup
  rejection;
- idempotent immutable policy staging with deterministic SHA-256-derived
  version IDs, content-free structural change summaries, and an explicit
  `policy_admin` capability boundary;
- atomic activation and rollback across two independent repositories, with a
  monotonically increasing activation sequence and hidden cross-tenant
  versions;
- hidden cross-tenant approval requests and denied self-approval;
- exactly one consumed grant under two competing gateway retries, with the
  second retry blocked behind a new pending request;
- exact actor/provider/model/policy/payload-fingerprint/rule binding, shared
  metadata-only security evidence, and actual-model mismatch audit; and
- lifecycle-gated runtime access, active-session revocation, private
  AES-256-GCM export plus content-free restore-plan validation, retention
  enforcement, hard purge, and owner-only tombstone retention; and
- rejection of unexpected accounting or security columns during schema
  verification.

### SQLite/PostgreSQL repository conformance

The same disposable run also executes the supported shared repository
contracts once through local SQLite and once through the split PostgreSQL
adapters, then compares normalized observable results. Opaque IDs, session
credentials, and write timestamps are deliberately not compared; policy
outcomes, tenant scope, normalized metadata, and externally observable
accounting state are.

| Shared contract | Executable parity coverage |
| --- | --- |
| Usage and security ledger | Usage recording, totals, summaries, reports, coverage, scoped budget lifecycle, idempotent provider-cost import/reconciliation, administrative-read audit, secret/DLP metadata, one-time approval, and cross-tenant negative reads. |
| Human session repository | OIDC enrollment/callback, projection-bound authorization, credential rotation and replay-family revocation, logout, authorization-mapping and administrative revocation, session/event listing, failed enrollment, and tenant scope. |
| SCIM directory repository | User/group lifecycle resolved through a policy-owned profile, workload lifecycle, idempotent create, group removal/reassignment, deactivation, issuer discovery, and cross-tenant reads. |

Policy administration and tenant lifecycle are PostgreSQL-only shared-control
plane contracts; their real PostgreSQL proofs remain separate in this runner.
The deprecated built-in context repository is explicitly excluded under
[ADR 0008](decisions/0008-gateway-product-boundary.md),
so it is not represented as SQLite/PostgreSQL parity.

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

Schema v12 is a real accounting, human-session, DLP/security, policy-version,
identity-type usage, tenant-lifecycle, and shared-directory persistence slice,
not the completed hosted product.
The repository currently opens a fresh PostgreSQL connection per operation.
Configuration remains the desired-state identity source and deployment
provisioning boundary for bootstrap/admin identities. The repository also has a
shared PostgreSQL SCIM directory and administrative API, plus a separate
single-node SQLite adapter, documented in [SCIM.md](SCIM.md). Removing a person
or mapping while retaining its configured organization increments the affected
authorization version and revokes active sessions. Removing the entire
organization from configuration is not a deprovisioning operation: the sync
command intentionally will not discover or scan tenants outside the supplied
desired state. Keep the organization present while removing its people, and do
not consider tenant deletion complete until a separately verified shared
deprovision/SCIM workflow exists. A keyed routing tag lets an OIDC subject
select one RLS tenant without exposing raw issuer or subject values in a global
directory index, but production custody and rotation of that key remain open.
Policy notification/queue UX, two-person
activation approval, representative DLP evaluation, usage/security backfill,
automated restore and backup/PITR, central retention-policy administration,
customer KMS/BYOK, pooling, HA, operations, and independent security review
remain separate gates. Do not describe this checkpoint as the completed
enterprise storage plane.
