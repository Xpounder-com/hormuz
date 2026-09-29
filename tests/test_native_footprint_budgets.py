from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import unittest

from tools.verify_windows_footprint_baseline import verify_budgets


ROOT = Path(__file__).resolve().parents[1]
BUDGETS = ROOT / "docs/evidence/native-client-footprint-budgets-v1.json"
MAC_VERIFIER = (ROOT / "docs/evidence/native-client-baseline-2026-09-29"
                / "verify_release_baseline.py")


class NativeFootprintBudgetTests(unittest.TestCase):
    def test_budget_document_is_strict_and_numeric(self) -> None:
        document = json.loads(BUDGETS.read_text())
        self.assertEqual(set(document), {
            "schema_id", "schema_version", "status", "basis",
            "macos", "windows", "limits",
        })
        self.assertEqual(document["schema_id"], "hormuz.native-client-footprint-budgets")
        self.assertEqual(document["schema_version"], 1)
        self.assertEqual(document["status"], "proposed")
        self.assertEqual(set(document["macos"]), {
            "archive_bytes_max", "bundle_logical_bytes_max",
            "bundle_allocated_bytes_max", "idle_rss_kib_max",
            "idle_physical_footprint_bytes_max", "idle_cpu_percent_one_core_max",
            "package_idle_wakeups_per_five_minutes_max",
            "interrupt_wakeups_per_five_minutes_max",
            "warm_start_panel_upper_bound_seconds_max",
        })
        self.assertEqual(set(document["windows"]), {
            "executable_bytes_max", "working_set_bytes_max", "private_bytes_max",
            "app_cpu_percent_one_core_max",
            "first_run_window_ready_upper_bound_seconds_max",
            "subsequent_window_ready_upper_bound_seconds_max",
            "post_100_cycle_working_set_growth_bytes_max",
            "post_100_cycle_private_bytes_growth_max",
            "post_100_cycle_cpu_seconds_max", "post_100_cycle_handle_growth_max",
            "post_100_cycle_gdi_object_growth_max",
            "post_100_cycle_user_object_growth_max",
        })
        for platform in ("macos", "windows"):
            for value in document[platform].values():
                self.assertIsInstance(value, (int, float))
                self.assertNotIsInstance(value, bool)
                self.assertGreater(value, 0)

    def test_committed_macos_release_evidence_passes(self) -> None:
        subprocess.run(
            [sys.executable, str(MAC_VERIFIER)],
            check=True,
            capture_output=True,
            text=True,
        )

    def test_windows_budget_accepts_reference_envelope(self) -> None:
        summary = self.windows_summary()
        verify_budgets(summary, BUDGETS)
        self.assertEqual(summary["budget_status"], "passed")

    def test_windows_budget_rejects_regression(self) -> None:
        summary = self.windows_summary()
        summary["scenarios"]["visible"]["runs"][0]["working_set_bytes_max"] = 25_165_825
        with self.assertRaisesRegex(ValueError, "working_set_bytes_max"):
            verify_budgets(summary, BUDGETS)

    def test_windows_budget_separates_first_and_subsequent_readiness(self) -> None:
        summary = self.windows_summary()
        summary["scenarios"]["visible"]["runs"][0]["window_ready_upper_bound_seconds"] = 40.1
        with self.assertRaisesRegex(ValueError, "first_run_window_ready"):
            verify_budgets(summary, BUDGETS)

        summary = self.windows_summary()
        summary["scenarios"]["visible"]["runs"][1]["window_ready_upper_bound_seconds"] = 8.1
        with self.assertRaisesRegex(ValueError, "subsequent_window_ready"):
            verify_budgets(summary, BUDGETS)

    @staticmethod
    def windows_summary() -> dict:
        run = {
            "working_set_bytes_max": 17_326_080,
            "private_bytes_max": 2_605_056,
            "app_cpu_percent_one_core": 0.0,
            "window_ready_upper_bound_seconds": 5.6357166,
        }
        return {
            "executable_bytes": 2_662_912,
            "scenarios": {
                scenario: {"runs": [deepcopy(run) for _ in range(3)]}
                for scenario in ("visible", "folded", "hidden")
            },
        }


if __name__ == "__main__":
    unittest.main()
