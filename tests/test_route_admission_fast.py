"""Public route-admission contract rules without a connected runtime."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from hormuz.contracts import ContractValidationError, validate_contract


class RouteAdmissionFastTests(unittest.TestCase):
    def setUp(self):
        fixture = Path(__file__).parent / "fixtures/execution_destination/router-state-v2.json"
        self.state = json.loads(fixture.read_text())
        self.state["routes"][0].update(
            readiness="ready", generation="a" * 64,
            task_types=["code_change"], automatic_task_types=["code_change"],
            coding_readiness="qualified", coding_evidence={
                "basis": "operator_attested_outcomes", "criteria_version": "local-repair.v1",
                "evidence_id": "synthetic-passing-local-repair-v1",
                "plan_sha256": "a" * 64, "receipt_sha256": "a" * 64,
                "planned": 16, "evaluated": 16, "passed": 16})

    def test_complete_coding_evidence_summary_is_valid(self):
        validate_contract(self.state)

    def test_qualified_readiness_requires_all_planned_outcomes_to_pass(self):
        for planned, evaluated, passed in ((1, 0, 0), (15, 15, 15), (16, 0, 0), (16, 16, 15)):
            with self.subTest(planned=planned, evaluated=evaluated, passed=passed):
                invalid = deepcopy(self.state)
                invalid["routes"][0]["coding_evidence"].update(
                    planned=planned, evaluated=evaluated, passed=passed)
                with self.assertRaises(ContractValidationError):
                    validate_contract(invalid)

    def test_readiness_agrees_with_the_evidence_counts(self):
        for readiness, counts, accepted in (
                ("incomplete", (0, 0, 0), False), ("failed", (0, 0, 0), False),
                ("subject_mismatch", (0, 0, 0), False),
                ("failed", (16, 0, 0), False), ("failed", (16, 15, 15), False),
                ("failed", (16, 16, 16), False), ("unknown", (16, 16, 16), False),
                ("incomplete", (1, 0, 0), True), ("incomplete", (16, 5, 4), True),
                ("incomplete", (16, 16, 16), True),
                ("failed", (1, 1, 0), True), ("failed", (16, 5, 4), True),
                ("subject_mismatch", (16, 0, 0), True),
                ("subject_mismatch", (16, 16, 16), True),
                ("subject_mismatch", (16, 5, 4), True),
                ("qualified", (16, 16, 16), True), ("qualified", (64, 64, 64), True)):
            with self.subTest(readiness=readiness, counts=counts, accepted=accepted):
                summary = deepcopy(self.state)
                route = summary["routes"][0]
                route["coding_readiness"] = readiness
                route["automatic_task_types"] = ["code_change"] if readiness == "qualified" else []
                route["coding_evidence"].update(zip(("planned", "evaluated", "passed"), counts))
                if accepted:
                    validate_contract(summary)
                else:
                    with self.assertRaises(ContractValidationError):
                        validate_contract(summary)

    def test_only_unknown_readiness_allows_null_evidence(self):
        for readiness in ("unknown", "incomplete", "failed", "subject_mismatch", "qualified"):
            with self.subTest(null_evidence_readiness=readiness):
                summary = deepcopy(self.state)
                summary["routes"][0].update(coding_evidence=None, coding_readiness=readiness,
                                           automatic_task_types=[])
                if readiness == "unknown":
                    validate_contract(summary)
                else:
                    with self.assertRaises(ContractValidationError):
                        validate_contract(summary)

    def test_coding_evidence_identifiers_are_bounded_and_safe(self):
        for identifier in (" ", "x" * 129, "\ud800", "invalid\nidentifier", "\U0001f9ea"):
            with self.subTest(evidence_identifier=ascii(identifier)):
                invalid = deepcopy(self.state)
                invalid["routes"][0]["coding_evidence"]["evidence_id"] = identifier
                with self.assertRaises(ContractValidationError):
                    validate_contract(invalid)

    def test_automatic_tasks_exclude_duplicates_and_unsupported_coding_work(self):
        for automatic in (["code_change", "code_change"], ["unsupported"], ["code_review"]):
            with self.subTest(automatic_tasks=automatic):
                invalid = deepcopy(self.state)
                invalid["routes"][0].update(task_types=automatic, automatic_task_types=automatic)
                with self.assertRaises(ContractValidationError):
                    validate_contract(invalid)

    def test_coding_evidence_requires_lowercase_sha256_digests(self):
        for field in ("plan_sha256", "receipt_sha256"):
            for digest in ("x", "g" * 64, "A" * 64, "a" * 63, "a" * 65, "", None, [], {}):
                with self.subTest(field=field, digest=digest):
                    invalid = deepcopy(self.state)
                    invalid["routes"][0]["coding_evidence"][field] = digest
                    with self.assertRaises(ContractValidationError):
                        validate_contract(invalid)

    def test_readiness_and_task_fields_reject_wrong_types_and_unknown_values(self):
        for field, corrupt in (("coding_readiness", []), ("coding_readiness", {}),
                               ("coding_readiness", None), ("coding_readiness", True),
                               ("coding_readiness", 1), ("coding_readiness", "unsupported"),
                               ("task_types", None), ("task_types", 1), ("task_types", {})):
            with self.subTest(field=field, corrupt=corrupt):
                invalid = deepcopy(self.state)
                invalid["routes"][0][field] = corrupt
                with self.assertRaises(ContractValidationError):
                    validate_contract(invalid)


if __name__ == "__main__":
    unittest.main()
