# Signed private container releases

Hormuz has a tag-driven release contract for a private, signed reference image in GitHub Container Registry. The workflow is release plumbing, not evidence that a release exists: no package or GitHub release is claimed until a qualifying tag run completes and the resulting digest, signature, provenance, package visibility, and release assets are observed.

## Publication contract

`.github/workflows/release.yml` accepts only a `push` of a strict annotated `vX.Y.Z` tag when all of these conditions hold:

- the workflow repository is exactly `Xpounder-com/hormuz`;
- the full tag ref and 40-character commit SHA agree with the event;
- `pyproject.toml` contains exactly the same `X.Y.Z` version;
- the annotated tag resolves to the event commit;
- that commit is reachable from `origin/main`; and
- source verification and publication run only on GitHub-hosted runners; and
- the complete source, installed official-client, frozen benchmark, distribution, restricted-container smoke, SBOM, and high/critical vulnerability gates pass.

The workflow then builds `linux/amd64` and `linux/arm64`, pushes an untagged candidate by digest, validates the pulled `linux/amd64` source/version/revision labels and numeric non-root user, signs the exact multi-platform digest with GitHub Actions OIDC, renders and signs a content-free SLSA provenance v1 predicate, and independently verifies both the signature and exact predicate. It also proves that the package is private and linked to `Xpounder-com/hormuz` before creating either the version or revision alias.

There is no `latest` tag. A version alias and full-revision alias are accepted only when they resolve to the verified digest. A rerun reuses an existing version digest only after the source/version/revision labels match the tag and commit; it cannot repoint that version alias to a rebuild.

Privileges are split:

- source verification receives only `contents: read`;
- image publication receives `contents: read`, `packages: write`, and `id-token: write`, but cannot create a GitHub release; and
- the final evidence job receives `actions: read` and `contents: write`, but no package or OIDC permission.

Every third-party or first-party action is pinned to a reviewed immutable commit. Release evidence is metadata-only and contains the repository, tag, commit, image digest, signature identity, provenance URL, package visibility, run identity, and hashes of successful verification outputs—not prompts, responses, source content, filenames from customer work, or credentials.

Xpounder currently uses GitHub Team, while GitHub's first-party artifact attestations for a private repository require Enterprise Cloud. The implemented current-plan path therefore uses Cosign for both the image signature and signed SLSA predicate rather than invoking a GitHub attestation action that is known to be unavailable.

Public-good Sigstore keyless signing and attestation can place the workflow identity, release tag, image digest, certificate, predicate, and timestamp metadata in a publicly inspectable transparency service even when the repository and package are private. The predicate includes the repository URL, commit, release workflow and run URL, pinned base digest, dependency-lock hash, and target platforms. It does not publish the image, prompts, responses, credentials, or source content, but the existence and timing of a private release may become discoverable. Publication therefore fails closed unless the repository variable `HORMUZ_SIGSTORE_RELEASE_APPROVAL` is exactly `sigstore-public-transparency-v1`. Set that value only after the product owner accepts this disclosure. Otherwise select a private Sigstore deployment, customer/KMS signing, or GitHub Enterprise private-attestation design before cutting a tag.

## Reproducible Python distributions

Ordinary and tag-release workflows do not invoke the package backend directly. They run `scripts/reproducible_build.py` with the exact checked-out 40-character commit SHA. The gate:

1. requires the reviewed `build==1.3.0` frontend and exact `setuptools==84.0.0` and `wheel==0.48.0` backend inputs;
2. derives `SOURCE_DATE_EPOCH` from the selected commit rather than wall-clock time;
3. exports that commit through `git archive`, excluding untracked workspace state, and builds it twice in independent source and output directories;
4. rejects source-distribution traversal, duplicate paths, multiple roots, links, devices, and other non-file members before canonicalization;
5. rewrites the source archive with sorted paths, fixed owner/group, stable file modes, the commit timestamp, and a filename-free deterministic gzip header; and
6. compares the raw bytes of both wheels and both source archives before publishing either result.

The output directory also contains `hormuz-distribution-reproducibility.json`. It binds the two SHA-256 digests, byte sizes, commit, commit timestamp, build frontend, and backend versions without recording a workspace path, employee identity, prompt, response, credential, or build time. The output directory must be empty so stale artifacts cannot be mixed into the result.

From a committed local checkout with the reviewed frontend installed:

```bash
HORMUZ_RELEASE_SHA="$(git rev-parse HEAD)"
python3 scripts/reproducible_build.py \
  --source-sha "$HORMUZ_RELEASE_SHA" \
  --outdir dist
```

This proves byte equality across two independent package builds of one exact source revision under the reviewed Python build contract. It does not yet prove that OCI image layers are byte-identical across builders, that the Python artifacts reproduce across every operating system, or that a remote registry package has been published. Container rebuild reproducibility, an observed signed tag release, build-dependency hash custody, and external rebuilder comparison remain open release work.

## Cut a release

Do this only after the intended commit is merged to and verified on `main`. The current `0.x` line is marked as a prerelease automatically.

```bash
git switch main
git pull --ff-only origin main
python3 -m unittest -v
git tag -a v0.1.0 -m "Hormuz 0.1.0"
git push origin refs/tags/v0.1.0
```

Do not force-push a release tag. If version or source validation fails, fix the source on `main`, increment the version, and create a new tag. Do not delete or recycle an image version that an operator may already have recorded by digest.

The completed workflow creates a private GitHub release with five content-free evidence assets:

- `hormuz-release-evidence.json` — canonical digest, source, identity, package, and run summary;
- `hormuz-cosign-verification.json` — Cosign verification result for the exact digest; and
- `hormuz-provenance-verification.json` — cryptographic verification output for the signed SLSA predicate;
- `hormuz-slsa-provenance.json` — canonical source, dependency and build predicate; and
- `hormuz-slsa-provenance-validated.json` — bounded proof that the verified statement exactly matched the tag, commit, image digest and builder identity.

## Pull and verify

Production-like environments must use the `@sha256:...` reference from `hormuz-release-evidence.json`, not a mutable discovery tag. A unique machine or employee credential needs access to the private package; do not distribute one shared registry token.

```bash
docker pull 'ghcr.io/xpounder-com/hormuz@sha256:RELEASE_DIGEST'

cosign verify \
  --certificate-identity \
    'https://github.com/Xpounder-com/hormuz/.github/workflows/release.yml@refs/tags/v0.1.0' \
  --certificate-oidc-issuer 'https://token.actions.githubusercontent.com' \
  'ghcr.io/xpounder-com/hormuz@sha256:RELEASE_DIGEST'

cosign verify-attestation \
  --type slsaprovenance1 \
  --certificate-identity \
    'https://github.com/Xpounder-com/hormuz/.github/workflows/release.yml@refs/tags/v0.1.0' \
  --certificate-oidc-issuer 'https://token.actions.githubusercontent.com' \
  'ghcr.io/xpounder-com/hormuz@sha256:RELEASE_DIGEST'
```

Cosign verification validates that the signature covers the selected digest and was issued to the exact Hormuz tag workflow identity. Attestation verification validates the same identity and cryptographic statement; `release_contract.py` then requires that its decoded SLSA predicate exactly match the recorded source ref, commit, builder, image and digest. A successful signature alone does not prove that an image is production-ready or that an application configuration is safe.

## Rollback

Rollback means selecting a previously accepted digest, re-verifying its signature and provenance using that release's tag identity, and deploying that digest. Never implement rollback by retagging `latest` or rebuilding an old commit.

Before an upgrade, preserve the old digest and create a storage backup or snapshot that has been verified by a restore exercise. The current SQLite alpha has no proven backward-compatible schema rollback: do not point an older binary at a volume already migrated by a newer binary unless that exact compatibility path has passed. If upgrade verification fails, stop admission to the new instance, retain its data for investigation, restore the pre-upgrade data into an isolated volume, start the last known-good digest, and verify `/health/live`, `/health/ready`, authentication, policy, and a non-provider diagnostic before returning traffic.

This runbook does not prove backup/restore, RPO/RTO, zero-downtime upgrade, or multi-node rollback. Those remain issue #11 release gates.

## Governance still required

The repository currently has no tag ruleset or protected release environment. The workflow fails closed on event, tag, version, repository, commit, `main` ancestry, and explicit Sigstore-disclosure approval, but repository administrators can still change workflows or refs. Before an enterprise release, the owner must choose and enforce who can create `v*` tags, whether a protected release environment requires a second reviewer, package retention and pull-access policy, and whether customers receive Xpounder-signed images or customer-registry copies with their own KMS-backed signatures.

The private GHCR and GitHub OIDC path is the selected prototype distribution boundary. Customer-controlled registries, offline verification roots, KMS signing, public transparency disclosure, support lifetime, and emergency revocation remain later product decisions.
