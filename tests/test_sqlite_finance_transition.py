"""Red-first #8 probes using real outcome/released-v1 predecessors."""

from __future__ import annotations

import copy
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from hormuz.store import StorageSchemaError, UsageStore

if __package__:
    from ._finance_predecessor_fixture import outcome_predecessor_call
    from ._registry_transition_fixture import ledger_observation, released_v1_call, seed_registry_ledger, sqlite_backup, sqlite_snapshot
else:
    from _finance_predecessor_fixture import outcome_predecessor_call
    from _registry_transition_fixture import ledger_observation, released_v1_call, seed_registry_ledger, sqlite_backup, sqlite_snapshot


PROBE = "finance_transition_test_probe"


@unittest.skipUnless(os.environ.get("HORMUZ_TEST_OUTCOME_PYTHON"), "requires digest-pinned actual outcome predecessor")
class SQLiteFinanceTransitionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "usage.sqlite3"
        self.assertEqual(UsageStore.schema_version, 7)
        self.predecessor_request = {"backend": "sqlite", "path": str(self.path)}
        self.seeded = outcome_predecessor_call({**self.predecessor_request, "mode": "seed"})
        self.assertEqual(self.seeded["status"], "ready")
        self.assertEqual(self.seeded["runtime_files_verified"], 105)
        self.before = sqlite_snapshot(self.path)
        self.assertEqual(len(self.before["rows"]), 29)
        self.assertTrue(all(rows for table, rows in self.before["rows"].items() if table.startswith("portfolio_")))

    def probe(self, *, fail=False):
        original = UsageStore._apply_migration

        def apply(connection, version):
            if version != 8:
                return original(connection, version)
            connection.execute(f"CREATE TABLE {PROBE} (organization_id TEXT NOT NULL, id TEXT PRIMARY KEY)")
            if fail:
                raise RuntimeError("synthetic_finance_migration_failure")

        with mock.patch.object(UsageStore, "schema_version", 8), mock.patch.object(UsageStore, "_apply_migration", side_effect=apply):
            UsageStore(self.path).verify_ready()

    def assert_prior_state_preserved(self):
        current = copy.deepcopy(sqlite_snapshot(self.path))
        current["objects"] = [row for row in current["objects"] if row[2] != PROBE]
        current["rows"].pop(PROBE, None)
        current["rows"]["hormuz_schema_migrations"] = [row for row in current["rows"]["hormuz_schema_migrations"] if row[0] != 8]
        self.assertEqual(current, self.before)

    def test_sqlite_finance_migration_is_red_until_implementation(self):
        with mock.patch.object(UsageStore, "schema_version", 8):
            with self.assertRaises(StorageSchemaError) as caught:
                UsageStore(self.path)
        self.assertEqual(caught.exception.code, "storage_schema_migration_unsupported")
        self.assertEqual(sqlite_snapshot(self.path), self.before)

    def test_sqlite_finance_probe_failure_retry_preserves_all_predecessor_state(self):
        with self.assertRaisesRegex(RuntimeError, "synthetic_finance_migration_failure"):
            self.probe(fail=True)
        self.assertEqual(sqlite_snapshot(self.path), self.before)
        self.probe()
        self.assert_prior_state_preserved()
        current = sqlite_snapshot(self.path)
        self.probe()
        self.assertEqual(sqlite_snapshot(self.path), current)

    def test_sqlite_finance_partial_state_refuses_before_repair(self):
        with sqlite3.connect(self.path) as connection:
            connection.execute("INSERT INTO hormuz_schema_migrations (version, state) VALUES (8, 'applying')")
        before = sqlite_snapshot(self.path)
        for read_only in (False, True):
            with self.assertRaises(StorageSchemaError) as caught:
                UsageStore(self.path, read_only=read_only)
            self.assertEqual(caught.exception.code, "storage_schema_partial_upgrade")
            self.assertEqual(sqlite_snapshot(self.path), before)

    @unittest.skipUnless(os.environ.get("HORMUZ_TEST_V1_PYTHON"), "requires digest-pinned released-v1 interpreter")
    def test_sqlite_finance_actual_old_binaries_refuse_next_and_partial_schema(self):
        self.probe()
        before = sqlite_snapshot(self.path)
        request = {**self.predecessor_request, "mode": "verify"}
        with self.assertRaises(StorageSchemaError) as caught:
            UsageStore(self.path)
        self.assertEqual(caught.exception.code, "storage_schema_newer_than_binary")
        for driver in (outcome_predecessor_call, released_v1_call):
            self.assertEqual(driver(request), {"status": "refused", "code": "storage_schema_newer_than_binary"})
        self.assertEqual(sqlite_snapshot(self.path), before)
        with sqlite3.connect(self.path) as connection:
            connection.execute("UPDATE hormuz_schema_migrations SET state='applying' WHERE version=8")
        partial = sqlite_snapshot(self.path)
        for driver in (outcome_predecessor_call, released_v1_call):
            self.assertEqual(driver(request), {"status": "refused", "code": "storage_schema_partial_upgrade"})
        self.assertEqual(sqlite_snapshot(self.path), partial)

    def test_sqlite_finance_old_pair_restore_preserves_both_replays_cursors_and_facts(self):
        backup = self.root / "outcome-checkpoint.sqlite3"
        sqlite_backup(self.path, backup)
        self.probe()
        retained = sqlite_snapshot(self.path)
        restored = self.root / "restored-outcome.sqlite3"
        sqlite_backup(backup, restored)
        request = {"backend": "sqlite", "path": str(restored), "mode": "replay",
                   "registry_writes": self.seeded["registry_writes"], "attribution_write": self.seeded["attribution_write"], "outcome_seed": self.seeded["outcome_seed"]}
        replayed = outcome_predecessor_call(request)
        self.assertEqual(replayed["unknown_holds"], 1)
        self.assertEqual(replayed["registry_replays"], [write[3] for write in self.seeded["registry_writes"]])
        self.assertEqual(replayed["attribution_replay"], self.seeded["attribution_write"][2])
        self.assertEqual(replayed["outcome_replays"], [item["receipt"] for item in self.seeded["outcome_seed"]["deliveries"]])
        self.assertEqual(replayed["outcome_retention"], self.seeded["outcome_seed"]["retention"])
        self.assertEqual(sqlite_snapshot(restored), self.before)
        for resource in ("registry", "attribution", "outcome"):
            page = outcome_predecessor_call({**request, "mode": "page", "resource": resource,
                                                "cursor": self.seeded[resource + "_page"]["next_cursor"]})["page"]
            self.assertEqual(len(page["items"]), 1)
            self.assertIsNone(page["next_cursor"])
            if resource == "registry":
                self.assertEqual(page["items"], [self.seeded["registry_writes"][0][3][1]])
        facts = outcome_predecessor_call({**request, "mode": "facts", "attempt_id": self.seeded["attempt_id"]})["facts"]
        self.assertEqual(facts["provider_reported_model"], "recovery-actual-v1")
        self.assertEqual(facts["attribution"], self.seeded["attribution_write"][2][1])
        self.assertEqual(sqlite_snapshot(self.path), retained)

    def test_sqlite_finance_post_checkpoint_writes_require_forward_recovery(self):
        self.probe()
        with mock.patch.object(UsageStore, "schema_version", 8):
            seed_registry_ledger(UsageStore(self.path))
            with sqlite3.connect(self.path) as connection:
                connection.execute(f"INSERT INTO {PROBE} VALUES ('acme', 'synthetic-post-checkpoint-write')")
            after_write = sqlite_snapshot(self.path)
            retained = self.root / "retained-next-state.sqlite3"
            restored = self.root / "forward-recovered.sqlite3"
            sqlite_backup(self.path, retained)
            sqlite_backup(retained, restored)
            self.assertEqual(ledger_observation(UsageStore(restored))["unknown_holds"], 2)
            self.assertEqual(sqlite_snapshot(restored), after_write)
        self.assertEqual(sqlite_snapshot(self.path), after_write)
        self.assertNotEqual(after_write, self.before)
        self.assertEqual(outcome_predecessor_call({"backend": "sqlite", "path": str(restored), "mode": "verify"}),
                         {"status": "refused", "code": "storage_schema_newer_than_binary"})


if __name__ == "__main__":
    unittest.main()
