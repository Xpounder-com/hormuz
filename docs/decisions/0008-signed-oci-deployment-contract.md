# ADR 0008: Signed OCI deployment contract and first publication profile

- **Status:** Accepted
- **Decision date:** August 24, 2026
- **Decision record:** [issue #101 comment](https://github.com/Xpounder-com/hormuz/issues/101#issuecomment-5399436923)
- **Implementation gate:** [issue #101](https://github.com/Xpounder-com/hormuz/issues/101)

## Context

Hormuz needs one deployable application boundary that is independent of a
customer's orchestrator and registry. Making a Compose file, Helm chart,
Kubernetes cluster, or GHCR hostname the product contract would couple the
gateway to deployment tooling and weaken portability. The first release also
needs a signing identity that can be verified without storing a long-lived
private key.

The repository is initially private on GitHub Team, so GitHub's private
artifact-attestation service is not the chosen implementation. Public Sigstore
is acceptable because Hormuz is intended to become open source, provided its
metadata disclosure is explicit.

## Decision

1. The application deployment contract is the signed OCI manifest digest.
2. `ghcr.io/xpounder-com/hormuz` is the private first publication registry,
   not part of the product contract. Customers may mirror the exact digest and
   all signature/attestation referrers.
3. The initial supported platform is `linux/amd64` only. Issue #109 must close
   before a multi-architecture manifest or general ARM64 claim is published.
   Ceph-specific #68 remains separate.
4. Releases use keyless GitHub Actions OIDC through Cosign/Sigstore. The image
   signature uses public Rekor. CycloneDX and SLSA attestations use Fulcio and a
   signed timestamp but are excluded from Rekor and initially live only as
   referrers in private GHCR. Hormuz stores no long-lived signing private key.
5. Verification requires the GitHub OIDC issuer, exact
   `Xpounder-com/hormuz/.github/workflows/release-oci.yml` identity, expected
   protected annotated semantic-version tag, source commit, `push` trigger,
   and exact image digest.
6. The release workflow publishes a commit-addressed locator first and adds
   the semantic-version registry tag only after reproducibility, runtime,
   vulnerability, strict public-metadata validation, signature,
   SBOM-attestation, provenance, and image-signature Rekor checks pass. It never
   publishes `latest`.
7. Public Rekor material may expose the artifact digest, repository name,
   workflow path, commit/ref, and signing event. It must not contain source
   code, image layers, credentials, private workspace paths, prompts/responses,
   or customer data. The full SBOM/provenance referrers become readable only if
   the package is later made public.
8. Docker Compose is the first verified single-VM deployment profile for local
   use, evaluation, and pilots. Kubernetes/Helm is an optional enterprise
   profile outside the OCI application contract and is required for Hormuz's
   multi-replica/HA claims.

## Consequences

- A release can move between conforming OCI registries without changing the
  artifact identity or application contract.
- Consumers can enforce a narrow workload identity instead of trusting a
  repository-wide key or any GitHub Actions workflow.
- Rekor creates durable public metadata about the image-signing event; this is
  accepted and documented. It is not used as attestation storage.
- AMD64-only v1 reduces the first verification surface. ARM64 users must wait
  for #109 or use explicitly unsupported emulation.
- Registry availability and retention remain operational dependencies for the
  first publication location, but not semantic parts of the signed artifact.
- Compose evidence cannot be used to claim HA. Enterprise availability remains
  dependent on the optional Kubernetes profile and its dedicated gates.

## Nonclaims

This ADR does not certify GHCR, Sigstore availability, Docker, Kubernetes,
Helm, customer mirrors, TLS, PostgreSQL, HA, disaster recovery, or customer
operations. Each remains a separately evidenced boundary.
