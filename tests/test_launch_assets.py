from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tools.verify_launch_assets import LaunchAssetError, validate_launch_assets


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


class LaunchAssetTests(unittest.TestCase):
    def _copy_contract(self, target: Path) -> None:
        launch_source = REPOSITORY_ROOT / "docs/launch"
        shutil.copytree(launch_source, target / "docs/launch")
        shutil.copy2(REPOSITORY_ROOT / "MANIFEST.in", target / "MANIFEST.in")
        manifest = json.loads(
            (launch_source / "claims-v2.json").read_text(encoding="utf-8")
        )
        evidence_paths = {
            item["value"]
            for claim in manifest["claims"]
            for item in claim["evidence"]
            if item["kind"] == "repository_path"
        }
        for relative in evidence_paths:
            source = REPOSITORY_ROOT / relative
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

    def _manifest(self, root: Path) -> tuple[Path, dict[str, object]]:
        path = root / "docs/launch/claims-v2.json"
        return path, json.loads(path.read_text(encoding="utf-8"))

    def test_launch_draft_passes_strict_validation(self) -> None:
        result = validate_launch_assets(REPOSITORY_ROOT)
        self.assertEqual(result["schema_version"], 2)
        self.assertEqual(result["status"], "passed_draft")
        self.assertEqual(result["publication_status"], "draft_do_not_publish")
        self.assertEqual(result["launch_mode"], "public_alpha_tester_recruitment")
        self.assertFalse(result["publishable"])
        self.assertEqual(result["asset_count"], 6)
        self.assertEqual(result["claim_count"], 12)
        self.assertEqual(result["cta_count"], 2)
        self.assertEqual(result["analytic_count"], 7)
        self.assertEqual(result["required_closed_issue_count"], 11)
        self.assertEqual(result["post_publication_validation_issue"], 110)
        self.assertEqual(result["external_onboarding_validation"], "pending")
        self.assertEqual(result["external_tester_count"], "0/5")
        self.assertTrue(result["source_distribution_bound"])

    def test_missing_evidence_path_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            (root / "tests/test_demo.py").unlink()
            with self.assertRaisesRegex(LaunchAssetError, "is missing"):
                validate_launch_assets(root)

    def test_unknown_asset_claim_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            landing = root / "docs/launch/LANDING_PAGE.md"
            landing.write_text(
                landing.read_text(encoding="utf-8")
                + "\n<!-- claims: UNREVIEWED_CLAIM -->\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(LaunchAssetError, "claim references"):
                validate_launch_assets(root)

    def test_missing_draft_safety_label_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            article = root / "docs/launch/TECHNICAL_ARTICLE.md"
            article.write_text(
                article.read_text(encoding="utf-8").replace(
                    "# DRAFT — DO NOT PUBLISH", "# Technical article", 1
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(LaunchAssetError, "safety label"):
                validate_launch_assets(root)

    def test_unapproved_cta_token_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            landing = root / "docs/launch/LANDING_PAGE.md"
            landing.write_text(
                landing.read_text(encoding="utf-8")
                + "\n[Unreviewed calendar]({{CALENDAR_URL}})\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(LaunchAssetError, "unapproved template"):
                validate_launch_assets(root)

    def test_public_test_evidence_submission_remains_invitation_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            path, manifest = self._manifest(root)
            ctas = manifest["ctas"]
            assert isinstance(ctas, list) and isinstance(ctas[0], dict)
            ctas[0]["evidence_submission"] = "public_issue"
            path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(LaunchAssetError, "ctas.*changed"):
                validate_launch_assets(root)

    def test_positive_readiness_copy_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            social = root / "docs/launch/SOCIAL_AND_SHOW_HN.md"
            social.write_text(
                social.read_text(encoding="utf-8")
                + "\nHormuz is enterprise-ready.\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(LaunchAssetError, "readiness copy"):
                validate_launch_assets(root)

    def test_analytics_drift_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            path, manifest = self._manifest(root)
            analytics = manifest["analytics"]
            assert isinstance(analytics, list)
            analytics.pop()
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(LaunchAssetError, "analytics set"):
                validate_launch_assets(root)

    def test_publication_status_cannot_change_silently(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            path, manifest = self._manifest(root)
            manifest["publication_status"] = "approved_for_publication"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(LaunchAssetError, "without approval"):
                validate_launch_assets(root)

    def test_release_identity_cannot_change_silently(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            path, manifest = self._manifest(root)
            manifest["release"] = "v0.1.1-public-alpha"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(LaunchAssetError, "release identity"):
                validate_launch_assets(root)

    def test_post_publication_validation_contract_cannot_change_silently(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            path, manifest = self._manifest(root)
            gate = manifest["publication_gate"]
            assert isinstance(gate, dict)
            gate["post_publication_validation_issue"] = 999
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(LaunchAssetError, "publication gate"):
                validate_launch_assets(root)

    def test_quiet_alpha_is_not_a_required_closed_publication_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            path, manifest = self._manifest(root)
            gate = manifest["publication_gate"]
            assert isinstance(gate, dict)
            required = gate["required_closed_issues"]
            assert isinstance(required, list)
            required.append(110)
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(LaunchAssetError, "publication gate"):
                validate_launch_assets(root)

    def test_recruitment_disclosures_cannot_be_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            landing = root / "docs/launch/LANDING_PAGE.md"
            landing.write_text(
                landing.read_text(encoding="utf-8").replace("0/5", "unknown"),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(LaunchAssetError, "recruitment disclosure"):
                validate_launch_assets(root)

    def test_oci_digest_cannot_change_silently(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            expected = (
                "sha256:8ac24f5c7afb8ce09ec133616de06702"
                "f568a2e70594d8034146a131d86e5b67"
            )
            changed = "sha256:" + ("0" * 64)
            for relative in (
                "docs/launch/claims-v2.json",
                "docs/launch/LANDING_PAGE.md",
                "docs/launch/ARCHITECTURE_AND_SECURITY.md",
            ):
                path = root / relative
                path.write_text(
                    path.read_text(encoding="utf-8").replace(expected, changed),
                    encoding="utf-8",
                )
            with self.assertRaisesRegex(LaunchAssetError, "exact digest"):
                validate_launch_assets(root)

    def test_source_manifest_must_carry_claim_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            source_manifest = root / "MANIFEST.in"
            source_manifest.write_text(
                source_manifest.read_text(encoding="utf-8").replace(
                    "include docs/launch/claims-v2.json\n", "", 1
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(LaunchAssetError, "source distribution"):
                validate_launch_assets(root)


if __name__ == "__main__":
    unittest.main()
