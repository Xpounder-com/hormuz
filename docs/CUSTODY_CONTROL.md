# Tenant custody authority

Hormuz can persist a tenant-scoped `custody_admin` authority that approves
secret-envelope lifecycle work without granting the administrator inference,
policy, identity, database-owner, customer-IAM, or direct KMS-administration
permission. This is an authorization service, not the machine that performs an
approved operation.

The security boundary is deliberately split:

1. A human administrator authenticates to Hormuz and approves an exact,
   content-free operation intent.
2. PostgreSQL retains the tenant-qualified intent, approvals, and immutable
   control events through the dedicated custody-control role.
3. A future separately permissioned custody executor may consume an authorized,
   unexpired intent. The normal gateway runtime and current CLI do not become
   that executor.
4. The customer key service remains authoritative for key policy, protection,
   disablement, and deletion. Hormuz never edits customer IAM policy.

## Authority boundaries

| Principal | Hormuz authority | Explicitly absent |
| --- | --- | --- |
| `custody_admin` | Manage tenant custody administrators; approve exact lifecycle intents; inspect metadata-only status | Inference, policy, identity, customer IAM/KMS administration, plaintext access |
| Custody-control service | Persist administrators, intents, approvals, and events | KMS calls, secret material, policy changes, gateway traffic |
| Gateway runtime | Existing configured data-plane decrypt/generate operations only | Custody administration and governed lifecycle execution |
| Future custody executor | Consume one authorized, unexpired intent under separately reviewed machine permissions | Human authorization and arbitrary customer-IAM changes |
| Customer KMS administrator | Own key/IAM lifecycle and protection | Automatic Hormuz role or entitlement |
| Break-glass operator | Future recovery after loss of every custody administrator | Ordinary rewrap or restore-verification workflow |

## Configuration and bootstrap

Managed custody control requires PostgreSQL usage storage, a configured key
custody profile, and credentials and roles distinct from the runtime,
migration, and policy-control surfaces:

```json
{
  "usage_storage": {
    "backend": "postgresql",
    "postgres_dsn_env": "HORMUZ_POSTGRES_DSN",
    "postgres_migration_dsn_env": "HORMUZ_POSTGRES_MIGRATION_DSN",
    "postgres_schema": "hormuz",
    "postgres_runtime_role": "hormuz_runtime"
  },
  "policy_control": {
    "mode": "postgresql",
    "postgres_control_dsn_env": "HORMUZ_POLICY_CONTROL_DSN",
    "postgres_control_role": "hormuz_policy_control",
    "bootstrap_administrators": [
      {"organization_id": "acme", "actor_id": "policy-bootstrap"}
    ]
  },
  "custody_control": {
    "mode": "postgresql",
    "postgres_control_dsn_env": "HORMUZ_CUSTODY_CONTROL_DSN",
    "postgres_control_role": "hormuz_custody_control",
    "authorization_ttl_seconds": 900,
    "bootstrap_administrators": [
      {"organization_id": "acme", "actor_id": "custody-bootstrap"}
    ]
  }
}
```

The omitted `key_custody` block is still required; configure the selected
customer-controlled profile as described in [CUSTODY.md](CUSTODY.md). Put each
DSN and administrator credential in the deployment secret manager, never in
JSON or shell history. Every static bootstrap `actor_id` in this fragment must
also identify a configured static identity for the same organization.

Configuration bootstrap identities are consulted only before the tenant's
first custody initialization. Hormuz then persists them in one transaction;
PostgreSQL becomes the everyday source of authority. A static configured
identity may bootstrap and later be retired, but cannot be newly granted.
OIDC administrators are keyed only by `(organization_id, issuer, subject)`.
Email addresses, usernames, mutable group names, and arbitrary token claims do
not grant custody authority. An OIDC custody administrator need not have a
gateway inference identity.

Before schema migration, the database operator must create
`hormuz_custody_control` as a distinct restricted role with `NOINHERIT` and
without superuser, database-creation, role-creation, or `BYPASSRLS` privileges.
PostgreSQL schema v5 grants it only the custody-control tables and shared
migration ledger. Runtime and policy-control roles receive no custody-table
access; the custody role receives no usage or policy-control access.

## Operation and approval contract

Hormuz accepts only this fixed vocabulary:

| Operation | Target | Risk | Required approvals |
| --- | --- | --- | --- |
| `seal_envelope` | envelope | Routine | 1 |
| `rewrap_envelope` | envelope | Routine | 1 |
| `verify_restore` | restore | Routine | 1 |
| `retire_envelope` | envelope | Destructive | 2 |
| `disable_provider_credential` | provider credential | Destructive | 2 |
| `retire_key_reference` | key reference | Destructive | 2 |
| `resolve_recovery` | recovery record | Destructive | 2 |

There is no generic `revoke` operation. Revoking an administrator, retiring an
envelope, disabling a provider credential, and retiring a key reference have
different evidence and consequences.

An authorization binds one immutable operation ID to the operation type, a
target SHA-256, a normalized-parameters SHA-256, an expiry, and the requesting
administrator's opaque identity key. The requester supplies the first
approval. Destructive work remains pending until a different active
administrator approves the same row. Approval history and control events are
append-only; only the pending-to-authorized state pointer may change. Expiry is
derived when read and never rewrites the historical authorization or approval.
Both destructive-operation approvers must still be active when authorization
completes. If an earlier approver has been revoked, the immutable intent stays
pending and a new intent must collect two current approvals.

For initial enrollment, the secret owner supplies plaintext through a protected
input path owned by the future executor. The administrator authorizes only the
SHA-256 of that protected handle. Hormuz custody-control storage, status, and
events contain no plaintext, ciphertext, credential value, prompt, or response.

## CLI authorization flow

Run the command first and expose the authenticated administrator credential
only through the named secure environment source:

```bash
hormuz --config /etc/hormuz/hormuz.json custody bootstrap \
  --organization acme

hormuz --config /etc/hormuz/hormuz.json custody authorize \
  --organization acme \
  --operation retire_key_reference \
  --target-sha256 <64-lowercase-hex> \
  --parameters-sha256 <64-lowercase-hex>

hormuz --config /etc/hormuz/hormuz.json custody approve \
  --organization acme \
  --operation-id <immutable-operation-id>

hormuz --config /etc/hormuz/hormuz.json custody status \
  --organization acme \
  --json
```

The CLI never accepts a self-asserted actor or plaintext authorization field.
It authenticates through the Hormuz service boundary. In managed mode, the
legacy direct `custody verify`, `custody seal`, and `custody rewrap` commands
fail with `custody_governed_executor_required`; an approval is not silently
executed by the CLI process.

The last active administrator cannot be revoked through the ordinary path. If
all administrators are lost through an external database or disaster event,
ordinary operations fail with `custody_break_glass_required`. This release has
no custody break-glass recovery command. Adding one requires its own reviewed
credential, procedure, and evidence boundary.

## Contracts and nonclaims

`hormuz.custody-control-status` v1 is the strict CLI status schema and
`hormuz.custody-control-event` v1 is the strict durable event schema. Both are
listed in `hormuz contract-manifest` and covered by compatibility fixtures.

This checkpoint proves authorization persistence, tenant isolation, database
role separation, exact approval thresholds, expiry, replay denial, and
rollback on invalid evidence. It does not execute KMS operations, change
customer IAM, delete or disable customer keys, implement all-administrator-loss
break glass, place custody events in the gateway audit chain, certify a cloud
deployment, establish production readiness, or close parent issue #17.
