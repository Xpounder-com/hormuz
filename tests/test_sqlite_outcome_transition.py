"""Real #218 transitions using actual attribution/released-v1 predecessors."""

from __future__ import annotations

import copy
from dataclasses import replace
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from hormuz.store import StorageSchemaError, UsageStore
from hormuz._outcome_schema import TABLE_DDL, sqlite_statements
from hormuz._finance_schema import TABLE_DDL as FINANCE_TABLES
from hormuz._budget_schema import TABLE_DDL as BUDGET_TABLES
from hormuz._provider_reliability_schema import TABLE_DDL as PROVIDER_TABLES
from hormuz.portfolio_repository import create_portfolio_repository
from hormuz.portfolio_service import PortfolioService
from hormuz.portfolio_wire import OUTCOMES
if __package__:
    from ._sqlite import managed_sqlite_connection
else:
    from _sqlite import managed_sqlite_connection

if __package__:
    from ._outcome_predecessor_fixture import attribution_predecessor_call
    from ._outcome_fixture import replay_outcome_metadata, seed_outcome_metadata
    from ._portfolio_fixture import ADMIN, registry_config
    from ._registry_transition_fixture import ledger_observation, released_v1_call, seed_registry_ledger, sqlite_backup, sqlite_snapshot
else:
    from _outcome_predecessor_fixture import attribution_predecessor_call
    from _outcome_fixture import replay_outcome_metadata, seed_outcome_metadata
    from _portfolio_fixture import ADMIN, registry_config
    from _registry_transition_fixture import ledger_observation, released_v1_call, seed_registry_ledger, sqlite_backup, sqlite_snapshot


@unittest.skipUnless(os.environ.get("HORMUZ_TEST_ATTRIBUTION_PYTHON"), "requires digest-pinned actual attribution predecessor")
class SQLiteOutcomeTransitionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "usage.sqlite3"
        self.assertEqual(UsageStore.schema_version, 10)
        self.predecessor_request = {"backend": "sqlite", "path": str(self.path)}
        self.seeded = attribution_predecessor_call({**self.predecessor_request, "mode": "seed"})
        self.assertEqual(self.seeded["status"], "ready")
        self.assertEqual(self.seeded["runtime_files_verified"], 99)
        self.before = sqlite_snapshot(self.path)
        self.assertEqual(len(self.before["rows"]), 20)
        self.assertTrue(all(rows for table, rows in self.before["rows"].items() if table.startswith("portfolio_")))

    def upgrade(self, *, fail=False):
        original = UsageStore._apply_migration

        def apply(connection, version):
            self.assertIn(version, (7, 8, 9, 10))
            if fail and version == 7:
                connection.execute(sqlite_statements()[0])
                raise RuntimeError("synthetic_outcome_migration_failure")
            return original(connection, version)

        with mock.patch.object(UsageStore, "_apply_migration", side_effect=apply):
            UsageStore(self.path).verify_ready()

    def assert_prior_state_preserved(self):
        current = copy.deepcopy(sqlite_snapshot(self.path))
        added = set(TABLE_DDL) | set(FINANCE_TABLES) | set(BUDGET_TABLES) | set(PROVIDER_TABLES)
        current["objects"] = [
            row for row in current["objects"]
            if row[2] not in added and not row[1].startswith("gateway_provider_")
        ]
        current["rows"] = {table: rows for table, rows in current["rows"].items() if table not in added}
        current["rows"]["hormuz_schema_migrations"] = [
            row
            for row in current["rows"]["hormuz_schema_migrations"]
            if row[0] not in {7, 8, 9, 10}
        ]
        self.assertEqual(current, self.before)

    def test_sqlite_outcome_real_migration_and_missing_following_migration(self):
        self.upgrade()
        self.assert_prior_state_preserved()
        self.assertEqual(len(sqlite_snapshot(self.path)["rows"]), 38)
        before = sqlite_snapshot(self.path)
        with mock.patch.object(UsageStore, "schema_version", 11):
            with self.assertRaises(StorageSchemaError) as caught:
                UsageStore(self.path)
        self.assertEqual(caught.exception.code, "storage_schema_migration_unsupported")
        self.assertEqual(sqlite_snapshot(self.path), before)

    def test_sqlite_outcome_real_partial_ddl_failure_retry_preserves_all_predecessor_state(self):
        with self.assertRaisesRegex(RuntimeError, "synthetic_outcome_migration_failure"):
            self.upgrade(fail=True)
        self.assertEqual(sqlite_snapshot(self.path), self.before)
        self.upgrade()
        self.assert_prior_state_preserved()
        current = sqlite_snapshot(self.path)
        self.upgrade()
        self.assertEqual(sqlite_snapshot(self.path), current)

    def test_sqlite_outcome_partial_state_refuses_before_repair(self):
        with managed_sqlite_connection(self.path) as connection:
            connection.execute("INSERT INTO hormuz_schema_migrations (version, state) VALUES (7, 'applying')")
        before = sqlite_snapshot(self.path)
        for read_only in (False, True):
            with self.assertRaises(StorageSchemaError) as caught:
                UsageStore(self.path, read_only=read_only)
            self.assertEqual(caught.exception.code, "storage_schema_partial_upgrade")
            self.assertEqual(sqlite_snapshot(self.path), before)

    @unittest.skipUnless(os.environ.get("HORMUZ_TEST_V1_PYTHON"), "requires digest-pinned released-v1 interpreter")
    def test_sqlite_outcome_actual_old_binaries_refuse_next_and_partial_schema(self):
        self.upgrade()
        before = sqlite_snapshot(self.path)
        request = {**self.predecessor_request, "mode": "verify"}
        UsageStore(self.path).verify_ready()
        for driver in (attribution_predecessor_call, released_v1_call):
            self.assertEqual(driver(request), {"status": "refused", "code": "storage_schema_newer_than_binary"})
        self.assertEqual(sqlite_snapshot(self.path), before)
        with managed_sqlite_connection(self.path) as connection:
            connection.execute("UPDATE hormuz_schema_migrations SET state='applying' WHERE version=7")
        partial = sqlite_snapshot(self.path)
        for driver in (attribution_predecessor_call, released_v1_call):
            self.assertEqual(driver(request), {"status": "refused", "code": "storage_schema_partial_upgrade"})
        self.assertEqual(sqlite_snapshot(self.path), partial)

    def test_sqlite_outcome_old_pair_restore_preserves_both_replays_cursors_and_facts(self):
        backup = self.root / "attribution-checkpoint.sqlite3"
        sqlite_backup(self.path, backup)
        self.upgrade()
        retained = sqlite_snapshot(self.path)
        restored = self.root / "restored-attribution.sqlite3"
        sqlite_backup(backup, restored)
        request = {"backend": "sqlite", "path": str(restored), "mode": "replay",
                   "registry_writes": self.seeded["registry_writes"], "attribution_write": self.seeded["attribution_write"]}
        replayed = attribution_predecessor_call(request)
        self.assertEqual(replayed["unknown_holds"], 1)
        self.assertEqual(replayed["registry_replays"], [write[3] for write in self.seeded["registry_writes"]])
        self.assertEqual(replayed["attribution_replay"], self.seeded["attribution_write"][2])
        self.assertEqual(sqlite_snapshot(restored), self.before)
        for resource in ("registry", "attribution"):
            page = attribution_predecessor_call({**request, "mode": "page", "resource": resource,
                                                "cursor": self.seeded[resource + "_page"]["next_cursor"]})["page"]
            self.assertEqual(len(page["items"]), 1)
            self.assertIsNone(page["next_cursor"])
            if resource == "registry":
                self.assertEqual(page["items"], [self.seeded["registry_writes"][0][3][1]])
        facts = attribution_predecessor_call({**request, "mode": "facts", "attempt_id": self.seeded["attempt_id"]})["facts"]
        self.assertEqual(facts["provider_reported_model"], "recovery-actual-v1")
        self.assertEqual(facts["attribution"], self.seeded["attribution_write"][2][1])
        self.assertEqual(sqlite_snapshot(self.path), retained)

    def test_sqlite_outcome_post_checkpoint_writes_require_forward_recovery(self):
        self.upgrade()
        seed_registry_ledger(UsageStore(self.path))
        config = replace(registry_config(self.root), database_path=self.path)
        seeded = seed_outcome_metadata(config)
        after_write = sqlite_snapshot(self.path)
        self.assertTrue(all(after_write["rows"][table] for table in TABLE_DDL))
        retained = self.root / "retained-next-state.sqlite3"
        restored = self.root / "forward-recovered.sqlite3"
        sqlite_backup(self.path, retained)
        sqlite_backup(retained, restored)
        self.assertEqual(ledger_observation(UsageStore(restored))["unknown_holds"], 2)
        self.assertEqual(sqlite_snapshot(restored), after_write)
        restored_config = replace(config, database_path=restored)
        receipts, retention = replay_outcome_metadata(restored_config, seeded)
        self.assertEqual(receipts, [item["receipt"] for item in seeded["deliveries"]])
        self.assertEqual(retention, seeded["retention"])
        self.assertEqual(sqlite_snapshot(restored), after_write)
        service = PortfolioService(restored_config, create_portfolio_repository(restored_config))
        page = service.dispatch(ADMIN, "GET", OUTCOMES, query="cursor=" + seeded["page"]["next_cursor"])[1]
        self.assertEqual(page["as_of"], seeded["page"]["as_of"])
        self.assertEqual(len(page["items"]), 1)
        self.assertIsNone(page["next_cursor"])
        self.assertEqual(sqlite_snapshot(self.path), after_write)
        self.assertNotEqual(after_write, self.before)
        self.assertEqual(attribution_predecessor_call({"backend": "sqlite", "path": str(restored), "mode": "verify"}),
                         {"status": "refused", "code": "storage_schema_newer_than_binary"})


if __name__ == "__main__":
    unittest.main()
