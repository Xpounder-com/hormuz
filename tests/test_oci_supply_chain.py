from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tools import verify_oci_supply_chain


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "oci"
IMAGE_REFERENCE = "hormuz:ci-candidate"
IMAGE_ID = "sha256:" + ("a" * 64)
SCANNER_IMAGE = "aquasec/trivy@sha256:" + ("b" * 64)
SCANNER_VERSION = "0.74.0"


class OciSupplyChainEvidenceTests(unittest.TestCase):
    def _verify(self, vulnerability_fixture: str) -> tuple[dict[str, object], Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        sbom = root / "hormuz.cdx.json"
        vulnerabilities = root / "trivy-vulnerabilities.json"
        output = root / "summary.json"
        shutil.copyfile(FIXTURES / "supply-chain-sbom.json", sbom)
        shutil.copyfile(FIXTURES / vulnerability_fixture, vulnerabilities)
        summary = verify_oci_supply_chain.verify_evidence(
            image_reference=IMAGE_REFERENCE,
            image_id=IMAGE_ID,
            scanner_image=SCANNER_IMAGE,
            scanner_version=SCANNER_VERSION,
            sbom_path=sbom,
            vulnerabilities_path=vulnerabilities,
            output_path=output,
        )
        return summary, output

    def test_unfixed_high_and_lower_severity_findings_are_retained_without_blocking(self) -> None:
        summary, output = self._verify("supply-chain-pass-vulnerabilities.json")

        self.assertEqual(summary["schema_id"], "hormuz.oci-supply-chain-summary")
        self.assertEqual(summary["schema_version"], 1)
        self.assertEqual(summary["coverage"], "reference_oci_image_only")
        self.assertEqual(summary["verdict"], "pass")
        self.assertEqual(summary["findings"], {
            "total": 2,
            "by_severity": {
                "UNKNOWN": 0,
                "LOW": 1,
                "MEDIUM": 0,
                "HIGH": 1,
                "CRITICAL": 0,
            },
            "high_or_critical": {"total": 1, "fixable": 0, "unfixed": 1},
        })
        persisted = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(persisted, summary)
        self.assertTrue(persisted["artifacts"]["sbom"]["sha256"].startswith("sha256:"))
        self.assertTrue(persisted["artifacts"]["vulnerabilities"]["sha256"].startswith("sha256:"))

    def test_fixable_high_or_critical_finding_fails_after_writing_summary(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        sbom = root / "hormuz.cdx.json"
        vulnerabilities = root / "trivy-vulnerabilities.json"
        output = root / "summary.json"
        shutil.copyfile(FIXTURES / "supply-chain-sbom.json", sbom)
        shutil.copyfile(FIXTURES / "supply-chain-blocking-vulnerabilities.json", vulnerabilities)

        with self.assertRaisesRegex(verify_oci_supply_chain.EvidenceError, "1 HIGH/CRITICAL"):
            verify_oci_supply_chain.verify_evidence(
                image_reference=IMAGE_REFERENCE,
                image_id=IMAGE_ID,
                scanner_image=SCANNER_IMAGE,
                scanner_version=SCANNER_VERSION,
                sbom_path=sbom,
                vulnerabilities_path=vulnerabilities,
                output_path=output,
            )

        summary = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(summary["verdict"], "fail")
        self.assertEqual(summary["findings"]["high_or_critical"], {
            "total": 1,
            "fixable": 1,
            "unfixed": 0,
        })

    def test_malformed_vulnerability_report_fails_closed(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        sbom = root / "hormuz.cdx.json"
        vulnerabilities = root / "trivy-vulnerabilities.json"
        output = root / "summary.json"
        shutil.copyfile(FIXTURES / "supply-chain-sbom.json", sbom)
        shutil.copyfile(FIXTURES / "supply-chain-malformed-vulnerabilities.json", vulnerabilities)

        with self.assertRaisesRegex(verify_oci_supply_chain.EvidenceError, "unsupported"):
            verify_oci_supply_chain.verify_evidence(
                image_reference=IMAGE_REFERENCE,
                image_id=IMAGE_ID,
                scanner_image=SCANNER_IMAGE,
                scanner_version=SCANNER_VERSION,
                sbom_path=sbom,
                vulnerabilities_path=vulnerabilities,
                output_path=output,
            )
        self.assertFalse(output.exists())

    def test_sbom_must_bind_to_the_candidate_image(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        sbom = root / "hormuz.cdx.json"
        vulnerabilities = root / "trivy-vulnerabilities.json"
        output = root / "summary.json"
        sbom_value = json.loads((FIXTURES / "supply-chain-sbom.json").read_text(encoding="utf-8"))
        sbom_value["metadata"]["component"]["properties"][0]["value"] = "sha256:" + ("c" * 64)
        sbom.write_text(json.dumps(sbom_value), encoding="utf-8")
        shutil.copyfile(FIXTURES / "supply-chain-pass-vulnerabilities.json", vulnerabilities)

        with self.assertRaisesRegex(verify_oci_supply_chain.EvidenceError, "ImageID"):
            verify_oci_supply_chain.verify_evidence(
                image_reference=IMAGE_REFERENCE,
                image_id=IMAGE_ID,
                scanner_image=SCANNER_IMAGE,
                scanner_version=SCANNER_VERSION,
                sbom_path=sbom,
                vulnerabilities_path=vulnerabilities,
                output_path=output,
            )

    def test_vulnerability_report_must_identify_the_candidate_image(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        sbom = root / "hormuz.cdx.json"
        vulnerabilities = root / "trivy-vulnerabilities.json"
        output = root / "summary.json"
        shutil.copyfile(FIXTURES / "supply-chain-sbom.json", sbom)
        report = json.loads(
            (FIXTURES / "supply-chain-pass-vulnerabilities.json").read_text(encoding="utf-8")
        )
        report["ArtifactName"] = "hormuz:other-candidate"
        vulnerabilities.write_text(json.dumps(report), encoding="utf-8")

        with self.assertRaisesRegex(verify_oci_supply_chain.EvidenceError, "candidate image reference"):
            verify_oci_supply_chain.verify_evidence(
                image_reference=IMAGE_REFERENCE,
                image_id=IMAGE_ID,
                scanner_image=SCANNER_IMAGE,
                scanner_version=SCANNER_VERSION,
                sbom_path=sbom,
                vulnerabilities_path=vulnerabilities,
                output_path=output,
            )

    def test_scanner_image_must_be_pinned_by_digest(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        with self.assertRaisesRegex(verify_oci_supply_chain.EvidenceError, "pinned"):
            verify_oci_supply_chain.verify_evidence(
                image_reference=IMAGE_REFERENCE,
                image_id=IMAGE_ID,
                scanner_image="aquasec/trivy:0.74.0",
                scanner_version=SCANNER_VERSION,
                sbom_path=FIXTURES / "supply-chain-sbom.json",
                vulnerabilities_path=FIXTURES / "supply-chain-pass-vulnerabilities.json",
                output_path=Path(temporary.name) / "summary.json",
            )


if __name__ == "__main__":
    unittest.main()
