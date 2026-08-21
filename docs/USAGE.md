# Usage, cost, and budget reporting

Hormuz records one metadata-only usage event for each accounted generation attempt. The event snapshots the organization, actor, and team at request time, the client, provider protocol, requested, routed, and provider-returned actual model, policy outcome, exact immutable governance-policy version, provider-reported token categories, calculated billable-token units, configured-rate-card cost estimate and version, currency, status, provider request ID, and secret-redaction count. When automatic context policy is evaluated, the event also stores its mode, outcome, safe reason, pack/record lineage, version identifiers, estimated rendered tokens, assembly time, and authoritative fresh/already-present state. A strict provider-specific allowlist preserves native usage metadata needed for later reconciliation. Provider-returned model and request IDs are retained only when they match bounded opaque ASCII identifier grammars; arbitrary provider header or body text cannot become those fields. Prompt, response, retrieval query, and rendered context bodies are not stored. Legacy rows created before the organization field existed remain unbound rather than being assigned a guessed tenant.

Newly accounted generation attempts also store nullable, non-negative integer
timings for gateway handling, synchronous policy evaluation, and attempted
provider work. They contain no route query, prompt, response, credential,
filename, source text, network address, or arbitrary label. Historical events
remain null rather than being presented as zero-latency observations. These
columns are deliberately excluded from the stable usage audit-export v3 shape;
administrative aggregation is opt-in through usage-report schemas v3 and later.

## CLI reports

`status` reports the current UTC calendar month. The default view groups by person:

```bash
hormuz --config hormuz.json status
```

Local reports are explicitly organization-scoped. A configuration containing one organization selects it automatically; a configuration containing more than one requires `--organization`. Legacy rows without an organization are excluded from an organization report.

Available dimensions:

```bash
hormuz --config hormuz.json status --group-by organization
hormuz --config hormuz.json status --group-by team
hormuz --config hormuz.json status --group-by person
hormuz --config hormuz.json status --group-by model
hormuz --config hormuz.json status --group-by requested_model
hormuz --config hormuz.json status --group-by actual_model
hormuz --config hormuz.json status --group-by policy
hormuz --config hormuz.json status --group-by status
hormuz --config hormuz.json status --group-by client
hormuz --config hormuz.json status --group-by provider
```

Add `--include-latency` for content-free cumulative histograms in JSON, or for
p95 histogram-bucket upper bounds in the tabular view. A p95 bucket is an upper
bound from the fixed buckets, not an exact percentile and not an approved SLO.
Add `--include-outcomes` for exact, content-free counts of policy-routed model
fallbacks, enforced output caps, and atomic budget-reservation denials. The two
flags can be combined.

Limit any view to an event-time team or actor:

```bash
hormuz --config hormuz.json status --group-by model --team engineering
hormuz --config hormuz.json status --group-by provider --actor alice
```

Use JSON for scripts, exports, or a future dashboard:

```bash
hormuz --config hormuz.json status --group-by person --json
```

An administrator who should not receive filesystem or database access can use the authenticated gateway contract instead:

```bash
hormuz usage report \
  --gateway https://hormuz.example.com \
  --profile ai-operations \
  --group-by person
```

This route requires an explicit usage-report capability, derives organization
and the permitted reporting scope from the credential, supports frozen-window
cursor pagination, and audits every returned page. See
[USAGE_ADMIN_API.md](USAGE_ADMIN_API.md).

For a bounded account of gateway-observed coverage instead of report rows, an
authorized operator can run:

```bash
hormuz usage coverage \
  --gateway https://hormuz.example.com \
  --profile ai-operations
```

It reports only authenticated, accounted requests Hormuz recorded within the
credential's self, team, finance, or organization scope. It explicitly does
not observe gateway-bypassing or pre-authentication traffic, client deployment
coverage, or an organization-wide AI total. Provider invoice reconciliation is
separate; see [USAGE_ADMIN_API.md](USAGE_ADMIN_API.md) and
[BILLING_RECONCILIATION.md](BILLING_RECONCILIATION.md).

For an advisory current-month budget pace in the credential's existing reporting
scope, run:

```bash
hormuz usage pacing \
  --gateway https://hormuz.example.com \
  --profile ai-operations
```

This is **budget pacing**, not a predictive forecast engine. Its
`calendar_pace_estimate` divides Hormuz-recorded month-to-date estimated spend
by the elapsed fraction of the UTC calendar month. It reports the current
estimated spend, estimated per-calendar-day pace, projected month-end estimated
spend, applicable configured budget, projected utilization/overage, unpriced
requests, and whether the first seven UTC calendar days make the projection
early. It uses calendar days rather than working days.

The output is advisory and content-free. A projection with any unpriced request
is marked `partial_projection=true`; it is not invoice-reconciled spend,
financial guidance, organization-wide usage proof, or employee performance
data. A projected overage never changes enforcement: only actual usage plus
active budget reservations under the active policy can deny a request. See
[USAGE_ADMIN_API.md](USAGE_ADMIN_API.md) for the exact authenticated contract
and scope rules.

For a current-month model mix in the same reporting scope, run:

```bash
hormuz usage model-mix \
  --gateway https://hormuz.example.com \
  --profile ai-operations
```

This is a content-free consumption view, not a model-quality or employee
performance score. It groups each accounted attempt by the provider-returned
actual model when available, otherwise by the routed model. It reports request,
input-plus-output-token, and estimated-spend shares together with succeeded,
failed, denied, and unpriced-request counts. Estimated-spend shares are marked
partial when a provider attempt has no configured price, and are never
invoice-reconciled cost or proof of total organizational AI use.

## Field semantics

- `requests`, `succeeded`, `failed`, and `denied` describe gateway outcomes.
- opt-in `latency` contains cumulative fixed-bucket gateway, policy, provider,
  and injected-context measurements. Its count is the number of observed
  timings, which may be lower than `requests`; null historical values and
  requests that never reached a provider are excluded from the applicable
  histogram.
- opt-in `outcomes` contains only gateway-recorded policy-action aggregates:
  `model_fallback_requests`, `output_capped_requests`, and
  `reservation_budget_denied_requests`. A fallback that is also capped counts
  in both applicable fields. The reservation field is deliberately narrower
  than generic `denied`: it counts only the exact action recorded when the
  atomic in-flight reservation failed. Hormuz does not infer the reason for
  generic historical denials that did not persist one.
- `requested_model`, `resolved_alias`, and `upstream_model` preserve policy/routing intent; `actual_model` is the bounded provider-returned model identifier when present. The legacy `model` report prefers the actual model and falls back through those routing fields when the provider omits it. The explicit `requested_model` and `actual_model` groupings do not conflate those facts: an actual-model row has `actual_model_reported=true` only when the provider returned it; otherwise it uses `scope_id="not_reported"` with `actual_model_reported=false`. `policy` groups by the recorded bounded Hormuz policy action, and `status` groups by the recorded gateway outcome.
- `model_mix` applies that same actual-model-or-routed-fallback basis to the
  caller's current UTC calendar month. Its request share includes all accounted
  gateway attempts; status counts make succeeded, failed, and denied attempts
  explicit. Its token share uses `total_tokens` (input plus output), rather
  than double-counting provider cache or reasoning subcategories. Its
  estimated-spend share includes only priceable gateway events and is partial
  whenever `unpriced_requests` is nonzero.
- `input_tokens` and `output_tokens` come from the provider response.
- `cache_read_tokens`, `cache_write_tokens`, and `reasoning_tokens` are shown separately when the provider exposes them. Some are subcategories of input or output, so Hormuz does not add them again to `total_tokens`.
- Hormuz does not expose a cache-savings metric in this release. Provider-reported cache token categories can support a request-time cost estimate only under the exact configured rate card; neither those estimates nor an allocated person/team amount is a final provider invoice. See [provider-native cache policy](PROVIDER_CACHE_POLICY.md) and [billing reconciliation](BILLING_RECONCILIATION.md).
- `total_tokens` is input plus output tokens.
- `billable_tokens` is a normalized volume, not a price: OpenAI input plus output because cache-read and cache-write tokens are input subsets; Anthropic uncached input plus cache-read input plus cache-write input plus output because those are separate categories.
- `provider_usage` is the provider-native, metadata-only subset retained for reconciliation. Unknown fields, content, request bodies, and response bodies are discarded before persistence. The parser caps its input buffer at 1 MiB for one SSE line or 10 MiB for one non-stream JSON response and releases those transient buffers after parsing. Oversized or malformed accounting metadata does not alter the response relayed to the employee.
- `provider_request_id` is an optional bounded opaque ASCII identifier. Unsafe, duplicate, content-like, Unicode, control-character, or overlong upstream values are omitted from both usage storage and downstream response metadata.
- `cost_microusd` is the integer accounting value; `cost_usd` is its decimal representation. `estimated_cost_microusd` and `estimated_cost_usd` include only events whose cost basis is an estimate.
- `cost_bases` identifies `estimated`, migrated `estimated_legacy`, `not_available`, or `not_applicable` events in the report row. `unpriced_requests` counts provider attempts for which a price was unavailable. A provider response receives `estimated` only after Hormuz observes valid input and output usage in a non-stream response or terminal stream event. A successful response with missing, incomplete, oversized, or malformed accounting metadata remains content-free but is recorded as `not_available` with zero estimated cost; it must be resolved through provider reconciliation rather than treated as free usage.
- `currency` is currently fixed to USD because the persisted unit is micro-USD.
- `rate_card_version` is snapshotted on every event. Reports return every `rate_card_versions` value represented in a group.
- `active_actors` counts distinct attributed identities in the row.
- `redactions` counts transformations attached to accounted generation events. The separate secret-event ledger also covers non-generation endpoints.
- `context_injected_requests` counts requests whose authorized block was present in the provider-bound body; `context_required_denials` counts required-mode requests denied by context policy.
- `context_estimated_tokens` is Hormuz's deterministic estimate of rendered reference size, not a provider tokenizer result. `context_packs_used` counts distinct non-null pack IDs in the group.
- Per-event context lineage includes `context_injection_mode`, `context_injection_outcome`, `context_injection_reason`, `context_pack_id`, `context_record_ids`, policy/retrieval/render versions, trusted repository revision, assembly milliseconds, and reuse status. It deliberately excludes the query, record content, and raw repository/branch/revision selector headers. Authorized repository and branch scope IDs belong only to the separately access-controlled context-read audit.
- `budget_usd`, `budget_remaining_usd`, and `budget_used_percent` appear for organization, team, and person scopes when a corresponding cap is configured.

Cost is an estimate based on the immutable version label and rates attached to the routed model when the event is written. A later configuration update does not rewrite historical events. The example label is illustrative; operators must issue a new version whenever any applicable model, cache, or output rate changes.

Provider prices, discounts, credits, batches, tool fees, rounding, and invoice adjustments can differ from a request-time estimate. Hormuz therefore does not call these event costs final invoiced spend. OpenAI exposes organization costs in daily aggregate buckets grouped by dimensions such as project and line item through its [Costs API](https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/usage/methods/costs), while Anthropic exposes organization usage and cost reports through its [Usage & Cost Admin API](https://platform.claude.com/docs/en/manage-claude/usage-cost-api). The local `hormuz billing` commands can fetch or import complete API response pages as immutable exact-decimal snapshots, report aggregate variance without calling it proof of bypass, and apply a versioned exact-decimal policy that classifies a result as clear or requiring finance review. Authenticated fetch evidence binds the fixed endpoint, exact query window/scope, and completed cursor chain; it does not prove final invoice completeness. See [BILLING_RECONCILIATION.md](BILLING_RECONCILIATION.md). Scheduled polling, secure hosted key custody, final invoice/credit ingestion, shared tenant storage, and a persistent authenticated finance-case workflow remain open in issue #8.

Those provider reports do not provide a universal per-Hormuz-request invoice cost. With a shared provider project, workspace, or key, a team/person chargeback can only be an explicitly labeled allocation. Exact provider-backed separation requires the organization to isolate provider projects, workspaces, or credentials at the desired accounting boundary. Hormuz will not silently label a proportional allocation as final cost.

## Concurrent budget enforcement

Before an accounted provider call, Hormuz creates an atomic SQLite reservation against every applicable organization, team, employee, organization-model, team-model, and employee-model token/cost cap. Model capacity is keyed by the exact resolved policy alias after fallback, so usage of one alias does not consume another alias's allowance and a fallback cannot evade the destination model's cap. Organization and team policies may also set per-employee limits for that alias without enumerating a separate actor policy.

The reservation conservatively uses the serialized request byte count as the input-token upper bound, the effective maximum-output policy allowance, and uncached configured token rates. A competing request is denied with `hormuz_budget_denied` when its projected reservation would exceed any scope. Generation endpoints also receive the enforced output cap. OpenAI compaction does not define an output-cap request field, so Hormuz uses the allowance only for local reservation and records actual provider usage afterward; actual compaction output can exceed that allowance and therefore cannot provide a hard per-request ceiling.

The reservation is released after the actual provider usage event is recorded. It also has a bounded expiry so an interrupted process cannot hold budget forever. Configurations with monthly token or spend limits must therefore give every identity an effective `max_output_tokens` policy.

This is intentionally conservative: a request may be rejected even though caching or a short answer would have made its eventual cost smaller. It closes the concurrent-request race for configured token charges inside the current single-node SQLite deployment, but it cannot coordinate reservations across replicas or reserve provider tool-call fees and invoice adjustments that are absent from the rate card. Shared atomic enforcement depends on the approved production persistence topology. Provider-only charges remain reconciliation items and are another reason not to describe an estimate as final invoiced spend.

Per-person reporting is trustworthy only when every human, service account, and CI workload has a unique Hormuz identity. Shared tokens collapse attribution. Because the event stores team metadata at request time, later team transfers do not rewrite historical allocation.

Token volume and spend measure consumption, not employee productivity or work quality. Do not use this report as a performance ranking. `status` covers only requests that passed through Hormuz. Billing reconciliation labels whether source evidence is offline-unverifiable or an authenticated completed query, but neither label makes the result a final invoice or employee-performance signal.
