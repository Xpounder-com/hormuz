# ADR 0003: Provider and Hormuz cache privacy tiers

> **Partially superseded by [ADR 0008](0008-gateway-product-boundary.md).** Provider-native cache governance remains accepted; a Hormuz-owned reusable context-pack cache is no longer planned.

- Status: **Accepted**
- Date proposed: 2026-08-15
- Date accepted: 2026-08-20
- Provider documentation last re-verified for the current implementation: 2026-08-21
- Decision owner: Product owner
- Tracking issue: [#3](https://github.com/Xpounder-com/hormuz/issues/3)
- Unblocks after acceptance: [#14](https://github.com/Xpounder-com/hormuz/issues/14), cache portions of [#5](https://github.com/Xpounder-com/hormuz/issues/5) and [#8](https://github.com/Xpounder-com/hormuz/issues/8)

## Decision requested

Approve the default relationship between data classification and three different mechanisms that are often all called “cache”:

1. **Provider prompt cache:** provider-operated reusable prompt-prefix computation.
2. **Hormuz context-pack cache:** customer-controlled reuse of an already authorized, source-linked context pack.
3. **Final-answer cache:** returning a previous model answer without fresh inference.

The product owner accepted the recommended provider-specific fail-closed
controls, encrypted customer-controlled context-pack cache boundary, and no
final-answer caching on 2026-08-20. Acceptance authorizes implementation but
does not activate caching before its storage, authorization, encryption, and
invalidation gates pass.

## Current implementation boundary

The supported Hormuz product no longer includes a Hormuz-owned context-pack
cache under ADR 0008. Issue #22 now implements both a bounded `allow`/`deny`
control for known client-requested directives and a strict `disabled` mode.
Strict mode requires a fresh, route-bound, content-free provider capability
record and an exact client-supplied opt-out; unsupported or stale routes deny
before egress. Hormuz does not inject, remove, or rewrite cache directives.
The implementation does not claim to infer arbitrary future provider fields or
replace a customer's provider data-controls agreement. See
[provider-native cache policy](../PROVIDER_CACHE_POLICY.md) for the executable
contract and its limits.

The historical context-pack design material below remains part of the decision
record but is superseded as product scope by ADR 0008; it is not an
implementation or release claim.

## Why the distinction matters

A provider prompt cache does not store or return a previous answer. It reuses an exact prompt-prefix representation and still generates a fresh response. A Hormuz pack cache reuses the result of authorization, retrieval, ranking, and rendering. A final-answer cache skips inference and has different correctness, freshness, and attribution risks.

Hormuz must never report these as one hit rate or one savings number.

## Current provider facts

Provider behavior is a versioned capability input, not a timeless assumption. Hormuz must re-verify it from primary documentation and customer contract settings before enabling a policy.

### OpenAI

The current [OpenAI prompt-caching guide](https://developers.openai.com/api/docs/guides/prompt-caching) states:

- eligible prompts are cached automatically by default, with exact-prefix reuse and provider-reported cached-token fields;
- GPT-5.6 and later support `prompt_cache_options.mode: "explicit"`; explicit mode with no breakpoints performs no prompt-cache reads or writes;
- earlier supported models cache automatically and do not expose explicit breakpoints;
- supported retention and write accounting vary by model family;
- prompt caches are isolated between OpenAI organizations and cannot be manually cleared.

The current [OpenAI data-controls guide](https://developers.openai.com/api/docs/guides/your-data) states that prompt caching may store encrypted key/value tensors in GPU-local storage and ties effective behavior to the organization's Zero Data Retention setting, model, endpoint, and retention mode. Requests without ZDR use extended prompt caching for supported models. Default abuse-monitoring logs can retain customer content for up to 30 days unless approved data controls apply.

Consequently, Hormuz cannot truthfully promise strict “no provider cache” for every OpenAI model. It can disable implicit caching on model families that expose explicit-only mode. For an older automatically cached model, a strict no-cache policy must deny or reroute the request unless current provider capabilities and the customer's data-control agreement establish the required guarantee.

### Anthropic

The current [Anthropic prompt-caching guide](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) states:

- caching is requested with `cache_control`, using a 5-minute default or a 1-hour extended TTL;
- 5-minute writes cost 1.25 times base input, 1-hour writes cost 2 times, and reads cost 0.1 times base input under the documented pricing multipliers;
- usage separates cache creation and cache read tokens;
- prompt caching is ZDR eligible; raw prompt/response text is not stored for the cache, while KV representations and hashes are held in memory rather than at rest;
- caches are isolated between organizations and, for the Claude API, between workspaces within an organization.

Hormuz can deny ordinary Anthropic prompt caching by rejecting `cache_control`
before forwarding; it does not silently remove the field. Provider tools and
thinking behavior can have additional cache semantics, so the capability catalog
must account for the actual endpoint/features in use rather than only the
top-level marker.

## Accepted decision

### Policy modes

Each tenant policy chooses a mode by provider, model family, client, team, and classification:

- `provider_cache_disabled`: require no provider prompt-cache write. If the provider/model cannot guarantee this, deny or reroute before egress.
- `provider_cache_ephemeral`: allow only a provider capability whose storage medium, maximum retention, ZDR/residency eligibility, and customer contract meet the policy.
- `provider_cache_standard`: accept the provider's currently documented default for that exact model/project and record the effective capability version.
- `provider_cache_extended`: explicitly request the longer supported provider TTL when the classification and contract permit it.
- `hormuz_pack_disabled`: do not persist rendered context packs.
- `hormuz_pack_customer_controlled`: persist encrypted, authorized packs in tenant-approved storage under the invalidation contract below.
- `hormuz_pack_dedicated`: use a tenant-dedicated storage and key boundary.

These are capability requirements, not parameter translations. The policy compiler maps them to a supported provider request or fails closed; it never silently downgrades a privacy requirement.

### Recommended classification defaults

| Classification | Provider prompt cache | Hormuz context-pack cache | Final-answer cache |
| --- | --- | --- | --- |
| `public` | Standard allowed | Customer-controlled allowed after #14 | Disabled |
| `internal` | Standard allowed | Customer-controlled allowed after #14 | Disabled |
| `confidential` | Disabled by default; explicit tenant opt-in only when capability and contract meet policy | Disabled by default; tenant opt-in with tenant key, bounded TTL, and audit | Disabled |
| `restricted` | Disabled; unsupported provider/model combinations deny or reroute | Disabled; dedicated customer-controlled mode requires separate tenant approval | Disabled |

“Allowed after #14” does not activate caching now. Until the storage, encryption, invalidation, deletion, and isolation tests exist, the effective Hormuz pack mode remains disabled for every classification.

Tenant administrators may make a stricter policy at organization, workspace, project, team, client, or actor scope. A less strict override requires an explicit permission and audit event and cannot exceed provider contract/data-residency constraints.

### Provider capability catalog

The target architecture maintains a signed/versioned capability record for every
supported provider/model/endpoint combination. The current #22 implementation
uses a deployment-configured, versioned catalog that is immutably carried in a
v5 policy projection; it records route protocol/model/operation, review date,
and source URLs, but does not itself attest the provider documentation or a
customer contract:

- whether caching is automatic, explicit, or technically disableable;
- cache storage medium and provider isolation boundary;
- minimum and maximum retention semantics;
- ZDR/MAM and residency compatibility;
- request parameters and incompatible features;
- cache read/write token fields and pricing-table version;
- documentation verification date and source URLs.

Startup and `hormuz doctor` reject policies whose required capability is unknown or stale beyond an administrator-defined review window. Provider documentation is evidence of product behavior, while the customer's executed contract and configured project are authoritative for that customer's retention eligibility.

### Egress order

The provider-bound request follows this order:

1. authenticate tenant/principal/client;
2. authorize model, budget, and context scope;
3. retrieve or reuse only an authorized Hormuz pack;
4. inject context;
5. apply DLP/secret redaction or denial to the complete payload;
6. apply the provider cache mode to the redacted payload;
7. reserve budget and send to the provider.

Unredacted content never becomes a provider or Hormuz cache entry. A redaction-policy version change invalidates affected Hormuz packs and provider cache keys.

### Hormuz context-pack cache key

The cache key is a keyed digest, not a readable concatenation. Its canonical input includes:

- tenant ID and authorized access-scope digest;
- principal clearance and relevant membership/authorization version;
- repository/project ID and immutable source revision;
- task/query fingerprint without raw secret values;
- selected source record IDs, revisions, and content hashes;
- context policy, DLP, retrieval, ranking, and renderer versions;
- model family/tokenizer where token budgeting depends on them;
- requested context budget and output schema version.

Actor/team scope is included when visibility or policy differs. No pack can be reused across tenants, principals with insufficient clearance, policy versions, or source revisions even if its text happens to be identical.

Hormuz does not currently add a `prompt_cache_key`. Any future feature that
does so would require a separate reviewed contract and must not use an employee
email, repository name, ticket title, or other raw identifier. Provider cache
reads/writes remain separately attributed to the request that incurred them.

### Storage, encryption, and invalidation

- Pack content is encrypted with authenticated envelope encryption under a tenant-scoped data key; the wrapping key is held by the approved KMS/customer key boundary.
- Metadata exposes no source content and is tenant scoped under the persistence ADR.
- Pack content has a bounded tenant-configured TTL and a shorter effective lifetime when any source expires.
- Invalidation occurs on source revision/content change, supersession, expiry, verification downgrade, policy/DLP/ranker/renderer change, membership or clearance change, tenant revocation, repository access change, or key rotation requiring re-encryption.
- Deleting a canonical source schedules deletion of every derived pack and proves completion across replicas. Legal hold applies to canonical evidence, not to derived pack cache entries.
- Cache lookup always re-authorizes before decryption or return. Possession of a pack ID is not authorization.
- Final model answers are not stored in this cache. A future answer cache requires a separate ADR and correctness evaluation.

### Measurement

Every provider response records, when exposed:

- uncached input, cache-write, cache-read, output, and reasoning tokens;
- provider, actual model, endpoint, provider project/workspace, and capability/rate-card version;
- estimated cost at request time and later reconciled invoice cost;
- requested/effective provider cache mode and policy decision;
- Hormuz pack hit/miss/invalidation reason, pack version, assembly cost, and latency without pack content.

Dashboards and exports report provider prefix-cache savings and Hormuz pack reuse separately. A “useful hit” is evaluated against cost per verified accepted task and quality guardrails; a raw hit rate is not a product outcome.

## Security and correctness invariants

- Unknown provider behavior cannot satisfy a restrictive policy.
- The strict disabled mode denies/reroutes when technical opt-out is unavailable; it never labels provider-default caching as disabled.
- Provider and Hormuz cache lookup occur only inside the authenticated tenant and access scope.
- Redaction happens before provider cache writes and before Hormuz persists a rendered pack.
- Context packs are derived artifacts; canonical source records and provenance remain the system of record.
- Stale, superseded, unauthorized, or over-classification context cannot be returned because authorization and freshness are rechecked on every reuse.
- Cache timing and accounting do not expose whether another team or actor accessed confidential material.
- Manual deletion, tenant deletion, key revocation, and emergency global cache disable have executable runbooks and tests.

## Alternatives considered

### Treat provider prompt caching and Hormuz pack caching as one feature

This obscures custody, retention, opt-out, pricing, and invalidation differences and makes privacy claims unverifiable. Rejected.

### Cache final model answers

This can return stale or task-inappropriate output and makes quality attribution ambiguous. Rejected for the proposed product; reconsider only in a separate ADR for narrowly deterministic workloads.

### Enable all provider caching for cost savings

This ignores classification, contract, residency, and model-specific opt-out boundaries. Rejected.

### Disable every cache for every customer

This is safest operationally but abandons a primary economic mechanism even for public/internal data and does not eliminate provider behavior that is automatic. Rejected as the universal default; strict disable remains a supported policy.

## Consequences

- Hormuz needs a maintained provider capability catalog and fail-closed request compiler, not just pass-through request parameters.
- Some OpenAI model/classification combinations will be denied or rerouted when strict no-cache guarantees cannot be established.
- Confidential and restricted workloads start conservatively; customers explicitly opt into reusable content within their approved storage and contract boundary.
- Pack reuse can reduce retrieval and input-token work without turning model answers into stale artifacts.
- Pricing and retention changes become compatibility events that require tests and capability updates.

## Verification required

Acceptance of this ADR does not prove implementation. Issues #14 and #8 require:

- live provider contract tests that confirm request parameters and usage fields for every supported model family without storing prompt/response fixtures in logs;
- deny/reroute tests for every unsupported privacy guarantee;
- two-tenant and two-scope negative cache tests;
- invalidation tests for every listed source, policy, identity, and lifecycle transition;
- encryption/key-rotation/deletion and restore tests;
- DLP tests proving injected context is inspected before any cache write;
- provider-invoice reconciliation that keeps estimated and final costs distinct;
- retrieval/accepted-task experiments proving savings without lower verified quality or higher stale-context incidents.

## Owner approval record

The product owner approved **A — conservative classification defaults and no
final-answer cache** on 2026-08-20. The canonical approval is recorded in
[issue #3](https://github.com/Xpounder-com/hormuz/issues/3#issuecomment-5355712148).

Acceptance authorizes the privacy-tier and invalidation contract. It does not
claim that provider capability enforcement, encrypted pack storage,
cross-tenant isolation, deletion, key rotation, or accepted-task economics are
implemented or verified.
