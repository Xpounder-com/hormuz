"""Provider-free authentication and atomic storage witnesses for Linear snapshots."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import hmac
import http.client
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest import mock

from hormuz.linear_connector import (
    LinearOutcomeReceiver,
    NORMALIZER_DIGEST,
    SNAPSHOT_NORMALIZER_DIGEST,
)
from hormuz.linear_evidence import validate_linear_evidence
from hormuz.linear_snapshot import (
    LINEAR_SNAPSHOTS_PATH,
    LinearSnapshotAdapter,
    LinearSnapshotAuthenticator,
    LinearSnapshotReceiver,
)
from hormuz.outcome_connector_config import (
    build_outcome_connector_config,
    resolve_outcome_connector_credentials,
)
from hormuz.outcome_wire import REQUEST_BYTES
from hormuz.portfolio_repository import create_portfolio_repository
from hormuz.portfolio_wire import PortfolioError, canonical
from hormuz.server import GatewayServer, serve_in_thread
from hormuz.store import UsageStore

if __package__:
    from ._registry_transition_fixture import sqlite_snapshot
    from .test_linear_connector_runtime import (
        CYCLE,
        IDENTITY_KEY,
        ISSUE,
        NOW_MS,
        PROJECT,
        TEAM,
        WEBHOOK_SECRET,
        WORKSPACE,
        encoded,
        linear_base_config,
        payload,
        runtime_document,
        signed,
    )
else:
    from _registry_transition_fixture import sqlite_snapshot
    from test_linear_connector_runtime import (
        CYCLE,
        IDENTITY_KEY,
        ISSUE,
        NOW_MS,
        PROJECT,
        TEAM,
        WEBHOOK_SECRET,
        WORKSPACE,
        encoded,
        linear_base_config,
        payload,
        runtime_document,
        signed,
    )


SNAPSHOT_SECRET = "synthetic-linear-snapshot-secret-12345678"
RECONCILIATION = "90000000-0000-4000-8000-000000000001"
SNAPSHOT = "90000000-0000-4000-8000-000000000002"
PAGE = "90000000-0000-4000-8000-000000000003"
RETENTION = "90000000-0000-4000-8000-000000000010"


def snapshot_document() -> dict:
    document = runtime_document()
    document["schema_version"] = 3
    channel = document["linear"][0]
    channel["active_snapshot_secret"] = {
        "version": "snapshot-v1",
        "environment_variable": "SYNTHETIC_LINEAR_SNAPSHOT_SECRET",
    }
    channel["previous_snapshot_secret"] = None
    return document


def snapshot_config(root: Path):
    base = linear_base_config(root)
    unresolved = build_outcome_connector_config(
        snapshot_document(),
        base.portfolio_control,
    )
    resolved = resolve_outcome_connector_credentials(unresolved, {
        "SYNTHETIC_LINEAR_WEBHOOK_SECRET": WEBHOOK_SECRET,
        "SYNTHETIC_LINEAR_SNAPSHOT_SECRET": SNAPSHOT_SECRET,
        "SYNTHETIC_LINEAR_IDENTITY_KEY": IDENTITY_KEY,
    })
    return replace(base, outcome_connectors=resolved)


def snapshot_payload(*, page_id: str = PAGE, updated_at: str = "2026-09-23T12:00:00Z") -> dict:
    return {
        "schema_id": "hormuz.linear-authorized-snapshot",
        "schema_version": 1,
        "workspace_id": WORKSPACE,
        "reconciliation_id": RECONCILIATION,
        "snapshot_id": SNAPSHOT,
        "page_id": page_id,
        "page_number": 1,
        "page_count": 1,
        "captured_at": updated_at,
        "items": [{
            "type": "Issue",
            "data": {
                "id": ISSUE,
                "teamId": TEAM,
                "projectId": PROJECT,
                "cycleId": CYCLE,
                "updatedAt": updated_at,
                "startedAt": updated_at,
            },
        }],
    }


def snapshot_headers(raw: bytes, *, secret: str = SNAPSHOT_SECRET, now_ms: int = NOW_MS):
    timestamp_value = str(now_ms)
    signature = hmac.new(
        secret.encode("ascii"),
        timestamp_value.encode("ascii") + b"." + raw,
        hashlib.sha256,
    ).hexdigest()
    return {
        "X-Hormuz-Linear-Snapshot-Signature": signature,
        "X-Hormuz-Linear-Snapshot-Timestamp": timestamp_value,
    }


def snapshot_retention_event(target_context_event_id: str) -> dict:
    return {
        "schema_id": "hormuz.linear-context-retention",
        "schema_version": 1,
        "organization_id": "acme",
        "connector_id": "linear-one",
        "retention_event_id": RETENTION,
        "target_context_event_id": target_context_event_id,
        "actor_id": "alice",
        "reader_role": "portfolio_admin",
        "reason_code": "tombstoned",
        "observed_at": "2026-09-23T12:01:00.000000Z",
        "ingested_at": "2026-09-23T12:01:00.000000Z",
        "provenance_digest": "a" * 64,
    }


class LinearSnapshotConfigAndAuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.base = linear_base_config(self.root)
        self.config = snapshot_config(self.root)
        self.channel = self.config.outcome_connectors.linear[0]
        self.authenticator = LinearSnapshotAuthenticator(self.config, self.channel)
        self.adapter = LinearSnapshotAdapter(self.channel)

    def test_v3_requires_dedicated_snapshot_secret_and_rejects_secret_reuse(self):
        unresolved = build_outcome_connector_config(
            snapshot_document(),
            self.base.portfolio_control,
        )
        self.assertIsNone(unresolved.linear[0].active_snapshot_secret.value)
        self.assertNotIn(SNAPSHOT_SECRET, repr(unresolved))
        self.assertEqual(
            {kind for kind, _value in self.config.outcome_connectors.protected_values()},
            {"linear_webhook_secret", "linear_snapshot_secret", "outcome_identity_key"},
        )

        invalid = snapshot_document()
        invalid["linear"][0]["active_snapshot_secret"] = None
        with self.assertRaisesRegex(
            ValueError,
            "outcome_connector_configuration_invalid",
        ):
            build_outcome_connector_config(invalid, self.base.portfolio_control)

        with self.assertRaisesRegex(
            ValueError,
            "outcome_connector_credentials_unavailable",
        ):
            resolve_outcome_connector_credentials(unresolved, {
                "SYNTHETIC_LINEAR_WEBHOOK_SECRET": WEBHOOK_SECRET,
                "SYNTHETIC_LINEAR_SNAPSHOT_SECRET": WEBHOOK_SECRET,
                "SYNTHETIC_LINEAR_IDENTITY_KEY": IDENTITY_KEY,
            })

    def test_signature_precedes_parse_and_payload_is_metadata_only_and_scoped(self):
        malformed = b'{"duplicate":1,"duplicate":2}'
        with self.assertRaises(PortfolioError) as caught:
            self.authenticator.authenticate(
                snapshot_headers(malformed, secret="x" * 40),
                malformed,
                now_ms=NOW_MS,
            )
        self.assertEqual(caught.exception.code, "unauthenticated")
        with self.assertRaises(PortfolioError) as caught:
            self.authenticator.authenticate(
                snapshot_headers(malformed),
                malformed,
                now_ms=NOW_MS,
            )
        self.assertEqual(caught.exception.code, "invalid_request")

        for mutate, code in (
            (lambda value: value.update(workspace_id="90000000-0000-4000-8000-000000000099"), "forbidden"),
            (lambda value: value["items"][0]["data"].update(id="90000000-0000-4000-8000-000000000099"), "forbidden"),
            (lambda value: value["items"][0]["data"].update(title="private"), "invalid_request"),
            (lambda value: value.update(schema_version=True), "invalid_request"),
        ):
            value = snapshot_payload()
            mutate(value)
            raw = encoded(value)
            with self.subTest(code=code), self.assertRaises(PortfolioError) as caught:
                self.authenticator.authenticate(
                    snapshot_headers(raw),
                    raw,
                    now_ms=NOW_MS,
                )
            self.assertEqual(caught.exception.code, code)

        value = snapshot_payload()
        raw = encoded(value)
        verified, body = self.authenticator.authenticate(
            snapshot_headers(raw),
            raw,
            now_ms=NOW_MS,
        )
        boundary, _ = self.authenticator.authenticate(
            snapshot_headers(raw),
            raw,
            now_ms=NOW_MS + 300_000,
        )
        stale, _ = self.authenticator.authenticate(
            snapshot_headers(raw),
            raw,
            now_ms=NOW_MS + 300_001,
        )
        self.assertTrue(boundary.fresh)
        self.assertFalse(stale.fresh)
        projections = self.adapter.normalize(verified, body)
        self.assertEqual(len(projections), 1)
        self.assertIsNone(projections[0].outcome)
        self.assertNotIn("title", json.dumps(projections[0].source_fact))

    def test_request_item_and_page_limits_fail_closed(self):
        for raw in (
            b"x" * (REQUEST_BYTES + 1),
            encoded({**snapshot_payload(), "page_count": 101}),
            encoded({**snapshot_payload(), "items": snapshot_payload()["items"] * 101}),
        ):
            with self.subTest(length=len(raw)), self.assertRaises(PortfolioError) as caught:
                self.authenticator.authenticate(
                    snapshot_headers(raw),
                    raw,
                    now_ms=NOW_MS,
                )
            self.assertEqual(caught.exception.code, "invalid_request")

    def test_lifecycle_and_state_timestamps_cannot_exceed_capture_time(self):
        for field in ("archivedAt", "completedAt", "canceledAt", "startedAt"):
            value = snapshot_payload()
            value["items"][0]["data"][field] = "2026-09-23T12:00:01Z"
            raw = encoded(value)
            verified, body = self.authenticator.authenticate(
                snapshot_headers(raw),
                raw,
                now_ms=NOW_MS,
            )
            with self.subTest(field=field), self.assertRaises(PortfolioError) as caught:
                self.adapter.normalize(verified, body)
            self.assertEqual(caught.exception.code, "invalid_request")


class LinearSnapshotSQLiteRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = snapshot_config(self.root)
        UsageStore(self.config.database_path)
        self.repositories = create_portfolio_repository(self.config)
        self.receiver = LinearSnapshotReceiver(self.config, self.repositories.linear)
        self.webhook = LinearOutcomeReceiver(self.config, self.repositories.linear)

    def rows(self, table: str) -> list[dict]:
        connection = sqlite3.connect(self.config.database_path)
        connection.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in connection.execute(
                f"SELECT * FROM {table}",
            ).fetchall()]
        finally:
            connection.close()

    def ingest(self, value: dict | None = None, *, now_ms: int = NOW_MS) -> dict:
        raw = encoded(snapshot_payload() if value is None else value)
        return self.receiver.ingest(
            snapshot_headers(raw, now_ms=now_ms),
            raw,
            now_ms=now_ms,
        )

    def test_atomic_page_keeps_snapshot_provenance_separate_and_emits_no_outcome(self):
        receipt = self.ingest()
        self.assertEqual(
            (receipt["disposition"], receipt["accepted_event_count"]),
            ("accepted", 1),
        )
        self.assertEqual(len(self.rows("gateway_linear_snapshot_receipts")), 1)
        self.assertEqual(len(self.rows("portfolio_linear_snapshot_context_events")), 1)
        self.assertEqual(self.rows("gateway_linear_delivery_receipts"), [])
        self.assertEqual(self.rows("portfolio_linear_context_events"), [])
        self.assertEqual(self.rows("portfolio_outcome_receipts"), [])
        self.assertEqual(self.rows("portfolio_outcome_events"), [])

        context = json.loads(
            self.rows("portfolio_linear_snapshot_context_events")[0]["evidence_json"]
        )
        self.assertEqual(
            (context["capture_kind"], context["source_delivery_id"]),
            ("authorized_snapshot", PAGE),
        )
        self.assertEqual(
            context["normalizer"],
            {
                "id": "linear-authorized-snapshot-normalizer",
                "version": 1,
                "content_digest": SNAPSHOT_NORMALIZER_DIGEST,
            },
        )
        self.assertNotEqual(SNAPSHOT_NORMALIZER_DIGEST, NORMALIZER_DIGEST)
        validate_linear_evidence("hormuz.linear-context-event", context)
        wrong_normalizer = json.loads(json.dumps(context))
        wrong_normalizer["normalizer"] = {
            "id": "linear-webhook-normalizer",
            "version": 1,
            "content_digest": NORMALIZER_DIGEST,
        }
        with self.assertRaisesRegex(ValueError, "linear_evidence_invalid"):
            validate_linear_evidence(
                "hormuz.linear-context-event",
                wrong_normalizer,
            )
        sources = [
            row["source_schema_id"]
            for row in self.rows("gateway_audit_chain_entries")
        ]
        self.assertIn("hormuz.linear-snapshot-receipt", sources)
        self.assertIn("hormuz.linear-context-event", sources)
        database_bytes = self.config.database_path.read_bytes()
        self.assertNotIn(b"synthetic-linear-snapshot-secret", database_bytes)

    def test_exact_replay_conflicting_page_and_stale_unknown_fail_closed(self):
        original_raw = encoded(snapshot_payload())
        original_headers = snapshot_headers(original_raw)
        first = self.receiver.ingest(
            original_headers,
            original_raw,
            now_ms=NOW_MS,
        )
        before = sqlite_snapshot(self.config.database_path)
        self.assertEqual(
            self.receiver.ingest(
                original_headers,
                original_raw,
                now_ms=NOW_MS + 300_001,
            ),
            first,
        )
        self.assertEqual(sqlite_snapshot(self.config.database_path), before)

        changed = snapshot_payload()
        changed["items"][0]["data"]["updatedAt"] = "2026-09-23T11:59:59Z"
        raw = encoded(changed)
        with self.assertRaises(PortfolioError) as caught:
            self.receiver.ingest(
                snapshot_headers(raw),
                raw,
                now_ms=NOW_MS,
            )
        self.assertEqual(caught.exception.code, "idempotency_conflict")
        self.assertEqual(sqlite_snapshot(self.config.database_path), before)

        unknown = snapshot_payload(
            page_id="90000000-0000-4000-8000-000000000004",
        )
        raw = encoded(unknown)
        with self.assertRaises(PortfolioError) as caught:
            self.receiver.ingest(
                snapshot_headers(raw),
                raw,
                now_ms=NOW_MS + 300_001,
            )
        self.assertEqual(caught.exception.code, "unauthenticated")
        self.assertEqual(sqlite_snapshot(self.config.database_path), before)

    def test_snapshot_pages_require_one_binding_reconciliation_count_and_capture(self):
        first = snapshot_payload()
        first["page_count"] = 2
        self.assertEqual(self.ingest(first)["disposition"], "accepted")
        before = sqlite_snapshot(self.config.database_path)

        for field, value in (
            ("page_count", 3),
            ("reconciliation_id", "90000000-0000-4000-8000-000000000009"),
            ("captured_at", "2026-09-23T11:59:59Z"),
        ):
            page = snapshot_payload(
                page_id="90000000-0000-4000-8000-000000000004",
            )
            page["page_number"] = 2
            page["page_count"] = 2
            page[field] = value
            if field == "captured_at":
                page["items"][0]["data"]["updatedAt"] = value
                page["items"][0]["data"]["startedAt"] = value
            with self.subTest(field=field), self.assertRaises(PortfolioError) as caught:
                self.ingest(page)
            self.assertEqual(caught.exception.code, "idempotency_conflict")
            self.assertEqual(sqlite_snapshot(self.config.database_path), before)

        direct = dict(self.rows("gateway_linear_snapshot_receipts")[0])
        direct.update({
            "receipt_id": "90000000-0000-4000-8000-000000000007",
            "page_id": "90000000-0000-4000-8000-000000000008",
            "page_number": 2,
            "binding_version": 2,
            "body_fingerprint": "f" * 64,
        })
        columns = tuple(direct)
        connection = sqlite3.connect(self.config.database_path)
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "linear_snapshot_page_set_conflict",
            ):
                connection.execute(
                    "INSERT INTO gateway_linear_snapshot_receipts ("
                    + ",".join(columns)
                    + ") VALUES ("
                    + ",".join("?" for _ in columns)
                    + ")",
                    tuple(direct[column] for column in columns),
                )
        finally:
            connection.close()

    def test_webhook_and_snapshot_share_ordering_without_duplicate_context(self):
        update = payload(action="update", updatedFrom={"title": "before"})
        update["createdAt"] = "2026-09-23T11:59:00Z"
        raw = encoded(update)
        webhook_receipt = self.webhook.ingest(
            signed(raw),
            raw,
            now_ms=NOW_MS,
        )
        self.assertEqual(webhook_receipt["accepted_event_count"], 1)
        snapshot_receipt = self.ingest()
        self.assertEqual(
            (snapshot_receipt["disposition"], snapshot_receipt["accepted_event_count"]),
            ("duplicate", 0),
        )
        self.assertEqual(len(self.rows("portfolio_linear_context_events")), 1)
        self.assertEqual(self.rows("portfolio_linear_snapshot_context_events"), [])

        newer = snapshot_payload(
            page_id="90000000-0000-4000-8000-000000000005",
            updated_at="2026-09-23T12:01:00Z",
        )
        newer["snapshot_id"] = "90000000-0000-4000-8000-000000000006"
        receipt = self.ingest(newer, now_ms=NOW_MS + 60_000)
        self.assertEqual(receipt["accepted_event_count"], 1)
        webhook_context = json.loads(
            self.rows("portfolio_linear_context_events")[0]["evidence_json"]
        )
        snapshot_context = json.loads(
            self.rows("portfolio_linear_snapshot_context_events")[0]["evidence_json"]
        )
        self.assertEqual(
            snapshot_context["supersedes_context_event_id"],
            webhook_context["context_event_id"],
        )
        self.assertEqual(
            (webhook_context["commit_sequence"], snapshot_context["commit_sequence"]),
            (1, 2),
        )
        self.assertNotEqual(
            webhook_context["event_at"],
            webhook_context["revision"]["value"],
        )

    def test_snapshot_context_retention_is_fk_bound_and_auditable(self):
        self.ingest()
        context_id = self.rows("portfolio_linear_snapshot_context_events")[0][
            "context_event_id"
        ]
        event = snapshot_retention_event(context_id)
        validate_linear_evidence("hormuz.linear-context-retention", event)
        with self.repositories.linear._transaction(
            "acme",
            time.monotonic() + 4,
        ) as sql:
            sql.insert("portfolio_linear_snapshot_context_retention_events", {
                "organization_id": event["organization_id"],
                "connector_id": event["connector_id"],
                "retention_event_id": event["retention_event_id"],
                "target_context_event_id": event["target_context_event_id"],
                "actor_id": event["actor_id"],
                "reason_code": event["reason_code"],
                "observed_at": event["observed_at"],
                "ingested_at": event["ingested_at"],
                "provenance_digest": event["provenance_digest"],
                "evidence_json": canonical(event),
            })
            self.repositories.linear._append_audit(
                sql,
                event,
                "hormuz.linear-context-retention",
            )
        self.assertEqual(
            len(self.rows("portfolio_linear_snapshot_context_retention_events")),
            1,
        )
        self.assertIn(
            RETENTION,
            {
                row["source_event_id"]
                for row in self.rows("gateway_audit_chain_entries")
                if row["source_schema_id"] == "hormuz.linear-context-retention"
            },
        )

        invalid = dict(self.rows("portfolio_linear_snapshot_context_retention_events")[0])
        invalid.update({
            "retention_event_id": "90000000-0000-4000-8000-000000000011",
            "target_context_event_id": "90000000-0000-4000-8000-000000000012",
        })
        connection = sqlite3.connect(self.config.database_path)
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO portfolio_linear_snapshot_context_retention_events ("
                    + ",".join(invalid)
                    + ") VALUES ("
                    + ",".join("?" for _ in invalid)
                    + ")",
                    tuple(invalid.values()),
                )
        finally:
            connection.close()

        webhook_value = payload(action="create")
        raw = encoded(webhook_value)
        self.webhook.ingest(signed(raw), raw, now_ms=NOW_MS)
        webhook_context_id = self.rows("portfolio_linear_context_events")[0][
            "context_event_id"
        ]
        collision = dict(
            self.rows("portfolio_linear_snapshot_context_retention_events")[0]
        )
        collision["target_context_event_id"] = webhook_context_id
        connection = sqlite3.connect(self.config.database_path)
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "linear_retention_cross_table_conflict",
            ):
                connection.execute(
                    "INSERT INTO portfolio_linear_context_retention_events ("
                    + ",".join(collision)
                    + ") VALUES ("
                    + ",".join("?" for _ in collision)
                    + ")",
                    tuple(collision.values()),
                )
        finally:
            connection.close()

    def test_context_audit_failure_rolls_back_the_complete_page(self):
        original = self.repositories.linear._append_audit

        def fail(sql, event, schema_id):
            if schema_id == "hormuz.linear-context-event":
                raise RuntimeError("synthetic snapshot failure")
            return original(sql, event, schema_id)

        before = sqlite_snapshot(self.config.database_path)
        with mock.patch.object(
            self.repositories.linear,
            "_append_audit",
            side_effect=fail,
        ):
            with self.assertRaisesRegex(RuntimeError, "synthetic snapshot failure"):
                self.ingest()
        self.assertEqual(sqlite_snapshot(self.config.database_path), before)
        self.assertEqual(self.ingest()["disposition"], "accepted")

    def test_concurrent_pages_commit_one_context_and_all_receipts(self):
        pages = []
        for ordinal in range(1, 5):
            value = snapshot_payload(
                page_id=f"90000000-0000-4000-8000-{ordinal + 10:012d}",
            )
            value["page_number"] = ordinal
            value["page_count"] = 4
            raw = encoded(value)
            pages.append((snapshot_headers(raw), raw))

        def ingest(page):
            headers, raw = page
            return self.receiver.ingest(headers, raw, now_ms=NOW_MS)

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(ingest, pages))
        self.assertEqual(sum(item["accepted_event_count"] for item in results), 1)
        self.assertEqual(len(self.rows("gateway_linear_snapshot_receipts")), 4)
        self.assertEqual(len(self.rows("portfolio_linear_snapshot_context_events")), 1)


class LinearSnapshotHTTPRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.config = snapshot_config(Path(temporary.name))
        UsageStore(self.config.database_path)
        self.server = GatewayServer(
            self.config,
            environ={"SYNTHETIC_PROVIDER_KEY": "synthetic-provider-key"},
        )
        self.thread = serve_in_thread(self.server)
        self.addCleanup(self.close_gateway)

    def close_gateway(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)

    def test_http_route_acknowledges_only_after_snapshot_commit(self):
        captured = datetime.now(timezone.utc)
        captured_at = captured.isoformat(timespec="milliseconds").replace(
            "+00:00",
            "Z",
        )
        signed_at = int(captured.timestamp() * 1000)
        raw = encoded(snapshot_payload(updated_at=captured_at))
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            self.server.server_port,
            timeout=10,
        )
        connection.request(
            "POST",
            LINEAR_SNAPSHOTS_PATH,
            body=raw,
            headers={
                "Content-Type": "application/json",
                **snapshot_headers(raw, now_ms=signed_at),
            },
        )
        response = connection.getresponse()
        body = json.loads(response.read())
        headers = dict(response.getheaders())
        connection.close()

        self.assertEqual((response.status, body["disposition"]), (200, "accepted"))
        self.assertIn(
            "hormuz.connector-ingest-receipt",
            headers["X-Hormuz-Contract"],
        )
        database = sqlite3.connect(self.config.database_path)
        try:
            self.assertEqual(
                database.execute(
                    "SELECT count(*) FROM gateway_linear_snapshot_receipts"
                ).fetchone(),
                (1,),
            )
        finally:
            database.close()


if __name__ == "__main__":
    unittest.main()
