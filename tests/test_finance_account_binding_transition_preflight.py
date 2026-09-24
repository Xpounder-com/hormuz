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
from hormuz._finance_account_binding_schema import QUERY_AUDIT_TABLE
from hormuz.audit_chain import AuditChainSource
from hormuz.finance_account_evidence import (
    QUERY_AUDIT_SCHEMA_ID,
    canonical_evidence_text,
    validate_finance_query_audit_event,
)
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
        self.assertEqual(UsageStore.schema_version, 18)
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
            with (
                mock.patch.object(UsageStore, "schema_version", 13),
                mock.patch.object(portfolio_sql_module, "SQLITE_SCHEMA_VERSION", 13),
            ):
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
            with (
                mock.patch.object(UsageStore, "schema_version", 13),
                mock.patch.object(portfolio_sql_module, "SQLITE_SCHEMA_VERSION", 13),
            ):
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
        self.assertEqual(postgres_module.POSTGRES_SCHEMA_VERSION, 23)
        self._drop_schema(self.schema)
        # This historical transition deliberately leaves the schema at v18
        # while it proves the account-binding successor. Restore the current
        # v22 schema even when an assertion fails so later tests do not inherit
        # a predecessor schema from this shared class fixture.
        self.addCleanup(self.migrate)
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
        with mock.patch.object(postgres_module, "POSTGRES_SCHEMA_VERSION", 18):
            self.assertEqual(self.migrate().version, 18)
        after = self.snapshot()
        for table, rows in before["rows"].items():
            if table == "hormuz_schema_migrations":
                continue
            self.assertEqual(after["rows"][table], rows, table)
        for table in PROBE_TABLES:
            self.assertEqual(after["rows"][table], [])
        with mock.patch.object(postgres_module, "POSTGRES_SCHEMA_VERSION", 18):
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


# These names determine the implemented managed-deployment ACL surface.
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
# Measured twice in independent disposable managed-role PostgreSQL bootstraps;
# never derived as expectations from the database under test.  This cumulative
# deployment probe follows the current schema-20 baseline after the Linear
# reconciliation migration rather than freezing the historical schema-19 ACL.
PROPOSED_ACL = (222, "cd86c395bea316873e11e175563cbf31563212c45ca6cb6b6fb066f2f8f1e64d")
INJECTED_ACL = (223, "012a3b9915df3000feab05f408ee27a1fae18e6f14cf773d665f8ee498dcbb11")
PINNED = bool(os.environ.get("HORMUZ_TEST_ACCOUNT_BINDING_PYTHON")
              and os.environ.get("HORMUZ_TEST_ACCOUNT_BINDING_SOURCE"))


def query_audit_event(binding_id, binding_version):
    event = {
        "schema_id": QUERY_AUDIT_SCHEMA_ID,
        "schema_version": 1,
        "organization_id": "acme",
        "query_event_id": str(uuid4()),
        "actor_id": "alice",
        "query_class": "finance_coverage_report_v1",
        "binding_id": binding_id,
        "binding_version": binding_version,
        "collection_profile": "openai.organization-usage-completions.v1",
        "query_start_at": "2026-09-01T00:00:00.000000Z",
        "query_end_at": "2026-09-02T00:00:00.000000Z",
        "as_of_commit_sequence": 0,
        "currency": "USD",
        "selected_snapshot_count": 0,
        "coverage_bucket_count": 0,
        "provider_observation_count": 0,
        "terminal_attempt_count": 0,
        "terminal_attempts_missing_sidecar_count": 0,
        "occurred_at": "2026-09-02T00:00:00.000000Z",
    }
    validate_finance_query_audit_event(event)
    return event


def query_audit_row(event):
    row = {
        key: value
        for key, value in event.items()
        if key not in {"schema_id", "schema_version"}
    }
    row["evidence_json"] = canonical_evidence_text(event)
    return row


def query_audit_source(event):
    return AuditChainSource(
        QUERY_AUDIT_SCHEMA_ID,
        1,
        str(event["query_event_id"]),
    )


def append_sqlite_query_audit(store):
    with store._connection() as connection:
        binding = connection.execute(
            "SELECT binding_id, version FROM portfolio_finance_source_binding_versions "
            "WHERE organization_id='acme' ORDER BY version DESC LIMIT 1"
        ).fetchone()
        if binding is None:
            raise AssertionError("published_predecessor_source_binding_missing")
        event = query_audit_event(str(binding[0]), int(binding[1]))
        row = query_audit_row(event)
        columns = tuple(row)
        connection.execute(
            f"INSERT INTO {QUERY_AUDIT_TABLE} ({', '.join(columns)}) "
            f"VALUES ({', '.join('?' for _ in columns)})",
            tuple(row.values()),
        )
        store._append_audit_chain_entry_in_connection(
            connection,
            event=event,
            source=query_audit_source(event),
        )


def append_postgres_query_audit(store):
    with store._transaction("acme") as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT binding_id, version FROM "
                f"{store._table('portfolio_finance_source_binding_versions')} "
                "WHERE organization_id=%s ORDER BY version DESC LIMIT 1",
                ("acme",),
            )
            binding = cursor.fetchone()
            if binding is None:
                raise AssertionError("published_predecessor_source_binding_missing")
            binding_id = binding["binding_id"] if isinstance(binding, dict) else binding[0]
            binding_version = binding["version"] if isinstance(binding, dict) else binding[1]
            event = query_audit_event(str(binding_id), int(binding_version))
            row = query_audit_row(event)
            columns = tuple(row)
            cursor.execute(
                f"INSERT INTO {store._table(QUERY_AUDIT_TABLE)} "
                f"({', '.join(columns)}) VALUES ({', '.join('%s' for _ in columns)})",
                tuple(row.values()),
            )
            store._append_audit_chain_entry_in_cursor(
                cursor,
                event=event,
                source=query_audit_source(event),
            )


@contextmanager
def sqlite_migration_failure():
    original = UsageStore._apply_migration

    def migration(connection, version):
        original(connection, version)
        if version == 13:
            connection.execute("INSERT INTO deliberately_absent_account_binding_probe VALUES (1)")

    with mock.patch.object(UsageStore, "_apply_migration", side_effect=migration):
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

    def assert_old_rows(self, after, *, allow_appended=False):
        for table, rows in self.before["rows"].items():
            if table == "hormuz_schema_migrations":
                predecessor_versions = {row[0] for row in rows}
                actual = [
                    row for row in after["rows"][table]
                    if row[0] in predecessor_versions
                ]
            else:
                actual = after["rows"][table]
            if allow_appended:
                if table == "gateway_audit_chain_heads":
                    self.assertEqual(len(actual), len(rows), table)
                else:
                    self.assertTrue(all(row in actual for row in rows), table)
            else:
                self.assertEqual(actual, rows, table)

    def test_published_predecessor_populates_finance_and_replays_original_receipts(self):
        for table in POPULATED_TABLES:
            self.assertTrue(self.before["rows"][table], table)
        self.assertEqual(predecessor_call(self.request(mode="replay")), self.seeded)
        self.assertEqual(sqlite_snapshot(self.path), self.before)
        with self.assertRaisesRegex(StorageSchemaError, "storage_schema_unavailable"):
            UsageStore(self.path, read_only=True)

    def test_real_successor_preserves_published_predecessor(self):
        UsageStore(self.path).verify_ready()
        after = sqlite_snapshot(self.path)
        self.assert_old_rows(after)
        self.assertTrue(all(after["rows"][table] == [] for table in PROBE_TABLES))
        self.assertEqual(
            predecessor_call(self.request()),
            {"status": "refused", "code": "storage_schema_newer_than_binary"},
        )

    def test_real_ddl_failure_rolls_back_and_retry_is_idempotent(self):
        with sqlite_migration_failure(), self.assertRaises(sqlite3.OperationalError):
            UsageStore(self.path)
        self.assertEqual(sqlite_snapshot(self.path), self.before)
        UsageStore(self.path)
        after = sqlite_snapshot(self.path)
        self.assert_old_rows(after)
        self.assertTrue(all(after["rows"][table] == [] for table in PROBE_TABLES))
        UsageStore(self.path)
        self.assertEqual(sqlite_snapshot(self.path), after)

    def test_published_binary_refuses_newer_and_both_refuse_partial_without_repair(self):
        UsageStore(self.path)
        self.assertEqual(
            predecessor_call(self.request()),
            {"status": "refused", "code": "storage_schema_newer_than_binary"},
        )
        UsageStore(self.path, read_only=True).verify_ready()
        with managed_sqlite_connection(self.path) as connection:
            connection.execute(
                "UPDATE hormuz_schema_migrations SET state='applying' WHERE version=13"
            )
        before = sqlite_snapshot(self.path)
        self.assertEqual(
            predecessor_call(self.request()),
            {"status": "refused", "code": "storage_schema_partial_upgrade"},
        )
        for read_only in (True, False):
            with self.assertRaisesRegex(StorageSchemaError, "storage_schema_partial_upgrade"):
                UsageStore(self.path, read_only=read_only)
        self.assertEqual(sqlite_snapshot(self.path), before)

    def test_quiesced_old_pair_restore_is_separate_and_preserves_original_receipts(self):
        checkpoint = self.root / "checkpoint.sqlite3"
        sqlite_backup(self.path, checkpoint)
        UsageStore(self.path)
        retained = sqlite_snapshot(self.path)
        restored = self.root / "restored.sqlite3"
        sqlite_backup(checkpoint, restored)
        self.assertEqual(sqlite_snapshot(restored), self.before)
        self.assertEqual(predecessor_call(self.request(restored, "replay")), self.seeded)
        self.assertEqual(sqlite_snapshot(self.path), retained)

    def test_post_checkpoint_write_requires_retained_forward_recovery(self):
        old = self.root / "old.sqlite3"
        sqlite_backup(self.path, old)
        store = UsageStore(self.path)
        append_sqlite_query_audit(store)
        retained = sqlite_snapshot(self.path)
        self.assertTrue(retained["rows"][QUERY_AUDIT_TABLE])
        restored = self.root / "forward.sqlite3"
        sqlite_backup(self.path, restored)
        UsageStore(restored, read_only=True).verify_ready()
        self.assertEqual(sqlite_snapshot(restored), retained)
        self.assert_old_rows(retained, allow_appended=True)
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
        self.assertEqual(postgres_module.POSTGRES_SCHEMA_VERSION, 23)
        version_patch = mock.patch.object(
            postgres_module, "POSTGRES_SCHEMA_VERSION", 18
        )
        version_patch.start()
        self.addCleanup(version_patch.stop)
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
    def migration_failure(self):
        original = postgres_module._migration_sql

        def migration(version, schema, *roles):
            statement = original(version, schema, *roles)
            return statement + ("\nSELECT 1 / 0;" if version == 18 else "")

        with mock.patch.object(postgres_module, "_migration_sql", side_effect=migration):
            yield

    def assert_old_rows(self, after, *, allow_appended=False):
        for table, rows in self.before["rows"].items():
            actual = after["rows"][table]
            if table == "hormuz_schema_migrations":
                actual = [row for row in actual if json.loads(row[0])["version"] != 18]
            if allow_appended:
                if table == "gateway_audit_chain_heads":
                    self.assertEqual(len(actual), len(rows), table)
                else:
                    self.assertTrue(all(row in actual for row in rows), table)
            else:
                self.assertEqual(actual, rows, table)

    def test_published_predecessor_populates_finance_and_replays_original_receipts(self):
        for table in POPULATED_TABLES:
            self.assertTrue(self.before["rows"][table], table)
        self.assertEqual(predecessor_call(self.request(mode="replay")), self.seeded)
        self.assertEqual(self.snapshot(), self.before)
        with self.assertRaisesRegex(PostgresStorageError, "storage_schema_unavailable"):
            self.runtime()

    def test_real_successor_preserves_published_predecessor(self):
        self.assertEqual(self.migrate().version, 18)
        after = self.snapshot()
        self.assert_old_rows(after)
        self.assertTrue(all(after["rows"][table] == [] for table in PROBE_TABLES))
        self.runtime().verify_ready()
        self.assertEqual(
            predecessor_call(self.request()),
            {"status": "refused", "code": "storage_schema_newer_than_binary"},
        )

    def test_real_ddl_failure_rolls_back_and_retry_is_idempotent(self):
        with self.migration_failure(), self.assertRaisesRegex(
            PostgresStorageError, "storage_unavailable"
        ):
            self.migrate()
        self.assertEqual(self.snapshot(), self.before)
        self.assertEqual(self.migrate().version, 18)
        after = self.snapshot()
        self.assert_old_rows(after)
        self.assertTrue(all(after["rows"][table] == [] for table in PROBE_TABLES))
        self.migrate()
        self.assertEqual(self.snapshot(), after)

    def test_published_binary_refuses_newer_and_both_refuse_partial_without_repair(self):
        self.migrate()
        self.assertEqual(
            predecessor_call(self.request()),
            {"status": "refused", "code": "storage_schema_newer_than_binary"},
        )
        self.runtime().verify_ready()
        with self.psycopg.connect(self.owner_dsn) as connection:
            connection.execute(
                self.sql.SQL(
                    "UPDATE {}.hormuz_schema_migrations SET state='applying' WHERE version=18"
                ).format(self.sql.Identifier(self.schema))
            )
        before = self.snapshot()
        self.assertEqual(
            predecessor_call(self.request()),
            {"status": "refused", "code": "storage_schema_partial_upgrade"},
        )
        for operation in (self.migrate, self.runtime):
            with self.assertRaisesRegex(PostgresStorageError, "storage_schema_partial_upgrade"):
                operation()
        self.assertEqual(self.snapshot(), before)

    @unittest.skipUnless(os.environ.get("HORMUZ_TEST_PG_CONTAINER"), "requires matching pg_dump/restore")
    def test_quiesced_old_pair_restore_is_separate_and_preserves_original_receipts(self):
        checkpoint = self.backup()
        self.migrate()
        retained = self.snapshot()
        owner, runtime = self.restore(checkpoint)
        self.assertEqual(self.snapshot(owner), self.before)
        self.assertEqual(predecessor_call(self.request(runtime, "replay")), self.seeded)
        self.assertEqual(self.snapshot(), retained)

    @unittest.skipUnless(os.environ.get("HORMUZ_TEST_PG_CONTAINER"), "requires matching pg_dump/restore")
    def test_post_checkpoint_write_requires_retained_forward_recovery(self):
        old = self.backup()
        self.migrate()
        append_postgres_query_audit(self.runtime())
        retained = self.snapshot()
        self.assertTrue(retained["rows"][QUERY_AUDIT_TABLE])
        forward = self.backup()
        owner, runtime = self.restore(forward)
        self.runtime(runtime).verify_ready()
        self.assertEqual(self.snapshot(owner), retained)
        self.assert_old_rows(retained, allow_appended=True)
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

        with mock.patch.object(postgres_module, "POSTGRES_SCHEMA_VERSION", 20):
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
