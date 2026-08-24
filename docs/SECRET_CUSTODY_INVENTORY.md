# Active-core secret custody inventory

Hormuz ships a versioned, content-free inventory of the operational secret
sources used by the active gateway core. The inventory records identifiers and
custody policy only. It never reads, hashes, copies, or serializes a secret
value.

The packaged artifact is `hormuz/secret-inventory-v1.json`. Verify it against
the current source tree with:

```bash
python tools/verify_secret_inventory.py
```

The command reports only schema version, source counts, managed-material count,
and the inventory-file SHA-256. CI fails when a direct environment read, an AWS
ambient-credential entrypoint, a key purpose, or a managed-material source
coordinate/classification no longer matches the reviewed inventory. Existing
custody integration tests separately prove encryption and recovery behavior.

## Ownership boundary

The active core has two different custody categories:

1. **Hormuz-managed protected material.** Provider credentials may be stored
   in owner-only encrypted envelope files. Metadata-only audit artifacts use
   the `data_encryption` purpose before Object Lock storage in the self-hosted
   profile, or customer KMS service encryption in the optional AWS profile.
2. **Externally injected access credentials.** Static identity tokens, ingress
   credentials, PostgreSQL DSNs, policy- and custody-administrator credentials,
   policy-recovery credentials, custom redaction values, the OpenBao token,
   and S3-compatible credentials remain owned by the customer deployment
   secret manager or the authorized operator process.
   Hormuz holds them only in process memory for their configured consumer.

The second category must not be recursively placed behind the same service it
is needed to access. For example, Hormuz cannot use OpenBao Transit to decrypt
the OpenBao token required to reach Transit, and it cannot use a database-backed
control plane to recover the database credential required to open that control
plane. These bootstrap credentials require customer-controlled injection,
   least-privilege service identities, and deployment-level rotation.

This is an inventory of configured deployment, operator, and managed-material
sources. Transient HTTP bearer credentials are authenticated in the request
path and are not deployment custody sources; their values are never added to
this artifact or persisted by the inventory mechanism.

AWS custody uses the SDK ambient workload-identity chain. Hormuz configuration
does not accept a static AWS access key. The inventory tracks the two active SDK
entrypoints without inspecting the credential selected by the SDK.

## Purpose status

| Key purpose | Status in active core | Current consumer |
| --- | --- | --- |
| `provider_credential` | Active | Gateway provider-credential envelopes |
| `data_encryption` | Active | Metadata-only immutable audit artifacts |
| `identity_connector_secret` | Reserved | No active-core managed material |
| `session_material` | Reserved | Browser-session work remains deferred |
| `approval_fingerprint` | Reserved | Approval workflow is outside the reduced core |

Reserved means the name and separation requirement are retained, but the core
does not claim to store, encrypt, rotate, or audit that material. Static gateway
identity tokens are externally injected credentials; they are not mislabeled as
an implemented identity-connector envelope migration.

## Rotation and revocation ownership

- Provider credential operators replace an environment-injected credential or
  use the custody operator path to seal and rewrap an envelope.
- Database operators rotate the distinct runtime, migration, policy-control,
  and custody-control DSNs in the customer secret manager and roll the affected
  gateway or control-plane process.
- Identity operators rotate static or short-lived administrator credentials;
  the policy and custody services continue to authorize the resulting principal
  rather than trusting an actor name supplied to the CLI.
- Policy-recovery operators separately protect and rotate the opt-in break-glass
  credential.
- Custody operators rotate key-service authorization and purpose-specific key
  versions. The ordinary OpenBao runtime token has data-key operations and no
  rotation authority; the separately scoped administrator has rotation and no
  data-plane authority.
- Object-storage operators rotate the dedicated S3-compatible access pair and
  retain only the bucket/prefix and Object Lock permissions required by Hormuz.

Environment-source rotation currently requires a controlled process restart;
Hormuz does not silently fall back from an unavailable encrypted source to an
environment source. Envelope rewrap keeps the protected provider credential
encrypted from the administrator.

## Contract and nonclaims

The inventory is an internal release-gate contract, not a public secret API or
a replacement for a customer secret manager. Its entries use closed enums,
strict object fields, safe identifiers, and exact source coordinates. Unknown
fields—including a field that attempts to carry a secret value—fail closed with
a stable content-free error.

This checkpoint inventories the tenant-scoped `custody_admin` authorization
service and its separate database and administrator credentials. It does not
grant inference, policy, identity, KMS, IAM, or gateway-runtime entitlement;
execute an approved lifecycle operation; add custody events to the
per-organization gateway audit chain; implement all-administrator-loss
break-glass recovery; migrate reserved purpose classes; certify a customer
cloud environment; or close issue #17. Customer KMS authority and the future
narrowly permissioned custody executor remain separate security boundaries.
