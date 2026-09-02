from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from hormuz._provider_reliability_schema import postgres_statements, sqlite_statements
from hormuz.config import Identity
from hormuz.provider_reliability import (
    ProviderAttemptMetrics,
    ProviderFailoverContext,
    build_provider_attempt_metrics_event,
    build_provider_failover_event,
)
from hormuz.store import RequestAttemptStateError, UsageStore
from hormuz.store_router import create_provider_reliability_repository


IDENTITY = Identity(
    token_env="UNUSED_PROVIDER_RELIABILITY_TOKEN",
    token="synthetic-unused-secret",
    actor_id="alice",
    actor_name="Alice",
    team_id="engineering",
    team_name="Engineering",
    organization_id="acme",
    identity_type="human",
    authentication_source="oidc",
)


def _begin(store: UsageStore, *, identity: Identity = IDENTITY, failover=None):
    arguments = dict(
        identity=identity,
        client="codex",
        protocol="openai",
        requested_model="engineering-fast",
        resolved_alias="engineering-fast" if failover is None else "engineering-deep",
        upstream_model="gpt-fast" if failover is None else "gpt-deep",
        policy_version="local-config-synthetic",
        policy_action="allowed",
        redaction_count=0,
        redaction_rules=(),
        scopes=(),
        reserved_tokens=10,
        reserved_cost_microusd=20,
        ttl_seconds=60,
    )
    if failover is None:
        return store.begin_request_attempt(**arguments)
    reliability = create_provider_reliability_repository(store)
    assert reliability is not None
    return reliability.begin_request_attempt(
        **arguments,
        work_budget=None,
        provider_failover=failover,
    )


class ProviderReliabilitySchemaTests(unittest.TestCase):
    def test_events_are_append_only_tenant_bound_and_one_per_attempt(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "usage.sqlite3"
            store = UsageStore(path)
            reliability = create_provider_reliability_repository(store)
            assert reliability is not None
            original = _begin(store)
            reliability.finalize_request_attempt(
                attempt=original,
                organization_id="acme",
                status="rate_limited",
                provider_metrics=ProviderAttemptMetrics(429, 1_000, None, 1_500, 0, 0),
            )
            failover = _begin(
                store,
                failover=ProviderFailoverContext(
                    original_attempt_id=original.attempt_id,
                    trigger_status=429,
                    reason_code="provider_rate_limited",
                ),
            )
            reliability.finalize_request_attempt(
                attempt=failover,
                organization_id="acme",
                status="succeeded",
                provider_metrics=ProviderAttemptMetrics(200, 800, 1_100, 1_600, 12, 12),
            )

            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT original_attempt_id, failover_attempt_id, trigger_status, reason_code "
                        "FROM gateway_provider_failover_events"
                    ).fetchone(),
                    (
                        original.attempt_id,
                        failover.attempt_id,
                        429,
                        "provider_rate_limited",
                    ),
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT provider_status, response_headers_us, first_body_byte_us, "
                        "total_us, provider_bytes_read, downstream_bytes_sent "
                        "FROM gateway_provider_attempt_metrics ORDER BY provider_status"
                    ).fetchall(),
                    [(200, 800, 1100, 1600, 12, 12), (429, 1000, None, 1500, 0, 0)],
                )
                for statement in (
                    "UPDATE gateway_provider_attempt_metrics SET total_us=total_us",
                    "DELETE FROM gateway_provider_failover_events",
                ):
                    with self.subTest(statement=statement), self.assertRaisesRegex(
                        sqlite3.IntegrityError,
                        "provider_reliability_append_only",
                    ):
                        connection.execute(statement)

            totals = reliability.totals(
                actor_id="alice",
                organization_id="acme",
            )
            self.assertEqual(totals.attempt_count, 2)
            self.assertEqual(totals.live_provider_request_count, 1)
            self.assertEqual(totals.provider_attempt_record_count, 2)
            self.assertEqual(totals.latency_header_sample_count, 2)
            self.assertEqual(totals.latency_first_body_byte_sample_count, 1)
            self.assertEqual(totals.latency_total_sample_count, 2)
            self.assertEqual(totals.failover_link_record_count, 1)
            self.assertEqual(totals.outcome_unknown_count, 0)
            self.assertEqual(totals.cancellation_outcome_unknown_count, 0)
            self.assertEqual(
                reliability.totals(
                    actor_id="bob",
                    organization_id="acme",
                ).attempt_count,
                0,
            )

            other = Identity(
                token_env=IDENTITY.token_env,
                token=IDENTITY.token,
                actor_id=IDENTITY.actor_id,
                actor_name=IDENTITY.actor_name,
                team_id=IDENTITY.team_id,
                team_name=IDENTITY.team_name,
                organization_id="beta",
                identity_type=IDENTITY.identity_type,
                authentication_source=IDENTITY.authentication_source,
            )
            with self.assertRaisesRegex(RequestAttemptStateError, "request_attempt_not_found"):
                _begin(
                    store,
                    identity=other,
                    failover=ProviderFailoverContext(
                        original_attempt_id=original.attempt_id,
                        trigger_status=429,
                        reason_code="provider_rate_limited",
                    ),
                )

    def test_metrics_and_failover_builders_reject_inconsistent_evidence(self):
        now = datetime.now(timezone.utc)
        invalid_metrics = (
            ProviderAttemptMetrics(99, 0, None, 0, 0, 0),
            ProviderAttemptMetrics(200, 2, 1, 3, 0, 0),
            ProviderAttemptMetrics(200, 1, 4, 3, 0, 0),
            ProviderAttemptMetrics(200, 1, 2, 3, 1, 2),
        )
        for metrics in invalid_metrics:
            with self.subTest(metrics=metrics), self.assertRaises(ValueError):
                build_provider_attempt_metrics_event(
                    attempt_id="attempt",
                    organization_id="acme",
                    recorded_at=now,
                    metrics=metrics,
                )
        for context in (
            ProviderFailoverContext("attempt", 500, "provider_overloaded"),
            ProviderFailoverContext("attempt", 529, "provider_rate_limited"),
        ):
            with self.subTest(context=context), self.assertRaises(ValueError):
                build_provider_failover_event(
                    organization_id="acme",
                    failover_attempt_id="alternate",
                    recorded_at=now,
                    context=context,
                )

    def test_sqlite_nine_to_ten_failure_rolls_back_and_retry_preserves_attempts(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "usage.sqlite3"
            with mock.patch.object(UsageStore, "schema_version", 9):
                store = UsageStore(path)
                attempt = _begin(store)
                store.mark_request_attempt_outcome_unknown(
                    attempt=attempt,
                    organization_id="acme",
                    reason_code="provider_transport_ambiguous",
                )
            with closing(sqlite3.connect(path)) as connection:
                before = list(connection.iterdump())

            def fail_after_first_statement(connection, version):
                self.assertEqual(version, 10)
                connection.execute(sqlite_statements()[0])
                raise RuntimeError("synthetic_provider_reliability_migration_failure")

            with (
                mock.patch.object(UsageStore, "_apply_migration", side_effect=fail_after_first_statement),
                self.assertRaisesRegex(
                    RuntimeError,
                    "synthetic_provider_reliability_migration_failure",
                ),
            ):
                UsageStore(path)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(list(connection.iterdump()), before)

            UsageStore(path).verify_ready()
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT state FROM hormuz_schema_migrations WHERE version=10"
                    ).fetchone(),
                    ("applied",),
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT state FROM gateway_request_attempt_events "
                        "WHERE attempt_id=? ORDER BY sequence DESC LIMIT 1",
                        (attempt.attempt_id,),
                    ).fetchone(),
                    ("outcome_unknown",),
                )

    def test_packaged_postgres_migration_matches_owned_schema_source(self):
        actual = resources.files("hormuz").joinpath(
            "migrations/postgresql/0014_provider_reliability.sql"
        ).read_text()
        self.assertEqual(actual, postgres_statements("{schema}", "{runtime_role}"))


if __name__ == "__main__":
    unittest.main()
