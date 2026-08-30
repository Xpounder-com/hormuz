"""Pre-implementation probes, not proof of a real registry migration."""

from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest import mock

from hormuz.contracts import contract_manifest
from hormuz.store import StorageSchemaError, UsageStore
if __package__:
    from ._registry_transition_fixture import (
        PROBE_TABLE, ledger_observation, released_v1_call, seed_registry_ledger, sqlite_backup, sqlite_snapshot,
    )
else:
    from _registry_transition_fixture import (
        PROBE_TABLE, ledger_observation, released_v1_call, seed_registry_ledger, sqlite_backup, sqlite_snapshot,
    )


class SQLiteRegistryTransitionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / "usage.sqlite3"
        seed_registry_ledger(UsageStore(self.path))
        self.before = sqlite_snapshot(self.path)
        self.assertEqual(len(self.before["rows"]), 10)

    def probe(self, *, fail: bool = False) -> None:
        # Deliberately NOT registry DDL: exercise the existing transaction owner.
        def apply(connection, version):
            self.assertEqual(version, 5)
            connection.execute(f"CREATE TABLE {PROBE_TABLE} (id INTEGER PRIMARY KEY)")
            connection.execute(f"INSERT INTO {PROBE_TABLE} VALUES (1)")
            if fail:
                raise RuntimeError("synthetic_migration_failure")

        with (
            mock.patch.object(UsageStore, "schema_version", 5),
            mock.patch.object(UsageStore, "_apply_migration", side_effect=apply),
        ):
            UsageStore(self.path).verify_ready()

    def assert_v1_preserved(self) -> None:
        after = copy.deepcopy(sqlite_snapshot(self.path))
        after["objects"] = [row for row in after["objects"] if row[2] != PROBE_TABLE]
        after["rows"].pop(PROBE_TABLE, None)
        after["rows"]["hormuz_schema_migrations"] = [
            row for row in after["rows"]["hormuz_schema_migrations"] if row[0] != 5
        ]
        self.assertEqual(after, self.before)

    def test_registry_sqlite_migration_is_red_until_feature_implementation(self) -> None:
        self.assertEqual(UsageStore.schema_version, 4)
        for _ in range(2):
            with mock.patch.object(UsageStore, "schema_version", 5):
                with self.assertRaises(StorageSchemaError) as raised:
                    UsageStore(self.path)
            self.assertEqual(raised.exception.code, "storage_schema_migration_unsupported")
            self.assertEqual(sqlite_snapshot(self.path), self.before)

    def test_sqlite_probe_failure_rolls_back_and_retry_preserves_v1_rows(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "synthetic_migration_failure"):
            self.probe(fail=True)
        self.assertEqual(sqlite_snapshot(self.path), self.before)
        self.probe()
        after = sqlite_snapshot(self.path)
        self.assertEqual(after["rows"][PROBE_TABLE], [(1,)])
        self.probe()  # Applied migration is not executed again.
        self.assertEqual(sqlite_snapshot(self.path), after)
        self.assert_v1_preserved()

    def test_sqlite_partial_upgrade_refuses_readers_and_writers_without_changes(self) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute("INSERT INTO hormuz_schema_migrations (version, state) VALUES (5, 'applying')")
        before = sqlite_snapshot(self.path)
        for read_only in (False, True):
            with self.assertRaises(StorageSchemaError) as raised:
                UsageStore(self.path, read_only=read_only)
            self.assertEqual(raised.exception.code, "storage_schema_partial_upgrade")
            self.assertEqual(sqlite_snapshot(self.path), before)

    @unittest.skipUnless(os.environ.get("HORMUZ_TEST_V1_PYTHON"), "requires the digest-pinned released v1 interpreter")
    def test_released_sqlite_binary_preserves_old_state_and_refuses_newer_or_partial_state(self) -> None:
        self.path = self.root / "released.sqlite3"
        request = {"backend": "sqlite", "path": str(self.path), "mode": "seed"}
        seeded = released_v1_call(request)
        self.assertEqual(seeded["status"], "ready")
        self.assertEqual(seeded["manifest"], contract_manifest())
        self.assertEqual(seeded["unknown_holds"], 1)
        self.before = sqlite_snapshot(self.path)
        self.assertEqual(ledger_observation(UsageStore(self.path)), {
            key: seeded[key] for key in ("unknown_holds", "audit_sequence", "usage_events")
        })
        self.assertEqual(sqlite_snapshot(self.path), self.before)
        self.probe()
        for state, expected in (("applied", "storage_schema_newer_than_binary"), ("applying", "storage_schema_partial_upgrade")):
            with sqlite3.connect(self.path) as connection:
                connection.execute("UPDATE hormuz_schema_migrations SET state = ? WHERE version = 5", (state,))
            before = sqlite_snapshot(self.path)
            self.assertEqual(released_v1_call({**request, "mode": "verify"}), {"status": "refused", "code": expected})
            self.assertEqual(sqlite_snapshot(self.path), before)
            self.assert_v1_preserved()

    @unittest.skipUnless(os.environ.get("HORMUZ_TEST_V1_PYTHON"), "requires the digest-pinned released v1 interpreter")
    def test_sqlite_quiesced_verified_pair_restore_keeps_unknown_holds(self) -> None:
        backup = self.root / "before.sqlite3"
        sqlite_backup(self.path, backup)
        self.assertEqual(sqlite_snapshot(backup), self.before)
        digest = hashlib.sha256(backup.read_bytes()).hexdigest()
        self.probe()
        candidate = self.root / "candidate-retained.sqlite3"
        sqlite_backup(self.path, candidate)
        self.assertEqual(sqlite_snapshot(candidate), sqlite_snapshot(self.path))
        # No writes after the checkpoint. Restore only into a fresh test path.
        restored = self.root / "restored.sqlite3"
        shutil.copyfile(backup, restored)
        self.assertEqual(hashlib.sha256(restored.read_bytes()).hexdigest(), digest)
        self.assertEqual(sqlite_snapshot(restored), self.before)
        observed = released_v1_call({"backend": "sqlite", "path": str(restored), "mode": "verify"})
        self.assertEqual(observed["status"], "ready")
        self.assertEqual(observed["unknown_holds"], 1)
        self.assertEqual(sqlite_snapshot(restored), self.before)
        self.assertEqual(sqlite_snapshot(candidate), sqlite_snapshot(self.path))

    def test_sqlite_candidate_writes_remain_present_for_forward_recovery(self) -> None:
        self.probe()
        with mock.patch.object(UsageStore, "schema_version", 5):
            candidate = UsageStore(self.path)
            seed_registry_ledger(candidate)
            self.assertEqual(ledger_observation(candidate)["unknown_holds"], 2)
            after_write = sqlite_snapshot(self.path)
            # Retry/reopen keeps both pre- and post-checkpoint writes and holds.
            self.probe()
            self.assertEqual(sqlite_snapshot(self.path), after_write)
        with self.assertRaises(StorageSchemaError) as raised:
            UsageStore(self.path)
        self.assertEqual(raised.exception.code, "storage_schema_newer_than_binary")
        self.assertEqual(sqlite_snapshot(self.path), after_write)
        self.assertNotEqual(after_write["rows"]["gateway_usage_events"], self.before["rows"]["gateway_usage_events"])


if __name__ == "__main__":
    unittest.main()
