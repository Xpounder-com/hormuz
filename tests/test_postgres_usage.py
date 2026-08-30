from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock

from hormuz.evidence import EvidenceStorageError
from hormuz.postgres import PostgresStorageError, postgres_transaction
from hormuz.postgres_usage_store import PostgresUsageStore
from hormuz.store import ReservationDenied, ReservationScope, UsageStore
if __package__:
    from ._postgres_fixture import FIXTURES, PostgresTestCase, _identity, _normalized_events
    from ._usage_repository_contract import exercise_usage_repository, ledger_clock, read_usage_repository
else:  # Isolated wheel compatibility discovery uses the tests directory as its import root.
    from _postgres_fixture import FIXTURES, PostgresTestCase, _identity, _normalized_events
    from _usage_repository_contract import exercise_usage_repository, ledger_clock, read_usage_repository


class PostgresUsageEvidenceTests(PostgresTestCase):
    def test_all_v1_operations_have_equivalent_normalized_outcomes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            sqlite_store = UsageStore(Path(temporary) / "usage.sqlite3")
            self.assertEqual(
                exercise_usage_repository(self, sqlite_store),
                exercise_usage_repository(self, self.store),
            )

    def test_read_operations_preserve_rows_and_the_existing_locking_contract(self) -> None:
        transaction = self.store._transaction

        def durable_rows():
            tables = (
                "hormuz_schema_migrations", "gateway_usage_events", "gateway_secret_events",
                "gateway_budget_reservations", "gateway_request_attempts", "gateway_request_attempt_events",
                "gateway_audit_chain_epochs", "gateway_audit_chain_heads", "gateway_audit_chain_entries",
                "gateway_audit_chain_checkpoints",
            )
            rows = {}
            with self.psycopg.connect(self.owner_dsn) as connection:
                with connection.cursor() as cursor:
                    for table in tables:
                        cursor.execute(self.sql.SQL(
                            "SELECT row_to_json(t)::text FROM {}.{} AS t ORDER BY row_to_json(t)::text"
                        ).format(self.sql.Identifier(self.schema), self.sql.Identifier(table)))
                        rows[table] = cursor.fetchall()
            return rows

        @contextmanager
        def read_only_transaction(organization_id):
            with transaction(organization_id) as connection:
                connection.execute("SET TRANSACTION READ ONLY")
                yield connection

        for populated in (False, True):
            with self.subTest(populated=populated):
                if populated:
                    exercise_usage_repository(self, self.store)
                before = durable_rows()
                # FOR SHARE protects a consistent verification snapshot; it
                # is not a durable mutation but SQL READ ONLY disallows it.
                self.store.verify_audit_chain(organization_id="acme")
                with (
                    mock.patch.object(self.store, "_transaction", side_effect=read_only_transaction),
                    ledger_clock(),
                ):
                    read_usage_repository(self.store, verify_chain=False)
                    with self.assertRaises(PostgresStorageError) as raised:
                        self.store.verify_audit_chain(organization_id="acme")
                    self.assertEqual(raised.exception.code, "storage_unavailable")
                    with self.assertRaises(PostgresStorageError):
                        self.store.audit_chain_head(organization_id="acme")
                self.assertEqual(durable_rows(), before)

    def test_sqlite_and_postgres_have_equivalent_normalized_outcomes(self) -> None:
        identity = _identity("acme")
        with tempfile.TemporaryDirectory() as temporary:
            sqlite_store = UsageStore(Path(temporary) / "usage.sqlite3")
            for store in (sqlite_store, self.store):
                store.record(
                    identity=identity,
                    client="codex",
                    protocol="openai",
                    requested_model="gpt-test",
                    resolved_alias="gpt-test",
                    upstream_model="gpt-upstream",
                    provider_reported_model="gpt-provider",
                    policy_version="policy-v1",
                    policy_action="allowed+redacted",
                    status="succeeded",
                    input_tokens=101,
                    output_tokens=23,
                    cache_read_tokens=11,
                    reasoning_tokens=7,
                    cost_microusd=1234,
                    redaction_count=1,
                    redaction_rules=("openai_api_key",),
                )
                store.record_secret_event(
                    identity=identity,
                    client="codex",
                    protocol="openai",
                    requested_model="gpt-test",
                    policy_version="policy-v1",
                    action="redacted",
                    detection_count=1,
                    rules=("openai_api_key",),
                )
            sqlite_events = sqlite_store.audit_events(
                since="2000-01-01T00:00:00+00:00",
                organization_id="acme",
            )
            postgres_events = self.store.audit_events(
                since="2000-01-01T00:00:00+00:00",
                organization_id="acme",
            )
            self.assertEqual(_normalized_events(sqlite_events), _normalized_events(postgres_events))
            self.assertEqual(
                sqlite_store.report_rows(group_by="organization", organization_id="acme"),
                self.store.report_rows(group_by="organization", organization_id="acme"),
            )

            attempt_scope = (ReservationScope(name="organization", cost_limit_microusd=10_000),)
            for store in (sqlite_store, self.store):
                terminal_attempt = store.begin_request_attempt(
                    identity=identity,
                    client="codex",
                    protocol="openai",
                    requested_model="gpt-terminal",
                    resolved_alias="gpt-terminal",
                    upstream_model="gpt-upstream",
                    policy_version="policy-v3",
                    policy_action="allowed",
                    redaction_count=0,
                    redaction_rules=(),
                    scopes=attempt_scope,
                    reserved_tokens=20,
                    reserved_cost_microusd=100,
                    ttl_seconds=60,
                )
                store.finalize_request_attempt(
                    attempt=terminal_attempt,
                    organization_id="acme",
                    status="succeeded",
                    input_tokens=10,
                    output_tokens=2,
                    cost_microusd=80,
                )
                unknown_attempt = store.begin_request_attempt(
                    identity=identity,
                    client="codex",
                    protocol="openai",
                    requested_model="gpt-unknown",
                    resolved_alias="gpt-unknown",
                    upstream_model="gpt-upstream",
                    policy_version="policy-v3",
                    policy_action="allowed",
                    redaction_count=0,
                    redaction_rules=(),
                    scopes=attempt_scope,
                    reserved_tokens=30,
                    reserved_cost_microusd=200,
                    ttl_seconds=60,
                )
                self.assertTrue(
                    store.mark_request_attempt_outcome_unknown(
                        attempt=unknown_attempt,
                        organization_id="acme",
                        reason_code="provider_transport_ambiguous",
                    )
                )

            sqlite_connection = sqlite3.connect(sqlite_store.path)
            sqlite_attempts = [
                (str(row[0]), int(row[1]), str(row[2]), row[3], bool(row[4]))
                for row in sqlite_connection.execute(
                    """
                    SELECT root.requested_model, event.sequence, event.state,
                           event.reason_code, event.usage_event_id IS NOT NULL
                    FROM gateway_request_attempts AS root
                    JOIN gateway_request_attempt_events AS event ON event.attempt_id = root.attempt_id
                    WHERE root.organization_id = ?
                    ORDER BY root.requested_model, event.sequence
                    """,
                    ("acme",),
                ).fetchall()
            ]
            sqlite_connection.close()
            with postgres_transaction(
                self.runtime_dsn,
                schema=self.schema,
                runtime_role=self.runtime_role,
                organization_id="acme",
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT root.requested_model, event.sequence, event.state,
                               event.reason_code, event.usage_event_id IS NOT NULL AS has_usage_event
                        FROM gateway_request_attempts AS root
                        JOIN gateway_request_attempt_events AS event ON event.attempt_id = root.attempt_id
                        WHERE root.organization_id = %s
                        ORDER BY root.requested_model, event.sequence
                        """,
                        ("acme",),
                    )
                    postgres_attempts = [
                        (
                            str(row["requested_model"]),
                            int(row["sequence"]),
                            str(row["state"]),
                            row["reason_code"],
                            bool(row["has_usage_event"]),
                        )
                        for row in cursor.fetchall()
                    ]
            self.assertEqual(sqlite_attempts, postgres_attempts)
            self.assertEqual(
                sqlite_attempts,
                [
                    ("gpt-terminal", 1, "pending", None, False),
                    ("gpt-terminal", 2, "succeeded", None, True),
                    ("gpt-unknown", 1, "pending", None, False),
                    ("gpt-unknown", 2, "outcome_unknown", "provider_transport_ambiguous", False),
                ],
            )
            self.assertEqual(sqlite_store.active_budget_reservations(organization_id="acme"), 1)
            self.assertEqual(self.store.active_budget_reservations(organization_id="acme"), 1)

            sqlite_connection = sqlite3.connect(sqlite_store.path)
            sqlite_connection.execute("UPDATE gateway_usage_events SET evidence_schema_version = 1")
            sqlite_connection.execute("UPDATE gateway_secret_events SET evidence_schema_version = 1")
            sqlite_connection.commit()
            sqlite_connection.close()
            with self.psycopg.connect(self.owner_dsn) as connection:
                with connection.transaction():
                    with connection.cursor() as cursor:
                        cursor.execute(
                            self.sql.SQL(
                                "UPDATE {}.gateway_usage_events SET evidence_schema_version = 1"
                            ).format(self.sql.Identifier(self.schema))
                        )
                        cursor.execute(
                            self.sql.SQL(
                                "UPDATE {}.gateway_secret_events SET evidence_schema_version = 1"
                            ).format(self.sql.Identifier(self.schema))
                        )
            self.assertEqual(
                _normalized_events(
                    sqlite_store.audit_events(
                        since="2000-01-01T00:00:00+00:00",
                        organization_id="acme",
                    )
                ),
                _normalized_events(
                    self.store.audit_events(
                        since="2000-01-01T00:00:00+00:00",
                        organization_id="acme",
                    )
                ),
            )
    def test_contract_fixtures_and_historical_version_are_materialized(self) -> None:
        fixtures = json.loads((FIXTURES / "valid-v1.json").read_text(encoding="utf-8"))
        identity = _identity("acme")
        event_id = self.store.record(
            identity=identity,
            client="codex",
            protocol="openai",
            requested_model="gpt-test",
            resolved_alias="gpt-test",
            upstream_model="gpt-upstream",
            policy_version="policy-v1",
            policy_action="allowed",
            status="succeeded",
        )
        self.store.record_secret_event(
            identity=identity,
            client="codex",
            protocol="openai",
            requested_model="gpt-test",
            policy_version="policy-v1",
            action="redacted",
            detection_count=1,
            rules=("openai_api_key",),
        )
        current = self.store.audit_events(since="2000-01-01T00:00:00+00:00", organization_id="acme")
        self.assertEqual(set(current[0]), set(fixtures["audit_usage_v2"]))
        self.assertEqual(set(current[1]), set(fixtures["audit_security_v2"]))

        with self.psycopg.connect(self.owner_dsn) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        self.sql.SQL(
                            "UPDATE {}.gateway_usage_events SET evidence_schema_version = 1 WHERE id = %s"
                        ).format(self.sql.Identifier(self.schema)),
                        (event_id,),
                    )
        historical = self.store.audit_events(since="2000-01-01T00:00:00+00:00", organization_id="acme")
        self.assertEqual(set(historical[0]), set(fixtures["audit_usage_v1"]))
    def test_malformed_evidence_fails_closed_without_content(self) -> None:
        event_id = self.store.record(
            identity=_identity("acme"),
            client="codex",
            protocol="openai",
            requested_model="gpt-test",
            resolved_alias="gpt-test",
            upstream_model="gpt-upstream",
            policy_action="allowed",
            status="succeeded",
        )
        with self.psycopg.connect(self.owner_dsn) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        self.sql.SQL(
                            "UPDATE {}.gateway_usage_events SET redaction_rules = %s WHERE id = %s"
                        ).format(self.sql.Identifier(self.schema)),
                        ('["must-not-leak", 1]', event_id),
                    )
        with self.assertRaises(EvidenceStorageError) as raised:
            self.store.audit_events(since="2000-01-01T00:00:00+00:00", organization_id="acme")
        self.assertEqual(raised.exception.code, "stored_evidence_malformed")
        self.assertNotIn("must-not-leak", str(raised.exception))
    def test_tenant_scope_and_budget_reservation_concurrency_match_sqlite_contract(self) -> None:
        acme = _identity("acme")
        beta = _identity("beta")
        for identity in (acme, beta):
            self.store.record(
                identity=identity,
                client="codex",
                protocol="openai",
                requested_model="gpt-test",
                resolved_alias="gpt-test",
                upstream_model="gpt-upstream",
                policy_action="allowed",
                status="succeeded",
                cost_microusd=400,
            )
        self.assertEqual(self.store.monthly_totals(organization_id="acme").requests, 1)
        self.assertEqual(self.store.monthly_totals(organization_id="beta").requests, 1)
        scope = ReservationScope(name="organization", cost_limit_microusd=1_000)
        first = self.store.reserve_budget(
            identity=acme,
            scopes=(scope,),
            reserved_tokens=1,
            reserved_cost_microusd=600,
            ttl_seconds=60,
        )
        second = self.store.reserve_budget(
            identity=beta,
            scopes=(scope,),
            reserved_tokens=1,
            reserved_cost_microusd=600,
            ttl_seconds=60,
        )
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)

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
        barrier = threading.Barrier(2)
        outcomes: list[str] = []
        outcome_lock = threading.Lock()

        def reserve(store: PostgresUsageStore) -> None:
            barrier.wait()
            try:
                store.reserve_budget(
                    identity=acme,
                    scopes=(ReservationScope(name="actor", actor_id="alice", cost_limit_microusd=1_800),),
                    reserved_tokens=1,
                    reserved_cost_microusd=600,
                    ttl_seconds=60,
                )
                outcome = "allowed"
            except ReservationDenied:
                outcome = "denied"
            with outcome_lock:
                outcomes.append(outcome)

        threads = [threading.Thread(target=reserve, args=(store,)) for store in stores]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(sorted(outcomes), ["allowed", "denied"])
    def test_unknown_organization_fails_closed(self) -> None:
        with self.assertRaises(PostgresStorageError) as raised:
            self.store.monthly_totals(organization_id="unknown")
        self.assertEqual(raised.exception.code, "storage_organization_not_configured")


if __name__ == "__main__":
    unittest.main()
