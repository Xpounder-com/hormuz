"""Red-first SQLite 11-to-12 provider-collection transition proof."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from hormuz.store import StorageSchemaError, UsageStore

if __package__:
    from ._finance_collection_predecessor_fixture import (
        finance_collection_predecessor_call,
    )
    from ._finance_collection_transition_fixture import (
        seed_sqlite_collection_predecessor,
    )
    from ._registry_transition_fixture import sqlite_backup, sqlite_snapshot
    from ._sqlite import managed_sqlite_connection
else:
    from _finance_collection_predecessor_fixture import (
        finance_collection_predecessor_call,
    )
    from _finance_collection_transition_fixture import (
        seed_sqlite_collection_predecessor,
    )
    from _registry_transition_fixture import sqlite_backup, sqlite_snapshot
    from _sqlite import managed_sqlite_connection


PLANNED_TABLES = (
    "portfolio_finance_source_binding_versions",
    "portfolio_finance_collection_attempts",
    "portfolio_finance_collection_events",
    "portfolio_finance_snapshots",
    "portfolio_finance_usage_observations",
    "portfolio_finance_cost_observations",
)


def _synthetic_collection_migration(connection, *, fail=False):
    connection.execute(
        """
        CREATE TABLE portfolio_finance_source_binding_versions (
            binding_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            organization_id TEXT NOT NULL,
            PRIMARY KEY (binding_id, version)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE portfolio_finance_collection_attempts (
            attempt_id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL,
            binding_id TEXT NOT NULL,
            binding_version INTEGER NOT NULL,
            FOREIGN KEY (binding_id, binding_version)
                REFERENCES portfolio_finance_source_binding_versions
                    (binding_id, version)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE portfolio_finance_collection_events (
            event_id TEXT PRIMARY KEY,
            attempt_id TEXT NOT NULL UNIQUE,
            organization_id TEXT NOT NULL,
            state TEXT NOT NULL,
            FOREIGN KEY (attempt_id)
                REFERENCES portfolio_finance_collection_attempts (attempt_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE portfolio_finance_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            attempt_id TEXT NOT NULL UNIQUE,
            organization_id TEXT NOT NULL,
            content_digest TEXT NOT NULL,
            FOREIGN KEY (attempt_id)
                REFERENCES portfolio_finance_collection_attempts (attempt_id)
        )
        """
    )
    for table in (
        "portfolio_finance_usage_observations",
        "portfolio_finance_cost_observations",
    ):
        connection.execute(
            f"""
            CREATE TABLE {table} (
                observation_id TEXT PRIMARY KEY,
                snapshot_id TEXT NOT NULL,
                organization_id TEXT NOT NULL,
                FOREIGN KEY (snapshot_id)
                    REFERENCES portfolio_finance_snapshots (snapshot_id)
            )
            """
        )
    connection.execute(
        "CREATE INDEX finance_collection_audit_source_probe "
        "ON gateway_audit_chain_entries "
        "(source_schema_id, source_schema_version, source_event_id)"
    )
    if fail:
        raise RuntimeError("synthetic_finance_collection_migration_failure")


class SQLiteFinanceCollectionTransitionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "usage.sqlite3"
        self.assertEqual(UsageStore.schema_version, 11)
        self.seeded = seed_sqlite_collection_predecessor(self.path)
        self.before = sqlite_snapshot(self.path)

    def probe(self, *, fail=False, path=None):
        target = path or self.path
        original = UsageStore._apply_migration

        def apply(connection, version):
            if version == 12:
                _synthetic_collection_migration(connection, fail=fail)
            else:
                original(connection, version)

        with (
            mock.patch.object(UsageStore, "schema_version", 12),
            mock.patch.object(UsageStore, "_apply_migration", side_effect=apply),
        ):
            UsageStore(target).verify_ready()

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
        with mock.patch.object(UsageStore, "schema_version", 12):
            with self.assertRaises(StorageSchemaError) as caught:
                UsageStore(self.path)
        self.assertEqual(caught.exception.code, "storage_schema_migration_unsupported")
        self.assertEqual(sqlite_snapshot(self.path), self.before)

    def test_synthetic_ddl_failure_rolls_back_and_retry_is_idempotent(self):
        with self.assertRaisesRegex(
            RuntimeError,
            "synthetic_finance_collection_migration_failure",
        ):
            self.probe(fail=True)
        self.assertEqual(sqlite_snapshot(self.path), self.before)
        self.probe()
        after = sqlite_snapshot(self.path)
        for table in PLANNED_TABLES:
            self.assertEqual(after["rows"][table], [])
        self.probe()
        self.assertEqual(sqlite_snapshot(self.path), after)

    def test_current_binary_refuses_newer_and_partial_state_without_repair(self):
        self.probe()
        candidate = sqlite_snapshot(self.path)
        for read_only in (False, True):
            with self.subTest(read_only=read_only), self.assertRaises(
                StorageSchemaError
            ) as caught:
                UsageStore(self.path, read_only=read_only)
            self.assertEqual(caught.exception.code, "storage_schema_newer_than_binary")
            self.assertEqual(sqlite_snapshot(self.path), candidate)

        with managed_sqlite_connection(self.path) as connection:
            connection.execute(
                "UPDATE hormuz_schema_migrations SET state='applying' "
                "WHERE version=12"
            )
        partial = sqlite_snapshot(self.path)
        for read_only in (False, True):
            with self.subTest(partial=True, read_only=read_only), self.assertRaises(
                StorageSchemaError
            ) as caught:
                UsageStore(self.path, read_only=read_only)
            self.assertEqual(caught.exception.code, "storage_schema_partial_upgrade")
            self.assertEqual(sqlite_snapshot(self.path), partial)

    def test_quiesced_old_pair_restore_preserves_every_accepted_byte(self):
        backup = self.root / "accepted-main-checkpoint.sqlite3"
        sqlite_backup(self.path, backup)
        self.probe()
        retained = sqlite_snapshot(self.path)
        restored = self.root / "restored-accepted-main.sqlite3"
        shutil.copyfile(backup, restored)
        UsageStore(restored, read_only=True).verify_ready()
        self.assertEqual(sqlite_snapshot(restored), self.before)
        self.assertEqual(sqlite_snapshot(self.path), retained)

    def test_post_checkpoint_write_requires_retained_forward_recovery(self):
        self.probe()
        with managed_sqlite_connection(self.path) as connection:
            connection.execute(
                "INSERT INTO portfolio_finance_source_binding_versions "
                "(binding_id, version, organization_id) VALUES (?, ?, ?)",
                ("synthetic-binding", 1, "acme"),
            )
        after_write = sqlite_snapshot(self.path)
        retained = self.root / "retained-candidate.sqlite3"
        recovered = self.root / "forward-recovered.sqlite3"
        sqlite_backup(self.path, retained)
        sqlite_backup(retained, recovered)
        with (
            mock.patch.object(UsageStore, "schema_version", 12),
            mock.patch.object(UsageStore, "_apply_migration"),
        ):
            UsageStore(recovered, read_only=True).verify_ready()
        self.assertEqual(sqlite_snapshot(recovered), after_write)
        with self.assertRaises(StorageSchemaError) as caught:
            UsageStore(recovered, read_only=True)
        self.assertEqual(caught.exception.code, "storage_schema_newer_than_binary")


@unittest.skipUnless(
    os.environ.get("HORMUZ_TEST_FINANCE_COLLECTION_PYTHON"),
    "requires digest-pinned accepted native-finance predecessor",
)
class SQLiteFinanceCollectionExactPredecessorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "usage.sqlite3"
        seed_sqlite_collection_predecessor(self.path)

    def test_exact_predecessor_accepts_current_and_refuses_newer_and_partial(self):
        request = {"backend": "sqlite", "path": str(self.path)}
        self.assertEqual(
            finance_collection_predecessor_call(request),
            {"status": "ready", "runtime_files_verified": 144},
        )
        SQLiteFinanceCollectionTransitionTests.probe(self)
        self.assertEqual(
            finance_collection_predecessor_call(request),
            {"status": "refused", "code": "storage_schema_newer_than_binary"},
        )
        with managed_sqlite_connection(self.path) as connection:
            connection.execute(
                "UPDATE hormuz_schema_migrations SET state='applying' "
                "WHERE version=12"
            )
        self.assertEqual(
            finance_collection_predecessor_call(request),
            {"status": "refused", "code": "storage_schema_partial_upgrade"},
        )


if __name__ == "__main__":
    unittest.main()
