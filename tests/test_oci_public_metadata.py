from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools import create_oci_release_provenance as release_provenance
from tools import verify_public_oci_metadata as public_metadata


IMAGE = "ghcr.io/xpounder-com/hormuz"
DIGEST = "sha256:" + ("a" * 64)
COMMIT = "b" * 40
EVIDENCE_HASH = "sha256:" + ("c" * 64)
CONFIG_DIGEST = "sha256:" + ("d" * 64)
INVOCATION_URI = "https://github.com/Xpounder-com/hormuz/actions/runs/123/attempts/1"
ROOT_REF = (
    "pkg:oci/hormuz@"
    f"{DIGEST}?arch=amd64&repository_url=ghcr.io%2Fxpounder-com%2Fhormuz"
)
ROOT = Path(__file__).resolve().parents[1]


class OciPublicMetadataTests(unittest.TestCase):
    def _validate(
        self,
        *,
        sbom: dict[str, object] | None = None,
        provenance: dict[str, object] | None = None,
        repository_visibility: str = "public",
    ) -> dict[str, object]:
        return public_metadata.validate_public_metadata(
            sbom=sbom or self._sbom(),
            provenance=provenance or self._provenance(),
            image_reference=IMAGE,
            image_digest=DIGEST,
            release_tag="v0.1.0",
            commit=COMMIT,
            invocation_uri=INVOCATION_URI,
            repository_visibility=repository_visibility,
            sbom_sha256=EVIDENCE_HASH,
            provenance_sha256=EVIDENCE_HASH,
            byproduct_sha256={
                name: EVIDENCE_HASH
                for name in (
                    "preflight",
                    "reproducibility",
                    "supply-chain-summary",
                    "cyclonedx-sbom",
                    "vulnerability-report",
                )
            },
            dependency_sha256={
                "build-lock": EVIDENCE_HASH,
                "runtime-lock": EVIDENCE_HASH,
            },
        )

    def test_strict_public_sbom_and_provenance_produce_content_free_summary(self) -> None:
        summary = self._validate()

        self.assertEqual(summary["artifact"]["digest"], DIGEST)
        self.assertEqual(summary["artifact"]["repository_visibility"], "public")
        self.assertEqual(summary["sbom"]["component_count"], 1)
        self.assertEqual(summary["secret_leak_validation"]["verdict"], "pass")
        self.assertNotIn("components", summary["sbom"])

    def test_repository_must_be_public_before_transparency_logging(self) -> None:
        with self.assertRaisesRegex(
            public_metadata.PublicMetadataError,
            "public_metadata_repository_not_public",
        ):
            self._validate(repository_visibility="private")

    def test_root_purl_uses_registry_manifest_digest_not_local_image_id(self) -> None:
        sbom = self._sbom()
        invalid_root_ref = ROOT_REF.replace(DIGEST, CONFIG_DIGEST)
        sbom["metadata"]["component"]["bom-ref"] = invalid_root_ref
        sbom["metadata"]["component"]["purl"] = invalid_root_ref
        sbom["dependencies"][0]["ref"] = invalid_root_ref
        with self.assertRaisesRegex(
            public_metadata.PublicMetadataError,
            "sbom_root_purl_invalid",
        ):
            self._validate(sbom=sbom)

    def test_unknown_schema_field_and_release_version_drift_fail_closed(self) -> None:
        sbom = self._sbom()
        sbom["metadata"]["unexpected"] = True
        with self.assertRaisesRegex(public_metadata.PublicMetadataError, "schema_invalid"):
            self._validate(sbom=sbom)

        sbom = self._sbom()
        sbom["components"][0]["version"] = "0.1.1"
        with self.assertRaisesRegex(public_metadata.PublicMetadataError, "hormuz_version"):
            self._validate(sbom=sbom)

    def test_provenance_must_bind_current_commit_run_and_evidence_hashes(self) -> None:
        provenance = self._provenance()
        provenance["buildDefinition"]["resolvedDependencies"][0]["digest"][
            "gitCommit"
        ] = "e" * 40
        with self.assertRaisesRegex(public_metadata.PublicMetadataError, "commit_mismatch"):
            self._validate(provenance=provenance)

        provenance = self._provenance()
        provenance["runDetails"]["byproducts"][0]["digest"]["sha256"] = "e" * 64
        with self.assertRaisesRegex(public_metadata.PublicMetadataError, "digest_mismatch"):
            self._validate(provenance=provenance)

    def test_secret_detection_returns_only_a_fixed_error_code(self) -> None:
        sbom = self._sbom()
        leaked_value = "api_key=not-a-real-but-sensitive-value"
        sbom["components"][0]["properties"] = [
            {
                "name": "aquasecurity:trivy:PkgID",
                "value": leaked_value,
            }
        ]

        with self.assertRaises(public_metadata.PublicMetadataError) as raised:
            self._validate(sbom=sbom)
        self.assertEqual(str(raised.exception), "sbom_secret_pattern_detected")
        self.assertNotIn(leaked_value, str(raised.exception))

    def test_duplicate_json_members_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "duplicate.json"
            path.write_text('{"bomFormat":"CycloneDX","bomFormat":"SPDX"}\n', encoding="utf-8")
            with self.assertRaisesRegex(
                public_metadata.PublicMetadataError,
                "duplicate_json_member",
            ):
                public_metadata._load_json(path)

    def test_release_workflow_validates_before_egress_and_separates_rekor_scope(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "release-oci.yml").read_text(
            encoding="utf-8"
        )
        validation_index = workflow.index("tools/verify_public_oci_metadata.py")
        signature_index = workflow.index("cosign sign --yes")
        attestation_index = workflow.index("cosign attest --yes")

        self.assertLess(validation_index, signature_index)
        self.assertLess(signature_index, attestation_index)
        self.assertIn("--no-default-rekor", workflow)
        self.assertEqual(workflow.count("--insecure-ignore-tlog"), 2)
        self.assertEqual(workflow.count("--use-signed-timestamps"), 2)
        self.assertIn('--use-signing-config=true "$IMAGE@$DIGEST"', workflow)
        self.assertEqual(
            workflow.count(
                '--signing-config "$RUNNER_TEMP/hormuz-attestation-signing-config.json"'
            ),
            2,
        )
        self.assertEqual(workflow.count("jq -er '.manifest.digest'"), 2)
        self.assertNotIn("sha256sum", workflow)

        retained = workflow.split("- name: Preserve metadata-only release evidence", 1)[1]
        self.assertNotIn("hormuz.cdx.json", retained)
        self.assertNotIn("provenance-predicate.json", retained)
        self.assertNotIn("trivy-vulnerabilities.json", retained)
        self.assertNotIn("/**", retained)

    def test_oci_contract_keeps_trust_and_first_release_boundaries(self) -> None:
        contract = (ROOT / "docs" / "OCI.md").read_text(encoding="utf-8")
        normalized = " ".join(contract.split())

        self.assertIn("Retention is an operator-owned prerequisite", normalized)
        self.assertIn(
            "Signer-identity retirement is a trust-policy operation", normalized
        )
        self.assertIn("must fail closed before the artifact is admitted", normalized)
        self.assertIn("there is no older signed Hormuz digest", normalized)
        self.assertIn("issues/135", normalized)

    @staticmethod
    def _provenance() -> dict[str, object]:
        ref = "refs/tags/v0.1.0"
        identity = (
            "https://github.com/Xpounder-com/hormuz/.github/workflows/"
            f"release-oci.yml@{ref}"
        )
        hash_value = "c" * 64
        return {
            "buildDefinition": {
                "buildType": release_provenance.BUILD_TYPE,
                "externalParameters": {
                    "artifactContract": "signed_oci_digest",
                    "firstPublicationRegistry": IMAGE,
                    "platform": "linux/amd64",
                    "ref": ref,
                    "repository": "Xpounder-com/hormuz",
                    "tag": "v0.1.0",
                    "version": "0.1.0",
                },
                "internalParameters": {},
                "resolvedDependencies": [
                    {
                        "digest": {"gitCommit": COMMIT},
                        "uri": f"git+https://github.com/Xpounder-com/hormuz@{ref}",
                    },
                    {
                        "digest": {
                            "sha256": release_provenance.BASE_DIGEST.removeprefix("sha256:")
                        },
                        "uri": release_provenance.BASE_IMAGE,
                    },
                    {
                        "digest": {
                            "sha256": release_provenance.FRONTEND_DIGEST.removeprefix("sha256:")
                        },
                        "uri": release_provenance.FRONTEND_IMAGE,
                    },
                    {
                        "digest": {"sha256": hash_value},
                        "uri": (
                            "git+https://github.com/Xpounder-com/hormuz#"
                            "requirements/oci-build-linux-amd64.lock"
                        ),
                    },
                    {
                        "digest": {"sha256": hash_value},
                        "uri": (
                            "git+https://github.com/Xpounder-com/hormuz#"
                            "requirements/oci-runtime-linux-amd64.lock"
                        ),
                    },
                ],
            },
            "runDetails": {
                "builder": {"id": identity},
                "byproducts": [
                    {"name": name, "digest": {"sha256": hash_value}}
                    for name in (
                        "preflight",
                        "reproducibility",
                        "supply-chain-summary",
                        "cyclonedx-sbom",
                        "vulnerability-report",
                    )
                ],
                "metadata": {
                    "invocationId": INVOCATION_URI
                },
            },
        }

    @staticmethod
    def _sbom() -> dict[str, object]:
        subject = f"{IMAGE}@{DIGEST}"
        return {
            "$schema": "http://cyclonedx.org/schema/bom-1.7.schema.json",
            "bomFormat": "CycloneDX",
            "specVersion": "1.7",
            "serialNumber": "urn:uuid:123e4567-e89b-42d3-a456-426614174000",
            "version": 1,
            "metadata": {
                "timestamp": "2026-08-24T00:00:00+00:00",
                "tools": {
                    "components": [
                        {
                            "type": "application",
                            "manufacturer": {"name": "Aqua Security Software Ltd."},
                            "group": "aquasecurity",
                            "name": "trivy",
                            "version": "0.74.0",
                        }
                    ]
                },
                "component": {
                    "bom-ref": ROOT_REF,
                    "type": "container",
                    "name": subject,
                    "purl": ROOT_REF,
                    "properties": [
                        {"name": "aquasecurity:trivy:ImageID", "value": CONFIG_DIGEST},
                        {
                            "name": "aquasecurity:trivy:Labels:org.opencontainers.image.description",
                            "value": (
                                "Non-root reference runtime for the Hormuz enterprise AI "
                                "policy gateway"
                            ),
                        },
                        {
                            "name": "aquasecurity:trivy:Labels:org.opencontainers.image.licenses",
                            "value": "Apache-2.0",
                        },
                        {
                            "name": "aquasecurity:trivy:Labels:org.opencontainers.image.revision",
                            "value": COMMIT,
                        },
                        {
                            "name": "aquasecurity:trivy:Labels:org.opencontainers.image.source",
                            "value": "https://github.com/Xpounder-com/hormuz",
                        },
                        {
                            "name": "aquasecurity:trivy:Labels:org.opencontainers.image.title",
                            "value": "Hormuz",
                        },
                        {
                            "name": "aquasecurity:trivy:Labels:org.opencontainers.image.version",
                            "value": "0.1.0",
                        },
                        {
                            "name": "aquasecurity:trivy:Labels:org.opencontainers.image.vendor",
                            "value": "NeuralInt",
                        },
                        {"name": "aquasecurity:trivy:Reference", "value": subject},
                        {"name": "aquasecurity:trivy:RepoDigest", "value": subject},
                        {"name": "aquasecurity:trivy:SchemaVersion", "value": "2"},
                        {"name": "aquasecurity:trivy:Size", "value": "123456"},
                    ],
                },
            },
            "components": [
                {
                    "bom-ref": "pkg:pypi/hormuz@0.1.0",
                    "type": "library",
                    "name": "hormuz",
                    "version": "0.1.0",
                    "purl": "pkg:pypi/hormuz@0.1.0",
                    "supplier": {"name": "Jörg Example <maintainer@example.invalid>"},
                }
            ],
            "dependencies": [
                {"ref": ROOT_REF, "dependsOn": ["pkg:pypi/hormuz@0.1.0"]},
                {"ref": "pkg:pypi/hormuz@0.1.0", "dependsOn": []},
            ],
            "vulnerabilities": [],
        }


if __name__ == "__main__":
    unittest.main()
