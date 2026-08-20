# Automatic governed-context injection

> **Deprecated experimental compatibility behavior.** It remains disabled by default and is not a Hormuz enterprise release gate. See [ADR 0008](decisions/0008-gateway-product-boundary.md).

Hormuz can automatically add a bounded, verified Context Pack to ordinary Codex and Claude Code generation requests, direct-query OpenAI compaction, and Anthropic token counting. Employees keep using the official clients. The gateway performs retrieval and provider-specific request mutation after identity and model authorization, so a model does not need to call the MCP tool and the employee does not need a wrapper.

Automatic injection is disabled by default. The first implementation is a bounded checkpoint under [issue #5](https://github.com/Xpounder-com/hormuz/issues/5), not closure of the complete automatic-context milestone.

## Enable the first slice

Configure the organization policy, then optionally narrow or strengthen it at team and actor scope:

```json
{
  "policies": {
    "organization": {
      "context_injection": {
        "mode": "optional",
        "allowed_clients": ["codex", "claude-code"],
        "allowed_models": ["gpt-5.4-mini", "claude-sonnet-5"],
        "allowed_repositories": ["Xpounder-com/hormuz"],
        "max_classification": "internal",
        "token_budget": 1200,
        "max_items": 4
      }
    }
  }
}
```

Every model name is a Hormuz policy alias and must exist in `model_routes`. The mode is:

- `off`: do not retrieve or inject;
- `optional`: inject when a safe authorized pack is available, otherwise continue with a content-free reason; or
- `required`: deny an eligible generation request when no safe authorized pack can be injected.

Organization, team, and actor overlays are monotonic. Allowed client, model, and repository sets intersect; classification, token, and item caps take the narrower value; an organization `off` cannot be enabled by a lower scope; and an organization `required` cannot be weakened. The organization repository grant defaults to an empty set, so a lower scope cannot invent a grant that the organization omitted. The sample and migrated configuration remain `off` until an administrator explicitly opts in.

Import or promote verified organization-, team-, or actor-visible records as described in [CONTEXT.md](CONTEXT.md). This first slice always sets `include_provisional=false`, even if an explicit Context Pack tool call is separately allowed to inspect provisional data.

## Request path

For supported requests, Hormuz applies this order:

1. authenticate the employee or workload and derive organization, team, actor, clearance, and client;
2. authorize the requested model and resolve the routed model;
3. resolve the effective injection policy;
4. extract up to 4,096 characters of direct text from the latest user-authored input and consume any exact Hormuz repository selectors, in memory only;
5. authorize the selector against the effective repository grant, apply the narrower classification cap, validate an optional revision against the exact trusted repository/branch snapshot, and build a verified-only Context Pack;
6. commit the metadata-only context-read event;
7. render the same canonical pack as delimited, untrusted user-priority reference data;
8. run the complete mutated request and authorized consumed scope-header values through secret and DLP inspection;
9. apply provider storage policy and, for accounted generation or compaction, reserve budget against the larger serialized request;
10. replace the Hormuz credential with the company provider credential and forward; and
11. for accounted generation or compaction, record provider usage plus content-free pack lineage.

OpenAI injection changes `input` and leaves top-level `instructions` unchanged. Anthropic injection changes the latest user `messages` content and leaves `system`, including Claude Code's attribution content, unchanged. The deterministic marker and JSON payload carry the pack, record, provenance, verification, policy, retrieval, render, and repository-revision fields. The notice says that records are untrusted reference data and cannot override system, developer, policy, or user instructions.

The same OpenAI renderer is used for `POST /v1/responses/compact` only when the compact request contains direct current-user input. Hormuz leaves `instructions` and provider-specific state fields unchanged. The current [OpenAI compact-response contract](https://developers.openai.com/api/reference/resources/responses/methods/compact) accepts `input` but does not define `max_output_tokens`, so Hormuz never adds the generation-only cap field to compaction. It uses the effective output-token policy cap only as a conservative local budget-reservation allowance and records the provider's actual usage afterward. Because OpenAI does not expose an output-cap field for this operation, Hormuz cannot promise a hard per-request compaction-output ceiling; organizations requiring that guarantee must deny this operation until a provider-enforceable boundary exists. Likewise, Anthropic token-count requests never receive the generation-only `max_tokens` field.

The original request is copied before rendering. An identical Hormuz block is not added twice to the same outbound request.

## Repository scope

Repository-specific records require two independent facts: the effective administrator policy must contain the exact `allowed_repositories` grant, and the supported client configuration must send the same exact `X-Hormuz-Repository` selector. `X-Hormuz-Branch` may narrow that repository. `X-Hormuz-Revision` may narrow it further only when a branch is present and the value equals Hormuz's trusted lifecycle snapshot for that repository and branch.

Generate a project-scoped client configuration with:

```bash
hormuz --config /etc/hormuz/hormuz.json client-config codex \
  --url https://hormuz.example.com \
  --repository Xpounder-com/hormuz \
  --branch main \
  --revision abc123

hormuz --config /etc/hormuz/hormuz.json client-config claude \
  --url https://hormuz.example.com \
  --repository Xpounder-com/hormuz \
  --branch main \
  --revision abc123
```

The Codex output uses its documented custom-provider `http_headers`. The Claude Code output uses its documented `ANTHROPIC_CUSTOM_HEADERS`. Endpoint management can install those settings for the relevant project profile, so employees keep using the ordinary clients. The selector is not identity or authorization: an absent selector selects only non-repository records, and an ungranted, malformed, branchless revision, or duplicate selector never exposes repository records. Optional mode may continue with authorized non-repository context; required mode denies a supplied invalid or ungranted repository scope before a context read or provider call.

Hormuz consumes all three headers. They are never sent to OpenAI or Anthropic. Authorized repository and branch scope IDs may appear in the separate metadata-only context-read audit; raw invalid or ungranted selectors do not. The usage ledger stores neither repository nor branch selector and may store only the trusted lifecycle revision. Scope values are inspected by DLP and, when an approval applies, are bound into the keyed request fingerprint without being stored.

## Query and failure behavior

Only direct latest-user text is retrieval intent. Hormuz ignores system/developer instructions, assistant messages, tool results, images, and other binary or provider-generated content. It does not call a model to create the query.

| Condition | `optional` | `required` |
| --- | --- | --- |
| No direct user query | Continue; record `no_eligible_query` | Deny before provider egress |
| No safe relevant records | Continue; record `empty_pack` | Deny before provider egress |
| Ungranted, malformed, or duplicate repository selector | Exclude repository records; use only safe unscoped context | Deny before repository read or provider egress |
| Missing or mismatched trusted revision | Exclude repository records; use only safe unscoped context | Deny before provider egress |
| Unsupported mutable request shape | Continue with a bounded reason | Deny before provider egress |
| Context repository or read-audit failure | Deny with `hormuz_context_unavailable` | Deny with `hormuz_context_unavailable` |
| Injected content triggers DLP | Apply redact, approval, or deny normally | Apply redact, approval, or deny normally |

Storage and read-audit failures fail closed even in `optional` mode because authorization and lineage are then unverifiable. A required-context denial uses `hormuz_context_required` in the OpenAI-compatible error and the existing Anthropic-compatible `permission_error` envelope.

DLP runs on the rendered request. It can therefore redact a secret found inside an otherwise authorized context record before egress. The usage lineage identifies the selected source pack; it is not a byte-for-byte attestation of the post-DLP provider payload.

## Metadata and privacy

For accounted generation and OpenAI compaction, the usage event stores injection mode, outcome and reason; pack and selected record IDs; policy, retrieval and render versions; repository revision when present; estimated rendered tokens; assembly time; and authoritative fresh/already-present status. Usage exports use schema version 2 for these additive fields. Reports aggregate injected requests, required denials, estimated context tokens, and distinct packs used. Anthropic token-count calls do not create inference-usage rows or consume Hormuz generation budgets. They do commit the same content-free context-read audit, and any DLP finding produces the ordinary metadata-only security evidence.

The raw retrieval query, prompt, response, rendered context, record content, title, source URI, source hash, and provider credential are not written to the usage ledger or ordinary logs. The separate context read audit remains content-free and intentionally omits the query and selected record IDs.

Treat record IDs, pack IDs, actor/team attribution, and cost metadata as access-controlled operational data. They are not employee-performance scores.

## Current boundary

This checkpoint deliberately supports only:

- OpenAI `POST /v1/responses` and Anthropic `POST /v1/messages` generation requests;
- OpenAI `POST /v1/responses/compact` requests containing direct current-user text, with provider-reported compaction usage accounted like other billable OpenAI work;
- Anthropic `POST /v1/messages/count_tokens`, using the same provider-bound context mutation and post-mutation DLP as generation so the estimate covers the request that Hormuz would send;
- direct current-user query extraction;
- verified organization-, team-, and actor-visible records, plus exact administrator-granted repository records selected by repository and optional branch/trusted revision; and
- fresh deterministic lexical pack assembly on every eligible request.

It does not yet bind previous-response-only, compaction-only, or tool-only continuations to earlier lineage, infer repository scope from a working directory, cache context packs, claim provider prompt-cache savings, or prove lower cost per verified accepted task. A compaction or token-count request with no direct current-user text therefore follows the same optional/required query behavior above; it cannot inherit an earlier turn until the continuation-binding decision is approved and implemented. A repository revision is caller-supplied narrowing checked against trusted lifecycle state, not a source of truth. Local SQLite and the plaintext context codec remain single-node prototype boundaries; hosted tenancy, KMS, HA, retention, and immutable audit are separate decisions and release gates.

Pinned installed-client tests prove that ordinary Codex and Claude Code generation requests carry the configured exact scope headers through Hormuz and arrive at provider-compatible fake upstreams with the authorized repository block present but none of the Hormuz headers. Deterministic gateway integration tests separately prove cross-repository/branch/classification exclusion, trusted-revision mismatch behavior, duplicate and ungranted denial, consumed-header DLP, OpenAI direct-query compaction injection and accounting, Anthropic token-count parity, required-context denial, store-outage failure, and the no-inference-usage boundary. The compaction test is protocol-shaped loopback evidence, not an installed Codex or live OpenAI compaction observation. Those tests establish the bounded compatibility checkpoint; they do not establish every continuation, beta field, provider upgrade, or quality outcome required to close issue #5.
