"""First red boundary only; not complete account-binding transition proof.

No successor is implemented/reserved here. Probe the immediately absent
version and require the entire populated predecessor to remain unchanged.
Actual successor DDL, predecessor-package and recovery proofs remain gated.
"""

import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import hormuz.postgres as postgres_module
from hormuz.postgres import PostgresStorageError
from hormuz.store import StorageSchemaError, UsageStore

if __package__:
    from ._finance_collection_transition_fixture import (
        seed_sqlite_collection_predecessor, seed_postgres_collection_predecessor,
    )
    from ._registry_transition_fixture import sqlite_snapshot
    from ._postgres_fixture import PostgresTestCase
    from . import test_postgres_finance_collection_transition as previous
else:
    from _finance_collection_transition_fixture import (
        seed_sqlite_collection_predecessor, seed_postgres_collection_predecessor,
    )
    from _registry_transition_fixture import sqlite_snapshot
    from _postgres_fixture import PostgresTestCase
    import test_postgres_finance_collection_transition as previous


class SQLiteFinanceAccountBindingTransitionPreflightTests(unittest.TestCase):
    def test_absent_successor_refuses_without_touching_populated_predecessor(self):
        self.assertEqual(UsageStore.schema_version, 12)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "synthetic.sqlite3"
            seed_sqlite_collection_predecessor(path)
            before = sqlite_snapshot(path)
            for table in ("gateway_finance_attempt_evidence", "gateway_audit_chain_entries",
                          "portfolio_work_budget_plan_versions", "gateway_provider_attempt_metrics"):
                self.assertTrue(before["rows"][table], table)
            with mock.patch.object(UsageStore, "schema_version", 13):
                with self.assertRaisesRegex(StorageSchemaError, "storage_schema_migration_unsupported"):
                    UsageStore(path)
            self.assertEqual(sqlite_snapshot(path), before)
            UsageStore(path, read_only=True).verify_ready()
            self.assertEqual(sqlite_snapshot(path), before)


@unittest.skipUnless(os.environ.get("HORMUZ_TEST_POSTGRES_DSN"), "requires disposable PostgreSQL")
class PostgresFinanceAccountBindingTransitionPreflightTests(PostgresTestCase):
    migrate = previous.PostgresFinanceCollectionTransitionTests.migrate
    snapshot = previous.PostgresFinanceCollectionTransitionTests.snapshot
    runtime = previous.PostgresFinanceCollectionTransitionTests.runtime

    def test_absent_successor_refuses_without_touching_populated_predecessor(self):
        self.assertEqual(postgres_module.POSTGRES_SCHEMA_VERSION, 17)
        self._drop_schema(self.schema)
        seed_postgres_collection_predecessor(
            owner_dsn=self.owner_dsn, runtime_dsn=self.runtime_dsn, schema=self.schema,
            runtime_role=self.runtime_role, policy_control_role=self.policy_control_role,
            custody_control_role=self.custody_control_role,
            custody_executor_role=self.custody_executor_role,
        )
        self.assertEqual(self.migrate().version, 17)
        before = self.snapshot()
        for table in ("gateway_finance_attempt_evidence", "gateway_audit_chain_entries",
                      "portfolio_work_budget_plan_versions", "gateway_provider_attempt_metrics"):
            self.assertTrue(before["rows"][table], table)
        with mock.patch.object(postgres_module, "POSTGRES_SCHEMA_VERSION", 18):
            with self.assertRaisesRegex(PostgresStorageError, "storage_schema_migration_unsupported"):
                self.migrate()
        self.assertEqual(self.snapshot(), before)
        self.runtime().verify_ready()
        self.assertEqual(self.snapshot(), before)
