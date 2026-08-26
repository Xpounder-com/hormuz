from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools import write_oci_release_evidence as evidence


class OciReleaseEvidenceTests(unittest.TestCase):
    def test_summary_is_allowlisted_portable_and_explicit_about_rekor(self) -> None:
        digest = "sha256:" + ("a" * 64)
        with tempfile.TemporaryDirectory() as temporary:
            paths = {}
            for name in {
                "preflight",
                "provenance",
                "provenance_verification",
                "public_metadata_validation",
                "reproducibility",
                "sbom",
                "sbom_verification",
                "signature_verification",
                "supply_chain",
                "vulnerabilities",
            }:
                path = Path(temporary) / name
                if name == "public_metadata_validation":
                    path.write_text(
                        json.dumps(
                            {
                                "schema_id": "hormuz.oci-public-metadata-validation",
                                "schema_version": 1,
                                "artifact": {
                                    "digest": digest,
                                    "image_reference": "ghcr.io/xpounder-com/hormuz",
                                    "platform": "linux/amd64",
                                    "repository_visibility": "public",
                                },
                                "release": {"commit": "b" * 40, "tag": "v0.1.0"},
                                "verdict": "pass",
                            }
                        ),
                        encoding="utf-8",
                    )
                else:
                    path.write_text(f"{name}\n", encoding="utf-8")
                paths[name] = path

            summary = evidence.create_summary(
                digest=digest,
                tag="v0.1.0",
                commit="b" * 40,
                workflow_identity=(
                    "https://github.com/Xpounder-com/hormuz/.github/workflows/"
                    "release-oci.yml@refs/tags/v0.1.0"
                ),
                first_publication_visibility="public",
                evidence_paths=paths,
            )
            with self.assertRaisesRegex(
                evidence.ReleaseEvidenceError,
                "first_publication_visibility",
            ):
                evidence.create_summary(
                    digest=digest,
                    tag="v0.1.0",
                    commit="b" * 40,
                    workflow_identity=(
                        "https://github.com/Xpounder-com/hormuz/.github/workflows/"
                        "release-oci.yml@refs/tags/v0.1.0"
                    ),
                    first_publication_visibility="private",
                    evidence_paths=paths,
                )
            paths["public_metadata_validation"].write_text(
                '{"schema_id":"hormuz.oci-public-metadata-validation",'
                '"schema_version":1,"artifact":{"digest":"sha256:'
                + ("f" * 64)
                + '","image_reference":"ghcr.io/xpounder-com/hormuz",'
                '"platform":"linux/amd64","repository_visibility":"public"},'
                '"release":{"commit":"'
                + ("b" * 40)
                + '","tag":"v0.1.0"},'
                '"verdict":"pass"}\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                evidence.ReleaseEvidenceError,
                "public_metadata_validation_mismatch",
            ):
                evidence.create_summary(
                    digest=digest,
                    tag="v0.1.0",
                    commit="b" * 40,
                    workflow_identity=(
                        "https://github.com/Xpounder-com/hormuz/.github/workflows/"
                        "release-oci.yml@refs/tags/v0.1.0"
                    ),
                    first_publication_visibility="public",
                    evidence_paths=paths,
                )

        self.assertEqual(summary["artifact"]["contract"], "signed_oci_digest")
        self.assertEqual(summary["schema_version"], 2)
        self.assertEqual(
            summary["artifact"]["first_publication_registry_visibility"],
            "public",
        )
        self.assertFalse(summary["artifact"]["registry_is_product_contract"])
        self.assertEqual(summary["signing"]["key_management"], "keyless_github_oidc")
        self.assertEqual(summary["disclosure"]["public_rekor_may_expose"], [
            "artifact_digest",
            "repository_name",
            "workflow_path",
            "commit_or_ref",
            "signing_event",
        ])
        self.assertFalse(summary["registry_attestations"]["public_rekor_upload"])
        self.assertTrue(summary["mirroring"]["destination_digest_must_match"])
        self.assertEqual(set(summary["evidence"]), set(paths))

    def test_wrong_identity_or_missing_evidence_fails_closed(self) -> None:
        with self.assertRaisesRegex(evidence.ReleaseEvidenceError, "identity"):
            evidence.create_summary(
                digest="sha256:" + ("a" * 64),
                tag="v0.1.0",
                commit="b" * 40,
                workflow_identity="https://github.com/fork/hormuz/workflow",
                first_publication_visibility="public",
                evidence_paths={},
            )


if __name__ == "__main__":
    unittest.main()
