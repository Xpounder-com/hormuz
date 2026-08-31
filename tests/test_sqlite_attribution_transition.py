"""Actual #216 migration with real registry and released-v1 predecessor proof."""

from __future__ import annotations

import copy
from dataclasses import replace
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from hormuz.portfolio_repository import RegistryRepository
from hormuz.portfolio_service import PortfolioService
from hormuz.portfolio_wire import SCOPES
from hormuz.store import StorageSchemaError, UsageStore
from hormuz._attribution_schema import TABLE_DDL
from hormuz._outcome_schema import TABLE_DDL as OUTCOME_TABLES
from hormuz._finance_schema import TABLE_DDL as FINANCE_TABLES
from hormuz.portfolio_repository import create_portfolio_repository
from hormuz.portfolio_wire import ATTRIBUTIONS, canonical

if __package__:
    from ._attribution_predecessor_fixture import registry_predecessor_call
    from ._attribution_fixture import seed_attribution_metadata
    from ._portfolio_fixture import ADMIN, registry_config, seed_registry_metadata
    from ._registry_transition_fixture import ledger_observation, released_v1_call, seed_registry_ledger, sqlite_backup, sqlite_snapshot
else:
    from _attribution_predecessor_fixture import registry_predecessor_call
    from _attribution_fixture import seed_attribution_metadata
    from _portfolio_fixture import ADMIN, registry_config, seed_registry_metadata
    from _registry_transition_fixture import ledger_observation, released_v1_call, seed_registry_ledger, sqlite_backup, sqlite_snapshot


@unittest.skipUnless(os.environ.get("HORMUZ_TEST_REGISTRY_PYTHON"), "requires digest-pinned real registry predecessor")
class SQLiteAttributionTransitionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = registry_config(self.root)
        self.path = self.config.database_path
        self.assertEqual(UsageStore.schema_version, 8)
        seeded = registry_predecessor_call({"backend": "sqlite", "path": str(self.path), "mode": "seed"})
        self.assertEqual(seeded["status"], "ready")
        self.writes, self.page = seeded["writes"], seeded["page"]
        self.before = sqlite_snapshot(self.path)
        self.assertEqual(len(self.before["rows"]), 15)
        self.assertTrue(all(rows for table, rows in self.before["rows"].items() if table.startswith("portfolio_")))

    def probe(self, *, fail=False):
        original = UsageStore._apply_migration

        def apply(connection, version):
            self.assertIn(version, (6, 7, 8))
            original(connection, version)
            if fail and version == 6:
                raise RuntimeError("synthetic_attribution_migration_failure")

        with mock.patch.object(UsageStore, "_apply_migration", side_effect=apply):
            UsageStore(self.path).verify_ready()

    def assert_prior_state_preserved(self):
        current = copy.deepcopy(sqlite_snapshot(self.path))
        added = set(TABLE_DDL) | set(OUTCOME_TABLES) | set(FINANCE_TABLES)
        current["objects"] = [row for row in current["objects"] if row[2] not in added]
        current["rows"] = {table: rows for table, rows in current["rows"].items() if table not in added}
        current["rows"]["hormuz_schema_migrations"] = [row for row in current["rows"]["hormuz_schema_migrations"] if row[0] not in {6, 7, 8}]
        self.assertEqual(current, self.before)

    def test_sqlite_attribution_migration_is_additive_and_idempotent(self):
        for _ in range(2):
            self.probe()
            self.assertEqual(len(sqlite_snapshot(self.path)["rows"]), 31)
            self.assert_prior_state_preserved()

    def test_sqlite_attribution_failure_and_retry_preserve_populated_registry(self):
        with self.assertRaisesRegex(RuntimeError, "synthetic_attribution_migration_failure"):
            self.probe(fail=True)
        self.assertEqual(sqlite_snapshot(self.path), self.before)
        self.probe()
        self.assert_prior_state_preserved()
        current = sqlite_snapshot(self.path)
        self.probe()
        self.assertEqual(sqlite_snapshot(self.path), current)

    def test_sqlite_attribution_partial_state_refuses_before_repair(self):
        with sqlite3.connect(self.path) as connection:
            connection.execute("INSERT INTO hormuz_schema_migrations (version, state) VALUES (6, 'applying')")
        before = sqlite_snapshot(self.path)
        for read_only in (False, True):
            with self.assertRaises(StorageSchemaError) as caught:
                UsageStore(self.path, read_only=read_only)
            self.assertEqual(caught.exception.code, "storage_schema_partial_upgrade")
            self.assertEqual(sqlite_snapshot(self.path), before)

    @unittest.skipUnless(os.environ.get("HORMUZ_TEST_V1_PYTHON"), "requires digest-pinned released-v1 interpreter")
    def test_sqlite_attribution_old_processes_refuse_next_schema(self):
        self.probe()
        before = sqlite_snapshot(self.path)
        request = {"backend": "sqlite", "path": str(self.path), "mode": "verify"}
        self.assertEqual(registry_predecessor_call(request), {"status": "refused", "code": "storage_schema_newer_than_binary"})
        self.assertEqual(released_v1_call(request), {"status": "refused", "code": "storage_schema_newer_than_binary"})
        self.assertEqual(sqlite_snapshot(self.path), before)
        # Inject partial state only in this disposable candidate fixture.
        with sqlite3.connect(self.path) as connection:
            connection.execute("UPDATE hormuz_schema_migrations SET state='applying' WHERE version=6")
        partial = sqlite_snapshot(self.path)
        with self.assertRaises(StorageSchemaError) as caught:
            UsageStore(self.path)
        self.assertEqual(caught.exception.code, "storage_schema_partial_upgrade")
        self.assertEqual(registry_predecessor_call(request), {"status": "refused", "code": "storage_schema_partial_upgrade"})
        self.assertEqual(released_v1_call(request), {"status": "refused", "code": "storage_schema_partial_upgrade"})
        self.assertEqual(sqlite_snapshot(self.path), partial)

    def test_sqlite_attribution_quiesced_registry_pair_restore_preserves_replays(self):
        backup = self.root / "registry-checkpoint.sqlite3"
        sqlite_backup(self.path, backup)
        self.probe()
        retained = sqlite_snapshot(self.path)
        restored = self.root / "restored-registry.sqlite3"
        sqlite_backup(backup, restored)
        request = {"backend": "sqlite", "path": str(restored), "mode": "replay", "writes": self.writes}
        replayed = registry_predecessor_call(request)
        self.assertEqual(replayed["unknown_holds"], 1)
        self.assertEqual(replayed["replays"], [write[3] for write in self.writes])
        self.assertEqual(sqlite_snapshot(restored), self.before)
        continuation = registry_predecessor_call({**request, "mode": "page", "cursor": self.page["next_cursor"]})["page"]
        self.assertEqual(continuation["items"], [self.writes[0][3][1]])
        self.assertIsNone(continuation["next_cursor"])
        self.assertEqual(sqlite_snapshot(self.path), retained)

    def test_sqlite_attribution_post_checkpoint_writes_require_forward_recovery(self):
        self.probe()
        seed_registry_ledger(UsageStore(self.path))
        config, write, page, attempt_id = seed_attribution_metadata(self.config)
        after_write = sqlite_snapshot(self.path)
        self.assertTrue(all(after_write["rows"][table] for table in TABLE_DDL))
        retained = self.root / "retained-next-state.sqlite3"
        restored = self.root / "forward-recovered.sqlite3"
        sqlite_backup(self.path, retained)
        sqlite_backup(retained, restored)
        self.assertEqual(ledger_observation(UsageStore(restored))["unknown_holds"], 2)
        self.assertEqual(sqlite_snapshot(restored), after_write)
        restored_config = replace(config, database_path=restored)
        group = create_portfolio_repository(restored_config)
        service = PortfolioService(restored_config, group)
        body, key, expected = write
        self.assertEqual(service.dispatch(ADMIN, "POST", ATTRIBUTIONS, body=canonical(body).encode(), idempotency_key=key), expected)
        self.assertEqual(sqlite_snapshot(restored), after_write)
        continuation = service.dispatch(ADMIN, "GET", ATTRIBUTIONS, query="cursor=" + page["next_cursor"])[1]
        self.assertEqual(len(continuation["items"]), 1)
        self.assertEqual(group.attributions.attempt_facts(service.authenticate(ADMIN), attempt_id)["provider_reported_model"], "recovery-actual-v1")
        self.assertEqual(sqlite_snapshot(self.path), after_write)
        self.assertNotEqual(after_write, self.before)
        self.assertEqual(registry_predecessor_call({"backend": "sqlite", "path": str(restored), "mode": "verify"}),
                         {"status": "refused", "code": "storage_schema_newer_than_binary"})


if __name__ == "__main__":
    unittest.main()
