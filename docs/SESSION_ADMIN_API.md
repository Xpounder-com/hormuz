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

Authentication alone is insufficient. A valid identity without this explicit capability receives `403 session_admin_capability_required`. Every list and revocation operation derives `organization_id` from the authenticated administrator; callers cannot request another organization.

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

`POST /v1/admin/session-revocations` accepts exactly:

```json
{
  "scope": "actor",
  "target": "alice",
  "reason_code": "access_change"
}
```

Organization scope omits `target`. The response includes the same scope, target, reason code, and `revoked_sessions` count. Unknown, repeated, malformed, cross-tenant, and unsupported fields fail closed with stable JSON errors.

## Persistence and security boundary

Session-store schema version 2 binds each newly issued session to the event-time organization, actor, team, clearance, and AI client. On every use, Hormuz compares that binding with the current authoritative issuer-subject mapping. Changing the organization, actor, team, clearance, or allowed client revokes the session before policy or provider work.

The v1-to-v2 migration cannot reconstruct trustworthy historical tenant bindings from credential hashes alone. It therefore revokes every unbound legacy session and requires those employees to sign in again. Administrative revocation records one metadata-only local security event per affected session with target organization/actor/team, decision actor, scope, and bounded reason code.

This is the verified single-node control, not the pending enterprise persistence design. Shared PostgreSQL tenancy, multi-node immediate revocation, SCIM/event-driven deprovisioning, KMS custody, live configuration reload, externally immutable session-event export, HA, backup/restore, and a real owner-selected IdP remain open gates.
