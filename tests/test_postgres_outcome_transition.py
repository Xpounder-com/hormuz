"""Real PostgreSQL outcome migration, actual predecessors, isolated restores."""

from __future__ import annotations

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
from hormuz.postgres import PostgresStorageError, migrate_postgres
from hormuz.postgres_usage_store import PostgresUsageStore
from hormuz._outcome_schema import TABLE_DDL
from hormuz._finance_schema import TABLE_DDL as FINANCE_TABLES
from hormuz._budget_schema import TABLE_DDL as BUDGET_TABLES
from hormuz._provider_reliability_schema import TABLE_DDL as PROVIDER_TABLES
from hormuz.config import UsageStorageConfig
from hormuz.portfolio_repository import create_portfolio_repository
from hormuz.portfolio_service import PortfolioService
from hormuz.portfolio_wire import OUTCOMES

if __package__:
    from ._outcome_predecessor_fixture import attribution_predecessor_call
    from ._outcome_fixture import replay_outcome_metadata, seed_outcome_metadata
    from ._portfolio_fixture import ADMIN, registry_config
    from ._postgres_fixture import PostgresTestCase, without_finance_attempt_successor
    from ._registry_transition_fixture import ledger_observation, released_v1_call, seed_registry_ledger
else:
    from _outcome_predecessor_fixture import attribution_predecessor_call
    from _outcome_fixture import replay_outcome_metadata, seed_outcome_metadata
    from _portfolio_fixture import ADMIN, registry_config
    from _postgres_fixture import PostgresTestCase, without_finance_attempt_successor
    from _registry_transition_fixture import ledger_observation, released_v1_call, seed_registry_ledger


@unittest.skipUnless(os.environ.get("HORMUZ_TEST_ATTRIBUTION_PYTHON"), "requires digest-pinned actual attribution predecessor")
class PostgresOutcomeTransitionTests(PostgresTestCase):
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
        # This suite proves the completed 10-to-11 outcome transition; the
        # later collection transition owns the current v16 gate.
        self._schema_version_patch = mock.patch.object(
            postgres_module, "POSTGRES_SCHEMA_VERSION", 15
        )
        self._schema_version_patch.start()
        self.addCleanup(self._schema_version_patch.stop)
        self.assertEqual(postgres_module.POSTGRES_SCHEMA_VERSION, 15)
        # The inherited fixture creates and owns this unique test schema/roles.
        self._drop_schema(self.schema)
        self.predecessor_request = {
            "backend": "postgresql", "schema": self.schema, "owner_dsn": self.owner_dsn,
            "runtime_dsn": self.runtime_dsn, "runtime_role": self.runtime_role,
            "policy_control_role": self.policy_control_role, "custody_control_role": self.custody_control_role,
            "custody_executor_role": self.custody_executor_role,
        }
        self.seeded = attribution_predecessor_call({**self.predecessor_request, "mode": "seed"})
        self.assertEqual(self.seeded["status"], "ready")
        self.assertEqual(self.seeded["runtime_files_verified"], 99)
        self.before = self.snapshot()
        self.assertEqual(len(self.before["rows"]), 42)
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

    def upgrade(self, *, fail=False):
        original = postgres_module._migration_sql

        def migration(version, schema, *roles):
            self.assertIn(version, (11, 12, 13, 14, 15))
            ddl = original(version, schema, *roles)
            return ddl.split(";", 1)[0] + "; SELECT 1 / 0;" if fail and version == 11 else ddl

        with mock.patch.object(postgres_module, "_migration_sql", side_effect=migration):
            self.assertEqual(self.migrate().version, 15)

    def assert_prior_state_preserved(self):
        current = without_finance_attempt_successor(self.snapshot())
        added = set(TABLE_DDL) | set(FINANCE_TABLES) | set(BUDGET_TABLES) | set(PROVIDER_TABLES)
        current["rows"] = {table: rows for table, rows in current["rows"].items() if table not in added}
        current["shape"] = [row for row in current["shape"] if not row[0].startswith(("portfolio_outcome_", "portfolio_finance_", "portfolio_work_budget_", "gateway_provider_"))]
        current["rows"]["hormuz_schema_migrations"] = [row for row in current["rows"]["hormuz_schema_migrations"] if json.loads(row[0])["version"] not in {11, 12, 13, 14}]
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
        database = "outcome_restore_" + uuid4().hex[:12]
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

    def test_postgres_outcome_real_migration_and_missing_following_migration(self):
        self.upgrade()
        self.assert_prior_state_preserved()
        self.assertEqual(len(self.snapshot()["rows"]), 61)
        before = self.snapshot()
        with mock.patch.object(postgres_module, "POSTGRES_SCHEMA_VERSION", 16):
            with self.assertRaises(PostgresStorageError) as caught:
                self.migrate()
        self.assertEqual(caught.exception.code, "storage_schema_migration_unsupported")
        self.assertEqual(self.snapshot(), before)

    def test_postgres_outcome_real_partial_ddl_failure_retry_preserves_all_predecessor_state(self):
        with self.assertRaises(PostgresStorageError) as caught:
            self.upgrade(fail=True)
        self.assertEqual(caught.exception.code, "storage_unavailable")
        self.assertEqual(self.snapshot(), self.before)
        self.upgrade()
        self.assert_prior_state_preserved()
        current = self.snapshot()
        self.upgrade()
        self.assertEqual(self.snapshot(), current)

    def test_postgres_outcome_partial_state_refuses_before_repair(self):
        with self.psycopg.connect(self.owner_dsn) as connection:
            connection.execute(self.sql.SQL("INSERT INTO {}.hormuz_schema_migrations (version, state) VALUES (11, 'applying')").format(self.sql.Identifier(self.schema)))
        before = self.snapshot()
        for operation in (self.migrate, self.runtime):
            with self.assertRaises(PostgresStorageError) as caught:
                operation()
            self.assertEqual(caught.exception.code, "storage_schema_partial_upgrade")
            self.assertEqual(self.snapshot(), before)

    @unittest.skipUnless(os.environ.get("HORMUZ_TEST_V1_PYTHON"), "requires digest-pinned released-v1 interpreter")
    def test_postgres_outcome_actual_old_binaries_refuse_next_and_partial_schema(self):
        self.upgrade()
        before = self.snapshot()
        self.runtime()
        request = {**self.predecessor_request, "mode": "verify"}
        for driver in (attribution_predecessor_call, released_v1_call):
            self.assertEqual(driver(request), {"status": "refused", "code": "storage_schema_newer_than_binary"})
        self.assertEqual(self.snapshot(), before)
        with self.psycopg.connect(self.owner_dsn) as connection:
            connection.execute(self.sql.SQL("UPDATE {}.hormuz_schema_migrations SET state='applying' WHERE version=11").format(self.sql.Identifier(self.schema)))
        partial = self.snapshot()
        for driver in (attribution_predecessor_call, released_v1_call):
            self.assertEqual(driver(request), {"status": "refused", "code": "storage_schema_partial_upgrade"})
        self.assertEqual(self.snapshot(), partial)

    @unittest.skipUnless(os.environ.get("HORMUZ_TEST_PG_CONTAINER"), "requires disposable PostgreSQL matched backup tools")
    def test_postgres_outcome_old_pair_restore_preserves_both_replays_cursors_and_facts(self):
        backup = self.backup()
        self.upgrade()
        retained = self.snapshot()
        owner, runtime = self.restore(backup)
        request = {**self.predecessor_request, "runtime_dsn": runtime, "mode": "replay",
                   "registry_writes": self.seeded["registry_writes"], "attribution_write": self.seeded["attribution_write"]}
        replayed = attribution_predecessor_call(request)
        self.assertEqual(replayed["unknown_holds"], 1)
        self.assertEqual(replayed["registry_replays"], [write[3] for write in self.seeded["registry_writes"]])
        self.assertEqual(replayed["attribution_replay"], self.seeded["attribution_write"][2])
        self.assertEqual(self.snapshot(owner), self.before)
        for resource in ("registry", "attribution"):
            page = attribution_predecessor_call({**request, "mode": "page", "resource": resource,
                                                "cursor": self.seeded[resource + "_page"]["next_cursor"]})["page"]
            self.assertEqual(len(page["items"]), 1)
            self.assertIsNone(page["next_cursor"])
            if resource == "registry":
                self.assertEqual(page["items"], [self.seeded["registry_writes"][0][3][1]])
        facts = attribution_predecessor_call({**request, "mode": "facts", "attempt_id": self.seeded["attempt_id"]})["facts"]
        self.assertEqual(facts["provider_reported_model"], "recovery-actual-v1")
        self.assertEqual(facts["attribution"], self.seeded["attribution_write"][2][1])
        self.assertEqual(self.snapshot(), retained)

    @unittest.skipUnless(os.environ.get("HORMUZ_TEST_PG_CONTAINER"), "requires disposable PostgreSQL matched backup tools")
    def test_postgres_outcome_post_checkpoint_writes_require_forward_recovery(self):
        self.upgrade()
        seed_registry_ledger(self.runtime())
        config = replace(registry_config(Path("/unused/outcome-recovery")), usage_storage=UsageStorageConfig(
            backend="postgresql", postgres_schema=self.schema, postgres_runtime_role=self.runtime_role))
        seeded = seed_outcome_metadata(config, environ={"HORMUZ_POSTGRES_DSN": self.runtime_dsn})
        after_write = self.snapshot()
        self.assertTrue(all(after_write["rows"][table] for table in TABLE_DDL))
        owner, runtime = self.restore(self.backup())
        self.assertEqual(ledger_observation(self.runtime(runtime))["unknown_holds"], 2)
        self.assertEqual(self.snapshot(owner), after_write)
        receipts, retention = replay_outcome_metadata(config, seeded, environ={"HORMUZ_POSTGRES_DSN": runtime})
        self.assertEqual(receipts, [item["receipt"] for item in seeded["deliveries"]])
        self.assertEqual(retention, seeded["retention"])
        self.assertEqual(self.snapshot(owner), after_write)
        service = PortfolioService(config, create_portfolio_repository(config, environ={"HORMUZ_POSTGRES_DSN": runtime}))
        page = service.dispatch(ADMIN, "GET", OUTCOMES, query="cursor=" + seeded["page"]["next_cursor"])[1]
        self.assertEqual(page["as_of"], seeded["page"]["as_of"])
        self.assertEqual(len(page["items"]), 1)
        self.assertIsNone(page["next_cursor"])
        self.assertEqual(self.snapshot(), after_write)
        self.assertNotEqual(after_write, self.before)
        self.assertEqual(attribution_predecessor_call({**self.predecessor_request, "runtime_dsn": runtime, "mode": "verify"}),
                         {"status": "refused", "code": "storage_schema_newer_than_binary"})
