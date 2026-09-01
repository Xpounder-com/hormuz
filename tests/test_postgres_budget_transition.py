"""Red-first PostgreSQL 12-to-13 budget transition using the actual predecessor."""

from __future__ import annotations

import hashlib
import os
import subprocess
import unittest
from unittest import mock
from uuid import uuid4

import hormuz.postgres as postgres_module
from hormuz.postgres import PostgresStorageError, migrate_postgres
from hormuz.postgres_usage_store import PostgresUsageStore

if __package__:
    from ._budget_predecessor_fixture import finance_predecessor_call
    from ._postgres_fixture import PostgresTestCase
else:
    from _budget_predecessor_fixture import finance_predecessor_call
    from _postgres_fixture import PostgresTestCase


PROBE_TABLE = "budget_transition_test_probe"


@unittest.skipUnless(
    os.environ.get("HORMUZ_TEST_FINANCE_PYTHON") and os.environ.get("HORMUZ_TEST_POSTGRES_DSN"),
    "requires digest-pinned finance predecessor and disposable PostgreSQL",
)
class PostgresBudgetTransitionTests(PostgresTestCase):
    def migrate(self):
        return migrate_postgres(
            self.owner_dsn, schema=self.schema, runtime_role=self.runtime_role,
            policy_control_role=self.policy_control_role,
            custody_control_role=self.custody_control_role,
            custody_executor_role=self.custody_executor_role,
        )

    def runtime(self, dsn=None):
        return PostgresUsageStore(
            dsn or self.runtime_dsn, schema=self.schema,
            runtime_role=self.runtime_role, organization_ids=("acme", "beta"),
        )

    def setUp(self):
        self.assertEqual(postgres_module.POSTGRES_SCHEMA_VERSION, 12)
        self._drop_schema(self.schema)
        self.request = {
            "backend": "postgresql", "schema": self.schema,
            "owner_dsn": self.owner_dsn, "runtime_dsn": self.runtime_dsn,
            "runtime_role": self.runtime_role,
            "policy_control_role": self.policy_control_role,
            "custody_control_role": self.custody_control_role,
            "custody_executor_role": self.custody_executor_role,
        }
        self.seeded = finance_predecessor_call({**self.request, "mode": "seed"})
        self.assertEqual(self.seeded["status"], "ready")
        self.assertEqual(self.seeded["runtime_files_verified"], 111)
        self.before = self.snapshot()
        self.assertEqual(len(self.before["rows"]), 53)
        self.assertTrue(self.seeded["finance_registration"]["receipt_id"])

    def snapshot(self, dsn=None):
        with self.psycopg.connect(dsn or self.owner_dsn) as connection:
            tables = connection.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname=%s ORDER BY tablename",
                (self.schema,),
            ).fetchall()
            rows = {
                table: connection.execute(self.sql.SQL(
                    "SELECT row_to_json(t)::text FROM {}.{} t ORDER BY row_to_json(t)::text"
                ).format(self.sql.Identifier(self.schema), self.sql.Identifier(table))).fetchall()
                for (table,) in tables
            }
            shape = connection.execute(
                "SELECT c.relname,c.relkind,c.relrowsecurity,c.relforcerowsecurity,c.relacl::text "
                "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname=%s ORDER BY c.relname",
                (self.schema,),
            ).fetchall()
        return {"rows": rows, "shape": shape}

    def probe(self, *, fail=False):
        original = postgres_module._migration_sql

        def migration(version, schema, *roles):
            if version != 13:
                return original(version, schema, *roles)
            statement = (
                f"CREATE TABLE {schema}.\"{PROBE_TABLE}\" "
                "(id BIGINT PRIMARY KEY, marker TEXT NOT NULL);"
            )
            return statement + (" SELECT 1 / 0;" if fail else "")

        with (
            mock.patch.object(postgres_module, "POSTGRES_SCHEMA_VERSION", 13),
            mock.patch.object(postgres_module, "_migration_sql", side_effect=migration),
        ):
            self.assertEqual(self.migrate().version, 13)

    def test_missing_migration_is_red_and_rolls_back_cleanly(self):
        with mock.patch.object(postgres_module, "POSTGRES_SCHEMA_VERSION", 13):
            with self.assertRaises(PostgresStorageError) as caught:
                self.migrate()
        self.assertEqual(caught.exception.code, "storage_schema_migration_unsupported")
        self.assertEqual(self.snapshot(), self.before)
        self.runtime().verify_ready()

    def test_probe_failure_rolls_back_and_retry_is_idempotent(self):
        with self.assertRaises(PostgresStorageError) as caught:
            self.probe(fail=True)
        self.assertEqual(caught.exception.code, "storage_unavailable")
        self.assertEqual(self.snapshot(), self.before)
        self.probe()
        after = self.snapshot()
        self.assertIn(PROBE_TABLE, after["rows"])
        self.assertEqual(after["rows"][PROBE_TABLE], [])
        self.probe()
        self.assertEqual(self.snapshot(), after)

    def test_current_and_predecessor_binaries_refuse_newer_and_partial_probe_state(self):
        self.probe()
        candidate = self.snapshot()
        with self.assertRaises(PostgresStorageError) as caught:
            self.runtime()
        self.assertEqual(caught.exception.code, "storage_schema_newer_than_binary")
        self.assertEqual(
            finance_predecessor_call({**self.request, "mode": "verify"}),
            {"status": "refused", "code": "storage_schema_newer_than_binary"},
        )
        self.assertEqual(self.snapshot(), candidate)
        with self.psycopg.connect(self.owner_dsn) as connection:
            connection.execute(self.sql.SQL(
                "UPDATE {}.hormuz_schema_migrations SET state='applying' WHERE version=13"
            ).format(self.sql.Identifier(self.schema)))
        partial = self.snapshot()
        with self.assertRaises(PostgresStorageError) as caught:
            self.runtime()
        self.assertEqual(caught.exception.code, "storage_schema_partial_upgrade")
        self.assertEqual(
            finance_predecessor_call({**self.request, "mode": "verify"}),
            {"status": "refused", "code": "storage_schema_partial_upgrade"},
        )
        self.assertEqual(self.snapshot(), partial)

    def backup(self):
        info = self.psycopg.conninfo.conninfo_to_dict(self.owner_dsn)
        result = subprocess.run(
            ["docker", "exec", "-i", os.environ["HORMUZ_TEST_PG_CONTAINER"],
             "pg_dump", "-U", info["user"], "-d", info["dbname"],
             "--schema", self.schema, "--format=custom"],
            capture_output=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, "test_pg_dump_failed")
        self.assertTrue(result.stdout.startswith(b"PGDMP"))
        return result.stdout

    def restore(self, backup):
        database = "budget_restore_" + uuid4().hex[:12]
        with self.psycopg.connect(self.owner_dsn, autocommit=True) as connection:
            connection.execute(self.sql.SQL("CREATE DATABASE {}").format(self.sql.Identifier(database)))

        def cleanup():
            with self.psycopg.connect(self.owner_dsn, autocommit=True) as connection:
                connection.execute(self.sql.SQL("DROP DATABASE {}").format(self.sql.Identifier(database)))

        self.addCleanup(cleanup)
        info = self.psycopg.conninfo.conninfo_to_dict(self.owner_dsn)
        digest = hashlib.sha256(backup).hexdigest()
        result = subprocess.run(
            ["docker", "exec", "-i", os.environ["HORMUZ_TEST_PG_CONTAINER"],
             "pg_restore", "-U", info["user"], "-d", database,
             "--no-owner", "--exit-on-error"],
            input=backup, capture_output=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, "test_pg_restore_failed")
        self.assertEqual(hashlib.sha256(backup).hexdigest(), digest)
        return (
            self.psycopg.conninfo.make_conninfo(self.owner_dsn, dbname=database),
            self.psycopg.conninfo.make_conninfo(self.runtime_dsn, dbname=database),
        )

    @unittest.skipUnless(os.environ.get("HORMUZ_TEST_PG_CONTAINER"), "requires matched disposable backup tools")
    def test_quiesced_old_pair_restore_preserves_all_replays_and_finance_receipt(self):
        backup = self.backup()
        self.probe()
        retained = self.snapshot()
        owner, runtime = self.restore(backup)
        replay = {
            **self.request, "runtime_dsn": runtime, "mode": "replay",
            "registry_writes": self.seeded["registry_writes"],
            "attribution_write": self.seeded["attribution_write"],
            "outcome_seed": self.seeded["outcome_seed"],
            "finance_registration": self.seeded["finance_registration"],
        }
        replayed = finance_predecessor_call(replay)
        self.assertEqual(replayed["unknown_holds"], 1)
        self.assertEqual(replayed["registry_replays"], [write[3] for write in self.seeded["registry_writes"]])
        self.assertEqual(replayed["attribution_replay"], self.seeded["attribution_write"][2])
        self.assertEqual(replayed["outcome_replays"], [item["receipt"] for item in self.seeded["outcome_seed"]["deliveries"]])
        self.assertEqual(replayed["outcome_retention"], self.seeded["outcome_seed"]["retention"])
        self.assertEqual(replayed["finance_replay"], self.seeded["finance_registration"])
        self.assertEqual(self.snapshot(owner), self.before)
        self.assertEqual(self.snapshot(), retained)

    @unittest.skipUnless(os.environ.get("HORMUZ_TEST_PG_CONTAINER"), "requires matched disposable backup tools")
    def test_post_checkpoint_probe_write_requires_forward_recovery(self):
        self.probe()
        with self.psycopg.connect(self.owner_dsn) as connection:
            connection.execute(self.sql.SQL(
                "INSERT INTO {}.{} (id, marker) VALUES (1, 'candidate-write')"
            ).format(self.sql.Identifier(self.schema), self.sql.Identifier(PROBE_TABLE)))
        after_write = self.snapshot()
        backup = self.backup()
        owner, runtime = self.restore(backup)
        with mock.patch.object(postgres_module, "POSTGRES_SCHEMA_VERSION", 13):
            self.runtime(runtime).verify_ready()
        self.assertEqual(self.snapshot(owner), after_write)
        self.assertEqual(
            finance_predecessor_call({**self.request, "runtime_dsn": runtime, "mode": "verify"}),
            {"status": "refused", "code": "storage_schema_newer_than_binary"},
        )
        self.assertEqual(self.snapshot(), after_write)


if __name__ == "__main__":
    unittest.main()
