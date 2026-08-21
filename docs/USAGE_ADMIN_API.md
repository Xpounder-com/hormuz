# Usage administration API

Hormuz exposes a read-only, metadata-only usage report while employees continue
using Codex or Claude Code. The gateway derives organization and reporting scope
from the authenticated identity; neither the CLI nor the HTTP query can select
another organization or widen the credential's scope.

## Capability and credential

Assign exactly one usage-report scope to an identity or OIDC subject mapping:

```json
{
  "actor_id": "ai-operations-admin",
  "organization_id": "xpounder",
  "capabilities": ["usage_organization_viewer"]
}
```

| Capability | Allowed view |
| --- | --- |
| `usage_self_viewer` | The authenticated person's own usage. Hormuz forces the actor filter to that person. |
| `usage_team_viewer` | Aggregates for the authenticated person's current team. Person rows and actor filters are denied. |
| `usage_finance_viewer` | Organization-wide aggregates by organization, model, client, or provider. Person/team rows and actor/team filters are denied. |
| `usage_organization_viewer` | All current-organization report dimensions, including person-level drill-down and bounded actor/team filters. |
| `usage_viewer` | Backward-compatible alias for `usage_organization_viewer`. |

Configuration rejects an identity that combines distinct usage-report scopes.
`policy_admin`, `session_admin`, `dlp_approver`, and `context_promoter` do not
implicitly grant usage visibility. Authentication without a usage-report
capability receives `403 usage_viewer_capability_required`; a query outside a
valid scope receives `403 usage_report_scope_forbidden`.

These capabilities grant access only to usage metadata, never prompts,
responses, source files, provider keys, governed-context content, session
credentials, or DLP matched values. A saved Hormuz session profile or a
short-lived workload OIDC credential can be used instead of a static bootstrap
token.

## CLI

```bash
hormuz usage report \
  --gateway https://hormuz.example.com \
  --profile ai-operations \
  --group-by team
```

Add `--include-latency` when an operator needs the versioned content-free
gateway, policy, provider, and automatic-context latency histograms.

An organization-scoped administrator can group by `organization`, `team`,
`person`, `model`, `client`, or `provider`, and can use exact event-time
`--actor` and `--team` filters. Narrower roles are constrained by the table
above; the gateway applies their actor or team filter server-side. `--limit`
accepts 1–100 rows. When `next_cursor` is present, pass it with the same
grouping and requested filters:

```bash
hormuz usage report \
  --gateway https://hormuz.example.com \
  --profile ai-operations \
  --group-by person \
  --limit 50 \
  --cursor "$NEXT_CURSOR"
```

For a workload credential, replace `--profile` with `--credential-env HORMUZ_TOKEN`. Plain HTTP is refused except explicit loopback development with `--allow-insecure-http`, redirects are refused, credentials are never included in output, and responses are bounded and schema-checked.

## HTTP contract

`GET /v1/admin/usage` accepts these single-valued query fields:

- `group_by` is required and selects one supported dimension;
- `actor_id` and `team_id` are optional exact filters;
- `limit` defaults to 50 and accepts 1–100;
- `cursor` is the opaque value from the previous page.
- `include=latency` opts into a latency response: version 3 for organization
  scope and version 5 for constrained scope. Omitting it preserves version 2
  for organization scope and returns version 4 for constrained scope.

An organization-scoped response has schema version 2. Version 2 adds the
content-free automatic-context aggregates documented in [USAGE.md](USAGE.md):

```json
{
  "schema_version": 2,
  "organization_id": "xpounder",
  "group_by": "team",
  "filters": {"actor_id": null, "team_id": null},
  "window": {
    "start": "2026-08-01T00:00:00+00:00",
    "end": "2026-08-16T12:00:00+00:00",
    "timezone": "UTC"
  },
  "coverage": {
    "scope": "gateway_captured_requests_only",
    "legacy_unattributed_rows_excluded": true,
    "provider_invoice_reconciled": false
  },
  "rows": [],
  "next_cursor": null
}
```

Schema version 3 is returned only for an organization-scoped
`include=latency` response. Each row then adds a `latency` object with
`gateway`, `policy`, `provider`, and `context` cumulative histograms. Every
histogram contains an observed count, arithmetic average, maximum, and fixed
millisecond buckets ending in `le_ms=null` for positive infinity. A zero-count
histogram has null average and maximum. The coverage object also states that
only accounted gateway requests are included, historical rows without timings
are excluded, and no SLO target is configured.

Constrained roles receive schema version 4, or version 5 with
`include=latency`. These response shapes add an explicit `access` object and
return the effective, server-applied filters:

```json
{
  "schema_version": 4,
  "access": {"scope": "team"},
  "filters": {"actor_id": null, "team_id": "engineering"}
}
```

The bundled CLI accepts versions 2–5 and validates the constrained scope/filter
shape. Version 2/3 organization-administrator responses are unchanged for
existing clients.

Latency cursors bind `include=latency`; using one against a non-latency view, or
using a non-latency cursor against the latency view, fails with
`400 invalid_usage_report_request`. This prevents a page sequence from silently
changing its response contract. Cursor state is never an authorization source:
Hormuz re-derives the role and effective filters from the credential on every
page.

## Compatibility and migration

Existing organization administrators and clients require no change: requests
without `include=latency` continue to receive the exact schema-version-2
envelope and row shape. To consume timings, upgrade the Hormuz CLI/client and
add `--include-latency` or `include=latency`; the response is version 3 for an
organization scope and version 5 for a constrained scope. Do not treat a later
schema as a replacement for earlier schemas or send the new query to an older
gateway that does not advertise this contract.

The first request freezes an exclusive `window.end`; every cursor page reuses
that window. Rows have the token, request-status, cost-basis, rate-card,
model/provider/client, redaction, automatic-context, active-actor, and
applicable budget fields documented in [USAGE.md](USAGE.md).

Unknown, repeated, blank, over-limit, malformed, cursor/filter-mismatched, and unsupported fields return `400 invalid_usage_report_request`. A storage or mandatory audit-write failure returns `503 usage_admin_unavailable` without returning report rows.

## Audit and interpretation

Every successful page writes `security.admin.usage_read` before its rows are
returned. The event records the organization, viewing actor, grouping, frozen
window, row count, and SHA-256 digests of the effective actor/team filters. It
contains no request content. The event appears in local `audit-export --kind
security` output.

The report covers only generation attempts recorded through this Hormuz gateway. Legacy rows without an organization are excluded rather than guessed, and request-time costs remain estimates until separately reconciled. Tokens and spend measure consumption, not employee productivity or work quality. Treat person-level output as access-controlled employee metadata and never as a performance ranking.

Timing fields are also consumption and operations metadata, not employee
performance evidence. `gateway` measures from the start of Hormuz request
handling until immediately before its mandatory usage write; it does not
include the database commit itself. Successful provider streams therefore
include downstream relay, while a denied request stops when its usage snapshot
is ready to persist. `policy` measures the synchronous policy decision only.
`provider` starts immediately before the upstream open and runs through
response relay, so it includes downstream backpressure; it is absent when no
provider was attempted. `context` uses the existing automatic-injection
assembly timing and is observed only when a pack was injected.
Pre-authentication failures, unaccounted administration routes, proxy/TLS time,
traffic bypassing Hormuz, and provider-side work after the connection closes
are not included.

This usage-report surface is a verified single-node SQLite boundary. It does not itself administer SCIM; the separate local lifecycle is documented in [SCIM.md](SCIM.md). Neither surface claims shared hosted storage, PostgreSQL row security, SIEM delivery, externally immutable audit, or complete provider-account coverage.
