"""Red-first SQLite 8-to-9 budget transition and predecessor recovery proof."""

from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest import mock

from hormuz.store import StorageSchemaError, UsageStore

if __package__:
    from ._budget_predecessor_fixture import finance_predecessor_call
    from ._finance_fixture import seed_finance
    from ._portfolio_fixture import registry_config
    from ._registry_transition_fixture import seed_registry_ledger, sqlite_backup, sqlite_snapshot
else:
    from _budget_predecessor_fixture import finance_predecessor_call
    from _finance_fixture import seed_finance
    from _portfolio_fixture import registry_config
    from _registry_transition_fixture import seed_registry_ledger, sqlite_backup, sqlite_snapshot


PROBE_TABLE = "budget_transition_test_probe"


class SQLiteBudgetRedFirstTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "usage.sqlite3"
        self.assertEqual(UsageStore.schema_version, 8)
        store = UsageStore(self.path)
        seed_registry_ledger(store)
        seed_finance(replace(registry_config(self.root), database_path=self.path))
        self.before = sqlite_snapshot(self.path)

    def probe(self, *, fail=False, path=None):
        target = path or self.path
        original = UsageStore._apply_migration

        def apply(connection, version):
            if version != 9:
                return original(connection, version)
            connection.execute(
                f"CREATE TABLE {PROBE_TABLE} (id INTEGER PRIMARY KEY, marker TEXT NOT NULL)"
            )
            if fail:
                raise RuntimeError("synthetic_budget_migration_failure")

        with (
            mock.patch.object(UsageStore, "schema_version", 9),
            mock.patch.object(UsageStore, "_apply_migration", side_effect=apply),
        ):
            UsageStore(target).verify_ready()

    def test_missing_migration_is_red_and_transaction_leaves_no_partial_state(self):
        with mock.patch.object(UsageStore, "schema_version", 9):
            with self.assertRaises(StorageSchemaError) as caught:
                UsageStore(self.path)
        self.assertEqual(caught.exception.code, "storage_schema_migration_unsupported")
        self.assertEqual(sqlite_snapshot(self.path), self.before)
        UsageStore(self.path).verify_ready()

    def test_probe_failure_rolls_back_and_retry_is_idempotent(self):
        with self.assertRaisesRegex(RuntimeError, "synthetic_budget_migration_failure"):
            self.probe(fail=True)
        self.assertEqual(sqlite_snapshot(self.path), self.before)
        self.probe()
        after = sqlite_snapshot(self.path)
        self.assertIn(PROBE_TABLE, after["rows"])
        self.assertEqual(after["rows"][PROBE_TABLE], [])
        self.probe()
        self.assertEqual(sqlite_snapshot(self.path), after)

    def test_partial_and_newer_states_fail_closed_without_repair(self):
        with sqlite3.connect(self.path) as connection:
            connection.execute("INSERT INTO hormuz_schema_migrations (version, state) VALUES (9, 'applying')")
        partial = sqlite_snapshot(self.path)
        for read_only in (False, True):
            with self.assertRaises(StorageSchemaError) as caught:
                UsageStore(self.path, read_only=read_only)
            self.assertEqual(caught.exception.code, "storage_schema_partial_upgrade")
            self.assertEqual(sqlite_snapshot(self.path), partial)
        with sqlite3.connect(self.path) as connection:
            connection.execute("UPDATE hormuz_schema_migrations SET state='applied' WHERE version=9")
        newer = sqlite_snapshot(self.path)
        with self.assertRaises(StorageSchemaError) as caught:
            UsageStore(self.path)
        self.assertEqual(caught.exception.code, "storage_schema_newer_than_binary")
        self.assertEqual(sqlite_snapshot(self.path), newer)


@unittest.skipUnless(os.environ.get("HORMUZ_TEST_FINANCE_PYTHON"), "requires digest-pinned actual finance predecessor")
class SQLiteBudgetPredecessorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "usage.sqlite3"
        self.request = {"backend": "sqlite", "path": str(self.path)}
        self.seeded = finance_predecessor_call({**self.request, "mode": "seed"})
        self.assertEqual(self.seeded["status"], "ready")
        self.assertEqual(self.seeded["runtime_files_verified"], 111)
        self.before = sqlite_snapshot(self.path)
        self.assertTrue(self.seeded["finance_registration"]["receipt_id"])

    def probe(self):
        original = UsageStore._apply_migration

        def apply(connection, version):
            if version == 9:
                connection.execute(
                    f"CREATE TABLE {PROBE_TABLE} (id INTEGER PRIMARY KEY, marker TEXT NOT NULL)"
                )
                return
            original(connection, version)

        with (
            mock.patch.object(UsageStore, "schema_version", 9),
            mock.patch.object(UsageStore, "_apply_migration", side_effect=apply),
        ):
            UsageStore(self.path).verify_ready()

    def replay_request(self, path):
        return {
            "backend": "sqlite", "path": str(path), "mode": "replay",
            "registry_writes": self.seeded["registry_writes"],
            "attribution_write": self.seeded["attribution_write"],
            "outcome_seed": self.seeded["outcome_seed"],
            "finance_registration": self.seeded["finance_registration"],
        }

    def test_actual_predecessor_refuses_probe_state_and_partial_state(self):
        self.probe()
        candidate = sqlite_snapshot(self.path)
        self.assertEqual(
            finance_predecessor_call({**self.request, "mode": "verify"}),
            {"status": "refused", "code": "storage_schema_newer_than_binary"},
        )
        self.assertEqual(sqlite_snapshot(self.path), candidate)
        with sqlite3.connect(self.path) as connection:
            connection.execute("UPDATE hormuz_schema_migrations SET state='applying' WHERE version=9")
        partial = sqlite_snapshot(self.path)
        self.assertEqual(
            finance_predecessor_call({**self.request, "mode": "verify"}),
            {"status": "refused", "code": "storage_schema_partial_upgrade"},
        )
        self.assertEqual(sqlite_snapshot(self.path), partial)

    def test_quiesced_old_pair_restore_preserves_replays_and_finance_receipt(self):
        backup = self.root / "finance-checkpoint.sqlite3"
        sqlite_backup(self.path, backup)
        self.probe()
        retained = sqlite_snapshot(self.path)
        restored = self.root / "restored-finance.sqlite3"
        shutil.copyfile(backup, restored)
        replayed = finance_predecessor_call(self.replay_request(restored))
        self.assertEqual(replayed["unknown_holds"], 1)
        self.assertEqual(replayed["registry_replays"], [write[3] for write in self.seeded["registry_writes"]])
        self.assertEqual(replayed["attribution_replay"], self.seeded["attribution_write"][2])
        self.assertEqual(replayed["outcome_replays"], [item["receipt"] for item in self.seeded["outcome_seed"]["deliveries"]])
        self.assertEqual(replayed["outcome_retention"], self.seeded["outcome_seed"]["retention"])
        self.assertEqual(replayed["finance_replay"], self.seeded["finance_registration"])
        self.assertEqual(sqlite_snapshot(restored), self.before)
        self.assertEqual(sqlite_snapshot(self.path), retained)

    def test_post_checkpoint_probe_write_requires_forward_recovery(self):
        self.probe()
        with sqlite3.connect(self.path) as connection:
            connection.execute(f"INSERT INTO {PROBE_TABLE} (id, marker) VALUES (1, 'candidate-write')")
        after_write = sqlite_snapshot(self.path)
        retained = self.root / "retained-candidate.sqlite3"
        restored = self.root / "forward-recovered.sqlite3"
        sqlite_backup(self.path, retained)
        sqlite_backup(retained, restored)
        with mock.patch.object(UsageStore, "schema_version", 9):
            UsageStore(restored, read_only=True).verify_ready()
        self.assertEqual(sqlite_snapshot(restored), after_write)
        self.assertEqual(
            finance_predecessor_call({"backend": "sqlite", "path": str(restored), "mode": "verify"}),
            {"status": "refused", "code": "storage_schema_newer_than_binary"},
        )
        self.assertEqual(sqlite_snapshot(self.path), after_write)


if __name__ == "__main__":
    unittest.main()
