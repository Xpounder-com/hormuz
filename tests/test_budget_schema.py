from __future__ import annotations

from contextlib import closing
from importlib import resources
from pathlib import Path
import sqlite3
import tempfile
import unittest

from hormuz._budget_schema import (
    BUDGET_BINDING_ACCOUNTING_COLUMNS,
    BUDGET_BINDING_ACCOUNTING_INDEX,
    TABLE_DDL,
    postgres_statements,
)
from hormuz.store import StorageSchemaError, UsageStore


class BudgetSchemaTests(unittest.TestCase):
    def test_budget_nine_remains_exact_in_current_cumulative_schema(self):
        self.assertEqual(UsageStore.schema_version, 9)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "usage.sqlite3"
            UsageStore(path).verify_ready()
            with closing(sqlite3.connect(path)) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                self.assertTrue(set(TABLE_DDL).issubset(tables))
                self.assertEqual(len(tables), 36)
                self.assertEqual(
                    connection.execute(
                        "SELECT state FROM hormuz_schema_migrations WHERE version=9"
                    ).fetchone(),
                    ("applied",),
                )
                self.assertEqual(
                    tuple(
                        row[2]
                        for row in connection.execute(
                            f"PRAGMA index_info({BUDGET_BINDING_ACCOUNTING_INDEX})"
                        )
                    ),
                    BUDGET_BINDING_ACCOUNTING_COLUMNS,
                )
                before = list(connection.iterdump())
            UsageStore(path).verify_ready()
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(list(connection.iterdump()), before)
                connection.execute(
                    "INSERT INTO portfolio_work_budget_audit_events VALUES "
                    "('acme',?,1,NULL,'report','candidate-budget',1,'observed',"
                    "'2026-08-31T12:00:00Z')",
                    ("f" * 32,),
                )
                with self.assertRaisesRegex(
                    sqlite3.IntegrityError, "portfolio_budget_append_only"
                ):
                    connection.execute(
                        "DELETE FROM portfolio_work_budget_audit_events"
                    )

    def test_missing_budget_shape_fails_without_automatic_repair(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "usage.sqlite3"
            UsageStore(path)
            with closing(sqlite3.connect(path)) as connection:
                connection.execute(
                    "DROP TRIGGER portfolio_work_budget_reservation_bindings_no_update"
                )
                before = list(connection.iterdump())
            for read_only in (False, True):
                with self.assertRaises(StorageSchemaError) as caught:
                    UsageStore(path, read_only=read_only)
                self.assertEqual(
                    caught.exception.code, "storage_schema_partial_upgrade"
                )
                with closing(sqlite3.connect(path)) as connection:
                    self.assertEqual(list(connection.iterdump()), before)

    def test_missing_budget_accounting_index_fails_without_automatic_repair(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "usage.sqlite3"
            UsageStore(path)
            with closing(sqlite3.connect(path)) as connection:
                connection.execute(f"DROP INDEX {BUDGET_BINDING_ACCOUNTING_INDEX}")
                before = list(connection.iterdump())
            with self.assertRaises(StorageSchemaError) as caught:
                UsageStore(path)
            self.assertEqual(caught.exception.code, "storage_schema_partial_upgrade")
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(list(connection.iterdump()), before)

    def test_packaged_postgres_migration_matches_owned_schema_source(self):
        actual = resources.files("hormuz").joinpath(
            "migrations/postgresql/0013_work_budgets.sql"
        ).read_text()
        self.assertEqual(
            actual,
            postgres_statements("{schema}", "{runtime_role}"),
        )


if __name__ == "__main__":
    unittest.main()
