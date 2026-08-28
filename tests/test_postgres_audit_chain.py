from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock

from hormuz.audit_chain import build_audit_chain_checkpoint
from hormuz.postgres import PostgresStorageError, postgres_transaction
from hormuz.postgres_usage_store import PostgresUsageStore
from hormuz.store import StorageSchemaError, UsageStore
if __package__:
    from ._postgres_fixture import PostgresTestCase, _identity
else:  # Isolated wheel compatibility discovery uses the tests directory as its import root.
    from _postgres_fixture import PostgresTestCase, _identity


class PostgresAuditChainTests(PostgresTestCase):
    def _clear_audit_evidence(self) -> None:
        with self.psycopg.connect(self.owner_dsn, autocommit=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self.sql.SQL(
                        "TRUNCATE TABLE {}.gateway_audit_chain_checkpoints, "
                        "{}.gateway_audit_chain_entries, {}.gateway_audit_chain_heads, "
                        "{}.gateway_audit_chain_epochs, {}.gateway_secret_events, "
                        "{}.gateway_usage_events CASCADE"
                    ).format(*(self.sql.Identifier(self.schema) for _ in range(6)))
                )

    @staticmethod
    def _record_sqlite(store: UsageStore) -> None:
        for _ in range(3):
            store.record(
                identity=_identity("acme"),
                client="codex",
                protocol="openai",
                requested_model="gpt-test",
                resolved_alias="gpt-test",
                upstream_model="gpt-upstream",
                policy_action="allowed",
                status="succeeded",
            )

    def _record_postgres(self) -> None:
        for _ in range(3):
            self.store.record(
                identity=_identity("acme"),
                client="codex",
                protocol="openai",
                requested_model="gpt-test",
                resolved_alias="gpt-test",
                upstream_model="gpt-upstream",
                policy_action="allowed",
                status="succeeded",
            )

    @staticmethod
    def _tamper_sqlite(path: Path, case: str) -> None:
        connection = sqlite3.connect(path)
        if case in {"entry_deletion", "truncation"}:
            connection.execute("DROP TRIGGER gateway_audit_chain_entries_no_delete")
        if case in {"entry_reordering", "invalid_predecessor", "schema_version_rejection"}:
            connection.execute("DROP TRIGGER gateway_audit_chain_entries_no_update")
        if case == "entry_deletion":
            connection.execute("DELETE FROM gateway_audit_chain_entries WHERE sequence = 2")
        elif case == "source_deletion":
            event_id = connection.execute(
                "SELECT event_id FROM gateway_audit_chain_entries WHERE sequence = 2"
            ).fetchone()[0]
            connection.execute("DELETE FROM gateway_usage_events WHERE id = ?", (event_id,))
        elif case == "entry_reordering":
            connection.execute("UPDATE gateway_audit_chain_entries SET sequence = 4 WHERE sequence = 2")
        elif case == "truncation":
            digest = connection.execute(
                "SELECT event_digest FROM gateway_audit_chain_entries WHERE sequence = 2"
            ).fetchone()[0]
            connection.execute("DELETE FROM gateway_audit_chain_entries WHERE sequence = 3")
            connection.execute(
                "UPDATE gateway_audit_chain_heads SET sequence = 2, head_digest = ? "
                "WHERE organization_id = 'acme'",
                (digest,),
            )
        elif case == "wrong_source_event":
            event_id = connection.execute(
                "SELECT event_id FROM gateway_audit_chain_entries WHERE sequence = 2"
            ).fetchone()[0]
            connection.execute(
                "UPDATE gateway_usage_events SET requested_model = 'wrong-source-model' WHERE id = ?",
                (event_id,),
            )
        elif case == "invalid_predecessor":
            connection.execute(
                "UPDATE gateway_audit_chain_entries SET previous_digest = ? WHERE sequence = 2",
                ("f" * 64,),
            )
        elif case == "schema_version_rejection":
            connection.execute("PRAGMA ignore_check_constraints = ON")
            connection.execute(
                "UPDATE gateway_audit_chain_entries SET entry_schema_version = 99 WHERE sequence = 2"
            )
        connection.commit()
        connection.close()

    def _tamper_postgres(self, case: str) -> None:
        with self.psycopg.connect(self.owner_dsn, autocommit=True) as connection:
            with connection.cursor() as cursor:
                table = self.sql.SQL("{}.gateway_audit_chain_entries").format(
                    self.sql.Identifier(self.schema)
                )
                usage = self.sql.SQL("{}.gateway_usage_events").format(
                    self.sql.Identifier(self.schema)
                )
                heads = self.sql.SQL("{}.gateway_audit_chain_heads").format(
                    self.sql.Identifier(self.schema)
                )
                if case == "entry_deletion":
                    cursor.execute(
                        self.sql.SQL("DELETE FROM {} WHERE organization_id = %s AND sequence = 2").format(table),
                        ("acme",),
                    )
                elif case == "source_deletion":
                    cursor.execute(
                        self.sql.SQL(
                            "DELETE FROM {} WHERE organization_id = %s AND id = "
                            "(SELECT event_id FROM {} WHERE organization_id = %s AND sequence = 2)"
                        ).format(usage, table),
                        ("acme", "acme"),
                    )
                elif case == "entry_reordering":
                    cursor.execute(
                        self.sql.SQL("UPDATE {} SET sequence = 4 WHERE organization_id = %s AND sequence = 2").format(table),
                        ("acme",),
                    )
                elif case == "truncation":
                    cursor.execute(
                        self.sql.SQL(
                            "SELECT event_digest FROM {} WHERE organization_id = %s AND sequence = 2"
                        ).format(table),
                        ("acme",),
                    )
                    digest = cursor.fetchone()[0]
                    cursor.execute(
                        self.sql.SQL("DELETE FROM {} WHERE organization_id = %s AND sequence = 3").format(table),
                        ("acme",),
                    )
                    cursor.execute(
                        self.sql.SQL(
                            "UPDATE {} SET sequence = 2, head_digest = %s WHERE organization_id = %s"
                        ).format(heads),
                        (digest, "acme"),
                    )
                elif case == "wrong_source_event":
                    cursor.execute(
                        self.sql.SQL(
                            "UPDATE {} SET requested_model = 'wrong-source-model' "
                            "WHERE organization_id = %s AND id = "
                            "(SELECT event_id FROM {} WHERE organization_id = %s AND sequence = 2)"
                        ).format(usage, table),
                        ("acme", "acme"),
                    )
                elif case == "invalid_predecessor":
                    cursor.execute(
                        self.sql.SQL(
                            "UPDATE {} SET previous_digest = %s WHERE organization_id = %s AND sequence = 2"
                        ).format(table),
                        ("f" * 64, "acme"),
                    )
                elif case == "schema_version_rejection":
                    cursor.execute(
                        self.sql.SQL(
                            "ALTER TABLE {} DROP CONSTRAINT "
                            "gateway_audit_chain_entries_entry_schema_version_check"
                        ).format(table)
                    )
                    cursor.execute(
                        self.sql.SQL(
                            "ALTER TABLE {} DROP CONSTRAINT "
                            "gateway_audit_chain_entries_source_identity_check"
                        ).format(table)
                    )
                    cursor.execute(
                        self.sql.SQL(
                            "UPDATE {} SET entry_schema_version = 99 "
                            "WHERE organization_id = %s AND sequence = 2"
                        ).format(table),
                        ("acme",),
                    )

    def _restore_postgres_schema_version_constraint(self) -> None:
        with self.psycopg.connect(self.owner_dsn, autocommit=True) as connection:
            with connection.cursor() as cursor:
                table = self.sql.SQL("{}.gateway_audit_chain_entries").format(
                    self.sql.Identifier(self.schema)
                )
                cursor.execute(
                    self.sql.SQL(
                        "UPDATE {} SET entry_schema_version = 1 WHERE entry_schema_version = 99"
                    ).format(table)
                )
                cursor.execute(
                    self.sql.SQL(
                        "ALTER TABLE {} ADD CONSTRAINT "
                        "gateway_audit_chain_entries_entry_schema_version_check "
                        "CHECK (entry_schema_version IN (1, 2))"
                    ).format(table)
                )
                cursor.execute(
                    self.sql.SQL(
                        "ALTER TABLE {} ADD CONSTRAINT "
                        "gateway_audit_chain_entries_source_identity_check CHECK ("
                        "(entry_schema_version = 1 AND source_schema_id IS NULL "
                        "AND source_schema_version IS NULL AND source_event_id IS NULL) OR "
                        "(entry_schema_version = 2 AND ("
                        "(source_schema_id = 'hormuz.custody-control-event' AND source_schema_version = 1) OR "
                        "(source_schema_id = 'hormuz.custody-execution-attempt' AND source_schema_version = 2) OR "
                        "(source_schema_id = 'hormuz.custody-execution-event' AND source_schema_version = 1) OR "
                        "(source_schema_id = 'hormuz.custody-lifecycle-event' AND source_schema_version = 1) OR "
                        "(source_schema_id = 'hormuz.custody-envelope-attestation' AND source_schema_version = 1) OR "
                        "(source_schema_id = 'hormuz.custody-deletion-event' AND source_schema_version = 1)) "
                        "AND source_event_id IS NOT NULL))"
                    ).format(table)
                )

    def test_normalized_cross_backend_corruption_fixtures_have_exact_error_parity(self) -> None:
        cases = {
            "entry_deletion": "audit_chain_sequence_invalid",
            "source_deletion": "audit_chain_source_event_missing",
            "entry_reordering": "audit_chain_sequence_invalid",
            "truncation": "audit_chain_checkpoint_mismatch",
            "wrong_source_event": "audit_chain_source_event_mismatch",
            "invalid_predecessor": "audit_chain_predecessor_invalid",
            "checkpoint_mismatch": "audit_chain_checkpoint_mismatch",
            "schema_version_rejection": "audit_chain_entry_schema_unsupported",
        }
        for case, expected_code in cases.items():
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                self._clear_audit_evidence()
                sqlite_path = Path(temporary) / "usage.sqlite3"
                sqlite_store = UsageStore(sqlite_path)
                self._record_sqlite(sqlite_store)
                self._record_postgres()
                sqlite_checkpoint = build_audit_chain_checkpoint(
                    sqlite_store.audit_chain_head(organization_id="acme")
                )
                postgres_checkpoint = build_audit_chain_checkpoint(
                    self.store.audit_chain_head(organization_id="acme")
                )
                if case == "checkpoint_mismatch":
                    sqlite_checkpoint = {**sqlite_checkpoint, "head_digest": "f" * 64}
                    postgres_checkpoint = {**postgres_checkpoint, "head_digest": "f" * 64}
                else:
                    self._tamper_sqlite(sqlite_path, case)
                    self._tamper_postgres(case)
                try:
                    with self.assertRaises(StorageSchemaError) as sqlite_error:
                        sqlite_store.verify_audit_chain(
                            organization_id="acme",
                            checkpoint=sqlite_checkpoint,
                        )
                    with self.assertRaises(PostgresStorageError) as postgres_error:
                        self.store.verify_audit_chain(
                            organization_id="acme",
                            checkpoint=postgres_checkpoint,
                        )
                finally:
                    if case == "schema_version_rejection":
                        self._restore_postgres_schema_version_constraint()
                self.assertEqual(sqlite_error.exception.code, expected_code)
                self.assertEqual(postgres_error.exception.code, expected_code)

    def test_cross_backend_recovery_epoch_has_checkpoint_parity(self) -> None:
        self._clear_audit_evidence()
        with tempfile.TemporaryDirectory() as temporary:
            sqlite_path = Path(temporary) / "usage.sqlite3"
            sqlite_store = UsageStore(sqlite_path)
            for _ in range(2):
                sqlite_store.record(
                    identity=_identity("acme"),
                    client="codex",
                    protocol="openai",
                    requested_model="gpt-test",
                    resolved_alias="gpt-test",
                    upstream_model="gpt-upstream",
                    policy_action="allowed",
                    status="succeeded",
                )
                self.store.record(
                    identity=_identity("acme"),
                    client="codex",
                    protocol="openai",
                    requested_model="gpt-test",
                    resolved_alias="gpt-test",
                    upstream_model="gpt-upstream",
                    policy_action="allowed",
                    status="succeeded",
                )
            sqlite_checkpoint = build_audit_chain_checkpoint(
                sqlite_store.audit_chain_head(organization_id="acme")
            )
            postgres_checkpoint = build_audit_chain_checkpoint(
                self.store.audit_chain_head(organization_id="acme")
            )

            sqlite_connection = sqlite3.connect(sqlite_path)
            sqlite_connection.execute("DROP TRIGGER gateway_audit_chain_entries_no_delete")
            sqlite_digest = sqlite_connection.execute(
                "SELECT event_digest FROM gateway_audit_chain_entries WHERE sequence = 1"
            ).fetchone()[0]
            sqlite_event = sqlite_connection.execute(
                "SELECT event_id FROM gateway_audit_chain_entries WHERE sequence = 2"
            ).fetchone()[0]
            sqlite_connection.execute("DELETE FROM gateway_audit_chain_entries WHERE sequence = 2")
            sqlite_connection.execute("DELETE FROM gateway_usage_events WHERE id = ?", (sqlite_event,))
            sqlite_connection.execute(
                "UPDATE gateway_audit_chain_heads SET sequence = 1, head_digest = ? "
                "WHERE organization_id = 'acme'",
                (sqlite_digest,),
            )
            sqlite_connection.commit()
            sqlite_connection.close()

            with self.psycopg.connect(self.owner_dsn, autocommit=True) as connection:
                with connection.cursor() as cursor:
                    entries = self.sql.SQL("{}.gateway_audit_chain_entries").format(
                        self.sql.Identifier(self.schema)
                    )
                    usage = self.sql.SQL("{}.gateway_usage_events").format(
                        self.sql.Identifier(self.schema)
                    )
                    heads = self.sql.SQL("{}.gateway_audit_chain_heads").format(
                        self.sql.Identifier(self.schema)
                    )
                    cursor.execute(
                        self.sql.SQL(
                            "SELECT event_digest FROM {} WHERE organization_id = %s AND sequence = 1"
                        ).format(entries),
                        ("acme",),
                    )
                    postgres_digest = cursor.fetchone()[0]
                    cursor.execute(
                        self.sql.SQL(
                            "SELECT event_id FROM {} WHERE organization_id = %s AND sequence = 2"
                        ).format(entries),
                        ("acme",),
                    )
                    postgres_event = cursor.fetchone()[0]
                    cursor.execute(
                        self.sql.SQL(
                            "DELETE FROM {} WHERE organization_id = %s AND sequence = 2"
                        ).format(entries),
                        ("acme",),
                    )
                    cursor.execute(
                        self.sql.SQL("DELETE FROM {} WHERE organization_id = %s AND id = %s").format(usage),
                        ("acme", postgres_event),
                    )
                    cursor.execute(
                        self.sql.SQL(
                            "UPDATE {} SET sequence = 1, head_digest = %s WHERE organization_id = %s"
                        ).format(heads),
                        (postgres_digest, "acme"),
                    )

            sqlite_store.begin_audit_chain_epoch(
                checkpoint=sqlite_checkpoint,
                reason_code="restore",
            )
            self.store.begin_audit_chain_epoch(
                checkpoint=postgres_checkpoint,
                reason_code="restore",
            )
            sqlite_store.record(
                identity=_identity("acme"),
                client="codex",
                protocol="openai",
                requested_model="gpt-test",
                resolved_alias="gpt-test",
                upstream_model="gpt-upstream",
                policy_action="allowed",
                status="succeeded",
            )
            self.store.record(
                identity=_identity("acme"),
                client="codex",
                protocol="openai",
                requested_model="gpt-test",
                resolved_alias="gpt-test",
                upstream_model="gpt-upstream",
                policy_action="allowed",
                status="succeeded",
            )
            with self.assertRaises(StorageSchemaError) as sqlite_required:
                sqlite_store.verify_audit_chain(organization_id="acme")
            with self.assertRaises(PostgresStorageError) as postgres_required:
                self.store.verify_audit_chain(organization_id="acme")
            self.assertEqual(sqlite_required.exception.code, "audit_chain_checkpoint_required")
            self.assertEqual(postgres_required.exception.code, "audit_chain_checkpoint_required")

            sqlite_head = sqlite_store.verify_audit_chain(
                organization_id="acme",
                checkpoint=sqlite_checkpoint,
            )
            postgres_head = self.store.verify_audit_chain(
                organization_id="acme",
                checkpoint=postgres_checkpoint,
            )
            self.assertEqual((sqlite_head.chain_epoch, sqlite_head.sequence), (2, 1))
            self.assertEqual((postgres_head.chain_epoch, postgres_head.sequence), (2, 1))

    def test_commit_time_audit_chain_serializes_multi_instance_writes_and_is_tenant_isolated(self) -> None:
        stores = (
            PostgresUsageStore(
                self.runtime_dsn,
                organization_ids=("acme", "beta"),
                schema=self.schema,
                runtime_role=self.runtime_role,
            ),
            PostgresUsageStore(
                self.runtime_dsn,
                organization_ids=("acme", "beta"),
                schema=self.schema,
                runtime_role=self.runtime_role,
            ),
        )
        barrier = threading.Barrier(3)
        errors: list[BaseException] = []

        def append(store: PostgresUsageStore, actor: str) -> None:
            try:
                barrier.wait(timeout=10)
                store.record(
                    identity=replace(_identity("acme"), actor_id=actor, actor_name=actor.title()),
                    client="codex",
                    protocol="openai",
                    requested_model="gpt-test",
                    resolved_alias="gpt-test",
                    upstream_model="gpt-upstream",
                    policy_action="allowed",
                    status="succeeded",
                )
            except BaseException as error:  # The assertion below reports a real serialization failure.
                errors.append(error)

        threads = [
            threading.Thread(target=append, args=(stores[0], "alice")),
            threading.Thread(target=append, args=(stores[1], "bob")),
        ]
        for thread in threads:
            thread.start()
        barrier.wait(timeout=10)
        for thread in threads:
            thread.join(timeout=15)

        self.assertFalse(errors, errors)
        head = self.store.verify_audit_chain(organization_id="acme")
        self.assertEqual((head.chain_epoch, head.sequence), (1, 2))
        checkpoint = build_audit_chain_checkpoint(head)
        self.store.record_audit_chain_checkpoint(
            checkpoint=checkpoint,
            artifact_sha256="a" * 64,
            anchor_backend="test-object-lock",
            object_version="version-1",
        )
        self.assertEqual(self.store.verify_audit_chain(organization_id="acme", checkpoint=checkpoint), head)

        self.store.record(
            identity=_identity("beta"),
            client="codex",
            protocol="openai",
            requested_model="gpt-test",
            resolved_alias="gpt-test",
            upstream_model="gpt-upstream",
            policy_action="allowed",
            status="succeeded",
        )
        with self.assertRaises(PostgresStorageError) as raised:
            self.store.verify_audit_chain(organization_id="beta", checkpoint=checkpoint)
        self.assertEqual(raised.exception.code, "audit_chain_tenant_mismatch")
    def test_commit_time_audit_chain_rolls_back_and_runtime_cannot_rewrite_history(self) -> None:
        with mock.patch.object(
            self.store,
            "_append_audit_chain_entry_in_cursor",
            side_effect=PostgresStorageError("audit_chain_test_failure"),
        ):
            with self.assertRaises(PostgresStorageError) as raised:
                self.store.record(
                    identity=_identity("acme"),
                    client="codex",
                    protocol="openai",
                    requested_model="gpt-test",
                    resolved_alias="gpt-test",
                    upstream_model="gpt-upstream",
                    policy_action="allowed",
                    status="succeeded",
                )
        self.assertEqual(raised.exception.code, "audit_chain_test_failure")
        with self.psycopg.connect(self.owner_dsn) as connection:
            with connection.cursor() as cursor:
                counts = {}
                for table in ("gateway_usage_events", "gateway_audit_chain_epochs", "gateway_audit_chain_entries"):
                    cursor.execute(
                        self.sql.SQL("SELECT COUNT(*) FROM {}.{}").format(
                            self.sql.Identifier(self.schema),
                            self.sql.Identifier(table),
                        )
                    )
                    counts[table] = cursor.fetchone()[0]
        self.assertEqual(counts, {table: 0 for table in counts})

        self.store.record(
            identity=_identity("acme"),
            client="codex",
            protocol="openai",
            requested_model="gpt-test",
            resolved_alias="gpt-test",
            upstream_model="gpt-upstream",
            policy_action="allowed",
            status="succeeded",
        )
        with postgres_transaction(
            self.runtime_dsn,
            schema=self.schema,
            runtime_role=self.runtime_role,
            organization_id="acme",
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT has_table_privilege(current_user, %s, 'UPDATE') AS can_update, "
                    "has_table_privilege(current_user, %s, 'DELETE') AS can_delete",
                    (
                        f"{self.schema}.gateway_audit_chain_entries",
                        f"{self.schema}.gateway_audit_chain_entries",
                    ),
                )
                privileges = cursor.fetchone()
                self.assertFalse(privileges["can_update"])
                self.assertFalse(privileges["can_delete"])
        self.store.verify_ready()
        with self.assertRaises(PostgresStorageError) as raised:
            with postgres_transaction(
                self.runtime_dsn,
                schema=self.schema,
                runtime_role=self.runtime_role,
                organization_id="acme",
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("DELETE FROM gateway_audit_chain_entries WHERE organization_id = %s", ("acme",))
        self.assertEqual(raised.exception.code, "storage_access_denied")


if __name__ == "__main__":
    unittest.main()
