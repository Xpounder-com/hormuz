from __future__ import annotations

import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = ROOT / "tools" / "verify_policy_admin_usability_evidence.py"
FIXTURE_PATH = (
    ROOT
    / "tests"
    / "fixtures"
    / "policy_admin_usability"
    / "complete-synthetic-v1.json"
)
_SPEC = importlib.util.spec_from_file_location(
    "verify_policy_admin_usability_evidence", TOOL_PATH
)
assert _SPEC is not None and _SPEC.loader is not None
usability = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = usability
_SPEC.loader.exec_module(usability)


class PolicyAdminUsabilityEvidenceTests(unittest.TestCase):
    def _fixture(self) -> dict[str, object]:
        return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def _release_evidence(self) -> dict[str, object]:
        value = self._fixture()
        value["evidence_kind"] = "release_gate_evidence"
        value["operator_attestation"][  # type: ignore[index]
            "distinct_humans_verified_off_repository"
        ] = True
        return value

    def _sessions(self, value: dict[str, object], track: str) -> list[dict[str, object]]:
        return [
            session
            for session in value["sessions"]  # type: ignore[union-attr]
            if session["track"] == track
        ]

    def _add_blocked_origin(
        self,
        value: dict[str, object],
        *,
        blocker_reason: str = "published_guidance_failure",
        category: str = "documentation",
        status: str = "open",
        broad: bool = False,
        corrected_at: str = "2026-08-27T10:00:00Z",
        retest_session_id: str = "paus:10000000-0000-4000-8000-000000000001",
    ) -> tuple[dict[str, object], dict[str, object]]:
        origin = copy.deepcopy(self._sessions(value, "offline")[0])
        origin["session_id"] = "paus:30000000-0000-4000-8000-000000000001"
        origin["release_artifact_digest"] = (
            "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        )
        origin["started_at"] = "2026-08-27T09:00:00Z"
        origin["duration_seconds"] = 300
        origin["outcome"] = "blocked"
        origin["friction_categories"] = [category]
        origin["finding_ids"] = [
            "pauf:40000000-0000-4000-8000-000000000001"
        ]
        origin["stages"][3]["status"] = "failed"  # type: ignore[index]
        origin["stages"][4]["status"] = "not_attempted"  # type: ignore[index]
        origin["stages"][5]["status"] = "not_attempted"  # type: ignore[index]
        origin["offline_verification"] = None
        value["sessions"].append(origin)  # type: ignore[union-attr]

        correction = None
        if status == "resolved":
            correction = {
                "resolution_commit": "c" * 40,
                "corrected_release_source_commit": value["release"][  # type: ignore[index]
                    "source_commit"
                ],
                "corrected_release_digest": value["release"]["artifact_digest"],  # type: ignore[index]
                "corrected_release_published_at": corrected_at,
                "resolution_commit_ancestor_verified": True,
                "automated_regression_url": (
                    "https://github.com/Xpounder-com/hormuz/actions/runs/123456789"
                ),
                "automated_regression_source_commit": value["release"][  # type: ignore[index]
                    "source_commit"
                ],
                "automated_regression_workflow_path": ".github/workflows/ci.yml",
                "automated_regression_binding_verified": True,
                "automated_regression_conclusion": "success",
                "retest_session_id": retest_session_id,
                "broad_workflow_change": broad,
                "affected_tracks": ["offline"],
            }
        finding = {
            "finding_id": origin["finding_ids"][0],
            "origin_session_id": origin["session_id"],
            "track": "offline",
            "category": category,
            "blocker_reason": blocker_reason,
            "reference_type": "public_issue",
            "reference": "https://github.com/Xpounder-com/hormuz/issues/174",
            "status": status,
            "correction": correction,
        }
        value["findings"].append(finding)  # type: ignore[union-attr]
        return origin, finding

    def _add_postgresql_blocker(
        self,
        value: dict[str, object],
        session: dict[str, object],
        *,
        category: str,
        blocker_reason: str,
    ) -> None:
        finding_id = "pauf:50000000-0000-4000-8000-000000000001"
        session["outcome"] = "blocked"
        session["friction_categories"] = [category]
        session["finding_ids"] = [finding_id]
        value["findings"].append(  # type: ignore[union-attr]
            {
                "finding_id": finding_id,
                "origin_session_id": session["session_id"],
                "track": "postgresql",
                "category": category,
                "blocker_reason": blocker_reason,
                "reference_type": "public_issue",
                "reference": "https://github.com/Xpounder-com/hormuz/issues/174",
                "status": "open",
                "correction": None,
            }
        )

    def test_synthetic_fixture_exercises_complete_gate_without_counting_as_human_evidence(
        self,
    ) -> None:
        result = usability.validate_evidence(self._fixture())

        self.assertFalse(result["ready_for_v1_policy_admin_claim"])
        self.assertEqual(result["status"], "not_ready")
        self.assertEqual(result["offline_participant_count"], 5)
        self.assertEqual(result["offline_completed_unaided_count"], 5)
        self.assertEqual(result["offline_within_15_minutes_count"], 4)
        self.assertEqual(result["postgresql_participant_count"], 3)
        self.assertEqual(result["postgresql_completed_verified_count"], 3)
        self.assertIn("synthetic_fixture", result["reasons"])
        self.assertIn("distinct_humans_not_attested", result["reasons"])

    def test_complete_release_evidence_satisfies_exact_thresholds(self) -> None:
        value = self._release_evidence()
        result = usability.validate_evidence(value)

        self.assertTrue(result["ready_for_v1_policy_admin_claim"])
        self.assertEqual(result["status"], "passed_release_gate")
        self.assertEqual(result["reasons"], [])
        self.assertEqual(result["offline_over_25_minutes_count"], 0)
        self.assertEqual(value["release"]["version"], "v1.0")

    def test_preregistered_cohort_completeness_and_no_replacement_are_attested(
        self,
    ) -> None:
        requirements = (
            (
                "cohorts_preregistered_before_testing",
                "cohorts_not_preregistered_before_testing",
            ),
            (
                "all_started_sessions_included",
                "started_sessions_not_fully_attested",
            ),
            (
                "participant_replacement_absent",
                "participant_replacement_not_ruled_out",
            ),
        )
        for field, reason in requirements:
            with self.subTest(field=field):
                value = self._release_evidence()
                value["operator_attestation"][field] = False

                result = usability.validate_evidence(value)

                self.assertFalse(result["ready_for_v1_policy_admin_claim"])
                self.assertIn(reason, result["reasons"])

    def test_release_label_uses_stable_v1_naming(self) -> None:
        for version in ("v1.0-alpha", "v1.1", "1.0"):
            with self.subTest(version=version):
                value = self._release_evidence()
                value["release"]["version"] = version
                with self.assertRaisesRegex(
                    usability.PolicyAdminUsabilityEvidenceError,
                    "release_version_invalid",
                ):
                    usability.validate_evidence(value)

    def test_v1_gate_accepts_only_the_complete_source_archive_kit(self) -> None:
        self.assertEqual(usability.ARTIFACT_KINDS, {"source_archive"})
        for artifact_kind in ("wheel", "signed_oci"):
            with self.subTest(artifact_kind=artifact_kind):
                value = self._release_evidence()
                value["release"]["artifact_kind"] = artifact_kind
                with self.assertRaisesRegex(
                    usability.PolicyAdminUsabilityEvidenceError,
                    "release_artifact_kind_invalid",
                ):
                    usability.validate_evidence(value)

    def test_overlap_between_offline_and_postgresql_participants_is_allowed(self) -> None:
        value = self._release_evidence()
        offline_ids = {
            session["participant_id"] for session in self._sessions(value, "offline")
        }
        postgresql_ids = {
            session["participant_id"] for session in self._sessions(value, "postgresql")
        }

        self.assertEqual(len(offline_ids & postgresql_ids), 3)
        self.assertTrue(
            usability.validate_evidence(value)["ready_for_v1_policy_admin_claim"]
        )

    def test_same_participant_sessions_must_not_overlap_in_time(self) -> None:
        value = self._release_evidence()
        postgresql = self._sessions(value, "postgresql")[0]
        postgresql["started_at"] = "2026-08-27T11:05:00Z"
        with self.assertRaisesRegex(
            usability.PolicyAdminUsabilityEvidenceError,
            "participant_sessions_overlap",
        ):
            usability.validate_evidence(value)

        postgresql["started_at"] = "2026-08-27T11:10:00Z"
        self.assertTrue(
            usability.validate_evidence(value)["ready_for_v1_policy_admin_claim"]
        )

    def test_offline_completion_is_bound_to_shipped_assets_and_expected_results(
        self,
    ) -> None:
        mutations = (
            (
                "baseline asset",
                lambda verification: verification.update(
                    baseline_asset_sha256="sha256:" + "0" * 64
                ),
                "offline_baseline_asset_unexpected",
            ),
            (
                "scenario asset",
                lambda verification: verification.update(
                    scenario_suite_asset_sha256="sha256:" + "0" * 64
                ),
                "offline_scenario_asset_unexpected",
            ),
            (
                "comparison result",
                lambda verification: verification["comparison"].update(after=3999),
                "offline_comparison_after_invalid",
            ),
            (
                "candidate identity",
                lambda verification: verification["comparison"].update(
                    candidate_version_id="sha256:" + "3" * 64,
                    candidate_content_sha256="3" * 64,
                ),
                "offline_comparison_identity_unexpected",
            ),
            (
                "evaluation result",
                lambda verification: verification["evaluation"].update(
                    candidate_max_output_tokens=3999
                ),
                "offline_evaluation_candidate_max_output_tokens_invalid",
            ),
        )
        for label, mutation, expected_error in mutations:
            with self.subTest(label=label):
                value = self._release_evidence()
                session = self._sessions(value, "offline")[0]
                mutation(session["offline_verification"])
                with self.assertRaisesRegex(
                    usability.PolicyAdminUsabilityEvidenceError,
                    expected_error,
                ):
                    usability.validate_evidence(value)

        value = self._release_evidence()
        self._sessions(value, "offline")[0]["offline_verification"] = None
        with self.assertRaisesRegex(
            usability.PolicyAdminUsabilityEvidenceError,
            "offline_verification_inconsistent",
        ):
            usability.validate_evidence(value)

    def test_unknown_content_fields_and_misordered_stages_fail_closed(self) -> None:
        value = self._release_evidence()
        self._sessions(value, "offline")[0]["policy_document"] = {"content": "forbidden"}
        with self.assertRaisesRegex(
            usability.PolicyAdminUsabilityEvidenceError,
            "session_0_fields_invalid",
        ):
            usability.validate_evidence(value)

        value = self._release_evidence()
        value["feedback"] = "free text must never enter the aggregate"
        with self.assertRaisesRegex(
            usability.PolicyAdminUsabilityEvidenceError,
            "root_fields_invalid",
        ):
            usability.validate_evidence(value)

        value = self._release_evidence()
        self._sessions(value, "postgresql")[0]["postgresql_verification"]["history"][
            "actor_id"
        ] = "private-identity"
        with self.assertRaisesRegex(
            usability.PolicyAdminUsabilityEvidenceError,
            "history_fields_invalid",
        ):
            usability.validate_evidence(value)

        value = self._release_evidence()
        self._sessions(value, "offline")[0]["content_free_attestations"][
            "free_text_absent"
        ] = False
        with self.assertRaisesRegex(
            usability.PolicyAdminUsabilityEvidenceError,
            "content_free_invalid",
        ):
            usability.validate_evidence(value)

        value = self._release_evidence()
        stages = self._sessions(value, "offline")[0]["stages"]
        stages[0], stages[1] = stages[1], stages[0]  # type: ignore[index]
        with self.assertRaisesRegex(
            usability.PolicyAdminUsabilityEvidenceError,
            "stage_order_invalid",
        ):
            usability.validate_evidence(value)

    def test_each_independence_rule_disqualifies_the_session(self) -> None:
        mutations = (
            ("author", lambda session: session["independence"].update(workflow_author_or_reviewer=True)),
            ("walkthrough", lambda session: session["independence"].update(prior_private_walkthrough=True)),
            ("assistance", lambda session: session["independence"].update(assistance_count=1)),
            (
                "unshipped guidance",
                lambda session: session["guidance_usage"].update(
                    sources=["other_public_material"], lookup_count=1
                ),
            ),
        )
        for label, mutation in mutations:
            with self.subTest(label=label):
                value = self._release_evidence()
                mutation(self._sessions(value, "offline")[0])
                result = usability.validate_evidence(value)
                self.assertEqual(result["offline_completed_unaided_count"], 4)
                self.assertFalse(result["ready_for_v1_policy_admin_claim"])
                self.assertIn("offline_completion_count_not_five", result["reasons"])

        value = self._release_evidence()
        self._sessions(value, "offline")[0]["guidance_usage"].update(
            sources=["command_help", "shipped_documentation"],
            lookup_count=1,
        )
        with self.assertRaisesRegex(
            usability.PolicyAdminUsabilityEvidenceError,
            "guidance_usage_inconsistent",
        ):
            usability.validate_evidence(value)

    def test_offline_time_boundaries_are_inclusive_and_enforced(self) -> None:
        value = self._release_evidence()
        result = usability.validate_evidence(value)
        self.assertEqual(result["offline_within_15_minutes_count"], 4)
        self.assertEqual(result["offline_over_25_minutes_count"], 0)

        self._sessions(value, "offline")[3]["duration_seconds"] = 901
        result = usability.validate_evidence(value)
        self.assertIn("offline_under_15_minute_count_below_four", result["reasons"])

        self._sessions(value, "offline")[4]["duration_seconds"] = 1501
        result = usability.validate_evidence(value)
        self.assertIn("offline_duration_over_25_minutes", result["reasons"])

    def test_aggregate_generation_cannot_precede_session_completion(self) -> None:
        value = self._release_evidence()
        value["generated_at"] = "2026-08-27T14:45:00Z"
        with self.assertRaisesRegex(
            usability.PolicyAdminUsabilityEvidenceError,
            "session_ends_after_generation",
        ):
            usability.validate_evidence(value)

        value["generated_at"] = "2026-08-27T14:50:00Z"
        self.assertTrue(
            usability.validate_evidence(value)["ready_for_v1_policy_admin_claim"]
        )

    def test_future_dated_aggregate_cannot_pass_before_sessions_occur(self) -> None:
        value = self._release_evidence()
        value["generated_at"] = "2099-08-27T20:00:00Z"
        value["release"]["published_at"] = "2099-08-27T10:00:00Z"  # type: ignore[index]
        for session in value["sessions"]:  # type: ignore[union-attr]
            session["started_at"] = session["started_at"].replace("2026", "2099")

        with self.assertRaisesRegex(
            usability.PolicyAdminUsabilityEvidenceError,
            "generation_in_future",
        ):
            usability.validate_evidence(value)

    def test_exact_participant_counts_prevent_cherry_picking_extra_runs(self) -> None:
        value = self._release_evidence()
        extra = copy.deepcopy(self._sessions(value, "offline")[0])
        extra["session_id"] = "paus:10000000-0000-4000-8000-000000000006"
        extra["participant_id"] = "pau:00000000-0000-4000-8000-000000000006"
        extra["started_at"] = "2026-08-27T13:00:00Z"
        value["sessions"].append(extra)  # type: ignore[union-attr]

        result = usability.validate_evidence(value)

        self.assertEqual(result["offline_participant_count"], 6)
        self.assertIn("offline_participant_count_not_five", result["reasons"])

    def test_postgresql_version_digest_generation_and_history_are_computed(self) -> None:
        value = self._release_evidence()
        session = self._sessions(value, "postgresql")[0]
        verification = session["postgresql_verification"]
        verification["apply"]["observed_version_id"] = "sha256:" + "3" * 64  # type: ignore[index]
        verification["apply"]["observed_content_sha256"] = "3" * 64  # type: ignore[index]
        verification["history"]["apply_version_id"] = "sha256:" + "3" * 64  # type: ignore[index]
        verification["history"]["apply_content_sha256"] = "3" * 64  # type: ignore[index]
        self._add_postgresql_blocker(
            value,
            session,
            category="verification",
            blocker_reason="wrong_policy_state",
        )

        result = usability.validate_evidence(value)

        self.assertEqual(result["postgresql_completed_verified_count"], 2)
        self.assertIn(
            "postgresql_completion_or_state_verification_incomplete",
            result["reasons"],
        )
        self.assertIn("blocker_open", result["reasons"])

        value = self._release_evidence()
        session = self._sessions(value, "postgresql")[0]
        session["postgresql_verification"]["history"]["rollback_generation"] = 4
        self._add_postgresql_blocker(
            value,
            session,
            category="history",
            blocker_reason="history_inconsistency",
        )
        result = usability.validate_evidence(value)
        self.assertEqual(result["postgresql_completed_verified_count"], 2)
        self.assertIn("blocker_open", result["reasons"])

        value = self._release_evidence()
        session = self._sessions(value, "postgresql")[0]
        session["postgresql_verification"]["history"]["apply_event_type"] = (
            "policy_staged"
        )
        self._add_postgresql_blocker(
            value,
            session,
            category="history",
            blocker_reason="history_inconsistency",
        )
        result = usability.validate_evidence(value)
        self.assertEqual(result["postgresql_completed_verified_count"], 2)
        self.assertIn("blocker_open", result["reasons"])

    def test_postgresql_sessions_require_unique_isolated_tenant_scopes(self) -> None:
        value = self._release_evidence()
        self._sessions(value, "postgresql")[0]["postgresql_isolation"][
            "isolated_tenant_attested"
        ] = False
        with self.assertRaisesRegex(
            usability.PolicyAdminUsabilityEvidenceError,
            "postgresql_isolation_not_attested",
        ):
            usability.validate_evidence(value)

        value = self._release_evidence()
        sessions = self._sessions(value, "postgresql")
        sessions[1]["postgresql_isolation"]["run_scope_id"] = sessions[0][
            "postgresql_isolation"
        ]["run_scope_id"]
        with self.assertRaisesRegex(
            usability.PolicyAdminUsabilityEvidenceError,
            "postgresql_run_scope_ids_duplicated",
        ):
            usability.validate_evidence(value)

        value = self._release_evidence()
        self._sessions(value, "offline")[0]["postgresql_isolation"] = {
            "run_scope_id": "pauscope:60000000-0000-4000-8000-000000000001",
            "isolated_tenant_attested": True,
        }
        with self.assertRaisesRegex(
            usability.PolicyAdminUsabilityEvidenceError,
            "postgresql_isolation_unexpected",
        ):
            usability.validate_evidence(value)

    def test_postgresql_completion_requires_both_active_version_guards(self) -> None:
        value = self._release_evidence()
        session = self._sessions(value, "postgresql")[0]
        verification = session["postgresql_verification"]
        verification["apply"]["if_active_guard_used"] = False  # type: ignore[index]
        verification["apply"]["if_active_version_id"] = None  # type: ignore[index]
        self._add_postgresql_blocker(
            value,
            session,
            category="activation",
            blocker_reason="published_guidance_failure",
        )

        result = usability.validate_evidence(value)

        self.assertEqual(result["postgresql_completed_verified_count"], 2)
        self.assertIn("blocker_open", result["reasons"])

        value = self._release_evidence()
        verification = self._sessions(value, "postgresql")[0][
            "postgresql_verification"
        ]
        verification["rollback"]["if_active_version_id"] = verification[  # type: ignore[index]
            "apply"
        ]["previous_version_id"]
        with self.assertRaisesRegex(
            usability.PolicyAdminUsabilityEvidenceError,
            "outcome_inconsistent",
        ):
            usability.validate_evidence(value)

    def test_each_approved_blocker_reason_blocks_the_gate(self) -> None:
        offline_reasons = (
            usability.BLOCKER_REASONS
            - usability._POSTGRESQL_ONLY_BLOCKER_REASONS
            - {"none"}
        )
        for blocker_reason in offline_reasons:
            with self.subTest(blocker_reason=blocker_reason):
                value = self._release_evidence()
                self._add_blocked_origin(value, blocker_reason=blocker_reason)
                result = usability.validate_evidence(value)
                self.assertIn("blocker_open", result["reasons"])
                self.assertFalse(result["ready_for_v1_policy_admin_claim"])

        categories = {
            "authentication_bypass": "authentication",
            "history_inconsistency": "history",
            "wrong_policy_state": "verification",
        }
        for blocker_reason in usability._POSTGRESQL_ONLY_BLOCKER_REASONS:
            with self.subTest(blocker_reason=blocker_reason):
                value = self._release_evidence()
                session = self._sessions(value, "postgresql")[0]
                self._add_postgresql_blocker(
                    value,
                    session,
                    category=categories[blocker_reason],
                    blocker_reason=blocker_reason,
                )
                result = usability.validate_evidence(value)
                self.assertIn("blocker_open", result["reasons"])
                self.assertFalse(result["ready_for_v1_policy_admin_claim"])

    def test_postgresql_only_blockers_are_rejected_for_offline_sessions(self) -> None:
        for blocker_reason in usability._POSTGRESQL_ONLY_BLOCKER_REASONS:
            with self.subTest(blocker_reason=blocker_reason):
                value = self._release_evidence()
                self._add_blocked_origin(value, blocker_reason=blocker_reason)
                with self.assertRaisesRegex(
                    usability.PolicyAdminUsabilityEvidenceError,
                    "blocker_track_invalid",
                ):
                    usability.validate_evidence(value)

    def test_postgresql_blocker_before_state_verification_needs_no_fake_state(self) -> None:
        value = self._release_evidence()
        session = self._sessions(value, "postgresql")[0]
        session["postgresql_verification"] = None
        session["stages"][0]["status"] = "failed"
        for stage in session["stages"][1:]:
            stage["status"] = "not_attempted"
        self._add_postgresql_blocker(
            value,
            session,
            category="authentication",
            blocker_reason="authentication_bypass",
        )

        result = usability.validate_evidence(value)

        self.assertEqual(result["postgresql_completed_verified_count"], 2)
        self.assertIn("blocker_open", result["reasons"])

    def test_resolved_nonblocking_friction_needs_no_release_retest(self) -> None:
        value = self._release_evidence()
        session = self._sessions(value, "offline")[0]
        finding_id = "pauf:60000000-0000-4000-8000-000000000001"
        session["friction_categories"] = ["command_discovery"]
        session["finding_ids"] = [finding_id]
        value["findings"].append(  # type: ignore[union-attr]
            {
                "finding_id": finding_id,
                "origin_session_id": session["session_id"],
                "track": "offline",
                "category": "command_discovery",
                "blocker_reason": "none",
                "reference_type": "public_issue",
                "reference": "https://github.com/Xpounder-com/hormuz/issues/174",
                "status": "resolved",
                "correction": None,
            }
        )

        result = usability.validate_evidence(value)

        self.assertTrue(result["ready_for_v1_policy_admin_claim"])
        self.assertEqual(result["finding_count"], 1)

        finding = value["findings"][0]
        finding["reference_type"] = "private_security_advisory"
        finding["reference"] = (
            "private-advisory:70000000-0000-4000-8000-000000000001"
        )
        with self.assertRaisesRegex(
            usability.PolicyAdminUsabilityEvidenceError,
            "private_reference_invalid",
        ):
            usability.validate_evidence(value)

    def test_one_session_can_record_every_observed_finding(self) -> None:
        value = self._release_evidence()
        origin, _ = self._add_blocked_origin(value)
        second_id = "pauf:40000000-0000-4000-8000-000000000002"
        origin["finding_ids"].append(second_id)  # type: ignore[union-attr]
        origin["friction_categories"] = ["documentation", "verification"]
        value["findings"].append(  # type: ignore[union-attr]
            {
                "finding_id": second_id,
                "origin_session_id": origin["session_id"],
                "track": "offline",
                "category": "verification",
                "blocker_reason": "content_or_credential_exposure",
                "reference_type": "private_security_advisory",
                "reference": (
                    "private-advisory:70000000-0000-4000-8000-000000000002"
                ),
                "status": "open",
                "correction": None,
            }
        )

        result = usability.validate_evidence(value)

        self.assertEqual(result["finding_count"], 2)
        self.assertEqual(result["unresolved_blocker_count"], 2)
        self.assertIn("blocker_open", result["reasons"])

        origin["finding_ids"].remove(second_id)  # type: ignore[union-attr]
        origin["friction_categories"] = ["documentation"]
        with self.assertRaisesRegex(
            usability.PolicyAdminUsabilityEvidenceError,
            "finding_origin_invalid",
        ):
            usability.validate_evidence(value)

    def test_resolved_blocker_requires_regression_and_fresh_independent_retest(self) -> None:
        value = self._release_evidence()
        _, finding = self._add_blocked_origin(value, status="resolved")

        result = usability.validate_evidence(value)

        self.assertTrue(result["ready_for_v1_policy_admin_claim"])
        self.assertEqual(result["resolved_blocker_count"], 1)

        value = self._release_evidence()
        _, finding = self._add_blocked_origin(value, status="resolved")
        finding["correction"]["automated_regression_url"] = "manual-check"
        with self.assertRaisesRegex(
            usability.PolicyAdminUsabilityEvidenceError,
            "automated_regression_invalid",
        ):
            usability.validate_evidence(value)

        value = self._release_evidence()
        _, finding = self._add_blocked_origin(value, status="resolved")
        finding["correction"]["automated_regression_conclusion"] = "failure"
        with self.assertRaisesRegex(
            usability.PolicyAdminUsabilityEvidenceError,
            "automated_regression_conclusion_invalid",
        ):
            usability.validate_evidence(value)

        value = self._release_evidence()
        _, finding = self._add_blocked_origin(value, status="resolved")
        finding["correction"]["automated_regression_source_commit"] = "d" * 40
        with self.assertRaisesRegex(
            usability.PolicyAdminUsabilityEvidenceError,
            "finding_regression_not_for_gated_release",
        ):
            usability.validate_evidence(value)

        value = self._release_evidence()
        _, finding = self._add_blocked_origin(value, status="resolved")
        finding["correction"]["automated_regression_workflow_path"] = (
            ".github/workflows/release.yml"
        )
        with self.assertRaisesRegex(
            usability.PolicyAdminUsabilityEvidenceError,
            "automated_regression_workflow_invalid",
        ):
            usability.validate_evidence(value)

        value = self._release_evidence()
        _, finding = self._add_blocked_origin(value, status="resolved")
        finding["correction"]["automated_regression_binding_verified"] = False
        with self.assertRaisesRegex(
            usability.PolicyAdminUsabilityEvidenceError,
            "automated_regression_binding_not_verified",
        ):
            usability.validate_evidence(value)

        value = self._release_evidence()
        _, finding = self._add_blocked_origin(value, status="resolved")
        retest_started_at = self._sessions(value, "offline")[0]["started_at"]
        value["release"]["published_at"] = retest_started_at
        finding["correction"]["corrected_release_published_at"] = retest_started_at
        with self.assertRaisesRegex(
            usability.PolicyAdminUsabilityEvidenceError,
            "finding_retest_invalid",
        ):
            usability.validate_evidence(value)

        value = self._release_evidence()
        origin, finding = self._add_blocked_origin(value, status="resolved")
        origin["release_artifact_digest"] = finding["correction"][
            "corrected_release_digest"
        ]
        origin["participant_id"] = "pau:00000000-0000-4000-8000-000000000006"
        origin["started_at"] = "2026-08-27T10:15:00Z"
        with self.assertRaisesRegex(
            usability.PolicyAdminUsabilityEvidenceError,
            "finding_correction_not_fresh",
        ):
            usability.validate_evidence(value)

        value = self._release_evidence()
        origin, finding = self._add_blocked_origin(value, status="resolved")
        origin["started_at"] = finding["correction"][
            "corrected_release_published_at"
        ]
        with self.assertRaisesRegex(
            usability.PolicyAdminUsabilityEvidenceError,
            "finding_correction_not_fresh",
        ):
            usability.validate_evidence(value)

        value = self._release_evidence()
        _, finding = self._add_blocked_origin(value, status="resolved")
        finding["correction"]["resolution_commit_ancestor_verified"] = False
        with self.assertRaisesRegex(
            usability.PolicyAdminUsabilityEvidenceError,
            "resolution_commit_ancestor_not_verified",
        ):
            usability.validate_evidence(value)

    def test_resolved_blocker_is_bound_to_the_exact_gated_release(self) -> None:
        mutations = (
            (
                "artifact digest",
                "corrected_release_digest",
                "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
            ),
            ("source commit", "corrected_release_source_commit", "d" * 40),
            (
                "publication time",
                "corrected_release_published_at",
                "2026-08-27T10:01:00Z",
            ),
        )
        for label, field, replacement in mutations:
            with self.subTest(label=label):
                value = self._release_evidence()
                _, finding = self._add_blocked_origin(value, status="resolved")
                finding["correction"][field] = replacement
                with self.assertRaisesRegex(
                    usability.PolicyAdminUsabilityEvidenceError,
                    "finding_correction_not_in_gated_release",
                ):
                    usability.validate_evidence(value)

    def test_broad_change_requires_every_current_session_in_affected_track_to_rerun(
        self,
    ) -> None:
        value = self._release_evidence()
        _, finding = self._add_blocked_origin(
            value,
            status="resolved",
            broad=True,
            corrected_at="2026-08-27T11:30:00Z",
            retest_session_id="paus:10000000-0000-4000-8000-000000000003",
        )
        value["release"]["published_at"] = "2026-08-27T11:30:00Z"
        offline_sessions = self._sessions(value, "offline")
        offline_sessions[0]["release_artifact_digest"] = (
            "sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
        )
        offline_sessions[1]["release_artifact_digest"] = (
            "sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
        )
        offline_sessions[3]["started_at"] = "2026-08-27T11:30:00Z"

        result = usability.validate_evidence(value)

        self.assertIn("broad_workflow_gate_not_fully_rerun", result["reasons"])

        for index, session in enumerate(offline_sessions[:5]):
            session["release_artifact_digest"] = value["release"]["artifact_digest"]
            session["started_at"] = f"2026-08-27T1{3 + index}:00:00Z"
        value["generated_at"] = "2026-08-27T20:00:00Z"
        result = usability.validate_evidence(value)
        self.assertNotIn("broad_workflow_gate_not_fully_rerun", result["reasons"])
        self.assertTrue(result["ready_for_v1_policy_admin_claim"])

    def test_cli_exit_codes_distinguish_error_incomplete_and_passed(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(usability.main([str(FIXTURE_PATH)]), 2)
        self.assertIn("synthetic_fixture_requires_explicit_flag", stderr.getvalue())

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(
                usability.main([str(FIXTURE_PATH), "--allow-synthetic-fixture"]),
                0,
            )
        self.assertFalse(
            json.loads(stdout.getvalue())["ready_for_v1_policy_admin_claim"]
        )

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "release.json"
            incomplete = self._release_evidence()
            incomplete["operator_attestation"][  # type: ignore[index]
                "distinct_humans_verified_off_repository"
            ] = False
            path.write_text(json.dumps(incomplete), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(usability.main([str(path)]), 1)

            path.write_text(json.dumps(self._release_evidence()), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(usability.main([str(path)]), 0)

    def test_cli_rejects_duplicate_json_members_without_echoing_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "duplicate.json"
            path.write_text(
                '{"schema_id":"first","schema_id":"private-value"}',
                encoding="utf-8",
            )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(usability.main([str(path)]), 2)
        self.assertIn("duplicate_json_member", stderr.getvalue())
        self.assertNotIn("private-value", stderr.getvalue())

    def test_evidence_reader_rejects_links_special_files_and_oversized_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            link = root / "evidence-link.json"
            link.symlink_to(FIXTURE_PATH)
            with self.assertRaisesRegex(
                usability.PolicyAdminUsabilityEvidenceError,
                "evidence_not_regular",
            ):
                usability._read_evidence(link)

            with self.assertRaisesRegex(
                usability.PolicyAdminUsabilityEvidenceError,
                "evidence_not_regular",
            ):
                usability._read_evidence(root)

            fifo = root / "evidence.pipe"
            os.mkfifo(fifo)
            with (
                mock.patch.object(
                    usability.os,
                    "open",
                    side_effect=AssertionError("FIFO must be rejected before open"),
                ),
                self.assertRaisesRegex(
                    usability.PolicyAdminUsabilityEvidenceError,
                    "evidence_not_regular",
                ),
            ):
                usability._read_evidence(fifo)

            oversized = root / "oversized.json"
            oversized.write_bytes(b" " * (usability._MAX_EVIDENCE_BYTES + 1))
            with self.assertRaisesRegex(
                usability.PolicyAdminUsabilityEvidenceError,
                "evidence_too_large",
            ):
                usability._read_evidence(oversized)

            nested = root / "nested.json"
            nested.write_text("[]", encoding="utf-8")
            stderr = io.StringIO()
            with (
                mock.patch.object(usability.json, "loads", side_effect=RecursionError),
                contextlib.redirect_stderr(stderr),
            ):
                self.assertEqual(usability.main([str(nested)]), 2)
            self.assertIn("evidence_invalid_json", stderr.getvalue())

            stderr = io.StringIO()
            with (
                mock.patch.object(
                    usability.json,
                    "loads",
                    side_effect=ValueError("oversized integer details"),
                ),
                contextlib.redirect_stderr(stderr),
            ):
                self.assertEqual(usability.main([str(nested)]), 2)
            self.assertIn("evidence_invalid_json", stderr.getvalue())
            self.assertNotIn("oversized integer details", stderr.getvalue())

            huge_integer = root / "huge-integer.json"
            huge_integer.write_text(
                '{"value":' + ("9" * 5_000) + "}",
                encoding="utf-8",
            )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(usability.main([str(huge_integer)]), 2)
            self.assertIn("evidence_invalid_json", stderr.getvalue())

    def test_source_distribution_and_docs_carry_the_gate_contract(self) -> None:
        from tools import verify_core_wheel

        manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
        guide = (ROOT / "docs" / "POLICY_ADMIN_USABILITY.md").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "include tools/verify_policy_admin_usability_evidence.py\n", manifest
        )
        self.assertIn("recursive-include docs *.md\n", manifest)
        self.assertIn("recursive-include tests *.py *.json\n", manifest)
        self.assertIn("include examples/policy-admin-usability-baseline.json\n", manifest)
        self.assertIn("include examples/policy-admin-usability-scenarios.json\n", manifest)
        self.assertIn("0/5", guide)
        self.assertIn("0/3", guide)
        self.assertIn("Issue #110", guide)
        self.assertIn("separate", guide)
        self.assertEqual(
            set(verify_core_wheel.REQUIRED_POLICY_ADMIN_USABILITY_SDIST_PATHS),
            {
                "config.example.json",
                "docs/POLICY_ADMIN_USABILITY.md",
                "examples/policy-admin-usability-baseline.json",
                "examples/policy-admin-usability-scenarios.json",
                "tools/verify_policy_admin_usability_evidence.py",
            },
        )

    def test_shipped_offline_task_assets_produce_the_expected_semantic_change(
        self,
    ) -> None:
        from hormuz.config import GatewayConfig
        from hormuz.policy_analysis import (
            compare_policy_documents,
            evaluate_policy_scenario_suite,
        )
        from hormuz.policy_document import PolicyDocument
        from hormuz.policy_scenarios import PolicyScenarioSuite
        from hormuz.store import MonthlyTotals, UsageStore

        baseline_path = ROOT / "examples" / "policy-admin-usability-baseline.json"
        suite_path = ROOT / "examples" / "policy-admin-usability-scenarios.json"
        self.assertEqual(
            "sha256:" + hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
            usability._OFFLINE_BASELINE_ASSET_SHA256,
        )
        self.assertEqual(
            "sha256:" + hashlib.sha256(suite_path.read_bytes()).hexdigest(),
            usability._OFFLINE_SCENARIO_SUITE_ASSET_SHA256,
        )
        context = GatewayConfig.load_policy_analysis_context(ROOT / "config.example.json")
        baseline_mapping = json.loads(
            baseline_path.read_text(encoding="utf-8")
        )
        suite_mapping = json.loads(
            suite_path.read_text(encoding="utf-8")
        )
        baseline = PolicyDocument.from_mapping(baseline_mapping, config=context)
        candidate_mapping = copy.deepcopy(baseline_mapping)
        candidate_mapping["policies"]["organization"]["max_output_tokens"] = 4000
        candidate = PolicyDocument.from_mapping(candidate_mapping, config=context)
        suite = PolicyScenarioSuite.from_mapping(suite_mapping)
        self.assertEqual(
            baseline.content_sha256,
            usability._OFFLINE_BASELINE_CONTENT_SHA256,
        )
        self.assertEqual(
            candidate.content_sha256,
            usability._OFFLINE_CANDIDATE_CONTENT_SHA256,
        )
        self.assertEqual(
            suite.content_sha256,
            usability._OFFLINE_SCENARIO_SUITE_CONTENT_SHA256,
        )

        comparison = compare_policy_documents(baseline, candidate)
        self.assertEqual(
            [(change.path, change.before, change.after) for change in comparison.changes],
            [("policies.organization.max_output_tokens", 16000, 4000)],
        )

        with tempfile.TemporaryDirectory() as temporary:
            usage = UsageStore(Path(temporary) / "usage.sqlite3")
            self.assertEqual(usage.monthly_totals(organization_id="xpounder"), MonthlyTotals())
            evaluation = evaluate_policy_scenario_suite(
                config=context,
                usage_store=usage,
                suite=suite,
                baseline=baseline,
                candidate=candidate,
            )

        self.assertEqual(evaluation.changed_count, 1)
        self.assertEqual(evaluation.baseline_allowed_count, 1)
        self.assertEqual(evaluation.candidate_allowed_count, 1)
        self.assertEqual(evaluation.scenarios[0].baseline_decision.max_output_tokens, 16000)
        self.assertEqual(evaluation.scenarios[0].candidate_decision.max_output_tokens, 4000)


if __name__ == "__main__":
    unittest.main()
