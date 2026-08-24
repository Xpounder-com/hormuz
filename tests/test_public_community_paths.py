from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tools.verify_public_community_paths import (
    CommunityPathError,
    validate_public_community_paths,
)


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


class PublicCommunityPathTests(unittest.TestCase):
    def _copy_contract(self, target: Path) -> None:
        for name in (
            "README.md",
            "CONTRIBUTING.md",
            "CODE_OF_CONDUCT.md",
            "SECURITY.md",
            "SUPPORT.md",
            "MANIFEST.in",
        ):
            shutil.copy2(REPOSITORY_ROOT / name, target / name)
        shutil.copytree(REPOSITORY_ROOT / ".github", target / ".github")
        shutil.copytree(REPOSITORY_ROOT / "docs", target / "docs")
        (target / "tools").mkdir()
        for name in (
            "verify_public_community_paths.py",
            "verify_oci_release_preflight.py",
        ):
            shutil.copy2(REPOSITORY_ROOT / "tools" / name, target / "tools" / name)

    def test_repository_public_paths_pass_strict_validation(self) -> None:
        result = validate_public_community_paths(REPOSITORY_ROOT)
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["document_count"], 4)
        self.assertEqual(result["issue_form_count"], 4)
        self.assertEqual(result["contact_link_count"], 2)
        self.assertTrue(result["pull_request_template"])
        self.assertTrue(result["support_matrix_bound"])

    def test_unknown_issue_form_type_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            form_path = root / ".github/ISSUE_TEMPLATE/bug.yml"
            form = json.loads(form_path.read_text(encoding="utf-8"))
            form["body"][1]["type"] = "secret-upload"
            form_path.write_text(json.dumps(form), encoding="utf-8")
            with self.assertRaisesRegex(CommunityPathError, "unsupported type"):
                validate_public_community_paths(root)

    def test_missing_relative_link_target_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            contributing = root / "CONTRIBUTING.md"
            contributing.write_text(
                contributing.read_text(encoding="utf-8")
                + "\n[Missing](docs/DOES_NOT_EXIST.md)\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(CommunityPathError, "missing file"):
                validate_public_community_paths(root)

    def test_client_version_drift_requires_support_update(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            ci = root / ".github/workflows/ci.yml"
            ci.write_text(
                ci.read_text(encoding="utf-8").replace(
                    'CODEX_VERSION: "0.147.0"', 'CODEX_VERSION: "99.0.0"'
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(CommunityPathError, "pinned Codex"):
                validate_public_community_paths(root)

    def test_placeholder_contact_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            code = root / "CODE_OF_CONDUCT.md"
            code.write_text(
                code.read_text(encoding="utf-8") + "\n[INSERT CONTACT METHOD]\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(CommunityPathError, "placeholder"):
                validate_public_community_paths(root)


if __name__ == "__main__":
    unittest.main()
