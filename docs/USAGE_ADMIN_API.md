# Usage administration API

Hormuz exposes a read-only, metadata-only usage report for organization administrators while employees continue using Codex or Claude Code. The gateway derives organization scope from the authenticated identity; neither the CLI nor the HTTP query can select another organization.

## Capability and credential

Add `usage_viewer` only to an administrator identity or OIDC subject mapping:

```json
{
  "actor_id": "ai-operations-admin",
  "organization_id": "xpounder",
  "capabilities": ["usage_viewer"]
}
```

Authentication without this capability receives `403 usage_viewer_capability_required`. The capability grants access to usage metadata, not prompts, responses, source files, provider keys, governed-context content, session credentials, or DLP matched values. A saved Hormuz session profile or a short-lived workload OIDC credential can be used instead of a static bootstrap token.

## CLI

```bash
hormuz usage report \
  --gateway https://hormuz.example.com \
  --profile ai-operations \
  --group-by team
```

The report can group by `organization`, `team`, `person`, `model`, `client`, or `provider`. Exact event-time `--actor` and `--team` filters are optional. `--limit` accepts 1–100 rows. When `next_cursor` is present, pass it with the same grouping and filters:

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

A successful response has schema version 2. Version 2 adds the content-free automatic-context aggregates documented in [USAGE.md](USAGE.md):

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

The first request freezes an exclusive `window.end`; every cursor page reuses that window. Rows have the token, request-status, cost-basis, rate-card, model/provider/client, redaction, automatic-context, active-actor, and applicable budget fields documented in [USAGE.md](USAGE.md). Cursor state is never an authorization source: the gateway validates it and re-derives organization from the current credential on every page.

Unknown, repeated, blank, over-limit, malformed, cursor/filter-mismatched, and unsupported fields return `400 invalid_usage_report_request`. A storage or mandatory audit-write failure returns `503 usage_admin_unavailable` without returning report rows.

## Audit and interpretation

Every successful page writes `security.admin.usage_read` before its rows are returned. The event records the organization, viewing actor, grouping, frozen window, row count, and SHA-256 digests of optional actor/team filters. It contains no request content. The event appears in local `audit-export --kind security` output.

The report covers only generation attempts recorded through this Hormuz gateway. Legacy rows without an organization are excluded rather than guessed, and request-time costs remain estimates until separately reconciled. Tokens and spend measure consumption, not employee productivity or work quality. Treat person-level output as access-controlled employee metadata and never as a performance ranking.

This is a verified single-node SQLite boundary. It does not accept ADR 0002 or claim shared hosted storage, PostgreSQL row security, SIEM delivery, externally immutable audit, SCIM, or complete provider-account coverage.
