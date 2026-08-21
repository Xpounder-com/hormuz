# Usage, cost, and budget reporting

Hormuz records one metadata-only usage event for each accounted generation attempt. The event snapshots the organization, actor, team, identity type, authentication source, and policy version at request time; the client, provider protocol, requested/routed/provider-reported model; policy outcome; provider-reported token categories; configured-rate-card cost estimate; status; provider request ID; and secret-redaction count. Prompt and response bodies are not stored.

## CLI reports

`status` reports the current UTC calendar month. The default view groups by person:

```bash
hormuz --config hormuz.json status
```

Available dimensions:

```bash
hormuz --config hormuz.json status --group-by organization
hormuz --config hormuz.json status --group-by team
hormuz --config hormuz.json status --group-by person
hormuz --config hormuz.json status --group-by model
hormuz --config hormuz.json status --group-by client
hormuz --config hormuz.json status --group-by provider
```

Limit any view to an event-time team or actor:

```bash
hormuz --config hormuz.json status --group-by model --team engineering
hormuz --config hormuz.json status --group-by provider --actor alice
```

Use JSON for scripts, exports, or a future dashboard:

```bash
hormuz --config hormuz.json status --group-by person --json
```

The JSON command emits `hormuz.usage-report` v1. Its `rows` array contains the usage rows; the envelope declares the UTC month, grouping/filter selection, cost basis, allocation basis, and coverage. See [CONTRACTS.md](CONTRACTS.md) before integrating it into a script.

## Field semantics

- `requests`, `succeeded`, `failed`, `denied`, and `rate_limited` describe gateway outcomes. `rate_limited` means an upstream provider returned HTTP 429 after Hormuz allowed the request.
- `input_tokens` and `output_tokens` come from the provider response.
- `cache_read_tokens`, `cache_write_tokens`, and `reasoning_tokens` are shown separately when the provider exposes them. Some are subcategories of input or output, so Hormuz does not add them again to `total_tokens`.
- `total_tokens` is input plus output tokens.
- `cost_microusd` is the integer accounting value; `cost_usd` is its decimal representation.
- `active_actors` counts distinct attributed identities in the row.
- `redactions` counts transformations attached to accounted generation events. The separate secret-event ledger also covers non-generation endpoints.
- `budget_usd`, `budget_remaining_usd`, and `budget_used_percent` appear for organization, team, and person scopes when a corresponding cap is configured.

Cost is an estimate based on the rate card attached to the routed model in the active Hormuz configuration. The precise contract labels are `configured_rate_card_estimate`, `direct_gateway_request`, and `gateway_captured_requests_only`. Provider prices, discounts, credits, cache rules, and invoice adjustments can change. Production reporting must version rate cards and reconcile estimates against provider invoices before calling spend final.

## Concurrent budget enforcement

Before an accounted provider call, Hormuz creates an atomic reservation against every applicable organization, team, and employee token/cost cap. SQLite uses an immediate local transaction; the optional PostgreSQL repository uses a transaction-local tenant context, forced row-level security, and a per-organization/month advisory transaction lock. The reservation conservatively uses the serialized request byte count as the input-token upper bound, the enforced maximum output tokens, and uncached configured token rates. A competing request is denied with `hormuz_budget_denied` when its projected reservation would exceed any scope.

The reservation is released after the actual provider usage event is recorded. It also has a bounded expiry so an interrupted process cannot hold budget forever. Configurations with monthly token or spend limits must therefore give every identity an effective `max_output_tokens` policy. See [STORAGE.md](STORAGE.md) for the PostgreSQL operational boundary and recovery rules.

This is intentionally conservative: a request may be rejected even though caching or a short answer would have made its eventual cost smaller. It closes the concurrent-request race for configured token charges, but it cannot reserve provider tool-call fees or invoice adjustments that are absent from the rate card. Those remain reconciliation items and are another reason not to describe an estimate as final invoiced spend.

Per-person reporting is trustworthy only when every human, service account, and CI workload has a unique Hormuz identity. Shared tokens collapse attribution. Because the event stores team metadata at request time, later team transfers do not rewrite historical allocation.

Token volume and spend measure consumption, not employee productivity or work quality. Do not use this report as a performance ranking. It also covers only requests that passed through Hormuz; future enterprise reporting must surface gateway coverage and provider-invoice reconciliation rather than presenting partial traffic as complete organizational usage.
