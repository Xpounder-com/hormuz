# ADR 0002: Enterprise tenancy, authorization, and persistence

- Status: **Accepted**
- Date proposed: 2026-08-15
- Date accepted: 2026-08-20
- Decision owner: Product owner
- Tracking issue: [#1](https://github.com/Xpounder-com/hormuz/issues/1)
- Unblocks after acceptance: [#6](https://github.com/Xpounder-com/hormuz/issues/6), [#7](https://github.com/Xpounder-com/hormuz/issues/7), [#4](https://github.com/Xpounder-com/hormuz/issues/4)

## Decision requested

Choose the authorization and persistence topology before Hormuz creates a stable enterprise schema:

1. **Hybrid PostgreSQL tenancy — recommended.** A shared schema with mandatory tenant keys and database-enforced row security for the hosted standard offering, plus a dedicated database/deployment option for customers requiring stronger isolation or residency control.
2. **Database per tenant only.** Strong operational isolation at substantially higher provisioning, migration, and fleet cost.
3. **Application-filtered shared database.** Simpler initially, but a missed predicate can become a cross-tenant disclosure.
4. **SQLite as the production store.** Preserve today's local topology and defer shared enterprise semantics.

The product owner accepted option 1 on 2026-08-20. This authorizes the stable
tenancy and persistence contract; implementation and release evidence remain
separate gates.

## Context

The private alpha stores a metadata-only usage ledger in SQLite and loads policy identities from configuration. That is useful for local verification but cannot provide hosted multi-tenant isolation, durable identity/session state, SCIM, governed context, tenant-scoped backup/restore, or concurrent migrations.

PostgreSQL row security can restrict selected and mutated rows and defaults to denying access when row security is enabled without an applicable policy. Table owners and roles with `BYPASSRLS` normally bypass it, so Hormuz must run as a non-owner without that attribute and apply `FORCE ROW LEVEL SECURITY`. See the [current PostgreSQL row-security documentation](https://www.postgresql.org/docs/current/ddl-rowsecurity.html).

Row security is defense in depth, not permission to omit tenant scope from application APIs, keys, indexes, foreign keys, caches, jobs, or object storage.

### Feasibility evidence, not acceptance

On August 20, 2026, the opt-in
[`scripts/postgres_rls_feasibility.py`](../../scripts/postgres_rls_feasibility.py)
verifier exercised two synthetic tenants against a locally cached, immutable
PostgreSQL image digest with container networking disabled and image pulling
forbidden. PostgreSQL `16.14` observed all of the following:

- the runtime role was neither superuser nor `BYPASSRLS`;
- row security and `FORCE ROW LEVEL SECURITY` were enabled;
- a missing tenant context, the forced table owner without context, and a reused
  session after transaction commit each saw zero rows;
- each tenant saw only its own synthetic record;
- a cross-tenant write was denied by RLS; and
- a composite tenant foreign key denied a cross-tenant reference.

The content-free observation is recorded in
[`evidence/postgres-rls-feasibility-2026-08-20.json`](../../evidence/postgres-rls-feasibility-2026-08-20.json).
It contains no SQL output, row identifiers, credentials, or customer content.
This proves that the core RLS invariants proposed below are feasible in one
disposable local exercise. It does **not** accept this ADR or prove a production
schema, repository implementation, migrations, connection-pool behavior,
concurrency, backup/PITR, restore, deletion, residency, KMS, HA, or independent
security review.

## Accepted decision

### Security and ownership hierarchy

- A **tenant** is the billing, policy, encryption, residency, audit, and isolation root. It has an immutable internal ID and a mutable display name.
- A **workspace** belongs to exactly one tenant and groups related teams, applications, connectors, and projects.
- A **project** belongs to exactly one workspace and may map to one repository or another governed work boundary. Repository identity and revision are explicit fields, not inferred from a display name.
- A **team** belongs to one tenant and may be granted roles in one or more workspaces/projects.
- A **human actor** has a tenant-scoped identity. External identities map by `(tenant, issuer, subject)` and memberships are effective-dated.
- A **workload/service account** is a separate principal type with its own credentials, owner, allowed clients, scopes, and expiry. It never impersonates a human for attribution.
- A **session** binds one principal, tenant, client application, credential family, and authorization snapshot/version.

Global Hormuz operators are not tenant actors. Support access requires a separate, time-bounded, approved, audited break-glass path and cannot be represented by silently assigning an operator to a customer tenant.

### Authorization model

Permissions are explicit capabilities, not assumptions encoded in route names. Initial capabilities include:

- view own usage;
- view team usage;
- view tenant billing aggregates;
- administer model, budget, DLP, and cache policies;
- read governed context;
- propose context;
- verify/promote/supersede context;
- administer connectors and provider credentials;
- view/export audit evidence;
- manage identities, memberships, sessions, and service accounts.

Named roles are tenant-configurable bundles of capabilities. The built-in bundles are starting defaults, not authorization code branches. A manager does not automatically receive prompt, response, file, or code content; metadata usage visibility and content access are distinct permissions.

Every authorization decision receives a verified `TenantContext` containing tenant, principal, client, memberships, capabilities, clearance, and authorization version. Repository methods require that context explicitly. Background jobs carry a signed/serialized tenant job envelope and re-authorize before reading current data or writing results.

Historical usage and audit events snapshot the effective tenant, workspace, project, team, actor, principal type, client, and policy version at event time. A later transfer does not rewrite prior allocation.

### Hosted PostgreSQL topology

The standard hosted offering uses one logical schema with these invariants:

- every tenant-owned row has a non-null immutable `tenant_id`;
- primary/unique constraints that identify tenant data include `tenant_id`;
- tenant-owned foreign keys are composite and cannot point across tenants;
- row security is enabled and forced on every tenant table;
- the runtime role is not the table owner, is not a superuser, and lacks `BYPASSRLS`;
- each transaction sets a verified tenant context locally before any tenant query;
- missing or malformed tenant context denies access;
- connection-pool checkout/check-in clears session state, and tests exercise tenant switching on reused connections;
- migrations and narrowly scoped operations roles are separate from the runtime role.

Application-level tenant predicates remain mandatory so intent is visible, query plans use tenant-leading indexes, and non-PostgreSQL stores follow the same contract. RLS is the second enforcement layer.

The enterprise isolation option uses a dedicated PostgreSQL database, and when required a dedicated deployment/network boundary, while preserving the same logical schema and repository contract. Dedicated tenants do not receive a divergent product fork.

### Other durable stores

Object storage keys begin with an opaque tenant ID and are authorized before a signed URL is created. Search/vector indexes use a tenant partition or dedicated index and apply authorization before candidate retrieval. Queues include tenant and authorization version in the authenticated job envelope. Cache keys include tenant and access-scope digests. Provider credentials and context encryption keys are tenant-scoped through KMS; their custody is finalized under the later KMS/BYOK gate.

SQLite remains supported only for local, single-tenant development and deterministic tests. It implements the same repository interfaces and schema semantics where practical, but it is not production evidence for tenant isolation, concurrency, backup, or migrations.

### Migrations and compatibility

- Schema changes use versioned, reviewed migrations and an expand/migrate/contract rollout for zero-downtime compatibility.
- Destructive contraction occurs only after old application versions are drained and data migration is verified.
- Rollback normally means rolling the application back to a schema-compatible version; destructive down-migrations are not the primary recovery mechanism.
- Startup refuses a schema newer than the binary supports and refuses unsafe partially applied versions.
- Shared and dedicated databases run the same migration suite, with canary and fleet status evidence.

The migration framework/library is deliberately not selected in this ADR. That choice is reversible if the migration contract and evidence remain the same.

### Backup, restore, deletion, and residency

- Hosted PostgreSQL uses encrypted continuous backup and point-in-time recovery with regularly executed restore drills.
- A dedicated database can be restored as a complete tenant boundary.
- A shared-database single-tenant restore is performed into an isolated recovery environment, validated, and replayed through tenant-scoped import tooling; production-wide rollback is not used for one tenant.
- Tenant export and deletion traverse every durable store from a registry of tenant-owned data classes and produce auditable completion evidence.
- A tenant is pinned to an approved region. Replication, backups, analytics, and support workflows cannot move tenant content outside that boundary without explicit policy.
- Legal hold applies to canonical records and audit evidence. Derived caches are rebuilt from canonical state rather than treated as the legal system of record.

Recovery point and recovery time objectives are not chosen here; they are commercial/operational decisions for the deployment milestone and must be measured before v1.0.

## Security invariants

- Tenant resolution happens once from an authenticated principal and cannot be overridden by a request body, header, context record, or provider response.
- Authorization precedes data retrieval, cache lookup, embedding/search, connector calls, and expensive provider work.
- Cross-tenant identifiers return the same non-disclosing result as nonexistent identifiers.
- Data access tests use at least two tenants and prove read, write, aggregate, search, cache, queue, export, backup/restore, and migration isolation.
- Database owners, migration roles, support roles, and runtime roles are distinct and auditable.
- Referential-integrity and unique-constraint behavior is reviewed for covert cross-tenant existence disclosure because PostgreSQL integrity checks can bypass RLS.
- No prompt, response, source content, provider credential, or session credential appears in usage metadata or routine audit events.

## Alternatives considered

### Database per tenant only

This provides a strong default blast-radius boundary and simpler tenant restore. It creates a database fleet for every customer, complicates migrations and aggregate operations, and is disproportionate for the $79 tier. Preserve it as the enterprise/dedicated option.

### Schema per tenant

This still shares a database process while multiplying schema migrations, search paths, and connection-pool hazards. It offers less isolation than a dedicated database without the operational simplicity of a shared schema. Rejected in the proposal.

### Application filters without RLS

This reduces database complexity but makes one missing predicate a security incident. Rejected for hosted multi-tenancy.

### SQLite in production

This is excellent for local development but does not satisfy the hosted concurrency, migration, HA, restore, or row-security contract. Rejected for enterprise production.

## Consequences

- PostgreSQL becomes the production source of truth for control-plane, identity, usage, and governed-context metadata/content, behind repository interfaces.
- Every new persistence feature must prove tenant scope across SQL, jobs, caches, indexes, object storage, exports, and audit.
- The standard hosted tier can be operated economically, while regulated customers can buy a dedicated boundary without a separate codebase.
- Tenant-level restore in a shared database requires purpose-built export/replay tooling and drills.
- The schema and authorization capability model become high-cost-to-change contracts; implementation proceeds through separately verified milestones under the accepted decision.

## Verification required

Acceptance of this ADR does not prove the implementation. Issue #6 closes only with:

- migrations from an empty database and every supported prior release;
- repository contract tests against PostgreSQL and local SQLite;
- cross-tenant negative tests for every query/mutation class and reused pooled connections;
- database-role tests proving runtime cannot bypass RLS or own protected tables;
- concurrency tests for budgets, membership/revocation, and context lifecycle;
- backup/PITR and isolated single-tenant restore drills;
- shared-to-dedicated tenant portability evidence;
- logs and exports scanned for credentials and governed content.

## Owner approval record

The product owner approved **A — hybrid shared-schema PostgreSQL plus a
dedicated database/deployment option** on 2026-08-20. The canonical approval is
recorded in [issue #1](https://github.com/Xpounder-com/hormuz/issues/1#issuecomment-5355712147).

Acceptance authorizes the contract in this ADR; it does not by itself prove
implementation. Schema v2 now adds an opt-in usage, cost, usage-read-audit, and
atomic budget-reservation repository to the separately evidenced role/RLS
foundation. Sessions, approvals, governed context, pooling, backup/PITR, HA,
KMS, and independent review remain open gates.
