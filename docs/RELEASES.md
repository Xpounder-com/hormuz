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

1. requires the six-wheel Python 3.11+ cross-platform closure in `deploy/build/requirements.lock`: `build==1.3.0`, Windows-conditional `colorama==0.4.6`, `packaging==26.3`, `pyproject-hooks==1.2.0`, `setuptools==84.0.0`, and `wheel==0.48.0`, each with its reviewed PyPI wheel SHA-256;
2. derives `SOURCE_DATE_EPOCH` from the selected commit rather than wall-clock time;
3. requires the selected SHA to equal checked-out `HEAD`, exports that commit through `git archive`, and validates the backend declarations plus lock from the exported tree rather than untracked workspace state;
4. rejects source-distribution traversal, duplicate paths, multiple roots, links, devices, and other non-file members before canonicalization;
5. rewrites the source archive with sorted paths, fixed owner/group, stable file modes, the commit timestamp, and a filename-free deterministic gzip header; and
6. builds twice with isolation disabled, so the backend cannot create a second network-resolved environment, and compares the raw bytes of both wheels and both source archives before publishing either result.

Every Python workflow installs the build lock with pip `--require-hashes --only-binary=:all:` before any editable or distribution build. Every repository-owned source/test workflow also installs the canonical multi-platform runtime closure from `deploy/container/requirements.lock` with the same hash and binary requirements before Hormuz itself. Editable installs use `--no-build-isolation --no-deps`; the isolated wheel smoke uses `--no-deps`; and every such environment runs `pip check`. This prevents the Hormuz install step from authorizing an incidental runtime resolver. `colorama` is installed on all build hosts so the same strict build lock is complete on Windows without environment-marker ambiguity; it is inert outside Windows. The reviewed wheel hashes match the official PyPI release metadata for [build 1.3.0](https://pypi.org/project/build/1.3.0/), [colorama 0.4.6](https://pypi.org/project/colorama/0.4.6/), [packaging 26.3](https://pypi.org/project/packaging/26.3/), [pyproject-hooks 1.2.0](https://pypi.org/project/pyproject-hooks/1.2.0/), [setuptools 84.0.0](https://pypi.org/project/setuptools/84.0.0/), and [wheel 0.48.0](https://pypi.org/project/wheel/0.48.0/). A version or hash change is a reviewed supply-chain update, not an incidental resolver outcome. The official-client npm lock described below is a separate executable-test supply-chain contract; the latest-client canary remains intentionally dynamic.

The runtime lock also carries `backports-tarfile==1.2.0`, `importlib-metadata==9.0.0`, and `zipp==4.1.0` behind `python_full_version < '3.12'`, with reviewed wheel and source-archive hashes, to close the conditional dependencies required by `jaraco.context==6.1.2` and `keyring==25.7.0` on the oldest supported Python release.

The output directory also contains schema `hormuz.reproducible-distributions.v2` in `hormuz-distribution-reproducibility.json`. It binds the two artifact SHA-256 digests, byte sizes, commit, commit timestamp, build-lock digest, and exact distribution versions without recording a workspace path, employee identity, prompt, response, credential, or build time. The output directory must be empty so stale artifacts cannot be mixed into the result.

From a committed local checkout with the reviewed frontend installed:

```bash
python3 -m pip install \
  --require-hashes \
  --only-binary=:all: \
  --requirement deploy/build/requirements.lock
HORMUZ_RELEASE_SHA="$(git rev-parse HEAD)"
python3 scripts/reproducible_build.py \
  --source-sha "$HORMUZ_RELEASE_SHA" \
  --outdir dist
```

This proves byte equality across two independent package builds of one exact source revision under the hash-custodied Python build contract. It does not prove that the Python artifacts reproduce across every operating system, that build inputs are available offline, or that a remote package has been published.

## Integrity-locked official clients

Ordinary CI and tag verification use `deploy/clients/package-lock.json` as the sole dependency source for Codex `0.147.0`, Claude Code `2.1.233`, and all 14 supported platform-native optional packages. The fixture pins Node `24.19.0` and npm `11.17.0`; npm lockfile v3 records the exact version, official registry tarball URL, and SHA-512 integrity for all 16 packages. `scripts/client_lock_contract.py` rejects a changed direct version or integrity, an added package, a non-official registry URL, a linked/bundled package, an unexpected transitive edge, a platform-marker mismatch, or any lifecycle-script set other than the reviewed Claude Code installer.

Workflows run `npm ci` with lifecycle scripts disabled. They then invoke only `@anthropic-ai/claude-code/install.cjs`, whose reviewed purpose is to link or copy the already integrity-verified platform binary into the wrapper path; it does not resolve another package. The tag workflow runs official-client compatibility in a separate read-only job from the job that builds release artifacts, and publication requires both jobs. The emitted `hormuz.pinned-client-lock.v1` evidence contains only tool versions, package count, direct package versions/integrities, registry, lock digest, and the explicit script path.

Release verification also validates the versioned compatibility matrix and retains its content-free hash, category and support-level counts, exact client versions, and Python versions. Unsupported live-provider conformance, real-IdP profiles, PostgreSQL, and production-deployment surfaces remain zero rather than being promoted by a green package build.

Regeneration is an intentional compatibility and supply-chain review:

```bash
cd deploy/clients
npx --yes npm@11.17.0 install \
  --package-lock-only \
  --ignore-scripts \
  --no-audit \
  --no-fund
cd ../..
python3 scripts/client_lock_contract.py
```

Review every changed tarball URL, version, integrity, platform constraint, optional edge, and script flag before accepting the lock. Dependabot may propose this diff, but the hard-coded client contract intentionally keeps CI red until the approved versions, direct integrities, and whole-lock digest are updated together. This proves the exact pinned client closure exercised on GitHub-hosted Linux and the locally exercised host; it does not provide an offline npm mirror, authenticate a package beyond npm integrity and review, cover every operating system at runtime, or make the separate latest-version canary deterministic.

## Exact-source OCI image reproducibility

Ordinary CI and tag verification also run `scripts/reproducible_image.py` against the exact checked-out 40-character commit. The gate:

1. requires that source SHA to equal `HEAD`, derives the build epoch from the commit, and exports tracked files through `git archive` into two isolated contexts;
2. fails closed unless the active `docker-container` builder reports reviewed BuildKit `v0.32.2`; workflow setup binds that implementation to the official multi-platform image digest `sha256:28a898719c18a33f4e8000685287fa36fd0dd9560c6440227d3a732d79bb41d8`;
3. builds `linux/amd64` twice and `linux/arm64` twice with no cache, no pull, no tag, no push, the pinned base digest, the hash-locked runtime closure, and source/version/revision labels bound to the commit;
4. supplies `SOURCE_DATE_EPOCH` while pip installs the runtime closure so explicitly compiled Python bytecode uses deterministic hash-based invalidation rather than build-time timestamps, and removes emulator-created root-cache state from the process-boundary layer;
5. disables provenance and SBOM attestations only in the equality builds because those envelopes contain run-specific evidence;
6. rejects unsafe or excessive OCI layouts, ambiguous JSON, unexpected or unreferenced blobs, descriptor digest/size mismatches, wrong platform/source/version/user configuration, and layer/diff-ID inconsistencies with fixed diagnostics; and
7. requires each platform's two complete OCI file sets, sizes, and SHA-256 digests to match before publishing one canonical image tar and schema `hormuz.reproducible-oci.v1` evidence manifest per platform.

The manifest binds the exact source SHA and epoch, target platform, reviewed builder driver/image/version, Dockerfile/base/lock digests, OCI index/manifest/config/layer digests, artifact digest, and artifact size. It contains no generated time, local path, source content, prompt, response, employee identity, or credential. The output directory must be empty and cannot be a symlink.

The release image remains a separate multi-platform build with provenance and SBOM generation enabled, followed by exact-digest signing and verification. The reproducibility gate proves bounded unsigned `linux/amd64` and `linux/arm64` image payloads independently; it does not claim equality between architectures, universal cross-builder or cross-host equality, offline base/dependency availability, deterministic signature or attestation envelopes, an external independent rebuild service, or an observed registry release.

## Repository-local incident gate

Ordinary package CI and exact-tag verification run `scripts/incident_drill.py` against `operations/incident-drills.json`. The gate resolves and executes one exact regression for each of the seven incident scenarios required by issue #9, then writes `hormuz-incident-drill-evidence.json` only if every test passes. Ordinary CI retains the artifact for seven days; tag verification retains it for thirty days with the release-verification bundle.

The artifact is content-free and private by construction: it contains the catalog schema, version, SHA-256, scope, aggregate counts, and explicit false production-readiness flags. It does not contain scenario prose, test identifiers, runbook text, prompts, responses, identities, or credentials. The catalog and runbook explicitly leave live provider and IdP exercises, shared persistence and multi-region failure, complete deletion and legal-hold behavior, named on-call ownership, response/recovery targets, external communications, and independent review open.

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
