# Context Pack API

Hormuz exposes an authenticated REST boundary for retrieving the smallest authorized governed-context pack without calling a model:

```http
POST /v1/context/packs
Authorization: Bearer <Hormuz bootstrap or OIDC access token>
Content-Type: application/json
```

This is an additive `v1` endpoint. It uses the same static-token or OIDC JWT authentication boundary as the provider gateway, but it does not trust organization, team, actor, or policy-version values from the request.

## Organization policy

The server owns the pack caps and policy version:

```json
{
  "context_service": {
    "policy_version": "engineering-context-v1",
    "max_token_budget": 32768,
    "max_items": 20,
    "requests_per_minute": 60,
    "allow_provisional": false
  }
}
```

`requests_per_minute` is enforced per `(organization, actor)` in each Hormuz process and returns `Retry-After` on denial. A horizontally scaled deployment still requires a shared limiter or an organization-controlled ingress limit; the local limiter is not a claim of distributed quota consistency.

## Request

Minimal request:

```json
{
  "query": "How should API retries work?",
  "token_budget": 2000
}
```

Full request:

```json
{
  "query": "How should API retries work?",
  "token_budget": 2000,
  "max_items": 10,
  "repository_id": "Xpounder-com/hormuz",
  "branch": "main",
  "clearance": "internal",
  "include_provisional": false
}
```

Contract:

- `query` and `token_budget` are required. Query length, lexical content, integer types, and positive bounds are validated before retrieval.
- `max_items` may narrow but cannot exceed the configured organization cap.
- `repository_id` and `branch` narrow scope. A branch requires a repository.
- `clearance` may narrow but cannot exceed the authenticated identity's configured clearance.
- `include_provisional` defaults to `false` and is denied unless the organization explicitly enables it.
- Unknown fields fail closed. In particular, callers cannot supply `organization_id`, `team_id`, `actor_id`, `policy_version`, or `as_of`.
- Evaluation time is the server's current UTC time. The online endpoint cannot replay expired or not-yet-effective records by requesting a historical time.

The existing `max_request_bytes` setting bounds the JSON body before parsing. Output is additionally bounded by the context token and item caps.

## Response

A successful response is `hormuz.context-pack.v1`:

```json
{
  "schema_version": "hormuz.context-pack.v1",
  "pack_id": "ctxpack_0123456789abcdef01234567",
  "manifest_sha256": "...",
  "query": "How should API retries work?",
  "policy_version": "engineering-context-v1",
  "as_of": "2026-08-15T22:00:00Z",
  "scope": {
    "organization_id": "xpounder",
    "team_id": "engineering",
    "actor_id": "alice",
    "clearance": "internal",
    "repository_id": "Xpounder-com/hormuz",
    "branch": "main"
  },
  "token_budget": 2000,
  "estimated_tokens": 0,
  "eligible_records": 0,
  "matched_records": 0,
  "selected_records": 0,
  "items": []
}
```

Items use the same content-bearing, source-linked manifest contract documented in [CONTEXT.md](CONTEXT.md). Storage versions and persistence rows are never returned. An empty authorized or lexical result is `200 OK` with an empty `items` array, not `404`.

Every response uses `Cache-Control: no-store`. Hormuz does not cache an authorization decision, pack, prompt, or model answer in this path.

## Errors

Errors have a stable envelope:

```json
{
  "error": {
    "code": "context_policy_denied",
    "message": "Requested token budget exceeds organization context policy"
  }
}
```

| Status | Code | Meaning |
| --- | --- | --- |
| `400` | `context_invalid_request` | Invalid type, value, scope combination, query, or unknown field. |
| `401` | `unauthorized` | Missing or invalid Hormuz credential. |
| `403` | `context_policy_denied` | Requested budget, item cap, clearance, or provisional access exceeds policy. |
| `413` | `request_too_large` | JSON body exceeds `max_request_bytes`. |
| `429` | `context_rate_limited` | Per-actor local request limit exceeded; inspect `Retry-After`. |
| `503` | `context_store_unavailable` | The governed-context repository failed closed. |

Internal storage errors are logged without query or record content and are not returned to the caller.

## Security and side-effect boundary

The request path is:

1. authenticate bootstrap/OIDC identity;
2. enforce actor rate limit;
3. validate and apply server-owned policy caps;
4. filter organization, visibility, classification, repository, branch, verification, and freshness in SQLite before content decode;
5. repeat authorization checks, rank deterministically, and enforce the token/item budget;
6. return the explicit manifest with `no-store`.

The endpoint does not call OpenAI or Anthropic, consume a provider credential, mutate the usage ledger, inject content into Codex or Claude Code, or enable the proposed cache. A metadata log records actor/team/org, repository/branch, pack ID, item count, and estimated tokens; it does not record the query or context content. Durable read-audit events and reader-specific RBAC remain open work.

## Example

```bash
curl --fail-with-body \
  --request POST \
  --header "Authorization: Bearer ${HORMUZ_TOKEN}" \
  --header "Content-Type: application/json" \
  --data '{"query":"API retries","token_budget":2000,"repository_id":"Xpounder-com/hormuz"}' \
  http://127.0.0.1:8787/v1/context/packs
```

The API is not yet an MCP tool and is not automatically called by Codex or Claude Code. Those are separate roadmap gates so automatic injection cannot bypass context authorization or secret-egress inspection.
