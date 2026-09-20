"""Fixture-backed checks for the developer-only portfolio display prototype."""

from __future__ import annotations

import copy
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tools import render_portfolio_display_examples as display
from tools._portfolio_wire_contract import PortfolioWireSchemaError
from tools.verify_budget_transition_plan import BudgetTransitionError


ROOT = Path(__file__).resolve().parents[1]
DOCUMENT = ROOT / "docs/PORTFOLIO_CLI_DISPLAY_EXAMPLES.md"


class PortfolioDisplayExamplesTests(unittest.TestCase):
    def test_documented_output_is_exact_and_fits_80_columns(self) -> None:
        result = subprocess.run(
            [sys.executable, "tools/render_portfolio_display_examples.py"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        )
        documented = re.search(
            r"<!-- generated example output -->\n```text\n(.*?)```",
            DOCUMENT.read_text(encoding="utf-8"), re.DOTALL,
        )
        self.assertIsNotNone(documented)
        self.assertEqual(result.stdout, documented.group(1))
        self.assertEqual(result.stderr, "")
        self.assertTrue(all(len(line) <= 80 for line in result.stdout.splitlines()))

    def test_four_cases_keep_evidence_and_cost_basis_distinct(self) -> None:
        output = display.render_examples()
        self.assertEqual(output.count("Case: "), 4)
        self.assertIn("Case: hormuz.work-budget-report:first-activation", output)
        self.assertIn("Remaining balance (derived): unknown (missing_evidence)", output)
        self.assertIn("Forecast: not available (missing_evidence)", output)
        self.assertIn("Change: increased; prior USD 100; delta USD 20 (20%)", output)
        self.assertIn("Remaining balance (derived): USD 108 (known)", output)
        self.assertIn("Forecast: USD 20; configured_rate_card_estimate", output)
        self.assertIn("State: inconclusive (missing_evidence); evidence descriptive", output)
        self.assertIn("0/0; ratio unknown (missing_evidence)", output)
        self.assertIn("Cost component: not available (no components); currency unknown", output)
        self.assertIn("State: eligible (eligible); evidence associated", output)
        self.assertIn("Cost component: USD 1; provider_final (not summed)", output)
        self.assertIn("Model: provider example-id; model example-id; version example-id", output)
        for forbidden in ("prompt:", "response:", "employee", "credential", "file://"):
            self.assertNotIn(forbidden, output.lower())

    def test_exact_decimals_zero_negative_credit_and_missing(self) -> None:
        self.assertEqual(display._money(None, "USD"), "unknown")
        self.assertEqual(display._money("0", "USD"), "USD 0")
        self.assertEqual(display._money("-7.000000000000000001", "USD"),
                         "USD -7.000000000000000001")
        self.assertEqual(display._money("0.100000000000000001", "USD"),
                         "USD 0.100000000000000001")
        self.assertEqual(display._observation({
            "basis": "credit_or_discount", "amount": "-2.50", "currency": "USD",
            "reason_code": "known",
        }), "credit_or_discount; USD -2.50; known")

    def test_invalid_budget_domain_fails_before_rendering(self) -> None:
        original = display._read_known

        def changed(name: str) -> dict[str, object]:
            value = copy.deepcopy(original(name))
            if name == "budget_examples":
                value["cases"][1]["value"]["enforcement"]["remaining_amount"] = "999"
            return value

        with mock.patch.object(display, "_read_known", side_effect=changed):
            with self.assertRaisesRegex(BudgetTransitionError, "budget_report_fixture_invalid"):
                display.render_examples()

    def test_invalid_scorecard_field_fails_closed_before_rendering(self) -> None:
        original = display._read_known

        def changed(name: str) -> dict[str, object]:
            value = copy.deepcopy(original(name))
            if name == "scorecard_examples":
                case = next(case for case in value["cases"]
                            if case["name"] == "hormuz.model-scorecard:minimal")
                case["value"]["prompt"] = "SYNTHETIC_DO_NOT_ECHO"
            return value

        with mock.patch.object(display, "_read_known", side_effect=changed):
            with self.assertRaisesRegex(PortfolioWireSchemaError, "wire_payload_unknown_field") as caught:
                display.render_examples()
        self.assertNotIn("SYNTHETIC_DO_NOT_ECHO", str(caught.exception))

    def test_known_fixture_digest_and_no_user_supplied_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fake_root = Path(temporary)
            relative, _ = display.FILES["scorecard_examples"]
            destination = fake_root / relative
            destination.parent.mkdir(parents=True)
            destination.write_bytes((ROOT / relative).read_bytes() + b" ")
            with mock.patch.object(display, "ROOT", fake_root):
                with self.assertRaisesRegex(ValueError, "portfolio_display_fixture_changed"):
                    display._read_known("scorecard_examples")
        result = subprocess.run(
            [sys.executable, "tools/render_portfolio_display_examples.py", "--fixture", "elsewhere"],
            cwd=ROOT, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
