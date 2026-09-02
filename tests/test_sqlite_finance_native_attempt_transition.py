"""Red-first SQLite 10-to-11 native-attempt finance transition proof."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from hormuz.store import StorageSchemaError, UsageStore

if __package__:
    from ._finance_native_predecessor_fixture import finance_native_predecessor_call
    from ._sqlite import managed_sqlite_connection
    from ._registry_transition_fixture import (
        seed_registry_ledger,
        sqlite_backup,
        sqlite_snapshot,
    )
else:
    from _finance_native_predecessor_fixture import finance_native_predecessor_call
    from _sqlite import managed_sqlite_connection
    from _registry_transition_fixture import (
        seed_registry_ledger,
        sqlite_backup,
        sqlite_snapshot,
    )


PROBE_TABLE = "finance_native_attempt_transition_test_probe"
PROBE_COLUMNS = (
    ("configured_rate_card_state", "TEXT"),
    ("configured_rate_card_id", "TEXT"),
    ("configured_rate_card_version", "INTEGER"),
    ("configured_rate_card_digest", "TEXT"),
    ("configured_rate_card_currency", "TEXT"),
)


def _apply_probe_migration(connection) -> None:
    for name, kind in PROBE_COLUMNS:
        connection.execute(
            f"ALTER TABLE gateway_request_attempts ADD COLUMN {name} {kind}"
        )
    connection.execute(
        f"CREATE TABLE {PROBE_TABLE} "
        "(id INTEGER PRIMARY KEY, marker TEXT NOT NULL)"
    )


class SQLiteFinanceNativeAttemptRedFirstTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "usage.sqlite3"
        self.assertEqual(UsageStore.schema_version, 10)
        store = UsageStore(self.path)
        seed_registry_ledger(store)
        self.before = sqlite_snapshot(self.path)

    def probe(self, *, fail=False, path=None):
        target = path or self.path
        original = UsageStore._apply_migration

        def apply(connection, version):
            if version != 11:
                return original(connection, version)
            _apply_probe_migration(connection)
            if fail:
                raise RuntimeError("synthetic_finance_native_migration_failure")

        with (
            mock.patch.object(UsageStore, "schema_version", 11),
            mock.patch.object(UsageStore, "_apply_migration", side_effect=apply),
        ):
            UsageStore(target).verify_ready()

    def test_missing_migration_is_red_and_leaves_no_partial_state(self):
        with mock.patch.object(UsageStore, "schema_version", 11):
            with self.assertRaises(StorageSchemaError) as caught:
                UsageStore(self.path)
        self.assertEqual(
            caught.exception.code,
            "storage_schema_migration_unsupported",
        )
        self.assertEqual(sqlite_snapshot(self.path), self.before)
        UsageStore(self.path).verify_ready()

    def test_probe_failure_rolls_back_and_retry_is_idempotent(self):
        with self.assertRaisesRegex(
            RuntimeError,
            "synthetic_finance_native_migration_failure",
        ):
            self.probe(fail=True)
        self.assertEqual(sqlite_snapshot(self.path), self.before)
        self.probe()
        after = sqlite_snapshot(self.path)
        self.assertIn(PROBE_TABLE, after["rows"])
        self.assertEqual(after["rows"][PROBE_TABLE], [])
        self.probe()
        self.assertEqual(sqlite_snapshot(self.path), after)

    def test_partial_and_newer_states_fail_closed_without_repair(self):
        with managed_sqlite_connection(self.path) as connection:
            connection.execute(
                "INSERT INTO hormuz_schema_migrations (version, state) "
                "VALUES (11, 'applying')"
            )
        partial = sqlite_snapshot(self.path)
        for read_only in (False, True):
            with self.subTest(read_only=read_only), self.assertRaises(
                StorageSchemaError
            ) as caught:
                UsageStore(self.path, read_only=read_only)
            self.assertEqual(
                caught.exception.code,
                "storage_schema_partial_upgrade",
            )
            self.assertEqual(sqlite_snapshot(self.path), partial)
        with managed_sqlite_connection(self.path) as connection:
            connection.execute(
                "UPDATE hormuz_schema_migrations SET state='applied' "
                "WHERE version=11"
            )
        newer = sqlite_snapshot(self.path)
        with self.assertRaises(StorageSchemaError) as caught:
            UsageStore(self.path)
        self.assertEqual(caught.exception.code, "storage_schema_newer_than_binary")
        self.assertEqual(sqlite_snapshot(self.path), newer)


@unittest.skipUnless(
    os.environ.get("HORMUZ_TEST_FINANCE_NATIVE_PYTHON"),
    "requires digest-pinned accepted budget-runtime predecessor",
)
class SQLiteFinanceNativeAttemptPredecessorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "usage.sqlite3"
        self.request = {"backend": "sqlite", "path": str(self.path)}
        self.seeded = finance_native_predecessor_call(
            {**self.request, "mode": "seed"}
        )
        self.assertEqual(self.seeded["status"], "ready")
        self.assertEqual(self.seeded["runtime_files_verified"], 141)
        self.before = sqlite_snapshot(self.path)
        self.assertTrue(self.seeded["finance_registration"]["receipt_id"])
        self.assertEqual(
            self.seeded["budget_registration"]["active_version"],
            1,
        )
        self.assertEqual(
            self.seeded["provider_reliability"]["metric_count"],
            2,
        )
        self.assertEqual(
            len(self.before["rows"]["portfolio_work_budget_plan_versions"]),
            1,
        )
        self.assertEqual(
            len(self.before["rows"]["gateway_provider_attempt_metrics"]),
            2,
        )
        self.assertEqual(
            len(self.before["rows"]["gateway_provider_failover_events"]),
            1,
        )

    def probe(self, *, path=None):
        target = path or self.path
        original = UsageStore._apply_migration

        def apply(connection, version):
            if version == 11:
                _apply_probe_migration(connection)
                return
            original(connection, version)

        with (
            mock.patch.object(UsageStore, "schema_version", 11),
            mock.patch.object(UsageStore, "_apply_migration", side_effect=apply),
        ):
            UsageStore(target).verify_ready()

    def replay_request(self, path):
        return {
            "backend": "sqlite",
            "path": str(path),
            "mode": "replay",
            "registry_writes": self.seeded["registry_writes"],
            "attribution_write": self.seeded["attribution_write"],
            "outcome_seed": self.seeded["outcome_seed"],
            "finance_registration": self.seeded["finance_registration"],
        }

    def test_exact_predecessor_refuses_probe_and_partial_states(self):
        self.probe()
        candidate = sqlite_snapshot(self.path)
        self.assertEqual(
            finance_native_predecessor_call({**self.request, "mode": "verify"}),
            {"status": "refused", "code": "storage_schema_newer_than_binary"},
        )
        self.assertEqual(sqlite_snapshot(self.path), candidate)
        with managed_sqlite_connection(self.path) as connection:
            connection.execute(
                "UPDATE hormuz_schema_migrations SET state='applying' "
                "WHERE version=11"
            )
        partial = sqlite_snapshot(self.path)
        self.assertEqual(
            finance_native_predecessor_call({**self.request, "mode": "verify"}),
            {"status": "refused", "code": "storage_schema_partial_upgrade"},
        )
        self.assertEqual(sqlite_snapshot(self.path), partial)

    def test_quiesced_old_pair_restore_preserves_all_accepted_state(self):
        backup = self.root / "accepted-main-checkpoint.sqlite3"
        sqlite_backup(self.path, backup)
        self.probe()
        retained = sqlite_snapshot(self.path)
        restored = self.root / "restored-accepted-main.sqlite3"
        shutil.copyfile(backup, restored)
        replayed = finance_native_predecessor_call(self.replay_request(restored))
        self.assertEqual(replayed["unknown_holds"], self.seeded["unknown_holds"])
        self.assertEqual(
            replayed["registry_replays"],
            [write[3] for write in self.seeded["registry_writes"]],
        )
        self.assertEqual(
            replayed["attribution_replay"],
            self.seeded["attribution_write"][2],
        )
        self.assertEqual(
            replayed["outcome_replays"],
            [item["receipt"] for item in self.seeded["outcome_seed"]["deliveries"]],
        )
        self.assertEqual(
            replayed["outcome_retention"],
            self.seeded["outcome_seed"]["retention"],
        )
        self.assertEqual(
            replayed["finance_replay"],
            self.seeded["finance_registration"],
        )
        self.assertEqual(sqlite_snapshot(restored), self.before)
        self.assertEqual(sqlite_snapshot(self.path), retained)

    def test_post_checkpoint_write_requires_forward_recovery(self):
        self.probe()
        with managed_sqlite_connection(self.path) as connection:
            connection.execute(
                f"INSERT INTO {PROBE_TABLE} (id, marker) "
                "VALUES (1, 'candidate-write')"
            )
        after_write = sqlite_snapshot(self.path)
        retained = self.root / "retained-candidate.sqlite3"
        restored = self.root / "forward-recovered.sqlite3"
        sqlite_backup(self.path, retained)
        sqlite_backup(retained, restored)
        with mock.patch.object(UsageStore, "schema_version", 11):
            UsageStore(restored, read_only=True).verify_ready()
        self.assertEqual(sqlite_snapshot(restored), after_write)
        self.assertEqual(
            finance_native_predecessor_call(
                {"backend": "sqlite", "path": str(restored), "mode": "verify"}
            ),
            {"status": "refused", "code": "storage_schema_newer_than_binary"},
        )
        self.assertEqual(sqlite_snapshot(self.path), after_write)


if __name__ == "__main__":
    unittest.main()
