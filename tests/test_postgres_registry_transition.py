"""Registry preflight against disposable PostgreSQL; probe DDL is not #215."""

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
from hormuz.contracts import contract_manifest
from hormuz.postgres import PostgresStorageError, migrate_postgres
from hormuz.postgres_usage_store import PostgresUsageStore
if __package__:
    from ._postgres_fixture import PostgresTestCase
    from ._registry_transition_fixture import PROBE_TABLE, ledger_observation, released_v1_call, seed_registry_ledger
else:
    from _postgres_fixture import PostgresTestCase
    from _registry_transition_fixture import PROBE_TABLE, ledger_observation, released_v1_call, seed_registry_ledger


class PostgresRegistryTransitionTests(PostgresTestCase):
    def runtime(self):
        return PostgresUsageStore(
            self.runtime_dsn, schema=self.schema, runtime_role=self.runtime_role,
            organization_ids=("acme", "beta"),
        )

    def migrate(self):
        return migrate_postgres(
            self.owner_dsn, schema=self.schema, runtime_role=self.runtime_role,
            policy_control_role=self.policy_control_role, custody_control_role=self.custody_control_role,
            custody_executor_role=self.custody_executor_role,
        )

    def setUp(self) -> None:
        # The shared fixture owns this uniquely named schema and its four roles.
        # Each case needs a fresh v8 ledger, not the previous case's v9 probe.
        self._drop_schema(self.schema)
        self.migrate()
        super().setUp()
        seed_registry_ledger(self.store)
        self.before = self.snapshot()
        self.assertEqual(len(self.before["rows"]), 32)

    def snapshot(self, *, dsn=None):
        with self.psycopg.connect(dsn or self.owner_dsn) as connection:
            tables = connection.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = %s ORDER BY tablename", (self.schema,),
            ).fetchall()
            rows = {}
            for (table,) in tables:
                rows[table] = connection.execute(self.sql.SQL(
                    "SELECT row_to_json(t)::text FROM {}.{} AS t ORDER BY row_to_json(t)::text"
                ).format(self.sql.Identifier(self.schema), self.sql.Identifier(table))).fetchall()
            shape = connection.execute(
                "SELECT c.relname, c.relkind, c.relrowsecurity, c.relforcerowsecurity, c.relacl::text "
                "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = %s ORDER BY c.relname", (self.schema,),
            ).fetchall()
        return {"rows": rows, "shape": shape}

    def probe(self, *, fail=False):
        def migration(version, schema, *roles):
            self.assertEqual(version, 9)
            ddl = f"CREATE TABLE {schema}.{PROBE_TABLE} (id INTEGER PRIMARY KEY); "
            ddl += f"INSERT INTO {schema}.{PROBE_TABLE} VALUES (1);"
            if fail:
                ddl += " SELECT 1 / 0;"
            return ddl

        with (
            mock.patch.object(postgres_module, "POSTGRES_SCHEMA_VERSION", 9),
            mock.patch.object(postgres_module, "_migration_sql", side_effect=migration),
        ):
            self.assertEqual(self.migrate().version, 9)

    def assert_v1_preserved(self):
        after = copy.deepcopy(self.snapshot())
        after["rows"].pop(PROBE_TABLE, None)
        after["rows"]["hormuz_schema_migrations"] = [
            row for row in after["rows"]["hormuz_schema_migrations"] if json.loads(row[0])["version"] != 9
        ]
        after["shape"] = [row for row in after["shape"] if row[0] not in {PROBE_TABLE, f"{PROBE_TABLE}_pkey"}]
        self.assertEqual(after, self.before)

    def request(self, mode):
        return {
            "backend": "postgresql", "mode": mode, "schema": self.schema,
            "owner_dsn": self.owner_dsn, "runtime_dsn": self.runtime_dsn,
            "runtime_role": self.runtime_role, "policy_control_role": self.policy_control_role,
            "custody_control_role": self.custody_control_role, "custody_executor_role": self.custody_executor_role,
        }

    def test_registry_postgres_migration_is_red_until_feature_implementation(self) -> None:
        self.assertEqual(postgres_module.POSTGRES_SCHEMA_VERSION, 8)
        for _ in range(2):
            with mock.patch.object(postgres_module, "POSTGRES_SCHEMA_VERSION", 9):
                with self.assertRaises(PostgresStorageError) as raised:
                    self.migrate()
            self.assertEqual(raised.exception.code, "storage_schema_migration_unsupported")
            self.assertEqual(self.snapshot(), self.before)

    def test_postgres_probe_failure_rolls_back_and_retry_preserves_v1_rows(self) -> None:
        with self.assertRaises(PostgresStorageError) as raised:
            self.probe(fail=True)
        self.assertEqual(raised.exception.code, "storage_unavailable")
        self.assertEqual(self.snapshot(), self.before)
        self.probe()
        after = self.snapshot()
        self.assertEqual(after["rows"][PROBE_TABLE], [('{"id":1}',)])
        self.probe()
        self.assertEqual(self.snapshot(), after)
        self.assert_v1_preserved()

    def test_postgres_partial_upgrade_refuses_migration_and_runtime_without_changes(self) -> None:
        with self.psycopg.connect(self.owner_dsn) as connection:
            connection.execute(self.sql.SQL(
                "INSERT INTO {}.hormuz_schema_migrations (version, state) VALUES (9, 'applying')"
            ).format(self.sql.Identifier(self.schema)))
        before = self.snapshot()
        # Readiness is a tenant-access/privilege probe, not a repeated schema
        # check. Existing instances MUST be stopped before operator migration.
        self.store.verify_ready()
        self.assertEqual(self.snapshot(), before)
        for operation in (self.migrate, self.runtime):
            with self.assertRaises(PostgresStorageError) as raised:
                operation()
            self.assertEqual(raised.exception.code, "storage_schema_partial_upgrade")
            self.assertEqual(self.snapshot(), before)

    @unittest.skipUnless(os.environ.get("HORMUZ_TEST_V1_PYTHON"), "requires the digest-pinned released v1 interpreter")
    def test_released_postgres_binary_preserves_old_state_and_refuses_newer_or_partial_state(self) -> None:
        self._drop_schema(self.schema)
        seeded = released_v1_call(self.request("seed"))
        self.assertEqual(seeded["status"], "ready")
        self.assertEqual(seeded["manifest"], contract_manifest())
        self.assertEqual(seeded["unknown_holds"], 1)
        self.before = self.snapshot()
        self.migrate()
        self.assertEqual(ledger_observation(self.store), {
            key: seeded[key] for key in ("unknown_holds", "audit_sequence", "usage_events")
        })
        self.assertEqual(self.snapshot(), self.before)
        self.probe()
        for state, expected in (("applied", "storage_schema_newer_than_binary"), ("applying", "storage_schema_partial_upgrade")):
            with self.psycopg.connect(self.owner_dsn) as connection:
                connection.execute(self.sql.SQL(
                    "UPDATE {}.hormuz_schema_migrations SET state = %s WHERE version = 9"
                ).format(self.sql.Identifier(self.schema)), (state,))
            before = self.snapshot()
            self.assertEqual(released_v1_call(self.request("verify")), {"status": "refused", "code": expected})
            self.assertEqual(self.snapshot(), before)
            self.assert_v1_preserved()

    @unittest.skipUnless(
        os.environ.get("HORMUZ_TEST_V1_PYTHON") and os.environ.get("HORMUZ_TEST_PG_CONTAINER"),
        "requires the released v1 interpreter and a disposable PostgreSQL container for matched backup tools",
    )
    def test_postgres_quiesced_verified_pair_restore_keeps_unknown_holds(self) -> None:
        connection_info = self.psycopg.conninfo.conninfo_to_dict(self.owner_dsn)
        prefix = ["docker", "exec", "-i", os.environ["HORMUZ_TEST_PG_CONTAINER"]]
        dumped = subprocess.run(
            [*prefix, "pg_dump", "-U", connection_info["user"], "-d", connection_info["dbname"],
             "--schema", self.schema, "--format=custom"], capture_output=True, timeout=60,
        )
        self.assertEqual(dumped.returncode, 0, "test_pg_dump_failed")
        backup = dumped.stdout
        self.assertTrue(backup.startswith(b"PGDMP"))
        digest = hashlib.sha256(backup).hexdigest()
        self.probe()
        candidate = self.snapshot()
        database = f"registry_restore_{uuid4().hex[:12]}"
        with self.psycopg.connect(self.owner_dsn, autocommit=True) as connection:
            connection.execute(self.sql.SQL("CREATE DATABASE {}").format(self.sql.Identifier(database)))

        def cleanup():
            with self.psycopg.connect(self.owner_dsn, autocommit=True) as connection:
                connection.execute(self.sql.SQL("DROP DATABASE {}").format(self.sql.Identifier(database)))

        self.addCleanup(cleanup)
        self.assertEqual(hashlib.sha256(backup).hexdigest(), digest)
        restored = subprocess.run(
            [*prefix, "pg_restore", "-U", connection_info["user"], "-d", database,
             "--no-owner", "--exit-on-error"], input=backup, capture_output=True, timeout=60,
        )
        self.assertEqual(restored.returncode, 0, "test_pg_restore_failed")
        restored_owner = self.psycopg.conninfo.make_conninfo(self.owner_dsn, dbname=database)
        restored_runtime = self.psycopg.conninfo.make_conninfo(self.runtime_dsn, dbname=database)
        self.assertEqual(self.snapshot(dsn=restored_owner), self.before)
        observed = released_v1_call({**self.request("verify"), "runtime_dsn": restored_runtime})
        self.assertEqual(observed["status"], "ready")
        self.assertEqual(observed["unknown_holds"], 1)
        self.assertEqual(self.snapshot(dsn=restored_owner), self.before)
        # Original candidate remains available; no destructive in-place restore.
        self.assertEqual(self.snapshot(), candidate)

    def test_postgres_candidate_writes_remain_present_for_forward_recovery(self) -> None:
        self.probe()
        with mock.patch.object(postgres_module, "POSTGRES_SCHEMA_VERSION", 9):
            candidate = PostgresUsageStore(
                self.runtime_dsn, schema=self.schema, runtime_role=self.runtime_role,
                organization_ids=("acme", "beta"),
            )
            seed_registry_ledger(candidate)
            self.assertEqual(ledger_observation(candidate)["unknown_holds"], 2)
            after_write = self.snapshot()
            self.probe()
            self.assertEqual(self.snapshot(), after_write)
        self.store.verify_ready()  # Existing readiness alone is insufficient.
        with self.assertRaises(PostgresStorageError) as raised:
            self.runtime()
        self.assertEqual(raised.exception.code, "storage_schema_newer_than_binary")
        self.assertEqual(self.snapshot(), after_write)
        self.assertNotEqual(after_write["rows"]["gateway_usage_events"], self.before["rows"]["gateway_usage_events"])


if __name__ == "__main__":
    unittest.main()
