"""Red-first PostgreSQL 15-to-16 provider-collection transition proof."""

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
    from ._finance_collection_predecessor_fixture import (
        finance_collection_predecessor_call,
    )
    from ._finance_collection_transition_fixture import (
        seed_postgres_collection_predecessor,
    )
    from ._postgres_fixture import PostgresTestCase
else:
    from _finance_collection_predecessor_fixture import (
        finance_collection_predecessor_call,
    )
    from _finance_collection_transition_fixture import (
        seed_postgres_collection_predecessor,
    )
    from _postgres_fixture import PostgresTestCase


PLANNED_TABLES = (
    "portfolio_finance_source_binding_versions",
    "portfolio_finance_collection_attempts",
    "portfolio_finance_collection_events",
    "portfolio_finance_snapshots",
    "portfolio_finance_usage_observations",
    "portfolio_finance_cost_observations",
)


def _synthetic_collection_migration(quoted_schema: str, *, fail=False) -> str:
    statement = f"""
    CREATE TABLE {quoted_schema}.portfolio_finance_source_binding_versions (
        binding_id TEXT NOT NULL,
        version BIGINT NOT NULL,
        organization_id TEXT NOT NULL,
        PRIMARY KEY (binding_id, version)
    );
    CREATE TABLE {quoted_schema}.portfolio_finance_collection_attempts (
        attempt_id TEXT PRIMARY KEY,
        organization_id TEXT NOT NULL,
        binding_id TEXT NOT NULL,
        binding_version BIGINT NOT NULL,
        FOREIGN KEY (binding_id, binding_version)
            REFERENCES {quoted_schema}.portfolio_finance_source_binding_versions
                (binding_id, version)
    );
    CREATE TABLE {quoted_schema}.portfolio_finance_collection_events (
        event_id TEXT PRIMARY KEY,
        attempt_id TEXT NOT NULL UNIQUE,
        organization_id TEXT NOT NULL,
        state TEXT NOT NULL,
        FOREIGN KEY (attempt_id)
            REFERENCES {quoted_schema}.portfolio_finance_collection_attempts
                (attempt_id)
    );
    CREATE TABLE {quoted_schema}.portfolio_finance_snapshots (
        snapshot_id TEXT PRIMARY KEY,
        attempt_id TEXT NOT NULL UNIQUE,
        organization_id TEXT NOT NULL,
        content_digest TEXT NOT NULL,
        FOREIGN KEY (attempt_id)
            REFERENCES {quoted_schema}.portfolio_finance_collection_attempts
                (attempt_id)
    );
    CREATE TABLE {quoted_schema}.portfolio_finance_usage_observations (
        observation_id TEXT PRIMARY KEY,
        snapshot_id TEXT NOT NULL,
        organization_id TEXT NOT NULL,
        FOREIGN KEY (snapshot_id)
            REFERENCES {quoted_schema}.portfolio_finance_snapshots (snapshot_id)
    );
    CREATE TABLE {quoted_schema}.portfolio_finance_cost_observations (
        observation_id TEXT PRIMARY KEY,
        snapshot_id TEXT NOT NULL,
        organization_id TEXT NOT NULL,
        FOREIGN KEY (snapshot_id)
            REFERENCES {quoted_schema}.portfolio_finance_snapshots (snapshot_id)
    );
    CREATE INDEX finance_collection_audit_source_probe
        ON {quoted_schema}.gateway_audit_chain_entries
            (source_schema_id, source_schema_version, source_event_id);
    """
    return statement + (" SELECT 1 / 0;" if fail else "")


@unittest.skipUnless(
    os.environ.get("HORMUZ_TEST_POSTGRES_DSN"),
    "requires disposable PostgreSQL",
)
class PostgresFinanceCollectionTransitionTests(PostgresTestCase):
    def migrate(self):
        return migrate_postgres(
            self.owner_dsn,
            schema=self.schema,
            runtime_role=self.runtime_role,
            policy_control_role=self.policy_control_role,
            custody_control_role=self.custody_control_role,
            custody_executor_role=self.custody_executor_role,
        )

    def runtime(self, dsn=None):
        return PostgresUsageStore(
            dsn or self.runtime_dsn,
            schema=self.schema,
            runtime_role=self.runtime_role,
            organization_ids=("acme", "beta"),
        )

    def setUp(self):
        self.assertEqual(postgres_module.POSTGRES_SCHEMA_VERSION, 15)
        self._drop_schema(self.schema)
        self.seeded = seed_postgres_collection_predecessor(
            owner_dsn=self.owner_dsn,
            runtime_dsn=self.runtime_dsn,
            schema=self.schema,
            runtime_role=self.runtime_role,
            policy_control_role=self.policy_control_role,
            custody_control_role=self.custody_control_role,
            custody_executor_role=self.custody_executor_role,
        )
        self.before = self.snapshot()

    def request(self, runtime_dsn=None):
        return {
            "backend": "postgresql",
            "runtime_dsn": runtime_dsn or self.runtime_dsn,
            "schema": self.schema,
            "runtime_role": self.runtime_role,
        }

    def snapshot(self, dsn=None):
        with self.psycopg.connect(dsn or self.owner_dsn) as connection:
            tables = connection.execute(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname=%s ORDER BY tablename",
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
                "SELECT c.relname,c.relkind,c.relrowsecurity,"
                "c.relforcerowsecurity,c.relacl::text "
                "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname=%s ORDER BY c.relname",
                (self.schema,),
            ).fetchall()
        return {"rows": rows, "shape": shape}

    def probe(self, *, fail=False, dsn=None):
        original = postgres_module._migration_sql

        def migration(version, schema, *roles):
            if version == 16:
                return _synthetic_collection_migration(schema, fail=fail)
            return original(version, schema, *roles)

        original_dsn = self.owner_dsn
        if dsn is not None:
            self.owner_dsn = dsn
        try:
            with (
                mock.patch.object(postgres_module, "POSTGRES_SCHEMA_VERSION", 16),
                mock.patch.object(
                    postgres_module,
                    "_migration_sql",
                    side_effect=migration,
                ),
            ):
                self.assertEqual(self.migrate().version, 16)
        finally:
            self.owner_dsn = original_dsn

    def test_predecessor_has_every_accepted_populated_domain(self):
        rows = self.before["rows"]
        for table in (
            "portfolio_work_scope_versions",
            "portfolio_attribution_events",
            "portfolio_outcome_events",
            "portfolio_finance_rate_cards",
            "portfolio_work_budget_plan_versions",
            "gateway_provider_attempt_metrics",
            "gateway_finance_attempt_evidence",
            "gateway_audit_chain_entries",
        ):
            with self.subTest(table=table):
                self.assertIn(table, rows)
                self.assertTrue(rows[table])
        self.assertEqual(self.seeded["budget"]["active_version"], 1)
        self.assertEqual(self.seeded["outcome_delivery_count"], 3)

    def test_missing_migration_is_red_without_partial_state(self):
        with mock.patch.object(postgres_module, "POSTGRES_SCHEMA_VERSION", 16):
            with self.assertRaises(PostgresStorageError) as caught:
                self.migrate()
        self.assertEqual(caught.exception.code, "storage_schema_migration_unsupported")
        self.assertEqual(self.snapshot(), self.before)

    def test_synthetic_ddl_failure_rolls_back_and_retry_is_idempotent(self):
        with self.assertRaises(PostgresStorageError) as caught:
            self.probe(fail=True)
        self.assertEqual(caught.exception.code, "storage_unavailable")
        self.assertEqual(self.snapshot(), self.before)
        self.probe()
        after = self.snapshot()
        for table in PLANNED_TABLES:
            self.assertEqual(after["rows"][table], [])
        self.probe()
        self.assertEqual(self.snapshot(), after)

    def test_current_binary_refuses_newer_and_partial_state_without_repair(self):
        self.probe()
        candidate = self.snapshot()
        with self.assertRaises(PostgresStorageError) as caught:
            self.runtime()
        self.assertEqual(caught.exception.code, "storage_schema_newer_than_binary")
        self.assertEqual(self.snapshot(), candidate)

        with self.psycopg.connect(self.owner_dsn) as connection:
            connection.execute(
                self.sql.SQL(
                    "UPDATE {}.hormuz_schema_migrations "
                    "SET state='applying' WHERE version=16"
                ).format(self.sql.Identifier(self.schema))
            )
        partial = self.snapshot()
        with self.assertRaises(PostgresStorageError) as caught:
            self.runtime()
        self.assertEqual(caught.exception.code, "storage_schema_partial_upgrade")
        self.assertEqual(self.snapshot(), partial)

    @unittest.skipUnless(
        os.environ.get("HORMUZ_TEST_FINANCE_COLLECTION_PYTHON"),
        "requires digest-pinned accepted native-finance predecessor",
    )
    def test_exact_predecessor_accepts_current_and_refuses_newer_and_partial(self):
        self.assertEqual(
            finance_collection_predecessor_call(self.request()),
            {"status": "ready", "runtime_files_verified": 144},
        )
        self.probe()
        self.assertEqual(
            finance_collection_predecessor_call(self.request()),
            {"status": "refused", "code": "storage_schema_newer_than_binary"},
        )
        with self.psycopg.connect(self.owner_dsn) as connection:
            connection.execute(
                self.sql.SQL(
                    "UPDATE {}.hormuz_schema_migrations "
                    "SET state='applying' WHERE version=16"
                ).format(self.sql.Identifier(self.schema))
            )
        self.assertEqual(
            finance_collection_predecessor_call(self.request()),
            {"status": "refused", "code": "storage_schema_partial_upgrade"},
        )

    def backup(self):
        info = self.psycopg.conninfo.conninfo_to_dict(self.owner_dsn)
        result = subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                os.environ["HORMUZ_TEST_PG_CONTAINER"],
                "pg_dump",
                "-U",
                info["user"],
                "-d",
                info["dbname"],
                "--schema",
                self.schema,
                "--format=custom",
            ],
            capture_output=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, "test_pg_dump_failed")
        self.assertTrue(result.stdout.startswith(b"PGDMP"))
        return result.stdout

    def restore(self, backup):
        database = "finance_collection_restore_" + uuid4().hex[:12]
        with self.psycopg.connect(
            self.owner_dsn,
            autocommit=True,
        ) as connection:
            connection.execute(
                self.sql.SQL("CREATE DATABASE {}").format(
                    self.sql.Identifier(database)
                )
            )

        def cleanup():
            with self.psycopg.connect(
                self.owner_dsn,
                autocommit=True,
            ) as connection:
                connection.execute(
                    self.sql.SQL("DROP DATABASE {}").format(
                        self.sql.Identifier(database)
                    )
                )

        self.addCleanup(cleanup)
        info = self.psycopg.conninfo.conninfo_to_dict(self.owner_dsn)
        digest = hashlib.sha256(backup).hexdigest()
        result = subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                os.environ["HORMUZ_TEST_PG_CONTAINER"],
                "pg_restore",
                "-U",
                info["user"],
                "-d",
                database,
                "--no-owner",
                "--exit-on-error",
            ],
            input=backup,
            capture_output=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, "test_pg_restore_failed")
        self.assertEqual(hashlib.sha256(backup).hexdigest(), digest)
        return (
            self.psycopg.conninfo.make_conninfo(self.owner_dsn, dbname=database),
            self.psycopg.conninfo.make_conninfo(self.runtime_dsn, dbname=database),
        )

    @unittest.skipUnless(
        os.environ.get("HORMUZ_TEST_PG_CONTAINER"),
        "requires matched disposable backup tools",
    )
    def test_quiesced_old_pair_restore_preserves_every_accepted_byte(self):
        backup = self.backup()
        self.probe()
        retained = self.snapshot()
        owner, runtime = self.restore(backup)
        PostgresUsageStore(
            runtime,
            schema=self.schema,
            runtime_role=self.runtime_role,
            organization_ids=("acme", "beta"),
        ).verify_ready()
        self.assertEqual(self.snapshot(owner), self.before)
        self.assertEqual(self.snapshot(), retained)

    @unittest.skipUnless(
        os.environ.get("HORMUZ_TEST_PG_CONTAINER"),
        "requires matched disposable backup tools",
    )
    def test_post_checkpoint_write_requires_retained_forward_recovery(self):
        self.probe()
        with self.psycopg.connect(self.owner_dsn) as connection:
            connection.execute(
                self.sql.SQL(
                    "INSERT INTO {}.portfolio_finance_source_binding_versions "
                    "(binding_id, version, organization_id) VALUES (%s, %s, %s)"
                ).format(self.sql.Identifier(self.schema)),
                ("synthetic-binding", 1, "acme"),
            )
        after_write = self.snapshot()
        backup = self.backup()
        owner, runtime = self.restore(backup)
        self.probe(dsn=owner)
        self.assertEqual(self.snapshot(owner), after_write)
        with self.assertRaises(PostgresStorageError) as caught:
            PostgresUsageStore(
                runtime,
                schema=self.schema,
                runtime_role=self.runtime_role,
                organization_ids=("acme", "beta"),
            )
        self.assertEqual(caught.exception.code, "storage_schema_newer_than_binary")


if __name__ == "__main__":
    unittest.main()
