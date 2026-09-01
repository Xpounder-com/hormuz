"""Red-first SQLite 8-to-9 budget transition and predecessor recovery proof."""

from __future__ import annotations

import copy
from dataclasses import replace
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest import mock

import hormuz._portfolio_sql as portfolio_sql_module
from hormuz._budget_schema import TABLE_DDL, sqlite_statements
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


class SQLiteBudgetTransitionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "usage.sqlite3"
        self.assertEqual(UsageStore.schema_version, 10)
        with (
            mock.patch.object(UsageStore, "schema_version", 8),
            mock.patch.object(portfolio_sql_module, "SQLITE_SCHEMA_VERSION", 8),
            mock.patch("hormuz.store.prepare_work_budget", return_value=None),
            mock.patch("hormuz.store.enforce_and_bind_work_budget", return_value=None),
        ):
            store = UsageStore(self.path)
            seed_registry_ledger(store)
            seed_finance(replace(registry_config(self.root), database_path=self.path))
        self.before = sqlite_snapshot(self.path)
        self.assertEqual(len(self.before["rows"]), 31)
        self.assertTrue(self.before["rows"]["gateway_usage_events"])
        self.assertTrue(self.before["rows"]["portfolio_finance_rate_cards"])

    def upgrade(self, *, fail=False, path=None):
        target = path or self.path
        original = UsageStore._apply_migration

        def apply(connection, version):
            self.assertEqual(version, 9)
            if fail:
                connection.execute(sqlite_statements()[0])
                raise RuntimeError("synthetic_budget_migration_failure")
            return original(connection, version)

        with (
            mock.patch.object(UsageStore, "schema_version", 9),
            mock.patch.object(UsageStore, "_apply_migration", side_effect=apply),
        ):
            UsageStore(target).verify_ready()

    def assert_prior_state_preserved(self):
        current = copy.deepcopy(sqlite_snapshot(self.path))
        current["objects"] = [row for row in current["objects"] if row[2] not in TABLE_DDL]
        current["rows"] = {table: rows for table, rows in current["rows"].items() if table not in TABLE_DDL}
        current["rows"]["hormuz_schema_migrations"] = [
            row for row in current["rows"]["hormuz_schema_migrations"] if row[0] != 9
        ]
        self.assertEqual(current, self.before)

    def test_real_migration_preserves_predecessor_and_missing_following_migration_is_safe(self):
        self.upgrade()
        self.assert_prior_state_preserved()
        current = sqlite_snapshot(self.path)
        self.assertEqual(len(current["rows"]), 36)
        self.assertTrue(all(not current["rows"][table] for table in TABLE_DDL))
        with mock.patch.object(UsageStore, "schema_version", 11):
            with self.assertRaises(StorageSchemaError) as caught:
                UsageStore(self.path)
        self.assertEqual(caught.exception.code, "storage_schema_migration_unsupported")
        self.assertEqual(sqlite_snapshot(self.path), current)

    def test_real_partial_ddl_failure_rolls_back_and_retry_is_idempotent(self):
        with self.assertRaisesRegex(RuntimeError, "synthetic_budget_migration_failure"):
            self.upgrade(fail=True)
        self.assertEqual(sqlite_snapshot(self.path), self.before)
        self.upgrade()
        self.assert_prior_state_preserved()
        after = sqlite_snapshot(self.path)
        self.upgrade()
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
            connection.execute("UPDATE hormuz_schema_migrations SET version=11, state='applied' WHERE version=9")
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

    def upgrade(self, path=None):
        UsageStore(path or self.path).verify_ready()

    def replay_request(self, path):
        return {
            "backend": "sqlite", "path": str(path), "mode": "replay",
            "registry_writes": self.seeded["registry_writes"],
            "attribution_write": self.seeded["attribution_write"],
            "outcome_seed": self.seeded["outcome_seed"],
            "finance_registration": self.seeded["finance_registration"],
        }

    def test_actual_predecessor_refuses_budget_state_and_partial_state(self):
        self.upgrade()
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
        self.upgrade()
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

    def test_post_checkpoint_budget_write_requires_forward_recovery(self):
        self.upgrade()
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "INSERT INTO portfolio_work_budget_audit_events "
                "(organization_id,event_id,sequence,actor_id,operation,entity_id,entity_version,reason_code,occurred_at) "
                "VALUES ('acme',?,1,'alice','report','candidate-budget',1,'observed','2026-08-31T12:00:00Z')",
                ("f" * 32,),
            )
        after_write = sqlite_snapshot(self.path)
        retained = self.root / "retained-candidate.sqlite3"
        restored = self.root / "forward-recovered.sqlite3"
        sqlite_backup(self.path, retained)
        sqlite_backup(retained, restored)
        UsageStore(restored, read_only=True).verify_ready()
        self.assertEqual(sqlite_snapshot(restored), after_write)
        self.assertEqual(
            finance_predecessor_call({"backend": "sqlite", "path": str(restored), "mode": "verify"}),
            {"status": "refused", "code": "storage_schema_newer_than_binary"},
        )
        self.assertEqual(sqlite_snapshot(self.path), after_write)


if __name__ == "__main__":
    unittest.main()
