# SCIM identity lifecycle

Hormuz can use a SCIM 2.0-shaped directory to provision people and
policy-bearing groups without asking employees to adopt a new AI client: a
successful OIDC login is resolved against the live directory before Hormuz
applies model, client, DLP, budget, or usage policy.

This is a bounded issue #7 milestone, not a claim of a completed enterprise
directory service. Choose either a separate local SQLite database for one
gateway process/host or the tenant-isolated PostgreSQL adapter for a shared
deployment. PostgreSQL shared state is covered by a real, disposable database
proof of CRUD, generic OIDC resolution, collision denial, denied raw routing
table access, and session revocation. HA, SCIM certification, real-IdP
conformance, SCIM bulk/filtering, KMS, and immutable external audit remain open
gates.

## What is governed

The directory stores only identity and authorization metadata:

- stable external and Hormuz resource IDs;
- display/user names, OIDC issuer and subject;
- active state and group membership;
- group-to-team, clearance, client, and capability mapping; and
- metadata-only lifecycle audit entries (administrator, action, resource ID,
  and revisions).

It does **not** store prompts, responses, code, provider keys, OIDC bearer
tokens, or static workload secrets. Directory data is still employee metadata,
so restrict database/file and administrator access accordingly. In PostgreSQL
mode, the only cross-tenant lookup relation contains HMAC tags of issuer and
subject, never their plaintext values; the runtime cannot read that relation
directly and can use only exact-tag security-definer functions.

Dynamic directory identities take precedence over a matching configured
`(issuer, subject)` mapping. An inactive user, inactive workload, removed group
member, or ambiguous team mapping fails closed before any provider request.
Existing Hormuz browser sessions re-resolve their subject on every request and
are rejected when that mapping changes. When the optional session broker is
available, a changed directory resource also triggers eager actor-scoped
session-family revocation.

## Enable it

Use a separate database and retain a tightly controlled bootstrap administrator
only until a federated identity can administer the directory. The administrator
needs `identity_admin`; that capability grants no usage-report, policy, or DLP
approval access by itself.

```json
{
  "identities": [
    {
      "token_env": "HORMUZ_BREAK_GLASS_ADMIN_TOKEN",
      "actor_id": "identity-admin",
      "actor_name": "Identity administrator",
      "team_id": "security",
      "team_name": "Security",
      "organization_id": "acme",
      "clearance": "restricted",
      "capabilities": ["identity_admin"]
    }
  ],
  "authentication": {
    "directory": {
      "enabled": true,
      "database": "./hormuz-directory.sqlite3"
    },
    "oidc": {
      "issuers": [
        {
          "issuer": "https://identity.example.com",
          "audiences": ["api://hormuz"],
          "algorithms": ["RS256"],
          "subjects": []
        }
      ]
    }
  }
}
```

The configured OIDC issuer is still the token-verification authority. An empty
`subjects` list is permitted only when the directory is enabled; the static
bootstrap administrator above gives the deployment a first administrative
principal. Replace or remove that credential through the normal configuration
rollout after a least-privileged federated directory administrator is working.
It is break-glass only, not a workload credential pattern.

The SQLite directory database must differ from the usage, context, and SQLite
session databases. `hormuz --config /etc/hormuz/hormuz.json doctor` validates
the configuration and OIDC metadata before the service starts.

### Shared PostgreSQL mode

For a shared deployment, first migrate the schema and synchronize the
deployment-owned bootstrap identity state. Set a distinct, random 32-byte
base64url routing secret in the process environment; it is used only to make
opaque routing tags and is not stored by Hormuz.

```bash
hormuz storage migrate
hormuz --config /etc/hormuz/hormuz.json identities sync
export HORMUZ_DIRECTORY_ROUTING_KEY='injected-by-your-secret-manager'
```

```json
{
  "usage_storage": {"backend": "postgresql"},
  "authentication": {
    "directory": {
      "enabled": true,
      "backend": "postgresql",
      "routing_key_env": "HORMUZ_DIRECTORY_ROUTING_KEY"
    }
  }
}
```

The PostgreSQL directory uses the same configured `usage_storage` DSN, schema,
and non-owner runtime role. It intentionally rejects a separate directory DSN:
tenant lifecycle, session revocation, and policy enforcement must share the
same RLS boundary. It does not grant the runtime direct writes to deployment
identity tables; a narrowly scoped security-definer projection function accepts
only an existing managed User or Workload in the already-bound tenant.

## SCIM surface

All routes require a current Hormuz credential whose identity has
`identity_admin`. Responses use `application/scim+json` and expose an opaque
ETag in `meta.version`/`ETag`.

| Resource | Supported routes |
| --- | --- |
| Service provider | `GET /v1/admin/scim/v2/ServiceProviderConfig` |
| Users | `GET`, `POST /v1/admin/scim/v2/Users`; `GET`, `PUT`, `PATCH`, `DELETE /v1/admin/scim/v2/Users/{id}` |
| Groups | `GET`, `POST /v1/admin/scim/v2/Groups`; `GET`, `PUT`, `PATCH`, `DELETE /v1/admin/scim/v2/Groups/{id}` |
| Workloads | `GET`, `POST /v1/admin/workload-identities`; `GET`, `PUT`, `PATCH`, `DELETE /v1/admin/workload-identities/{id}` |

`Users` and `Groups` support a deliberate SCIM subset: collection pagination
with `startIndex` and `count` (1–100), `PUT`, and SCIM PatchOp. Bulk,
filtering, sorting, and schema-discovery endpoints are not implemented; the
service-provider descriptor reports those limits.

`POST` is idempotent for the same `externalId` and complete matching resource:
the initial result is `201`, while an exact retry is `200` with the same ID.
Conflicting reuse of an external ID receives `409`. `externalId` is immutable;
rename `userName` or `displayName` instead. `PUT`, `PATCH`, and `DELETE` accept
the current ETag through `If-Match`; an old ETag receives
`412 scim_version_conflict`. Omitting `If-Match` is accepted for compatibility,
but provisioning clients should send it to make out-of-order updates explicit.

`DELETE` is a reversible deactivation rather than a physical delete. Reactivate
a user or workload with `PUT`/`PATCH` and `active: true`. Groups expose their
activation in the Hormuz group-policy extension and can be reactivated the same
way. The resource and its audit history remain for deterministic retries and
forensic review.

## User and group mapping

Users are standard SCIM Users plus the Hormuz identity extension:

```json
{
  "schemas": [
    "urn:ietf:params:scim:schemas:core:2.0:User",
    "urn:hormuz:params:scim:schemas:extension:identity:2.0:User"
  ],
  "externalId": "employee-123",
  "userName": "alice@example.com",
  "displayName": "Alice Example",
  "active": true,
  "urn:hormuz:params:scim:schemas:extension:identity:2.0:User": {
    "issuer": "https://identity.example.com",
    "subject": "stable-oidc-subject"
  }
}
```

Groups carry the explicit, versioned policy mapping rather than trusting group,
team, or role claims from an employee token:

```json
{
  "schemas": [
    "urn:ietf:params:scim:schemas:core:2.0:Group",
    "urn:hormuz:params:scim:schemas:extension:policy:2.0:Group"
  ],
  "externalId": "engineering",
  "displayName": "Engineering",
  "members": [{"value": "usr_hormuz_resource_id"}],
  "urn:hormuz:params:scim:schemas:extension:policy:2.0:Group": {
    "active": true,
    "teamId": "engineering",
    "teamName": "Engineering",
    "clearance": "internal",
    "allowedClients": ["codex", "claude-code"],
    "capabilities": []
  }
}
```

For an active user, all active groups must resolve to exactly one team. Hormuz
unions capabilities, intersects non-empty client allowlists, and chooses the
most restrictive clearance. A user with no active group, an inactive group, or
two teams is denied rather than assigned a guessed scope. Moving a group to a
new team is therefore an optimistic-concurrency update with a new ETag, not a
silent reassignment.

Group mappings govern the identity's effective team, clearance, client, and
capability scope immediately. A configuration-owned team policy overlay is
applied only when that team is already bound to exactly one organization in the
deployment configuration; otherwise the group receives the organization's
policy. Hormuz deliberately does not accept an arbitrary new SCIM `teamId` as
a cross-tenant policy key. Tenant-qualified dynamic team-policy bindings are a
remaining #7 design and implementation gate.

## Federated workload identities

Workloads are intentionally separate from SCIM people/groups. They are
federated OIDC bindings, not stored API keys:

```json
{
  "externalId": "github-actions-production",
  "displayName": "GitHub Actions production",
  "identityType": "ci",
  "active": true,
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "repo:acme/hormuz:environment:production",
  "teamId": "engineering",
  "teamName": "Engineering",
  "clearance": "internal",
  "allowedClients": ["codex"],
  "capabilities": []
}
```

`identityType` must be `service_account`, `ci`, or `connector`. The issuer must
already be configured for Hormuz OIDC verification. The upstream JWT's required
`exp` claim and signature verification provide the short-lived credential
boundary; this record only authorizes its `(issuer, subject)` after those checks
pass. Usage events preserve the same identity type so human, service-account,
CI, and connector traffic can be distinguished without retaining request
content.

Do not create a long-lived static token for a workload. A static configured
identity is only a documented break-glass exception during rollout; use a
short-lived OIDC workload token in CI or a connector instead.

## Rollout and incident procedure

1. Choose the local SQLite adapter for one host, or migrate PostgreSQL and set
   its routing-key environment variable for a shared deployment. Keep one
   least-privileged bootstrap `identity_admin`.
2. Configure and preflight the OIDC issuer; keep its audience and signing
   policy separate from the browser-login client.
3. Create an empty group mapping first, then users and memberships, and test a
   `GET /v1/gateway/whoami` with a short-lived token before migrating a team.
4. Use ETags for every update, record external IDs in the IdP/SCIM connector,
   and treat a `409` or `412` as an operator reconciliation event rather than
   retrying blindly.
5. On departure or incident, set the user/workload/group inactive or remove
   the membership. The next gateway request denies the identity; a session
   broker also eagerly revokes matching browser-session families.
6. Rotate or remove the bootstrap credential after testing a federated
   `identity_admin`; audit directory ownership and back it up according to the
   organization's employee-data policy.

The directory lifecycle log is not an immutable audit sink and does not replace
IdP, HR, or SIEM records. SCIM vendor testing, service-account token exchange,
retention/legal-hold, KMS custody/rotation, pooling/HA, backup/PITR, and
independent review are deliberately not represented as complete by this
document.
