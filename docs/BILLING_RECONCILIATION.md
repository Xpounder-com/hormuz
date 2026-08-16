# Provider cost import and reconciliation

Hormuz can import complete cost-report API responses from OpenAI and Anthropic, preserve their normalized billing dimensions in the local usage database, and compare one immutable provider snapshot with request-time Hormuz estimates for the same provider, organization, and time window.

This is an offline reconciliation kernel. Hormuz does not yet fetch reports with an administrator credential, ingest a final invoice, or claim that aggregate provider cost is a final per-person charge. The commands deliberately keep provider administrator keys outside the Hormuz process: an operator downloads the reports, then imports the JSON files.

## Supported provider contracts

- OpenAI: `GET /v1/organization/costs`. The [official Costs API](https://platform.openai.com/docs/api-reference/usage/costs) returns daily buckets with decimal USD values and optional project and line-item dimensions. OpenAI recommends the Costs endpoint for financial reconciliation rather than deriving spend only from token usage.
- Anthropic: `GET /v1/organizations/cost_report`. The [official Usage and Cost Admin API](https://platform.claude.com/docs/en/manage-claude/usage-cost-api) returns daily cost buckets. Amounts are decimal strings in cents, including fractional cents; grouping by workspace and description exposes model, service-tier, token-type, cache, and other cost dimensions when available.

Both endpoints use administrator credentials that are different from ordinary inference credentials. Those credentials are needed to download organization billing reports, but they are not read or stored by `hormuz billing import`.

## Download complete reports

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

The response body does not prove which filters were used to obtain it, and it does not carry the cursor that requested the current page. The offline importer therefore cannot independently prove that separately supplied files belong to one unfiltered query. Preserve the download command and report files as operator evidence. A future authenticated fetcher can bind request parameters and page cursors to the import evidence.

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
  --json
```

Omit `--import-id` to use the latest imported snapshot for that organization and provider.

## Accounting semantics

- Provider amounts are stored as canonical decimal USD text. OpenAI JSON numbers are parsed as decimals, not binary floating point. Anthropic fractional-cent strings are divided by exactly 100. Hormuz does not round either source into request-level micro-USD storage.
- Positive, zero, and negative provider items are all included. Credits, discounts, and adjustments remain signed provider amounts; Hormuz does not guess a financial category from free-form line-item text.
- Cache, batch, model, service-tier, token-type, line-item, project, and workspace dimensions are retained when the provider supplies them. Hormuz does not reprice those items during import.
- Gateway succeeded, failed, denied, and unpriced request counts remain separate. A failed or rate-limited request is not assumed to be free or billable without provider evidence.
- `provider_cost_usd` has basis `provider_reported`; `gateway_estimated_cost_usd` has basis `request_time_estimated`. Their signed difference is `variance_usd`.
- A positive variance is labeled possible unobserved or adjusted cost. It does not by itself prove traffic bypassed Hormuz: filters, delayed data, credits, tool fees, rounding, provider-side adjustments, and unsupported traffic can also explain a difference.
- Provider cost reports do not universally map a billed amount to a Hormuz request, employee, or team. Per-person and per-team costs remain estimates unless the organization isolates provider projects or workspaces at that exact accounting boundary and records an explicit mapping.
- Pre-migration usage rows without a trustworthy organization binding are excluded from the organization comparison and counted as `legacy_unattributed_gateway_requests`.

`provider_report_completeness` is currently `not_verifiable_from_response`, and overall `coverage_status` is therefore `partial_unverified_provider_scope`. Do not present the result as total organizational spend or a final invoice.

## Stored data and limits

Hormuz stores the organization ID, provider, import fingerprint, timestamps, exact decimal amount, project/workspace ID, provider line item, and supported Anthropic billing dimensions. It does not store the raw provider JSON, administrator credential, prompt, response, code, filename, or matched secret. Unknown provider response fields are not retained.

Each input page is limited to 16 MiB, the complete import to 32 MiB and 100 pages, and duplicate JSON object members and non-standard numeric constants fail closed. The database is still the local single-node SQLite usage store. Shared tenant isolation, RBAC for billing operators, encrypted/KMS-backed storage, authenticated provider polling, invoice/credit reconciliation, retention, HA, and immutable external audit remain enterprise release work.
