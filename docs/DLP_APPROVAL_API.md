# DLP approval API

Hormuz exposes a narrow metadata-only approval API for the built-in CLI and future organization integrations. It is not an employee content-review API.

## Authentication and authorization

Both endpoints require the ordinary Hormuz bearer credential. The mapped identity must belong to the same organization as the request and have the configured `dlp_approver` capability. Authorization comes only from the static identity or OIDC subject mapping; caller-supplied claims are ignored. The request actor may inspect metadata when they also hold the capability, but cannot approve their own exception.

Cross-organization lookup returns the same `dlp_approval_not_found` response as an unknown ID.

## Read one request

```http
GET /v1/dlp/approval-requests/{request_id}
Authorization: Bearer <approver credential>
Accept: application/json
```

Successful responses use HTTP `200` and schema version `1`:

```json
{
  "schema_version": 1,
  "request_id": "apr_0123456789abcdef0123456789abcdef",
  "created_at": "2026-08-15T12:00:00+00:00",
  "updated_at": "2026-08-15T12:00:00+00:00",
  "expires_at": "2026-08-15T12:15:00+00:00",
  "organization_id": "example-org",
  "actor_id": "alice",
  "actor_name": "Alice Example",
  "team_id": "engineering",
  "team_name": "Engineering",
  "client": "codex",
  "protocol": "openai",
  "requested_model": "engineering-fast",
  "routed_model": "gpt-5.4",
  "policy_version": "organization-dlp-v1",
  "rules": ["company.codename"],
  "detection_count": 1,
  "status": "pending",
  "approved_by_actor_id": null,
  "approved_by_actor_name": null,
  "approved_at": null,
  "consumed_at": null
}
```

Status is one of `pending`, `approved`, `consumed`, or `expired`. The response never contains the prompt, matched value, response, payload fingerprint, source file, provider credential, or approval fingerprint key.

`policy_version` is the organization-declared DLP version when no scoped overlay applies. When a team or person overlay changes the effective rule set, Hormuz returns a bounded `dlp-effective-v1:<digest>` value instead. That digest is derived only from safe layer versions and rule metadata; exact dictionary values are excluded. It binds the approval to the resolved policy used by the retry, so operators must bump the owning organization, team, or person policy version whenever a dictionary changes.

## Approve one request

```http
POST /v1/dlp/approval-requests/{request_id}/decisions
Authorization: Bearer <approver credential>
Content-Type: application/json

{"decision":"approve"}
```

The request body must contain exactly that field and value. Success returns HTTP `200` with the same resource schema and `status: approved`. Repeating the same decision with the same approver is idempotent and does not extend expiry. A decision from another approver after approval, a consumed request, or an expired request returns a conflict.

Approval does not itself call a provider. A later exact retry by the original employee atomically changes the state to `consumed` before provider egress. Exact matching binds the operation, transformed JSON body, raw provider query when present, complete allowlisted forwarded-header map, routed model, identity scope, and effective policy. Changing a protected query or header therefore cannot consume a grant created for earlier request material. Omitting the query field for query-free requests preserves compatibility with pending grants created before query inspection was introduced.

## Stable errors

Errors use the standard Hormuz envelope:

```json
{"error":{"code":"dlp_approval_forbidden","message":"..."}}
```

| HTTP | Code | Meaning |
| --- | --- | --- |
| `400` | `invalid_dlp_approval_decision` | Body is not the exact version-1 approve contract. |
| `401` | `unauthorized` | Credential is missing or invalid. |
| `403` | `dlp_approval_forbidden` | Capability is absent or the decision is self-approval. |
| `404` | `dlp_approval_disabled` | Approval workflow is disabled. |
| `404` | `dlp_approval_not_found` | ID is absent or belongs to another organization. |
| `409` | `dlp_approval_conflict` | Request is expired, consumed, replayed, or already decided by someone else. |
| `503` | `dlp_approval_unavailable` | Durable approval state cannot be read or committed. |

Responses use `Cache-Control: no-store`. The client refuses cross-origin redirects and plaintext HTTP outside explicit loopback development.
