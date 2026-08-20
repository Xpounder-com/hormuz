# Human-session administration

Hormuz exposes tenant-scoped, metadata-only administration for the opaque human sessions issued by the accepted OIDC session broker. Administrators continue using the `hormuz` CLI; employees continue using Codex or Claude Code.

## Authorization

Grant `session_admin` only to the static or OIDC subject mappings that operate session security for their organization:

```json
{
  "actor_id": "security-admin",
  "actor_name": "Security Admin",
  "team_id": "security",
  "team_name": "Security",
  "organization_id": "xpounder",
  "clearance": "restricted",
  "allowed_clients": ["codex", "claude-code"],
  "capabilities": ["session_admin"]
}
```

Authentication alone is insufficient. A valid identity without this explicit capability receives `403 session_admin_capability_required`. Every session, event, and revocation operation derives `organization_id` from the authenticated administrator; callers cannot request another organization.

## CLI

List active sessions, optionally filtering by an exact actor or team ID:

```bash
export HORMUZ_ADMIN_TOKEN="current-short-lived-administrator-credential"

hormuz sessions list \
  --gateway https://hormuz.example.com \
  --credential-env HORMUZ_ADMIN_TOKEN \
  --team engineering \
  --limit 50
```

The JSON result contains session ID, organization, actor, team, bound client, creation/refresh times, access expiry, absolute expiry, and an optional opaque `next_cursor`. It never contains an access token, refresh token, OIDC subject, provider token, or prompt content. Pass `--cursor` to fetch the next page.

Inspect session-security evidence, optionally filtering by target actor, target team, event type, or an inclusive UTC lower bound:

```bash
hormuz sessions events \
  --gateway https://hormuz.example.com \
  --credential-env HORMUZ_ADMIN_TOKEN \
  --event-type admin_revocation \
  --since 2026-08-01T00:00:00Z \
  --limit 50
```

Visible event types are `refresh_replay`, `logout`, `authorization_mapping_removed`, and `admin_revocation`. Results contain only event/session IDs, event time and type, target organization/actor/team, and nullable administrative decision actor/scope/reason. They contain no session credential, OIDC subject, provider key, prompt, response, or model payload. Events are ordered newest first; pass the opaque `next_cursor` unchanged to fetch the next page.

Revoke one session, every session for an actor, every session for a team, or every active session in the administrator's organization:

```bash
hormuz sessions revoke \
  --gateway https://hormuz.example.com \
  --credential-env HORMUZ_ADMIN_TOKEN \
  --scope actor \
  --target alice \
  --reason employment_change

hormuz sessions revoke \
  --gateway https://hormuz.example.com \
  --credential-env HORMUZ_ADMIN_TOKEN \
  --scope organization \
  --reason security_incident
```

`--target` is required for `session`, `actor`, and `team`; it is forbidden for `organization`. Supported reason codes are `access_change`, `employment_change`, `security_incident`, and `administrative`. Revocation is idempotent: a retry reports zero newly revoked sessions rather than restoring or duplicating state. For a legacy pre-v2 session ID that begins with `-`, use the `--target=<id>` form.

A saved administrator human-session profile can replace the environment credential with `--profile <name>`. Its current short-lived access credential is read from the OS secure store through the same helper used by Codex and Claude Code.

## HTTP contract

`GET /v1/admin/sessions` accepts optional single-valued `actor_id`, `team_id`, `limit` (1–100), and opaque `cursor` query fields. Active sessions are ordered by creation time and session ID, newest first. The response is:

```json
{
  "schema_version": 1,
  "sessions": [],
  "next_cursor": null
}
```

`GET /v1/admin/session-events` accepts optional single-valued `actor_id`, `team_id`, `event_type`, `since`, `limit` (1–100), and opaque `cursor` query fields. `since` must be an offset-aware ISO-8601 timestamp. The response is:

```json
{
  "schema_version": 1,
  "events": [
    {
      "event_id": "sev_example",
      "occurred_at": "2026-08-15T20:00:00+00:00",
      "session_id": "ses_example",
      "event_type": "admin_revocation",
      "organization_id": "xpounder",
      "target_actor_id": "alice",
      "target_team_id": "engineering",
      "decision_actor_id": "security-admin",
      "decision_scope": "actor",
      "reason_code": "access_change"
    }
  ],
  "next_cursor": null
}
```

`POST /v1/admin/session-revocations` accepts exactly:

```json
{
  "scope": "actor",
  "target": "alice",
  "reason_code": "access_change"
}
```

Organization scope omits `target`. The response includes the same scope, target, reason code, and `revoked_sessions` count. Unknown, repeated, malformed, cross-tenant, and unsupported fields fail closed with stable JSON errors.

## Stable errors

All routes use the standard Hormuz JSON error envelope. The mutation route can
also fail at the shared HTTP body-ingress boundary before JSON parsing:

| HTTP | Code | Meaning |
| --- | --- | --- |
| `400` | `incomplete_request_body` | The connection ended before the announced `Content-Length` was received. |
| `400` | `invalid_session_list_request` | Session-list query fields are invalid. |
| `400` | `invalid_session_event_request` | Session-event query fields are invalid. |
| `400` | `invalid_session_revocation` | The revocation body is malformed or violates its scope contract. |
| `401` | `unauthorized` | The credential is missing or invalid. |
| `403` | `session_admin_capability_required` | The authenticated identity lacks the explicit administrator capability. |
| `408` | `request_body_timeout` | The complete announced body was not received within the configured absolute request-body deadline. |
| `503` | `session_admin_unavailable` | Durable session administration state cannot be read or committed. |

## Persistence and security boundary

SQLite session-store schema version 2 and PostgreSQL schema version 3 bind each newly issued session to the event-time organization, actor, team, clearance, AI client, and authorization version. On every use, Hormuz compares that binding with the current authoritative issuer-subject mapping. `hormuz identities sync` increments affected database authorization versions and revokes active sessions transactionally when the organization, actor, team, clearance, capability, or allowed client changes.

The v1-to-v2 migration cannot reconstruct trustworthy historical tenant bindings from credential hashes alone. It therefore revokes every unbound legacy session and requires those employees to sign in again. Newly recorded logout, refresh-replay, mapping-removal, and administrative-revocation events carry the trusted session binding. Migration evidence and older pre-v2 events without a trustworthy organization binding are deliberately excluded from the tenant event API.

This is a queryable evidence ledger, not an immutable enterprise audit sink. PostgreSQL supplies shared tenancy and multi-instance immediate revocation, but SCIM/event-driven deprovisioning, KMS custody and rotation, live configuration reload, signed or externally immutable session-event export, retention controls, SIEM delivery, pooling, HA, backup/restore, and a real owner-selected IdP remain open gates.
