from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from hormuz.policy_analysis import (
    compare_policy_documents,
    evaluate_policy_scenario_suite,
    preview_policy_request,
)
from hormuz.policy_document import PolicyDocument
from hormuz.policy_scenarios import PolicyScenarioSuite
from hormuz.portfolio_wire import validate
from hormuz.recommendation_kernel import build_recommendation_evaluation
from hormuz.store import MonthlyTotals

from ._portfolio_fixture import ADMIN, registry_config


FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "recommendation" / "decision-v1.json"
)


class RecommendationKernelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            set(self.fixture),
            {
                "schema_id",
                "schema_version",
                "scorecard_fixture",
                "created_at",
                "scorecard_evaluation_digest",
                "baseline_policy",
                "candidate_policy",
                "scenario_suite",
                "preview_request",
                "generation_request",
                "expected_evaluation",
                "accepted_decision",
                "rejected_decision",
                "suppression_cases",
            },
        )
        self.assertEqual(
            (self.fixture["schema_id"], self.fixture["schema_version"]),
            ("hormuz.recommendation-decision-fixture", 1),
        )
        scorecard_path = Path(self.fixture["scorecard_fixture"]["path"])
        scorecard_bytes = scorecard_path.read_bytes()
        self.assertEqual(
            hashlib.sha256(scorecard_bytes).hexdigest(),
            self.fixture["scorecard_fixture"]["sha256"],
        )
        self.scorecard_evaluation = json.loads(scorecard_bytes)["expected"]
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.config = registry_config(Path(temporary.name))
        self.baseline = PolicyDocument.from_mapping(
            self.fixture["baseline_policy"], config=self.config
        )
        self.candidate = PolicyDocument.from_mapping(
            self.fixture["candidate_policy"], config=self.config
        )
        self.scenarios = PolicyScenarioSuite.from_mapping(
            self.fixture["scenario_suite"]
        )
        self.usage = mock.Mock()
        self.usage.monthly_totals.return_value = MonthlyTotals()

    def evaluate(
        self,
        scorecard_evaluation=None,
        *,
        preview_overrides=None,
        evaluated_at=None,
        usage_totals=None,
    ):
        preview = dict(self.fixture["preview_request"])
        preview.update({} if preview_overrides is None else preview_overrides)
        evaluated_at = evaluated_at or datetime.fromisoformat(
            self.fixture["created_at"].replace("Z", "+00:00")
        )
        self.usage.monthly_totals.return_value = (
            MonthlyTotals() if usage_totals is None else usage_totals
        )
        comparison = compare_policy_documents(self.baseline, self.candidate)
        preview_result = preview_policy_request(
            config=self.config,
            usage_store=self.usage,
            identity=self.config.identities_by_token[ADMIN],
            baseline=self.baseline,
            candidate=self.candidate,
            client=preview["client"],
            protocol=preview["protocol"],
            requested_model=preview["requested_model"],
            requested_output_tokens=preview["requested_output_tokens"],
            evaluated_at=evaluated_at,
        )
        scenario_result = evaluate_policy_scenario_suite(
            config=self.config,
            usage_store=self.usage,
            suite=self.scenarios,
            baseline=self.baseline,
            candidate=self.candidate,
            evaluated_at=evaluated_at,
        )
        return build_recommendation_evaluation(
            request=self.fixture["generation_request"],
            scorecard_evaluation=(
                self.scorecard_evaluation
                if scorecard_evaluation is None
                else scorecard_evaluation
            ),
            scorecard_evaluation_digest=self.fixture[
                "scorecard_evaluation_digest"
            ],
            created_at=self.fixture["created_at"],
            comparison=comparison,
            preview=preview_result,
            scenarios=scenario_result,
            budget_bindings=[],
            active_policy_version=self.baseline.version_id,
        )

    def test_frozen_evaluation_and_decision_requests_are_exact(self) -> None:
        evaluation = self.evaluate()
        self.assertEqual(evaluation, self.fixture["expected_evaluation"])
        assert evaluation is not None
        self.assertEqual(
            self.fixture["accepted_decision"]["pre_apply_evidence"],
            evaluation["pre_apply_evidence"],
        )
        validate(
            self.fixture["accepted_decision"],
            "hormuz.policy-recommendation-decision-request",
        )
        validate(
            self.fixture["rejected_decision"],
            "hormuz.policy-recommendation-decision-request",
        )
        self.assertFalse(evaluation["recommendation"]["automatic_application"])

    def test_request_preview_evidence_binds_request_time_and_usage_snapshot(self) -> None:
        baseline = self.evaluate()["pre_apply_evidence"]["request_preview"][
            "digest"
        ]
        request_changed = self.evaluate(
            preview_overrides={"requested_output_tokens": 749}
        )["pre_apply_evidence"]["request_preview"]["digest"]
        evaluated_at = datetime.fromisoformat(
            self.fixture["created_at"].replace("Z", "+00:00")
        )
        time_changed = self.evaluate(
            evaluated_at=evaluated_at + timedelta(seconds=1)
        )["pre_apply_evidence"]["request_preview"]["digest"]
        usage_changed = self.evaluate(
            usage_totals=MonthlyTotals(requests=1)
        )["pre_apply_evidence"]["request_preview"]["digest"]

        self.assertEqual(
            len({baseline, request_changed, time_changed, usage_changed}), 4
        )

    def test_every_required_coverage_gap_suppresses_a_recommendation(self) -> None:
        for field in self.fixture["suppression_cases"]["coverage_fields"]:
            with self.subTest(field=field):
                scorecard = deepcopy(self.scorecard_evaluation)
                scorecard["scorecard"]["coverage"][field].update(
                    {"numerator": "0", "ratio": 0.0, "reason_code": "below_threshold"}
                )
                self.assertIsNone(self.evaluate(scorecard))

    def test_sample_freshness_overlap_and_quality_fail_closed(self) -> None:
        for guardrail in self.fixture["suppression_cases"]["guardrails"]:
            with self.subTest(guardrail=guardrail):
                scorecard = deepcopy(self.scorecard_evaluation)
                for cohort in scorecard["scorecard"]["cohorts"]:
                    cohort["guardrails"][guardrail].update(
                        {"state": "fail", "reason_code": "below_threshold"}
                    )
                self.assertIsNone(self.evaluate(scorecard))

        scorecard = deepcopy(self.scorecard_evaluation)
        for cohort in scorecard["scorecard"]["cohorts"]:
            cohort["eligibility"].update(
                {"status": "inconclusive", "sample_count": 3}
            )
        self.assertIsNone(self.evaluate(scorecard))


if __name__ == "__main__":
    unittest.main()
