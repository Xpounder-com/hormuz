from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from hormuz._budget_schema import TABLE_DDL
from hormuz.budget_runtime import WorkBudgetDenied
from hormuz.store import ReservationDenied, StorageSchemaError, UsageStore

if __package__:
    from ._budget_fixture import BudgetAssertions
    from ._portfolio_fixture import registry_config
else:
    from _budget_fixture import BudgetAssertions
    from _portfolio_fixture import registry_config


class SQLiteBudgetTests(BudgetAssertions, unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.config = registry_config(Path(temporary.name))
        self.environment = None
        self.store = UsageStore(self.config.database_path)
        self.setup_budget()

    def budget_rows(self):
        with closing(sqlite3.connect(self.config.database_path)) as connection:
            connection.row_factory = sqlite3.Row
            return {
                table: sorted(
                    (dict(row) for row in connection.execute(f"SELECT * FROM {table}").fetchall()),
                    key=repr,
                )
                for table in TABLE_DDL
            }

    def attribution_rows(self):
        with closing(sqlite3.connect(self.config.database_path)) as connection:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute(
                "SELECT * FROM portfolio_attribution_events ORDER BY sequence"
            ).fetchall()]

    def new_store(self):
        return UsageStore(self.config.database_path)

    def test_plan_versions_activation_and_management_change(self):
        self.check_plan_versions_activation_and_management_change()

    def test_compare_and_set_authority_and_strict_values(self):
        self.check_compare_and_set_authority_and_strict_values()

    def test_preview_is_read_only_and_scenario_backed(self):
        self.check_preview_is_read_only_and_scenario_backed()

    def test_atomic_budget_binding_reconciliation_and_unknown_holds(self):
        self.check_atomic_budget_binding_reconciliation_and_unknown_holds()

    def test_model_output_request_caps_and_missing_attribution_fail_closed(self):
        self.check_model_output_request_caps_and_missing_attribution_fail_closed()

    def test_concurrent_instances_cannot_overspend(self):
        self.check_concurrent_instances_cannot_overspend()

    def test_hierarchy_deny_wins_and_legacy_ceiling_is_atomic(self):
        self.check_hierarchy_deny_wins_and_legacy_ceiling_is_atomic()

    def test_model_intersection_exact_decimal_and_deny_audit(self):
        self.check_model_intersection_exact_decimal_and_deny_audit()

    def test_replacement_emergency_tightening_rollback_and_schedule_bounds(self):
        self.check_replacement_emergency_tightening_rollback_and_schedule_bounds()

    def test_hierarchy_change_never_resets_same_period_spend(self):
        self.check_hierarchy_change_never_resets_same_period_spend()

    def test_archived_and_ambiguous_attribution_fail_closed(self):
        self.check_archived_and_ambiguous_attribution_fail_closed()

    def test_exact_microusd_boundary(self):
        self.check_exact_microusd_boundary()

    def test_unsupported_currency_and_future_report_fail_safely(self):
        self.check_unsupported_currency_and_future_report_fail_safely()

    def test_missing_terminal_price_never_becomes_zero(self):
        self.check_missing_terminal_price_never_becomes_zero()

    def test_bounded_gateway_actor_is_never_silently_unaudited(self):
        self.check_bounded_gateway_actor_is_never_silently_unaudited()

    def test_denial_audit_failure_is_a_storage_failure(self):
        plan = self.create(amount="0")
        self.activate(plan)
        with mock.patch(
            "hormuz.store.record_work_budget_denial",
            side_effect=ReservationDenied("synthetic audit failure"),
        ):
            with self.assertRaises(StorageSchemaError) as caught:
                self.attempt(cost_microusd=1)
        self.assertEqual(caught.exception.code, "storage_unavailable")
        with self.assertRaises(StorageSchemaError) as caught:
            self.store._record_work_budget_denial(
                self.identity,
                WorkBudgetDenied(
                    "synthetic uncoordinated denial", "attribution_invalid", (),
                ),
            )
        self.assertEqual(caught.exception.code, "storage_unavailable")


if __name__ == "__main__":
    unittest.main()
