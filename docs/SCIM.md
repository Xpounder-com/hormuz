# SCIM identity lifecycle

Hormuz can use a SCIM 2.0-shaped directory to provision people and group
memberships without asking employees to adopt a new AI client: a
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

The directory stores only identity and lifecycle metadata:

- stable external and Hormuz resource IDs;
- display/user names, OIDC issuer and subject;
- active state and group membership;
- metadata-only lifecycle audit entries (administrator, action, resource ID,
  and revisions).

The tenant's immutable policy version stores the authorization mapping:
`(organization_id, scim_group_external_id)` selects one pre-approved Hormuz
authorization profile. A profile supplies the internal team, clearance,
allowed clients, capabilities, and an additional restrictive model/budget
policy. This split is intentional: the IdP says who belongs to which stable
group; Hormuz decides what that membership may do.

It does **not** store prompts, responses, code, provider keys, OIDC bearer
tokens, or static workload secrets. Directory data is still employee metadata,
so restrict database/file and administrator access accordingly. In PostgreSQL
mode, the only cross-tenant lookup relation contains HMAC tags of issuer and
subject, never their plaintext values; the runtime cannot read that relation
directly and can use only exact-tag security-definer functions.

Dynamic directory identities take precedence over a matching configured
`(issuer, subject)` mapping. An inactive user, inactive workload, removed group
member, unbound active group, or conflicting group-policy selection fails
closed before any provider request. Unbound groups deny by default; an active
tenant policy may opt into one explicit pre-approved fallback profile.
Existing Hormuz browser sessions re-resolve their subject on every request and
are rejected when that mapping changes. When the optional session broker is
available, a changed directory resource also triggers eager actor-scoped
session-family revocation.

## Enable it

Use a separate database and retain a tightly controlled bootstrap administrator
only until a federated identity can administer the directory. The administrator
that provisions Users and Groups needs `identity_admin`; that capability grants
no usage-report, policy, DLP approval, model, budget, or client authorization
access by itself. A separate `policy_admin` stages and activates group bindings
and authorization profiles.

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

Users and Groups require `identity_admin`. Workload identity routes require
`policy_admin`, because their current direct fields determine authorization.
Responses use `application/scim+json` and expose an opaque ETag in
`meta.version`/`ETag`.

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
activation in the Hormuz directory extension and can be reactivated the same
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

Groups are lifecycle records. Their `externalId` is the stable key Hormuz uses
to select a policy-owned authorization profile; `displayName` is never used for
authorization:

```json
{
  "schemas": [
    "urn:ietf:params:scim:schemas:core:2.0:Group",
    "urn:hormuz:params:scim:schemas:extension:directory:3.0:Group"
  ],
  "externalId": "engineering",
  "displayName": "Engineering",
  "members": [{"value": "usr_hormuz_resource_id"}],
  "urn:hormuz:params:scim:schemas:extension:directory:3.0:Group": {
    "active": true
  }
}
```

For an active user, Hormuz resolves every active group through the active
tenant policy. Every group must be bound, unless that tenant explicitly chose a
single fallback profile. All selected bindings must produce the same
`(team_id, policy_id)` pair; otherwise Hormuz denies the request. The selected
profile supplies the effective team, clearance, allowed clients, capabilities,
and an additional restrictive policy overlay.

In configuration, this lives under the policy system, not under
`authentication.directory`:

```json
{
  "policies": {
    "authorization_profiles": {
      "engineering-standard": {
        "organization_id": "acme",
        "team_id": "engineering",
        "team_name": "Engineering",
        "clearance": "internal",
        "allowed_clients": ["codex", "claude-code"],
        "capabilities": [],
        "policy": {
          "allowed_models": ["gpt-5.4", "claude-sonnet-5"],
          "max_output_tokens": 16000
        }
      }
    },
    "team_bindings": [
      {
        "organization_id": "acme",
        "scim_group_external_id": "engineering-employees",
        "team_id": "engineering",
        "policy_id": "engineering-standard"
      }
    ],
    "unbound_scim_group_action": "deny"
  }
}
```

The policy API projects these fields as `authorization_profiles` and
`team_bindings` in tenant policy projection v4. Thus only `policy_admin` can
stage, activate, or roll back a new group-to-profile authorization decision;
an `identity_admin` may add/remove membership but cannot grant a new model,
budget, client, clearance, or capability. `unbound_scim_group_action` defaults
to `deny`. The only alternative is `fallback` with an explicit
`unbound_scim_group_fallback` containing a pre-approved `team_id` and
`policy_id`.

### SCIM group contract migration

The previous `urn:hormuz:params:scim:schemas:extension:policy:2.0:Group`
contract is no longer accepted for new or replacement group requests. Its
`teamId`, `teamName`, `clearance`, `allowedClients`, and `capabilities` fields
produce `scim_group_authorization_fields_forbidden`. Update the connector to
send the directory 3.0 extension above while preserving the same stable group
`externalId`, then stage and activate the v4 policy binding before relying on
the membership. Existing stored legacy values are ignored for authorization;
there is no fallback to their former permissions.

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
content. Because this v0.2 workload record still contains direct authorization
fields, creating, changing, or deactivating it requires `policy_admin`, not
`identity_admin`. Converging workloads on the same pre-approved profile model
is a follow-on; this change does not claim that SCIM group bindings govern
workloads.

Do not create a long-lived static token for a workload. A static configured
identity is only a documented break-glass exception during rollout; use a
short-lived OIDC workload token in CI or a connector instead.

## Rollout and incident procedure

1. Choose the local SQLite adapter for one host, or migrate PostgreSQL and set
   its routing-key environment variable for a shared deployment. Keep one
   least-privileged bootstrap `identity_admin` and one separate `policy_admin`.
2. Configure and preflight the OIDC issuer; keep its audience and signing
   policy separate from the browser-login client.
3. Create the pre-approved authorization profile and tenant-qualified group
   binding, stage/activate the policy version, then create users and group
   memberships. Test a `GET /v1/gateway/whoami` with a short-lived token before
   migrating a team.
4. Use ETags for every update, record external IDs in the IdP/SCIM connector,
   and treat a `409` or `412` as an operator reconciliation event rather than
   retrying blindly.
5. On departure or incident, set the user/workload/group inactive or remove
   the membership. The next gateway request denies the identity; a session
   broker also eagerly revokes matching browser-session families.
6. Rotate or remove the bootstrap credential after testing federated
   `identity_admin` and `policy_admin` identities; audit directory ownership
   and back it up according to the organization's employee-data policy.

The directory lifecycle log is not an immutable audit sink and does not replace
IdP, HR, or SIEM records. SCIM vendor testing, service-account token exchange,
retention/legal-hold, KMS custody/rotation, pooling/HA, backup/PITR, and
independent review are deliberately not represented as complete by this
document.
