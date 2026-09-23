"""SQLite 12 to 13 and PostgreSQL 17 to 18 account-binding transitions."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock
from uuid import uuid4

import hormuz.postgres as postgres_module
import hormuz._portfolio_sql as portfolio_sql_module
from hormuz.postgres import PostgresStorageError
from hormuz.store import StorageSchemaError, UsageStore

if __package__:
    from ._finance_collection_transition_fixture import (
        seed_sqlite_collection_predecessor, seed_postgres_collection_predecessor,
    )
    from ._registry_transition_fixture import sqlite_snapshot
    from ._registry_transition_fixture import sqlite_backup
    from ._sqlite import managed_sqlite_connection
    from ._finance_account_binding_predecessor_fixture import predecessor_call
    from ._postgres_fixture import PostgresTestCase
    from . import test_postgres_finance_collection_transition as previous
else:
    from _finance_collection_transition_fixture import (
        seed_sqlite_collection_predecessor, seed_postgres_collection_predecessor,
    )
    from _registry_transition_fixture import sqlite_snapshot
    from _registry_transition_fixture import sqlite_backup
    from _sqlite import managed_sqlite_connection
    from _finance_account_binding_predecessor_fixture import predecessor_call
    from _postgres_fixture import PostgresTestCase
    import test_postgres_finance_collection_transition as previous


class SQLiteFinanceAccountBindingTransitionTests(unittest.TestCase):
    def test_real_successor_preserves_populated_predecessor_and_starts_empty(self):
        self.assertEqual(UsageStore.schema_version, 13)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "synthetic.sqlite3"
            with (
                mock.patch.object(UsageStore, "schema_version", 12),
                mock.patch.object(portfolio_sql_module, "SQLITE_SCHEMA_VERSION", 12),
            ):
                seed_sqlite_collection_predecessor(path)
            before = sqlite_snapshot(path)
            for table in ("gateway_finance_attempt_evidence", "gateway_audit_chain_entries",
                          "portfolio_work_budget_plan_versions", "gateway_provider_attempt_metrics"):
                self.assertTrue(before["rows"][table], table)
            UsageStore(path).verify_ready()
            after = sqlite_snapshot(path)
            for table, rows in before["rows"].items():
                if table == "hormuz_schema_migrations":
                    self.assertEqual(
                        [row for row in after["rows"][table] if row[0] != 13],
                        rows,
                    )
                else:
                    self.assertEqual(after["rows"][table], rows, table)
            for table in PROBE_TABLES:
                self.assertEqual(after["rows"][table], [])
            self.assertIn(
                (13, "applied"),
                {
                    (row[0], row[1])
                    for row in after["rows"]["hormuz_schema_migrations"]
                },
            )
            UsageStore(path, read_only=True).verify_ready()
            with mock.patch.object(UsageStore, "schema_version", 12):
                with self.assertRaisesRegex(
                    StorageSchemaError,
                    "storage_schema_newer_than_binary",
                ):
                    UsageStore(path, read_only=True)

    def test_runtime_verifier_rejects_tampered_account_audit_source_guard(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "synthetic.sqlite3"
            UsageStore(path)
            with managed_sqlite_connection(path) as connection:
                connection.execute(
                    "DROP TRIGGER gateway_finance_account_audit_source_required"
                )
                connection.execute(
                    "CREATE TRIGGER gateway_finance_account_audit_source_required "
                    "BEFORE INSERT ON gateway_audit_chain_entries BEGIN SELECT 1; END"
                )
            with self.assertRaisesRegex(
                StorageSchemaError,
                "storage_schema_partial_upgrade",
            ):
                UsageStore(path, read_only=True)


@unittest.skipUnless(os.environ.get("HORMUZ_TEST_POSTGRES_DSN"), "requires disposable PostgreSQL")
class PostgresFinanceAccountBindingTransitionTests(PostgresTestCase):
    migrate = previous.PostgresFinanceCollectionTransitionTests.migrate
    snapshot = previous.PostgresFinanceCollectionTransitionTests.snapshot
    runtime = previous.PostgresFinanceCollectionTransitionTests.runtime

    def test_real_successor_preserves_populated_predecessor_and_starts_empty(self):
        self.assertEqual(postgres_module.POSTGRES_SCHEMA_VERSION, 18)
        self._drop_schema(self.schema)
        seed_postgres_collection_predecessor(
            owner_dsn=self.owner_dsn, runtime_dsn=self.runtime_dsn, schema=self.schema,
            runtime_role=self.runtime_role, policy_control_role=self.policy_control_role,
            custody_control_role=self.custody_control_role,
            custody_executor_role=self.custody_executor_role,
        )
        with mock.patch.object(postgres_module, "POSTGRES_SCHEMA_VERSION", 17):
            self.assertEqual(self.migrate().version, 17)
        before = self.snapshot()
        for table in ("gateway_finance_attempt_evidence", "gateway_audit_chain_entries",
                      "portfolio_work_budget_plan_versions", "gateway_provider_attempt_metrics"):
            self.assertTrue(before["rows"][table], table)
        self.assertEqual(self.migrate().version, 18)
        after = self.snapshot()
        for table, rows in before["rows"].items():
            if table == "hormuz_schema_migrations":
                continue
            self.assertEqual(after["rows"][table], rows, table)
        for table in PROBE_TABLES:
            self.assertEqual(after["rows"][table], [])
        self.runtime().verify_ready()
        with mock.patch.object(postgres_module, "POSTGRES_SCHEMA_VERSION", 17):
            with self.assertRaisesRegex(
                PostgresStorageError,
                "storage_schema_newer_than_binary",
            ):
                self.runtime()

    def test_runtime_verifier_rejects_missing_account_consistency_trigger(self):
        drop = self.sql.SQL(
            "DROP TRIGGER portfolio_finance_account_binding_source_consistency "
            "ON {}.portfolio_finance_account_binding_versions"
        ).format(self.sql.Identifier(self.schema))
        restore = self.sql.SQL(
            "CREATE TRIGGER portfolio_finance_account_binding_source_consistency "
            "BEFORE INSERT ON {}.portfolio_finance_account_binding_versions "
            "FOR EACH ROW EXECUTE FUNCTION {}.enforce_finance_account_binding_source()"
        ).format(self.sql.Identifier(self.schema), self.sql.Identifier(self.schema))
        with self.psycopg.connect(self.owner_dsn) as connection:
            connection.execute(drop)
        try:
            with self.assertRaisesRegex(
                PostgresStorageError,
                "storage_schema_partial_upgrade",
            ):
                self.runtime()
        finally:
            with self.psycopg.connect(self.owner_dsn) as connection:
                connection.execute(restore)

    def test_runtime_verifier_rejects_weakened_account_audit_guard(self):
        with self.psycopg.connect(self.owner_dsn) as connection:
            original = connection.execute(
                "SELECT pg_get_functiondef(p.oid) FROM pg_proc p "
                "JOIN pg_namespace n ON n.oid=p.pronamespace "
                "WHERE n.nspname=%s "
                "AND p.proname='enforce_custody_audit_chain_entry_insert'",
                (self.schema,),
            ).fetchone()[0]
            weakened = self.sql.SQL(
                "CREATE OR REPLACE FUNCTION {}.enforce_custody_audit_chain_entry_insert() "
                "RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog "
                "AS $$ BEGIN "
                "PERFORM 'hormuz.finance-account-binding-version'; "
                "PERFORM 'hormuz.finance-account-binding-version'; "
                "PERFORM 'hormuz.finance-attempt-account-binding'; "
                "PERFORM 'hormuz.finance-attempt-account-binding'; "
                "PERFORM 'hormuz.finance-query-audit-event'; "
                "PERFORM 'hormuz.finance-query-audit-event'; "
                "RETURN NEW; END; $$"
            ).format(self.sql.Identifier(self.schema))
            connection.execute(weakened)
        try:
            with self.assertRaisesRegex(
                PostgresStorageError,
                "storage_schema_partial_upgrade",
            ):
                self.runtime()
        finally:
            with self.psycopg.connect(self.owner_dsn) as connection:
                connection.execute(original)


# These names determine the implemented six-grant ACL surface.
PROBE_TABLES = (
    "portfolio_finance_account_binding_versions",
    "gateway_finance_attempt_account_bindings",
    "portfolio_finance_query_audit_events",
)
POPULATED_TABLES = (
    "gateway_usage_events", "gateway_request_attempts", "gateway_budget_reservations",
    "gateway_audit_chain_entries", "portfolio_work_scope_versions",
    "portfolio_attribution_events", "portfolio_outcome_events", "portfolio_finance_rate_cards",
    "portfolio_work_budget_plan_versions", "gateway_provider_attempt_metrics",
    "gateway_finance_attempt_evidence", "portfolio_finance_source_binding_versions",
    "portfolio_finance_collection_attempts", "portfolio_finance_collection_events",
    "portfolio_finance_snapshots", "portfolio_finance_snapshot_bucket_coverage",
    "portfolio_finance_usage_observations", "portfolio_finance_cost_observations",
)
BASELINE_READY = {"status": "ready", "runtime_files_verified": 161}
# Measured twice in independent disposable managed-role PostgreSQL bootstraps;
# never derived as expectations from the database under test.
PROPOSED_ACL = (205, "56dd1434aaf078fdcdb5ed38059ed9c0f4a57f82310dc4fdfa18dff650628e9f")
INJECTED_ACL = (206, "5946c0c2303c3c824223a6558e6547dce0c5f282d10f45852d0356bbc3cc74ef")
PINNED = bool(os.environ.get("HORMUZ_TEST_ACCOUNT_BINDING_PYTHON")
              and os.environ.get("HORMUZ_TEST_ACCOUNT_BINDING_SOURCE"))


@contextmanager
def sqlite_witness(*, fail=False):
    original = UsageStore._apply_migration

    def migration(connection, version):
        if version != 13:
            return original(connection, version)
        for table in PROBE_TABLES:
            connection.execute(f"CREATE TABLE {table} (organization_id TEXT NOT NULL, "
                               "event_id TEXT PRIMARY KEY, evidence_json TEXT NOT NULL)")
        if fail:
            connection.execute("INSERT INTO deliberately_absent_account_binding_probe VALUES (1)")

    with mock.patch.object(UsageStore, "schema_version", 13), mock.patch.object(
        UsageStore, "_apply_migration", side_effect=migration
    ):
        yield


@unittest.skipUnless(PINNED, "requires digest-verified published v1.2.0 wheel and source")
class SQLitePublishedAccountBindingPreflightTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "predecessor.sqlite3"
        self.seeded = predecessor_call(self.request(mode="seed"))
        self.assertEqual(self.seeded["status"], "ready")
        self.before = sqlite_snapshot(self.path)

    def request(self, path=None, mode="ready"):
        return {"backend": "sqlite", "path": str(path or self.path), "mode": mode}

    def assert_old_rows(self, after):
        for table, rows in self.before["rows"].items():
            if table == "hormuz_schema_migrations":
                self.assertEqual([row for row in after["rows"][table] if row[0] != 13], rows)
            else:
                self.assertEqual(after["rows"][table], rows, table)
        for definition in self.before["objects"]:
            self.assertIn(definition, after["objects"])

    def test_published_predecessor_populates_finance_and_replays_original_receipts(self):
        for table in POPULATED_TABLES:
            self.assertTrue(self.before["rows"][table], table)
        self.assertEqual(predecessor_call(self.request(mode="replay")), self.seeded)
        self.assertEqual(sqlite_snapshot(self.path), self.before)
        UsageStore(self.path, read_only=True).verify_ready()

    def test_real_missing_successor_preserves_published_predecessor(self):
        with mock.patch.object(UsageStore, "schema_version", 13):
            with self.assertRaisesRegex(StorageSchemaError, "storage_schema_migration_unsupported"):
                UsageStore(self.path)
        self.assertEqual(sqlite_snapshot(self.path), self.before)
        self.assertEqual(predecessor_call(self.request()), BASELINE_READY)

    def test_test_only_ddl_failure_rolls_back_and_retry_is_idempotent(self):
        with sqlite_witness(fail=True), self.assertRaises(sqlite3.OperationalError):
            UsageStore(self.path)
        self.assertEqual(sqlite_snapshot(self.path), self.before)
        with sqlite_witness():
            UsageStore(self.path)
            after = sqlite_snapshot(self.path)
            self.assert_old_rows(after)
            self.assertTrue(all(after["rows"][table] == [] for table in PROBE_TABLES))
            UsageStore(self.path)
            self.assertEqual(sqlite_snapshot(self.path), after)

    def test_published_and_current_binaries_refuse_partial_and_newer_without_repair(self):
        with sqlite_witness():
            UsageStore(self.path)
        for state, code in (("applied", "storage_schema_newer_than_binary"),
                            ("applying", "storage_schema_partial_upgrade")):
            with managed_sqlite_connection(self.path) as connection:
                connection.execute("UPDATE hormuz_schema_migrations SET state=? WHERE version=13", (state,))
            before = sqlite_snapshot(self.path)
            self.assertEqual(predecessor_call(self.request()), {"status": "refused", "code": code})
            for read_only in (True, False):
                with self.assertRaisesRegex(StorageSchemaError, code):
                    UsageStore(self.path, read_only=read_only)
            self.assertEqual(sqlite_snapshot(self.path), before)

    def test_quiesced_old_pair_restore_is_separate_and_preserves_original_receipts(self):
        checkpoint = self.root / "checkpoint.sqlite3"
        sqlite_backup(self.path, checkpoint)
        with sqlite_witness():
            UsageStore(self.path)
        retained = sqlite_snapshot(self.path)
        restored = self.root / "restored.sqlite3"
        sqlite_backup(checkpoint, restored)
        self.assertEqual(sqlite_snapshot(restored), self.before)
        self.assertEqual(predecessor_call(self.request(restored, "replay")), self.seeded)
        self.assertEqual(sqlite_snapshot(self.path), retained)

    def test_post_checkpoint_witness_write_requires_retained_forward_recovery(self):
        old = self.root / "old.sqlite3"
        sqlite_backup(self.path, old)
        with sqlite_witness():
            UsageStore(self.path)
            with managed_sqlite_connection(self.path) as connection:
                for table in PROBE_TABLES:
                    connection.execute(f"INSERT INTO {table} VALUES (?, ?, ?)",
                                       ("acme", "synthetic-post-checkpoint", '{"test_only":true}'))
            retained = sqlite_snapshot(self.path)
            restored = self.root / "forward.sqlite3"
            sqlite_backup(self.path, restored)
            UsageStore(restored, read_only=True).verify_ready()
            self.assertEqual(sqlite_snapshot(restored), retained)
        self.assert_old_rows(retained)
        self.assertNotEqual(sqlite_snapshot(old), retained)
        self.assertEqual(predecessor_call(self.request(old, "replay")), self.seeded)
        self.assertEqual(predecessor_call(self.request(restored)),
                         {"status": "refused", "code": "storage_schema_newer_than_binary"})
        self.assertEqual(sqlite_snapshot(self.path), retained)


@unittest.skipUnless(PINNED and os.environ.get("HORMUZ_TEST_POSTGRES_DSN"),
                     "requires published v1.2.0 artifacts and disposable PostgreSQL")
class PostgresPublishedAccountBindingPreflightTests(PostgresTestCase):
    migrate = previous.PostgresFinanceCollectionTransitionTests.migrate
    runtime = previous.PostgresFinanceCollectionTransitionTests.runtime
    snapshot = previous.PostgresFinanceCollectionTransitionTests.snapshot
    backup = previous.PostgresFinanceCollectionTransitionTests.backup
    restore = previous.PostgresFinanceCollectionTransitionTests.restore

    def setUp(self):
        self._drop_schema(self.schema)
        self.seeded = predecessor_call(self.request(mode="seed"))
        self.assertEqual(self.seeded["status"], "ready")
        self.before = self.snapshot()

    def request(self, dsn=None, mode="ready"):
        return {"backend": "postgresql", "mode": mode, "owner_dsn": self.owner_dsn,
                "runtime_dsn": dsn or self.runtime_dsn, "schema": self.schema,
                "runtime_role": self.runtime_role, "policy_control_role": self.policy_control_role,
                "custody_control_role": self.custody_control_role, "custody_executor_role": self.custody_executor_role}

    @contextmanager
    def witness(self, *, fail=False):
        original = postgres_module._migration_sql

        def migration(version, schema, *roles):
            if version != 18:
                return original(version, schema, *roles)
            statements = [f"CREATE TABLE {schema}.{table} (organization_id TEXT NOT NULL, "
                          "event_id TEXT PRIMARY KEY, evidence_json TEXT NOT NULL);" for table in PROBE_TABLES]
            if fail:
                statements.append("SELECT 1 / 0;")
            return "\n".join(statements)

        # Test-only owner tables receive no grants. No production ACL
        # expectation is patched or promoted by the transaction witness.
        with mock.patch.object(postgres_module, "POSTGRES_SCHEMA_VERSION", 18), mock.patch.object(
            postgres_module, "_migration_sql", side_effect=migration
        ):
            yield

    def assert_old_rows(self, after):
        for table, rows in self.before["rows"].items():
            actual = after["rows"][table]
            if table == "hormuz_schema_migrations":
                actual = [row for row in actual if json.loads(row[0])["version"] != 18]
            self.assertEqual(actual, rows, table)
        for field in ("shape", "constraints", "triggers", "functions"):
            for definition in self.before[field]:
                self.assertIn(definition, after[field], field)

    def test_published_predecessor_populates_finance_and_replays_original_receipts(self):
        for table in POPULATED_TABLES:
            self.assertTrue(self.before["rows"][table], table)
        self.assertEqual(predecessor_call(self.request(mode="replay")), self.seeded)
        self.assertEqual(self.snapshot(), self.before)
        self.runtime().verify_ready()

    def test_real_missing_successor_preserves_published_predecessor(self):
        with mock.patch.object(postgres_module, "POSTGRES_SCHEMA_VERSION", 18):
            with self.assertRaisesRegex(PostgresStorageError, "storage_schema_migration_unsupported"):
                self.migrate()
        self.assertEqual(self.snapshot(), self.before)
        self.assertEqual(predecessor_call(self.request()), BASELINE_READY)

    def test_test_only_ddl_failure_rolls_back_and_retry_is_idempotent(self):
        with self.witness(fail=True), self.assertRaisesRegex(PostgresStorageError, "storage_unavailable"):
            self.migrate()
        self.assertEqual(self.snapshot(), self.before)
        with self.witness():
            self.assertEqual(self.migrate().version, 18)
            after = self.snapshot()
            self.assert_old_rows(after)
            self.assertTrue(all(after["rows"][table] == [] for table in PROBE_TABLES))
            self.migrate()
            self.assertEqual(self.snapshot(), after)

    def test_published_and_current_binaries_refuse_partial_and_newer_without_repair(self):
        with self.witness():
            self.migrate()
        for state, code in (("applied", "storage_schema_newer_than_binary"),
                            ("applying", "storage_schema_partial_upgrade")):
            with self.psycopg.connect(self.owner_dsn) as connection:
                connection.execute(self.sql.SQL("UPDATE {}.hormuz_schema_migrations SET state=%s WHERE version=18")
                                   .format(self.sql.Identifier(self.schema)), (state,))
            before = self.snapshot()
            self.assertEqual(predecessor_call(self.request()), {"status": "refused", "code": code})
            for operation in (self.migrate, self.runtime):
                with self.assertRaisesRegex(PostgresStorageError, code):
                    operation()
            self.assertEqual(self.snapshot(), before)

    @unittest.skipUnless(os.environ.get("HORMUZ_TEST_PG_CONTAINER"), "requires matching pg_dump/restore")
    def test_quiesced_old_pair_restore_is_separate_and_preserves_original_receipts(self):
        checkpoint = self.backup()
        with self.witness():
            self.migrate()
        retained = self.snapshot()
        owner, runtime = self.restore(checkpoint)
        self.assertEqual(self.snapshot(owner), self.before)
        self.assertEqual(predecessor_call(self.request(runtime, "replay")), self.seeded)
        self.assertEqual(self.snapshot(), retained)

    @unittest.skipUnless(os.environ.get("HORMUZ_TEST_PG_CONTAINER"), "requires matching pg_dump/restore")
    def test_post_checkpoint_witness_write_requires_retained_forward_recovery(self):
        old = self.backup()
        with self.witness():
            self.migrate()
            with self.psycopg.connect(self.owner_dsn) as connection:
                for table in PROBE_TABLES:
                    connection.execute(self.sql.SQL("INSERT INTO {}.{} VALUES (%s, %s, %s)").format(
                        self.sql.Identifier(self.schema), self.sql.Identifier(table)),
                        ("acme", "synthetic-post-checkpoint", '{"test_only":true}'))
            retained = self.snapshot()
            forward = self.backup()
            owner, runtime = self.restore(forward)
            self.runtime(runtime).verify_ready()
            self.assertEqual(self.snapshot(owner), retained)
        self.assert_old_rows(retained)
        old_owner, old_runtime = self.restore(old)
        self.assertEqual(self.snapshot(old_owner), self.before)
        self.assertNotEqual(self.snapshot(old_owner), retained)
        self.assertEqual(predecessor_call(self.request(old_runtime, "replay")), self.seeded)
        self.assertEqual(predecessor_call(self.request(runtime)),
                         {"status": "refused", "code": "storage_schema_newer_than_binary"})
        self.assertEqual(self.snapshot(), retained)

@unittest.skipUnless(os.environ.get("HORMUZ_TEST_POSTGRES_DSN"), "requires disposable PostgreSQL")
class PostgresAccountBindingACLPreflightTests(PostgresTestCase):
    def test_two_managed_bootstraps_measure_literal_proposal_and_reject_extra_permission(self):
        for _ in range(2):
            self.measure_proposal()

    def measure_proposal(self):
        # Use the actual deployment bootstrap, whose LOGIN/NOLOGIN role
        # boundary differs from the general repository test fixture.
        suffix = uuid4().hex[:12]
        schema = "binding_acl_" + suffix
        login = "binding_login_" + suffix
        roles = tuple("binding_" + name + "_" + suffix for name in ("runtime", "policy", "custody", "executor"))
        runtime_dsn = self.psycopg.conninfo.make_conninfo(self.owner_dsn,
            user=login, password="synthetic-binding-login-password")
        arguments = dict(schema=schema, runtime_role=roles[0], policy_control_role=roles[1],
                         custody_control_role=roles[2], custody_executor_role=roles[3])

        def cleanup():
            with self.psycopg.connect(self.owner_dsn, autocommit=True) as connection:
                connection.execute(self.sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(self.sql.Identifier(schema)))
                for role in (login, *roles):
                    connection.execute(self.sql.SQL("DROP ROLE IF EXISTS {}").format(self.sql.Identifier(role)))

        self.addCleanup(cleanup)
        with self.psycopg.connect(self.owner_dsn) as connection:
            connection.execute(self.sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB "
                "NOCREATEROLE INHERIT NOREPLICATION NOBYPASSRLS").format(
                    self.sql.Identifier(login), self.sql.Literal("synthetic-binding-login-password")))

        def bootstrap():
            return postgres_module.bootstrap_postgres_deployment(self.owner_dsn, runtime_dsn, **arguments)

        def verify_runtime():
            return postgres_module.verify_postgres_deployment_runtime(runtime_dsn, **arguments)

        def acl(connection):
            with connection.cursor() as cursor:
                owner = cursor.execute("SELECT current_user").fetchone()[0]
                entries = postgres_module._postgres_acl_entries(cursor, schema=schema, migration_login=owner)
            return postgres_module._postgres_acl_boundary(entries, schema=schema, migration_login=owner, role_names=roles)

        first = bootstrap()
        with self.psycopg.connect(self.owner_dsn) as connection:
            self.assertEqual(acl(connection), PROPOSED_ACL)
        self.assertEqual(bootstrap(), first)
        verify_runtime()
        with self.psycopg.connect(self.owner_dsn) as connection:
            connection.execute(self.sql.SQL("GRANT DELETE ON {}.gateway_provider_attempt_metrics TO {}")
                               .format(self.sql.Identifier(schema), self.sql.Identifier(roles[0])))
            injected = acl(connection)
        for operation in (bootstrap, verify_runtime):
            with self.assertRaisesRegex(PostgresStorageError, "acl_boundary_invalid"):
                operation()
        self.assertEqual(injected, INJECTED_ACL)
