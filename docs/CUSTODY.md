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

A deployment does not inherit verification merely by selecting a profile. It
must complete the profile's live conformance evidence run. This is an explicit operational
control: it is not enabled by default, does not store prompts or responses,
and does not automatically migrate existing environment variables.

The versioned active-core ownership inventory distinguishes material managed
by these custody adapters from credentials that must remain externally
injected to bootstrap the database, key service, or immutable store. Run
`python tools/verify_secret_inventory.py` and see
[SECRET_CUSTODY_INVENTORY.md](SECRET_CUSTODY_INVENTORY.md) for the exact
content-free classification and its nonclaims.

For tenant-scoped human authorization of envelope lifecycle operations, use
[CUSTODY_CONTROL.md](CUSTODY_CONTROL.md). Managed custody control separates
administrator approval, PostgreSQL evidence, the future machine executor, and
customer KMS authority. When enabled, it deliberately disables the legacy
direct verify/seal/rewrap CLI path until that executor is separately proven.

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

The repository's generic adapter tests do not verify a particular storage
product. OpenBao Transit plus Ceph RGW Tentacle is Hormuz's first **verified
self-hosted reference** for the exact custody/retention and rotation/recovery
behaviors. The target is optional and does not change the vendor-neutral
envelope-encryption or S3-compatible Object Lock product contracts. Its
content-free evidence, exact OpenBao/Ceph attestations, and pinned
`linux/amd64` runner provenance are published in
[#95](https://github.com/Xpounder-com/hormuz/issues/95). See
[CEPH_RGW_CONFORMANCE.md](CEPH_RGW_CONFORMANCE.md).

## Optional AWS profile

The adapter is available but **not yet live-certified**. Its customer-authorized
AWS KMS + S3 Object Lock conformance gate is tracked separately in
[issue #94](https://github.com/Xpounder-com/hormuz/issues/94); it does not
block the vendor-neutral core custody gate.

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

## Commit-time chain checkpoints

The same vendor-neutral Object Lock interface accepts the small
`hormuz.audit-chain-checkpoint` v1 artifact in addition to the older export
snapshot. This lets a scheduled, operator-controlled job anchor the current
per-organization commit-time chain without putting an S3 call on model-request
egress.

Opt into a local freshness bound only when an external anchor target is already
configured:

```json
{
  "audit_chain": {
    "maximum_anchor_age_seconds": 3600
  }
}
```

The value is a minimum of 60 seconds and maximum of 31 days. `GET /ready` and
`audit-chain status` inspect only local chain entries and successful local
checkpoint receipts. They never make an Object Lock request. Readiness reports
an evidence-health failure only when there are unanchored committed events
older than the bound; an idle tenant with no events is not overdue.

Run a checkpoint on the customer-controlled schedule:

```bash
hormuz --config /etc/hormuz/hormuz.json audit-chain anchor \
  --output /var/lib/hormuz/checkpoints/20260823T120000Z.json
```

The command first writes the exact canonical metadata-only checkpoint with
owner-only permissions, then performs the Object Lock write, then records the
receipt in the usage store. It prints only the backend, random checkpoint ID,
epoch/sequence, digests, and object-version metadata. A failed or ambiguous
Object Lock call is never retried automatically; inspect the protected store
before issuing a new checkpoint. The output file is deliberately retained for
later verification or an explicit restore/migration epoch. Scheduled jobs must
use a fresh, owner-only output path for every checkpoint; do not silently
overwrite a recovery artifact.

For a restore or migration epoch, use only the exact checkpoint recovered from
the protected Object Lock version and preserve its independent receipt/version
evidence. The local `audit-chain epoch` command validates canonical format and
chain binding; it does not by itself establish that a supplied local file was
externally retained.

The Object Lock profile protects a checkpoint tuple, not the request path.
It does not prove provider traffic that bypassed Hormuz, does not remove the
anchor-delay window for subsequent events, and does not turn a single-host
Ceph lab into host-root or production-retention protection.

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

For the Ceph RGW reference specifically, a single-host run proves RGW-level
Object Lock enforcement only. It does **not** protect against a root
administrator deleting the host's underlying disks, Docker volumes, or Ceph
data directories. That is an intentional nonclaim of the self-hosted
conformance target, not a limitation Hormuz hides from an operator.

## Live Ceph RGW self-hosted conformance

The verified RGW target is **Ceph RGW Tentacle 20.2.3**, attested at runtime to
this immutable image index:

```text
quay.io/ceph/ceph@sha256:d195020de02512030118e772cef7859e92904e91eb4cb21acb503f8b94118137
```

It is part of Hormuz's first **verified self-hosted reference** for the
constrained RGW-level Object Lock scope described here: exact OpenBao Transit
plus exact Ceph RGW. The content-free schema-v3 paired custody/retention record
and schema-v2 paired rotation/recovery record are published in
[#95](https://github.com/Xpounder-com/hormuz/issues/95).
The operator-provisioned lab is a disposable, single-host Linux Cephadm
environment with a loopback RGW endpoint and a local OpenBao Transit service.
The gate refuses arbitrary remote endpoints and refuses a running RGW or
OpenBao container whose image, release/version, or platform is not the exact
reference recorded in [CEPH_RGW_CONFORMANCE.md](CEPH_RGW_CONFORMANCE.md). It leaves
two retained objects behind: one to prove COMPLIANCE retention cannot be
shortened or deleted, and one with a legal hold. It also writes and deletes an
unprotected control version, and extends retained-object retention, so a later
denial cannot be explained away as missing RGW permissions. Native ARM64
runtime conformance is separately tracked in
[issue #68](https://github.com/Xpounder-com/hormuz/issues/68) and does not
block this reference verification unless it becomes a promised launch platform.
Run it only with a disposable Object-Lock-enabled bucket with no default
retention:

```bash
python -m pip install '.[self-hosted]'
export HORMUZ_RUN_CEPH_RGW_CUSTODY_CONFORMANCE=1
export HORMUZ_CEPH_RGW_CUSTODY_CONFIRMATION=I_UNDERSTAND_DISPOSABLE_OBJECT_LOCK_RETENTION
export HORMUZ_CEPH_RGW_ENDPOINT=http://127.0.0.1:7480
# Match GetBucketLocation; stock single-zone Ceph reports "default".
export HORMUZ_CEPH_RGW_REGION=default
export HORMUZ_CEPH_RGW_BUCKET=hormuz-ceph-conformance
export HORMUZ_CEPH_RGW_ACCESS_KEY=... # dedicated RGW credential, not an AWS credential
export HORMUZ_CEPH_RGW_SECRET_KEY=...
export HORMUZ_CEPH_RGW_CONTAINER=... # locally running RGW container name or ID
export HORMUZ_CEPH_OPENBAO_CONTAINER=... # locally running exact OpenBao container name or ID
export HORMUZ_CEPH_OPENBAO_ENDPOINT=http://127.0.0.1:8200
export HORMUZ_CEPH_OPENBAO_TOKEN=...
export HORMUZ_CEPH_OPENBAO_PROVIDER_KEY=hormuz-conformance-provider
export HORMUZ_CEPH_OPENBAO_DATA_KEY=hormuz-conformance-audit
python tools/verify_ceph_rgw_custody_conformance.py \
  --evidence-out /secure/path/ceph-rgw-custody-evidence.json
```

The output record deliberately excludes endpoints, bucket names, organization
identifiers, credentials, prompts, responses, and object keys. A passing record
is necessary but not, by itself, an unrestricted production-certification claim:
attach it to the release issue/PR and review the exact target digest before
changing the target status. Full environment preparation, evidence semantics,
and nonclaims are in
[CEPH_RGW_CONFORMANCE.md](CEPH_RGW_CONFORMANCE.md).

### Self-hosted Transit key-version rotation and artifact recovery

The bounded self-hosted custody checkpoint in
[issue #69](https://github.com/Xpounder-com/hormuz/issues/69) is completed in
[PR #70](https://github.com/Xpounder-com/hormuz/pull/70). Its final
content-free exact-pair live evidence is published in
[#95](https://github.com/Xpounder-com/hormuz/issues/95). It does not change
the normal gateway runtime: the runtime's OpenBao token remains a
data-plane credential and must have no rotation authority. A separately scoped,
short-lived **lab administrator** token is the only credential permitted to
rotate the same named Transit key versions.

The opt-in recovery gate creates only a synthetic in-memory provider-credential
fixture and a metadata-only audit artifact. It then rotates the existing
provider-credential and data-encryption Transit keys, creates fresh recovery
clients, and proves that the pre-rotation envelope and exact retained artifact
remain recoverable. It also proves a same-key provider envelope can be rewrapped
under the newer key version. The retained audit artifact is never rewritten.

The runtime token must be able to use only the named data-key operations needed
by the proof and query its own capabilities. For both named keys, its effective
capabilities for `transit/keys/<key>/rotate` must be exactly `deny`. The separate
administrator token needs only the two named rotate operations. Do not grant a
wildcard Transit policy to either credential.

Run it only in the same disposable, loopback-only Ceph/OpenBao lab. It creates
one additional Object Lock `COMPLIANCE` object retained for at least one day:

```bash
export HORMUZ_CEPH_CUSTODY_ROTATION_RECOVERY_ENV_FILE=/secure/path/ceph-rotation-recovery.env
tools/run_ceph_rgw_custody_rotation_recovery_container.sh \
  --evidence-out /secure/path/ceph-rgw-custody-rotation-recovery.json
```

The environment file supplies the explicit opt-in/acknowledgement, RGW, and
OpenBao values, including `HORMUZ_CEPH_OPENBAO_CONTAINER`, distinct
`HORMUZ_CEPH_OPENBAO_RUNTIME_TOKEN` and
`HORMUZ_CEPH_OPENBAO_ADMIN_TOKEN`, plus distinct provider, data, and deliberately
unavailable key names. The result is a private, content-free evidence record;
it excludes endpoints, bucket/object identifiers, tokens, credentials, and
plaintext fixture data. Full prerequisites and nonclaims are in
[CEPH_RGW_CONFORMANCE.md](CEPH_RGW_CONFORMANCE.md).

This checkpoint is not OpenBao backend backup, seal/master-key recovery,
customer RPO/RTO, high availability, production KMS/BYOK certification, or
host-root/disk-administrator protection. Its successful lab run closed only
the narrowly stated recovery evidence issue; the broader #17 custody gate
remains open.

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
