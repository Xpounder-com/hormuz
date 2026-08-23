# Customer-controlled custody and immutable audit anchors

Hormuz can use an external key service to envelope-encrypt provider
credentials and anchor metadata-only audit snapshots in immutable storage. The
interfaces are provider-neutral; AWS is not a policy dependency. There are two
explicit profiles:

- **Self-hosted:** OpenBao Transit plus a customer-operated S3-compatible
  Object Lock service. This needs no cloud account. Hormuz encrypts the entire
  audit artifact before it reaches the object store.
- **AWS (optional):** customer-managed AWS KMS keys plus a bucket created with
  S3 Object Lock enabled.

A deployment is not certified merely by selecting a profile. It must complete
the profile's live conformance evidence run. This is an explicit operational
control: it is not enabled by default, does not store prompts or responses,
and does not automatically migrate existing environment variables.

## Account-free self-hosted profile

Install the optional S3 wire-protocol adapter into the Hormuz service
environment:

```bash
python -m pip install 'hormuz[self-hosted]'
```

The dependency is used only to speak the S3-compatible protocol. It does not
create, discover, or fall back to an AWS account or ambient AWS credentials.

Run OpenBao Transit as the key authority. Hormuz receives a short-lived random
data key only to perform local AES-256-GCM envelope work; it never receives or
stores a Transit master key. Configure one distinct Transit key name for each
enabled material class, including `provider_credential` and
`data_encryption`.

The object store must provide Object Lock `COMPLIANCE` retention. The
`custody verify` command checks these no-write prerequisites:

- bucket versioning enabled;
- Object Lock enabled for the bucket;
- the configured bucket region.

Every `audit-anchor` write requests `COMPLIANCE` retention and optional legal
hold. The opt-in live conformance test is the evidence that a particular
storage product actually enforces those operations.

Use a separate S3 credential for the immutable-audit bucket and give it only
the bucket/prefix and Object Lock operations required by Hormuz. The OpenBao
token and the S3 access/secret values are process-environment secrets, never
JSON configuration values. HTTP is allowed only for loopback development;
remote OpenBao and object-store endpoints must use HTTPS.

```json
{
  "key_custody": {
    "backend": "openbao-transit",
    "endpoint_url": "https://openbao.internal.example",
    "token_env": "HORMUZ_OPENBAO_TOKEN",
    "transit_mount": "transit",
    "key_references": {
      "provider_credential": "hormuz-provider-credentials",
      "data_encryption": "hormuz-audit-data"
    }
  },
  "audit_anchor": {
    "backend": "s3-compatible-object-lock",
    "endpoint_url": "https://audit-store.internal.example",
    "region": "us-east-1",
    "bucket": "example-hormuz-immutable-audit",
    "prefix": "hormuz/audit",
    "retention_days": 365,
    "legal_hold": false,
    "access_key_env": "HORMUZ_AUDIT_S3_ACCESS_KEY",
    "secret_key_env": "HORMUZ_AUDIT_S3_SECRET_KEY"
  }
}
```

The sealed object contains a versioned Hormuz encrypted-envelope JSON payload,
not the canonical audit artifact. Its Object Lock metadata binds the encrypted
payload digest and the artifact's hash-chain head; the object key uses a hash
of the tenant identifier. This is deliberate: a storage administrator can see
that an object exists, but does not receive employee, event, or audit-artifact
contents merely by reading it.

Before sealing a credential or anchoring evidence, validate the live custody
profile without writing an audit object:

```bash
hormuz --config /etc/hormuz/hormuz.json custody verify
```

The repository's generic adapter tests do not certify a particular storage
product. A named self-hosted reference is added only after an opt-in,
disposable conformance run proves actual COMPLIANCE-mode retention and legal
hold behavior against that product and version.

## Optional AWS profile

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
`provider_credential` custody key purpose. Envelope files must be regular,
owner-only (`0600`) files; links, group/world-readable files, malformed
envelopes, unavailable KMS keys, and a purpose or tenant mismatch fail closed.

## Seal and rotate a provider key

First exercise the configured custody keys for the configured tenant/purpose
contexts and validate Object Lock readiness without creating an audit object.
This creates no Hormuz secret or audit artifact. An AWS profile may record the
verification calls in CloudTrail:

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

To rotate to the currently configured custody key for an envelope purpose:

```bash
hormuz --config /etc/hormuz/hormuz.json custody rewrap \
  --input /etc/hormuz/openai.envelope \
  --output /etc/hormuz/openai.next.envelope
```

Verify the new file with `hormuz doctor`, atomically replace the deployment
reference according to your configuration-management process, then retire old
key-service permissions only after all active envelopes and backups have been
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
and writes a unique Object Lock `COMPLIANCE` object. The self-hosted profile
stores a complete envelope-encrypted artifact; the AWS profile uses SSE-KMS.
The sink accepts only the canonical serialized artifact. It includes a
conditional-create precondition so the same artifact identifier cannot create
a confusing second object version. The command prints only backend, a random
artifact identifier, counts, hashes, and object-version metadata—never an S3
URL, tenant identifier, employee identity, prompt, response, token, or
credential.

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
are controlled customer storage operations. S3 Object Lock `COMPLIANCE`
prevents shortening retention or deleting a protected object version through
normal account operations, but it is not a substitute for storage-administrator
access controls, backup/recovery practice, or independent audit review.

## Live AWS conformance evidence

The repository includes an opt-in test at
`tests/test_aws_custody_live.py`. It is deliberately skipped in normal CI.
Run it only against a customer-controlled, non-production AWS test account
after creating the customer-managed keys and Object-Lock-enabled bucket:

```bash
python -m pip install '.[aws]'
export HORMUZ_RUN_AWS_CUSTODY_CONFORMANCE=1
export HORMUZ_AWS_CUSTODY_CONFIRMATION=I_UNDERSTAND_OBJECT_LOCK_RETENTION
export HORMUZ_AWS_CUSTODY_REGION=us-east-1
export HORMUZ_AWS_CUSTODY_BUCKET=your-dedicated-object-lock-test-bucket
export HORMUZ_AWS_CUSTODY_PROVIDER_KEY=alias/hormuz-test-provider
export HORMUZ_AWS_CUSTODY_DATA_KEY=alias/hormuz-test-data
python -m unittest -v tests.test_aws_custody_live
```

It validates customer-managed key metadata and real tenant-bound KMS data-key
operations, then writes one metadata-only audit artifact and verifies the
retained S3 object version, `COMPLIANCE` mode, SSE-KMS, and retention date. The
test intentionally does **not** delete the object. It requires an explicit
acknowledgement because the object remains retained for at least one day;
`HORMUZ_AWS_CUSTODY_RETENTION_DAYS` may increase that period. Use an AWS SSO,
role, or workload identity—never an access key embedded in source or config.
