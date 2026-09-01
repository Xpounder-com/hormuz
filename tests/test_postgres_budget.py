from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from importlib import resources
from pathlib import Path
from unittest import mock

from hormuz._budget_schema import TABLE_DDL, postgres_statements
from hormuz.budget_runtime import WorkBudgetDenied
from hormuz.config import UsageStorageConfig
from hormuz.postgres import POSTGRES_SCHEMA_VERSION
from hormuz.postgres import PostgresStorageError
from hormuz.postgres_usage_store import PostgresUsageStore
from hormuz.store import ReservationDenied, ReservationScope

if __package__:
    from ._budget_fixture import ADMIN, BudgetAssertions
    from ._portfolio_fixture import registry_config
    from ._postgres_fixture import PostgresTestCase
else:
    from _budget_fixture import ADMIN, BudgetAssertions
    from _portfolio_fixture import registry_config
    from _postgres_fixture import PostgresTestCase


class PostgresBudgetTests(BudgetAssertions, PostgresTestCase):
    def setUp(self):
        super().setUp()
        self.config = replace(
            registry_config(Path("/unused/synthetic-budget")),
            usage_storage=UsageStorageConfig(
                backend="postgresql", postgres_schema=self.schema,
                postgres_runtime_role=self.runtime_role,
            ),
        )
        self.environment = {"HORMUZ_POSTGRES_DSN": self.runtime_dsn}
        self.store = PostgresUsageStore(
            self.runtime_dsn, schema=self.schema, runtime_role=self.runtime_role,
            organization_ids=("acme", "beta"),
        )
        self.setup_budget()

    def _rows(self, names):
        with self.psycopg.connect(
            self.owner_dsn, row_factory=self.psycopg.rows.dict_row,
        ) as connection:
            return {
                table: sorted(
                    (dict(row) for row in connection.execute(
                        self.sql.SQL("SELECT * FROM {}.{}").format(
                            self.sql.Identifier(self.schema), self.sql.Identifier(table),
                        )
                    ).fetchall()),
                    key=repr,
                )
                for table in names
            }

    def budget_rows(self):
        return self._rows(TABLE_DDL)

    def attribution_rows(self):
        return self._rows(("portfolio_attribution_events",))["portfolio_attribution_events"]

    def new_store(self):
        return PostgresUsageStore(
            self.runtime_dsn, schema=self.schema, runtime_role=self.runtime_role,
            organization_ids=("acme", "beta"),
        )

    def test_real_schema_and_checked_in_migration(self):
        self.assertEqual(POSTGRES_SCHEMA_VERSION, 13)
        with self.psycopg.connect(self.owner_dsn) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM pg_tables WHERE schemaname=%s", (self.schema,),
            ).fetchone()[0]
        self.assertEqual(count, 58)
        actual = resources.files("hormuz").joinpath(
            "migrations/postgresql/0013_work_budgets.sql"
        ).read_text()
        self.assertEqual(actual, postgres_statements("{schema}", "{runtime_role}"))

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

    def test_independent_replicas_cannot_overspend(self):
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

    def test_active_plan_count_is_bounded_at_activation_and_request_time(self):
        self.check_active_plan_count_is_bounded_at_activation_and_request_time()

    def test_request_time_accounting_history_is_bounded(self):
        self.check_request_time_accounting_history_is_bounded()

    def test_activation_history_is_bounded_for_reads_and_writes(self):
        self.check_activation_history_is_bounded_for_reads_and_writes()

    def test_denial_audit_retains_evaluation_time(self):
        self.check_denial_audit_retains_evaluation_time()

    def test_denial_audit_failure_is_a_storage_failure(self):
        plan = self.create(amount="0")
        self.activate(plan)
        with mock.patch(
            "hormuz.postgres_usage_store.record_work_budget_denial",
            side_effect=ReservationDenied("synthetic audit failure"),
        ):
            with self.assertRaises(PostgresStorageError) as caught:
                self.attempt(cost_microusd=1)
        self.assertEqual(caught.exception.code, "storage_unavailable")
        with self.assertRaises(PostgresStorageError) as caught:
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

    def test_gateway_work_budget_uses_database_clock(self):
        plan = self.create(amount="0")
        self.activate(plan)

        class AheadProcessClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.now(timezone.utc) + timedelta(days=2)

        with mock.patch("hormuz.postgres_usage_store.datetime", AheadProcessClock):
            with self.assertRaises(ReservationDenied):
                self.attempt(cost_microusd=1)
        self.assertEqual(
            self.budget_rows()["portfolio_work_budget_reservation_bindings"],
            [],
        )

    def test_reservation_paths_lock_month_before_sweeping_attempts(self):
        observed = []
        acquire = self.store._acquire_budget_month_in_cursor
        sweep = self.store._sweep_stale_request_attempts_in_cursor

        def record_acquire(cursor, *, organization_id, observed_at):
            observed.append("month")
            return acquire(
                cursor,
                organization_id=organization_id,
                observed_at=observed_at,
            )

        def record_sweep(cursor, *, now, organization_id):
            observed.append("sweep")
            return sweep(cursor, now=now, organization_id=organization_id)

        with (
            mock.patch.object(
                self.store,
                "_acquire_budget_month_in_cursor",
                side_effect=record_acquire,
            ),
            mock.patch.object(
                self.store,
                "_sweep_stale_request_attempts_in_cursor",
                side_effect=record_sweep,
            ),
        ):
            self.store.reserve_budget(
                identity=self.identity,
                scopes=(ReservationScope(name="organization", cost_limit_microusd=10),),
                reserved_tokens=1,
                reserved_cost_microusd=1,
                ttl_seconds=60,
            )
        self.assertEqual(observed[:2], ["month", "sweep"])

        observed.clear()
        plan = self.create(amount="10")
        self.activate(plan)
        with (
            mock.patch.object(
                self.store,
                "_acquire_budget_month_in_cursor",
                side_effect=record_acquire,
            ),
            mock.patch.object(
                self.store,
                "_sweep_stale_request_attempts_in_cursor",
                side_effect=record_sweep,
            ),
        ):
            self.attempt(cost_microusd=1)
        self.assertEqual(observed[:2], ["month", "sweep"])

    def test_binding_window_is_tied_to_plan_version(self):
        plan = self.create(amount="10")
        self.activate(plan)
        self.attempt(cost_microusd=1)
        with self.psycopg.connect(self.owner_dsn) as connection:
            table = self.sql.SQL(
                "{}.portfolio_work_budget_reservation_bindings"
            ).format(self.sql.Identifier(self.schema))
            connection.execute(
                self.sql.SQL("ALTER TABLE {} DISABLE TRIGGER USER").format(table)
            )
            with self.assertRaises(self.psycopg.errors.ForeignKeyViolation):
                connection.execute(
                    self.sql.SQL(
                        "UPDATE {} SET window_start_at=%s "
                        "WHERE organization_id='acme' AND budget_plan_id=%s"
                    ).format(table),
                    ("2000-01-01T00:00:00.000000Z", plan["budget_plan_id"]),
                )
            connection.rollback()

    def test_malformed_persisted_plan_refuses_without_repair(self):
        plan = self.create()
        self.activate(plan)
        with self.psycopg.connect(self.owner_dsn) as connection:
            table = self.sql.SQL(
                "{}.portfolio_work_budget_plan_versions"
            ).format(self.sql.Identifier(self.schema))
            connection.execute(
                self.sql.SQL("ALTER TABLE {} DISABLE TRIGGER USER").format(table)
            )
            connection.execute(
                self.sql.SQL(
                    "UPDATE {} SET allowed_models_json=%s "
                    "WHERE organization_id='acme' AND budget_plan_id=%s AND version=1"
                ).format(table),
                ("{}", plan["budget_plan_id"]),
            )
            connection.execute(
                self.sql.SQL("ALTER TABLE {} ENABLE TRIGGER USER").format(table)
            )
        self.error(
            "unavailable",
            lambda: self.repository.get_plan(ADMIN, plan["budget_plan_id"]),
        )
        with self.assertRaises(ReservationDenied):
            self.attempt(cost_microusd=1)
        self.assertEqual(self.attribution_rows(), [])
        self.assertEqual(
            self.budget_rows()["portfolio_work_budget_reservation_bindings"], [],
        )
        self.assertEqual(
            [
                row["reason_code"]
                for row in self.budget_rows()["portfolio_work_budget_audit_events"]
                if row["operation"] == "reserve_denied"
            ],
            ["attribution_invalid"],
        )

    def test_malformed_active_timestamp_cannot_hide_budget_enforcement(self):
        plan = self.create()
        self.activate(plan)
        with self.psycopg.connect(self.owner_dsn) as connection:
            activation = self.sql.SQL(
                "{}.portfolio_work_budget_activation_events"
            ).format(self.sql.Identifier(self.schema))
            pointer = self.sql.SQL(
                "{}.portfolio_work_budget_active_plans"
            ).format(self.sql.Identifier(self.schema))
            connection.execute(
                self.sql.SQL("ALTER TABLE {} DISABLE TRIGGER ALL").format(activation)
            )
            connection.execute(
                self.sql.SQL("ALTER TABLE {} DISABLE TRIGGER ALL").format(pointer)
            )
            connection.execute(
                self.sql.SQL(
                    "UPDATE {} SET committed_at='9999-99-99T99:99:99Z' "
                    "WHERE organization_id='acme' AND budget_plan_id=%s"
                ).format(activation),
                (plan["budget_plan_id"],),
            )
            connection.execute(
                self.sql.SQL(
                    "UPDATE {} SET changed_at='9999-99-99T99:99:99Z' "
                    "WHERE organization_id='acme' AND budget_plan_id=%s"
                ).format(pointer),
                (plan["budget_plan_id"],),
            )
            connection.execute(
                self.sql.SQL("ALTER TABLE {} ENABLE TRIGGER ALL").format(pointer)
            )
            connection.execute(
                self.sql.SQL("ALTER TABLE {} ENABLE TRIGGER ALL").format(activation)
            )
        self.error(
            "unavailable",
            lambda: self.repository.current_report(ADMIN, plan["budget_plan_id"]),
        )
        with self.assertRaises(ReservationDenied):
            self.attempt(cost_microusd=1)
        self.assertEqual(self.attribution_rows(), [])
        self.assertEqual(
            self.budget_rows()["portfolio_work_budget_reservation_bindings"], [],
        )
        self.assertEqual(
            [
                row["reason_code"]
                for row in self.budget_rows()["portfolio_work_budget_audit_events"]
                if row["operation"] == "reserve_denied"
            ],
            ["attribution_invalid"],
        )


if __name__ == "__main__":
    import unittest
    unittest.main()
