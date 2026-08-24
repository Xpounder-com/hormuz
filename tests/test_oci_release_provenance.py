from __future__ import annotations

import unittest

from tools import create_oci_release_provenance as provenance


DIGEST = "sha256:" + ("a" * 64)
CONFIG_ID = "sha256:" + ("b" * 64)
ARTIFACT_HASH = "sha256:" + ("c" * 64)


class OciReleaseProvenanceTests(unittest.TestCase):
    def _create(self, **overrides: object) -> dict[str, object]:
        preflight = {
            "schema_id": "hormuz.oci-release-preflight",
            "schema_version": 1,
            "artifact": {
                "contract": "signed_oci_digest",
                "first_publication_registry": "ghcr.io/xpounder-com/hormuz",
                "platform": "linux/amd64",
                "registry_is_product_contract": False,
            },
            "release": {
                "commit": "d" * 40,
                "package_version": "0.1.0",
                "ref": "refs/tags/v0.1.0",
                "repository_visibility": "public",
                "source_date_epoch": 1787562000,
                "tag": "v0.1.0",
                "tag_object": "annotated",
                "tag_protected": True,
            },
            "signing": {
                "issuer": "https://token.actions.githubusercontent.com",
                "key_management": "keyless_github_oidc",
                "transparency_log": "public_rekor",
                "workflow_identity": (
                    "https://github.com/Xpounder-com/hormuz/.github/workflows/"
                    "release-oci.yml@refs/tags/v0.1.0"
                ),
            },
        }
        reproducibility = {
            "schema_id": "hormuz.oci-reproducibility",
            "schema_version": 1,
            "artifact": {
                "digest": DIGEST,
                "platform": "linux/amd64",
                "reference": "v0.1.0",
                "revision": "d" * 40,
                "version": "0.1.0",
            },
            "build": {"source_date_epoch": 1787562000},
            "verdict": "pass",
        }
        supply_chain = {
            "schema_id": "hormuz.oci-supply-chain-summary",
            "schema_version": 1,
            "candidate": {
                "image_reference": f"ghcr.io/xpounder-com/hormuz@{DIGEST}",
                "image_id": CONFIG_ID,
            },
            "artifacts": {
                "sbom": {"sha256": ARTIFACT_HASH},
                "vulnerabilities": {"sha256": ARTIFACT_HASH},
            },
            "verdict": "pass",
        }
        values: dict[str, object] = {
            "preflight": preflight,
            "reproducibility": reproducibility,
            "supply_chain": supply_chain,
            "preflight_sha256": ARTIFACT_HASH,
            "reproducibility_sha256": ARTIFACT_HASH,
            "supply_chain_sha256": ARTIFACT_HASH,
            "sbom_sha256": ARTIFACT_HASH,
            "vulnerabilities_sha256": ARTIFACT_HASH,
            "build_lock_sha256": ARTIFACT_HASH,
            "runtime_lock_sha256": ARTIFACT_HASH,
            "image_reference": "ghcr.io/xpounder-com/hormuz",
            "image_digest": DIGEST,
            "invocation_uri": "https://github.com/Xpounder-com/hormuz/actions/runs/123/attempts/1",
        }
        values.update(overrides)
        return provenance.create_predicate(**values)  # type: ignore[arg-type]

    def test_predicate_binds_source_dependencies_and_security_evidence(self) -> None:
        predicate = self._create()

        self.assertEqual(predicate["buildDefinition"]["buildType"], provenance.BUILD_TYPE)
        self.assertEqual(
            predicate["buildDefinition"]["externalParameters"]["artifactContract"],
            "signed_oci_digest",
        )
        self.assertEqual(
            predicate["buildDefinition"]["externalParameters"]["platform"],
            "linux/amd64",
        )
        dependencies = predicate["buildDefinition"]["resolvedDependencies"]
        self.assertEqual(len(dependencies), 5)
        self.assertEqual(predicate["runDetails"]["metadata"]["invocationId"], (
            "https://github.com/Xpounder-com/hormuz/actions/runs/123/attempts/1"
        ))
        self.assertEqual(len(predicate["runDetails"]["byproducts"]), 5)

    def test_published_digest_must_equal_reproducible_and_scanned_candidate(self) -> None:
        mismatched = {
            "schema_id": "hormuz.oci-reproducibility",
            "schema_version": 1,
            "artifact": {
                "digest": "sha256:" + ("e" * 64),
                "platform": "linux/amd64",
                "reference": "v0.1.0",
                "revision": "d" * 40,
                "version": "0.1.0",
            },
            "build": {"source_date_epoch": 1787562000},
            "verdict": "pass",
        }
        with self.assertRaisesRegex(provenance.ProvenanceError, "not_reproducible"):
            self._create(reproducibility=mismatched)

    def test_failed_scan_or_other_registry_fails_closed(self) -> None:
        failed = {
            "schema_id": "hormuz.oci-supply-chain-summary",
            "schema_version": 1,
            "candidate": {
                "image_reference": f"ghcr.io/xpounder-com/hormuz@{DIGEST}",
            },
            "artifacts": {
                "sbom": {"sha256": ARTIFACT_HASH},
                "vulnerabilities": {"sha256": ARTIFACT_HASH},
            },
            "verdict": "fail",
        }
        with self.assertRaisesRegex(provenance.ProvenanceError, "verdict"):
            self._create(supply_chain=failed)
        with self.assertRaisesRegex(provenance.ProvenanceError, "registry"):
            self._create(image_reference="example.invalid/hormuz")

    def test_protected_tag_and_exact_signer_boundary_cannot_drift(self) -> None:
        preflight = self._create_preflight()
        preflight["release"]["tag_protected"] = False
        with self.assertRaisesRegex(provenance.ProvenanceError, "tag_boundary"):
            self._create(preflight=preflight)

        preflight = self._create_preflight()
        preflight["signing"]["workflow_identity"] = "https://github.com/fork/workflow"
        with self.assertRaisesRegex(provenance.ProvenanceError, "signing_boundary"):
            self._create(preflight=preflight)

    @staticmethod
    def _create_preflight() -> dict[str, object]:
        return {
            "schema_id": "hormuz.oci-release-preflight",
            "schema_version": 1,
            "artifact": {
                "contract": "signed_oci_digest",
                "first_publication_registry": "ghcr.io/xpounder-com/hormuz",
                "platform": "linux/amd64",
                "registry_is_product_contract": False,
            },
            "release": {
                "commit": "d" * 40,
                "package_version": "0.1.0",
                "ref": "refs/tags/v0.1.0",
                "repository_visibility": "public",
                "source_date_epoch": 1787562000,
                "tag": "v0.1.0",
                "tag_object": "annotated",
                "tag_protected": True,
            },
            "signing": {
                "issuer": "https://token.actions.githubusercontent.com",
                "key_management": "keyless_github_oidc",
                "transparency_log": "public_rekor",
                "workflow_identity": (
                    "https://github.com/Xpounder-com/hormuz/.github/workflows/"
                    "release-oci.yml@refs/tags/v0.1.0"
                ),
            },
        }


if __name__ == "__main__":
    unittest.main()
