# Ceph RGW self-hosted custody conformance

## Status

**Candidate only — not certified.**

Ceph RGW is Hormuz's first self-hosted certification target for the optional,
vendor-neutral S3-compatible Object Lock custody interface. Hormuz does not
require Ceph in its core package, runtime, configuration, or customer
deployment. An organization may use another S3-compatible Object Lock service
after completing a separate conformance run.

The target under test is:

```text
Ceph release: Tentacle 20.2.3
Image: quay.io/ceph/ceph@sha256:d195020de02512030118e772cef7859e92904e91eb4cb21acb503f8b94118137
```

The target has no certification status until an operator retains a passing
evidence record from the exact release/digest and attaches it to the relevant
Hormuz release issue or pull request.

## What the gate proves

`tools/verify_ceph_rgw_custody_conformance.py` is intentionally opt-in. It
first inspects the named local RGW container and refuses to continue unless it
is running the exact image digest above and reports `ceph version 20.2.3 ...
tentacle (stable)`. It then uses only explicit OpenBao and RGW credentials to:

1. verify two purpose-separated, tenant-bound OpenBao Transit data-key flows;
2. verify bucket versioning and Object Lock are enabled;
3. create and delete an unprotected control object version with the same RGW
   credential, proving a later protected-delete denial is not merely an IAM
   denial;
4. write an envelope-encrypted, metadata-only Hormuz audit artifact with
   `COMPLIANCE` retention, recover it, and validate its hash chain;
5. extend that retention successfully with the same credential, proving a
   later reduction denial is not merely an IAM denial;
6. prove an attempt to shorten its retention is denied;
7. prove an attempt to delete its protected object version is denied;
8. write a second retained artifact and verify its legal hold is on;
9. write a private, versioned, content-free evidence record only when every
   check passes.

The current evidence JSON is schema v2. It intentionally contains the candidate
release/digest and platform, the pinned `linux/amd64` runner's local
content-addressed image digest, check names, random artifact IDs, artifact
hashes, hashes of object versions, and nonclaims. It excludes the endpoint,
bucket, object key, tenant identifier, credential values, prompts, and
responses.

Schema v1 records remain readable as historical evidence. They lack runner
attestation and must not be rewritten in place; a new v2 run is required for a
current Ceph reference review.

## Lab prerequisites

This is a **disposable single-host Linux lab**, not a production deployment.
Cephadm requires Linux host facilities including systemd, a container engine,
and LVM-backed storage. Prepare the Ceph cluster and RGW service using the
official Cephadm procedure, pinning the target image above, then expose the
RGW S3 endpoint only on loopback for the test. Create the bucket with Object
Lock enabled at creation, with no bucket-level default retention, and a
dedicated RGW access key limited to that bucket and its Object Lock operations.
The credential must be able to delete an unprotected version and extend
retention; otherwise the runner cannot distinguish an authorization denial from
actual `COMPLIANCE` enforcement.

For a Ceph account/IAM user, grant only the operations the gate needs on the
test bucket: `s3:GetBucketLocation`, `s3:GetBucketVersioning`, and
`s3:GetBucketObjectLockConfiguration` on the bucket; plus `s3:PutObject`,
`s3:GetObject`, `s3:GetObjectVersion`, `s3:DeleteObject`,
`s3:DeleteObjectVersion`, `s3:GetObjectRetention`,
`s3:PutObjectRetention`, `s3:GetObjectLegalHold`, and
`s3:PutObjectLegalHold` on its objects. Do not grant account-wide listing or
bucket creation. Ceph evaluates versioned `HeadObject` through
`s3:GetObjectVersion`, so that action is required even when ordinary
`s3:GetObject` is present.

The one-OSD lab may report undersized/degraded placement groups when its
default replica count is two. That is an expected single-host limitation, not
evidence of redundant or production-ready storage.

Run a local OpenBao Transit service on the same host (or a loopback port) and
create two separate keys: one for `provider_credential`, one for
`data_encryption`. Use a short-lived token limited to those keys. The runner
never falls back to AWS credentials or a remote endpoint.

Record the active RGW container name or ID after Cephadm deploys it. The runner
will independently attest it, so an incorrect value fails before any retained
object is written.

## Run the gate

Run the gate from the dedicated, pinned `linux/amd64` container, even when the
Ceph lab host is ARM64. The wrapper builds a local content-addressed runner
image from an immutable Python base digest, records that resulting runner image
digest and architecture in evidence, and runs it with Linux host networking so
only the lab's loopback RGW and OpenBao endpoints are reachable.
Before it starts the runner, the wrapper independently checks the local RGW
container's exact release, digest, and platform. It passes only that
content-free attestation to the runner, so the runner never receives Docker's
privileged host socket.

Place the existing conformance environment values in a root-readable,
mode-`0600` environment file. Do not put the runner image values in that file:
the wrapper derives them after its build. Then invoke:

```bash
export HORMUZ_CEPH_RGW_CONFORMANCE_ENV_FILE=/secure/path/ceph-conformance.env
tools/run_ceph_rgw_custody_conformance_container.sh \
  --evidence-out /secure/path/ceph-rgw-custody-evidence.json
```

The environment file contains the previous `HORMUZ_RUN_CEPH_RGW_CUSTODY_CONFORMANCE`,
confirmation, RGW, OpenBao, and credential variables. Its RGW and OpenBao
endpoints must still be loopback-only. The exact confirmation is deliberate: a
successful gate leaves at least two objects retained for
`HORMUZ_CEPH_RGW_RETENTION_DAYS` (one day by default). Do not set the test
bucket or endpoint to a production custody location. Never place a secret
value in a shell command, source file, configuration file, evidence record, or
issue/PR comment.

`HORMUZ_CEPH_RGW_REGION` is the exact S3 location returned by
`GetBucketLocation`, not an inferred AWS geography. Use `us-east-1` only when
that is what the target RGW returns.

## Certification procedure

A successful command is an evidence input, not an automatic marketing claim.
For an actual certification decision:

1. retain the resulting evidence JSON in the release record;
2. verify its schema, exact target release/digest, `linux/amd64` runner digest,
   and all required check names;
3. review the local Cephadm and RGW logs under the organization's normal
   operational controls;
4. attach only the content-free record and a concise outcome to the issue/PR;
5. update this status and the release ledger only after that review.

## Nonclaims

The single-host target proves **RGW-level Object Lock enforcement**. It does
not protect against a root administrator deleting the host's disks, Docker
volumes, Ceph OSD data, or the lab itself. It also does not establish a
production storage design, host hardening, IAM/RBAC completeness, backup/PITR,
multi-host durability, HA/failover, SIEM delivery, or independent audit. Those
remain distinct enterprise-release gates.

The reference runner's `linux/amd64` architecture proves neither native ARM64
Hormuz runtime support nor a production deployment topology. ARM64 is tracked
separately if it is included in a future launch support commitment.
