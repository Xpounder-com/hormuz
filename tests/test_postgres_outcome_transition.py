"""Owned PostgreSQL preflight probes, actual predecessors, isolated restores."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import unittest
from unittest import mock
from uuid import uuid4

import hormuz.postgres as postgres_module
from hormuz.postgres import PostgresStorageError, migrate_postgres
from hormuz.postgres_usage_store import PostgresUsageStore

if __package__:
    from ._outcome_predecessor_fixture import attribution_predecessor_call
    from ._postgres_fixture import PostgresTestCase
    from ._registry_transition_fixture import ledger_observation, released_v1_call, seed_registry_ledger
else:
    from _outcome_predecessor_fixture import attribution_predecessor_call
    from _postgres_fixture import PostgresTestCase
    from _registry_transition_fixture import ledger_observation, released_v1_call, seed_registry_ledger


PROBE = "outcome_transition_test_probe"


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
        self.assertEqual(postgres_module.POSTGRES_SCHEMA_VERSION, 10)
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

    def probe(self, *, fail=False):
        original = postgres_module._migration_sql

        def migration(version, schema, *roles):
            if version != 11:
                return original(version, schema, *roles)
            ddl = f"CREATE TABLE {schema}.{PROBE} (organization_id TEXT NOT NULL, id TEXT PRIMARY KEY);"
            return ddl + (" SELECT 1 / 0;" if fail else "")

        with mock.patch.object(postgres_module, "POSTGRES_SCHEMA_VERSION", 11), mock.patch.object(postgres_module, "_migration_sql", side_effect=migration):
            self.assertEqual(self.migrate().version, 11)

    def assert_prior_state_preserved(self):
        current = copy.deepcopy(self.snapshot())
        current["rows"].pop(PROBE, None)
        current["shape"] = [row for row in current["shape"] if not row[0].startswith(PROBE)]
        current["rows"]["hormuz_schema_migrations"] = [row for row in current["rows"]["hormuz_schema_migrations"] if json.loads(row[0])["version"] != 11]
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

    def test_postgres_outcome_migration_is_red_until_implementation(self):
        with mock.patch.object(postgres_module, "POSTGRES_SCHEMA_VERSION", 11):
            with self.assertRaises(PostgresStorageError) as caught:
                self.migrate()
        self.assertEqual(caught.exception.code, "storage_schema_migration_unsupported")
        self.assertEqual(self.snapshot(), self.before)

    def test_postgres_outcome_probe_failure_retry_preserves_all_predecessor_state(self):
        with self.assertRaises(PostgresStorageError) as caught:
            self.probe(fail=True)
        self.assertEqual(caught.exception.code, "storage_unavailable")
        self.assertEqual(self.snapshot(), self.before)
        self.probe()
        self.assert_prior_state_preserved()
        current = self.snapshot()
        self.probe()
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
        self.probe()
        before = self.snapshot()
        with self.assertRaises(PostgresStorageError) as caught:
            self.runtime()
        self.assertEqual(caught.exception.code, "storage_schema_newer_than_binary")
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
        self.probe()
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
        self.probe()
        with mock.patch.object(postgres_module, "POSTGRES_SCHEMA_VERSION", 11):
            seed_registry_ledger(self.runtime())
            with self.psycopg.connect(self.owner_dsn) as connection:
                connection.execute(self.sql.SQL("INSERT INTO {}.{} VALUES ('acme', 'synthetic-post-checkpoint-write')").format(self.sql.Identifier(self.schema), self.sql.Identifier(PROBE)))
            after_write = self.snapshot()
            owner, runtime = self.restore(self.backup())
            self.assertEqual(ledger_observation(self.runtime(runtime))["unknown_holds"], 2)
            self.assertEqual(self.snapshot(owner), after_write)
        self.assertEqual(self.snapshot(), after_write)
        self.assertNotEqual(after_write, self.before)
        self.assertEqual(attribution_predecessor_call({**self.predecessor_request, "runtime_dsn": runtime, "mode": "verify"}),
                         {"status": "refused", "code": "storage_schema_newer_than_binary"})
