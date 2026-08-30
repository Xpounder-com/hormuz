# v1.1.0 portfolio registry

The #215 source implementation supplies tenant-scoped work scopes and external
work bindings. It does not release v1.1.0, attribute runs, enforce portfolio
budgets, ingest provider webhooks, or produce scorecards. Those roadmap gates
remain separate. Read [REGISTRY_TRANSITION.md](REGISTRY_TRANSITION.md) before
upgrading any existing database.

## Enable explicit operator authority

Existing configurations need no edit to retain their v1 gateway behavior.
Registry access is denied unless an operator adds the following optional
`portfolio_control` object to the existing gateway configuration. It has its
own versioned shape; unknown fields are rejected. IDs below are synthetic.

```json
{
  "portfolio_control": {
    "schema_id": "hormuz.portfolio-control",
    "schema_version": 1,
    "role_bindings": [
      {
        "organization_id": "acme",
        "actor_id": "alice",
        "roles": ["portfolio_admin"]
      }
    ],
    "connectors": [
      {
        "organization_id": "acme",
        "connector_id": "github-one",
        "provider": "github",
        "installation_id": "123",
        "workspace_id": null,
        "external_object_ids": ["456"]
      }
    ]
  }
}
```

Each role binding must reference an existing server-configured identity in the
same organization. Static or verified OIDC credentials retain their existing
identity mapping. Token claims, body fields, filters, and cursor values cannot
add an organization or role. Configuration changes require a fresh process;
configuration files and their distribution are operator-controlled authority.

Only `portfolio_admin` can use these raw registry endpoints. `finance_viewer`,
`platform_viewer`, and `team_lead` can be declared but acquire no raw registry
access here; their aggregate/team capabilities are gated by #223. Self usage,
provider access, inference eligibility, policy-root and custody authority are
unchanged. No registry role implicitly grants any of them.

Connector configuration is an explicit operator authorization, not a live
provider verification claim. Operators register GitHub installation/repository
numeric IDs or Linear workspace/project UUIDs obtained through their authorized
provider administration. Names, URLs and external content are not identifiers.
GitHub requires `installation_id` and a null `workspace_id`; Linear requires
`workspace_id` and a null `installation_id`. `external_object_ids` are the exact
repository/project allowlist; a binding request cannot extend it. Later signed
connectors must independently verify delivery and source context. This release
slice performs no external provider call. Keep each tenant's authority and
allowlist consistent across all its replicas.

Configuration is bounded to 1,000 role bindings, 1,000 connectors and 1,000
objects per connector, within the existing 1 MiB total configuration limit.

## CLI and HTTP contracts

`hormuz portfolio` emits version-1 JSON. Supply an existing bearer credential
through `--token-env` (default `HORMUZ_PORTFOLIO_TOKEN`), never as a command-line
secret. All operations authenticate before opening the registry. For example,
create `scope.json` containing only administrator-entered metadata:

```json
{
  "schema_id": "hormuz.work-scope-create-request",
  "schema_version": 1,
  "kind": "use_case",
  "parent_work_scope_id": null,
  "owner_team_id": "engineering",
  "display_name": "Synthetic use case"
}
```

```bash
hormuz portfolio create scope.json --idempotency-key create-use-case-001
hormuz portfolio list --limit 50
hormuz portfolio show SCOPE_ID --version 1
hormuz portfolio version SCOPE_ID replacement.json --idempotency-key change-001
hormuz portfolio archive SCOPE_ID --expected-version 2 --idempotency-key archive-001
hormuz portfolio tombstone SCOPE_ID --expected-version 3 --idempotency-key tombstone-001
hormuz portfolio bind binding.json --idempotency-key binding-001
hormuz portfolio bindings --work-scope-id SCOPE_ID
```

Version/binding JSON uses the exact schemas in the
[approved wire bundle](portfolio-intelligence-wire-v1.json). Archive/tombstone
commands read the requested immutable version then submit the full replacement
with that expected version. A concurrent change fails rather than overwriting.

| HTTP route | CLI operation |
| --- | --- |
| `POST /v1/admin/portfolio/work-scopes` | `create FILE` |
| `GET /v1/admin/portfolio/work-scopes` | `list` |
| `GET /v1/admin/portfolio/work-scopes/{id}` | `show ID [--version N]` |
| `POST /v1/admin/portfolio/work-scopes/{id}/versions` | `version`, `archive`, `tombstone` |
| `POST /v1/admin/portfolio/work-bindings` | `bind FILE` |
| `GET /v1/admin/portfolio/work-bindings` | `bindings` |

HTTP requires one `Authorization: Bearer ...` header. Mutations additionally
require `Content-Type: application/json`, one bounded `Content-Length`, and one
`Idempotency-Key`. A new mutation and its exact replay both return the original
201 result; reads return 200. Duplicate headers/members, non-finite JSON, BOM,
trailing bytes, unknown fields and route-inapplicable filters fail closed.
Bodies are capped at 256 KiB, depth 32; responses at 1 MiB. A body read has a
10-second total deadline, including slowly arriving bytes. Errors are the fixed `hormuz.portfolio-error` v1
envelope, never reflected labels, rejected values, credentials, or raw queries.

## Stable history and pagination

Organization is the root; optional portfolio/initiative levels precede a use
case. A parent must have a strictly earlier hierarchy kind: no cycles, same-kind
parents, cross-tenant references or multiple parents. Omitted levels attach to
the nearest ancestor configured for that branch, including the organization.
Parent versions are pinned at creation/change; changing a parent never rewrites
an existing child's history. Ownership must name an existing tenant team.

Active and archived scopes can append corrections and lifecycle versions.
Tombstones are terminal and have a null current display name. Historical
versions and linked evidence remain; no record or ID is erased/reassigned.
An archived parent is not implicitly cascaded into child versions. New active
children/bindings require active referenced state. Scope changes and binding
changes require the expected current version/event. Supersession is expressed
by a new link, never by updating the previous event's stored state.

Scope lists return each scope's latest version at a frozen committed sequence.
Binding lists return immutable binding history, including superseded and
tombstone events. Both sort descending by normalized UTC event time and opaque
ID. `--start-at`/`--end-at` form an inclusive/exclusive paired window; scope and
connector filters only narrow the authenticated tenant. Page size defaults to
50, maximum 100. Continue with `--cursor TOKEN` and optionally `--limit` only.

Cursors are unpredictable, durable server-side references bound to organization,
actor, exact roles, collection, filters, sequence, ordering and schema. Later
writes cannot enter the frozen window even if the database clock moves. Cursors
expire after one hour. Authorization is repeated on every page; a cursor is
never authority. Cursor metadata is retained under the customer-controlled
database boundary; expiry does not automatically delete rows.

## Transactions, audit and retention

SQLite uses `BEGIN IMMEDIATE`; PostgreSQL uses transaction-local tenant context,
forced RLS, restricted runtime grants and a per-organization transaction advisory
lock. Competing writers compare and append within the same transaction as the
audit event and idempotency identity. PostgreSQL lock waits are capped at five
seconds and statements at ten seconds. Failed transactions leave no partial
registry/audit/idempotency result. Retry only using the original key and body;
conflicting content fails. There is no provider work to replay here.

Every successful privileged read is audited before delivery. Audits contain
only opaque actor/entity IDs, version/sequence, fixed operation/reason codes and
database-assigned receipt time. Idempotency stores keyed request digests and
references to immutable results, not another JSON body. All five new tables
reject UPDATE/DELETE; PostgreSQL runtime cannot truncate them. The existing v1
audit chain is unchanged; registry audit is a separate append-only record,
not a claim of existing v1 external anchoring coverage.

Only the authorized registry display can contain the bounded administrator
label. It must not be copied from ticket/repository/workspace content. Routine
logs, errors and release/pilot evidence omit labels. CLI display output is an
authorized view, not content-free release evidence. Customers control database,
backup and downstream-copy access/retention. Tombstoning never promises universal
erasure. See [DURABLE_DATA.md](DURABLE_DATA.md).
