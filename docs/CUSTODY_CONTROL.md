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
3. A separately deployed, separately credentialed custody executor may consume
   one exact, authorized, unexpired intent. Routine intents perform limited
   envelope work; destructive intents append a governed Hormuz lifecycle event
   and derived restriction, not an external KMS or provider-administration
   action. The normal gateway runtime and human-administration CLI do not
   become that executor.
4. The customer key service remains authoritative for key policy, protection,
   disablement, and deletion. Hormuz never edits customer IAM policy.

## Authority boundaries

| Principal | Hormuz authority | Explicitly absent |
| --- | --- | --- |
| `custody_admin` | Manage tenant custody administrators; approve exact lifecycle intents; inspect metadata-only status | Inference, policy, identity, customer IAM/KMS administration, plaintext access |
| Custody-control service | Persist administrators, intents, approvals, and events | KMS calls, secret material, policy changes, gateway traffic |
| Gateway runtime | Existing configured data-plane decrypt/generate operations only, subject to its leased lifecycle projection | Custody administration, projection writes, and governed lifecycle execution |
| Custody executor | Consume one authorized, unexpired routine or destructive intent; register immutable configured asset generations; append attempt/lifecycle/attestation evidence | Human authorization, direct projection edits, provider-side credential revocation, customer-IAM/KMS changes, authority changes |
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
  },
  "custody_executor": {
    "postgres_executor_dsn_env": "HORMUZ_CUSTODY_EXECUTOR_DSN",
    "postgres_executor_role": "hormuz_custody_executor",
    "pending_attempt_ttl_seconds": 900
  },
  "custody_retention": {
    "retention_days": 365,
    "legal_hold": false
  }
}
```

Destructive lifecycle enforcement is opt-in through a tenant-scoped private
asset catalog. The catalog maps immutable IDs to local paths and customer key
references; Hormuz writes only the tenant-qualified ID, generation, and
binding fingerprint to durable evidence. This abbreviated configuration shape
shows the required links:

```json
{
  "custody_lifecycle": {
    "freshness_lease_seconds": 5,
    "assets": [
      {
        "asset_type": "provider_credential",
        "asset_id": "openai-primary",
        "generation": 1,
        "binding": {"protocol": "openai"}
      },
      {
        "asset_type": "key_reference",
        "asset_id": "provider-credential-2026",
        "generation": 1,
        "binding": {
          "purpose": "provider_credential",
          "key_reference": "customer-openbao-or-kms-key-reference"
        }
      },
      {
        "asset_type": "envelope",
        "asset_id": "openai-primary-envelope",
        "generation": 1,
        "binding": {
          "path": "/etc/hormuz/openai.envelope",
          "provider_credential_asset_id": "openai-primary",
          "provider_credential_generation": 1,
          "key_reference_asset_id": "provider-credential-2026",
          "key_reference_generation": 1
        }
      }
    ]
  }
}
```

An asset ID and generation are never reused for a changed binding. Asset IDs
are opaque ASCII identifiers (`A-Z`, `a-z`, `0-9`, `.`, `_`, `-`), never a
path, key reference, or credential label. Hormuz
requires exactly one configured provider-credential asset for each upstream,
an envelope asset for each encrypted upstream credential, and one configured
current key-reference asset for each custody purpose. The current lifecycle
configuration is intentionally single-tenant; deploy a tenant-scoped gateway
configuration when enabling it.

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

`custody_retention` is also a bootstrap input, not an everyday configuration
override. It is required before managed custody can initialize; missing,
malformed, or local-mode retention configuration fails closed. PostgreSQL's
`clock_timestamp()` supplies each custody-evidence `occurred_at` and stores an
immutable `retain_until` derived from the tenant's persisted retention days.
Later file/configuration changes cannot shorten existing records or alter their
legal-hold state. The only supported retention values are one to 36,500 calendar
days and a Boolean initial legal hold.

Before schema migration, the database operator must create both
`hormuz_custody_control` and `hormuz_custody_executor` as distinct restricted
roles with `NOINHERIT` and without superuser, database-creation, role-creation,
or `BYPASSRLS` privileges. A separately deployed executor login may assume only
the executor role; do not place its DSN or key-service credential in the normal
gateway deployment. PostgreSQL schema v8 grants the control role custody
authority and status reads. The executor receives only tenant-scoped
authorization metadata, a Boolean two-active-approver check, immutable
asset-registration and attempt/lifecycle/attestation insert surfaces, and no
direct projection update or delete privilege. Runtime receives only the reads
needed to verify asset fingerprints and load the derived projection.
Policy-control receives no custody-table access; neither custody role receives
usage or policy-control write access.

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
input path or resolver owned only by the executor. The administrator authorizes
only the SHA-256 of that protected handle. Hormuz custody-control storage,
execution roots/events, status, and logs contain no plaintext, ciphertext,
credential value, prompt, response, protected-input handle, target descriptor,
or parameter descriptor.

## Governed executor and lifecycle contract

The executor is a machine service boundary, not a human-administration
command. It accepts an in-memory `CustodyExecutionRequest` containing the
organization, operation ID, operation, target descriptor, parameter
descriptor, and—for `seal_envelope` only—a protected-input reference. Canonical
JSON SHA-256 values for those descriptors must exactly match the already
authorized intent. The raw descriptors and reference are transient executor
inputs; only their hashes are durable.

Immediately before a key-service, protected-input, filesystem, object-store,
or governed lifecycle effect, the executor atomically writes one immutable
`hormuz.custody-execution-attempt` root and its `pending` event under the
tenant's RLS scope. It verifies that the requester remains active and that the
intent is authorized, unexpired, and otherwise exact. A destructive claim also
requires two still-active distinct approvers at the database write boundary.
A new UUID `execution_id` identifies one attempt. PostgreSQL repeats the exact
authorization check in an insert trigger, so the executor credential cannot
manufacture a mismatched attempt root through a raw database write.

The only state transitions are:

```text
pending -> succeeded
pending -> failed
pending -> outcome_unknown
```

The terminal transition appends exactly one immutable event; neither the root
nor an earlier event is rewritten. A known pre-effect failure becomes `failed`.
An ambiguous provider, protected-input, filesystem, network, or finalization
failure leaves the root `pending`; the stale-attempt sweeper later appends
`outcome_unknown`. Hormuz never automatically replays the side effect. A new
effect needs a new human authorization and therefore a new operation ID.

The first concrete routine runner uses the existing vendor-neutral envelope
interfaces: `seal_envelope`, `rewrap_envelope`, and `verify_restore`. The
supplied owner-only-file resolver is a reference protected-input path;
production deployments can supply a separately reviewed resolver without
widening the ledger.

For a restrictive destructive operation, the executor first persists one
content-free proposed projection version and admission barrier. Every active
gateway installs that barrier locally before acknowledging it. Only then do
the terminal execution event, one immutable `hormuz.custody-lifecycle-event`
v1 record, its tenant hash-chain head, barrier activation, and derived
runtime-projection update commit in one PostgreSQL transaction.
Administrators and the normal runtime have no permission to edit the
projection directly. A failed activation transaction leaves the attempt and
prepared barrier intact rather than creating a partial restriction or
automatically replaying work.

This release serializes restrictive lifecycle changes to one prepared barrier
per organization. Destructive changes are rare control-plane operations, and
tenant serialization keeps the acknowledgement set and recovery path exact;
another restrictive operation fails closed until the first activates or is
resolved by a governed recovery event.

The meanings are deliberately narrow:

- `disable_provider_credential` stops Hormuz selecting that exact configured
  provider-credential generation. It does not revoke the provider API key.
- `retire_envelope` prevents future Hormuz selection of that envelope generation
  while retaining its ciphertext.
- `retire_key_reference` is **write retirement**. It prevents new sealing or
  rewrapping under that key generation but preserves decrypt/recovery access.
  It requires an active replacement key for the same custody purpose,
  successful rewrap evidence for every known non-retired envelope registered
  under that key, and a successful
  restore-verification attestation after those rewraps before the retirement
  event can commit. The catalog retains those envelope-to-key links as opaque
  asset IDs and fingerprints only—never filesystem paths or key references.
- `resolve_recovery` appends a separate two-person-approved lifecycle event
  using exactly `confirmed_applied`, `confirmed_not_applied`, or
  `compensating_action_completed`. It never rewrites the original
  `outcome_unknown` execution event.

If an uncommitted prepared barrier belongs to that unknown attempt,
`confirmed_not_applied` or `compensating_action_completed` may resolve and
release it in the same append-only recovery transaction. It was never an
active restriction, so this does not reactivate a committed asset.
`confirmed_applied` is rejected for such a barrier because Hormuz cannot claim
an uncommitted logical restriction was already applied.

Lifecycle restrictions are monotonic in this release: they may remove future
Hormuz use but never silently restore it. There is no reactivation operation.
The executor performs no provider-side revocation, customer KMS/IAM mutation,
customer-key disablement/deletion, envelope deletion, or break-glass recovery.

## Runtime projection

The immutable lifecycle events are authoritative. The runtime projection is
derived state maintained only by the transaction trigger. Before a gateway with
`custody_lifecycle` starts, it verifies every configured asset ID/generation
and fingerprint was registered through the restricted executor boundary, then
loads one tenant-qualified projection snapshot. The machine-only initial
registration command is:

```bash
hormuz --config /etc/hormuz/hormuz.json custody executor register assets
```

Run it only in the executor deployment where
`HORMUZ_CUSTODY_EXECUTOR_DSN` is available. It accepts no actor, approval,
plaintext, provider credential, KMS operation, or arbitrary organization ID;
it registers only the catalog in that configuration.

The gateway reads its local immutable projection and barrier set for normal
provider selection; it does not query PostgreSQL on every request. PostgreSQL
`LISTEN`/`NOTIFY` reduces invalidation latency and a durable background scan is
the authority. The coordinated flow is:

1. prepare one restrictive projection version and affected-asset barrier;
2. notify active replicas;
3. install the barrier locally, then persist each replica acknowledgement;
4. atomically activate only after every replica with an unexpired lease has
   acknowledged;
5. fence a disconnected or stale replica before its lease can be excluded;
6. release the local barrier only after the active projection contains the
   restriction, or after an explicit governed not-applied recovery event.

The serving lease is fixed at five seconds. It is a partition-safety backstop,
not the normal propagation mechanism. A replica starts its local monotonic
lease before the database round trip, so a delayed response cannot extend its
authority beyond the database lease. Any synchronization failure immediately
makes `/ready` unhealthy; once the lease expires, admission remains fail
closed without a synchronous database read from the request path. Startup
must synchronize, install any prepared barriers, and acknowledge them before
the gateway advertises readiness.

A restriction that commits is therefore already blocked on every acknowledged
replica; an excluded replica has exhausted its local authority. A request
pinned before activation may finish. A fresh process also does not resolve a
retired encrypted envelope into memory at startup; the ciphertext remains
available only to the governed recovery path.

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

The custody-administration CLI never accepts a self-asserted actor or plaintext
authorization field. It authenticates through the Hormuz service boundary. In
managed mode, the legacy direct `custody verify`, `custody seal`, and `custody
rewrap` commands fail with `custody_governed_executor_required`; an approval is
not silently executed by the administrator CLI process. The separate
`custody executor register assets` command is deliberately machine-only and
does not accept administrator credentials or execution input.

### Custody evidence export and deletion denials

The authenticated control service can export the tenant's custody evidence
without exposing ciphertext, envelope paths, KMS references, prompts, responses,
or secret values:

```bash
hormuz --config /etc/hormuz/hormuz.json custody evidence export \
  --organization acme > acme-custody-evidence.json
```

The export is `hormuz.custody-evidence-export` v1. It contains strict v2
commit-time chain entries for custody source records plus each source record's
immutable `retain_until` and legal-hold state. It is a tenant custody export,
not an organization-wide usage export; its entries retain their positions in the
organization's global audit chain and must be verified with the normal chain
verifier and a trusted checkpoint when independent tamper evidence is required.

Hormuz has no evidence-delete command. The only deletion-related control is a
content-free check which records a new immutable `hormuz.custody-deletion-event`
v1 with `decision: deletion_blocked`:

```bash
hormuz --config /etc/hormuz/hormuz.json custody evidence deletion check \
  --organization acme \
  --source-schema-id hormuz.custody-control-event \
  --source-schema-version 1 \
  --source-event-id <immutable-source-event-id>
```

It returns `retention_active`, `legal_hold_active`, or
`strong_approval_required`. The last result is still a denial: expiration never
grants a delete entitlement. A future destructive deletion design would need a
separate strong-approval contract, while externally anchored Object Lock
artifacts remain outside Hormuz's deletion scope. The normal gateway runtime
has no delete, retention-shortening, or chain-write privilege.

The last active administrator cannot be revoked through the ordinary path. If
all administrators are lost through an external database or disaster event,
ordinary operations fail with `custody_break_glass_required`. This release has
no custody break-glass recovery command. Adding one requires its own reviewed
credential, procedure, and evidence boundary.

## Contracts and nonclaims

`hormuz.custody-control-status` v3 is the current strict CLI status schema;
v1 and v2 remain validator-compatible historical shapes. Status v3 adds at
most the latest 100 metadata-only attempt records and accepts the governed
`hormuz.custody-execution-attempt` v2 shape; v1 remains the historical
routine-only attempt shape. Each append-only execution state record remains
`hormuz.custody-execution-event` v1. The separately hash-linked lifecycle
record is `hormuz.custody-lifecycle-event` v1. `hormuz.custody-control-event`
v1 remains the human-authority event schema. `hormuz.custody-envelope-attestation`
v1 and `hormuz.custody-deletion-event` v1 are strict metadata-only source
records. Each is wrapped only by the strict source-allowlisted
`hormuz.commit-audit-chain-entry` v2 format; version-1 chain entries remain
untouched for historical gateway evidence. All are listed in `hormuz
`contract manifest` and covered by compatibility fixtures.

This checkpoint proves governed destructive lifecycle authorization,
two-current-administrator enforcement, immutable asset generation identity,
atomic lifecycle-event/projection commits, coordinated multi-replica admission
barriers and acknowledgements, five-second partition fencing, write-retirement
prerequisites, append-only recovery resolution, tenant isolation, and no
automatic replay. It does **not** revoke a provider key,
change customer IAM, disable or delete a customer key, delete ciphertext,
implement all-administrator-loss break glass, certify a cloud deployment,
establish production readiness,
or close parent issue #17.
