# ADR 0006: Automatic governed-context injection

> **Superseded by [ADR 0008](0008-gateway-product-boundary.md).** This behavior remains a deprecated experimental compatibility surface, not a supported Hormuz release gate.

- Status: **Superseded**
- Date proposed: 2026-08-16
- Date accepted: 2026-08-16
- Decision owner: Product owner
- Tracking issue: [#5](https://github.com/Xpounder-com/hormuz/issues/5)
- Unblocks after acceptance: automatic Context Pack injection for Codex and Claude Code under [#5](https://github.com/Xpounder-com/hormuz/issues/5)

## Decision requested

Choose where Hormuz should apply governed organizational context in employees' existing Codex and Claude Code requests:

1. **Gateway-side, user-priority injection — recommended.** After authentication and model/context authorization, Hormuz retrieves a bounded verified Context Pack and renders it as clearly delimited reference data at user priority. It then runs the complete provider-bound request through secret and DLP enforcement before budget reservation and egress.
2. **MCP retrieval only.** Keep `hormuz_get_context` available, but let the model or employee decide whether to call it.
3. **Client-side wrapper injection.** Add a Hormuz launcher or wrapper that retrieves and inserts context before starting each supported client.
4. **Gateway-side high-priority injection.** Put governed records in OpenAI `instructions` and Anthropic `system` content.

The product owner accepted option 1 without changes on 2026-08-16. Acceptance authorizes bounded gateway-side user-priority injection work under issue #5; it does not close the issue or accept the separate cache, hosted-persistence, KMS, HA, or enterprise-release decisions.

## Context

Hormuz already has an authenticated, authorization-first Context Pack API and an MCP adapter that Codex and Claude Code can call. That is useful but optional: a model can omit the call, call it too late, request too much, or use inconsistent scope. It therefore cannot satisfy an organization policy that requires the smallest authorized reusable context on every eligible run.

The gateway is the only existing boundary that sees both the authenticated Hormuz principal and the final provider request. It already applies model policy, DLP, budget reservation, usage accounting, and provider routing there.

Provider compatibility constrains where injected data can go:

- OpenAI Responses separates top-level `instructions` from `input`, and describes system or developer guidance as top-level instructions or compatible message items. Organizational records can contain untrusted source text, so Hormuz must not promote them into that instruction channel. See OpenAI's [Responses migration guide](https://developers.openai.com/api/docs/guides/migrate-to-responses).
- Claude Code prepends a positional attribution block to the Anthropic `system` array. Anthropic says a gateway must preserve that array and first block exactly; prepending, reordering, or merging system content can defeat attribution stripping and affect prompt caching. See Anthropic's [gateway protocol reference](https://code.claude.com/docs/en/llm-gateway-protocol#system-prompt-attribution-block).
- Codex custom model providers support static and environment-backed HTTP headers, and Claude Code sends configured custom headers to a gateway. Those headers can carry a project selector, but a caller-supplied selector is not authorization. See OpenAI's [Codex configuration reference](https://developers.openai.com/codex/config-reference) and Anthropic's [gateway protocol reference](https://code.claude.com/docs/en/llm-gateway-protocol#request-headers).

## Proposed decision

### Injection location and order

For eligible generation and token-count requests, Hormuz uses this order:

1. authenticate the Hormuz principal and derive organization, team, actor, clearance, and application from server-controlled identity and endpoint state;
2. authorize the requested model and resolve the routed provider model;
3. resolve the effective context-injection policy;
4. derive a bounded retrieval query and optional scope selectors from the request without storing prompt content;
5. authorize context scope and build a verified, fresh Context Pack;
6. commit the metadata-only context-read event;
7. render the pack into a provider-specific user-priority reference block;
8. inspect the complete mutated request, including injected context and consumed scope headers, with secret and DLP policy;
9. apply provider-storage and later approved cache policy;
10. reserve the complete request's budget and forward it; and
11. record usage plus content-free pack lineage.

No context lookup occurs before authentication or model authorization. No rendered context reaches a provider unless its read audit commits and the complete payload passes DLP.

### Policy modes and monotonic overlays

The effective injection mode is one of:

- `off`: do not retrieve or inject context;
- `optional`: inject when an authorized useful pack can be produced, otherwise continue with an explicit metadata-only reason; or
- `required`: deny the provider request unless an authorized pack satisfying policy can be produced and injected.

The organization policy defines the maximum permission and minimum requirement. Team and actor overlays may narrow repositories, classifications, models, applications, budgets, and item limits or strengthen `optional` to `required`; they cannot turn an organization-required policy off or widen authorization.

Automatic injection starts disabled in example and migrated configurations. Enabling it is an explicit administrator action. Provisional records remain excluded unless the already-authorized context policy explicitly permits them; the recommended production default is verified-only.

### Query derivation and privacy

Hormuz derives retrieval terms only from bounded current user-authored text in the provider request. It ignores system/developer instructions, assistant output, reasoning, tool definitions, binary/media values, and provider-generated identifiers. Tool results continue through DLP but are not silently treated as the employee's retrieval intent in the first implementation.

The raw query is used in memory for deterministic retrieval and is not written to the usage ledger, context read audit, ordinary logs, metrics, errors, or approval records. If no eligible user text exists and the request is not an authenticated continuation of a previously injected turn:

- `optional` records `not_injected:no_eligible_query` and continues; and
- `required` denies before provider work.

Query extraction is versioned and bounded. A future semantic query generator or model-written summary is a separate evaluated feature; the first path does not spend model tokens to decide what context to retrieve.

### Conversation continuation without prompt retention

Agent clients issue tool-only continuation requests that may contain no new user text. Denying every such turn would break normal Codex and Claude Code workflows; treating tool output as a new query would let untrusted tool content steer retrieval.

Hormuz therefore retains a short-lived, content-free continuation binding after a successfully injected generation. The binding contains only a keyed digest of the provider/client conversation identifier, the authenticated organization/actor/client/protocol binding, pack and selected record/version lineage, trusted repository revision, policy/retrieval/render versions, and expiry. It contains no raw conversation/session/response ID, prompt, response, tool result, rendered pack, or provider credential.

On a tool-only continuation, Hormuz may reuse that lineage only after the incoming identifier maps to the same authenticated principal and client and every selected record is re-authorized and revalidated. A missing, expired, mismatched, stale, contradictory, or revoked binding follows the effective `optional` or `required` failure policy. A caller-supplied pack ID or session ID alone never proves authorization.

Where the client resends eligible user text, Hormuz performs ordinary fresh retrieval rather than trusting the continuation binding. Binding TTL and deletion are explicit policy, with a short default bounded by the human session and provider conversation. Multi-node continuation storage remains subject to the accepted enterprise persistence design; a node-local prototype cannot claim HA continuity.

### Authorization and repository selection

Organization, team, actor, clearance, and application never come from request headers or prompt content. They remain derived from the authenticated Hormuz identity and compatibility endpoint.

The first implementation can automatically select organization-, team-, and actor-visible records without a repository selector. Repository-specific records require both:

1. an administrator-authorized repository grant on the principal or effective policy; and
2. an exact repository selector supplied through the supported client configuration.

Optional branch and revision selectors narrow the granted repository. They never grant access, increase clearance, or override a trusted lifecycle snapshot. An absent, malformed, ungranted, or ambiguous selector excludes repository-scoped records in `optional` mode and denies in `required` mode when repository context is required.

Hormuz consumes its own scope headers and never forwards them to OpenAI or Anthropic. Raw repository, branch, path, or revision selectors are not retained in the usage ledger; content-free authorized scope IDs and trusted lifecycle revision may be recorded according to audit policy.

### Provider rendering

The canonical pack remains provider-neutral. Each provider renderer emits the same selected record IDs, content, provenance, verification state, contradiction state, and policy/retrieval/render versions under an equivalent bounded schema.

Injected text is reference data, not a policy or instruction channel. It includes an explicit boundary stating that the contents are untrusted organizational sources, may not override higher-priority instructions, and must be treated as potentially conflicting evidence. The renderer uses deterministic escaping and delimiters that source content cannot terminate.

- For OpenAI Responses, the renderer adds user-priority input content and leaves top-level `instructions`, system/developer messages, prior response state, tool-call linkage, and provider-specific fields unchanged.
- For Anthropic Messages, the renderer adds user-priority content while preserving the existing `system` value and Claude Code attribution block byte-for-byte and position-for-position.

If the request shape cannot be mutated without changing provider semantics, `optional` skips with a stable reason and `required` denies. Hormuz does not guess at unknown content block types. The original client request is never persisted merely because it was mutated.

### Repetition, conversation state, and caching

Injection is evaluated per provider request because authorization, lifecycle state, and DLP policy can change between turns. The renderer must avoid duplicating an already-present Hormuz block within one outbound request.

Conversation-state behavior is part of provider compatibility testing. When provider-managed previous-response state already carries the authorized pack, Hormuz does not append duplicate content; it revalidates the content-free continuation binding and records a lineage reuse. When the client resends a transcript that does not contain the gateway mutation, Hormuz emits one fresh block according to the versioned renderer contract. It must not infer either case solely from an employee-supplied pack ID.

This ADR does not enable a Hormuz content cache or claim provider prompt-cache savings. Reusable pack caching remains blocked on [ADR 0003](0003-cache-privacy-tiers.md). The injection implementation records whether a pack was freshly assembled or reused only when that fact is authoritative.

### Failure behavior

Failure behavior is policy, not an exception-handler accident:

| Condition | `optional` | `required` |
| --- | --- | --- |
| No relevant authorized records | Continue; record `empty_pack` | Deny if policy requires a non-empty pack |
| Context store unavailable or read audit cannot commit | Deny | Deny |
| Stale, contradictory, quarantined, or over-budget records leave no safe pack | Continue only when policy permits an empty result; record exact safe reason | Deny |
| Unsupported provider request shape | Continue; record `unsupported_shape` | Deny |
| Injected payload triggers secret/DLP denial or approval | Apply the existing DLP result; never drop context to bypass it | Same |
| Usage/lineage evidence cannot commit | Deny before provider work | Deny before provider work |

Even `optional` fails closed for storage failures that make authorization, freshness, read audit, or usage lineage unverifiable. It may continue only for an authoritative, policy-allowed empty or unsupported result.

### Usage lineage and privacy

Every accounted generation records, without prompt or response content:

- injection mode and outcome;
- pack ID or opaque lineage reference;
- selected record IDs and count;
- fresh-retrieval or authorized-continuation lineage, without a raw client/provider conversation identifier;
- context policy, retrieval, lifecycle, and renderer versions;
- trusted repository revision when repository context was selected;
- estimated injected tokens and context assembly latency;
- fresh/reused status only when authoritative; and
- a bounded skip or denial reason.

The event-time organization, team, actor, application, requested/routed/actual model, provider, request outcome, token categories, and cost remain the existing authoritative dimensions. Pack lineage is operational and quality evidence; it is not an employee-performance score.

## Alternatives considered

### MCP retrieval only

This preserves the current provider request unchanged and gives the model control over when to retrieve. It cannot enforce an organization requirement, reliably cap context before retrieval, or prove that a run used the intended pack. Keep MCP as an explicit tool path, not the only governed-context mechanism.

### Client-side wrapper injection

A wrapper can know the local working directory, but it creates a new client-specific trust and upgrade boundary, can be bypassed by starting Codex or Claude Code directly, and may handle prompt content before gateway DLP. Reject as the primary enforcement point. A future small scope helper may supply a selector, but the gateway remains the authorization and injection boundary.

### High-priority system or instruction injection

This makes context prominent but promotes untrusted records into an instruction channel. It also conflicts with Anthropic's documented positional system-block behavior. Rejected.

### Infer repository authorization from prompt text or working-directory strings

Prompt text and caller-controlled paths are selectors at most. Treating either as authorization creates cross-repository leakage and repository-name-confusion risk. Rejected.

## Consequences if accepted

- Hormuz can enforce reusable organizational context without replacing Codex or Claude Code.
- Context policy becomes part of the provider request path and therefore part of availability, budget, DLP, usage, and official-client compatibility gates.
- The first useful slice can inject verified organization/team/actor standards without waiting for a source connector or pack cache.
- Repository-specific injection requires explicit repository grants and client scope configuration; automatic trusted local-repository discovery is not claimed.
- Provider request schemas remain open for pass-through compatibility, while mutation is limited to versioned, tested user-priority shapes.
- Issue #5 remains open until both providers, scope policies, lineage, failure modes, and release evidence satisfy its acceptance criteria.

## Verification required after acceptance

- red-first unit tests for policy overlay monotonicity, query extraction, selector authorization, deterministic rendering, delimiter escaping, continuation binding, and no duplicate block;
- OpenAI Responses tests for string and item-array input, instructions preservation, tool calls/results, previous-response state, streaming, compaction boundaries, unknown item types, and provider errors;
- Anthropic Messages tests for string/array system content, exact first-block preservation, user/tool-result messages, token counting, streaming, beta fields, unknown blocks, and provider errors;
- cross-organization, cross-team, actor, clearance, repository, branch, revision, model, and client denial tests before repository reads;
- empty, stale, contradictory, quarantined, over-budget, unsupported-shape, context-store outage, read-audit failure, usage-store failure, and DLP denial/approval tests for both policy modes;
- source, database, logs, audit, metrics, errors, wheel, and runtime scans proving queries, prompt/response content, secrets, raw repository selectors, and injected content do not enter metadata surfaces;
- request accounting tests proving injected bytes/tokens are included in reservation and provider-reported usage without inventing token counts;
- installed pinned and current-supported Codex and Claude Code compatibility tests, including conversation/tool turns and client upgrades;
- strict retrieval benchmark plus end-to-end tasks comparing no context, MCP-only context, and automatic injection for cost per verified accepted task and stale-context guardrails; and
- source/wheel build-install checks and GitHub CI evidence under the universal definition of done.

## Owner approval record

Accepted option 1 without changes on 2026-08-16. The product owner approved the recommended direction in the Codex task, and the decision plus unchanged boundaries were recorded in [GitHub issue #5](https://github.com/Xpounder-com/hormuz/issues/5#issuecomment-5307621457). Automatic injection remains disabled by default in example and migrated configurations; verified-only remains the production default; repository selectors narrow administrator-authorized grants and never grant access; injected records remain untrusted user-priority reference data; and optional/required failures plus content-free lineage follow this ADR.
