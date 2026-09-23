"""Provider-free Linear connector proofs on the restricted PostgreSQL role."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from hormuz._linear_schema import BINDING_TABLE, POSTGRES_CLAIM_TABLE, TABLE_DDL
from hormuz.config import UsageStorageConfig
from hormuz.linear_connector import LinearOutcomeReceiver
from hormuz.portfolio_repository import create_portfolio_repository
from hormuz.portfolio_wire import PortfolioError
from hormuz.postgres import (
    POSTGRES_SCHEMA_VERSION,
    PostgresStorageError,
    postgres_transaction,
    verify_postgres_schema,
)

if __package__:
    from ._postgres_fixture import PostgresTestCase
    from .test_linear_connector_runtime import (
        DELIVERY,
        NOW_MS,
        encoded,
        payload,
        runtime_config,
        signed,
    )
else:
    from _postgres_fixture import PostgresTestCase
    from test_linear_connector_runtime import (
        DELIVERY,
        NOW_MS,
        encoded,
        payload,
        runtime_config,
        signed,
    )


@unittest.skipUnless(
    os.environ.get("HORMUZ_TEST_POSTGRES_DSN"),
    "requires disposable PostgreSQL",
)
class PostgresLinearConnectorRuntimeTests(PostgresTestCase):
    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.config = replace(
            runtime_config(Path(temporary.name)),
            usage_storage=UsageStorageConfig(
                backend="postgresql",
                postgres_schema=self.schema,
                postgres_runtime_role=self.runtime_role,
            ),
        )
        self.repositories = self.restart()
        self.receiver = LinearOutcomeReceiver(
            self.config,
            self.repositories.linear,
        )

    def restart(self, *, dsn: str | None = None):
        return create_portfolio_repository(
            self.config,
            environ={"HORMUZ_POSTGRES_DSN": dsn or self.runtime_dsn},
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

    def counts(self) -> dict[str, int]:
        names = (
            *TABLE_DDL,
            "portfolio_outcome_receipts",
            "portfolio_outcome_events",
            "portfolio_outcome_coverage_events",
            "gateway_audit_chain_entries",
        )
        with self.transaction() as connection:
            counts = {
                table: int(connection.execute(
                    f"SELECT count(*) AS count FROM {table}",
                ).fetchone()["count"])
                for table in names
            }
        with self.psycopg.connect(self.owner_dsn) as connection:
            counts[POSTGRES_CLAIM_TABLE] = int(connection.execute(
                self.sql.SQL("SELECT count(*) FROM {}.{}").format(
                    self.sql.Identifier(self.schema),
                    self.sql.Identifier(POSTGRES_CLAIM_TABLE),
                )
            ).fetchone()[0])
        return counts

    def ingest(
        self,
        value: dict | None = None,
        *,
        delivery: str = DELIVERY,
        now_ms: int = NOW_MS,
        receiver: LinearOutcomeReceiver | None = None,
    ) -> dict:
        raw = encoded(payload() if value is None else value)
        return (receiver or self.receiver).ingest(
            signed(raw, delivery=delivery),
            raw,
            now_ms=now_ms,
        )

    def test_schema20_restricted_commit_replay_rls_and_content_exclusion(self):
        self.assertEqual(POSTGRES_SCHEMA_VERSION, 20)
        status = verify_postgres_schema(
            self.runtime_dsn,
            schema=self.schema,
            runtime_role=self.runtime_role,
        )
        self.assertEqual((status.version, status.complete), (20, True))
        with self.psycopg.connect(self.owner_dsn) as connection:
            table_count = connection.execute(
                "SELECT count(*) FROM pg_tables WHERE schemaname=%s",
                (self.schema,),
            ).fetchone()[0]
        self.assertEqual(table_count, 79)

        accepted = self.ingest()
        self.assertEqual(
            (accepted["disposition"], accepted["accepted_event_count"]),
            ("accepted", 2),
        )
        self.assertEqual(
            {table: len(self.rows(table)) for table in TABLE_DDL},
            {
                "portfolio_linear_source_binding_versions": 1,
                "gateway_linear_delivery_receipts": 1,
                "portfolio_linear_context_events": 1,
                "portfolio_linear_context_retention_events": 0,
            },
        )
        with self.psycopg.connect(self.owner_dsn) as connection:
            claims = connection.execute(
                self.sql.SQL(
                    "SELECT claim_kind,organization_id,connector_id "
                    "FROM {}.{} ORDER BY claim_kind"
                ).format(
                    self.sql.Identifier(self.schema),
                    self.sql.Identifier(POSTGRES_CLAIM_TABLE),
                )
            ).fetchall()
        self.assertEqual(
            claims,
            [("webhook", "acme", "linear-one"), ("workspace", "acme", None)],
        )
        with self.assertRaisesRegex(PostgresStorageError, "storage_access_denied"):
            with self.transaction() as connection:
                connection.execute(f"SELECT * FROM {POSTGRES_CLAIM_TABLE}")
        self.assertTrue(all(not self.rows(table, organization="beta") for table in TABLE_DDL))

        restarted = self.restart()
        replay_receiver = LinearOutcomeReceiver(self.config, restarted.linear)
        before = self.counts()
        self.assertEqual(self.ingest(receiver=replay_receiver), accepted)
        self.assertEqual(self.counts(), before)

        with self.psycopg.connect(self.owner_dsn) as connection:
            serialized = json.dumps([
                row[0]
                for table in (
                    *TABLE_DDL,
                    "portfolio_outcome_receipts",
                    "portfolio_outcome_events",
                    "gateway_audit_chain_entries",
                )
                for row in connection.execute(
                    self.sql.SQL("SELECT row_to_json(t)::text FROM {}.{} t").format(
                        self.sql.Identifier(self.schema),
                        self.sql.Identifier(table),
                    )
                ).fetchall()
            ])
        self.assertNotIn("SYNTHETIC_PRIVATE_LINEAR_TITLE", serialized)
        self.assertNotIn("SYNTHETIC_PRIVATE_LINEAR_DESCRIPTION", serialized)

    def test_concurrent_replay_commits_one_receipt(self):
        raw = encoded(payload())
        headers = signed(raw)

        def ingest(_index: int) -> dict:
            return self.receiver.ingest(headers, raw, now_ms=NOW_MS)

        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(ingest, range(6)))
        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual(len(self.rows("gateway_linear_delivery_receipts")), 1)
        self.assertEqual(len(self.rows("portfolio_linear_context_events")), 1)

    def test_binding_version_cannot_roll_back_after_successor_commit(self):
        self.ingest()
        channel = self.config.outcome_connectors.linear[0]
        successor = replace(channel, binding_version=2)
        successor_config = replace(
            self.config,
            outcome_connectors=replace(
                self.config.outcome_connectors,
                linear=(successor,),
            ),
        )
        successor_receiver = LinearOutcomeReceiver(
            successor_config,
            create_portfolio_repository(
                successor_config,
                environ={"HORMUZ_POSTGRES_DSN": self.runtime_dsn},
            ).linear,
        )
        update = payload(action="update", updatedFrom={"title": "before"})
        update["data"]["updatedAt"] = "2026-09-23T12:00:01Z"
        self.ingest(
            update,
            delivery="60000000-0000-4000-8000-000000000010",
            receiver=successor_receiver,
        )
        before = self.counts()
        rollback = payload(action="update", updatedFrom={"startedAt": None})
        rollback["data"]["updatedAt"] = "2026-09-23T12:00:02Z"
        with self.assertRaises(PortfolioError) as caught:
            self.ingest(
                rollback,
                delivery="60000000-0000-4000-8000-000000000011",
            )
        self.assertEqual(caught.exception.code, "version_conflict")
        self.assertEqual(self.counts(), before)

    def test_append_only_grants_and_storage_cardinality_fail_closed(self):
        self.ingest()
        for table in TABLE_DDL:
            qualified = self.sql.SQL("{}.{}").format(
                self.sql.Identifier(self.schema),
                self.sql.Identifier(table),
            )
            with self.subTest(table=table, owner="runtime"):
                with self.psycopg.connect(self.runtime_dsn) as connection:
                    with self.assertRaises(self.psycopg.errors.InsufficientPrivilege):
                        connection.execute(
                            self.sql.SQL("UPDATE {} SET organization_id=organization_id").format(
                                qualified,
                            )
                        )
            with self.subTest(table=table, owner="migration"):
                with self.psycopg.connect(self.owner_dsn) as connection:
                    with self.assertRaisesRegex(
                        self.psycopg.errors.CheckViolation,
                        "portfolio_append_only",
                    ):
                        connection.execute(
                            self.sql.SQL("UPDATE {} SET organization_id=organization_id").format(
                                qualified,
                            )
                        )

        with self.psycopg.connect(
            self.owner_dsn,
            row_factory=self.psycopg.rows.dict_row,
        ) as connection:
            source = dict(connection.execute(
                self.sql.SQL("SELECT * FROM {}.{}").format(
                    self.sql.Identifier(self.schema),
                    self.sql.Identifier(BINDING_TABLE),
                )
            ).fetchone())

        def conflict(**changes):
            row = {**source, **changes}
            with self.psycopg.connect(self.owner_dsn) as connection:
                with self.assertRaisesRegex(
                    self.psycopg.errors.CheckViolation,
                    "linear_binding_cardinality_conflict",
                ):
                    connection.execute(
                        self.sql.SQL("INSERT INTO {}.{} ({}) VALUES ({})").format(
                            self.sql.Identifier(self.schema),
                            self.sql.Identifier(BINDING_TABLE),
                            self.sql.SQL(", ").join(
                                self.sql.Identifier(column) for column in row
                            ),
                            self.sql.SQL(", ").join(
                                self.sql.Placeholder() for _ in row
                            ),
                        ),
                        tuple(row.values()),
                    )

        conflict(
            organization_id="beta",
            connector_id="linear-two",
            binding_event_id="90000000-0000-4000-8000-000000000001",
            source_webhook_id="90000000-0000-4000-8000-000000000002",
            request_digest="1" * 64,
        )
        conflict(
            connector_id="linear-two",
            binding_event_id="90000000-0000-4000-8000-000000000003",
            source_workspace_id="90000000-0000-4000-8000-000000000004",
            request_digest="2" * 64,
        )

        runtime_conflict = {
            **source,
            "organization_id": "beta",
            "connector_id": "linear-three",
            "binding_event_id": "90000000-0000-4000-8000-000000000005",
            "source_webhook_id": "90000000-0000-4000-8000-000000000006",
            "request_digest": "3" * 64,
        }
        with self.transaction("beta") as connection:
            with self.assertRaisesRegex(
                self.psycopg.errors.CheckViolation,
                "linear_binding_cardinality_conflict",
            ):
                connection.execute(
                    "INSERT INTO portfolio_linear_source_binding_versions "
                    f"({', '.join(runtime_conflict)}) VALUES "
                    f"({', '.join('%s' for _ in runtime_conflict)})",
                    tuple(runtime_conflict.values()),
                )

    def test_staged_failure_and_storage_outage_publish_nothing(self):
        repository = self.repositories.linear
        original = repository._append_audit

        def fail(sql, event, schema_id):
            if schema_id == "hormuz.linear-delivery-receipt":
                raise RuntimeError("synthetic failure before commit")
            return original(sql, event, schema_id)

        before = self.counts()
        with mock.patch.object(repository, "_append_audit", side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
                self.ingest()
        self.assertEqual(self.counts(), before)

        unavailable = self.restart(
            dsn="host=/unused/SYNTHETIC_EXCLUDED dbname=synthetic "
            "user=synthetic connect_timeout=1"
        )
        receiver = LinearOutcomeReceiver(self.config, unavailable.linear)
        with self.assertRaises(PortfolioError) as caught:
            self.ingest(receiver=receiver)
        self.assertEqual(caught.exception.code, "unavailable")
        self.assertEqual(self.counts(), before)

    def test_runtime_verifier_rejects_missing_cardinality_trigger(self):
        before = self.counts()
        drop = self.sql.SQL(
            "DROP TRIGGER portfolio_linear_binding_cardinality "
            "ON {}.portfolio_linear_source_binding_versions"
        ).format(self.sql.Identifier(self.schema))
        restore = self.sql.SQL(
            "CREATE TRIGGER portfolio_linear_binding_cardinality "
            "BEFORE INSERT ON {}.portfolio_linear_source_binding_versions "
            "FOR EACH ROW EXECUTE FUNCTION {}.enforce_linear_binding_cardinality()"
        ).format(self.sql.Identifier(self.schema), self.sql.Identifier(self.schema))
        with self.psycopg.connect(self.owner_dsn) as connection:
            connection.execute(drop)
        try:
            with self.assertRaises(PortfolioError) as caught:
                self.ingest()
            self.assertEqual(caught.exception.code, "unavailable")
        finally:
            with self.psycopg.connect(self.owner_dsn) as connection:
                connection.execute(restore)
        self.assertEqual(self.counts(), before)


if __name__ == "__main__":
    unittest.main()
