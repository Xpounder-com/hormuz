from __future__ import annotations

import ast
import sqlite3
import tempfile
import unittest
from pathlib import Path

import hormuz._sqlite_schema as sqlite_schema
import hormuz.store as store_module
from hormuz.store import UsageStore


class SQLiteSchemaOwnershipTests(unittest.TestCase):
    def test_usage_store_remains_the_schema_version_facade(self) -> None:
        self.assertEqual(UsageStore.schema_version, sqlite_schema.SQLITE_SCHEMA_VERSION)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "usage.sqlite3"
            UsageStore(path).verify_ready()
            connection = sqlite3.connect(path)
            migrations = connection.execute(
                "SELECT version, state FROM hormuz_schema_migrations ORDER BY version"
            ).fetchall()
            connection.close()
        self.assertEqual(migrations, [(1, "applied"), (2, "applied"), (3, "applied"), (4, "applied"), (5, "applied"), (6, "applied"), (7, "applied")])

    def test_gateway_ddl_is_owned_only_by_the_internal_schema_module(self) -> None:
        store_source = Path(store_module.__file__).read_text(encoding="utf-8")
        self.assertNotIn("CREATE TABLE IF NOT EXISTS gateway_", store_source)
        self.assertNotIn("ALTER TABLE", store_source)
        self.assertNotIn("PRAGMA table_info", store_source)

        schema_source = Path(sqlite_schema.__file__).read_text(encoding="utf-8")
        for table in (
            "gateway_usage_events",
            "gateway_secret_events",
            "gateway_budget_reservations",
            "gateway_request_attempts",
            "gateway_request_attempt_events",
            "gateway_audit_chain_epochs",
            "gateway_audit_chain_heads",
            "gateway_audit_chain_entries",
            "gateway_audit_chain_checkpoints",
        ):
            with self.subTest(table=table):
                self.assertEqual(
                    schema_source.count(f"CREATE TABLE IF NOT EXISTS {table}"),
                    1,
                )

    def test_schema_module_does_not_import_runtime_store_or_postgres_adapter(self) -> None:
        source = Path(sqlite_schema.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        imported.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        self.assertNotIn("store", imported)
        self.assertNotIn("postgres_usage_store", imported)
        self.assertNotIn("postgres", imported)


if __name__ == "__main__":
    unittest.main()
