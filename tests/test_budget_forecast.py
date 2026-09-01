from __future__ import annotations

import unittest

from hormuz.budget_repository import WorkBudgetRepository


class BudgetForecastTests(unittest.TestCase):
    def test_millennium_scale_fractional_interval_fails_exactly(self) -> None:
        forecast = WorkBudgetRepository._forecast(
            {
                "window_start_at": "1000-01-01T00:00:00Z",
                "window_end_at": "3000-01-01T00:00:00Z",
                "currency": "USD",
            },
            {"reason_code": "known", "committed_amount": "1"},
            {"reason_code": "known", "pricing_eligible_attempts": 1},
            "2000-01-01T00:00:00.000001Z",
        )

        self.assertEqual(forecast["reason_code"], "precision_exceeded")
        self.assertIsNone(forecast["elapsed_seconds"])
        self.assertIsNone(forecast["period_seconds"])
        self.assertIsNone(forecast["projected_amount"])


if __name__ == "__main__":
    unittest.main()
