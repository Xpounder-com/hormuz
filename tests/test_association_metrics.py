from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import unittest

from hormuz.association_metrics import (
    AssociationMetricError,
    build_association_metric_vector,
)


FIXTURE = Path(__file__).parent / "fixtures" / "association" / "runtime-multisource-v1.json"


class AssociationMetricReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def build(self, value=None):
        selected = deepcopy(self.fixture["input"] if value is None else value)
        return build_association_metric_vector(
            context=selected["context"],
            attempts=tuple(selected["attempts"]),
            costs=tuple(selected["costs"]),
            outcomes=tuple(selected["outcomes"]),
            associations=tuple(selected["associations"]),
            deliveries=tuple(selected["deliveries"]),
            complete_connector_ids=tuple(selected["complete_connector_ids"]),
        )

    def invalid(self, selected):
        with self.assertRaisesRegex(
            AssociationMetricError, "^association_metric_evidence_invalid$"
        ):
            self.build(selected)

    def test_frozen_multisource_metric_vector_is_exact(self):
        result = self.build()
        self.assertEqual(result, self.fixture["expected"])
        self.assertEqual(
            result["denominators"],
            {
                "eligible_attempts": 3,
                "priced_attempts": 3,
                "eligible_outcome_events": 6,
                "unique_work_objects": 6,
                "eligible_association_candidates": 4,
                "associated": 2,
                "unmatched": 1,
                "ambiguous": 1,
                "excluded": 2,
                "connector_deliveries": 4,
                "connector_successful_deliveries": 3,
            },
        )

    def test_replay_and_input_order_are_deterministic(self):
        expected = self.build()
        selected = deepcopy(self.fixture["input"])
        for name in ("attempts", "costs", "outcomes", "associations", "deliveries"):
            selected[name].reverse()
        selected["complete_connector_ids"].reverse()
        self.assertEqual(self.build(selected), expected)
        self.assertEqual(self.build(), expected)

    def test_attempt_cost_and_work_object_grains_prevent_double_counting(self):
        result = self.build()
        final = next(
            item for item in result["cost_components"] if item["basis"] == "provider_final"
        )
        self.assertEqual(
            (final["amount"], final["attempt_count"], result["coverage"]["linked_work_objects"]["numerator"]),
            ("3.7", 3, 2),
        )
        self.assertNotIn(
            "provider_aggregate",
            {item["cost_basis"] for item in result["measures"]["cost_per_accepted_work_item"]},
        )
        selected = deepcopy(self.fixture["input"])
        duplicate = deepcopy(selected["costs"][0])
        duplicate["cost_event_id"] = "cost-final-duplicate"
        selected["costs"].append(duplicate)
        self.invalid(selected)

    def test_partial_history_keeps_history_dependent_measures_inconclusive(self):
        selected = deepcopy(self.fixture["input"])
        selected["complete_connector_ids"] = ["linear-one"]
        result = self.build(selected)
        for name in (
            "cycle_time", "throughput", "first_pass_success", "retries", "rework",
            "reversions", "defects",
        ):
            self.assertEqual(
                (result["measures"][name]["status"], result["measures"][name]["value"]),
                ("inconclusive", None),
            )
        self.assertTrue(all(
            item["status"] == "inconclusive"
            for item in result["measures"]["cost_per_accepted_work_item"]
        ))

    def test_undefined_populations_remain_null_instead_of_numeric_zero(self):
        context = deepcopy(self.fixture["input"]["context"])
        result = build_association_metric_vector(
            context=context,
            attempts=(),
            costs=(),
            outcomes=(),
            associations=(),
            deliveries=(),
            complete_connector_ids=(),
        )
        for value in result["coverage"].values():
            self.assertEqual(
                (value["numerator"], value["denominator"], value["ratio"], value["reason_code"]),
                (0, 0, None, "missing_evidence"),
            )
        self.assertEqual(result["measures"]["throughput"]["status"], "inconclusive")

    def test_cross_tenant_or_open_content_bearing_facts_fail_closed(self):
        collections = ("attempts", "costs", "outcomes", "associations", "deliveries")
        for name in collections:
            with self.subTest(name=name):
                selected = deepcopy(self.fixture["input"])
                selected[name][0]["organization_id"] = "other"
                self.invalid(selected)
        selected = deepcopy(self.fixture["input"])
        selected["outcomes"][0]["title"] = "forbidden-work-content"
        self.invalid(selected)

    def test_conflicting_revisions_reopen_and_revert_paths_remain_visible(self):
        result = self.build()
        self.assertEqual(
            (
                result["denominators"]["ambiguous"],
                result["denominators"]["excluded"],
                result["measures"]["rework"]["value"],
                result["measures"]["reversions"]["value"],
            ),
            (1, 2, "3", "1"),
        )
        serialized = json.dumps(result, sort_keys=True, separators=(",", ":"))
        for forbidden in (
            "title", "description", "body", "comment", "prompt", "actor_id",
            "actor_name", "employee", "credential", "raw_payload",
        ):
            self.assertNotIn(f'"{forbidden}"', serialized)

    def test_equal_revision_correction_remains_ambiguous_until_resolved(self):
        selected = deepcopy(self.fixture["input"])
        conflict = next(
            row for row in selected["outcomes"]
            if row["source_event_id"] == "gh-conflict-b"
        )
        conflict["supersedes_source_event_id"] = "gh-conflict-a"
        self.assertEqual(self.build(selected), self.build())

    def test_corrected_association_does_not_count_the_superseded_attempt_as_retry(self):
        selected = deepcopy(self.fixture["input"])
        original = next(
            row for row in selected["associations"]
            if row["source_event_id"] == "gh-pr1-v1"
        )
        corrected = deepcopy(original)
        corrected.update({
            "association_event_id": "association-pr1-v1-corrected",
            "request_attempt_id": "attempt-2",
            "sequence": max(row["sequence"] for row in selected["associations"]) + 1,
        })
        selected["associations"].append(corrected)
        result = self.build(selected)
        self.assertEqual(result["measures"]["retries"]["value"], "0")
        self.assertEqual(result["measures"]["first_pass_success"]["value"], "1")


if __name__ == "__main__":
    unittest.main()
