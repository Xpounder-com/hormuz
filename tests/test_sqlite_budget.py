from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
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

    def inject_nonpredecessor_activation(
        self, *, plan_id, current_version, wrong_previous_version,
        committed_at, template,
    ):
        with closing(sqlite3.connect(self.config.database_path)) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            event_id = "f" * 32
            connection.execute(
                "INSERT INTO portfolio_work_budget_activation_events "
                "(organization_id,activation_event_id,budget_plan_id,activation_generation,"
                "previous_version,current_version,actor_id,reason_code,policy_version,"
                "policy_digest,committed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "acme", event_id, plan_id, 2, wrong_previous_version,
                    current_version, template["actor_id"], "accepted",
                    template["policy_version"], template["policy_digest"],
                    committed_at,
                ),
            )
            connection.execute(
                "UPDATE portfolio_work_budget_active_plans SET active_version=?, "
                "activation_generation=2,current_activation_event_id=?,changed_at=? "
                "WHERE organization_id='acme' AND budget_plan_id=?",
                (current_version, event_id, committed_at, plan_id),
            )
            connection.commit()

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

    def test_configured_route_identity_accepts_provider_model_names(self):
        self.check_configured_route_identity_accepts_provider_model_names()

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

    def test_denial_report_population_is_bounded(self):
        self.check_denial_report_population_is_bounded()

    def test_orphaned_denial_audits_fail_closed(self):
        self.check_orphaned_denial_audits_fail_closed()

    def test_bounded_gateway_actor_is_never_silently_unaudited(self):
        self.check_bounded_gateway_actor_is_never_silently_unaudited()

    def test_active_plan_count_is_bounded_at_activation_and_request_time(self):
        self.check_active_plan_count_is_bounded_at_activation_and_request_time()

    def test_request_time_accounting_history_is_bounded(self):
        self.check_request_time_accounting_history_is_bounded()

    def test_activation_history_is_bounded_for_reads_and_writes(self):
        self.check_activation_history_is_bounded_for_reads_and_writes()

    def test_request_time_activation_predecessor_is_validated(self):
        self.check_request_time_activation_predecessor_is_validated()

    def test_denial_audit_retains_evaluation_time(self):
        self.check_denial_audit_retains_evaluation_time()

    def test_attribution_sequence_exhaustion_is_audited(self):
        self.check_attribution_sequence_exhaustion_is_audited()

    def test_malformed_rate_card_coordinates_fail_report_closed(self):
        self.check_malformed_rate_card_coordinates_fail_report_closed()

    def test_rate_card_diversity_is_bounded_without_losing_accounting(self):
        self.check_rate_card_diversity_is_bounded_without_losing_accounting()

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
                    evaluated_at=datetime.now(timezone.utc).isoformat(
                        timespec="microseconds",
                    ).replace("+00:00", "Z"),
                ),
            )
        self.assertEqual(caught.exception.code, "storage_unavailable")

    def test_binding_window_is_tied_to_plan_version(self):
        plan = self.create(amount="10")
        self.activate(plan)
        self.attempt(cost_microusd=1)
        with closing(sqlite3.connect(self.config.database_path)) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN")
            connection.execute(
                "DROP TRIGGER portfolio_work_budget_reservation_bindings_no_update"
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE portfolio_work_budget_reservation_bindings "
                    "SET window_start_at='2000-01-01T00:00:00.000000Z'"
                )
            connection.rollback()


if __name__ == "__main__":
    unittest.main()
