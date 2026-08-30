"""Disposable red-first #216 PostgreSQL transition; no attribution runtime."""

from __future__ import annotations

import copy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest import mock
from uuid import uuid4

import hormuz.postgres as postgres_module
from hormuz.config import UsageStorageConfig
from hormuz.portfolio_repository import RegistryRepository
from hormuz.portfolio_service import PortfolioService
from hormuz.portfolio_wire import SCOPES
from hormuz.postgres import PostgresStorageError, migrate_postgres
from hormuz.postgres_usage_store import PostgresUsageStore

if __package__:
    from ._portfolio_fixture import ADMIN, registry_config, seed_registry_metadata
    from ._postgres_fixture import PostgresTestCase
    from ._registry_transition_fixture import ledger_observation, released_v1_call, seed_registry_ledger
else:
    from _portfolio_fixture import ADMIN, registry_config, seed_registry_metadata
    from _postgres_fixture import PostgresTestCase
    from _registry_transition_fixture import ledger_observation, released_v1_call, seed_registry_ledger


PROBE = "attribution_transition_test_probe"


class PostgresAttributionTransitionTests(PostgresTestCase):
    def migrate(self):
        return migrate_postgres(
            self.owner_dsn, schema=self.schema, runtime_role=self.runtime_role,
            policy_control_role=self.policy_control_role, custody_control_role=self.custody_control_role,
            custody_executor_role=self.custody_executor_role,
        )

    def runtime(self, dsn=None):
        return PostgresUsageStore(dsn or self.runtime_dsn, schema=self.schema,
                                  runtime_role=self.runtime_role, organization_ids=("acme", "beta"))

    def setUp(self):
        self.assertEqual(postgres_module.POSTGRES_SCHEMA_VERSION, 9)
        self._drop_schema(self.schema)
        self.migrate()
        super().setUp()
        seed_registry_ledger(self.store)
        self.config = replace(registry_config(Path("/unused/attribution-preflight")), usage_storage=UsageStorageConfig(
            backend="postgresql", postgres_schema=self.schema, postgres_runtime_role=self.runtime_role))
        self.writes, self.page = seed_registry_metadata(self.config, environ={"HORMUZ_POSTGRES_DSN": self.runtime_dsn})
        self.before = self.snapshot()
        self.assertEqual(len(self.before["rows"]), 37)
        self.assertTrue(all(rows for table, rows in self.before["rows"].items() if table.startswith("portfolio_")))

    def snapshot(self, dsn=None):
        with self.psycopg.connect(dsn or self.owner_dsn) as connection:
            tables = connection.execute("SELECT tablename FROM pg_tables WHERE schemaname=%s ORDER BY tablename", (self.schema,)).fetchall()
            rows = {table: connection.execute(self.sql.SQL(
                "SELECT row_to_json(t)::text FROM {}.{} t ORDER BY row_to_json(t)::text"
            ).format(self.sql.Identifier(self.schema), self.sql.Identifier(table))).fetchall() for (table,) in tables}
            shape = connection.execute(
                "SELECT c.relname, c.relkind, c.relrowsecurity, c.relforcerowsecurity, c.relacl::text "
                "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s ORDER BY c.relname",
                (self.schema,),
            ).fetchall()
        return {"rows": rows, "shape": shape}

    def probe(self, *, fail=False):
        original = postgres_module._migration_sql

        def migration(version, schema, *roles):
            if version != 10:
                return original(version, schema, *roles)
            ddl = f"CREATE TABLE {schema}.{PROBE} (organization_id TEXT NOT NULL, id TEXT PRIMARY KEY);"
            return ddl + (" SELECT 1 / 0;" if fail else "")

        with mock.patch.object(postgres_module, "POSTGRES_SCHEMA_VERSION", 10), mock.patch.object(postgres_module, "_migration_sql", side_effect=migration):
            self.assertEqual(self.migrate().version, 10)

    def assert_prior_state_preserved(self):
        current = copy.deepcopy(self.snapshot())
        current["rows"].pop(PROBE, None)
        current["shape"] = [row for row in current["shape"] if not row[0].startswith(PROBE)]
        current["rows"]["hormuz_schema_migrations"] = [row for row in current["rows"]["hormuz_schema_migrations"] if json.loads(row[0])["version"] != 10]
        self.assertEqual(current, self.before)

    def backup(self):
        info = self.psycopg.conninfo.conninfo_to_dict(self.owner_dsn)
        result = subprocess.run(
            ["docker", "exec", "-i", os.environ["HORMUZ_TEST_PG_CONTAINER"], "pg_dump", "-U", info["user"],
             "-d", info["dbname"], "--schema", self.schema, "--format=custom"], capture_output=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, "test_pg_dump_failed")
        self.assertTrue(result.stdout.startswith(b"PGDMP"))
        return result.stdout

    def restore(self, backup):
        database = "attribution_restore_" + uuid4().hex[:12]
        with self.psycopg.connect(self.owner_dsn, autocommit=True) as connection:
            connection.execute(self.sql.SQL("CREATE DATABASE {}").format(self.sql.Identifier(database)))

        def cleanup():
            with self.psycopg.connect(self.owner_dsn, autocommit=True) as connection:
                connection.execute(self.sql.SQL("DROP DATABASE {}").format(self.sql.Identifier(database)))

        self.addCleanup(cleanup)
        info = self.psycopg.conninfo.conninfo_to_dict(self.owner_dsn)
        digest = hashlib.sha256(backup).hexdigest()
        result = subprocess.run(
            ["docker", "exec", "-i", os.environ["HORMUZ_TEST_PG_CONTAINER"], "pg_restore", "-U", info["user"],
             "-d", database, "--no-owner", "--exit-on-error"], input=backup, capture_output=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, "test_pg_restore_failed")
        self.assertEqual(hashlib.sha256(backup).hexdigest(), digest)
        return (self.psycopg.conninfo.make_conninfo(self.owner_dsn, dbname=database),
                self.psycopg.conninfo.make_conninfo(self.runtime_dsn, dbname=database))

    def test_postgres_attribution_migration_is_red_until_implementation(self):
        with mock.patch.object(postgres_module, "POSTGRES_SCHEMA_VERSION", 10):
            with self.assertRaises(PostgresStorageError) as caught:
                self.migrate()
        self.assertEqual(caught.exception.code, "storage_schema_migration_unsupported")
        self.assertEqual(self.snapshot(), self.before)

    def test_postgres_attribution_probe_failure_and_retry_preserve_populated_registry(self):
        with self.assertRaises(PostgresStorageError) as caught:
            self.probe(fail=True)
        self.assertEqual(caught.exception.code, "storage_unavailable")
        self.assertEqual(self.snapshot(), self.before)
        self.probe()
        self.assert_prior_state_preserved()
        current = self.snapshot()
        self.probe()
        self.assertEqual(self.snapshot(), current)

    def test_postgres_attribution_partial_state_refuses_before_repair(self):
        with self.psycopg.connect(self.owner_dsn) as connection:
            connection.execute(self.sql.SQL("INSERT INTO {}.hormuz_schema_migrations (version, state) VALUES (10, 'applying')").format(self.sql.Identifier(self.schema)))
        before = self.snapshot()
        for operation in (self.migrate, self.runtime):
            with self.assertRaises(PostgresStorageError) as caught:
                operation()
            self.assertEqual(caught.exception.code, "storage_schema_partial_upgrade")
            self.assertEqual(self.snapshot(), before)

    @unittest.skipUnless(os.environ.get("HORMUZ_TEST_V1_PYTHON"), "requires digest-pinned released-v1 interpreter")
    def test_postgres_attribution_old_processes_refuse_next_schema(self):
        self.probe()
        before = self.snapshot()
        with self.assertRaises(PostgresStorageError) as caught:
            self.runtime()
        self.assertEqual(caught.exception.code, "storage_schema_newer_than_binary")
        result = released_v1_call({"backend": "postgresql", "mode": "verify", "schema": self.schema,
                                   "runtime_dsn": self.runtime_dsn, "runtime_role": self.runtime_role})
        self.assertEqual(result, {"status": "refused", "code": "storage_schema_newer_than_binary"})
        self.assertEqual(self.snapshot(), before)
        # Inject partial state in this disposable future-schema fixture only.
        with self.psycopg.connect(self.owner_dsn) as connection:
            connection.execute(self.sql.SQL(
                "UPDATE {}.hormuz_schema_migrations SET state='applying' WHERE version=10"
            ).format(self.sql.Identifier(self.schema)))
        partial = self.snapshot()
        with self.assertRaises(PostgresStorageError) as caught:
            self.runtime()
        self.assertEqual(caught.exception.code, "storage_schema_partial_upgrade")
        result = released_v1_call({"backend": "postgresql", "mode": "verify", "schema": self.schema,
                                   "runtime_dsn": self.runtime_dsn, "runtime_role": self.runtime_role})
        self.assertEqual(result, {"status": "refused", "code": "storage_schema_partial_upgrade"})
        self.assertEqual(self.snapshot(), partial)

    @unittest.skipUnless(os.environ.get("HORMUZ_TEST_PG_CONTAINER"), "requires disposable PostgreSQL matched backup tools")
    def test_postgres_attribution_quiesced_registry_pair_restore_preserves_replays(self):
        backup = self.backup()
        self.probe()
        retained = self.snapshot()
        owner, runtime = self.restore(backup)
        self.assertEqual(ledger_observation(self.runtime(runtime))["unknown_holds"], 1)
        service = PortfolioService(self.config, RegistryRepository(self.config, environ={"HORMUZ_POSTGRES_DSN": runtime}))
        for path, body, key, expected in self.writes:
            self.assertEqual(service.dispatch(ADMIN, "POST", path, body=json.dumps(body).encode(), idempotency_key=key), expected)
        self.assertEqual(self.snapshot(owner), self.before)
        continuation = service.dispatch(ADMIN, "GET", SCOPES, query="cursor=" + self.page["next_cursor"])[1]
        self.assertEqual(continuation["items"], [self.writes[0][3][1]])
        self.assertIsNone(continuation["next_cursor"])
        self.assertEqual(self.snapshot(), retained)

    @unittest.skipUnless(os.environ.get("HORMUZ_TEST_PG_CONTAINER"), "requires disposable PostgreSQL matched backup tools")
    def test_postgres_attribution_post_checkpoint_writes_require_forward_recovery(self):
        self.probe()
        with mock.patch.object(postgres_module, "POSTGRES_SCHEMA_VERSION", 10):
            seed_registry_ledger(self.runtime())
            with self.psycopg.connect(self.owner_dsn) as connection:
                connection.execute(self.sql.SQL("INSERT INTO {}.{} VALUES ('acme','synthetic-post-checkpoint-write')").format(self.sql.Identifier(self.schema), self.sql.Identifier(PROBE)))
            after_write = self.snapshot()
            owner, runtime = self.restore(self.backup())
            self.assertEqual(ledger_observation(self.runtime(runtime))["unknown_holds"], 2)
            self.assertEqual(self.snapshot(owner), after_write)
        self.assertEqual(self.snapshot(), after_write)
        self.assertNotEqual(after_write, self.before)
        with self.assertRaises(PostgresStorageError) as caught:
            self.runtime(runtime)
        self.assertEqual(caught.exception.code, "storage_schema_newer_than_binary")
