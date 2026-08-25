# Signed OCI deployment contract

Hormuz's application deployment contract is one signed OCI digest. The image
runs the core `hormuz` package as numeric user and group `65532:65532`; it does
not contain customer configuration or policy data, credentials, usage data,
audit evidence, or the separately packaged context experiment.

`ghcr.io/xpounder-com/hormuz` is the first publication registry, not part of
the product contract. An operator may recursively mirror the exact manifest,
signature, and attestations to another OCI registry as long as the destination
manifest digest remains identical and the original release identity still
verifies.

The initial release platform is **`linux/amd64` only**. Issue
[#109](https://github.com/Xpounder-com/hormuz/issues/109) must close before a
multi-architecture manifest or general native ARM64 support claim is
published. Ceph-specific issue #68 cannot satisfy that runtime gate.

## Deterministic unsigned payload

[`Dockerfile`](../Dockerfile) pins the Dockerfile frontend and Python 3.14 slim
base by digest. It accepts only `linux/amd64`, resolves its build and runtime
Python wheels from reviewed exact-version/hash locks under `requirements/`,
does not compile bytecode, and performs no moving Debian update inside the
build. A fixable base vulnerability is addressed by reviewing a new base
digest, not by resolving a package index during a release build.

The Dockerfile-specific `Dockerfile.dockerignore` starts from an empty context
and admits only the Dockerfile, core packaging files including the Apache 2.0
license, the two OCI lock files, and the `hormuz/` package. It takes precedence over the repository's broader
Ceph-conformance ignore file. A local `hormuz.json`, `.env`, SQLite database,
test suite, verification tool, and `experiments/context/` cannot enter the
release build context through an incidental broad copy.

Build a local AMD64 image from the current commit:

```bash
version="$(python3 -c 'import pathlib, tomllib; print(tomllib.loads(pathlib.Path("pyproject.toml").read_text())["project"]["version"])')"
revision="$(git rev-parse HEAD)"
source_date_epoch="$(git show -s --format=%ct "$revision")"
docker build --platform linux/amd64 --provenance=false --sbom=false \
  --build-arg "HORMUZ_VERSION=$version" \
  --build-arg "VCS_REF=$revision" \
  --build-arg "SOURCE_DATE_EPOCH=$source_date_epoch" \
  --tag hormuz:local .
```

The release reproducibility proof builds two independent, no-cache OCI
archives and uses BuildKit timestamp rewriting. It requires byte-for-byte
identical archives, one `linux/amd64` manifest, matching version/revision
labels, complete blob hashes, and the same manifest digest:

```bash
HORMUZ_OCI_REPRODUCIBILITY_EVIDENCE_DIR="$(mktemp -d)" \
  ./tools/verify_oci_reproducibility.sh
```

This proves reproducibility under the exact recorded base, locks, Buildx,
BuildKit, source commit, and source-date boundary. It is not a universal claim
across unrecorded toolchains.

## SBOM and vulnerability evidence

Run the candidate evidence gate from the repository root:

```bash
HORMUZ_OCI_SUPPLY_CHAIN_EVIDENCE_DIR="$(mktemp -d)" \
  ./tools/verify_oci_supply_chain.sh
```

The gate generates a CycloneDX JSON SBOM, scans the exact candidate with Trivy
`0.74.0` pinned by immutable image digest, and writes:

- `hormuz.cdx.json` — package-level CycloneDX SBOM;
- `trivy-vulnerabilities.json` — complete raw scanner evidence;
- `summary.json` — candidate/scanner identity, evidence hashes, counts, policy,
  and verdict.

Missing, malformed, unsupported, or candidate-mismatched evidence fails
closed. A `HIGH` or `CRITICAL` finding with a scanner-reported fixed version
blocks release; all lower-severity and unfixed observations remain visible.
The vulnerability database refreshes at scan time, so an advisory update may
change the result without a source change. The evidence contains package/image
metadata, not prompts, responses, configuration, credentials, or customer
data.

## Protected release workflow

`.github/workflows/release-oci.yml` runs only for a strict `vMAJOR.MINOR.PATCH`
tag and fails unless all of these are true:

- the repository is exactly `Xpounder-com/hormuz`;
- the repository is public before any Sigstore operation can create durable
  external metadata;
- the tag is annotated, protected by GitHub rules, matches the package version,
  resolves to the event commit, and is reachable from `origin/main`;
- the workflow identity is exactly
  `Xpounder-com/hormuz/.github/workflows/release-oci.yml@refs/tags/<tag>`;
- the normalized payload rebuilds identically and the published digest equals
  the reproducibility digest;
- runtime smoke, SBOM, and vulnerability gates pass against that digest;
- the CycloneDX SBOM and bounded SLSA v1 predicate pass strict allowlisted
  schema, release-identity, private-path, and secret-pattern validation before
  any signing or attestation operation;
- the keyless image signature is written and independently verified through
  public Rekor;
- the keyless CycloneDX and SLSA v1 attestations use public Fulcio and timestamp
  services but no Rekor service, and are attached only to the private GHCR
  package during first publication;
- certificate extensions match the release workflow name, repository, source
  tag ref, source commit, and `push` trigger in addition to the exact subject;
- GHCR remains private and the semantic-version registry tag is either absent
  or already resolves to the exact verified digest; it is never reassigned.

The workflow first uses the immutable `sha-<commit>` locator. It adds the
semantic-version registry tag only after signature and attestation verification
passes. A rerun may accept the same tag at the same digest, but can never move
it to another digest. It never publishes a mutable `latest` tag.

## Signing identity and public disclosure

The release uses Cosign/Sigstore with GitHub Actions OIDC. It stores no
long-lived signing private key. Verification requires both:

```text
issuer:   https://token.actions.githubusercontent.com
identity: https://github.com/Xpounder-com/hormuz/.github/workflows/release-oci.yml@refs/tags/<tag>
```

The public image-signature Rekor entry may expose the artifact digest,
repository name, release workflow path, commit/ref, and signing event—not just
a generic identity. It must not contain source code, image layers, credentials,
private workspace paths, prompts/responses, or customer data. This disclosure
is an explicit tradeoff of the approved keyless profile.

The SBOM and SLSA predicate are not uploaded to Rekor. They are signed OCI
referrers in the first registry and remain private while the GHCR package is
private. If the package owner later makes that package public, those referrers
expose package names, versions, dependency and license metadata, and bounded
build metadata. The release gate therefore validates their complete schema and
all string values before they are signed or attached. Raw SBOM, provenance,
vulnerability, or verification payloads are not uploaded as workflow
artifacts; the workflow retains only allowlisted content-free summaries.

The `v0.1.1` package was made public only after its private release candidate
passed the complete gate. Verify the current public-alpha digest anonymously:

```bash
image="ghcr.io/xpounder-com/hormuz@sha256:1bbcca3490a7a5b004a880f42e8250acb91ce566a9c59f3263d7b279568efb5a"
identity="https://github.com/Xpounder-com/hormuz/.github/workflows/release-oci.yml@refs/tags/v0.1.1"
issuer="https://token.actions.githubusercontent.com"
commit="b9388cba8945dbdc86a55d79dd92283841aeecc4"

certificate_claims=(
  --certificate-identity "$identity"
  --certificate-oidc-issuer "$issuer"
  --certificate-github-workflow-name "Release signed OCI digest"
  --certificate-github-workflow-ref "refs/tags/v0.1.1"
  --certificate-github-workflow-repository "Xpounder-com/hormuz"
  --certificate-github-workflow-sha "$commit"
  --certificate-github-workflow-trigger "push"
)

cosign verify "${certificate_claims[@]}" "$image"
cosign verify-attestation --type cyclonedx \
  --insecure-ignore-tlog \
  --use-signed-timestamps \
  "${certificate_claims[@]}" \
  "$image"
cosign verify-attestation --type slsaprovenance1 \
  --insecure-ignore-tlog \
  --use-signed-timestamps \
  "${certificate_claims[@]}" \
  "$image"
```

Do not weaken verification to an unrestricted identity regular expression.
The expected protected tag is part of the certificate identity and ref claim.
For the two attestations, Cosign's explicitly named
`--insecure-ignore-tlog` option records the intentional absence of a Rekor
entry; verification still requires the exact Fulcio workload identity,
certificate transparency proof, and signed timestamp. Do not apply that option
to the image signature.

## Mirroring and rollback

A mirror procedure must copy the subject manifest and all OCI referrers,
including the Cosign signature and both attestations. After copying, resolve
the destination manifest, require the exact source digest, and repeat all three
Cosign verifications against the destination digest and original workflow
identity. A registry that changes the manifest digest or omits referrers has
not mirrored the Hormuz deployment contract successfully.

Rollback selects a previously verified signed digest directly. A mutable tag,
rebuild, re-sign under a different workflow, or ad hoc down-migration is not a
rollback substitute. Registry retention and deletion must preserve every
supported rollback digest and its referrers.

Retention is an operator-owned prerequisite, not a GHCR guarantee. Before a
digest becomes a supported rollback target, recursively copy its subject and
complete referrer set to a retained registry, compare every resulting digest,
and repeat the exact identity checks there. Periodic discovery must still find
the image signature and both attestations. A missing subject or referrer makes
that location ineligible for rollback even if a semantic tag remains.

Signer-identity retirement is a trust-policy operation; it does not erase or
rewrite historical Sigstore evidence. If the release workflow identity or its
tag-creation authority is compromised or retired, stop that path, publish an
advisory naming the affected identity and digests, and deny those values in
downstream deployment policy. A future release must use a newly reviewed,
protected workflow identity and a new immutable version tag. Existing Rekor
entries remain historical evidence and must not be deleted or represented as
revoked certificates. A verifier configured with a different expected
identity must fail closed before the artifact is admitted.

`v0.1.1` is the first supported signed Hormuz release, so there is no older
signed Hormuz digest against which to claim a cross-version rollback. Its live
release drill proves exact-digest selection, recursive mirroring, and original
identity verification. Issue
[#135](https://github.com/Xpounder-com/hormuz/issues/135) requires the first
real cross-version rollback after a second supported signed release exists;
storage rollback remains a separate compatibility and recovery boundary.

## Run with explicit runtime inputs

Prepare configuration outside the image. It names environment variables such
as `OPENAI_API_KEY`; it must not contain their values. For SQLite, set the
database path to `/var/lib/hormuz/hormuz.sqlite3`. A `0.0.0.0:8787` listener
also requires the external TLS proxy and private-hop credential described in
[DEPLOYMENT.md](DEPLOYMENT.md).

Create a durable volume owned by the fixed runtime identity, mount the real
configuration read-only, and inject secrets through the deployment platform:

```bash
docker volume create hormuz-data
docker run --rm --platform linux/amd64 --user 0:0 \
  --mount type=volume,src=hormuz-data,dst=/var/lib/hormuz \
  --entrypoint /usr/local/bin/python \
  hormuz:local -c 'import os; os.chown("/var/lib/hormuz", 65532, 65532)'

docker run --rm --platform linux/amd64 --name hormuz \
  --read-only \
  --tmpfs /tmp:mode=1777 \
  --cap-drop=ALL \
  --security-opt=no-new-privileges \
  --mount type=bind,src="$PWD/hormuz-config",dst=/etc/hormuz,readonly \
  --mount type=volume,src=hormuz-data,dst=/var/lib/hormuz \
  --env-file ./hormuz.runtime.env \
  --publish 127.0.0.1:8787:8787 \
  hormuz:local
```

Do not pass secrets in Docker build arguments, labels, command arguments, a
committed environment file, or the configuration JSON. PostgreSQL migrations
use a separately scoped operator credential before gateway startup; the image
does not perform schema changes automatically.

With a read-only root filesystem, `/tmp` is temporary writable storage and
`/var/lib/hormuz` is the explicit durable SQLite mount. Use `GET /health` for
liveness and `GET /ready` for traffic readiness; see
[OPERATIONS.md](OPERATIONS.md).

## Executable runtime proof and nonclaims

```bash
./tools/verify_oci_reference.sh
```

The proof confirms AMD64, numeric non-root identity, mounted inputs, read-only
root, versioned health/readiness, durable SQLite placement, and graceful
SIGTERM without contacting a model provider.

Signing the image does not certify Docker Compose, Kubernetes, Helm, public
TLS, a database, a cloud, customer configuration, HA, backup/PITR, disaster
recovery, or customer operations. Those claims require their own deployment
profile and release gates.
