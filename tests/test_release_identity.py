from __future__ import annotations

import json
import tomllib
import unittest
from pathlib import Path

import hormuz
from hormuz import client_versions
from tools import client_release_versions, v1_candidate


ROOT = Path(__file__).resolve().parents[1]


class ReleaseIdentityTests(unittest.TestCase):
    def test_runtime_and_isolated_qualification_client_versions_match(self) -> None:
        self.assertEqual(
            client_versions.SUPPORTED_CODEX_VERSION,
            client_release_versions.SUPPORTED_CODEX_VERSION,
        )
        self.assertEqual(
            client_versions.SUPPORTED_CLAUDE_CODE_VERSION,
            client_release_versions.SUPPORTED_CLAUDE_CODE_VERSION,
        )

    def test_current_package_runtime_and_container_identity_are_consistent(self) -> None:
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        project = pyproject["project"]
        expected_version = "1.8.0"
        self.assertEqual(project["version"], expected_version)
        self.assertEqual(hormuz.__version__, expected_version)
        self.assertNotIn("Development Status :: 3 - Alpha", project["classifiers"])

        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertEqual(dockerfile.count(f"ARG HORMUZ_VERSION={expected_version}"), 2)
        self.assertNotIn("ARG HORMUZ_VERSION=0.1.3", dockerfile)

        hosted_dockerfile = (ROOT / "deploy/render/gateway/Dockerfile").read_text(
            encoding="utf-8"
        )
        self.assertEqual(
            hosted_dockerfile.count(f"ARG HORMUZ_VERSION={expected_version}"), 2
        )
        self.assertIn('"hormuz==${HORMUZ_VERSION}"', hosted_dockerfile)
        self.assertNotIn("hormuz==1.0.0", hosted_dockerfile)

        mac_workflow = (ROOT / ".github/workflows/macos-distribution.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn(f"default: {expected_version}", mac_workflow)

        server = (ROOT / "hormuz" / "server.py").read_text(encoding="utf-8")
        self.assertIn('f"Hormuz/{__version__}"', server)
        self.assertNotIn("Hormuz/0.1.3", server)

    def test_candidate_contract_targets_the_same_v1_identity(self) -> None:
        self.assertEqual(v1_candidate.TARGET_VERSION, "v1.0.0")
        self.assertEqual(v1_candidate.PACKAGE_VERSION, "1.0.0")
        self.assertEqual(
            v1_candidate.EVIDENCE_SCHEMA_ID,
            "hormuz.v1-internal-repeatability-evidence",
        )
        self.assertEqual(v1_candidate.EVIDENCE_SCHEMA_VERSION, 1)
        self.assertIn(
            "tools/run_v1_internal_repeatability.py",
            v1_candidate.REQUIRED_ARCHIVE_PATHS,
        )
        self.assertIn(
            "tools/verify_v1_internal_repeatability_evidence.py",
            v1_candidate.REQUIRED_ARCHIVE_PATHS,
        )

    def test_current_release_preserves_native_preview_boundaries(self) -> None:
        note = (ROOT / "docs/releases/v1.8.0-notarized-mac.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("unsigned, unsupported development preview", note)
        self.assertIn("1.5.0-dev.1", note)
        self.assertIn("No signed Windows installer is published", note)
        self.assertIn("Developer ID signed and notarized", note)
        self.assertIn("does not complete the native Rust launch/relay milestone", note)
        self.assertIn("/issues/341", note)
        self.assertIn("releases/download/v1.8.0/Hormuz-1.8.0-notarized.zip", note)
        self.assertIn("Request-time credential", note)
        self.assertIn("30-second bound", note)
        windows = tomllib.loads(
            (ROOT / "clients/rust/windows/Cargo.toml").read_text(encoding="utf-8")
        )
        self.assertEqual(windows["package"]["version"], "1.5.0-dev.1")
        self.assertEqual(windows["package"]["publish"], {"workspace": True})
        workspace = tomllib.loads(
            (ROOT / "clients/rust/Cargo.toml").read_text(encoding="utf-8")
        )
        self.assertFalse(workspace["workspace"]["package"]["publish"])

    def test_current_mac_guides_match_the_release_identity(self) -> None:
        distribution = (ROOT / "docs/MACOS_DISTRIBUTION.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("--version 1.8.0", distribution)
        self.assertIn("/private/tmp/hormuz-macos-1.8.0/Hormuz.app", distribution)
        self.assertNotIn("--version 1.3.0", distribution)
        self.assertNotIn("/private/tmp/hormuz-macos-1.3.0", distribution)
        mac = (ROOT / "clients/macos/README.md").read_text(encoding="utf-8")
        self.assertIn("v1.8.0 notarized app", mac)
        self.assertNotIn("Mac app remains v1.3.0", mac)
        roadmap = (ROOT / "docs/NATIVE_CLIENT_ROADMAP.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("core package is v1.8.0", roadmap)
        self.assertIn("v1.8.0-notarized-mac.md", roadmap)
        relay = (ROOT / "clients/rust/relay/README.md").read_text(encoding="utf-8")
        self.assertIn("v1.8.0 notarized Mac archive includes this relay", relay)
        self.assertIn("not Windows or Linux native", relay)
        for filename in (
            "docs/ROADMAP.md",
            "docs/MACOS_CLIENT_LOCAL.md",
            "clients/contracts/README.md",
        ):
            guide = (ROOT / filename).read_text(encoding="utf-8")
            self.assertIn("v1.8.0", guide, filename)
            self.assertNotIn("Mac app remains v1.3.0", guide, filename)
            self.assertNotIn("Mac download stays v1.3.0", guide, filename)

    def test_current_readme_uses_the_bounded_v1_claim(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        opening = readme.split("## What works", 1)[0]
        self.assertIn("Hormuz 1.0", opening)
        self.assertIn("> 1.8 preserves", opening)
        self.assertIn("five isolated internal repetitions", opening)
        self.assertIn("does not prove external", opening)
        self.assertNotIn("public open-source alpha", opening)

    def test_pinned_deployment_references_remain_distinct_from_package_identity(self) -> None:
        chart = (ROOT / "deploy" / "helm" / "hormuz" / "Chart.yaml").read_text(
            encoding="utf-8"
        )
        values = (ROOT / "deploy" / "helm" / "hormuz" / "values.yaml").read_text(
            encoding="utf-8"
        )
        self.assertIn('appVersion: "0.1.3"', chart)
        self.assertIn(
            "sha256:8ac24f5c7afb8ce09ec133616de06702f568a2e70594d8034146a131d86e5b67",
            values,
        )

    def test_runtime_plans_pin_packaging_but_not_marketing_copy(self) -> None:
        filenames = (
            "association-runtime-plan-v1.json",
            "scorecard-runtime-plan-v1.json",
            "role-view-runtime-plan-v1.json",
            "recommendation-runtime-plan-v1.json",
        )

        for filename in filenames:
            with self.subTest(filename=filename):
                plan = json.loads(
                    (ROOT / "docs" / filename).read_text(encoding="utf-8")
                )
                self.assertIn("pyproject.toml", plan["source_sha256"])

        recommendation = json.loads(
            (ROOT / "docs" / "recommendation-runtime-plan-v1.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertTrue(
            {"README.md", "docs/ROADMAP.md"}.isdisjoint(
                recommendation["source_sha256"]
            )
        )


if __name__ == "__main__":
    unittest.main()
