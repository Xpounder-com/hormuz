"""Atomic SQLite 18-to-19 and PostgreSQL 23-to-24 recommendation transitions."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import hormuz.postgres as postgres_module
from hormuz._recommendation_schema import TABLE_DDL
from hormuz.postgres import PostgresStorageError, migrate_postgres
from hormuz.postgres_usage_store import PostgresUsageStore
from hormuz.store import StorageSchemaError, UsageStore

from ._postgres_fixture import PostgresTestCase
from ._registry_transition_fixture import seed_registry_ledger, sqlite_snapshot
from ._sqlite import managed_sqlite_connection


class SQLiteRecommendationTransitionTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "usage.sqlite3"
        with mock.patch.object(UsageStore, "schema_version", 18):
            seed_registry_ledger(UsageStore(self.path))
        self.before = sqlite_snapshot(self.path)
        self.predecessor_versions = {
            row[0] for row in self.before["rows"]["hormuz_schema_migrations"]
        }
        self.assertEqual(max(self.predecessor_versions), 18)

    def upgrade(self, *, fail: bool = False) -> None:
        original = UsageStore._apply_migration

        def apply(connection, version):
            self.assertEqual(version, 19)
            original(connection, version)
            if fail:
                raise RuntimeError("synthetic_recommendation_migration_failure")

        with mock.patch.object(UsageStore, "_apply_migration", side_effect=apply):
            UsageStore(self.path).verify_ready()

    def assert_predecessor_preserved(self) -> None:
        current = copy.deepcopy(sqlite_snapshot(self.path))
        current["objects"] = [
            row for row in current["objects"] if row[2] not in TABLE_DDL
        ]
        for table in TABLE_DDL:
            current["rows"].pop(table, None)
        current["rows"]["hormuz_schema_migrations"] = [
            row
            for row in current["rows"]["hormuz_schema_migrations"]
            if row[0] in self.predecessor_versions
        ]
        self.assertEqual(current, self.before)

    def test_real_migration_preserves_predecessor_and_rejects_missing_successor(self):
        self.upgrade()
        self.assert_predecessor_preserved()
        current = sqlite_snapshot(self.path)
        self.assertTrue(all(not current["rows"][table] for table in TABLE_DDL))
        self.upgrade()
        self.assertEqual(sqlite_snapshot(self.path), current)

        with mock.patch.object(UsageStore, "schema_version", 20):
            with self.assertRaises(StorageSchemaError) as caught:
                UsageStore(self.path)
        self.assertEqual(caught.exception.code, "storage_schema_migration_unsupported")
        self.assertEqual(sqlite_snapshot(self.path), current)

    def test_partial_ddl_failure_rolls_back_and_retry_is_idempotent(self):
        with self.assertRaisesRegex(
            RuntimeError, "synthetic_recommendation_migration_failure"
        ):
            self.upgrade(fail=True)
        self.assertEqual(sqlite_snapshot(self.path), self.before)
        self.upgrade()
        self.assert_predecessor_preserved()

    def test_predecessor_refuses_successor_without_mutation(self):
        self.upgrade()
        current = sqlite_snapshot(self.path)
        with mock.patch.object(UsageStore, "schema_version", 18):
            with self.assertRaises(StorageSchemaError) as caught:
                UsageStore(self.path, read_only=True)
        self.assertEqual(caught.exception.code, "storage_schema_newer_than_binary")
        self.assertEqual(sqlite_snapshot(self.path), current)

    def test_partial_ledger_refuses_before_any_schema_repair(self):
        with managed_sqlite_connection(self.path) as connection:
            connection.execute(
                "INSERT INTO hormuz_schema_migrations (version,state) "
                "VALUES (19,'applying')"
            )
        partial = sqlite_snapshot(self.path)
        for schema_version in (18, 19):
            with self.subTest(schema_version=schema_version), mock.patch.object(
                UsageStore, "schema_version", schema_version
            ):
                with self.assertRaises(StorageSchemaError) as caught:
                    UsageStore(self.path, read_only=True)
                self.assertEqual(caught.exception.code, "storage_schema_partial_upgrade")
                self.assertEqual(sqlite_snapshot(self.path), partial)


@unittest.skipUnless(
    os.environ.get("HORMUZ_TEST_POSTGRES_DSN"),
    "requires disposable PostgreSQL",
)
class PostgresRecommendationTransitionTests(PostgresTestCase):
    def migrate(self):
        return migrate_postgres(
            self.owner_dsn,
            schema=self.schema,
            runtime_role=self.runtime_role,
            policy_control_role=self.policy_control_role,
            custody_control_role=self.custody_control_role,
            custody_executor_role=self.custody_executor_role,
        )

    def runtime(self):
        return PostgresUsageStore(
            self.runtime_dsn,
            schema=self.schema,
            runtime_role=self.runtime_role,
            organization_ids=("acme", "beta"),
        )

    def setUp(self) -> None:
        super().setUp()
        self._drop_schema(self.schema)
        with mock.patch.object(postgres_module, "POSTGRES_SCHEMA_VERSION", 23):
            self.assertEqual(self.migrate().version, 23)
            seed_registry_ledger(self.runtime())
        self.before = self.snapshot()
        self.predecessor_versions = {
            json.loads(row[0])["version"]
            for row in self.before["rows"]["hormuz_schema_migrations"]
        }
        self.assertEqual(max(self.predecessor_versions), 23)

    def snapshot(self) -> dict[str, object]:
        with self.psycopg.connect(self.owner_dsn) as connection:
            tables = connection.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname=%s ORDER BY tablename",
                (self.schema,),
            ).fetchall()
            rows = {
                table: connection.execute(
                    self.sql.SQL(
                        "SELECT row_to_json(t)::text FROM {}.{} t "
                        "ORDER BY row_to_json(t)::text"
                    ).format(
                        self.sql.Identifier(self.schema),
                        self.sql.Identifier(table),
                    )
                ).fetchall()
                for (table,) in tables
            }
            shape = connection.execute(
                "SELECT c.relname,c.relkind,c.relrowsecurity,c.relforcerowsecurity,"
                "c.relacl::text FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname=%s ORDER BY c.relname",
                (self.schema,),
            ).fetchall()
        return {"rows": rows, "shape": shape}

    def upgrade(self, *, fail: bool = False) -> None:
        original = postgres_module._migration_sql

        def migration(version, schema, *roles):
            self.assertEqual(version, 24)
            ddl = original(version, schema, *roles)
            if fail:
                return ddl.split(";", 1)[0] + "; SELECT 1 / 0;"
            return ddl

        with mock.patch.object(
            postgres_module, "_migration_sql", side_effect=migration
        ):
            self.assertEqual(self.migrate().version, 24)

    def assert_predecessor_preserved(self) -> None:
        current = copy.deepcopy(self.snapshot())
        for table in TABLE_DDL:
            current["rows"].pop(table, None)
        current["rows"]["hormuz_schema_migrations"] = [
            row
            for row in current["rows"]["hormuz_schema_migrations"]
            if json.loads(row[0])["version"] in self.predecessor_versions
        ]
        current["shape"] = [
            row
            for row in current["shape"]
            if not str(row[0]).startswith(
                ("portfolio_policy_recommend", "portfolio_recommendation_")
            )
        ]
        self.assertEqual(current, self.before)

    def test_real_migration_preserves_predecessor_and_rejects_missing_successor(self):
        self.upgrade()
        self.assert_predecessor_preserved()
        current = self.snapshot()
        self.assertTrue(all(not current["rows"][table] for table in TABLE_DDL))
        self.upgrade()
        self.assertEqual(self.snapshot(), current)

        with mock.patch.object(postgres_module, "POSTGRES_SCHEMA_VERSION", 25):
            with self.assertRaises(PostgresStorageError) as caught:
                self.migrate()
        self.assertEqual(caught.exception.code, "storage_schema_migration_unsupported")
        self.assertEqual(self.snapshot(), current)

    def test_partial_ddl_failure_rolls_back_and_retry_is_idempotent(self):
        with self.assertRaises(PostgresStorageError) as caught:
            self.upgrade(fail=True)
        self.assertEqual(caught.exception.code, "storage_unavailable")
        self.assertEqual(self.snapshot(), self.before)
        self.upgrade()
        self.assert_predecessor_preserved()

    def test_predecessor_refuses_the_successor_without_mutation(self):
        self.upgrade()
        current = self.snapshot()
        with mock.patch.object(postgres_module, "POSTGRES_SCHEMA_VERSION", 23):
            with self.assertRaises(PostgresStorageError) as caught:
                self.migrate()
            self.assertEqual(caught.exception.code, "storage_schema_newer_than_binary")
            with self.assertRaises(PostgresStorageError) as caught:
                self.runtime()
            self.assertEqual(caught.exception.code, "storage_schema_newer_than_binary")
        self.assertEqual(self.snapshot(), current)

    def test_partial_ledger_refuses_before_any_schema_repair(self):
        with self.psycopg.connect(self.owner_dsn) as connection:
            connection.execute(
                self.sql.SQL(
                    "INSERT INTO {}.hormuz_schema_migrations (version,state) "
                    "VALUES (24,'applying')"
                ).format(self.sql.Identifier(self.schema))
            )
        partial = self.snapshot()
        try:
            for schema_version in (23, 24):
                with self.subTest(schema_version=schema_version), mock.patch.object(
                    postgres_module, "POSTGRES_SCHEMA_VERSION", schema_version
                ):
                    with self.assertRaises(PostgresStorageError) as caught:
                        self.migrate()
                    self.assertEqual(
                        caught.exception.code, "storage_schema_partial_upgrade"
                    )
                    self.assertEqual(self.snapshot(), partial)
        finally:
            with self.psycopg.connect(self.owner_dsn) as connection:
                connection.execute(
                    self.sql.SQL(
                        "DELETE FROM {}.hormuz_schema_migrations WHERE version=24"
                    ).format(self.sql.Identifier(self.schema))
                )
            self.upgrade()


if __name__ == "__main__":
    unittest.main()
