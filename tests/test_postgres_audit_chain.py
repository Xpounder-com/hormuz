from __future__ import annotations

from dataclasses import replace
import threading
import unittest
from unittest import mock

from hormuz.audit_chain import build_audit_chain_checkpoint
from hormuz.postgres import PostgresStorageError, postgres_transaction
from hormuz.postgres_usage_store import PostgresUsageStore
if __package__:
    from ._postgres_fixture import PostgresTestCase, _identity
else:  # Isolated wheel compatibility discovery uses the tests directory as its import root.
    from _postgres_fixture import PostgresTestCase, _identity


class PostgresAuditChainTests(PostgresTestCase):
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
