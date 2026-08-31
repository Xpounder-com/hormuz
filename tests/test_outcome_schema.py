from __future__ import annotations

from importlib import resources
from pathlib import Path
import sqlite3
import tempfile
import unittest

from hormuz._outcome_schema import TABLE_DDL, postgres_statements
from hormuz.store import StorageSchemaError, UsageStore


class OutcomeSchemaTests(unittest.TestCase):
    def test_real_sqlite_seven_installs_all_outcome_tables_and_no_probe(self):
        self.assertEqual(UsageStore.schema_version, 7)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "usage.sqlite3"
            UsageStore(path).verify_ready()
            with sqlite3.connect(path) as connection:
                names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertTrue(set(TABLE_DDL).issubset(names))
                self.assertEqual(len(names), 29)
                self.assertNotIn("outcome_transition_test_probe", names)
                before = list(connection.iterdump())
            UsageStore(path).verify_ready()
            with sqlite3.connect(path) as connection:
                self.assertEqual(list(connection.iterdump()), before)
                connection.execute("INSERT INTO portfolio_outcome_audit_events VALUES ('acme','audit',1,NULL,'github-one','ingest','receipt','observed','2026-08-30T12:00:00Z')")
                for operation in ("UPDATE portfolio_outcome_audit_events SET reason_code='unsupported'", "DELETE FROM portfolio_outcome_audit_events"):
                    with self.assertRaisesRegex(sqlite3.IntegrityError, "portfolio_append_only"):
                        connection.execute(operation)

    def test_missing_shape_refuses_without_repair_or_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "usage.sqlite3"
            UsageStore(path)
            with sqlite3.connect(path) as connection:
                connection.execute("DROP TRIGGER portfolio_outcome_events_no_update")
                before = list(connection.iterdump())
            for read_only in (True, False):
                with self.assertRaises(StorageSchemaError) as caught:
                    UsageStore(path, read_only=read_only)
                self.assertEqual(caught.exception.code, "storage_schema_partial_upgrade")
                with sqlite3.connect(path) as connection:
                    self.assertEqual(list(connection.iterdump()), before)

    def test_packaged_postgres_eleven_matches_generated_owned_shape(self):
        actual = resources.files("hormuz").joinpath("migrations/postgresql/0011_work_outcomes.sql").read_text()
        self.assertEqual(actual.strip(), postgres_statements("{schema}", "{runtime_role}").strip())


if __name__ == "__main__":
    unittest.main()
