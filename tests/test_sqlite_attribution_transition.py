"""Red-first #216 transition proof; test-only DDL is not attribution support."""

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

if __package__:
    from ._portfolio_fixture import ADMIN, registry_config, seed_registry_metadata
    from ._registry_transition_fixture import ledger_observation, released_v1_call, seed_registry_ledger, sqlite_backup, sqlite_snapshot
else:
    from _portfolio_fixture import ADMIN, registry_config, seed_registry_metadata
    from _registry_transition_fixture import ledger_observation, released_v1_call, seed_registry_ledger, sqlite_backup, sqlite_snapshot


PROBE = "attribution_transition_test_probe"


class SQLiteAttributionTransitionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = registry_config(self.root)
        self.path = self.config.database_path
        self.assertEqual(UsageStore.schema_version, 5)
        seed_registry_ledger(UsageStore(self.path))
        self.writes, self.page = seed_registry_metadata(self.config)
        self.before = sqlite_snapshot(self.path)
        self.assertEqual(len(self.before["rows"]), 15)
        self.assertTrue(all(rows for table, rows in self.before["rows"].items() if table.startswith("portfolio_")))

    def probe(self, *, fail=False):
        original = UsageStore._apply_migration

        def apply(connection, version):
            if version != 6:
                return original(connection, version)
            connection.execute(f"CREATE TABLE {PROBE} (organization_id TEXT NOT NULL, id TEXT PRIMARY KEY)")
            if fail:
                raise RuntimeError("synthetic_attribution_migration_failure")

        with mock.patch.object(UsageStore, "schema_version", 6), mock.patch.object(UsageStore, "_apply_migration", side_effect=apply):
            UsageStore(self.path).verify_ready()

    def assert_prior_state_preserved(self):
        current = copy.deepcopy(sqlite_snapshot(self.path))
        current["objects"] = [row for row in current["objects"] if row[2] != PROBE]
        current["rows"].pop(PROBE, None)
        current["rows"]["hormuz_schema_migrations"] = [row for row in current["rows"]["hormuz_schema_migrations"] if row[0] != 6]
        self.assertEqual(current, self.before)

    def test_sqlite_attribution_migration_is_red_until_implementation(self):
        with mock.patch.object(UsageStore, "schema_version", 6):
            with self.assertRaises(StorageSchemaError) as caught:
                UsageStore(self.path)
        self.assertEqual(caught.exception.code, "storage_schema_migration_unsupported")
        self.assertEqual(sqlite_snapshot(self.path), self.before)

    def test_sqlite_attribution_probe_failure_and_retry_preserve_populated_registry(self):
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
        with self.assertRaises(StorageSchemaError) as caught:
            UsageStore(self.path)
        self.assertEqual(caught.exception.code, "storage_schema_newer_than_binary")
        request = {"backend": "sqlite", "path": str(self.path), "mode": "verify"}
        self.assertEqual(released_v1_call(request), {"status": "refused", "code": "storage_schema_newer_than_binary"})
        self.assertEqual(sqlite_snapshot(self.path), before)
        # Inject partial state in this disposable future-schema fixture only.
        with sqlite3.connect(self.path) as connection:
            connection.execute("UPDATE hormuz_schema_migrations SET state='applying' WHERE version=6")
        partial = sqlite_snapshot(self.path)
        with self.assertRaises(StorageSchemaError) as caught:
            UsageStore(self.path)
        self.assertEqual(caught.exception.code, "storage_schema_partial_upgrade")
        self.assertEqual(released_v1_call(request), {"status": "refused", "code": "storage_schema_partial_upgrade"})
        self.assertEqual(sqlite_snapshot(self.path), partial)

    def test_sqlite_attribution_quiesced_registry_pair_restore_preserves_replays(self):
        backup = self.root / "registry-checkpoint.sqlite3"
        sqlite_backup(self.path, backup)
        self.probe()
        retained = sqlite_snapshot(self.path)
        restored = self.root / "restored-registry.sqlite3"
        sqlite_backup(backup, restored)
        restored_config = replace(self.config, database_path=restored)
        service = PortfolioService(restored_config, RegistryRepository(restored_config))
        self.assertEqual(ledger_observation(UsageStore(restored))["unknown_holds"], 1)
        for path, body, key, expected in self.writes:
            self.assertEqual(service.dispatch(ADMIN, "POST", path, body=json.dumps(body).encode(), idempotency_key=key), expected)
        self.assertEqual(sqlite_snapshot(restored), self.before)
        continuation = service.dispatch(ADMIN, "GET", SCOPES, query="cursor=" + self.page["next_cursor"])[1]
        self.assertEqual(continuation["items"], [self.writes[0][3][1]])
        self.assertIsNone(continuation["next_cursor"])
        self.assertEqual(sqlite_snapshot(self.path), retained)

    def test_sqlite_attribution_post_checkpoint_writes_require_forward_recovery(self):
        self.probe()
        with mock.patch.object(UsageStore, "schema_version", 6):
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
        with self.assertRaises(StorageSchemaError) as caught:
            UsageStore(restored)
        self.assertEqual(caught.exception.code, "storage_schema_newer_than_binary")


if __name__ == "__main__":
    unittest.main()
