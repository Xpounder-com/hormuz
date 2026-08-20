# Provider cost import and reconciliation

Hormuz can fetch or import complete cost-report API responses from OpenAI and Anthropic, preserve their normalized billing dimensions in the local usage database, and compare one immutable provider snapshot with request-time Hormuz estimates for the same provider, organization, and time window.

The authenticated fetch path is an operator-run ingestion checkpoint, not a hosted billing service or final-invoice workflow. It binds the stored evidence to the exact fixed provider endpoint, date window, grouping, and completed cursor chain used by Hormuz. It does not prove provider data freshness, include every external billing adjustment, or turn aggregate provider cost into a final per-person charge.

## Supported provider contracts

- OpenAI: `GET /v1/organization/costs`. The [official Costs API](https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/usage/methods/costs) returns daily buckets with decimal USD values and project, line-item, and API-key grouping options. Hormuz fixes its authenticated query to all projects grouped by `project_id` and `line_item`, with no project or API-key filters.
- Anthropic: `GET /v1/organizations/cost_report`. The [official Usage and Cost Admin API](https://platform.claude.com/docs/en/manage-claude/usage-cost-api) returns daily cost buckets. Amounts are decimal strings in cents, including fractional cents; grouping by workspace and description exposes model, service-tier, token-type, cache, and other cost dimensions when available.

Both endpoints use administrator credentials that are different from ordinary inference credentials. `billing fetch` reads the selected credential from an environment variable for the duration of the process; it never accepts the value as a CLI argument and does not persist or print it. `billing import` remains credential-free.

The Anthropic integration in this milestone is specifically for Claude Platform organizations using the Admin API. Claude Enterprise uses a separate Analytics API and key, and Claude Platform on AWS does not currently expose this endpoint. Anthropic also states that Priority Tier costs are not included in the cost endpoint. Those cases are outside this fetch contract.

## Fetch with an administrator credential

Inject the provider admin key from the organization's secret manager into the operator process. Do not put it in `hormuz.json`, shell history, source control, or a command-line argument. The defaults are `OPENAI_ADMIN_KEY` and `ANTHROPIC_ADMIN_KEY`; `--credential-env` can select another valid environment-variable name.

OpenAI example, with date-only UTC bounds where start is inclusive and end is exclusive:

```bash
hormuz --config hormuz.json billing fetch \
  --organization xpounder \
  --provider openai \
  --start 2026-08-01 \
  --end 2026-08-16
```

Anthropic example:

```bash
hormuz --config hormuz.json billing fetch \
  --organization xpounder \
  --provider anthropic \
  --start 2026-08-01 \
  --end 2026-08-16
```

Hormuz sends credentials only to the provider's fixed HTTPS origin, refuses redirects, applies bounded retries to transient failures, follows the provider cursor through `has_more=false`, validates every page before committing, and imports the normalized snapshot atomically. The raw response pages are transient and are not retained. Empty authenticated windows are valid and remain bound to the requested dates.

## Offline download and import

Example OpenAI request:

```bash
curl --get 'https://api.openai.com/v1/organization/costs' \
  --header "Authorization: Bearer $OPENAI_ADMIN_KEY" \
  --header 'Content-Type: application/json' \
  --data-urlencode 'start_time=1785542400' \
  --data-urlencode 'end_time=1788220800' \
  --data-urlencode 'limit=31' \
  --data-urlencode 'group_by[]=project_id' \
  --data-urlencode 'group_by[]=line_item' \
  --output openai-costs-page-1.json
```

Example Anthropic request:

```bash
curl --get 'https://api.anthropic.com/v1/organizations/cost_report' \
  --header "x-api-key: $ANTHROPIC_ADMIN_KEY" \
  --header 'anthropic-version: 2023-06-01' \
  --header 'User-Agent: Hormuz/0.1.0 (https://github.com/Xpounder-com/hormuz)' \
  --data-urlencode 'starting_at=2026-08-01T00:00:00Z' \
  --data-urlencode 'ending_at=2026-09-01T00:00:00Z' \
  --data-urlencode 'group_by[]=workspace_id' \
  --data-urlencode 'group_by[]=description' \
  --output anthropic-costs-page-1.json
```

If a response has `has_more: true`, download the next page using its `next_page` value and continue until the final response has `has_more: false`. Save the responses separately and retain their order. Hormuz rejects an import whose last supplied page says more data exists, or whose intermediate pagination flags are inconsistent.

The response body does not prove which filters were used to obtain it, and it does not carry the cursor that requested the current page. The offline importer therefore cannot independently prove that separately supplied files belong to one unfiltered query. Preserve the download command and report files as operator evidence. Use `billing fetch` when Hormuz itself should bind the request parameters and page chain to the stored source evidence.

## Import and reconcile

Import one complete provider snapshot. Repeat `--input` in pagination order:

```bash
hormuz --config hormuz.json billing import \
  --organization xpounder \
  --provider openai \
  --input openai-costs-page-1.json \
  --input openai-costs-page-2.json
```

The command returns a `pci_...` import ID, normalized time bounds and item counts, and a SHA-256 fingerprint. Importing the same normalized snapshot again is idempotent, including when two local import jobs race.

Compare that provider snapshot with Hormuz estimates:

```bash
hormuz --config hormuz.json billing reconcile \
  --organization xpounder \
  --provider openai \
  --import-id pci_0123456789abcdef0123456789abcdef \
  --json \
  --fail-on-review
```

Omit `--import-id` to use the latest imported snapshot for that organization and provider.

## Versioned exception policy

The optional `billing_reconciliation` configuration evaluates each aggregate
organization/provider result against exact, versioned rules:

- absolute variance in USD;
- relative variance in basis points;
- unpriced gateway requests;
- legacy gateway requests without a trustworthy organization binding;
- provider items without a project/workspace scope; and
- whether the snapshot came from Hormuz's authenticated fixed-query path.

Relative variance is
`abs(provider_cost - gateway_estimate) / abs(provider_cost) * 10000`. If both
costs are zero, it is zero. If provider-reported cost is zero but the variance
is nonzero, the relative basis is unavailable and an enabled relative rule
fails closed to `variance_basis_unavailable`. A value exactly equal to a
configured maximum is allowed; only a larger value requires review.

The JSON reconciliation schema is version `2` and returns
`exception_status` (`not_evaluated`, `clear`, or `review_required`), stable
`exception_reasons`, exact `variance_absolute_usd`, relative
`variance_basis_points`, and the complete policy version plus canonical
SHA-256. `--fail-on-review` still prints that result and then exits `3` when
review is required, allowing a scheduled job or CI control to alert without
discarding evidence. It exits `2` if requested while the policy is disabled.

This is deterministic exception classification, not a persistent finance case
manager. A reviewer identity, acknowledgements, resolution notes, invoice
finalization, notifications, and organization-scoped remote billing RBAC remain
open. The CLI does not infer that a variance proves bypass, and it does not
allocate aggregate provider cost to a team or employee.

## Accounting semantics

- Provider amounts are stored as canonical decimal USD text. OpenAI JSON numbers are parsed as decimals, not binary floating point. Anthropic fractional-cent strings are divided by exactly 100. Hormuz does not round either source into request-level micro-USD storage.
- Positive, zero, and negative provider items are all included. Credits, discounts, and adjustments remain signed provider amounts; Hormuz does not guess a financial category from free-form line-item text.
- Cache, batch, model, service-tier, token-type, line-item, project, and workspace dimensions are retained when the provider supplies them. Hormuz does not reprice those items during import.
- Gateway succeeded, failed, denied, and unpriced request counts remain separate. A failed or rate-limited request is not assumed to be free or billable without provider evidence.
- `provider_cost_usd` has basis `provider_reported`; `gateway_estimated_cost_usd` has basis `request_time_estimated`. Their signed difference is `variance_usd`.
- A positive variance is labeled possible unobserved or adjusted cost. It does not by itself prove traffic bypassed Hormuz: filters, delayed data, credits, tool fees, rounding, provider-side adjustments, and unsupported traffic can also explain a difference.
- Provider cost reports do not universally map a billed amount to a Hormuz request, employee, or team. Per-person and per-team costs remain estimates unless the organization isolates provider projects or workspaces at that exact accounting boundary and records an explicit mapping.
- Pre-migration usage rows without a trustworthy organization binding are excluded from the organization comparison and counted as `legacy_unattributed_gateway_requests`.

For an offline import, `provider_report_completeness` is `not_verifiable_from_response` and `coverage_status` is `partial_unverified_provider_scope`. For an authenticated fetch, they become `authenticated_query_pagination_complete` and `partial_authenticated_provider_endpoint_scope`. The latter proves the completed fixed Hormuz query, not final invoice completeness, provider freshness, or coverage outside the endpoint's documented scope. Do not present either result as a final invoice.

## Stored data and limits

Hormuz stores the organization ID, provider, import fingerprint, timestamps, exact decimal amount, project/workspace ID, provider line item, supported Anthropic billing dimensions, and metadata-only source evidence. Authenticated source evidence includes the API contract identifier, UTC query window, and fixed query scope. It does not store the raw provider JSON, administrator credential, prompt, response, code, filename, or matched secret. Unknown provider response fields are not retained.

Each page is limited to 16 MiB, the complete report to 32 MiB and 100 pages, and duplicate JSON object members and non-standard numeric constants fail closed. Authenticated date windows are limited to 366 days. The database is still the local single-node SQLite usage store. Scheduled polling, secure hosted credential custody, shared tenant isolation, RBAC for billing operators, encrypted/KMS-backed storage, invoice/credit reconciliation, retention, HA, and immutable external audit remain enterprise release work.
