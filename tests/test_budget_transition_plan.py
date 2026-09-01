"""#217 preflight and analytics-first report v2 contract tests."""

from __future__ import annotations

import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tools import verify_budget_transition_plan as verifier
from tools.verify_budget_transition_plan import BudgetTransitionError


ROOT = Path(__file__).resolve().parents[1]


class BudgetTransitionPlanTests(unittest.TestCase):
    def plan(self):
        return json.loads((ROOT / verifier.PLAN_PATH).read_bytes())

    def implementation_plan(self):
        return json.loads((ROOT / verifier.IMPLEMENTATION_PATH).read_bytes())

    def bundles(self):
        return (
            json.loads((ROOT / verifier.REPORT_V1_PATH).read_bytes()),
            json.loads((ROOT / verifier.REPORT_V2_PATH).read_bytes()),
        )

    def examples(self):
        return json.loads((ROOT / verifier.FIXTURE_PATH).read_bytes())

    def report(self, name="hormuz.work-budget-report:increased"):
        return copy.deepcopy(next(case["value"] for case in self.examples()["cases"] if case["name"] == name))

    def validate(self, value):
        v1, v2 = self.bundles()
        fixtures = self.examples()
        fixtures["cases"] = [{
            "name": "hormuz.work-budget-report:first-activation",
            "schema_id": "hormuz.work-budget-report",
            "value": self.report("hormuz.work-budget-report:first-activation"),
        }, {
            "name": "hormuz.work-budget-report:increased",
            "schema_id": "hormuz.work-budget-report",
            "value": value,
        }]
        verifier.validate_budget_report_v2(v1, v2, fixtures)

    def rejected(self, value, code=None):
        pattern = code or "budget_"
        with self.assertRaisesRegex(BudgetTransitionError, pattern):
            self.validate(value)

    def test_checkpoint_is_preflight_not_runtime_or_release(self):
        result = verifier.verify_budget_transition_plan(ROOT)
        self.assertEqual(result["status"], "budget_preflight_plan_verified")
        self.assertEqual(result["feature_issue"], 217)
        self.assertEqual(result["predecessor_sqlite_schema_version"], 8)
        self.assertEqual(result["predecessor_postgresql_schema_version"], 12)
        self.assertEqual(result["planned_sqlite_schema_version"], 9)
        self.assertEqual(result["planned_postgresql_schema_version"], 13)
        self.assertEqual(result["report_schema_version"], 2)
        self.assertEqual(result["new_http_routes"], 0)
        for field in ("budget_implemented", "feature_preflight_accepted", "final_candidate_accepted"):
            self.assertFalse(result[field])

    def test_successor_is_runtime_source_not_acceptance_or_release(self):
        result = verifier.verify_budget_implementation_plan(ROOT)
        self.assertEqual(result["status"], "budget_runtime_plan_verified")
        self.assertEqual(result["schema_version"], 2)
        self.assertTrue(result["feature_preflight_accepted"])
        self.assertTrue(result["budget_implemented"])
        self.assertFalse(result["budget_runtime_accepted"])
        self.assertFalse(result["final_candidate_accepted"])
        self.assertFalse(result["released"])
        self.assertEqual(result["sqlite_schema_version"], 9)
        self.assertEqual(result["postgresql_schema_version"], 13)
        self.assertEqual(result["budget_table_count"], 5)
        self.assertEqual(result["report_schema_version"], 2)
        self.assertEqual(result["new_http_routes"], 0)
        self.assertEqual(result["new_cli_commands"], 0)

    def test_successor_freezes_every_implementation_section(self):
        for key in self.implementation_plan():
            with self.subTest(key=key):
                changed = copy.deepcopy(self.implementation_plan())
                changed[key] = "SYNTHETIC_EXCLUDED"
                with self.assertRaisesRegex(
                    BudgetTransitionError,
                    "budget_implementation_contract_changed",
                ):
                    verifier.validate_budget_implementation_plan(changed)

    def test_every_plan_section_and_scope_limit_is_frozen(self):
        for key in self.plan():
            with self.subTest(key=key):
                changed = copy.deepcopy(self.plan())
                changed[key] = "SYNTHETIC_EXCLUDED"
                with self.assertRaisesRegex(BudgetTransitionError, "budget_preflight_contract_changed"):
                    verifier.validate_budget_transition_plan(changed)

    def test_plan_rejects_non_json_nonfinite_and_invalid_unicode(self):
        for value in (object(), float("nan"), float("inf"), {"x": "\ud800"}):
            with self.assertRaises(BudgetTransitionError) as caught:
                verifier.validate_budget_transition_plan(value)
            self.assertNotIn("SYNTHETIC_EXCLUDED", str(caught.exception))

    def test_finance_predecessor_is_required(self):
        with mock.patch.object(verifier, "verify_finance_implementation_plan", side_effect=verifier.FinanceTransitionError("synthetic")):
            with self.assertRaisesRegex(BudgetTransitionError, "budget_finance_predecessor_invalid"):
                verifier.verify_budget_transition_plan(ROOT)

    def test_v1_report_stays_frozen_and_v2_is_explicitly_selected(self):
        v1, v2 = self.bundles()
        self.assertIsNone(v1.get("x-hormuz-schema-versions"))
        self.assertEqual(v1["$defs"]["hormuz.work-budget-report"]["properties"]["schema_version"]["const"], 1)
        self.assertEqual(v2["x-hormuz-schema-versions"], {
            "hormuz.work-budget-preview": 1,
            "hormuz.work-budget-report": 2,
        })
        self.assertEqual(v2["$defs"]["hormuz.work-budget-report"]["properties"]["schema_version"]["const"], 2)
        self.assertIn("plan_change", v2["$defs"]["hormuz.work-budget-report"]["required"])
        self.assertNotIn("plan_change", v1["$defs"]["hormuz.work-budget-report"]["properties"])

        changed = copy.deepcopy(v2)
        changed["$defs"]["currency"]["pattern"] = "^USD$"
        with self.assertRaisesRegex(BudgetTransitionError, "budget_report_v2_not_additive"):
            verifier.validate_budget_report_v2(v1, changed, self.examples())

    def test_first_activation_has_no_fabricated_delta_or_percentage(self):
        value = self.report("hormuz.work-budget-report:first-activation")
        v1, v2 = self.bundles()
        verifier.validate_budget_report_v2(v1, v2, self.examples())
        for field, replacement in (
            ("previous_amount", "0"),
            ("previous_currency", "USD"),
            ("previous_work_scope", {"work_scope_id": "use-case-test", "version": 1}),
            ("previous_window", value["window"]),
            ("amount_delta", "100"),
            ("percent_delta", "0"),
        ):
            changed = copy.deepcopy(value)
            changed["plan_change"][field] = replacement
            fixtures = self.examples()
            fixtures["cases"][0]["value"] = changed
            with self.assertRaisesRegex(BudgetTransitionError, "budget_change_first_activation_invalid"):
                verifier.validate_budget_report_v2(v1, v2, fixtures)
        changed = self.report()
        changed["activation_generation"] = 1
        self.rejected(changed, "budget_change_first_activation_invalid")

    def test_increase_decrease_and_unchanged_percentages_match_exact_amounts(self):
        for current, remaining, delta, percent, kind in (
            ("120", "108", "20", "20", "increased"),
            ("80", "68", "-20", "-20", "decreased"),
            ("100", "88", "0", "0", "unchanged"),
            ("133.333333", "121.333333", "33.333333", "33.333333", "increased"),
        ):
            with self.subTest(kind=kind):
                value = self.report()
                value["plan_amount"] = current
                value["enforcement"]["remaining_amount"] = remaining
                value["plan_change"].update(kind=kind, amount_delta=delta, percent_delta=percent)
                self.validate(value)
                value["plan_change"]["percent_delta"] = "99"
                self.rejected(value, "budget_change_percentage_invalid")

    def test_percentage_rounding_is_half_even_to_six_places(self):
        _v1, v2 = self.bundles()
        description = v2["$defs"]["signed_percentage"]["description"]
        self.assertIn("relative percent change", description)
        self.assertNotIn("percentage points", description)
        value = self.report()
        value["plan_change"]["previous_amount"] = "3"
        value["plan_amount"] = "4"
        value["enforcement"]["remaining_amount"] = "-8"
        value["plan_change"].update(amount_delta="1", percent_delta="33.333333", kind="increased")
        self.validate(value)
        value["plan_change"]["percent_delta"] = "33.333334"
        self.rejected(value, "budget_change_percentage_invalid")

    def test_zero_denominator_keeps_exact_amount_change_without_percentage(self):
        value = self.report()
        value["plan_change"].update(
            previous_amount="0", amount_delta="120", percent_delta=None,
            comparison_status="previous_amount_zero", kind="increased",
        )
        self.validate(value)
        value["plan_change"]["percent_delta"] = "0"
        self.rejected(value, "budget_change_zero_denominator_invalid")

    def test_noncomparable_basis_is_visible_and_missing_evidence_never_becomes_zero(self):
        changes = (
            ("currency_changed", "previous_currency", "EUR"),
            ("work_scope_changed", "previous_work_scope", {"work_scope_id": "use-case-prior", "version": 1}),
            ("window_changed", "previous_window", {"start_at": "2026-07-01T00:00:00Z", "end_at": "2026-08-01T00:00:00Z"}),
        )
        for reason, field, replacement in changes:
            with self.subTest(reason=reason):
                value = self.report()
                value["plan_change"].update(
                    comparison_status="not_comparable", comparison_reasons=[reason],
                    kind="not_comparable", amount_delta=None, percent_delta=None,
                )
                value["plan_change"][field] = replacement
                self.validate(value)
                value["plan_change"]["comparison_reasons"] = []
                self.rejected(value, "budget_change_noncomparable_invalid")
        value = self.report()
        value["plan_change"].update(
            comparison_status="not_comparable",
            comparison_reasons=["currency_changed", "window_changed"],
            kind="not_comparable", amount_delta=None, percent_delta=None,
            previous_currency="EUR",
            previous_window={"start_at": "2026-07-01T00:00:00Z", "end_at": "2026-08-01T00:00:00Z"},
        )
        self.validate(value)
        value["plan_change"]["comparison_reasons"].reverse()
        self.rejected(value, "budget_change_noncomparable_invalid")
        value = self.report()
        value["plan_change"].update(
            comparison_status="missing_evidence", kind="not_comparable",
            comparison_reasons=[], previous_plan=None, previous_amount=None,
            previous_currency=None, previous_work_scope=None, previous_window=None,
            amount_delta=None, percent_delta=None,
        )
        self.validate(value)
        value["plan_change"]["previous_amount"] = "0"
        self.rejected(value, "budget_change_missing_evidence_invalid")

    def test_change_time_is_activation_commit_time_not_future_input(self):
        value = self.report()
        value["plan_change"]["changed_at"] = "2026-08-16T12:00:01Z"
        self.rejected(value, "budget_change_time_invalid")

    def test_v2_required_change_and_closed_content_are_enforced(self):
        for mutation in ("missing", "extra", "old_version"):
            value = self.report()
            if mutation == "missing":
                del value["plan_change"]
            elif mutation == "extra":
                value["plan_change"]["manager_note"] = "SYNTHETIC_EXCLUDED"
            else:
                value["schema_version"] = 1
            self.rejected(value, "budget_report_fixture_invalid")

    def test_cli_rejects_missing_or_wrong_finance_archive(self):
        for payload in (None, b"SYNTHETIC_EXCLUDED"):
            with tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "finance.tar"
                if payload is not None:
                    path.write_bytes(payload)
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(verifier.main(["--finance-archive", str(path)]), 1)
                self.assertEqual(output.getvalue().strip(), "budget_finance_archive_invalid")


if __name__ == "__main__":
    unittest.main()
