from __future__ import annotations

import copy
import unittest

from tools.evaluate_context_compaction import (
    EvaluationError,
    fixture_cases,
    generate_manifest,
    run_manifest,
    score,
    validate_live_budget,
)


COUNTERS = {"cl100k_base": len, "o200k_base": len}


class ContextEvaluationTests(unittest.TestCase):
    def test_manifest_has_twelve_cases_five_pairs_and_alternating_order(self) -> None:
        self.assertEqual(len(fixture_cases()), 12)
        manifest = generate_manifest(
            model="fixture-model", protocol="responses", repetitions=5, counters=COUNTERS
        )
        entries = manifest["entries"]
        self.assertEqual(len(entries), 120)
        first = [entry["arm"] for entry in entries if entry["case_id"] == "case-01"]
        self.assertEqual(first[:4], ["original", "compact", "compact", "original"])

    def test_scorer_is_type_aware_and_reports_paired_regression(self) -> None:
        manifest = generate_manifest(
            model="fixture-model", protocol="responses", repetitions=5, counters=COUNTERS
        )
        outcomes = []
        for entry in manifest["entries"]:
            answer = copy.deepcopy(entry["expected"])
            if entry["case_id"] == "case-04" and entry["arm"] == "compact" and entry["repetition"] == 1:
                answer = {"values": [None, 0, 0, ""]}
            outcomes.append({
                "case_id": entry["case_id"], "arm": entry["arm"],
                "repetition": entry["repetition"], "model": entry["model"],
                "settings_digest": entry["settings_digest"], "answer": answer,
                "success": True, "error": None, "latency_ms": 10.5,
                "usage": {"input_tokens": 100, "output_tokens": 5, "reasoning_tokens": None},
            })
        result = score(manifest, outcomes)
        self.assertEqual(result["exact_invariant_failures"], 1)
        self.assertEqual(result["paired_regressions"], [{"case_id": "case-04", "repetition": 1}])
        self.assertIsNone(result["reported_usage_by_category"]["compact"]["reasoning_tokens"])

    def test_scorer_rejects_duplicate_missing_and_mismatched_records(self) -> None:
        manifest = generate_manifest(
            model="fixture-model", protocol="responses", repetitions=5, counters=COUNTERS
        )
        entry = manifest["entries"][0]
        outcome = {
            "case_id": entry["case_id"], "arm": entry["arm"], "repetition": entry["repetition"],
            "model": "wrong", "settings_digest": entry["settings_digest"],
            "answer": entry["expected"], "success": True, "error": None,
            "latency_ms": 1, "usage": {},
        }
        with self.assertRaises(EvaluationError):
            score(manifest, [outcome])

    def test_live_budget_requires_the_declared_worst_case_to_fit(self) -> None:
        validate_live_budget(
            request_cap=120,
            spend_ceiling_usd="12.00",
            maximum_cost_per_request_usd="0.10",
        )
        for ceiling, per_request in (("11.99", "0.10"), ("nan", "0.10"), ("12", "0")):
            with self.subTest(ceiling=ceiling, per_request=per_request):
                with self.assertRaises(EvaluationError):
                    validate_live_budget(
                        request_cap=120,
                        spend_ceiling_usd=ceiling,
                        maximum_cost_per_request_usd=per_request,
                    )

    def test_live_runner_rejects_an_invalid_loopback_port_as_a_fixed_error(self) -> None:
        with self.assertRaisesRegex(EvaluationError, "local_helper_endpoint_required"):
            run_manifest(
                {"entries": [{}]},
                endpoint="http://127.0.0.1:not-a-port",
                credential="hox_l_" + "A" * 43,
                request_cap=1,
            )


if __name__ == "__main__":
    unittest.main()
