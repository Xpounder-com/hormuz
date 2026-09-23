"""SQLite 13-to-14 and PostgreSQL 18-to-19 Linear transition preflight."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock
from uuid import uuid4

import hormuz.postgres as postgres_module
from hormuz.postgres import PostgresStorageError
from hormuz.store import StorageSchemaError, UsageStore

if __package__:
    from ._finance_account_binding_predecessor_fixture import predecessor_call
    from ._postgres_fixture import PostgresTestCase
    from ._registry_transition_fixture import sqlite_backup, sqlite_snapshot
    from ._sqlite import managed_sqlite_connection
    from . import test_postgres_finance_collection_transition as previous
else:
    from _finance_account_binding_predecessor_fixture import predecessor_call
    from _postgres_fixture import PostgresTestCase
    from _registry_transition_fixture import sqlite_backup, sqlite_snapshot
    from _sqlite import managed_sqlite_connection
    import test_postgres_finance_collection_transition as previous


ROOT = Path(__file__).resolve().parents[1]
SQLITE_PROPOSAL = ROOT / "docs/linear-successor-schema-proposal.sqlite.sql"
POSTGRES_PROPOSAL = ROOT / "docs/linear-successor-schema-acl-proposal.sql"
PROPOSAL_TABLES = (
    "portfolio_linear_source_binding_versions",
    "gateway_linear_delivery_receipts",
    "portfolio_linear_context_events",
    "portfolio_linear_context_retention_events",
)
PROPOSED_ACL = (
    233,
    "0e51b64f26df25b78877e071695568a463d58cc1845d1b4751016ff3642f2adc",
)
PINNED = bool(
    os.environ.get("HORMUZ_TEST_ACCOUNT_BINDING_PYTHON")
    and os.environ.get("HORMUZ_TEST_ACCOUNT_BINDING_SOURCE")
)


def _sqlite_proposal_statements():
    buffer = ""
    for line in SQLITE_PROPOSAL.read_text(encoding="utf-8").splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            if statement:
                yield statement
            buffer = ""
    if buffer.strip():
        raise AssertionError("linear_sqlite_proposal_incomplete")


def _apply_sqlite_proposal(connection: sqlite3.Connection) -> None:
    for statement in _sqlite_proposal_statements():
        connection.execute(statement)


@contextmanager
def sqlite_linear_candidate(*, fail: bool = False):
    original = UsageStore._apply_migration

    def migration(connection, version):
        original(connection, version)
        if version == 14 and fail:
            connection.execute("INSERT INTO deliberately_absent_linear_probe VALUES (1)")

    with (
        mock.patch.object(UsageStore, "schema_version", 14),
        mock.patch.object(UsageStore, "_apply_migration", side_effect=migration),
    ):
        yield


@contextmanager
def postgres_linear_candidate(*, fail: bool = False):
    original = postgres_module._migration_sql

    def migration(version, schema, *roles):
        statement = original(version, schema, *roles)
        if version == 19 and fail:
            statement += "\nSELECT 1 / 0;"
        return statement

    with (
        mock.patch.object(postgres_module, "POSTGRES_SCHEMA_VERSION", 19),
        mock.patch.object(postgres_module, "_migration_sql", side_effect=migration),
    ):
        yield


def _sqlite_insert_witness(path: Path) -> None:
    with managed_sqlite_connection(path) as connection:
        connection.execute(
            "INSERT INTO portfolio_linear_source_binding_versions ("
            "organization_id,connector_id,version,binding_event_id,previous_version,"
            "binding_state,source_workspace_id,source_webhook_id,source_team_ids_json,"
            "typed_enrollment_json,credential_version,fingerprint_key_version,"
            "content_digest,request_digest,registered_by,registered_at,evidence_json"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "acme",
                "linear-main",
                1,
                "11111111-1111-4111-8111-111111111111",
                None,
                "active",
                "22222222-2222-4222-8222-222222222222",
                "33333333-3333-4333-8333-333333333333",
                "[]",
                "{}",
                "linear-secret-v1",
                1,
                "a" * 64,
                "b" * 64,
                "alice",
                "2026-09-23T00:00:00.000000Z",
                "{}",
            ),
        )


class SQLiteLinearProposalBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "proposal.sqlite3"
        with mock.patch.object(UsageStore, "schema_version", 13):
            UsageStore(self.path).verify_ready()
        with managed_sqlite_connection(self.path) as connection:
            _apply_sqlite_proposal(connection)
        _sqlite_insert_witness(self.path)
        with managed_sqlite_connection(self.path) as connection:
            connection.execute(
                "INSERT INTO gateway_linear_delivery_receipts ("
                "organization_id,connector_id,receipt_id,binding_version,"
                "source_delivery_id,credential_version,body_fingerprint,"
                "fingerprint_key_version,source_fact_fingerprint,"
                "source_fact_key_version,received_at,committed_at,response_digest,"
                "evidence_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "acme",
                    "linear-main",
                    "44444444-4444-4444-8444-444444444444",
                    1,
                    "linear-delivery-1",
                    "linear-secret-v1",
                    "c" * 64,
                    1,
                    "d" * 64,
                    1,
                    "2026-09-23T00:00:01.000000Z",
                    "2026-09-23T00:00:02.000000Z",
                    "e" * 64,
                    "{}",
                ),
            )
            connection.execute(
                "INSERT INTO portfolio_linear_context_events ("
                "organization_id,connector_id,context_event_id,receipt_id,"
                "object_kind,object_id,lifecycle,normalized_state,"
                "relationship_coverage,revision_kind,revision_value,ordering_state,"
                "scope_state,event_at,observed_at,ingested_at,provenance_digest,"
                "commit_sequence,evidence_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "acme",
                    "linear-main",
                    "55555555-5555-4555-8555-555555555555",
                    "44444444-4444-4444-8444-444444444444",
                    "issue",
                    "66666666-6666-4666-8666-666666666666",
                    "updated",
                    "in_progress",
                    "complete",
                    "source_updated_at_v1",
                    "2026-09-23T00:00:00Z",
                    "current",
                    "matched",
                    "2026-09-23T00:00:00.000000Z",
                    "2026-09-23T00:00:01.000000Z",
                    "2026-09-23T00:00:02.000000Z",
                    "f" * 64,
                    1,
                    "{}",
                ),
            )
            connection.execute(
                "INSERT INTO portfolio_linear_context_retention_events ("
                "organization_id,connector_id,retention_event_id,"
                "target_context_event_id,actor_id,reason_code,observed_at,"
                "ingested_at,provenance_digest,evidence_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    "acme",
                    "linear-main",
                    "77777777-7777-4777-8777-777777777777",
                    "55555555-5555-4555-8555-555555555555",
                    "alice",
                    "tombstoned",
                    "2026-09-23T00:00:03.000000Z",
                    "2026-09-23T00:00:04.000000Z",
                    "0" * 64,
                    "{}",
                ),
            )

    def test_every_proposed_table_rejects_update_and_delete(self):
        with managed_sqlite_connection(self.path) as connection:
            for table in PROPOSAL_TABLES:
                with self.subTest(table=table, operation="update"):
                    with self.assertRaisesRegex(
                        sqlite3.IntegrityError,
                        "linear_metadata_append_only",
                    ):
                        connection.execute(
                            f"UPDATE {table} SET organization_id=organization_id"
                        )
                with self.subTest(table=table, operation="delete"):
                    with self.assertRaisesRegex(
                        sqlite3.IntegrityError,
                        "linear_metadata_append_only",
                    ):
                        connection.execute(f"DELETE FROM {table}")
                self.assertEqual(
                    connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0],
                    1,
                )


@unittest.skipUnless(PINNED, "requires digest-verified published v1.2.0 wheel and source")
class SQLitePublishedLinearTransitionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "published.sqlite3"
        self.seeded = predecessor_call(self.request(mode="seed"))
        self.assertEqual(self.seeded["status"], "ready")
        self.published = sqlite_snapshot(self.path)
        self.assertEqual(UsageStore.schema_version, 14)
        version_patch = mock.patch.object(UsageStore, "schema_version", 13)
        version_patch.start()
        self.addCleanup(version_patch.stop)

    def request(self, path=None, mode="ready"):
        return {"backend": "sqlite", "path": str(path or self.path), "mode": mode}

    def advance_integrated_baseline(self):
        with mock.patch.object(UsageStore, "schema_version", 13):
            UsageStore(self.path).verify_ready()
        self.baseline = sqlite_snapshot(self.path)
        self.assertIn((13, "applied"), {
            (row[0], row[1])
            for row in self.baseline["rows"]["hormuz_schema_migrations"]
        })
        return self.baseline

    def assert_baseline_rows_preserved(self, after):
        for table, rows in self.baseline["rows"].items():
            actual = after["rows"][table]
            if table == "hormuz_schema_migrations":
                actual = [row for row in actual if row[0] != 14]
            self.assertEqual(actual, rows, table)

    def test_missing_linear_successor_preserves_integrated_baseline(self):
        before = self.advance_integrated_baseline()
        original = UsageStore._apply_migration

        def missing(connection, version):
            if version == 14:
                raise StorageSchemaError("storage_schema_migration_unsupported")
            return original(connection, version)

        with (
            mock.patch.object(UsageStore, "schema_version", 14),
            mock.patch.object(UsageStore, "_apply_migration", side_effect=missing),
        ):
            with self.assertRaisesRegex(StorageSchemaError, "storage_schema_migration_unsupported"):
                UsageStore(self.path)
        self.assertEqual(sqlite_snapshot(self.path), before)

    def test_test_only_linear_ddl_failure_rolls_back_and_retry_is_idempotent(self):
        self.advance_integrated_baseline()
        with sqlite_linear_candidate(fail=True), self.assertRaises(sqlite3.OperationalError):
            UsageStore(self.path)
        self.assertEqual(sqlite_snapshot(self.path), self.baseline)
        with sqlite_linear_candidate():
            UsageStore(self.path).verify_ready()
        after = sqlite_snapshot(self.path)
        self.assert_baseline_rows_preserved(after)
        self.assertTrue(all(after["rows"][table] == [] for table in PROPOSAL_TABLES))
        with sqlite_linear_candidate():
            UsageStore(self.path).verify_ready()
        self.assertEqual(sqlite_snapshot(self.path), after)

    def test_published_and_current_binaries_refuse_partial_and_newer_without_repair(self):
        self.advance_integrated_baseline()
        with sqlite_linear_candidate():
            UsageStore(self.path)
        self.assertEqual(
            predecessor_call(self.request()),
            {"status": "refused", "code": "storage_schema_newer_than_binary"},
        )
        with mock.patch.object(UsageStore, "schema_version", 13):
            with self.assertRaisesRegex(StorageSchemaError, "storage_schema_newer_than_binary"):
                UsageStore(self.path, read_only=True)
        with managed_sqlite_connection(self.path) as connection:
            connection.execute(
                "UPDATE hormuz_schema_migrations SET state='applying' WHERE version=14"
            )
        partial = sqlite_snapshot(self.path)
        self.assertEqual(
            predecessor_call(self.request()),
            {"status": "refused", "code": "storage_schema_partial_upgrade"},
        )
        with self.assertRaisesRegex(StorageSchemaError, "storage_schema_partial_upgrade"):
            UsageStore(self.path, read_only=True)
        with sqlite_linear_candidate():
            with self.assertRaisesRegex(StorageSchemaError, "storage_schema_partial_upgrade"):
                UsageStore(self.path, read_only=True)
        self.assertEqual(sqlite_snapshot(self.path), partial)

    def test_quiesced_published_pair_restore_replays_original_receipt(self):
        old = self.root / "published-checkpoint.sqlite3"
        sqlite_backup(self.path, old)
        self.advance_integrated_baseline()
        with sqlite_linear_candidate():
            UsageStore(self.path)
        retained = sqlite_snapshot(self.path)
        restored = self.root / "published-restored.sqlite3"
        sqlite_backup(old, restored)
        self.assertEqual(sqlite_snapshot(restored), self.published)
        self.assertEqual(predecessor_call(self.request(restored, "replay")), self.seeded)
        self.assertEqual(sqlite_snapshot(self.path), retained)

    def test_post_checkpoint_witness_requires_retained_forward_recovery(self):
        old = self.root / "published-checkpoint.sqlite3"
        sqlite_backup(self.path, old)
        self.advance_integrated_baseline()
        with sqlite_linear_candidate():
            UsageStore(self.path)
        _sqlite_insert_witness(self.path)
        retained = sqlite_snapshot(self.path)
        self.assertEqual(len(retained["rows"][PROPOSAL_TABLES[0]]), 1)
        forward = self.root / "candidate-forward.sqlite3"
        sqlite_backup(self.path, forward)
        with sqlite_linear_candidate():
            UsageStore(forward, read_only=True).verify_ready()
        self.assertEqual(sqlite_snapshot(forward), retained)
        restored_old = self.root / "published-restored.sqlite3"
        sqlite_backup(old, restored_old)
        self.assertEqual(predecessor_call(self.request(restored_old, "replay")), self.seeded)
        self.assertNotEqual(sqlite_snapshot(restored_old), retained)
        self.assertEqual(sqlite_snapshot(self.path), retained)


@unittest.skipUnless(
    PINNED and os.environ.get("HORMUZ_TEST_POSTGRES_DSN"),
    "requires published v1.2.0 artifacts and disposable PostgreSQL",
)
class PostgresPublishedLinearTransitionTests(PostgresTestCase):
    migrate = previous.PostgresFinanceCollectionTransitionTests.migrate
    runtime = previous.PostgresFinanceCollectionTransitionTests.runtime
    snapshot = previous.PostgresFinanceCollectionTransitionTests.snapshot
    backup = previous.PostgresFinanceCollectionTransitionTests.backup
    restore = previous.PostgresFinanceCollectionTransitionTests.restore

    def setUp(self):
        self._drop_schema(self.schema)
        self.seeded = predecessor_call(self.request(mode="seed"))
        self.assertEqual(self.seeded["status"], "ready")
        self.published = self.snapshot()
        self.assertEqual(postgres_module.POSTGRES_SCHEMA_VERSION, 19)
        version_patch = mock.patch.object(
            postgres_module, "POSTGRES_SCHEMA_VERSION", 18
        )
        version_patch.start()
        self.addCleanup(version_patch.stop)

    def request(self, dsn=None, mode="ready"):
        return {
            "backend": "postgresql",
            "mode": mode,
            "owner_dsn": self.owner_dsn,
            "runtime_dsn": dsn or self.runtime_dsn,
            "schema": self.schema,
            "runtime_role": self.runtime_role,
            "policy_control_role": self.policy_control_role,
            "custody_control_role": self.custody_control_role,
            "custody_executor_role": self.custody_executor_role,
        }

    def advance_integrated_baseline(self):
        with mock.patch.object(postgres_module, "POSTGRES_SCHEMA_VERSION", 18):
            self.assertEqual(self.migrate().version, 18)
        self.baseline = self.snapshot()
        return self.baseline

    def assert_baseline_rows_preserved(self, after):
        for table, rows in self.baseline["rows"].items():
            actual = after["rows"][table]
            if table == "hormuz_schema_migrations":
                actual = [
                    row
                    for row in actual
                    if json.loads(row[0])["version"] != 19
                ]
            self.assertEqual(actual, rows, table)

    def insert_witness(self, dsn=None):
        with self.psycopg.connect(dsn or self.owner_dsn) as connection:
            connection.execute(
                self.sql.SQL(
                    "INSERT INTO {}.portfolio_linear_source_binding_versions ("
                    "organization_id,connector_id,version,binding_event_id,previous_version,"
                    "binding_state,source_workspace_id,source_webhook_id,source_team_ids_json,"
                    "typed_enrollment_json,credential_version,fingerprint_key_version,"
                    "content_digest,request_digest,registered_by,registered_at,evidence_json"
                    ") VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                ).format(self.sql.Identifier(self.schema)),
                (
                    "acme",
                    "linear-main",
                    1,
                    "11111111-1111-4111-8111-111111111111",
                    None,
                    "active",
                    "22222222-2222-4222-8222-222222222222",
                    "33333333-3333-4333-8333-333333333333",
                    "[]",
                    "{}",
                    "linear-secret-v1",
                    1,
                    "a" * 64,
                    "b" * 64,
                    "alice",
                    "2026-09-23T00:00:00.000000Z",
                    "{}",
                ),
            )

    def test_missing_linear_successor_preserves_integrated_baseline(self):
        before = self.advance_integrated_baseline()
        original = postgres_module._migration_sql

        def missing(version, *args, **kwargs):
            if version == 19:
                raise PostgresStorageError("storage_schema_migration_unsupported")
            return original(version, *args, **kwargs)

        with (
            mock.patch.object(postgres_module, "POSTGRES_SCHEMA_VERSION", 19),
            mock.patch.object(postgres_module, "_migration_sql", side_effect=missing),
        ):
            with self.assertRaisesRegex(
                PostgresStorageError, "storage_schema_migration_unsupported"
            ):
                self.migrate()
        self.assertEqual(self.snapshot(), before)

    def test_test_only_linear_ddl_failure_rolls_back_and_retry_is_idempotent(self):
        self.advance_integrated_baseline()
        with postgres_linear_candidate(fail=True), self.assertRaisesRegex(
            PostgresStorageError, "storage_unavailable"
        ):
            self.migrate()
        self.assertEqual(self.snapshot(), self.baseline)
        with postgres_linear_candidate():
            self.assertEqual(self.migrate().version, 19)
        after = self.snapshot()
        self.assert_baseline_rows_preserved(after)
        self.assertTrue(all(after["rows"][table] == [] for table in PROPOSAL_TABLES))
        with postgres_linear_candidate():
            self.assertEqual(self.migrate().version, 19)
        self.assertEqual(self.snapshot(), after)

    def test_published_and_current_binaries_refuse_partial_and_newer_without_repair(self):
        self.advance_integrated_baseline()
        with postgres_linear_candidate():
            self.migrate()
        self.assertEqual(
            predecessor_call(self.request()),
            {"status": "refused", "code": "storage_schema_newer_than_binary"},
        )
        with mock.patch.object(postgres_module, "POSTGRES_SCHEMA_VERSION", 18):
            with self.assertRaisesRegex(PostgresStorageError, "storage_schema_newer_than_binary"):
                self.runtime()
        with self.psycopg.connect(self.owner_dsn) as connection:
            connection.execute(
                self.sql.SQL(
                    "UPDATE {}.hormuz_schema_migrations SET state='applying' WHERE version=19"
                ).format(self.sql.Identifier(self.schema))
            )
        partial = self.snapshot()
        self.assertEqual(
            predecessor_call(self.request()),
            {"status": "refused", "code": "storage_schema_partial_upgrade"},
        )
        with mock.patch.object(postgres_module, "POSTGRES_SCHEMA_VERSION", 18):
            with self.assertRaisesRegex(PostgresStorageError, "storage_schema_partial_upgrade"):
                self.runtime()
        with postgres_linear_candidate():
            with self.assertRaisesRegex(PostgresStorageError, "storage_schema_partial_upgrade"):
                self.runtime()
        self.assertEqual(self.snapshot(), partial)

    @unittest.skipUnless(
        os.environ.get("HORMUZ_TEST_PG_CONTAINER"),
        "requires matching pg_dump/restore",
    )
    def test_quiesced_published_pair_restore_replays_original_receipt(self):
        old = self.backup()
        self.advance_integrated_baseline()
        with postgres_linear_candidate():
            self.migrate()
        retained = self.snapshot()
        owner, runtime = self.restore(old)
        self.assertEqual(self.snapshot(owner)["rows"], self.published["rows"])
        self.assertEqual(predecessor_call(self.request(runtime, "replay")), self.seeded)
        self.assertEqual(self.snapshot(), retained)

    @unittest.skipUnless(
        os.environ.get("HORMUZ_TEST_PG_CONTAINER"),
        "requires matching pg_dump/restore",
    )
    def test_post_checkpoint_witness_requires_retained_forward_recovery(self):
        old = self.backup()
        self.advance_integrated_baseline()
        with postgres_linear_candidate():
            self.migrate()
        self.insert_witness()
        retained = self.snapshot()
        self.assertEqual(len(retained["rows"][PROPOSAL_TABLES[0]]), 1)
        forward = self.backup()
        owner, runtime = self.restore(forward)
        with postgres_linear_candidate():
            self.runtime(runtime).verify_ready()
        self.assertEqual(self.snapshot(owner)["rows"], retained["rows"])
        old_owner, old_runtime = self.restore(old)
        self.assertEqual(self.snapshot(old_owner)["rows"], self.published["rows"])
        self.assertEqual(predecessor_call(self.request(old_runtime, "replay")), self.seeded)
        self.assertNotEqual(self.snapshot(old_owner)["rows"], retained["rows"])
        self.assertEqual(self.snapshot(), retained)


@unittest.skipUnless(
    os.environ.get("HORMUZ_TEST_POSTGRES_DSN"),
    "requires disposable PostgreSQL",
)
class PostgresLinearProposalACLTests(PostgresTestCase):
    def bootstrap_clean_proposal(self):
        schema = "linear_acl_" + uuid4().hex[:12]
        self.addCleanup(self._drop_schema, schema)
        with mock.patch.object(postgres_module, "POSTGRES_SCHEMA_VERSION", 18):
            status = postgres_module.migrate_postgres(
                self.owner_dsn,
                schema=schema,
                runtime_role=self.runtime_role,
                policy_control_role=self.policy_control_role,
                custody_control_role=self.custody_control_role,
                custody_executor_role=self.custody_executor_role,
            )
        self.assertEqual(status.version, 18)
        proposal = POSTGRES_PROPOSAL.read_text(encoding="utf-8").format(
            schema=postgres_module._quote_identifier(schema),
            runtime_role=postgres_module._quote_identifier(self.runtime_role),
        )
        with self.psycopg.connect(self.owner_dsn) as connection:
            connection.execute(proposal)
        with self.psycopg.connect(self.owner_dsn) as connection:
            with connection.cursor() as cursor:
                owner = cursor.execute("SELECT current_user").fetchone()[0]
                entries = postgres_module._postgres_acl_entries(
                    cursor,
                    schema=schema,
                    migration_login=owner,
                )
        boundary = postgres_module._postgres_acl_boundary(
            entries,
            schema=schema,
            migration_login=owner,
            role_names=(
                self.runtime_role,
                self.policy_control_role,
                self.custody_control_role,
                self.custody_executor_role,
            ),
        )
        return schema, boundary

    def measure_clean_proposal(self):
        return self.bootstrap_clean_proposal()[1]

    def test_two_clean_bootstraps_match_literal_successor_acl(self):
        self.assertEqual(self.measure_clean_proposal(), PROPOSED_ACL)
        self.assertEqual(self.measure_clean_proposal(), PROPOSED_ACL)

    def test_extra_runtime_permission_changes_literal_acl(self):
        schema, boundary = self.bootstrap_clean_proposal()
        self.assertEqual(boundary, PROPOSED_ACL)
        with self.psycopg.connect(self.owner_dsn) as connection:
            connection.execute(
                self.sql.SQL("GRANT DELETE ON {}.portfolio_linear_context_events TO {}")
                .format(self.sql.Identifier(schema), self.sql.Identifier(self.runtime_role))
            )
            with connection.cursor() as cursor:
                owner = cursor.execute("SELECT current_user").fetchone()[0]
                entries = postgres_module._postgres_acl_entries(
                    cursor,
                    schema=schema,
                    migration_login=owner,
                )
        injected = postgres_module._postgres_acl_boundary(
            entries,
            schema=schema,
            migration_login=owner,
            role_names=(
                self.runtime_role,
                self.policy_control_role,
                self.custody_control_role,
                self.custody_executor_role,
            ),
        )
        self.assertNotEqual(injected, PROPOSED_ACL)

    def test_runtime_insert_is_tenant_scoped_and_mutation_is_denied(self):
        schema, boundary = self.bootstrap_clean_proposal()
        self.assertEqual(boundary, PROPOSED_ACL)
        columns = (
            "organization_id,connector_id,version,binding_event_id,previous_version,"
            "binding_state,source_workspace_id,source_webhook_id,source_team_ids_json,"
            "typed_enrollment_json,credential_version,fingerprint_key_version,"
            "content_digest,request_digest,registered_by,registered_at,evidence_json"
        )
        values = (
            "acme",
            "linear-main",
            1,
            "11111111-1111-4111-8111-111111111111",
            None,
            "active",
            "22222222-2222-4222-8222-222222222222",
            "33333333-3333-4333-8333-333333333333",
            "[]",
            "{}",
            "linear-secret-v1",
            1,
            "a" * 64,
            "b" * 64,
            "alice",
            "2026-09-23T00:00:00.000000Z",
            "{}",
        )
        with postgres_module.postgres_transaction(
            self.runtime_dsn,
            schema=schema,
            runtime_role=self.runtime_role,
            organization_id="acme",
        ) as connection:
            connection.execute(
                f"INSERT INTO portfolio_linear_source_binding_versions ({columns}) "
                f"VALUES ({','.join('%s' for _ in values)})",
                values,
            )
        with postgres_module.postgres_transaction(
            self.runtime_dsn,
            schema=schema,
            runtime_role=self.runtime_role,
            organization_id="beta",
        ) as connection:
            row = connection.execute(
                "SELECT count(*) AS count FROM portfolio_linear_source_binding_versions"
            ).fetchone()
            self.assertEqual(row["count"], 0)
        with self.assertRaisesRegex(PostgresStorageError, "storage_access_denied"):
            with postgres_module.postgres_transaction(
                self.runtime_dsn,
                schema=schema,
                runtime_role=self.runtime_role,
                organization_id="acme",
            ) as connection:
                connection.execute(
                    "UPDATE portfolio_linear_source_binding_versions "
                    "SET registered_by='mallory' WHERE connector_id='linear-main'"
                )


if __name__ == "__main__":
    unittest.main()
