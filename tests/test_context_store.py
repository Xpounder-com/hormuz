from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier

from hormuz.context import ContextPrincipal, ContextRecord
from hormuz.context_store import (
    ContextConflict,
    ContextStoreError,
    SQLiteContextRepository,
)
from hormuz.store import UsageStore


NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


def record(record_id: str, **overrides) -> ContextRecord:
    values = {
        "record_id": record_id,
        "record_kind": "decision",
        "title": f"Decision {record_id}",
        "content": f"Verified engineering guidance for {record_id}.",
        "owner_id": "alice",
        "organization_id": "xpounder",
        "visibility": "organization",
        "scope_id": "xpounder",
        "classification": "internal",
        "source_uri": "https://example.test/adr",
        "source_revision": "git:abc123",
        "source_item_key": record_id,
        "repository_id": None,
        "branch": None,
        "verification": "verified",
        "verification_evidence": ("ci:passed", "review:approved"),
        "effective_at": NOW - timedelta(days=1),
        "verified_at": NOW - timedelta(days=1),
        "invalidation_rules": ("source_revision_changed",),
        "tags": ("reliability",),
    }
    values.update(overrides)
    return ContextRecord(**values)


class CountingCodec:
    codec_id = "test-counting-v1"

    def __init__(self) -> None:
        self.decode_calls = 0

    def encode(self, plaintext: bytes) -> bytes:
        return plaintext

    def decode(self, stored: bytes) -> bytes:
        self.decode_calls += 1
        return stored


class ContextStoreTests(unittest.TestCase):
    def test_checked_in_sample_source_hash_is_real_and_importable(self) -> None:
        value = json.loads(
            (ROOT / "examples/context-records.jsonl").read_text(encoding="utf-8")
        )
        sample = ContextRecord.from_dict(value)
        source = ROOT / "examples/sources/adr-0017.md"

        self.assertEqual(
            hashlib.sha256(source.read_bytes()).hexdigest(),
            sample.source_sha256,
        )
        self.assertIn("examples/sources/adr-0017.md", sample.source_uri)

    def test_ingest_is_idempotent_and_audit_is_metadata_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "context.sqlite3"
            repository = SQLiteContextRepository(path)
            original = record(
                "retry-standard",
                content="SECRET-CONTENT use bounded retries.",
                title="SECRET-TITLE",
                source_uri="https://example.test/private/adr-7",
                source_sha256="a" * 64,
            )

            first = repository.ingest(original, actor_id="alice", policy_version="policy-7")
            second = repository.ingest(original, actor_id="alice", policy_version="policy-7")

            self.assertTrue(first.created)
            self.assertFalse(second.created)
            self.assertEqual(first.stored.version, 1)
            self.assertEqual(second.stored.version, 1)
            self.assertEqual(first.stored.record.content, original.content)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

            events = repository.audit_events(organization_id="xpounder")
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["action"], "ingest")
            serialized = json.dumps(events)
            self.assertNotIn("SECRET-CONTENT", serialized)
            self.assertNotIn("SECRET-TITLE", serialized)
            self.assertNotIn("private/adr-7", serialized)

    def test_source_identity_and_record_id_conflicts_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = SQLiteContextRepository(Path(temporary) / "context.sqlite3")
            repository.ingest(record("one"), actor_id="alice", policy_version="p1")

            with self.assertRaisesRegex(ContextConflict, "context_source_revision_conflict"):
                repository.ingest(
                    record("two", source_item_key="one", content="Different content"),
                    actor_id="alice",
                    policy_version="p1",
                )
            with self.assertRaisesRegex(ContextConflict, "context_record_id_conflict"):
                repository.ingest(
                    record("one", source_revision="git:different"),
                    actor_id="alice",
                    policy_version="p1",
                )

    def test_batch_ingest_rolls_back_records_and_audit_on_late_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "context.sqlite3"
            repository = SQLiteContextRepository(path)
            repository.ingest(record("existing"), actor_id="alice", policy_version="p1")

            with self.assertRaisesRegex(ContextConflict, "context_record_id_conflict"):
                repository.ingest_many(
                    [
                        record("would-have-been-created"),
                        record("existing", source_revision="git:different"),
                    ],
                    actor_id="alice",
                    policy_version="p2",
                )

            connection = sqlite3.connect(path)
            try:
                record_ids = {
                    str(row[0])
                    for row in connection.execute("SELECT id FROM context_records")
                }
                audit_count = connection.execute(
                    "SELECT COUNT(*) FROM context_mutation_events"
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(record_ids, {"existing"})
            self.assertEqual(audit_count, 1)

    def test_optimistic_concurrency_allows_exactly_one_writer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "context.sqlite3"
            SQLiteContextRepository(path).ingest(
                record("concurrent"),
                actor_id="alice",
                policy_version="p1",
            )
            barrier = Barrier(2)

            def update(content: str) -> str:
                repository = SQLiteContextRepository(path)
                barrier.wait()
                try:
                    repository.update(
                        record(
                            "concurrent",
                            content=content,
                            source_revision="git:" + content.replace(" ", "-"),
                        ),
                        expected_version=1,
                        actor_id="alice",
                        policy_version="p2",
                    )
                    return "updated"
                except ContextConflict:
                    return "conflict"

            with ThreadPoolExecutor(max_workers=2) as executor:
                outcomes = list(executor.map(update, ("writer one", "writer two")))

            self.assertCountEqual(outcomes, ["updated", "conflict"])
            events = SQLiteContextRepository(path).audit_events(organization_id="xpounder")
            self.assertEqual([event["action"] for event in events], ["ingest", "update"])

    def test_verified_records_require_evidence_and_source_revisions_are_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = SQLiteContextRepository(Path(temporary) / "context.sqlite3")
            with self.assertRaisesRegex(ContextStoreError, "context_verified_evidence_required"):
                repository.ingest(
                    record("missing-evidence", verification_evidence=()),
                    actor_id="alice",
                    policy_version="p1",
                )
            repository.ingest(record("immutable"), actor_id="alice", policy_version="p1")
            with self.assertRaisesRegex(ContextConflict, "context_source_revision_immutable"):
                repository.update(
                    record("immutable", content="changed without a new source revision"),
                    expected_version=1,
                    actor_id="alice",
                    policy_version="p2",
                )

    def test_authorization_and_freshness_filter_before_content_decode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            codec = CountingCodec()
            repository = SQLiteContextRepository(Path(temporary) / "context.sqlite3", codec=codec)
            candidates = [
                record("allowed", repository_id="acme/api", branch="main"),
                record("other-org", organization_id="other", scope_id="other"),
                record("other-team", visibility="team", scope_id="marketing"),
                record("other-actor", visibility="actor", scope_id="bob"),
                record("restricted", classification="restricted"),
                record("other-repo", repository_id="acme/web"),
                record("other-branch", repository_id="acme/api", branch="feature"),
                record("future", effective_at=NOW + timedelta(days=1)),
                record("expired", expires_at=NOW),
                record(
                    "provisional",
                    verification="provisional",
                    verified_at=None,
                ),
            ]
            for candidate in candidates:
                repository.ingest(candidate, actor_id="operator", policy_version="p1")
            codec.decode_calls = 0

            authorized = repository.list_authorized(
                ContextPrincipal(
                    organization_id="xpounder",
                    team_id="engineering",
                    actor_id="alice",
                    clearance="internal",
                    repository_id="acme/api",
                    branch="main",
                ),
                as_of=NOW,
            )

            self.assertEqual([item.record.record_id for item in authorized], ["allowed"])
            self.assertEqual(codec.decode_calls, 1)

            connection = sqlite3.connect(repository.path)
            try:
                connection.execute(
                    "UPDATE context_records SET classification_rank = 0 "
                    "WHERE organization_id = 'xpounder' AND id = 'allowed'"
                )
                connection.commit()
            finally:
                connection.close()
            codec.decode_calls = 0
            with self.assertRaisesRegex(ContextStoreError, "context_record_classification_corrupt"):
                repository.list_authorized(
                    ContextPrincipal(
                        organization_id="xpounder",
                        team_id="engineering",
                        actor_id="alice",
                        clearance="internal",
                        repository_id="acme/api",
                        branch="main",
                    ),
                    as_of=NOW,
                )
            self.assertEqual(codec.decode_calls, 0)

    def test_supersession_cycle_reference_and_physical_delete_guards(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "context.sqlite3"
            repository = SQLiteContextRepository(path)
            repository.ingest(record("old"), actor_id="alice", policy_version="p1")
            repository.ingest(
                record("new", supersedes_id="old"),
                actor_id="alice",
                policy_version="p1",
            )

            with self.assertRaisesRegex(ContextConflict, "context_supersession_cycle"):
                repository.update(
                    record("old", supersedes_id="new"),
                    expected_version=1,
                    actor_id="alice",
                    policy_version="p2",
                )
            with self.assertRaisesRegex(ContextConflict, "context_record_has_superseding_reference"):
                repository.delete(
                    organization_id="xpounder",
                    record_id="old",
                    expected_version=1,
                    actor_id="alice",
                    policy_version="p2",
                )

            repository.delete(
                organization_id="xpounder",
                record_id="new",
                expected_version=1,
                actor_id="alice",
                policy_version="p2",
            )
            self.assertNotIn(b"Verified engineering guidance for new", path.read_bytes())

    def test_content_integrity_and_export_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "context.sqlite3"
            repository = SQLiteContextRepository(path)
            original = record("round-trip")
            stored = repository.ingest(original, actor_id="alice", policy_version="p1").stored
            restored = ContextRecord.from_dict(stored.record.to_dict())
            self.assertEqual(restored, stored.record)

            connection = sqlite3.connect(path)
            try:
                connection.execute(
                    "UPDATE context_records SET content = ? WHERE organization_id = ? AND id = ?",
                    (b"tampered", "xpounder", "round-trip"),
                )
                connection.commit()
            finally:
                connection.close()
            with self.assertRaisesRegex(ContextStoreError, "context_content_integrity_failed"):
                repository.list_authorized(
                    ContextPrincipal("xpounder", "engineering", "alice"),
                    as_of=NOW,
                )

    def test_usage_and_context_schemas_are_separate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            usage_path = root / "usage.sqlite3"
            context_path = root / "context.sqlite3"
            UsageStore(usage_path)
            SQLiteContextRepository(context_path)

            def tables(path: Path) -> set[str]:
                connection = sqlite3.connect(path)
                try:
                    return {
                        str(row[0])
                        for row in connection.execute(
                            "SELECT name FROM sqlite_master WHERE type = 'table'"
                        )
                    }
                finally:
                    connection.close()

            self.assertNotIn("context_records", tables(usage_path))
            self.assertNotIn("usage_events", tables(context_path))


if __name__ == "__main__":
    unittest.main()
