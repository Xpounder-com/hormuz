from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime
from pathlib import Path

from tools import run_v1_internal_repeatability as runner
from tools import verify_v1_internal_repeatability_evidence as repeatability


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = (
    ROOT
    / "tests"
    / "fixtures"
    / "v1_internal_repeatability"
    / "complete-synthetic-v1.json"
)


class V1InternalRepeatabilityEvidenceTests(unittest.TestCase):
    def _value(self) -> dict[str, object]:
        return json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_synthetic_fixture_is_structurally_complete_but_never_promotable(self) -> None:
        result = repeatability.validate_evidence(self._value())
        self.assertEqual(result["status"], "synthetic_fixture_valid")
        self.assertEqual(result["run_count"], 5)
        self.assertEqual(result["passed_run_count"], 5)
        self.assertFalse(result["eligible_for_v1_0_0_promotion"])
        self.assertEqual(
            result["claim_scope"], "internal_offline_policy_workflow_repeatability"
        )
        self.assertIn("external_human_usability", result["nonclaims"])
        self.assertIn("postgresql_policy_state", result["nonclaims"])

    def test_same_exact_contract_is_eligible_only_as_real_candidate_evidence(self) -> None:
        value = self._value()
        value["evidence_kind"] = "candidate_gate_evidence"
        result = repeatability.validate_evidence(value)
        self.assertEqual(result["status"], "eligible_for_unchanged_promotion")
        self.assertTrue(result["eligible_for_v1_0_0_promotion"])
        self.assertTrue(result["promotion_requires_exact_candidate_digest"])

    def test_exactly_five_unique_digest_bound_runs_are_required(self) -> None:
        for mutation, code in (
            (lambda value: value["runs"].pop(), "run_count_invalid"),
            (
                lambda value: value["runs"][1].update(
                    run_id=value["runs"][0]["run_id"]
                ),
                "run_id_duplicate",
            ),
            (
                lambda value: value["runs"][2].update(
                    candidate_artifact_digest="sha256:" + "b" * 64
                ),
                "run_3_binding_invalid",
            ),
        ):
            value = self._value()
            mutation(value)
            with self.assertRaisesRegex(
                repeatability.V1InternalRepeatabilityEvidenceError, f"^{code}$"
            ):
                repeatability.validate_evidence(value)

    def test_one_failed_run_is_valid_not_ready_evidence(self) -> None:
        value = self._value()
        value["evidence_kind"] = "candidate_gate_evidence"
        run = value["runs"][2]
        run["stages"][3].update(status="failed", exit_code=2)
        for stage in run["stages"][4:]:
            stage.update(status="not_attempted", exit_code=None)
        run["outcome"] = "failed"
        run["observed"] = None
        result = repeatability.validate_evidence(value)
        self.assertEqual(result["status"], "not_ready")
        self.assertEqual(result["passed_run_count"], 4)
        self.assertFalse(result["eligible_for_v1_0_0_promotion"])

    def test_stage_order_exit_codes_and_observations_fail_closed(self) -> None:
        mutations = (
            lambda value: value["runs"][0]["stages"][3].update(exit_code=0),
            lambda value: value["runs"][0]["stages"][1].update(
                status="failed", exit_code=255
            ),
            lambda value: value["runs"][0]["observed"].update(
                candidate_max_output_tokens=8_000
            ),
        )
        for mutation in mutations:
            value = self._value()
            mutation(value)
            with self.assertRaises(repeatability.V1InternalRepeatabilityEvidenceError):
                repeatability.validate_evidence(value)

    def test_claim_boundary_and_task_cannot_be_relaxed(self) -> None:
        for field in (
            "automation_only",
            "one_archive_used_for_all_runs",
            "provider_credentials_unset",
            "no_external_usability_claim",
        ):
            value = self._value()
            value["execution_attestation"][field] = False
            with self.assertRaisesRegex(
                repeatability.V1InternalRepeatabilityEvidenceError,
                "^execution_attestation_invalid$",
            ):
                repeatability.validate_evidence(value)

        value = self._value()
        value["task"]["before"] = 32_000
        with self.assertRaisesRegex(
            repeatability.V1InternalRepeatabilityEvidenceError,
            "^task_contract_invalid$",
        ):
            repeatability.validate_evidence(value)

    def test_chronology_and_duration_are_bound(self) -> None:
        value = self._value()
        value["runs"][1]["started_at"] = "2026-08-27T11:00:00.250000Z"
        with self.assertRaisesRegex(
            repeatability.V1InternalRepeatabilityEvidenceError,
            "^run_2_chronology_invalid$",
        ):
            repeatability.validate_evidence(value)

        value = self._value()
        value["runs"][0]["duration_seconds"] = 20
        with self.assertRaisesRegex(
            repeatability.V1InternalRepeatabilityEvidenceError,
            "^run_1_duration_mismatch$",
        ):
            repeatability.validate_evidence(value)

    def test_cli_requires_explicit_synthetic_permission(self) -> None:
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            self.assertEqual(repeatability.main([str(FIXTURE)]), 2)
        self.assertIn("synthetic_fixture_requires_explicit_permission", stderr.getvalue())

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            self.assertEqual(
                repeatability.main([str(FIXTURE), "--allow-synthetic-fixture"]),
                0,
            )
        self.assertEqual(
            json.loads(stdout.getvalue())["status"], "synthetic_fixture_valid"
        )

    def test_evidence_reader_rejects_symlinks_duplicate_members_and_nonfinite_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            linked = root / "linked.json"
            linked.symlink_to(FIXTURE)
            with self.assertRaisesRegex(
                repeatability.V1InternalRepeatabilityEvidenceError,
                "^evidence_not_regular$",
            ):
                repeatability._read_evidence(linked)

            duplicate = root / "duplicate.json"
            duplicate.write_text('{"schema_id":"a","schema_id":"b"}', encoding="utf-8")
            with self.assertRaisesRegex(
                repeatability.V1InternalRepeatabilityEvidenceError,
                "^duplicate_json_member$",
            ):
                repeatability._read_evidence(duplicate)

            nonfinite = root / "nonfinite.json"
            nonfinite.write_text('{"duration":NaN}', encoding="utf-8")
            with self.assertRaisesRegex(
                repeatability.V1InternalRepeatabilityEvidenceError,
                "^json_number_not_finite$",
            ):
                repeatability._read_evidence(nonfinite)

    def test_shipped_task_assets_match_the_versioned_contract(self) -> None:
        baseline = ROOT / "examples" / "policy-admin-usability-baseline.json"
        scenarios = ROOT / "examples" / "policy-admin-usability-scenarios.json"
        self.assertEqual(
            "sha256:" + hashlib.sha256(baseline.read_bytes()).hexdigest(),
            repeatability.BASELINE_ASSET_SHA256,
        )
        self.assertEqual(
            "sha256:" + hashlib.sha256(scenarios.read_bytes()).hexdigest(),
            repeatability.SCENARIO_SUITE_ASSET_SHA256,
        )
        candidate = json.loads(baseline.read_text(encoding="utf-8"))
        candidate["policies"]["organization"]["max_output_tokens"] = 4_000
        payload = (json.dumps(candidate, indent=2, sort_keys=True) + "\n").encode()
        self.assertEqual(
            "sha256:" + hashlib.sha256(payload).hexdigest(),
            repeatability.CANDIDATE_ASSET_SHA256,
        )

    def test_runner_environment_is_an_allowlist_and_evidence_builder_stays_nonhuman(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment = runner._safe_environment(Path(temporary))
        self.assertNotIn("OPENAI_API_KEY", environment)
        self.assertNotIn("ANTHROPIC_API_KEY", environment)
        self.assertNotIn("HORMUZ_TOKEN", environment)
        self.assertNotIn("HORMUZ_POLICY_ADMIN_TOKEN", environment)
        self.assertEqual(environment["PYTHONNOUSERSITE"], "1")
        self.assertIn("socket.create_connection = network_disabled", runner._OFFLINE_CLI_WRAPPER)

        value = self._value()
        manifest = {"candidate": copy.deepcopy(value["candidate"])}
        evidence = runner.build_evidence(manifest, copy.deepcopy(value["runs"]))
        self.assertEqual(evidence["evidence_kind"], "candidate_gate_evidence")
        self.assertEqual(evidence["execution_attestation"]["human_participant_count"], 0)
        self.assertTrue(
            repeatability.validate_evidence(evidence)[
                "eligible_for_v1_0_0_promotion"
            ]
        )

    def test_runner_preserves_the_selected_virtual_environment_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            interpreter = root / "python-real"
            interpreter.write_text("", encoding="utf-8")
            selected = root / "venv-python"
            selected.symlink_to(interpreter)

            absolute = runner._absolute_without_resolving(selected)

        self.assertEqual(absolute, selected)
        self.assertNotEqual(absolute, interpreter.resolve())

    def test_runner_rounds_evidence_time_up_for_whole_second_custody(self) -> None:
        moment = datetime(2026, 8, 28, 12, 0, 0, 123_456, tzinfo=UTC)

        self.assertEqual(
            runner._ceiling_second_timestamp(moment),
            "2026-08-28T12:00:01Z",
        )


if __name__ == "__main__":
    unittest.main()
