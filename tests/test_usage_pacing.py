from __future__ import annotations

import unittest
from datetime import datetime, timezone

from hormuz.usage_reporting import build_budget_pacing, utc_month_bounds


class BudgetPacingTests(unittest.TestCase):
    def test_calendar_pace_uses_elapsed_utc_month_seconds(self) -> None:
        # 15.5 elapsed calendar days of a 31-day August is exactly 50% of the month.
        as_of = datetime(2026, 8, 16, 12, tzinfo=timezone.utc)
        pacing = build_budget_pacing(
            as_of=as_of,
            estimated_spend_microusd=15_500_000,
            unpriced_requests=2,
            monthly_budget_usd=30.0,
        )

        self.assertEqual(utc_month_bounds(as_of)[0], datetime(2026, 8, 1, tzinfo=timezone.utc))
        self.assertEqual(utc_month_bounds(as_of)[1], datetime(2026, 9, 1, tzinfo=timezone.utc))
        self.assertEqual(pacing["methodology"], "calendar_pace_estimate")
        self.assertTrue(pacing["advisory_only"])
        self.assertEqual(
            pacing["policy_enforcement_basis"],
            "actual_usage_plus_active_reservations_only",
        )
        self.assertEqual(pacing["elapsed_fraction"], 0.5)
        self.assertFalse(pacing["early_period"])
        self.assertTrue(pacing["projection_available"])
        self.assertEqual(pacing["month_to_date_estimated_spend_microusd"], 15_500_000)
        self.assertEqual(
            pacing["average_estimated_spend_per_calendar_day_microusd"],
            1_000_000,
        )
        self.assertEqual(
            pacing["projected_month_end_estimated_spend_microusd"],
            31_000_000,
        )
        self.assertEqual(pacing["configured_monthly_budget_usd"], 30.0)
        self.assertEqual(pacing["projected_budget_utilization_percent"], 103.333333)
        self.assertEqual(pacing["projected_budget_overage_usd"], 1.0)
        self.assertEqual(pacing["unpriced_requests"], 2)
        self.assertTrue(pacing["partial_projection"])

    def test_first_seven_utc_calendar_days_are_marked_early(self) -> None:
        early = build_budget_pacing(
            as_of=datetime(2026, 8, 7, 23, 59, 59, tzinfo=timezone.utc),
            estimated_spend_microusd=100,
            unpriced_requests=0,
            monthly_budget_usd=None,
        )
        later = build_budget_pacing(
            as_of=datetime(2026, 8, 8, tzinfo=timezone.utc),
            estimated_spend_microusd=100,
            unpriced_requests=0,
            monthly_budget_usd=None,
        )

        self.assertTrue(early["early_period"])
        self.assertFalse(later["early_period"])
        self.assertFalse(early["partial_projection"])

    def test_exact_month_start_returns_no_invented_projection(self) -> None:
        pacing = build_budget_pacing(
            as_of=datetime(2026, 9, 1, tzinfo=timezone.utc),
            estimated_spend_microusd=0,
            unpriced_requests=0,
            monthly_budget_usd=100.0,
        )

        self.assertFalse(pacing["projection_available"])
        self.assertEqual(pacing["elapsed_fraction"], 0.0)
        self.assertIsNone(pacing["average_estimated_spend_per_calendar_day_usd"])
        self.assertIsNone(pacing["projected_month_end_estimated_spend_usd"])
        self.assertIsNone(pacing["projected_budget_utilization_percent"])
        self.assertIsNone(pacing["projected_budget_overage_usd"])

    def test_timezone_aware_timestamp_is_required(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            build_budget_pacing(
                as_of=datetime(2026, 8, 16, 12),
                estimated_spend_microusd=0,
                unpriced_requests=0,
                monthly_budget_usd=None,
            )


if __name__ == "__main__":
    unittest.main()
