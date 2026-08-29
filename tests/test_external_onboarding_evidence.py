from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

from tools import verify_external_onboarding_evidence as onboarding


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = (
    ROOT
    / "tests"
    / "fixtures"
    / "external_onboarding"
    / "complete-synthetic-v1.json"
)


class ExternalOnboardingEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.object(
            onboarding, "_MAX_FUTURE_CLOCK_SKEW", timedelta(days=3_650)
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _fixture(self) -> dict[str, object]:
        return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def _human_evidence(self) -> dict[str, object]:
        value = self._fixture()
        value["evidence_kind"] = "external_onboarding_evidence"
        value["operator_attestation"][  # type: ignore[index]
            "distinct_humans_verified_off_repository"
        ] = True
        return value

    def _validate_future_fixture(self, value: dict[str, object]) -> dict[str, object]:
        return onboarding.validate_evidence(value)

    def test_synthetic_fixture_validates_but_never_counts_as_people(self) -> None:
        result = onboarding.validate_evidence(self._fixture())

        self.assertFalse(result["validated_human_onboarding"])
        self.assertEqual(result["status"], "not_ready")
        self.assertIn("synthetic_fixture", result["reasons"])
        self.assertIn("distinct_humans_not_attested", result["reasons"])
        self.assertEqual(result["participant_count"], 5)
        self.assertEqual(result["successful_independent_participant_count"], 5)
        self.assertEqual(result["successful_returning_participant_count"], 1)

    def test_complete_human_evidence_satisfies_only_the_bounded_claim(self) -> None:
        result = self._validate_future_fixture(self._human_evidence())

        self.assertTrue(result["validated_human_onboarding"])
        self.assertEqual(result["status"], "passed_external_onboarding")
        self.assertEqual(result["reasons"], [])
        self.assertEqual(
            result["claim_scope"],
            "independent_installation_and_provider_free_demo_usability",
        )
        self.assertNotIn("policy", result["claim_scope"])
        self.assertNotIn("enterprise", result["claim_scope"])

    def test_exact_immutable_v1_artifact_identity_is_required(self) -> None:
        value = self._human_evidence()
        value["artifact"]["artifact_digest"] = "sha256:" + "f" * 64  # type: ignore[index]

        with self.assertRaisesRegex(
            onboarding.ExternalOnboardingEvidenceError, "artifact_identity_invalid"
        ):
            onboarding.validate_evidence(value)

        self.assertEqual(onboarding.TARGET_VERSION, "v1.0.0")
        self.assertEqual(onboarding.PACKAGE_VERSION, "1.0.0")
        self.assertEqual(
            onboarding.ARTIFACT_DIGEST,
            "sha256:2c3b16c1742ee76032a33f3714492a8d8515c5291d4d57520441882cd8bc5b5a",
        )
        self.assertEqual(
            onboarding.SOURCE_COMMIT,
            "2fc0605252e41f731c85cc9146fbff6eb3b34669",
        )

    def test_every_counted_session_is_bound_to_the_current_artifact(self) -> None:
        value = self._human_evidence()
        value["sessions"][1]["artifact_digest"] = "sha256:" + "b" * 64  # type: ignore[index]

        result = self._validate_future_fixture(value)

        self.assertEqual(result["participant_count"], 4)
        self.assertEqual(result["successful_independent_participant_count"], 4)
        self.assertIn("participant_count_outside_target", result["reasons"])
        self.assertIn("independent_install_demo_count_incomplete", result["reasons"])

    def test_private_help_or_unshipped_guidance_does_not_count(self) -> None:
        value = self._human_evidence()
        value["sessions"][0]["assistance"] = "maintainer_or_private_help"  # type: ignore[index]
        result = self._validate_future_fixture(value)
        self.assertEqual(result["successful_independent_participant_count"], 4)

        value = self._human_evidence()
        value["sessions"][0]["guidance_usage"] = {  # type: ignore[index]
            "sources": ["other_public_material"],
            "lookup_count": 1,
        }
        result = self._validate_future_fixture(value)
        self.assertEqual(result["successful_independent_participant_count"], 4)

    def test_demo_pass_requires_the_fixed_provider_free_observations(self) -> None:
        value = self._human_evidence()
        value["sessions"][0]["demo_verification"][  # type: ignore[index]
            "external_provider_call_count"
        ] = 1

        with self.assertRaisesRegex(
            onboarding.ExternalOnboardingEvidenceError,
            "demo_verification_invalid",
        ):
            onboarding.validate_evidence(value)

        value = self._human_evidence()
        value["sessions"][0]["demo_status"] = "failed"  # type: ignore[index]
        value["sessions"][0]["failure_code"] = "demo_policy"  # type: ignore[index]
        with self.assertRaisesRegex(
            onboarding.ExternalOnboardingEvidenceError,
            "demo_verification_unexpected",
        ):
            onboarding.validate_evidence(value)

    def test_failed_session_requires_a_linked_finding(self) -> None:
        value = self._human_evidence()
        value["sessions"][0]["demo_status"] = "failed"  # type: ignore[index]
        value["sessions"][0]["failure_code"] = "demo_policy"  # type: ignore[index]
        value["sessions"][0]["demo_verification"] = None  # type: ignore[index]

        with self.assertRaisesRegex(
            onboarding.ExternalOnboardingEvidenceError,
            "failed_without_finding",
        ):
            onboarding.validate_evidence(value)

    def test_out_of_contract_platform_failure_can_be_nonblocking(self) -> None:
        value = self._human_evidence()
        finding_id = "eof:30000000-0000-4000-8000-000000000002"
        session = value["sessions"][1]  # type: ignore[index]
        session["installation_status"] = "failed"
        session["demo_status"] = "not_attempted"
        session["time_to_install_seconds"] = 90
        session["time_to_demo_seconds"] = None
        session["failure_code"] = "unsupported_platform"
        session["demo_verification"] = None
        session["friction_categories"] = ["compatibility"]
        session["finding_ids"] = [finding_id]
        value["findings"] = [
            {
                "finding_id": finding_id,
                "origin_session_id": session["session_id"],
                "category": "compatibility",
                "blocker_reason": "none",
                "reference_type": "public_issue",
                "reference": "https://github.com/Xpounder-com/hormuz/issues/110",
                "status": "open",
                "correction": None,
            }
        ]

        result = self._validate_future_fixture(value)

        self.assertFalse(result["validated_human_onboarding"])
        self.assertEqual(result["unresolved_blocker_count"], 0)
        self.assertEqual(result["successful_independent_participant_count"], 4)

    def test_same_participant_sessions_cannot_overlap(self) -> None:
        value = self._human_evidence()
        value["sessions"][1]["participant_id"] = value["sessions"][0][  # type: ignore[index]
            "participant_id"
        ]
        value["sessions"][1]["started_at"] = "2026-08-29T16:02:00Z"  # type: ignore[index]

        with self.assertRaisesRegex(
            onboarding.ExternalOnboardingEvidenceError,
            "participant_sessions_overlap",
        ):
            onboarding.validate_evidence(value)

    def test_returning_session_must_be_on_a_later_date(self) -> None:
        value = self._human_evidence()
        value["sessions"][-1]["started_at"] = "2026-08-29T18:00:00Z"  # type: ignore[index]

        with self.assertRaisesRegex(
            onboarding.ExternalOnboardingEvidenceError,
            "returning_session_not_later_date",
        ):
            onboarding.validate_evidence(value)

        value = self._human_evidence()
        value["sessions"] = value["sessions"][:-1]  # type: ignore[index]
        result = self._validate_future_fixture(value)
        self.assertIn("returning_user_session_missing", result["reasons"])

    def test_content_bearing_or_unknown_fields_fail_closed(self) -> None:
        value = self._human_evidence()
        value["participant_email"] = "must never enter evidence"
        with self.assertRaisesRegex(
            onboarding.ExternalOnboardingEvidenceError, "root_fields_invalid"
        ):
            onboarding.validate_evidence(value)

        value = self._human_evidence()
        value["sessions"][0]["environment"]["hostname"] = "private-host"  # type: ignore[index]
        with self.assertRaisesRegex(
            onboarding.ExternalOnboardingEvidenceError,
            "environment_fields_invalid",
        ):
            onboarding.validate_evidence(value)

        value = self._human_evidence()
        value["sessions"][0]["content_free_attestations"][  # type: ignore[index]
            "free_text_absent"
        ] = False
        with self.assertRaisesRegex(
            onboarding.ExternalOnboardingEvidenceError, "content_free_invalid"
        ):
            onboarding.validate_evidence(value)

    def test_open_blocker_is_linked_and_prevents_completion(self) -> None:
        value = self._human_evidence()
        finding_id = "eof:30000000-0000-4000-8000-000000000001"
        session = value["sessions"][0]  # type: ignore[index]
        session["friction_categories"] = ["security"]
        session["finding_ids"] = [finding_id]
        value["findings"] = [
            {
                "finding_id": finding_id,
                "origin_session_id": session["session_id"],
                "category": "security",
                "blocker_reason": "content_or_credential_exposure",
                "reference_type": "private_security_advisory",
                "reference": "private-advisory:40000000-0000-4000-8000-000000000001",
                "status": "open",
                "correction": None,
            }
        ]

        result = self._validate_future_fixture(value)

        self.assertFalse(result["validated_human_onboarding"])
        self.assertEqual(result["unresolved_blocker_count"], 1)
        self.assertIn("onboarding_blocker_open", result["reasons"])

    def test_blocker_cannot_be_marked_resolved_on_unchanged_artifact(self) -> None:
        value = self._human_evidence()
        finding_id = "eof:30000000-0000-4000-8000-000000000001"
        session = value["sessions"][0]  # type: ignore[index]
        session["friction_categories"] = ["installation"]
        session["finding_ids"] = [finding_id]
        value["findings"] = [
            {
                "finding_id": finding_id,
                "origin_session_id": session["session_id"],
                "category": "installation",
                "blocker_reason": "published_guidance_failure",
                "reference_type": "public_issue",
                "reference": "https://github.com/Xpounder-com/hormuz/issues/110",
                "status": "resolved",
                "correction": {
                    "resolution_commit": "a" * 40,
                    "corrected_source_commit": onboarding.SOURCE_COMMIT,
                    "corrected_artifact_digest": onboarding.ARTIFACT_DIGEST,
                    "resolution_commit_ancestor_verified": True,
                    "automated_regression_url": "https://github.com/Xpounder-com/hormuz/actions/runs/1",
                    "automated_regression_source_commit": onboarding.SOURCE_COMMIT,
                    "automated_regression_workflow_path": ".github/workflows/ci.yml",
                    "automated_regression_binding_verified": True,
                    "automated_regression_conclusion": "success",
                    "retest_session_id": value["sessions"][-1]["session_id"],  # type: ignore[index]
                    "broad_workflow_change": False,
                },
            }
        ]

        with self.assertRaisesRegex(
            onboarding.ExternalOnboardingEvidenceError,
            "finding_correction_not_fresh",
        ):
            onboarding.validate_evidence(value)

    def test_cli_exit_codes_distinguish_invalid_incomplete_and_synthetic(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(onboarding.main([str(FIXTURE_PATH)]), 2)
        self.assertIn("synthetic_fixture_requires_explicit_flag", stderr.getvalue())

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(
                onboarding.main(
                    [str(FIXTURE_PATH), "--allow-synthetic-fixture"]
                ),
                0,
            )
        self.assertFalse(json.loads(stdout.getvalue())["validated_human_onboarding"])

        value = self._human_evidence()
        value["sessions"][0]["assistance"] = "maintainer_or_private_help"  # type: ignore[index]
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary) / "evidence.json"
            evidence.write_text(json.dumps(value), encoding="utf-8")
            with mock.patch.object(
                onboarding, "_MAX_FUTURE_CLOCK_SKEW", timedelta(days=3_650)
            ):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(onboarding.main([str(evidence)]), 1)

    def test_evidence_reader_rejects_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target.json"
            link = root / "evidence.json"
            target.write_text(json.dumps(self._fixture()), encoding="utf-8")
            os.symlink(target, link)
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(
                    onboarding.main([str(link), "--allow-synthetic-fixture"]),
                    2,
                )
        self.assertIn("evidence_not_bounded_regular_file", stderr.getvalue())

    def test_active_guide_and_source_distribution_carry_the_contract(self) -> None:
        guide = (ROOT / "docs" / "EXTERNAL_ONBOARDING.md").read_text(
            encoding="utf-8"
        )
        manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")

        self.assertIn(onboarding.GATE_ISSUE, guide)
        self.assertIn(onboarding.ARTIFACT_DIGEST, guide)
        self.assertIn(onboarding.SOURCE_COMMIT, guide)
        self.assertIn("0/5", guide)
        self.assertIn("returning", guide.lower())
        self.assertIn("do not count", guide.lower())
        self.assertIn(
            "include tools/verify_external_onboarding_evidence.py\n", manifest
        )
        self.assertIn("recursive-include docs *.md\n", manifest)
        self.assertIn("recursive-include tests *.py *.json\n", manifest)


if __name__ == "__main__":
    unittest.main()
