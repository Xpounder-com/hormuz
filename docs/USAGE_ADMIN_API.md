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
Add `--include-outcomes` when the operator needs exact, content-free counts of
recorded model fallbacks, enforced output caps, and atomic budget-reservation
denials. The flags can be combined.

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

## Coverage summary

Use the separate coverage read when an operator needs to know exactly what this
Hormuz gateway can substantiate for the credential's own reporting scope:

```bash
hormuz usage coverage \
  --gateway https://hormuz.example.com \
  --profile ai-operations
```

`GET /v1/admin/usage/coverage` has no query fields. It returns a schema-version
1, current-UTC-month summary of authenticated, accounted gateway events in the
effective self, team, finance, or organization scope. The summary includes
recorded requests, identity-bound versus unattributed recorded requests, active
identities and teams, the four bounded identity types, and the observed Hormuz
client identifiers (`codex` and `claude-code`). It uses the same capability
checks and metadata-only read audit as the report endpoint.

This is deliberately **not** an organization-wide AI usage total. It cannot
observe pre-authentication attempts, requests that bypass Hormuz, or whether a
client is deployed but did not send a recorded request during the window. The
response fixes `organization_total` and `outside_gateway_traffic_observable` to
`false`; `observed_gateway_clients` is observed traffic, not client-deployment
coverage. Provider invoice reconciliation remains a separate aggregate billing
workflow and is never presented as a per-request or per-person final cost.

## Budget pacing

Use budget pacing for a deterministic, advisory view of whether the permitted
scope is on pace to consume its configured monthly budget:

```bash
hormuz usage pacing \
  --gateway https://hormuz.example.com \
  --profile ai-operations
```

`GET /v1/admin/usage/pacing` has no query fields and returns schema version 1.
It applies the exact same self, team, finance, or organization reporting scope
as the other usage views, and includes the effective server-applied filters.
An organization or finance viewer sees the organization budget when configured;
a team viewer sees its current team's budget; a self viewer sees the effective
per-person budget when one is configured. A missing applicable cap is returned
as `null`, not inferred from another reporting scope.

The feature is named **budget pacing**. Its methodology is a
`calendar_pace_estimate`:

```text
elapsed_fraction = elapsed UTC-month seconds / total UTC-month seconds
projected_month_end_estimated_spend = recorded month-to-date estimated spend
                                        / elapsed_fraction
```

It uses calendar days, never working days. The response includes month-to-date
estimated spend, average estimated spend per elapsed calendar-day equivalent,
projected month-end estimated spend, configured budget, projected utilization,
projected overage, unpriced-request count, and `early_period=true` from UTC
calendar days 1 through 7. At the exact UTC-month boundary, where the formula
has no elapsed time, `projection_available=false` and the projection fields are
`null` rather than invented.

`partial_projection=true` whenever one or more included requests has no
request-time price. That does not turn the missing request into zero cost.
`coverage.scope` is fixed to `gateway_captured_requests_only`; the result is
not invoice-reconciled spend, financial guidance, a complete organization-wide
AI-usage total, or employee productivity information.

Budget pacing is advisory only. A projected overage never denies a request.
Hormuz enforcement continues to use actual recorded usage plus active
reservations under the active policy.

## HTTP contract

`GET /v1/admin/usage` accepts these single-valued query fields:

- `group_by` is required and selects one supported dimension;
- `actor_id` and `team_id` are optional exact filters;
- `limit` defaults to 50 and accepts 1–100;
- `cursor` is the opaque value from the previous page.
- `include=latency` opts into a latency response: version 3 for organization
  scope and version 5 for constrained scope. Omitting it preserves version 2
  for organization scope and returns version 4 for constrained scope.
- `include=outcomes` opts into recorded policy-outcome aggregates: version 6
  for organization scope and version 8 for constrained scope.
- `include=latency,outcomes` opts into both additions: version 7 for
  organization scope and version 9 for constrained scope. The order is
  canonical; `outcomes,latency` is rejected.

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

Schema version 6 is returned only for an organization-scoped
`include=outcomes` response. Each row then adds this fixed, content-free
object:

```json
{
  "outcomes": {
    "model_fallback_requests": 12,
    "output_capped_requests": 9,
    "reservation_budget_denied_requests": 2
  }
}
```

The counts are independent and may overlap: one `fallback+capped` event adds
one to both of the first two fields. Hormuz derives them from the gateway's
stored policy-action marker only. `reservation_budget_denied_requests` counts
only the exact atomic-reservation action. Older generic `denied` rows do not
retain a reason category, so the report does not claim to classify historical
static-policy or budget-cap denials. The response coverage explicitly states
that limitation. Schema version 7 combines the version-3 `latency` object and
the version-6 `outcomes` object.

Constrained roles receive schema version 4 by default, version 5 with
`include=latency`, version 8 with `include=outcomes`, and version 9 with both
options. These response shapes add an explicit `access` object and return the
effective, server-applied filters:

```json
{
  "schema_version": 4,
  "access": {"scope": "team"},
  "filters": {"actor_id": null, "team_id": "engineering"}
}
```

The bundled CLI accepts versions 2–9 and validates the constrained scope/filter
shape. Version 2/3 organization-administrator responses are unchanged for
existing clients.

Every opt-in cursor binds its exact `include` value; using it against a default
or different opt-in view fails with
`400 invalid_usage_report_request`. This prevents a page sequence from silently
changing its response contract. Cursor state is never an authorization source:
Hormuz re-derives the role and effective filters from the credential on every
page.

## Compatibility and migration

Existing organization administrators and clients require no change: requests
without an `include` field continue to receive the exact schema-version-2
organization envelope and row shape (or version 4 for a constrained scope). To
consume timings, upgrade the Hormuz CLI/client and add `--include-latency` or
`include=latency`; to consume exact policy outcomes, add
`--include-outcomes` or `include=outcomes`. Do not treat a later schema as a
replacement for earlier schemas or send the new query to an older gateway that
does not advertise this contract.

The first request freezes an exclusive `window.end`; every cursor page reuses
that window. Rows have the token, request-status, cost-basis, rate-card,
model/provider/client, redaction, automatic-context, active-actor, and
applicable budget fields documented in [USAGE.md](USAGE.md).

Unknown, repeated, blank, over-limit, malformed, cursor/filter-mismatched, and unsupported fields return `400 invalid_usage_report_request`. A storage or mandatory audit-write failure returns `503 usage_admin_unavailable` without returning report rows.

`GET /v1/admin/usage/coverage` rejects all query fields with
`400 invalid_usage_coverage_request`. Its response does not paginate because it
is one bounded aggregate for the current request. A storage or mandatory
audit-write failure returns the same content-free `503 usage_admin_unavailable`
without a coverage object.

`GET /v1/admin/usage/pacing` likewise rejects every query field with
`400 invalid_usage_pacing_request`. Its current-month response does not
paginate. A storage or mandatory audit-write failure returns
`503 usage_admin_unavailable` without a budget-pacing object.

## Audit and interpretation

Every successful report page, coverage read, and budget-pacing read writes
`security.admin.usage_read` before its result is returned. The event records
the organization, viewing actor, grouping, frozen window, row count, and
SHA-256 digests of the effective actor/team filters. It contains no request
content. The event appears in local `audit-export --kind security` output.

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
