# KMS custody and immutable audit anchors

Hormuz can use an external key service to envelope-encrypt provider
credentials and anchor metadata-only audit snapshots in immutable storage. The
first supported and certification-target profile is **customer-managed AWS KMS
keys plus an S3 bucket created with Object Lock enabled**. The interfaces are
provider-neutral; AWS is not a policy dependency. A deployment is certified
only after it completes its own live KMS/S3 evidence run.

This is an explicit operational control. It is not enabled by default, does
not store prompts or responses, and does not automatically migrate existing
environment variables.

## Required AWS profile

Install the optional adapter into the Hormuz service environment:

```bash
python -m pip install 'hormuz[aws]'
```

Use the normal AWS workload credential chain: an instance/task role, IRSA,
AWS SSO credential process, or another AWS SDK-supported workload identity.
Do not put an AWS access key or secret in `hormuz.json`.

Create customer-managed symmetric KMS encryption keys. Give each material
class its own key reference when that class is enabled:

- `provider_credential`
- `identity_connector_secret`
- `session_material`
- `approval_fingerprint`
- `data_encryption`

The initial gateway integration uses `provider_credential`; the audit sink uses
`data_encryption` for S3 SSE-KMS. Additional key purposes are reserved and
validated for the next secret-class migrations. AWS KMS automatic rotation is
compatible with existing envelopes. A manual move to another KMS key uses
`hormuz custody rewrap`, which calls KMS `ReEncrypt` and never prints the
secret or plaintext data key.

Create the audit bucket **with S3 Object Lock enabled at creation time**. Keep
bucket versioning enabled. Hormuz writes every anchored object with
`COMPLIANCE` retention and optional legal hold; standard S3 versioning alone
does not meet this boundary. The service identity needs the narrow KMS and S3
permissions for its configured keys and bucket, including KMS data-key,
decrypt, re-encryption, describe, and S3 Object Lock read/write operations.
Grant only the configured bucket/prefix and key ARNs; use AWS CloudTrail for
the AWS principal-level record of KMS and S3 API access.

## Configuration

All values below are identifiers or policy values, not credentials. Use real
customer-controlled key references and bucket names in deployment.

```json
{
  "key_custody": {
    "backend": "aws-kms",
    "region": "us-east-1",
    "key_references": {
      "provider_credential": "alias/hormuz-provider-credentials",
      "data_encryption": "alias/hormuz-audit-data"
    }
  },
  "audit_anchor": {
    "backend": "aws-s3-object-lock",
    "region": "us-east-1",
    "bucket": "example-hormuz-immutable-audit",
    "prefix": "hormuz/audit",
    "retention_days": 365,
    "legal_hold": false
  },
  "upstreams": {
    "openai": {
      "base_url": "https://api.openai.com",
      "api_key_envelope": "/etc/hormuz/openai.envelope"
    }
  }
}
```

`api_key_env` and `api_key_envelope` are mutually exclusive. An encrypted
provider credential requires a single-tenant gateway configuration and the
`provider_credential` KMS key purpose. Envelope files must be regular,
owner-only (`0600`) files; links, group/world-readable files, malformed
envelopes, unavailable KMS keys, and a purpose or tenant mismatch fail closed.

## Seal and rotate a provider key

First exercise the configured KMS keys for the configured tenant/purpose
contexts and validate AWS Object Lock readiness without creating an audit
object. This creates no Hormuz secret or audit artifact, though AWS may record
the KMS verification calls in CloudTrail:

```bash
hormuz --config /etc/hormuz/hormuz.json custody verify
```

Seal a source value from the service environment. Run the command first, then
make the variable available only in that process environment; do not put the
value on the command line or in shell history.

```bash
hormuz --config /etc/hormuz/hormuz.json custody seal \
  --purpose provider_credential \
  --input-env HORMUZ_OPENAI_KEY_TO_SEAL \
  --output /etc/hormuz/openai.envelope
```

After changing the upstream configuration to `api_key_envelope`, remove the
temporary source variable and run `hormuz doctor`. Hormuz decrypts the
credential only into the gateway process and registers that runtime value with
the existing secret-redaction boundary.

The envelope writer publishes a complete `0600` temporary file atomically, so
a failed replacement does not truncate the prior envelope. Keep the prior
encrypted file until the replacement has been validated and retained in the
organization's normal configuration/backup process.

To rotate to the currently configured KMS key for an envelope purpose:

```bash
hormuz --config /etc/hormuz/hormuz.json custody rewrap \
  --input /etc/hormuz/openai.envelope \
  --output /etc/hormuz/openai.next.envelope
```

Verify the new file with `hormuz doctor`, atomically replace the deployment
reference according to your configuration-management process, then retire old
KMS permissions only after all active envelopes and backups have been
re-encrypted and tested. Disabling or revoking a key fails gateway startup
closed; it does not cause a fallback to plaintext environment credentials.

## Anchor audit evidence

Anchoring is intentionally separate from local `audit-export` and runs only
when an operator asks for it:

```bash
hormuz --config /etc/hormuz/hormuz.json audit-anchor \
  --kind all \
  --since 2026-08-01T00:00:00Z
```

Hormuz validates current v2 metadata-only audit events, rejects mixed-tenant
or legacy rows, generates a strict SHA-256 chain, verifies it before upload,
and writes a unique, SSE-KMS-encrypted object with Object Lock `COMPLIANCE`
retention. The sink accepts only the canonical serialized artifact. It includes a conditional-create precondition so the same artifact
identifier cannot create a confusing second object version. The command prints
only backend, a random artifact identifier, counts, hashes, and object-version
metadata—never an S3 URL, tenant identifier, employee identity, prompt,
response, token, or credential.

The artifact verifier detects altered, deleted, reordered, duplicated, and
cross-tenant entries within the anchored artifact. The current snapshot model
does not prove that an event was not removed from a mutable local database
before an anchor runs, and it does not provide automated scheduling, legal-hold
release, retention-policy lifecycle administration, backup/PITR recovery, or
SIEM delivery. Those are still open release gates.

## Operational boundaries

Each anchor command is tenant-scoped and rejects mixed-tenant evidence. The
retention timestamp is computed from the gateway host's UTC system clock at
command time, then sent to S3 as an absolute timestamp; production hosts must
be time-synchronized. `legal_hold: true` applies a legal hold at object
creation. Hormuz does not automate hold release or retention extension; those
are controlled AWS operations. S3 Object Lock `COMPLIANCE` prevents shortening
retention or deleting a protected object version through normal account
operations, but it is not a substitute for account-level access controls,
backup/recovery practice, or independent audit review.
