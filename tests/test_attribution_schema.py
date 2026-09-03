from __future__ import annotations

from pathlib import Path
from importlib import resources
import sqlite3
import tempfile
import unittest

from hormuz._attribution_schema import TABLE_DDL, postgres_statements
from hormuz.store import StorageSchemaError, UsageStore
if __package__:
    from ._sqlite import managed_sqlite_connection
else:
    from _sqlite import managed_sqlite_connection


class AttributionSchemaTests(unittest.TestCase):
    def test_attribution_six_remains_exact_in_current_cumulative_schema(self):
        self.assertEqual(UsageStore.schema_version, 12)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "usage.sqlite3"
            UsageStore(path).verify_ready()
            with managed_sqlite_connection(path) as connection:
                tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertTrue(set(TABLE_DDL).issubset(tables))
                self.assertEqual(len(tables), 46)
                self.assertEqual(connection.execute("SELECT state FROM hormuz_schema_migrations WHERE version=6").fetchone(), ("applied",))
                before = list(connection.iterdump())
            UsageStore(path).verify_ready()
            with managed_sqlite_connection(path) as connection:
                self.assertEqual(list(connection.iterdump()), before)
                connection.execute("INSERT INTO portfolio_attribution_audit_events VALUES ('acme','audit',1,NULL,'admit','attempt','bound','2026-01-01T00:00:00Z')")
                with self.assertRaisesRegex(sqlite3.IntegrityError, "portfolio_append_only"):
                    connection.execute("DELETE FROM portfolio_attribution_audit_events")

    def test_missing_attribution_shape_fails_without_automatic_repair(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "usage.sqlite3"
            UsageStore(path)
            with managed_sqlite_connection(path) as connection:
                connection.execute("DROP TRIGGER portfolio_attribution_events_no_update")
                before = list(connection.iterdump())
            for readonly in (False, True):
                with self.assertRaises(StorageSchemaError) as caught:
                    UsageStore(path, read_only=readonly)
                self.assertEqual(caught.exception.code, "storage_schema_partial_upgrade")
                with managed_sqlite_connection(path) as connection:
                    self.assertEqual(list(connection.iterdump()), before)

    def test_packaged_postgres_migration_matches_owned_schema_source(self):
        actual = resources.files("hormuz").joinpath("migrations/postgresql/0010_governed_run_attribution.sql").read_text()
        self.assertEqual(actual.strip(), postgres_statements("{schema}", "{runtime_role}").strip())


if __name__ == "__main__":
    unittest.main()
