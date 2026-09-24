"""SQLite 15-to-16 and PostgreSQL 20-to-21 association transition preflight."""

from __future__ import annotations

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
from hormuz.postgres import PostgresStorageError
from hormuz.postgres_usage_store import PostgresUsageStore
from hormuz.store import StorageSchemaError, UsageStore

if __package__:
    from ._finance_account_binding_predecessor_fixture import predecessor_call
    from ._postgres_fixture import PostgresTestCase
    from ._registry_transition_fixture import sqlite_backup, sqlite_snapshot
    from ._sqlite import managed_sqlite_connection
    from . import test_postgres_finance_collection_transition as previous
else:
    from _finance_account_binding_predecessor_fixture import predecessor_call
    from _postgres_fixture import PostgresTestCase
    from _registry_transition_fixture import sqlite_backup, sqlite_snapshot
    from _sqlite import managed_sqlite_connection
    import test_postgres_finance_collection_transition as previous


ROOT = Path(__file__).resolve().parents[1]
SQLITE_PROPOSAL = ROOT / "docs/association-successor-schema-proposal.sqlite.sql"
POSTGRES_PROPOSAL = ROOT / "docs/association-successor-schema-acl-proposal.sql"
PROPOSAL_TABLES = (
    "portfolio_association_audit_events",
    "portfolio_run_work_link_events",
    "portfolio_run_work_link_idempotency",
    "portfolio_run_outcome_association_events",
    "portfolio_run_outcome_association_cursors",
)
PROPOSED_ACL = (
    252,
    "a0296c1b3bdad58acfc2bd89c4c020fa6a0af75fba7ef895a798fb3ec4fcd3dc",
)
PINNED = bool(
    os.environ.get("HORMUZ_TEST_ACCOUNT_BINDING_PYTHON")
    and os.environ.get("HORMUZ_TEST_ACCOUNT_BINDING_SOURCE")
)


def _sqlite_proposal_statements():
    buffer = ""
    for line in SQLITE_PROPOSAL.read_text(encoding="utf-8").splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            if statement:
                yield statement
            buffer = ""
    if buffer.strip():
        raise AssertionError("association_sqlite_proposal_incomplete")


def _apply_sqlite_proposal(connection: sqlite3.Connection) -> None:
    for statement in _sqlite_proposal_statements():
        connection.execute(statement)


@contextmanager
def sqlite_association_candidate(*, fail: bool = False):
    original = UsageStore._apply_migration

    def migration(connection, version):
        original(connection, version)
        if fail and version == 16:
            connection.execute(
                "INSERT INTO deliberately_absent_association_probe VALUES (1)"
            )

    with (
        mock.patch.object(UsageStore, "schema_version", 16),
        mock.patch.object(UsageStore, "_apply_migration", side_effect=migration),
    ):
        yield


@contextmanager
def postgres_association_candidate(*, fail: bool = False):
    original = postgres_module._migration_sql

    def migration(version, schema, *roles):
        statement = original(version, schema, *roles)
        if fail and version == 21:
            return statement + "\nSELECT 1 / 0;"
        return statement

    with (
        mock.patch.object(postgres_module, "POSTGRES_SCHEMA_VERSION", 21),
        mock.patch.object(postgres_module, "_migration_sql", side_effect=migration),
    ):
        yield


def _sqlite_insert_witness(path: Path) -> None:
    with managed_sqlite_connection(path) as connection:
        connection.execute(
            "INSERT INTO portfolio_association_audit_events "
            "(organization_id,event_id,sequence,actor_id,operation,entity_id,"
            "reason_code,occurred_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                "acme",
                "11111111-1111-4111-8111-111111111111",
                1,
                "alice",
                "evaluate",
                None,
                "observed",
                "2026-09-23T00:00:00.000000Z",
            ),
        )


class SQLiteAssociationProposalBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "proposal.sqlite3"
        with mock.patch.object(UsageStore, "schema_version", 15):
            UsageStore(self.path).verify_ready()
        with managed_sqlite_connection(self.path) as connection:
            _apply_sqlite_proposal(connection)

    def test_exact_proposal_is_additive_empty_and_append_only(self):
        with managed_sqlite_connection(self.path) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            triggers = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger'"
                ).fetchall()
            }
            self.assertTrue(set(PROPOSAL_TABLES).issubset(tables))
            self.assertTrue(
                {
                    f"{table}_no_{operation}"
                    for table in PROPOSAL_TABLES
                    for operation in ("update", "delete")
                }.issubset(triggers)
            )
            self.assertTrue(
                all(
                    connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                    == 0
                    for table in PROPOSAL_TABLES
                )
            )
        _sqlite_insert_witness(self.path)
        with managed_sqlite_connection(self.path) as connection:
            for operation in (
                "UPDATE portfolio_association_audit_events SET actor_id='mallory'",
                "DELETE FROM portfolio_association_audit_events",
            ):
                with self.assertRaisesRegex(
                    sqlite3.IntegrityError, "association_metadata_append_only"
                ):
                    connection.execute(operation)

    def test_cross_tenant_and_missing_parent_foreign_keys_are_declared(self):
        with managed_sqlite_connection(self.path) as connection:
            parents = {
                tuple(row[2:5])
                for row in connection.execute(
                    "PRAGMA foreign_key_list(portfolio_run_work_link_events)"
                ).fetchall()
            }
        for table in (
            "gateway_request_attempts",
            "portfolio_attribution_events",
            "portfolio_outcome_events",
            "portfolio_work_scope_versions",
            "portfolio_binding_events",
            "portfolio_run_work_link_events",
            "portfolio_association_audit_events",
        ):
            self.assertTrue(any(parent[0] == table for parent in parents), table)
        self.assertTrue(
            all(
                any(parent[0] == table and parent[1] == "organization_id" for parent in parents)
                for table in {parent[0] for parent in parents}
            )
        )


@unittest.skipUnless(
    PINNED, "requires digest-verified published v1.2.0 wheel and source"
)
class SQLitePublishedAssociationTransitionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "published.sqlite3"
        self.seeded = predecessor_call(self.request(mode="seed"))
        self.assertEqual(self.seeded["status"], "ready")
        self.published = sqlite_snapshot(self.path)
        self.assertEqual(UsageStore.schema_version, 17)

    def request(self, path=None, mode="ready"):
        return {"backend": "sqlite", "path": str(path or self.path), "mode": mode}

    def advance_integrated_baseline(self):
        with mock.patch.object(UsageStore, "schema_version", 15):
            UsageStore(self.path).verify_ready()
        self.baseline = sqlite_snapshot(self.path)
        self.assertIn(
            (15, "applied"),
            {
                (row[0], row[1])
                for row in self.baseline["rows"]["hormuz_schema_migrations"]
            },
        )
        return self.baseline

    def assert_baseline_rows_preserved(self, after):
        for table, rows in self.baseline["rows"].items():
            actual = after["rows"][table]
            if table == "hormuz_schema_migrations":
                actual = [row for row in actual if row[0] != 16]
            self.assertEqual(actual, rows, table)

    def test_missing_association_successor_preserves_integrated_baseline(self):
        before = self.advance_integrated_baseline()
        original = UsageStore._apply_migration

        def missing(connection, version):
            if version == 16:
                raise StorageSchemaError("storage_schema_migration_unsupported")
            return original(connection, version)

        with (
            mock.patch.object(UsageStore, "schema_version", 16),
            mock.patch.object(UsageStore, "_apply_migration", side_effect=missing),
        ):
            with self.assertRaisesRegex(
                StorageSchemaError, "storage_schema_migration_unsupported"
            ):
                UsageStore(self.path)
        self.assertEqual(sqlite_snapshot(self.path), before)

    def test_test_only_association_ddl_failure_rolls_back_and_retry_is_idempotent(self):
        self.advance_integrated_baseline()
        with sqlite_association_candidate(fail=True), self.assertRaises(
            sqlite3.OperationalError
        ):
            UsageStore(self.path)
        self.assertEqual(sqlite_snapshot(self.path), self.baseline)
        with sqlite_association_candidate():
            UsageStore(self.path).verify_ready()
        after = sqlite_snapshot(self.path)
        self.assert_baseline_rows_preserved(after)
        self.assertTrue(all(after["rows"][table] == [] for table in PROPOSAL_TABLES))
        with sqlite_association_candidate():
            UsageStore(self.path).verify_ready()
        self.assertEqual(sqlite_snapshot(self.path), after)

    def test_published_and_current_binaries_refuse_partial_and_newer_without_repair(self):
        self.advance_integrated_baseline()
        with sqlite_association_candidate():
            UsageStore(self.path)
        self.assertEqual(
            predecessor_call(self.request()),
            {"status": "refused", "code": "storage_schema_newer_than_binary"},
        )
        with mock.patch.object(UsageStore, "schema_version", 15):
            with self.assertRaisesRegex(
                StorageSchemaError, "storage_schema_newer_than_binary"
            ):
                UsageStore(self.path, read_only=True)
        with managed_sqlite_connection(self.path) as connection:
            connection.execute(
                "UPDATE hormuz_schema_migrations SET state='applying' WHERE version=16"
            )
        partial = sqlite_snapshot(self.path)
        self.assertEqual(
            predecessor_call(self.request()),
            {"status": "refused", "code": "storage_schema_partial_upgrade"},
        )
        for version in (15, 16):
            with mock.patch.object(UsageStore, "schema_version", version):
                with self.assertRaisesRegex(
                    StorageSchemaError, "storage_schema_partial_upgrade"
                ):
                    UsageStore(self.path, read_only=True)
        self.assertEqual(sqlite_snapshot(self.path), partial)

    def test_quiesced_published_pair_restore_replays_original_receipt(self):
        old = self.root / "published-checkpoint.sqlite3"
        sqlite_backup(self.path, old)
        self.advance_integrated_baseline()
        with sqlite_association_candidate():
            UsageStore(self.path)
        retained = sqlite_snapshot(self.path)
        restored = self.root / "published-restored.sqlite3"
        sqlite_backup(old, restored)
        self.assertEqual(sqlite_snapshot(restored), self.published)
        self.assertEqual(predecessor_call(self.request(restored, "replay")), self.seeded)
        self.assertEqual(sqlite_snapshot(self.path), retained)

    def test_post_checkpoint_witness_requires_retained_forward_recovery(self):
        old = self.root / "published-checkpoint.sqlite3"
        sqlite_backup(self.path, old)
        self.advance_integrated_baseline()
        with sqlite_association_candidate():
            UsageStore(self.path)
        _sqlite_insert_witness(self.path)
        retained = sqlite_snapshot(self.path)
        self.assertEqual(len(retained["rows"][PROPOSAL_TABLES[0]]), 1)
        forward = self.root / "candidate-forward.sqlite3"
        sqlite_backup(self.path, forward)
        with sqlite_association_candidate():
            UsageStore(forward, read_only=True).verify_ready()
        self.assertEqual(sqlite_snapshot(forward), retained)
        restored_old = self.root / "published-restored.sqlite3"
        sqlite_backup(old, restored_old)
        self.assertEqual(predecessor_call(self.request(restored_old, "replay")), self.seeded)
        self.assertNotEqual(sqlite_snapshot(restored_old), retained)
        self.assertEqual(sqlite_snapshot(self.path), retained)


@unittest.skipUnless(
    PINNED and os.environ.get("HORMUZ_TEST_POSTGRES_DSN"),
    "requires published v1.2.0 artifacts and disposable PostgreSQL",
)
class PostgresPublishedAssociationTransitionTests(PostgresTestCase):
    migrate = previous.PostgresFinanceCollectionTransitionTests.migrate
    runtime = previous.PostgresFinanceCollectionTransitionTests.runtime
    snapshot = previous.PostgresFinanceCollectionTransitionTests.snapshot
    backup = previous.PostgresFinanceCollectionTransitionTests.backup
    restore = previous.PostgresFinanceCollectionTransitionTests.restore

    def setUp(self):
        self._drop_schema(self.schema)
        self.seeded = predecessor_call(self.request(mode="seed"))
        self.assertEqual(self.seeded["status"], "ready")
        self.published = self.snapshot()
        self.assertEqual(postgres_module.POSTGRES_SCHEMA_VERSION, 22)
        if self._testMethodName in {
            "test_quiesced_published_pair_restore_replays_original_receipt",
            "test_post_checkpoint_witness_requires_retained_forward_recovery",
        }:
            self.published_checkpoint = self.backup()
        with mock.patch.object(postgres_module, "POSTGRES_SCHEMA_VERSION", 20):
            self.migrate()
        self.baseline = self.snapshot()

    def request(self, dsn=None, mode="ready"):
        return {
            "backend": "postgresql",
            "mode": mode,
            "owner_dsn": self.owner_dsn,
            "runtime_dsn": dsn or self.runtime_dsn,
            "schema": self.schema,
            "runtime_role": self.runtime_role,
            "policy_control_role": self.policy_control_role,
            "custody_control_role": self.custody_control_role,
            "custody_executor_role": self.custody_executor_role,
        }

    def assert_baseline_rows_preserved(self, after):
        for table, rows in self.baseline["rows"].items():
            actual = after["rows"][table]
            if table == "hormuz_schema_migrations":
                actual = [
                    row
                    for row in actual
                    if json.loads(row[0])["version"] != 21
                ]
            self.assertEqual(actual, rows, table)

    def insert_witness(self, dsn=None):
        with self.psycopg.connect(dsn or self.owner_dsn) as connection:
            connection.execute(
                self.sql.SQL(
                    "INSERT INTO {}.portfolio_association_audit_events "
                    "(organization_id,event_id,sequence,actor_id,operation,entity_id,"
                    "reason_code,occurred_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)"
                ).format(self.sql.Identifier(self.schema)),
                (
                    "acme",
                    "11111111-1111-4111-8111-111111111111",
                    1,
                    "alice",
                    "evaluate",
                    None,
                    "observed",
                    "2026-09-23T00:00:00.000000Z",
                ),
            )

    def test_missing_association_successor_preserves_integrated_baseline(self):
        original = postgres_module._migration_sql

        def missing(version, *args, **kwargs):
            if version == 21:
                raise PostgresStorageError("storage_schema_migration_unsupported")
            return original(version, *args, **kwargs)

        boundaries = dict(postgres_module._POSTGRES_EXPECTED_ACL_BOUNDARY_BY_VERSION)
        boundaries[21] = PROPOSED_ACL
        with (
            mock.patch.object(postgres_module, "POSTGRES_SCHEMA_VERSION", 21),
            mock.patch.object(postgres_module, "_migration_sql", side_effect=missing),
            mock.patch.object(
                postgres_module,
                "_POSTGRES_EXPECTED_ACL_BOUNDARY_BY_VERSION",
                boundaries,
            ),
        ):
            with self.assertRaisesRegex(
                PostgresStorageError, "storage_schema_migration_unsupported"
            ):
                self.migrate()
        self.assertEqual(self.snapshot(), self.baseline)

    def test_test_only_association_ddl_failure_rolls_back_and_retry_is_idempotent(self):
        with postgres_association_candidate(fail=True), self.assertRaisesRegex(
            PostgresStorageError, "storage_unavailable"
        ):
            self.migrate()
        self.assertEqual(self.snapshot(), self.baseline)
        with postgres_association_candidate():
            self.assertEqual(self.migrate().version, 21)
        after = self.snapshot()
        self.assert_baseline_rows_preserved(after)
        self.assertTrue(all(after["rows"][table] == [] for table in PROPOSAL_TABLES))
        with postgres_association_candidate():
            self.assertEqual(self.migrate().version, 21)
        self.assertEqual(self.snapshot(), after)

    def test_published_and_current_binaries_refuse_partial_and_newer_without_repair(self):
        with postgres_association_candidate():
            self.migrate()
        self.assertEqual(
            predecessor_call(self.request()),
            {"status": "refused", "code": "storage_schema_newer_than_binary"},
        )
        with mock.patch.object(postgres_module, "POSTGRES_SCHEMA_VERSION", 20):
            with self.assertRaisesRegex(
                PostgresStorageError, "storage_schema_newer_than_binary"
            ):
                self.runtime()
        with self.psycopg.connect(self.owner_dsn) as connection:
            connection.execute(
                self.sql.SQL(
                    "UPDATE {}.hormuz_schema_migrations SET state='applying' "
                    "WHERE version=21"
                ).format(self.sql.Identifier(self.schema))
            )
        partial = self.snapshot()
        self.assertEqual(
            predecessor_call(self.request()),
            {"status": "refused", "code": "storage_schema_partial_upgrade"},
        )
        with mock.patch.object(postgres_module, "POSTGRES_SCHEMA_VERSION", 20):
            with self.assertRaisesRegex(
                PostgresStorageError, "storage_schema_partial_upgrade"
            ):
                self.runtime()
        with postgres_association_candidate():
            with self.assertRaisesRegex(
                PostgresStorageError, "storage_schema_partial_upgrade"
            ):
                self.runtime()
        self.assertEqual(self.snapshot(), partial)

    @unittest.skipUnless(
        os.environ.get("HORMUZ_TEST_PG_CONTAINER"), "requires matching pg_dump/restore"
    )
    def test_quiesced_published_pair_restore_replays_original_receipt(self):
        with postgres_association_candidate():
            self.migrate()
        retained = self.snapshot()
        owner, runtime = self.restore(self.published_checkpoint)
        self.assertEqual(self.snapshot(owner)["rows"], self.published["rows"])
        self.assertEqual(predecessor_call(self.request(runtime, "replay")), self.seeded)
        self.assertEqual(self.snapshot(), retained)

    @unittest.skipUnless(
        os.environ.get("HORMUZ_TEST_PG_CONTAINER"), "requires matching pg_dump/restore"
    )
    def test_post_checkpoint_witness_requires_retained_forward_recovery(self):
        with postgres_association_candidate():
            self.migrate()
        self.insert_witness()
        retained = self.snapshot()
        self.assertEqual(len(retained["rows"][PROPOSAL_TABLES[0]]), 1)
        forward = self.backup()
        owner, runtime = self.restore(forward)
        with postgres_association_candidate():
            PostgresUsageStore(
                runtime,
                schema=self.schema,
                runtime_role=self.runtime_role,
                organization_ids=("acme", "beta"),
            ).verify_ready()
        self.assertEqual(self.snapshot(owner)["rows"], retained["rows"])
        old_owner, old_runtime = self.restore(self.published_checkpoint)
        self.assertEqual(self.snapshot(old_owner)["rows"], self.published["rows"])
        self.assertEqual(predecessor_call(self.request(old_runtime, "replay")), self.seeded)
        self.assertNotEqual(self.snapshot(old_owner)["rows"], retained["rows"])
        self.assertEqual(self.snapshot(), retained)


@unittest.skipUnless(
    os.environ.get("HORMUZ_TEST_POSTGRES_DSN"), "requires disposable PostgreSQL"
)
class PostgresAssociationProposalACLTests(PostgresTestCase):
    def bootstrap_clean_proposal(self):
        schema = "association_acl_" + uuid4().hex[:12]
        self.addCleanup(self._drop_schema, schema)
        with mock.patch.object(postgres_module, "POSTGRES_SCHEMA_VERSION", 20):
            status = postgres_module.migrate_postgres(
                self.owner_dsn,
                schema=schema,
                runtime_role=self.runtime_role,
                policy_control_role=self.policy_control_role,
                custody_control_role=self.custody_control_role,
                custody_executor_role=self.custody_executor_role,
            )
        self.assertEqual(status.version, 20)
        proposal = POSTGRES_PROPOSAL.read_text(encoding="utf-8").format(
            schema=postgres_module._quote_identifier(schema),
            runtime_role=postgres_module._quote_identifier(self.runtime_role),
        )
        with self.psycopg.connect(self.owner_dsn) as connection:
            connection.execute(proposal)
        with self.psycopg.connect(self.owner_dsn) as connection:
            with connection.cursor() as cursor:
                owner = cursor.execute("SELECT current_user").fetchone()[0]
                entries = postgres_module._postgres_acl_entries(
                    cursor, schema=schema, migration_login=owner
                )
        boundary = postgres_module._postgres_acl_boundary(
            entries,
            schema=schema,
            migration_login=owner,
            role_names=(
                self.runtime_role,
                self.policy_control_role,
                self.custody_control_role,
                self.custody_executor_role,
            ),
        )
        return schema, boundary

    def test_two_clean_bootstraps_match_literal_successor_acl(self):
        self.assertEqual(self.bootstrap_clean_proposal()[1], PROPOSED_ACL)
        self.assertEqual(self.bootstrap_clean_proposal()[1], PROPOSED_ACL)

    def test_runtime_insert_is_tenant_scoped_and_mutation_is_denied(self):
        schema, boundary = self.bootstrap_clean_proposal()
        self.assertEqual(boundary, PROPOSED_ACL)
        values = (
            "acme",
            "11111111-1111-4111-8111-111111111111",
            1,
            "alice",
            "evaluate",
            None,
            "observed",
            "2026-09-23T00:00:00.000000Z",
        )
        with postgres_module.postgres_transaction(
            self.runtime_dsn,
            schema=schema,
            runtime_role=self.runtime_role,
            organization_id="acme",
        ) as connection:
            connection.execute(
                "INSERT INTO portfolio_association_audit_events "
                "(organization_id,event_id,sequence,actor_id,operation,entity_id,"
                "reason_code,occurred_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                values,
            )
        with postgres_module.postgres_transaction(
            self.runtime_dsn,
            schema=schema,
            runtime_role=self.runtime_role,
            organization_id="beta",
        ) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) AS count FROM portfolio_association_audit_events"
                ).fetchone()["count"],
                0,
            )
        with self.assertRaisesRegex(PostgresStorageError, "storage_access_denied"):
            with postgres_module.postgres_transaction(
                self.runtime_dsn,
                schema=schema,
                runtime_role=self.runtime_role,
                organization_id="acme",
            ) as connection:
                connection.execute(
                    "UPDATE portfolio_association_audit_events "
                    "SET actor_id='mallory' WHERE sequence=1"
                )


if __name__ == "__main__":
    unittest.main()
