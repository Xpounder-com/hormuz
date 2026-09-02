"""#8 native-attempt finance preflight contract tests."""

from __future__ import annotations

import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tools import verify_finance_native_attempt_transition_plan as verifier
from tools.verify_finance_native_attempt_transition_plan import (
    FinanceNativeAttemptTransitionError,
)


ROOT = Path(__file__).resolve().parents[1]


class FinanceNativeAttemptTransitionPlanTests(unittest.TestCase):
    def plan(self):
        return json.loads((ROOT / verifier.PLAN_PATH).read_bytes())

    def contract(self):
        return json.loads((ROOT / verifier.CONTRACT_PATH).read_bytes())

    def implementation(self):
        return json.loads((ROOT / verifier.IMPLEMENTATION_PLAN_PATH).read_bytes())

    def test_checkpoint_is_runtime_candidate_not_runtime_acceptance_or_release(self):
        result = verifier.verify_finance_native_attempt_transition_plan(ROOT)
        self.assertEqual(result["status"], "finance_native_attempt_runtime_candidate_verified")
        self.assertEqual(result["feature_issue"], 8)
        self.assertEqual(result["gate_issue"], 214)
        self.assertEqual(result["predecessor_sqlite_schema_version"], 10)
        self.assertEqual(result["predecessor_postgresql_schema_version"], 14)
        self.assertEqual(result["planned_sqlite_schema_version"], 11)
        self.assertEqual(result["planned_postgresql_schema_version"], 15)
        self.assertEqual(result["provider_profile_count"], 2)
        self.assertEqual(result["new_table_count"], 1)
        self.assertEqual(result["altered_table_count"], 2)
        self.assertTrue(result["audit_chain_source_union_expanded"])
        self.assertEqual(result["request_attempt_price_binding_column_count"], 5)
        self.assertTrue(result["post_migration_price_binding_required"])
        self.assertFalse(result["missing_usage_estimate_is_zero"])
        self.assertEqual(result["new_http_routes"], 0)
        self.assertEqual(result["new_cli_commands"], 0)
        self.assertTrue(result["budget_runtime_source_verified"])
        self.assertTrue(result["native_request_cost_capture_implemented"])
        self.assertTrue(result["native_attempt_preflight_accepted"])
        for field in (
            "native_attempt_runtime_accepted",
            "finance_implemented",
            "live_finance_verified",
            "final_candidate_accepted",
            "released",
        ):
            self.assertFalse(result[field])

    def test_every_plan_and_contract_section_is_digest_frozen(self):
        for value, validate, code in (
            (
                self.plan(),
                verifier.validate_finance_native_attempt_plan,
                "finance_native_preflight_contract_changed",
            ),
            (
                self.implementation(),
                verifier.validate_finance_native_attempt_implementation_plan,
                "finance_native_runtime_contract_changed",
            ),
            (
                self.contract(),
                verifier.validate_finance_attempt_evidence_contract,
                "finance_attempt_evidence_contract_changed",
            ),
        ):
            for key in value:
                with self.subTest(code=code, key=key):
                    changed = copy.deepcopy(value)
                    changed[key] = "SYNTHETIC_EXCLUDED"
                    with self.assertRaisesRegex(
                        FinanceNativeAttemptTransitionError,
                        code,
                    ):
                        validate(changed)

    def test_validator_rejects_non_json_nonfinite_and_invalid_unicode(self):
        for value in (
            object(),
            float("nan"),
            float("inf"),
            {"x": "\ud800"},
        ):
            with self.subTest(value=repr(value)), self.assertRaises(
                FinanceNativeAttemptTransitionError
            ) as caught:
                verifier.validate_finance_native_attempt_plan(value)
            self.assertNotIn("SYNTHETIC_EXCLUDED", str(caught.exception))

    def test_budget_runtime_source_is_required(self):
        with mock.patch.object(
            verifier,
            "verify_budget_implementation_plan",
            side_effect=verifier.BudgetTransitionError("synthetic"),
        ):
            with self.assertRaisesRegex(
                FinanceNativeAttemptTransitionError,
                "finance_native_budget_predecessor_invalid",
            ):
                verifier.verify_finance_native_attempt_transition_plan(ROOT)

    def test_complete_source_kit_is_required(self):
        with mock.patch.object(verifier, "REQUIRED_FILES", ("missing-file",)):
            with self.assertRaisesRegex(
                FinanceNativeAttemptTransitionError,
                "finance_native_source_kit_incomplete",
            ):
                verifier.verify_finance_native_attempt_transition_plan(ROOT)

    def test_contract_keeps_attempt_cardinality_and_unknowns_explicit(self):
        contract = self.contract()
        cardinality = contract["terminal_cardinality"]
        self.assertEqual(cardinality["pending"], "sidecar_prohibited")
        self.assertIn("exactly_one", cardinality["succeeded"])
        self.assertIn("exactly_one", cardinality["failed"])
        self.assertIn("exactly_one", cardinality["rate_limited"])
        self.assertIn("usage_event_null", cardinality["outcome_unknown"])
        self.assertIn("no_backfill", cardinality["pre_migration_terminal_attempt"])
        self.assertEqual(
            contract["observation"]["null_semantics"],
            "unknown_or_not_returned_never_zero",
        )
        self.assertEqual(
            contract["observation"]["zero_semantics"],
            "provider_explicitly_returned_zero",
        )

    def test_contract_separates_native_normalized_and_cost_provenance(self):
        contract = self.contract()
        self.assertEqual(
            contract["observation"]["native_payload"],
            "bounded_canonical_json_of_allowlisted_fields_only",
        )
        self.assertEqual(len(contract["provider_profiles"]), 2)
        openai = contract["provider_profiles"]["openai.responses.usage.v1"]
        anthropic = contract["provider_profiles"]["anthropic.messages.usage.v1"]
        self.assertEqual(
            openai["required_paths"],
            ["input_tokens", "output_tokens", "total_tokens"],
        )
        self.assertEqual(
            openai["terminal_metadata_allowlisted_paths"],
            ["service_tier"],
        )
        self.assertEqual(
            anthropic["required_paths"],
            ["input_tokens", "output_tokens"],
        )
        self.assertIn("ascii_safe_identifier", anthropic["inference_geo_syntax"])
        self.assertEqual(len(contract["normalized_metrics"]["columns"]), 11)
        self.assertEqual(
            contract["normalized_metrics"]["dimensions"],
            ["provider_service_tier", "provider_inference_geo"],
        )
        self.assertEqual(
            contract["normalized_metrics"]["derivation"],
            "only_lossless_provider_profile_rules_may_populate_a_normalized_column",
        )
        estimate = contract["configured_estimate"]
        self.assertEqual(estimate["basis"], "configured_rate_card_estimate")
        self.assertEqual(
            estimate["availability_states"],
            ["available", "unavailable"],
        )
        self.assertEqual(estimate["missing_usage"], "unavailable_never_zero")
        self.assertIn("never_substituted", estimate["budget_reservation"])
        self.assertFalse(estimate["provider_final"])
        self.assertFalse(estimate["reprice_historical_attempts"])
        self.assertEqual(
            estimate["rate_card_coordinates"],
            ["rate_card_id", "rate_card_version", "rate_card_digest"],
        )

    def test_contract_freezes_crash_safe_begin_time_price_binding(self):
        contract = self.contract()
        binding = contract["attempt_price_binding"]
        self.assertIn("before_provider_egress", binding["capture_time"])
        self.assertEqual(
            binding["fields"],
            [
                "configured_rate_card_state",
                "configured_rate_card_id",
                "configured_rate_card_version",
                "configured_rate_card_digest",
                "configured_rate_card_currency",
            ],
        )
        self.assertEqual(
            binding["post_migration_attempt"],
            "state_configured_and_all_coordinates_required_and_immutable",
        )
        self.assertIn("stale_sweep", binding["terminal_sidecar"])
        self.assertEqual(
            contract["storage"]["altered_table"],
            "gateway_request_attempts",
        )
        self.assertEqual(contract["bounds"]["integer_max"], 2**63 - 1)

    def test_contract_requires_atomic_sidecar_and_audit_without_replay(self):
        contract = self.contract()
        self.assertIn(
            "finance_sidecar",
            contract["transaction"]["terminal_write"],
        )
        self.assertIn(
            "commit_or_roll_back_together",
            contract["transaction"]["terminal_write"],
        )
        self.assertEqual(
            contract["transaction"]["provider_egress"],
            "never_triggered_by_persistence_retry_recovery_or_reporting",
        )
        self.assertTrue(contract["storage"]["append_only"])
        self.assertFalse(contract["storage"]["historical_backfill"])
        self.assertFalse(contract["compatibility"]["automatic_provider_replay"])

    def test_plan_reserves_only_additive_schema_versions(self):
        plan = self.plan()
        self.assertEqual(plan["transitions"], {
            "sqlite": {"from": 10, "to": 11},
            "postgresql": {"from": 14, "to": 15},
        })
        self.assertEqual(
            plan["planned_storage"]["tables"],
            ["gateway_finance_attempt_evidence"],
        )
        attempt_change = plan["planned_storage"]["altered_tables"][
            "gateway_request_attempts"
        ]
        self.assertEqual(len(attempt_change["additive_columns"]), 5)
        self.assertIn("legacy_unavailable", attempt_change["legacy_rows"])
        self.assertIn(
            "before_provider_egress",
            plan["atomic_terminal_write"]["begin_boundary"],
        )
        self.assertEqual(plan["compatibility"]["new_http_routes"], [])
        self.assertEqual(plan["compatibility"]["new_cli_commands"], [])
        self.assertFalse(plan["compatibility"]["historical_usage_repricing"])
        self.assertFalse(plan["compatibility"]["historical_attempt_backfill"])
        self.assertFalse(plan["implementation_decision"]["provider_final"])

    def test_runtime_plan_records_the_required_audit_chain_successor_change(self):
        plan = self.implementation()
        self.assertEqual(plan["schema_version"], 4)
        self.assertTrue(plan["candidate"]["native_request_cost_capture_implemented"])
        self.assertFalse(plan["candidate"]["native_attempt_runtime_accepted"])
        self.assertEqual(
            plan["storage"]["new_tables"],
            ["gateway_finance_attempt_evidence"],
        )
        self.assertEqual(
            set(plan["storage"]["altered_tables"]),
            {"gateway_request_attempts", "gateway_audit_chain_entries"},
        )
        correction = plan["storage"]["altered_tables"]["gateway_audit_chain_entries"]
        self.assertIn("each_finance_sidecar", correction["why_required"])
        self.assertIn("copies_every_predecessor_entry", correction["sqlite"])
        self.assertIn("gain_only", correction["postgresql"])
        self.assertIn("must_remain_unchanged", correction["rollback_preservation"])
        self.assertEqual(plan["compatibility"]["new_http_routes"], [])
        self.assertEqual(plan["compatibility"]["new_cli_commands"], [])
        self.assertFalse(plan["runtime"]["provider_final"])

    def test_cli_rejects_missing_or_wrong_predecessor_archive(self):
        for payload in (None, b"SYNTHETIC_EXCLUDED"):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "predecessor.tar"
                if payload is not None:
                    path.write_bytes(payload)
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(
                        verifier.main(["--predecessor-archive", str(path)]),
                        1,
                    )
                self.assertEqual(
                    output.getvalue().strip(),
                    "finance_native_predecessor_archive_invalid",
                )


if __name__ == "__main__":
    unittest.main()
