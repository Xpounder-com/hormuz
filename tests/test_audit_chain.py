from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock

from hormuz.audit_chain import (
    AuditChainError,
    build_custody_audit_chain_entry,
    build_audit_chain_checkpoint,
    parse_audit_chain_checkpoint,
    serialize_audit_chain_checkpoint,
    verify_audit_chain_entry,
)
from hormuz.config import Identity
from hormuz.store import StorageSchemaError, UsageStore


def _identity(organization_id: str, actor_id: str = "alice") -> Identity:
    return Identity(
        token_env="TEST_TOKEN",
        token="test-credential",
        actor_id=actor_id,
        actor_name=actor_id.title(),
        team_id="engineering",
        team_name="Engineering",
        organization_id=organization_id,
    )


def _record(store: UsageStore, identity: Identity, *, status: str = "succeeded") -> str:
    return store.record(
        identity=identity,
        client="codex",
        protocol="openai",
        requested_model="engineering-fast",
        resolved_alias="engineering-fast",
        upstream_model="gpt-provider-fast",
        policy_action="allowed",
        status=status,
        input_tokens=12,
        output_tokens=3,
        cost_microusd=42,
    )


class CommitTimeAuditChainTests(unittest.TestCase):
    def test_v2_custody_entry_has_a_strict_source_union_while_v1_remains_readable(self) -> None:
        fixtures = json.loads((Path(__file__).parent / "fixtures" / "contracts" / "valid-v1.json").read_text())
        event = fixtures["custody_control_event"]
        source_event_id = "01234567-89ab-4def-8123-456789abcdef"
        entry = build_custody_audit_chain_entry(
            event,
            source_schema_id="hormuz.custody-control-event",
            source_schema_version=1,
            source_event_id=source_event_id,
            chain_version=1,
            chain_epoch=1,
            sequence=1,
            previous_digest=None,
        )

        self.assertEqual(entry["schema_version"], 2)
        self.assertEqual(
            verify_audit_chain_entry(
                entry,
                expected_organization_id="xpounder",
                expected_chain_version=1,
                expected_chain_epoch=1,
                expected_sequence=1,
                expected_previous_digest=None,
                source_event=event,
            ),
            entry["event_digest"],
        )
        with self.assertRaises(AuditChainError) as unsupported_source:
            build_custody_audit_chain_entry(
                {"arbitrary": "json"},
                source_schema_id="hormuz.unreviewed-source",
                source_schema_version=1,
                source_event_id=source_event_id,
                chain_version=1,
                chain_epoch=1,
                sequence=1,
                previous_digest=None,
            )
        self.assertEqual(unsupported_source.exception.code, "audit_chain_event_malformed")

    def test_events_commit_to_independent_tenant_chains_and_remain_content_free(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "usage.sqlite3"
            store = UsageStore(path)
            acme = _identity("acme")
            beta = _identity("beta", "bob")

            _record(store, acme)
            store.record_secret_event(
                identity=acme,
                client="codex",
                protocol="openai",
                requested_model="engineering-fast",
                action="redacted",
                detection_count=1,
                rules=("openai_api_key",),
            )
            _record(store, beta)

            acme_head = store.verify_audit_chain(organization_id="acme")
            beta_head = store.verify_audit_chain(organization_id="beta")
            self.assertEqual((acme_head.chain_epoch, acme_head.sequence), (1, 2))
            self.assertEqual((beta_head.chain_epoch, beta_head.sequence), (1, 1))
            self.assertNotEqual(acme_head.head_digest, beta_head.head_digest)

            connection = sqlite3.connect(path)
            event_json = connection.execute(
                "SELECT event_json FROM gateway_audit_chain_entries WHERE organization_id = ? ORDER BY sequence",
                ("acme",),
            ).fetchall()
            connection.close()
            self.assertEqual(len(event_json), 2)
            self.assertNotIn("prompt", repr(event_json))
            self.assertNotIn("test-credential", repr(event_json))

    def test_event_chain_head_and_source_event_roll_back_together(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "usage.sqlite3"
            store = UsageStore(path)
            with mock.patch.object(
                store,
                "_append_audit_chain_entry_in_connection",
                side_effect=StorageSchemaError("audit_chain_test_failure"),
            ):
                with self.assertRaises(StorageSchemaError) as raised:
                    _record(store, _identity("acme"))
            self.assertEqual(raised.exception.code, "audit_chain_test_failure")

            connection = sqlite3.connect(path)
            counts = {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "gateway_usage_events",
                    "gateway_audit_chain_epochs",
                    "gateway_audit_chain_heads",
                    "gateway_audit_chain_entries",
                )
            }
            connection.close()
            self.assertEqual(counts, {table: 0 for table in counts})

    def test_concurrent_store_instances_serialize_one_tenant_head(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "usage.sqlite3"
            first = UsageStore(path)
            second = UsageStore(path)
            barrier = threading.Barrier(3)
            errors: list[BaseException] = []

            def append(store: UsageStore, actor: str) -> None:
                try:
                    barrier.wait(timeout=5)
                    _record(store, _identity("acme", actor))
                except BaseException as error:  # Test captures unexpected database races.
                    errors.append(error)

            threads = [
                threading.Thread(target=append, args=(first, "alice")),
                threading.Thread(target=append, args=(second, "bob")),
            ]
            for thread in threads:
                thread.start()
            barrier.wait(timeout=5)
            for thread in threads:
                thread.join(timeout=10)

            self.assertFalse(errors, errors)
            head = first.verify_audit_chain(organization_id="acme")
            self.assertEqual((head.chain_epoch, head.sequence), (1, 2))

    def test_alteration_deletion_reordering_and_truncation_are_detected(self) -> None:
        cases = {
            "source_deletion": self._tamper_source_deletion,
            "entry_alteration": self._tamper_entry_alteration,
            "entry_reordering": self._tamper_entry_reordering,
            "truncation": self._tamper_truncation,
            "head_deletion": self._tamper_head_deletion,
        }
        for name, tamper in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "usage.sqlite3"
                store = UsageStore(path)
                identity = _identity("acme")
                _record(store, identity)
                _record(store, identity)
                _record(store, identity)
                checkpoint = build_audit_chain_checkpoint(store.audit_chain_head(organization_id="acme"))
                tamper(path)
                with self.assertRaises(StorageSchemaError) as raised:
                    store.verify_audit_chain(organization_id="acme", checkpoint=checkpoint)
                self.assertIn(
                    raised.exception.code,
                    {
                        "audit_chain_source_event_missing",
                        "audit_chain_entry_malformed",
                        "audit_chain_sequence_invalid",
                        "audit_chain_checkpoint_mismatch",
                        "audit_chain_head_mismatch",
                    },
                )

    @staticmethod
    def _tamper_source_deletion(path: Path) -> None:
        connection = sqlite3.connect(path)
        event_id = connection.execute(
            "SELECT event_id FROM gateway_audit_chain_entries WHERE sequence = 2"
        ).fetchone()[0]
        connection.execute("DELETE FROM gateway_usage_events WHERE id = ?", (event_id,))
        connection.commit()
        connection.close()

    @staticmethod
    def _tamper_entry_alteration(path: Path) -> None:
        connection = sqlite3.connect(path)
        connection.execute("DROP TRIGGER gateway_audit_chain_entries_no_update")
        connection.execute(
            "UPDATE gateway_audit_chain_entries SET event_json = ? WHERE sequence = 2",
            ('{"not":"an-audit-event"}',),
        )
        connection.commit()
        connection.close()

    @staticmethod
    def _tamper_entry_reordering(path: Path) -> None:
        connection = sqlite3.connect(path)
        connection.execute("DROP TRIGGER gateway_audit_chain_entries_no_update")
        connection.execute("UPDATE gateway_audit_chain_entries SET sequence = 4 WHERE sequence = 2")
        connection.commit()
        connection.close()

    @staticmethod
    def _tamper_truncation(path: Path) -> None:
        connection = sqlite3.connect(path)
        connection.execute("DROP TRIGGER gateway_audit_chain_entries_no_delete")
        digest = connection.execute(
            "SELECT event_digest FROM gateway_audit_chain_entries WHERE sequence = 2"
        ).fetchone()[0]
        connection.execute("DELETE FROM gateway_audit_chain_entries WHERE sequence = 3")
        connection.execute(
            "UPDATE gateway_audit_chain_heads SET sequence = 2, head_digest = ? WHERE organization_id = ?",
            (digest, "acme"),
        )
        connection.commit()
        connection.close()

    @staticmethod
    def _tamper_head_deletion(path: Path) -> None:
        connection = sqlite3.connect(path)
        connection.execute("DELETE FROM gateway_audit_chain_heads WHERE organization_id = ?", ("acme",))
        connection.commit()
        connection.close()

    def test_sqlite_runtime_cannot_rewrite_or_delete_historical_chain_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "usage.sqlite3"
            store = UsageStore(path)
            _record(store, _identity("acme"))
            checkpoint = build_audit_chain_checkpoint(store.audit_chain_head(organization_id="acme"))
            store.record_audit_chain_checkpoint(
                checkpoint=checkpoint,
                artifact_sha256="c" * 64,
                anchor_backend="test-object-lock",
                object_version="version-1",
            )

            connection = sqlite3.connect(path)
            mutations = (
                "UPDATE gateway_audit_chain_entries SET event_json = event_json",
                "DELETE FROM gateway_audit_chain_entries",
                "UPDATE gateway_audit_chain_checkpoints SET anchor_backend = anchor_backend",
                "DELETE FROM gateway_audit_chain_checkpoints",
            )
            for statement in mutations:
                with self.subTest(statement=statement):
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(statement)
                    connection.rollback()
            connection.close()

    def test_checkpoint_rejects_cross_tenant_verification_and_preserves_canonical_form(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = UsageStore(Path(temporary) / "usage.sqlite3")
            _record(store, _identity("acme"))
            checkpoint = build_audit_chain_checkpoint(store.audit_chain_head(organization_id="acme"))
            serialized = serialize_audit_chain_checkpoint(checkpoint)
            self.assertEqual(parse_audit_chain_checkpoint(serialized), checkpoint)
            with self.assertRaises(StorageSchemaError) as raised:
                store.verify_audit_chain(organization_id="beta", checkpoint=checkpoint)
            self.assertEqual(raised.exception.code, "audit_chain_tenant_mismatch")

    def test_explicit_recovery_epoch_links_an_older_backup_to_a_trusted_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            primary_path = root / "primary.sqlite3"
            backup_path = root / "older-backup.sqlite3"
            primary = UsageStore(primary_path)
            identity = _identity("acme")
            _record(primary, identity)
            shutil.copy2(primary_path, backup_path)
            _record(primary, identity)
            checkpoint = build_audit_chain_checkpoint(primary.audit_chain_head(organization_id="acme"))

            recovered = UsageStore(backup_path)
            recovered_head = recovered.begin_audit_chain_epoch(checkpoint=checkpoint, reason_code="restore")
            self.assertEqual((recovered_head.chain_epoch, recovered_head.sequence), (2, 0))
            _record(recovered, identity)
            with self.assertRaises(StorageSchemaError) as required:
                recovered.verify_audit_chain(organization_id="acme")
            self.assertEqual(required.exception.code, "audit_chain_checkpoint_required")
            verified = recovered.verify_audit_chain(organization_id="acme", checkpoint=checkpoint)
            self.assertEqual((verified.chain_epoch, verified.sequence), (2, 1))

    def test_anchor_age_uses_only_local_checkpoint_receipts_and_unanchored_events(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = UsageStore(Path(temporary) / "usage.sqlite3")
            now = datetime.now(timezone.utc)
            idle = store.audit_chain_anchor_status(
                organization_id="acme",
                maximum_age_seconds=60,
                now=now + timedelta(days=1),
            )
            self.assertFalse(idle.overdue)

            _record(store, _identity("acme"))
            stale = store.audit_chain_anchor_status(
                organization_id="acme",
                maximum_age_seconds=60,
                now=now + timedelta(days=1),
            )
            self.assertTrue(stale.overdue)
            checkpoint = build_audit_chain_checkpoint(store.audit_chain_head(organization_id="acme"))
            store.record_audit_chain_checkpoint(
                checkpoint=checkpoint,
                artifact_sha256="b" * 64,
                anchor_backend="test-object-lock",
                object_version="version-1",
                anchored_at=now,
            )
            current = store.audit_chain_anchor_status(
                organization_id="acme",
                maximum_age_seconds=60,
                now=now + timedelta(days=1),
            )
            self.assertFalse(current.overdue)
            self.assertIsNone(current.oldest_unanchored_at)

    def test_configured_anchor_age_degrades_readiness_without_object_lock_io(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "usage.sqlite3"
            store = UsageStore(
                path,
                audit_chain_maximum_anchor_age_seconds=60,
                audit_chain_organization_ids=("acme",),
            )
            _record(store, _identity("acme"))
            future = datetime.now(timezone.utc) + timedelta(days=1)

            class _FutureDatetime(datetime):
                @classmethod
                def now(cls, tz: timezone | None = None) -> datetime:
                    return future if tz is None else future.astimezone(tz)

            with mock.patch("hormuz.store.datetime", _FutureDatetime):
                with self.assertRaises(StorageSchemaError) as raised:
                    store.verify_ready()
            self.assertEqual(raised.exception.code, "audit_chain_anchor_overdue")


if __name__ == "__main__":
    unittest.main()
