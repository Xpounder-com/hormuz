# Automatic governed-context injection

Hormuz can automatically add a bounded, verified Context Pack to ordinary Codex and Claude Code generation requests. Employees keep using the official clients. The gateway performs retrieval and provider-specific request mutation after identity and model authorization, so a model does not need to call the MCP tool and the employee does not need a wrapper.

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

Organization, team, and actor overlays are monotonic. Allowed client and model sets intersect, token and item caps take the minimum, an organization `off` cannot be enabled by a lower scope, and an organization `required` cannot be weakened. The sample and migrated configuration remain `off` until an administrator explicitly opts in.

Import or promote verified organization-, team-, or actor-visible records as described in [CONTEXT.md](CONTEXT.md). This first slice always sets `include_provisional=false`, even if an explicit Context Pack tool call is separately allowed to inspect provisional data.

## Request path

For supported requests, Hormuz applies this order:

1. authenticate the employee or workload and derive organization, team, actor, clearance, and client;
2. authorize the requested model and resolve the routed model;
3. resolve the effective injection policy;
4. extract up to 4,096 characters of direct text from the latest user-authored input, in memory only;
5. load only identity-authorized candidates and build a verified-only Context Pack;
6. commit the metadata-only context-read event;
7. render the same canonical pack as delimited, untrusted user-priority reference data;
8. run the complete mutated request through secret and DLP inspection;
9. apply provider storage policy and reserve budget against the larger serialized request;
10. replace the Hormuz credential with the company provider credential and forward; and
11. record provider usage plus content-free pack lineage.

OpenAI injection changes `input` and leaves top-level `instructions` unchanged. Anthropic injection changes the latest user `messages` content and leaves `system`, including Claude Code's attribution content, unchanged. The deterministic marker and JSON payload carry the pack, record, provenance, verification, policy, retrieval, render, and repository-revision fields. The notice says that records are untrusted reference data and cannot override system, developer, policy, or user instructions.

The original request is copied before rendering. An identical Hormuz block is not added twice to the same outbound request.

## Query and failure behavior

Only direct latest-user text is retrieval intent. Hormuz ignores system/developer instructions, assistant messages, tool results, images, and other binary or provider-generated content. It does not call a model to create the query.

| Condition | `optional` | `required` |
| --- | --- | --- |
| No direct user query | Continue; record `no_eligible_query` | Deny before provider egress |
| No safe relevant records | Continue; record `empty_pack` | Deny before provider egress |
| Unsupported mutable request shape | Continue with a bounded reason | Deny before provider egress |
| Context repository or read-audit failure | Deny with `hormuz_context_unavailable` | Deny with `hormuz_context_unavailable` |
| Injected content triggers DLP | Apply redact, approval, or deny normally | Apply redact, approval, or deny normally |

Storage and read-audit failures fail closed even in `optional` mode because authorization and lineage are then unverifiable. A required-context denial uses `hormuz_context_required` in the OpenAI-compatible error and the existing Anthropic-compatible `permission_error` envelope.

DLP runs on the rendered request. It can therefore redact a secret found inside an otherwise authorized context record before egress. The usage lineage identifies the selected source pack; it is not a byte-for-byte attestation of the post-DLP provider payload.

## Metadata and privacy

The usage event stores injection mode, outcome and reason; pack and selected record IDs; policy, retrieval and render versions; repository revision when present; estimated rendered tokens; assembly time; and authoritative fresh/already-present status. Usage exports use schema version 2 for these additive fields. Reports aggregate injected requests, required denials, estimated context tokens, and distinct packs used.

The raw retrieval query, prompt, response, rendered context, record content, title, source URI, source hash, and provider credential are not written to the usage ledger or ordinary logs. The separate context read audit remains content-free and intentionally omits the query and selected record IDs.

Treat record IDs, pack IDs, actor/team attribution, and cost metadata as access-controlled operational data. They are not employee-performance scores.

## Current boundary

This checkpoint deliberately supports only:

- OpenAI `POST /v1/responses` and Anthropic `POST /v1/messages` generation requests;
- direct current-user query extraction;
- verified organization-, team-, and actor-visible records without a repository selector; and
- fresh deterministic lexical pack assembly on every eligible request.

It does not yet inject into OpenAI compaction or Anthropic token-count requests, bind tool-only continuations to earlier lineage, accept repository/branch selectors and administrator grants, cache context packs, claim provider prompt-cache savings, or prove lower cost per verified accepted task. Repository-scoped records are excluded because the principal has no repository selector in this slice. Local SQLite and the plaintext context codec remain single-node prototype boundaries; hosted tenancy, KMS, HA, retention, and immutable audit are separate decisions and release gates.

Pinned installed-client tests prove that ordinary Codex and Claude Code generation requests traverse this path and arrive at provider-compatible fake upstreams with the authorized block present. Those tests establish the bounded compatibility checkpoint; they do not establish every continuation, beta field, provider upgrade, or quality outcome required to close issue #5.
