from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import unittest

from hormuz.portfolio_wire import validate
from hormuz.scorecard_kernel import ScorecardKernelError, build_scorecard_evaluation


FIXTURE = Path(__file__).parent / "fixtures" / "scorecard" / "runtime-v1.json"


class ScorecardKernelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def build(self, value=None):
        return build_scorecard_evaluation(deepcopy(
            self.fixture["input"] if value is None else value
        ))

    def invalid(self, value):
        with self.assertRaisesRegex(
            ScorecardKernelError, "^scorecard_evidence_invalid$"
        ):
            self.build(value)

    def test_frozen_scorecard_vector_is_exact_and_wire_valid(self):
        result = self.build()
        self.assertEqual(result, self.fixture["expected"])
        validate(result["scorecard"], "hormuz.model-scorecard")
        self.assertEqual(result["scorecard"]["state"], "eligible")
        self.assertEqual(result["scorecard"]["evidence_level"], "associated")
        self.assertEqual(result["scorecard"]["pareto_cohort_ids"], ["baseline", "efficient"])
        self.assertIsNone(result["scorecard"]["controlled_design"])

    def test_input_order_does_not_change_snapshot_or_lineage(self):
        selected = deepcopy(self.fixture["input"])
        selected["cohorts"].reverse()
        selected["eligibility_policy"]["required_strata"].reverse()
        for cohort in selected["cohorts"]:
            cohort["connector_ids"].reverse()
            cohort["strata"].reverse()
            for stratum in cohort["strata"]:
                stratum["work_items"].reverse()
                for work_item in stratum["work_items"]:
                    work_item["attempts"].reverse()
        self.assertEqual(self.build(selected), self.build())

    def test_work_items_not_attempts_are_independent_samples(self):
        result = self.build()
        baseline = next(
            item for item in result["scorecard"]["cohorts"]
            if item["cohort_id"] == "baseline"
        )
        self.assertEqual(baseline["eligibility"]["sample_count"], 6)
        self.assertEqual(baseline["drivers"]["actual_model_mix"][0]["attempt_count"], 7)
        uncertainty = next(
            item for item in result["uncertainty"]
            if item["cohort_id"] == "baseline"
            and item["metric"] == "quality_qualified_cost_per_accepted_work_item"
        )
        self.assertEqual(
            (uncertainty["method"], uncertainty["cluster_count"]),
            ("work_item_cluster_jackknife_95", 6),
        )

    def test_cost_bases_are_separate_and_all_attempt_costs_reach_the_numerator(self):
        result = self.build()
        baseline = next(
            item for item in result["scorecard"]["cohorts"]
            if item["cohort_id"] == "baseline"
        )
        components = {item["basis"]: item for item in baseline["cost_components"]}
        self.assertEqual(set(components), {"provider_final", "configured_rate_card_estimate"})
        metric = baseline["metrics"]["quality_qualified_cost_per_accepted_work_item"]
        self.assertEqual(metric["numerator"], components["provider_final"]["amount"])
        self.assertEqual(metric["denominator"], "5")
        self.assertEqual(baseline["cost_basis"], "provider_final")

    def test_requested_routed_and_actual_models_remain_distinct_dimensions(self):
        result = self.build()
        dimensions = next(
            item for item in result["cohort_dimensions"]
            if item["cohort_id"] == "efficient"
        )
        self.assertEqual(dimensions["requested_model_ids"], ["requested-model-efficient"])
        self.assertEqual(dimensions["routed_model_ids"], ["routed-model-efficient"])
        self.assertEqual(dimensions["actual_model"], {
            "provider_id": "provider-a",
            "model_id": "model-efficient",
            "model_version": "2026-08-01",
        })
        self.assertEqual(dimensions["client_id"], "codex-app")

    def test_failed_guarded_stratum_cannot_be_hidden_by_lower_cost(self):
        result = self.build()
        guarded = next(
            item for item in result["scorecard"]["cohorts"]
            if item["cohort_id"] == "guarded"
        )
        self.assertEqual(guarded["eligibility"]["status"], "inconclusive")
        self.assertEqual(guarded["guardrails"]["quality"]["state"], "fail")
        self.assertNotIn("guarded", result["scorecard"]["pareto_cohort_ids"])
        self.assertEqual(
            guarded["metrics"]["quality_qualified_cost_per_accepted_work_item"]["status"],
            "inconclusive",
        )

    def test_missing_actual_version_makes_comparison_inconclusive(self):
        selected = deepcopy(self.fixture["input"])
        cohort = next(item for item in selected["cohorts"] if item["cohort_id"] == "efficient")
        cohort["actual_model"]["model_version"] = None
        for stratum in cohort["strata"]:
            for work_item in stratum["work_items"]:
                for attempt in work_item["attempts"]:
                    attempt["actual_model"]["model_version"] = None
        result = self.build(selected)
        efficient = next(
            item for item in result["scorecard"]["cohorts"]
            if item["cohort_id"] == "efficient"
        )
        self.assertEqual(efficient["eligibility"]["status"], "inconclusive")
        self.assertEqual(result["scorecard"]["state"], "inconclusive")
        self.assertNotIn("efficient", result["scorecard"]["pareto_cohort_ids"])

    def test_zero_or_missing_coverage_is_inconclusive_not_fabricated_zero(self):
        for numerator, denominator in ((None, None), ("0", "0")):
            with self.subTest(numerator=numerator, denominator=denominator):
                selected = deepcopy(self.fixture["input"])
                cohort = next(item for item in selected["cohorts"] if item["cohort_id"] == "efficient")
                cohort["coverage"]["pricing"].update({
                    "numerator": numerator, "denominator": denominator,
                })
                result = self.build(selected)
                efficient = next(
                    item for item in result["scorecard"]["cohorts"]
                    if item["cohort_id"] == "efficient"
                )
                self.assertEqual(efficient["eligibility"]["status"], "inconclusive")
                self.assertIsNone(result["scorecard"]["coverage"]["pricing"]["ratio"])

    def test_duplicate_fact_digest_across_cohorts_is_rejected(self):
        selected = deepcopy(self.fixture["input"])
        selected["cohorts"][1]["strata"][0]["work_items"][0]["source_digest"] = (
            selected["cohorts"][0]["strata"][0]["work_items"][0]["source_digest"]
        )
        self.invalid(selected)

    def test_duplicate_attempt_identity_or_non_contiguous_retry_is_rejected(self):
        selected = deepcopy(self.fixture["input"])
        work = selected["cohorts"][0]["strata"][0]["work_items"][1]
        work["attempts"][1]["attempt_id"] = work["attempts"][0]["attempt_id"]
        self.invalid(selected)
        selected = deepcopy(self.fixture["input"])
        selected["cohorts"][0]["strata"][0]["work_items"][1]["attempts"][1]["retry_ordinal"] = 4
        self.invalid(selected)

    def test_open_content_or_person_dimension_is_rejected(self):
        for field in ("prompt", "response", "title", "body", "employee_id", "actor_id"):
            with self.subTest(field=field):
                selected = deepcopy(self.fixture["input"])
                selected["cohorts"][0]["strata"][0]["work_items"][0][field] = "forbidden"
                self.invalid(selected)

    def test_cost_basis_relabeling_and_cross_model_pooling_are_rejected(self):
        selected = deepcopy(self.fixture["input"])
        cohort = selected["cohorts"][0]
        cohort["cost_basis"] = "provider_aggregate"
        cohort["strata"][0]["work_items"][0]["attempts"][0]["cost_components"].append({
            "basis": "provider_aggregate",
            "amount": "78",
            "currency": "USD",
            "rate_card": None,
            "provenance_digest": "0" * 64,
        })
        self.invalid(selected)
        selected = deepcopy(self.fixture["input"])
        attempt = selected["cohorts"][0]["strata"][0]["work_items"][0]["attempts"][0]
        attempt["actual_model"]["model_id"] = "different-actual-model"
        self.invalid(selected)

    def test_version_lineage_and_closed_window_are_enforced(self):
        selected = deepcopy(self.fixture["input"])
        selected.update({"version": 2, "supersedes_version": None})
        self.invalid(selected)
        selected = deepcopy(self.fixture["input"])
        selected["evaluated_at"] = "2026-09-07T00:00:00Z"
        self.invalid(selected)

    def test_noncanonical_financial_values_are_rejected(self):
        for invalid in (1.0, "01", "1e0", "NaN", "Infinity", "-0", True):
            with self.subTest(invalid=invalid):
                selected = deepcopy(self.fixture["input"])
                selected["cohorts"][0]["strata"][0]["work_items"][0]["attempts"][0][
                    "cost_components"
                ][0]["amount"] = invalid
                self.invalid(selected)


if __name__ == "__main__":
    unittest.main()
