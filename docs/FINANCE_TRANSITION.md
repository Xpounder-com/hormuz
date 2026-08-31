# v1.1.0 finance reconciliation checkpoint

Historical preflight: the later [durable rate-card slice](FINANCE_RATE_CARDS.md)
uses the reviewed [v2 successor](finance-transition-plan-v2.json) for schema 8/12.
The v1 plan below remains frozen; it is not a claim that full finance is implemented.

This is the pre-implementation compatibility checkpoint for #8 under #214.
It does not implement finance reconciliation, grant provider access, accept a
feature, or create a release. The [transition plan](finance-transition-plan-v1.json)
and [provider source contract](finance-source-contract-v1.json) are separately
versioned, digest-bound documents. A changed decision requires a reviewed
successor, not an edit to an accepted historical plan.

The work starts from protected main
`c6d9558add40df71cc9ffbbaaf07d280e70e821c`. The actual accepted outcome
predecessor is PR #240: reviewed source
`aa648edf64df9f4a0c426ad73a95852f11561099`, merged main
`fefd3ebc27e4340a169588dc190e11f874d8e949`, exact-main CI `33397923194`.
The intervening Kubernetes change does not alter the runtime or schema.

## Affected operators and compatibility

| Boundary | Outcome implementation at preflight | Finance target at preflight |
| --- | --- | --- |
| SQLite schema | 7 | 8, not implemented here |
| PostgreSQL schema | 11 | 12, not implemented here |
| Existing usage API, CLI and cost/coverage enums | Frozen v1 shapes | Unchanged |
| Registry, attribution and outcome wire | Accepted versioned shapes | Unchanged |
| Finance records and operator outputs | Not implemented | Separate schema identities at v1 |
| HTTP routes and role grants | Current inventory | No additions from this checkpoint |

Only the finance migration may use these next versions after this checkpoint
is reviewed and accepted on green protected main. If another feature consumes
either number, stop and review a successor plan. Do not renumber applied
migrations or treat a checkpoint for one feature as authority for another.

The existing usage report still has `cost_basis: configured_rate_card_estimate`,
`allocation_basis: direct_gateway_request`, and
`coverage: gateway_captured_requests_only`. No existing client needs a field,
route, authentication, error, configuration, pagination or retry change.
No old usage/audit/attempt row is rewritten, repriced, or enriched with guessed
native usage, finance or work context. Old model-rate provenance that cannot
be recovered exactly remains unavailable.

The implementation will add a separate finance repository through the accepted
persistence composition seam. It may borrow the existing pool and tenant
transaction mechanics but must not add finance responsibilities to the v1
usage facade. Provider calls happen outside database transactions. A successful
import commits its normalized observations, provenance, receipt, idempotency
result and safe audit together. Unknown request reservations remain held;
finance collection never replays model work.

Planned local operator commands are `finance rate-card`, `finance import`,
`finance fetch` and `finance reconcile`. They are not registered by this
checkpoint. Their strict outputs will use the distinct schema families named
in the plan, not extra fields in an existing v1 response. Before file reads,
network access or database access, require explicit tenant-bound operator
authority or the authenticated configured principal. A body organization or
provider account claim is not a credential. No role gains raw peer records;
#223 still owns role-scoped aggregate views.

## Provider source and accounting contract

The supported target is **first-party API accounting**, not every product sold
by either provider. The source contract records official documentation reviewed
on August 31, 2026, fixed HTTPS endpoints, grouping, units, metadata allowlists
and unsupported sources.

OpenAI's organization [usage endpoint](https://developers.openai.com/api/reference/python/resources/admin/subresources/organization/subresources/usage/methods/completions)
includes cached and cache-write tokens in total input. Its
[Costs endpoint](https://developers.openai.com/api/reference/python/resources/admin/subresources/organization/subresources/usage/methods/costs)
supports daily buckets and project, line-item and API-key grouping. Its numeric
amount is in the stated currency's major units. Parse JSON decimals without a
binary-float round trip. Use only explicitly approved credentials with the
needed administration capability; model access does not establish billing
access. The official [usage/cost example](https://developers.openai.com/cookbook/examples/completions_usage_api)
uses an Admin key.

Anthropic's [Messages Usage Report](https://platform.claude.com/docs/en/api/admin/usage_report/retrieve_messages)
separates uncached input, cache reads, five-minute and one-hour cache creation,
and output. Keep those categories; do not import OpenAI's input convention.
The [Cost Report](https://platform.claude.com/docs/en/api/admin/cost_report/retrieve)
provides structured workspace, model, token-type and tier metadata when
available. Its USD amounts are fractional cents, so divide by 100 exactly.
The [Usage and Cost guide](https://platform.claude.com/docs/en/manage-claude/usage-cost-api)
states that workspace keys cannot access these Admin endpoints and Priority
Tier costs are excluded. Claude Enterprise Analytics and reseller/cloud
billing are separate sources, not silently covered by this integration.

Keep these facts distinct:

- Immutable gateway request-time estimates and their exact rate-card identity.
- Provider-reported aggregate observations, with their real period and scope.
- Authoritative finalized invoice facts at the scope actually documented.
- Explicit credit/discount or other adjustments, preserving signs and source.
- Any separately approved allocation, labeled `allocated_estimate` with method,
  version, weights and rounding. This plan authorizes no new allocation method.
- Missing/unsupported cost and unresolved reconciliation variance.

An API report is not automatically a finalized invoice. Do not infer credit,
discount, model or employee authority by parsing a description. Unknown
line-item/description text may receive a tenant-keyed fingerprint, but cannot
be retained as arbitrary free text. Preserve structured allowlisted metadata.
A signed provider row whose meaning is unknown stays an unclassified
adjustment; it cannot be forced into the frozen public cost shape as a negative
`provider_final` fact.

Native usage and normalized usage need separate fields and explicit known or
unknown states. OpenAI cache subcategories and reasoning inside output cannot
be added again to the total. Anthropic cache lifetime categories must survive
normalization. An unreturned reasoning or request-count field is not zero.
New per-request capture must use a separately versioned sidecar linked to the
exact immutable attempt; do not reinterpret legacy `input_tokens` or invent a
historical native payload. Capture gaps remain visible until proven complete.

Rate cards are immutable versioned facts with tenant, provider, actual model,
currency, effective interval, category/tier rules and content digest. A future
rate-card change can create a new estimate, never alter a past snapshot. Cover
at least two versions and both providers' cache conventions in executable
fixtures. Amounts use bounded finite decimal strings; implicit currency
conversion, silent rounding and non-finite values are forbidden.

## Coverage, review and failure behavior

Reconciliation pins the provider snapshot, original gateway facts, source
binding, rate-card and review-policy versions. Overlapping refreshes are
explicit successors, never additive totals. Provider pagination is not a
transactionally frozen provider dataset; record the collection window and do
not overstate point-in-time consistency.

Expose attributed, unattributed, unpriced, unsupported and unbound traffic
separately. Known bypass requires independent positive evidence; unexplained
variance alone does not prove bypass. Report unsupported or unknown finance
granularity for team, actor, application or model instead of manufacturing a
final cost. Versioned absolute/relative thresholds must explicitly handle a
zero denominator, missing amounts, currency differences and incomplete scope.
Review decisions append; they never delete the original exception or amount.
Actor-level operational cost coverage is not employee performance evidence.

A source binding records explicit operator account attestation separately
from authenticated transport. A successful response does not itself prove
that a provider account belongs to the asserted Hormuz organization. Neither
an imported file's fields nor synthetic fixtures may set a live-verification
flag. Customer authorization, actual successful read/import, and adequate scope
provenance are necessary before a live finance-verified label or #225 evidence.
A credential permission probe is not that finance proof.

The plan fixes a 1 MiB page, 16 MiB collection, 32 pages, 4,096 records and
31-day maximum window. Parsing bounds are depth 16 and 65,536 members/page.
There are at most four concurrent collections/process, a ten-second request
timeout and a sixty-second total collection deadline. At most two retries per
page are allowed for transient transport, 429 and 5xx failures, with at most
five seconds of delay and no wait beyond the global deadline. Do not retry
401/403 or follow redirects. A provider cursor may supply only the bounded
cursor value, never a host/path or unapproved query. An oversized Retry-After
ends the attempt with a retryable gap instead of sleeping unboundedly.

Reject malformed/incomplete page chains, repeated cursors, duplicate JSON
members, conflicting duplicate records, out-of-window buckets, non-finite
numbers and inconsistent currency metadata. If the collection fails, retain
the prior complete snapshot and report staleness/failure; never commit a
partial success or substitute zero. Import retries require the same tenant
and idempotency identity. Same normalized content returns the original
receipt even if another replica committed it; conflicting content fails.

Database statements have a five-second budget. Credentials are supplied by an
explicit operator-selected environment reference, not a plaintext CLI value.
No secret persistence, provisioning, rotation, scheduler or new secret manager
is activated here. Rows, output, errors and logs exclude keys, raw provider
payloads, request/response work content and free-form exception messages.
Retention appends lifecycle events and preserves linked financial/audit facts;
customer backup/export policy remains authoritative.

## Migration and recovery procedure

1. Stop all writers and pools; verify the application/database checkpoint and
   take a consistent backup. Serialize migrations. Existing PostgreSQL
   readiness is not a continuous schema-version monitor.
2. Apply the reviewed additive migration, validate schema and unchanged old
   facts, and start fresh processes. A failed DDL/ledger update rolls back as
   one unit; a retry must preserve original receipts and uncertain holds.
3. If no post-checkpoint writes were accepted and the evidence is known,
   restore the verified old application/database pair **into a separate
   destination**. Retain the candidate state. Never just run an old binary on
   the new schema.
4. If accepted writes are nonzero or unknown, preserve the new state and
   recover forward. Never decrement ledgers, drop finance facts to downgrade,
   release uncertain reservations or automatically replay provider work.

These are the same operational limits as the earlier feature checkpoints,
with the actual accepted outcome application now the immediate predecessor.
Immutable v1.0.0 remains a second refusal/compatibility baseline.

## Reproducible preflight proof

Create the exact predecessor, not an archive of the current worktree:

```bash
git -c tar.umask=0000 archive --format=tar --prefix=hormuz-outcome-baseline/ \
  --output=/path/to/outcome-baseline-aa648ed.tar \
  aa648edf64df9f4a0c426ad73a95852f11561099
```

Required SHA-256:
`fbbe1178607a38c5f390b96c646f5a93414523b37f553abb7d830d42f30ff056`.
Install that archive with `[postgres]` in a separate virtual environment and
set `HORMUZ_TEST_OUTCOME_PYTHON` to its interpreter. The isolated driver checks
the distribution source digest, all 105 runtime files, schema 7/11, and loads
fixtures from the same verified archive bytes. Changed, extra or missing
runtime files fail verification. This is an accepted development checkpoint,
not a published v1.1.0 artifact.

Supply the earlier `HORMUZ_TEST_V1_PYTHON`, `HORMUZ_TEST_REGISTRY_PYTHON` and
`HORMUZ_TEST_ATTRIBUTION_PYTHON` from the
[registry](REGISTRY_TRANSITION.md), [attribution](ATTRIBUTION_TRANSITION.md) and
[outcome](OUTCOME_TRANSITION.md) guides. Set only an explicitly disposable
`HORMUZ_TEST_POSTGRES_DSN` and its matching `HORMUZ_TEST_PG_CONTAINER` for the
owned-schema/dump/restore tests. Missing opt-in environments are local skips,
not evidence; CI supplies all predecessors and both adapters.

```bash
python tools/verify_finance_transition_plan.py \
  --baseline-archive /path/to/hormuz-1.0.0.tar.gz \
  --baseline-manifest /path/to/hormuz-v1.0.0-candidate-manifest.json \
  --outcome-archive /path/to/outcome-baseline-aa648ed.tar
python -m unittest -v tests.test_finance_transition_plan \
  tests.test_sqlite_finance_transition tests.test_postgres_finance_transition
```

Six cases per adapter prove the missing finance migration is still red, a
test-only DDL failure rolls back, retry is idempotent, partial/newer schemas
refuse without repairs, old-pair restore preserves all three domains' replays
and frozen cursors, and post-checkpoint writes survive forward restore.
The predecessor has 29 SQLite and 51 PostgreSQL tables, including populated
registry, attribution and all nine outcome tables. Original ledger rows,
actual-model facts, ACL/RLS shape and unknown reservations remain unchanged.

The probe is **not finance implementation**. Empty policy/custody fixture
tables are not populated-domain recovery proof. Implementation must replace
the probes with real migrations and add every runtime/security/source test
listed in the plan, including all populated finance tables. The package gate
requires this complete source kit; CI also runs transitions against a clean
installed wheel outside the checkout.

Only technical review, normal protected merge, and all required checks on the
exact merged main can accept this checkpoint in #214 and unblock #8 storage
work. Keep #8 open until its implementation and own evidence pass. #214's final
source/wheel/signed-OCI/Compose transition and populated recovery, #225's real
external pilot, and #226's release remain independent gates. No release/tag,
customer deployment or new external data collection is authorized here.
