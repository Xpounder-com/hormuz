# Authenticated audit-event API

Hormuz exposes a read-only, metadata-only audit-event endpoint for a tenant's
authorized auditors. It is distinct from the local `audit-export` command:
the gateway derives the organization exclusively from the authenticated
identity, and the successful read is itself recorded before a response is
released.

## Authorization

Assign the independent `audit_viewer` capability to the identity or mapped OIDC
subject that may inspect audit events. It grants only this tenant-scoped,
metadata-only read. It does not grant policy administration, identity or SCIM
administration, session administration, DLP approval, usage-report visibility,
model access, budget changes, or a selectable organization scope.

An identity without that capability receives:

```text
403 audit_viewer_capability_required
```

## CLI

Employees continue to use their existing Codex or Claude Code client for model
work. An authorized auditor can separately inspect the evidence ledger through
the same Hormuz gateway:

```bash
hormuz audit events \
  --gateway https://hormuz.example.com \
  --profile security-auditor \
  --kind security \
  --since 2026-08-01T00:00:00Z
```

For a workload or break-glass credential, use a dedicated environment variable
instead of putting a credential on the command line:

```bash
HORMUZ_AUDIT_TOKEN=... hormuz audit events \
  --gateway https://hormuz.example.com \
  --credential-env HORMUZ_AUDIT_TOKEN \
  --kind all
```

`--limit` is 1 through 100 and defaults to 50. Pass the opaque `next_cursor`
unchanged to retrieve the next page. The client rejects redirects, oversized
responses, malformed envelopes, cross-tenant events, and fields that could
carry request content or credentials.

## HTTP contract

```text
GET /v1/admin/audit-events
Authorization: Bearer <Hormuz credential>
Accept: application/json
```

Optional query parameters are:

| Parameter | Meaning |
| --- | --- |
| `kind` | `all` (default), `usage`, or `security`. |
| `since` | UTC ISO-8601 lower bound; default is the start of the current UTC month. |
| `limit` | Page size from 1 through 100; default 50. |
| `cursor` | Opaque cursor from the immediately compatible prior page. |

There is intentionally no `organization_id`, actor, team, or arbitrary query
filter. The server fixes the organization from the credential. A cursor binds
the event kind, normalized lower bound, frozen UTC upper bound, and offset, so
new events cannot drift a multi-page read. Changing a cursor-bound kind or
lower bound fails with `400 invalid_audit_event_request`.

A successful response has this exact envelope:

```json
{
  "schema_version": 1,
  "organization_id": "xpounder",
  "kind": "security",
  "window": {
    "start": "2026-08-01T00:00:00+00:00",
    "end": "2026-08-21T12:00:00+00:00",
    "timezone": "UTC"
  },
  "events": [],
  "next_cursor": null
}
```

Every returned event carries the same `organization_id`. Events are the existing
metadata-only usage and security audit forms described in [AUDIT.md](AUDIT.md);
they do not include prompts, responses, raw request bodies, headers, OAuth
codes, matched secret values, provider credentials, or source content.

Before the gateway sends this response, it writes a
`security.admin.audit_read` event containing the auditor identity, tenant,
event kind, frozen window, and returned-event count. If reading or writing that
event fails, Hormuz returns `503 audit_events_unavailable` without an `events`
field.

The ledger remains employee and security metadata. Treat audit-viewer access,
exports, retention, and downstream destinations as controlled company data.

## Storage and evidence boundary

SQLite supports this endpoint for local development and single-node operation.
The PostgreSQL backend preserves the same tenant-scoped query and audit-write
semantics while combining current PostgreSQL evidence with explicitly retained
pre-cutover SQLite evidence. Neither mode is an immutable enterprise audit
sink: signed/WORM delivery, centralized retention and legal hold, backup/PITR,
HA/DR, KMS/BYOK custody, real-IdP conformance, and independent security review
remain separate release gates.
