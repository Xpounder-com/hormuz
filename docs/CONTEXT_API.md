# Context Pack API

Hormuz exposes an authenticated REST boundary for retrieving the smallest authorized governed-context pack without calling a model:

```http
POST /v1/context/packs
Authorization: Bearer <Hormuz bootstrap, workload OIDC, or human-session access credential>
Content-Type: application/json
```

This is an additive `v1` endpoint. It uses the same static, OIDC JWT, or opaque human-session authentication boundary as the provider gateway, but it does not trust organization, team, actor, or policy-version values from the request.

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
- The context route has a fixed `64 KiB` JSON-body ceiling in addition to the
  global gateway request limit. It rejects oversized bodies before JSON parsing.
- `max_items` may narrow but cannot exceed the configured organization cap.
- `repository_id` and `branch` narrow scope. A branch requires a repository;
  non-printable Unicode and control characters are rejected before those values
  can reach authorization or metadata logs.
- `clearance` may narrow but cannot exceed the authenticated identity's configured clearance.
- `include_provisional` defaults to `false` and is denied unless the organization explicitly enables it.
- Unknown fields fail closed. In particular, callers cannot supply `organization_id`, `team_id`, `actor_id`, `policy_version`, or `as_of`.
- Evaluation time is the server's current UTC time. The online endpoint cannot replay expired or not-yet-effective records by requesting a historical time.

The complete serialized form of every selected item—including content,
provenance, lifecycle, and classification metadata—contributes to its
deterministic token estimate. Selection is bounded by the context token and item
caps; the response wrapper is outside that estimate, and the estimate is not a
provider tokenizer guarantee.

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
  "lifecycle": {
    "version": "v1",
    "outcome": "complete",
    "snapshot_sha256": null,
    "repository_revision": null,
    "excluded_records": 0,
    "contradiction_groups": 0
  },
  "exclusions": [],
  "contradictions": [],
  "items": []
}
```

Items use the same content-bearing, source-linked manifest contract documented in [CONTEXT.md](CONTEXT.md). Storage versions and persistence rows are never returned. An empty authorized or lexical result is `200 OK` with an empty `items` array, not `404`.

The additive lifecycle result is `complete`, `partial`, or `requires_resolution`. `partial` means at least one otherwise-authorized record was excluded by dependency, source-revision, or quarantine evaluation. `requires_resolution` means active structured assertions disagree; every conflicting record is excluded, and `contradictions` returns its authorized source references and assertion values. Because exclusions and contradictions contain authorized source metadata, the complete response remains company content.

When both `repository_id` and `branch` are requested, the server loads the latest trusted snapshot stored for that exact authenticated organization scope. Callers cannot submit a lifecycle snapshot to the HTTP endpoint. A record with explicit dependencies fails closed when its dependency observation is missing, including when no exact snapshot has been stored.

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
| `413` | `request_too_large` | JSON body exceeds the context route's `64 KiB` ceiling or the lower configured global limit. |
| `429` | `context_rate_limited` | Per-actor local request limit exceeded; inspect `Retry-After`. |
| `503` | `context_store_unavailable` | The governed-context repository failed closed. |

Internal storage errors are logged without query or record content and are not returned to the caller.
Authentication, rate-limit, unknown-route, and oversized-body rejections close
the HTTP/1.1 connection when a request body may remain unread, preventing the
body from being interpreted as a subsequent request.

## Security and side-effect boundary

The request path is:

1. authenticate bootstrap, workload OIDC, or opaque human-session identity;
2. enforce actor rate limit;
3. validate and apply server-owned policy caps;
4. filter organization, visibility, classification, repository, branch, verification, and freshness in SQLite before content decode;
5. load the server-owned lifecycle snapshot for the exact authenticated organization/repository/branch scope;
6. repeat authorization checks, identify lexical matches, then quarantine high-confidence prompt injection, invalidate changed dependencies and `git:` source revisions, and exclude explicit contradiction groups without disclosing unrelated lifecycle findings;
7. rank deterministically and enforce the token/item budget;
8. durably commit a metadata-only pack-read event, including only lifecycle outcome and aggregate exclusion/contradiction counts;
9. return the explicit manifest with `no-store`.

The endpoint does not call OpenAI or Anthropic, consume a provider credential, mutate the usage ledger, inject content into Codex or Claude Code, or enable the proposed cache. Every successful response first commits a durable metadata-only read event containing actor/team/org, repository/branch, clearance, policy version, pack ID, provisional flag, lifecycle outcome, and aggregate record/token/exclusion/contradiction counts. It does not contain the query, context content, titles, source locators or hashes, assertion values, or selected record IDs. If that audit write fails, Hormuz returns the sanitized `503` envelope and no pack. Reader-specific enterprise RBAC remains open work.

## Example

```bash
curl --fail-with-body \
  --request POST \
  --header "Authorization: Bearer ${HORMUZ_TOKEN}" \
  --header "Content-Type: application/json" \
  --data '{"query":"API retries","token_budget":2000,"repository_id":"Xpounder-com/hormuz"}' \
  http://127.0.0.1:8787/v1/context/packs
```

The same boundary is now exposed as the read-only `hormuz_get_context` MCP tool documented in [MCP.md](MCP.md). MCP retrieval remains explicit; automatic injection is a separate roadmap gate so it cannot bypass context authorization or secret-egress inspection.
