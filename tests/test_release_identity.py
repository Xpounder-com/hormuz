from __future__ import annotations

import tomllib
import unittest
from pathlib import Path

import hormuz
from tools import v1_candidate


ROOT = Path(__file__).resolve().parents[1]


class ReleaseIdentityTests(unittest.TestCase):
    def test_v1_package_runtime_and_container_identity_are_consistent(self) -> None:
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        project = pyproject["project"]
        self.assertEqual(project["version"], "1.0.0")
        self.assertEqual(hormuz.__version__, "1.0.0")
        self.assertNotIn("Development Status :: 3 - Alpha", project["classifiers"])

        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertEqual(dockerfile.count("ARG HORMUZ_VERSION=1.0.0"), 2)
        self.assertNotIn("ARG HORMUZ_VERSION=0.1.3", dockerfile)

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

    def test_current_readme_uses_the_bounded_v1_claim(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        opening = readme.split("## What works", 1)[0]
        self.assertIn("Hormuz 1.0", opening)
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


if __name__ == "__main__":
    unittest.main()
