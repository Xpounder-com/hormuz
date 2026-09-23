"""Restricted-role PostgreSQL witnesses for Linear reconciliation snapshots."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import tempfile
import unittest
from dataclasses import replace

from hormuz._linear_snapshot_schema import TABLE_DDL, verify_postgres_linear_snapshot
from hormuz.config import UsageStorageConfig
from hormuz.linear_connector import LinearOutcomeReceiver
from hormuz.linear_snapshot import LinearSnapshotReceiver
from hormuz.portfolio_repository import create_portfolio_repository
from hormuz.postgres import (
    POSTGRES_SCHEMA_VERSION,
    PostgresStorageError,
    postgres_transaction,
    verify_postgres_schema,
)

if __package__:
    from ._postgres_fixture import PostgresTestCase
    from .test_linear_connector_runtime import NOW_MS, encoded, payload, signed
    from .test_linear_snapshot_runtime import (
        snapshot_config,
        snapshot_headers,
        snapshot_payload,
    )
else:
    from _postgres_fixture import PostgresTestCase
    from test_linear_connector_runtime import NOW_MS, encoded, payload, signed
    from test_linear_snapshot_runtime import (
        snapshot_config,
        snapshot_headers,
        snapshot_payload,
    )


@unittest.skipUnless(
    os.environ.get("HORMUZ_TEST_POSTGRES_DSN"),
    "requires disposable PostgreSQL",
)
class PostgresLinearSnapshotRuntimeTests(PostgresTestCase):
    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.config = replace(
            snapshot_config(Path(temporary.name)),
            usage_storage=UsageStorageConfig(
                backend="postgresql",
                postgres_schema=self.schema,
                postgres_runtime_role=self.runtime_role,
            ),
        )
        self.repositories = create_portfolio_repository(
            self.config,
            environ={"HORMUZ_POSTGRES_DSN": self.runtime_dsn},
        )
        self.receiver = LinearSnapshotReceiver(
            self.config,
            self.repositories.linear,
        )
        self.webhook = LinearOutcomeReceiver(
            self.config,
            self.repositories.linear,
        )

    def transaction(self, organization: str = "acme"):
        return postgres_transaction(
            self.runtime_dsn,
            schema=self.schema,
            runtime_role=self.runtime_role,
            organization_id=organization,
        )

    def rows(self, table: str, *, organization: str = "acme") -> list[dict]:
        with self.transaction(organization) as connection:
            return [dict(row) for row in connection.execute(
                f"SELECT * FROM {table}",
            ).fetchall()]

    def ingest(self, value: dict | None = None, *, now_ms: int = NOW_MS) -> dict:
        raw = encoded(snapshot_payload() if value is None else value)
        return self.receiver.ingest(
            snapshot_headers(raw, now_ms=now_ms),
            raw,
            now_ms=now_ms,
        )

    def test_schema20_restricted_commit_replay_rls_audit_and_no_outcome(self):
        self.assertEqual(POSTGRES_SCHEMA_VERSION, 20)
        status = verify_postgres_schema(
            self.runtime_dsn,
            schema=self.schema,
            runtime_role=self.runtime_role,
        )
        self.assertEqual((status.version, status.complete), (20, True))

        original_raw = encoded(snapshot_payload())
        original_headers = snapshot_headers(original_raw)
        accepted = self.receiver.ingest(
            original_headers,
            original_raw,
            now_ms=NOW_MS,
        )
        self.assertEqual(
            (accepted["disposition"], accepted["accepted_event_count"]),
            ("accepted", 1),
        )
        self.assertEqual(
            {table: len(self.rows(table)) for table in TABLE_DDL},
            {
                "gateway_linear_snapshot_receipts": 1,
                "portfolio_linear_snapshot_context_events": 1,
            },
        )
        self.assertEqual(self.rows("portfolio_outcome_receipts"), [])
        self.assertEqual(self.rows("portfolio_outcome_events"), [])
        self.assertTrue(all(not self.rows(table, organization="beta") for table in TABLE_DDL))
        head = self.store.verify_audit_chain(organization_id="acme")
        self.assertEqual(head.sequence, 3)

        before = {table: self.rows(table) for table in TABLE_DDL}
        self.assertEqual(
            self.receiver.ingest(
                original_headers,
                original_raw,
                now_ms=NOW_MS + 300_001,
            ),
            accepted,
        )
        self.assertEqual({table: self.rows(table) for table in TABLE_DDL}, before)

        with self.transaction() as connection:
            with self.assertRaises(self.psycopg.Error):
                connection.execute(
                    "UPDATE gateway_linear_snapshot_receipts SET page_count=2"
                )

    def test_cross_capture_sequence_and_identity_are_storage_enforced(self):
        webhook_value = payload(action="update", updatedFrom={"title": "before"})
        raw = encoded(webhook_value)
        self.webhook.ingest(signed(raw), raw, now_ms=NOW_MS)

        snapshot_value = snapshot_payload(
            page_id="90000000-0000-4000-8000-000000000005",
            updated_at="2026-09-23T12:01:00Z",
        )
        snapshot_value["snapshot_id"] = "90000000-0000-4000-8000-000000000006"
        self.ingest(snapshot_value, now_ms=NOW_MS + 60_000)

        webhook_context = json.loads(
            self.rows("portfolio_linear_context_events")[0]["evidence_json"]
        )
        snapshot_row = self.rows("portfolio_linear_snapshot_context_events")[0]
        snapshot_context = json.loads(snapshot_row["evidence_json"])
        self.assertEqual(
            (webhook_context["commit_sequence"], snapshot_context["commit_sequence"]),
            (1, 2),
        )
        self.assertEqual(
            snapshot_context["supersedes_context_event_id"],
            webhook_context["context_event_id"],
        )

        conflicting = dict(snapshot_row)
        conflicting["context_event_id"] = webhook_context["context_event_id"]
        conflicting["commit_sequence"] = 3
        conflicting["source_fact_fingerprint"] = "f" * 64
        columns = tuple(conflicting)
        with self.transaction() as connection:
            with self.assertRaisesRegex(
                self.psycopg.errors.UniqueViolation,
                "linear_context_cross_capture_conflict",
            ):
                connection.execute(
                    "INSERT INTO portfolio_linear_snapshot_context_events ("
                    + ",".join(columns)
                    + ") VALUES ("
                    + ",".join("%s" for _ in columns)
                    + ")",
                    tuple(conflicting[column] for column in columns),
                )

        with self.psycopg.connect(self.owner_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self.sql.SQL(
                        """
                        CREATE OR REPLACE FUNCTION {}.enforce_linear_context_cross_capture()
                        RETURNS trigger
                        LANGUAGE plpgsql
                        SECURITY DEFINER
                        SET search_path = pg_catalog
                        AS $$ BEGIN RETURN NEW; END; $$
                        """
                    ).format(self.sql.Identifier(self.schema))
                )
                with self.assertRaisesRegex(
                    PostgresStorageError,
                    "storage_schema_partial_upgrade",
                ):
                    verify_postgres_linear_snapshot(
                        cursor,
                        self.schema,
                        PostgresStorageError,
                    )
            connection.rollback()

    def test_snapshot_page_set_consistency_is_storage_enforced(self):
        first = snapshot_payload()
        first["page_count"] = 2
        self.ingest(first)
        base = dict(self.rows("gateway_linear_snapshot_receipts")[0])
        for field, value in (
            ("binding_version", 2),
            ("reconciliation_id", "90000000-0000-4000-8000-000000000009"),
            ("page_count", 3),
            ("captured_at", "2026-09-23T11:59:59.000000Z"),
        ):
            conflicting = dict(base)
            conflicting.update({
                "receipt_id": "90000000-0000-4000-8000-000000000007",
                "page_id": "90000000-0000-4000-8000-000000000008",
                "page_number": 2,
                "body_fingerprint": "f" * 64,
                field: value,
            })
            columns = tuple(conflicting)
            with self.subTest(field=field), self.transaction() as connection:
                with self.assertRaisesRegex(
                    self.psycopg.errors.UniqueViolation,
                    "linear_snapshot_page_set_conflict",
                ):
                    connection.execute(
                        "INSERT INTO gateway_linear_snapshot_receipts ("
                        + ",".join(columns)
                        + ") VALUES ("
                        + ",".join("%s" for _ in columns)
                        + ")",
                    tuple(conflicting[column] for column in columns),
                )

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


if __name__ == "__main__":
    unittest.main()
