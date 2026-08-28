from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
import json
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = ROOT / "tools" / "verify_quiet_alpha_evidence.py"
FIXTURE_PATH = (
    ROOT / "tests" / "fixtures" / "quiet_alpha" / "complete-synthetic-v1.json"
)
_SPEC = importlib.util.spec_from_file_location("verify_quiet_alpha_evidence", TOOL_PATH)
assert _SPEC is not None and _SPEC.loader is not None
quiet_alpha = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = quiet_alpha
_SPEC.loader.exec_module(quiet_alpha)


class QuietAlphaEvidenceTests(unittest.TestCase):
    def _fixture(self) -> dict[str, object]:
        return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def _release_evidence(self) -> dict[str, object]:
        value = self._fixture()
        value["evidence_kind"] = "quiet_alpha_release_evidence"
        value["operator_attestation"][  # type: ignore[index]
            "distinct_humans_verified_off_repository"
        ] = True
        return value

    def test_synthetic_fixture_validates_but_can_never_satisfy_gate(self) -> None:
        result = quiet_alpha.validate_evidence(self._fixture())

        self.assertFalse(result["ready_for_broad_promotion"])
        self.assertEqual(result["status"], "not_ready")
        self.assertIn("synthetic_fixture", result["reasons"])
        self.assertEqual(result["participant_count"], 6)
        self.assertEqual(result["successful_independent_participant_count"], 6)
        self.assertEqual(result["successful_returning_participant_count"], 1)

    def test_complete_release_evidence_satisfies_gate(self) -> None:
        result = quiet_alpha.validate_evidence(self._release_evidence())

        self.assertTrue(result["ready_for_broad_promotion"])
        self.assertEqual(result["status"], "passed_release_gate")
        self.assertEqual(result["reasons"], [])
        self.assertEqual(
            result["persona_coverage"],
            ["developer", "engineering_admin", "platform", "security"],
        )

    def test_archived_release_identity_matches_guide_and_fixture(self) -> None:
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        current_package_version = metadata["project"]["version"]
        archived_package_version = quiet_alpha.PACKAGE_VERSION
        guide = (ROOT / "docs" / "QUIET_ALPHA.md").read_text(encoding="utf-8")
        fixture = self._fixture()

        self.assertEqual(archived_package_version, "0.1.3")
        self.assertNotEqual(archived_package_version, current_package_version)
        self.assertEqual(
            quiet_alpha.PROGRAM,
            f"hormuz-v{archived_package_version}-quiet-alpha",
        )
        self.assertIn(f'"package_version": "{archived_package_version}"', guide)
        self.assertIn("--branch v0.1.3", guide)
        self.assertIn(quiet_alpha.RELEASE_SOURCE_COMMIT, guide)
        self.assertEqual(fixture["program"], quiet_alpha.PROGRAM)
        self.assertTrue(
            all(
                session["package_version"] == archived_package_version
                and session["source_commit"] == quiet_alpha.RELEASE_SOURCE_COMMIT
                for session in fixture["sessions"]
            )
        )

    def test_session_source_commit_is_pinned_to_advertised_release(self) -> None:
        value = self._release_evidence()
        value["sessions"][0]["source_commit"] = "f" * 40  # type: ignore[index]

        with self.assertRaisesRegex(
            quiet_alpha.QuietAlphaEvidenceError, "source_commit_unpinned"
        ):
            quiet_alpha.validate_evidence(value)

    def test_unknown_or_content_bearing_fields_fail_closed(self) -> None:
        value = self._release_evidence()
        value["prompt"] = "must never enter aggregate evidence"
        with self.assertRaisesRegex(
            quiet_alpha.QuietAlphaEvidenceError, "root_fields_invalid"
        ):
            quiet_alpha.validate_evidence(value)

        value = self._release_evidence()
        value["sessions"][0]["environment"]["hostname"] = "private-host"  # type: ignore[index]
        with self.assertRaisesRegex(
            quiet_alpha.QuietAlphaEvidenceError, "environment_fields_invalid"
        ):
            quiet_alpha.validate_evidence(value)

        value = self._release_evidence()
        value["sessions"][0]["content_free_attestations"][  # type: ignore[index]
            "free_text_absent"
        ] = False
        with self.assertRaisesRegex(
            quiet_alpha.QuietAlphaEvidenceError, "content_free_invalid"
        ):
            quiet_alpha.validate_evidence(value)

    def test_private_help_session_does_not_count_as_independent(self) -> None:
        value = self._release_evidence()
        sessions = value["sessions"]
        assert isinstance(sessions, list)
        for session in sessions:
            if session["participant_id"].endswith("000000000005"):
                session["assistance"] = "maintainer_or_private_help"

        result = quiet_alpha.validate_evidence(value)

        self.assertEqual(result["successful_independent_participant_count"], 5)
        self.assertTrue(result["ready_for_broad_promotion"])

        for session in sessions:
            if session["participant_id"].endswith("000000000006"):
                session["assistance"] = "maintainer_or_private_help"
        result = quiet_alpha.validate_evidence(value)
        self.assertEqual(result["successful_independent_participant_count"], 4)
        self.assertFalse(result["ready_for_broad_promotion"])
        self.assertIn("independent_install_demo_count_incomplete", result["reasons"])

    def test_persona_coverage_and_returning_session_are_required(self) -> None:
        value = self._release_evidence()
        sessions = value["sessions"]
        assert isinstance(sessions, list)
        for session in sessions:
            if session["persona"] == "engineering_admin":
                session["persona"] = "developer"
        result = quiet_alpha.validate_evidence(value)
        self.assertFalse(result["ready_for_broad_promotion"])
        self.assertIn("persona_coverage_incomplete", result["reasons"])

        value = self._release_evidence()
        sessions = value["sessions"]
        assert isinstance(sessions, list)
        value["sessions"] = [
            session for session in sessions if not session["returning_session"]
        ]
        value["findings"] = []
        result = quiet_alpha.validate_evidence(value)
        self.assertFalse(result["ready_for_broad_promotion"])
        self.assertIn("returning_user_session_missing", result["reasons"])

    def test_open_security_or_installation_blocker_prevents_readiness(self) -> None:
        value = self._release_evidence()
        finding = value["findings"][0]  # type: ignore[index]
        finding["status"] = "open"
        finding["resolution_commit"] = None
        finding["retest_session_id"] = None

        result = quiet_alpha.validate_evidence(value)

        self.assertFalse(result["ready_for_broad_promotion"])
        self.assertEqual(
            result["unresolved_security_or_installation_blocker_count"], 1
        )
        self.assertIn("security_or_installation_blocker_open", result["reasons"])

    def test_resolved_finding_requires_successful_independent_retest(self) -> None:
        value = self._release_evidence()
        retest_id = value["findings"][0]["retest_session_id"]  # type: ignore[index]
        for session in value["sessions"]:  # type: ignore[union-attr]
            if session["session_id"] == retest_id:
                session["assistance"] = "maintainer_or_private_help"
        with self.assertRaisesRegex(
            quiet_alpha.QuietAlphaEvidenceError, "finding_retest_invalid"
        ):
            quiet_alpha.validate_evidence(value)

    def test_participant_has_one_initial_session_and_later_returns(self) -> None:
        value = self._release_evidence()
        sessions = value["sessions"]
        assert isinstance(sessions, list)
        duplicate = copy.deepcopy(sessions[0])
        duplicate["session_id"] = "qas:10000000-0000-4000-8000-000000000008"
        sessions.append(duplicate)
        with self.assertRaisesRegex(
            quiet_alpha.QuietAlphaEvidenceError,
            "participant_initial_session_duplicated",
        ):
            quiet_alpha.validate_evidence(value)

        value = self._release_evidence()
        value["sessions"][-1]["session_date"] = "2026-08-01"  # type: ignore[index]
        with self.assertRaisesRegex(
            quiet_alpha.QuietAlphaEvidenceError, "returning_session_not_later"
        ):
            quiet_alpha.validate_evidence(value)

    def test_cli_requires_explicit_synthetic_fixture_mode(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(quiet_alpha.main([str(FIXTURE_PATH)]), 1)
        self.assertIn("synthetic_fixture_requires_explicit_flag", stderr.getvalue())

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(
                quiet_alpha.main(
                    [str(FIXTURE_PATH), "--allow-synthetic-fixture"]
                ),
                0,
            )
        self.assertFalse(json.loads(stdout.getvalue())["ready_for_broad_promotion"])

    def test_cli_rejects_duplicate_json_members(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "evidence.json"
            path.write_text(
                '{"schema_id":"first","schema_id":"second"}',
                encoding="utf-8",
            )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(quiet_alpha.main([str(path)]), 1)
        self.assertIn("duplicate_json_member", stderr.getvalue())

    def test_cli_returns_nonzero_for_valid_but_incomplete_release_evidence(self) -> None:
        value = self._release_evidence()
        value["operator_attestation"]["broad_promotion_not_started"] = False  # type: ignore[index]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "evidence.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(quiet_alpha.main([str(path)]), 1)
        result = json.loads(stdout.getvalue())
        self.assertFalse(result["ready_for_broad_promotion"])
        self.assertIn("broad_promotion_already_started", result["reasons"])

    def test_session_cannot_postdate_aggregate(self) -> None:
        value = self._release_evidence()
        value["sessions"][0]["session_date"] = "2026-08-25"  # type: ignore[index]
        with self.assertRaisesRegex(
            quiet_alpha.QuietAlphaEvidenceError, "session_date_after_generation"
        ):
            quiet_alpha.validate_evidence(value)

    def test_source_distribution_manifest_carries_contract(self) -> None:
        manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
        self.assertIn("include tools/verify_quiet_alpha_evidence.py\n", manifest)
        self.assertIn("recursive-include docs *.md\n", manifest)
        self.assertIn("recursive-include tests *.py *.json\n", manifest)


if __name__ == "__main__":
    unittest.main()
