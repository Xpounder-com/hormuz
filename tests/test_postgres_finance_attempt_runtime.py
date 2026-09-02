"""PostgreSQL parity for immutable provider-attempt finance evidence."""

from __future__ import annotations

import hashlib
import json
import threading
import unittest
from unittest import mock

from hormuz.finance_attempts import (
    MAX_INTEGER,
    estimate_configured_route,
    finance_attempt_storage_row,
)
from hormuz.postgres import PostgresStorageError, postgres_transaction
from hormuz.postgres_usage_store import PostgresUsageStore
from hormuz.store import RequestAttemptStateError, ReservationScope
from hormuz.usage import ResponseUsageParser

if __package__:
    from ._postgres_fixture import PostgresTestCase, _identity
    from .test_finance_attempt_runtime import (
        binding,
        complete_estimate,
        complete_observation,
        replace_storage_row_fields,
    )
else:  # Isolated wheel compatibility discovery uses the tests directory as its import root.
    from _postgres_fixture import PostgresTestCase, _identity
    from test_finance_attempt_runtime import (
        binding,
        complete_estimate,
        complete_observation,
        replace_storage_row_fields,
    )


def begin(
    store: PostgresUsageStore,
    *,
    ttl_seconds: int = 60,
    protocol: str = "openai",
):
    return store._begin_request_attempt_with_work_budget(
        identity=_identity("acme"),
        client="codex",
        protocol=protocol,
        requested_model="smart",
        resolved_alias="smart",
        upstream_model="gpt-test",
        policy_version="policy-1",
        policy_action="allowed",
        redaction_count=0,
        redaction_rules=(),
        scopes=(ReservationScope(name="organization"),),
        reserved_tokens=100,
        reserved_cost_microusd=500,
        ttl_seconds=ttl_seconds,
        work_budget=None,
        configured_rate_card=binding(),
    )


class PostgresFinanceAttemptRuntimeTests(PostgresTestCase):
    def test_available_estimate_without_observation_rolls_back_terminal_transition(self) -> None:
        attempt = begin(self.store)

        with self.assertRaisesRegex(
            PostgresStorageError,
            "finance_attempt_evidence_invalid",
        ):
            self.store._finalize_request_attempt_with_provider_metrics(
                attempt=attempt,
                organization_id="acme",
                status="succeeded",
                provider_metrics=None,
                finance_observation=None,
                configured_estimate=complete_estimate(),
            )

        with postgres_transaction(
            self.runtime_dsn,
            schema=self.schema,
            runtime_role=self.runtime_role,
            organization_id="acme",
        ) as connection:
            self.assertEqual(
                [row["state"] for row in connection.execute(
                    "SELECT state FROM gateway_request_attempt_events ORDER BY sequence"
                ).fetchall()],
                ["pending"],
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM gateway_finance_attempt_evidence"
                ).fetchone()["count"],
                0,
            )

    def test_anthropic_canonical_native_profile_passes_database_guard(self) -> None:
        attempt = begin(self.store, protocol="anthropic")
        parser = ResponseUsageParser("anthropic", is_event_stream=False)
        parser.feed(json.dumps({
            "model": "claude-test",
            "usage": {
                "input_tokens": 10,
                "cache_read_input_tokens": 2,
                "cache_creation_input_tokens": 3,
                "cache_creation": {
                    "ephemeral_5m_input_tokens": 1,
                    "ephemeral_1h_input_tokens": 2,
                },
                "output_tokens": 4,
                "output_tokens_details": {"thinking_tokens": 1},
                "server_tool_use": {
                    "web_search_requests": 1,
                    "web_fetch_requests": 2,
                },
                "service_tier": "priority",
                "inference_geo": "us",
            },
        }).encode("utf-8"))
        parsed = parser.finish_with_finance()
        estimate = estimate_configured_route(
            binding(),
            parsed.finance,
            input_cost_per_million=1,
            cache_read_cost_per_million=1,
            cache_write_cost_per_million=1,
            output_cost_per_million=1,
        )
        self.store._finalize_request_attempt_with_provider_metrics(
            attempt=attempt,
            organization_id="acme",
            status="succeeded",
            provider_reported_model=parsed.usage.provider_reported_model,
            input_tokens=parsed.usage.input_tokens,
            output_tokens=parsed.usage.output_tokens,
            cache_read_tokens=parsed.usage.cache_read_tokens,
            cache_write_tokens=parsed.usage.cache_write_tokens,
            reasoning_tokens=parsed.usage.reasoning_tokens,
            cost_microusd=19,
            provider_metrics=None,
            finance_observation=parsed.finance,
            configured_estimate=estimate,
        )

        with postgres_transaction(
            self.runtime_dsn,
            schema=self.schema,
            runtime_role=self.runtime_role,
            organization_id="acme",
        ) as connection:
            row = connection.execute(
                "SELECT provider_schema_id, server_tool_request_count, "
                "provider_service_tier, provider_inference_geo, configured_estimate_microusd "
                "FROM gateway_finance_attempt_evidence WHERE request_attempt_id=%s",
                (attempt.attempt_id,),
            ).fetchone()
        self.assertEqual(
            tuple(row.values()),
            ("anthropic.messages.usage.v1", 3, "priority", "us", 19),
        )

    def test_terminal_transition_is_atomic_tenant_scoped_and_append_only(self) -> None:
        attempt = begin(self.store)
        observation = complete_observation()
        estimate = complete_estimate()

        self.store._finalize_request_attempt_with_provider_metrics(
            attempt=attempt,
            organization_id="acme",
            status="succeeded",
            provider_reported_model="gpt-test-2026-09-02",
            input_tokens=10,
            output_tokens=4,
            cache_read_tokens=2,
            reasoning_tokens=3,
            cost_microusd=34,
            provider_request_id="request-1",
            provider_metrics=None,
            finance_observation=observation,
            configured_estimate=estimate,
        )

        with postgres_transaction(
            self.runtime_dsn,
            schema=self.schema,
            runtime_role=self.runtime_role,
            organization_id="acme",
        ) as connection:
            row = connection.execute(
                """
                SELECT finance.*, terminal.id AS expected_terminal_id,
                       terminal.occurred_at AS expected_occurred_at,
                       terminal.usage_event_id AS expected_usage_event_id,
                       usage.occurred_at AS expected_usage_occurred_at
                FROM gateway_finance_attempt_evidence AS finance
                JOIN gateway_request_attempt_events AS terminal
                  ON terminal.organization_id=finance.organization_id
                 AND terminal.id=finance.terminal_attempt_event_id
                JOIN gateway_usage_events AS usage
                  ON usage.organization_id=finance.organization_id
                 AND usage.id=finance.usage_event_id
                WHERE finance.request_attempt_id=%s
                """,
                (attempt.attempt_id,),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["terminal_attempt_event_id"], row["expected_terminal_id"])
            self.assertEqual(row["occurred_at"], row["expected_occurred_at"])
            self.assertEqual(row["occurred_at"], row["expected_usage_occurred_at"])
            self.assertEqual(row["usage_event_id"], row["expected_usage_event_id"])
            self.assertEqual(row["configured_rate_card_id"], "gateway-route-test")
            self.assertEqual(row["configured_rate_card_version"], 1)
            self.assertEqual(row["configured_rate_card_digest"], "a" * 64)
            self.assertEqual(row["observation_state"], "complete")
            self.assertEqual(row["cache_write_input_tokens"], 1)
            self.assertEqual(row["configured_estimate_microusd"], 35)
            self.assertEqual(row["configured_estimate_amount"], "0.000035")
            self.assertFalse(row["provider_final"])
            self.assertNotIn("ignored", row["evidence_json"])
            source = connection.execute(
                "SELECT source_schema_id, source_schema_version, source_event_id, event_json "
                "FROM gateway_audit_chain_entries WHERE source_event_id=%s",
                (row["evidence_event_id"],),
            ).fetchone()
            self.assertEqual(
                (source["source_schema_id"], source["source_schema_version"], source["source_event_id"]),
                ("hormuz.finance-attempt-evidence", 1, row["evidence_event_id"]),
            )
            self.assertEqual(source["event_json"], row["evidence_json"])

        with postgres_transaction(
            self.runtime_dsn,
            schema=self.schema,
            runtime_role=self.runtime_role,
            organization_id="beta",
        ) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM gateway_finance_attempt_evidence"
                ).fetchone()["count"],
                0,
            )

        self.assertEqual(self.store.verify_audit_chain(organization_id="acme").sequence, 2)
        with self.assertRaisesRegex(RequestAttemptStateError, "request_attempt_not_pending"):
            self.store._finalize_request_attempt_with_provider_metrics(
                attempt=attempt,
                organization_id="acme",
                status="succeeded",
                provider_metrics=None,
                finance_observation=observation,
                configured_estimate=estimate,
            )

        with self.assertRaises(PostgresStorageError) as runtime_mutation:
            with postgres_transaction(
                self.runtime_dsn,
                schema=self.schema,
                runtime_role=self.runtime_role,
                organization_id="acme",
            ) as connection:
                connection.execute(
                    "UPDATE gateway_finance_attempt_evidence SET provider_final=TRUE "
                    "WHERE request_attempt_id=%s",
                    (attempt.attempt_id,),
                )
        self.assertEqual(runtime_mutation.exception.code, "storage_access_denied")

        with self.psycopg.connect(self.owner_dsn) as connection:
            with self.assertRaisesRegex(
                self.psycopg.errors.CheckViolation,
                "portfolio_append_only",
            ):
                connection.execute(
                    self.sql.SQL(
                        "DELETE FROM {}.gateway_finance_attempt_evidence "
                        "WHERE request_attempt_id=%s"
                    ).format(self.sql.Identifier(self.schema)),
                    (attempt.attempt_id,),
                )

        with self.psycopg.connect(self.owner_dsn) as connection:
            with self.assertRaisesRegex(
                self.psycopg.errors.CheckViolation,
                "portfolio_append_only",
            ):
                connection.execute(
                    self.sql.SQL(
                        "UPDATE {}.gateway_request_attempts "
                        "SET configured_rate_card_id='rewritten' WHERE attempt_id=%s"
                    ).format(self.sql.Identifier(self.schema)),
                    (attempt.attempt_id,),
                )

    def test_unknown_and_stale_paths_keep_holds_and_record_absence_once(self) -> None:
        unknown = begin(self.store)
        parser = ResponseUsageParser("openai", is_event_stream=False)
        parser.feed(json.dumps({"usage": {"input_tokens": 7}}).encode())
        partial = parser.finish_with_finance().finance
        self.assertTrue(self.store._mark_request_attempt_outcome_unknown_with_provider_metrics(
            attempt=unknown,
            organization_id="acme",
            reason_code="provider_transport_ambiguous",
            provider_metrics=None,
            finance_observation=partial,
        ))
        self.assertFalse(self.store._mark_request_attempt_outcome_unknown_with_provider_metrics(
            attempt=unknown,
            organization_id="acme",
            reason_code="provider_transport_ambiguous",
            provider_metrics=None,
            finance_observation=partial,
        ))

        stale = begin(self.store, ttl_seconds=1)
        with self.psycopg.connect(self.owner_dsn) as connection:
            connection.execute(
                self.sql.SQL(
                    "UPDATE {}.gateway_budget_reservations "
                    "SET expires_at=TIMESTAMPTZ '2000-01-01T00:00:00+00:00' WHERE id=%s"
                ).format(self.sql.Identifier(self.schema)),
                (stale.reservation_id,),
            )
        self.assertEqual(self.store.sweep_stale_request_attempts(organization_id="acme"), 1)
        self.assertEqual(self.store.sweep_stale_request_attempts(organization_id="acme"), 0)

        with postgres_transaction(
            self.runtime_dsn,
            schema=self.schema,
            runtime_role=self.runtime_role,
            organization_id="acme",
        ) as connection:
            rows = connection.execute(
                "SELECT request_attempt_id, observation_state, observation_reason_code, "
                "usage_event_id, configured_estimate_availability, "
                "configured_estimate_reason_code, configured_estimate_microusd "
                "FROM gateway_finance_attempt_evidence ORDER BY request_attempt_id"
            ).fetchall()
            self.assertEqual(len(rows), 2)
            by_attempt = {row["request_attempt_id"]: row for row in rows}
            self.assertEqual(
                tuple(by_attempt[unknown.attempt_id].values()),
                (
                    unknown.attempt_id,
                    "partial",
                    "provider_transport_ambiguous",
                    None,
                    "unavailable",
                    "attempt_outcome_unknown",
                    None,
                ),
            )
            self.assertEqual(
                tuple(by_attempt[stale.attempt_id].values()),
                (
                    stale.attempt_id, "absent", "stale_pending",
                    None, "unavailable", "attempt_outcome_unknown", None,
                ),
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM gateway_usage_events"
                ).fetchone()["count"],
                0,
            )
        self.assertEqual(self.store.active_budget_reservations(organization_id="acme"), 2)
        self.assertEqual(self.store.verify_audit_chain(organization_id="acme").sequence, 2)

    def test_sidecar_failure_rolls_back_terminal_transition_and_retry_is_safe(self) -> None:
        attempt = begin(self.store)
        with mock.patch.object(
            self.store,
            "_append_finance_attempt_evidence_in_cursor",
            side_effect=PostgresStorageError("finance_attempt_evidence_invalid"),
        ):
            with self.assertRaisesRegex(
                PostgresStorageError,
                "finance_attempt_evidence_invalid",
            ):
                self.store._finalize_request_attempt_with_provider_metrics(
                    attempt=attempt,
                    organization_id="acme",
                    status="succeeded",
                    input_tokens=10,
                    output_tokens=4,
                    provider_metrics=None,
                    finance_observation=complete_observation(),
                    configured_estimate=complete_estimate(),
                )

        with postgres_transaction(
            self.runtime_dsn,
            schema=self.schema,
            runtime_role=self.runtime_role,
            organization_id="acme",
        ) as connection:
            self.assertEqual(
                [row["state"] for row in connection.execute(
                    "SELECT state FROM gateway_request_attempt_events ORDER BY sequence"
                ).fetchall()],
                ["pending"],
            )
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) AS count FROM gateway_usage_events"
            ).fetchone()["count"], 0)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) AS count FROM gateway_finance_attempt_evidence"
            ).fetchone()["count"], 0)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) AS count FROM gateway_budget_reservations"
            ).fetchone()["count"], 1)

        self.store._finalize_request_attempt_with_provider_metrics(
            attempt=attempt,
            organization_id="acme",
            status="succeeded",
            input_tokens=10,
            output_tokens=4,
            provider_metrics=None,
            finance_observation=complete_observation(),
            configured_estimate=complete_estimate(),
        )

    def test_runtime_role_cannot_use_canonical_fields_as_json_side_channel(self) -> None:
        attempt = begin(self.store)

        def duplicate_event_member(event):
            row = finance_attempt_storage_row(event)
            row["evidence_json"] = '{"schema_id":"smuggled",' + str(row["evidence_json"])[1:]
            return row

        with mock.patch(
            "hormuz.postgres_usage_store.finance_attempt_storage_row",
            side_effect=duplicate_event_member,
        ):
            with self.assertRaises(PostgresStorageError) as event_error:
                self.store._finalize_request_attempt_with_provider_metrics(
                    attempt=attempt,
                    organization_id="acme",
                    status="succeeded",
                    provider_metrics=None,
                    finance_observation=complete_observation(),
                    configured_estimate=complete_estimate(),
                )
        self.assertEqual(event_error.exception.code, "storage_unavailable")

        def duplicate_native_member(event):
            row = finance_attempt_storage_row(event)
            native = '{"input_tokens":999,' + str(row["native_payload_json"])[1:]
            digest = hashlib.sha256(native.encode("utf-8")).hexdigest()
            evidence = json.loads(str(row["evidence_json"]))
            evidence["native_payload_json"] = native
            evidence["native_payload_digest"] = digest
            row["native_payload_json"] = native
            row["native_payload_digest"] = digest
            row["evidence_json"] = json.dumps(
                evidence,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            )
            return row

        with mock.patch(
            "hormuz.postgres_usage_store.finance_attempt_storage_row",
            side_effect=duplicate_native_member,
        ):
            with self.assertRaises(PostgresStorageError) as native_error:
                self.store._finalize_request_attempt_with_provider_metrics(
                    attempt=attempt,
                    organization_id="acme",
                    status="succeeded",
                    provider_metrics=None,
                    finance_observation=complete_observation(),
                    configured_estimate=complete_estimate(),
                )
        self.assertEqual(native_error.exception.code, "storage_unavailable")

        def conflicting_normalized_value(event):
            row = finance_attempt_storage_row(event)
            evidence = json.loads(str(row["evidence_json"]))
            evidence["provider_input_tokens"] = 999
            row["provider_input_tokens"] = 999
            row["evidence_json"] = json.dumps(
                evidence,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            )
            return row

        with mock.patch(
            "hormuz.postgres_usage_store.finance_attempt_storage_row",
            side_effect=conflicting_normalized_value,
        ):
            with self.assertRaises(PostgresStorageError) as normalized_error:
                self.store._finalize_request_attempt_with_provider_metrics(
                    attempt=attempt,
                    organization_id="acme",
                    status="succeeded",
                    provider_metrics=None,
                    finance_observation=complete_observation(),
                    configured_estimate=complete_estimate(),
                )
        self.assertEqual(normalized_error.exception.code, "storage_unavailable")

        def nonnumeric_estimate_content(event):
            row = finance_attempt_storage_row(event)
            evidence = json.loads(str(row["evidence_json"]))
            evidence["configured_estimate_amount"] = "smuggled-response-content"
            row["configured_estimate_amount"] = "smuggled-response-content"
            row["evidence_json"] = json.dumps(
                evidence,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            )
            return row

        with mock.patch(
            "hormuz.postgres_usage_store.finance_attempt_storage_row",
            side_effect=nonnumeric_estimate_content,
        ):
            with self.assertRaises(PostgresStorageError) as estimate_error:
                self.store._finalize_request_attempt_with_provider_metrics(
                    attempt=attempt,
                    organization_id="acme",
                    status="succeeded",
                    provider_metrics=None,
                    finance_observation=complete_observation(),
                    configured_estimate=complete_estimate(),
                )
        self.assertEqual(estimate_error.exception.code, "storage_unavailable")

        def unsupported_available_estimate(event):
            return replace_storage_row_fields(
                event,
                cache_write_input_tokens=None,
            )

        with mock.patch(
            "hormuz.postgres_usage_store.finance_attempt_storage_row",
            side_effect=unsupported_available_estimate,
        ):
            with self.assertRaises(PostgresStorageError) as unsupported_error:
                self.store._finalize_request_attempt_with_provider_metrics(
                    attempt=attempt,
                    organization_id="acme",
                    status="succeeded",
                    provider_metrics=None,
                    finance_observation=complete_observation(),
                    configured_estimate=complete_estimate(),
                )
        self.assertEqual(unsupported_error.exception.code, "storage_unavailable")

        self.store._finalize_request_attempt_with_provider_metrics(
            attempt=attempt,
            organization_id="acme",
            status="succeeded",
            provider_metrics=None,
            finance_observation=complete_observation(),
            configured_estimate=complete_estimate(),
        )
        with postgres_transaction(
            self.runtime_dsn,
            schema=self.schema,
            runtime_role=self.runtime_role,
            organization_id="acme",
        ) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM gateway_finance_attempt_evidence"
                ).fetchone()["count"],
                1,
            )

    def test_postgres_guard_rejects_complete_anthropic_total_overflow(self) -> None:
        attempt = begin(self.store, protocol="anthropic")
        parser = ResponseUsageParser("anthropic", is_event_stream=False)
        parser.feed(json.dumps({
            "usage": {
                "input_tokens": 1,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "output_tokens": 1,
            },
        }).encode())
        observation = parser.finish_with_finance().finance
        estimate = estimate_configured_route(
            binding(), observation,
            input_cost_per_million=1,
            cache_read_cost_per_million=1,
            cache_write_cost_per_million=1,
            output_cost_per_million=1,
        )

        def overflow_total(event):
            native = json.dumps(
                {
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "input_tokens": MAX_INTEGER,
                    "output_tokens": 1,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            return replace_storage_row_fields(
                event,
                native_payload_json=native,
                native_payload_digest=hashlib.sha256(native.encode()).hexdigest(),
                provider_input_tokens=MAX_INTEGER,
                provider_output_tokens=1,
                cache_read_input_tokens=0,
                cache_write_input_tokens=0,
                total_tokens=None,
            )

        with mock.patch(
            "hormuz.postgres_usage_store.finance_attempt_storage_row",
            side_effect=overflow_total,
        ):
            with self.assertRaises(PostgresStorageError) as caught:
                self.store._finalize_request_attempt_with_provider_metrics(
                    attempt=attempt,
                    organization_id="acme",
                    status="succeeded",
                    provider_metrics=None,
                    finance_observation=observation,
                    configured_estimate=estimate,
                )
        self.assertEqual(caught.exception.code, "storage_unavailable")

        with postgres_transaction(
            self.runtime_dsn,
            schema=self.schema,
            runtime_role=self.runtime_role,
            organization_id="acme",
        ) as connection:
            self.assertEqual(
                [row["state"] for row in connection.execute(
                    "SELECT state FROM gateway_request_attempt_events ORDER BY sequence"
                ).fetchall()],
                ["pending"],
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM gateway_finance_attempt_evidence"
                ).fetchone()["count"],
                0,
            )

    def test_two_instances_serialize_one_terminal_finance_fact(self) -> None:
        attempt = begin(self.store)
        second = PostgresUsageStore(
            self.runtime_dsn,
            organization_ids=("acme", "beta"),
            schema=self.schema,
            runtime_role=self.runtime_role,
        )
        gate = threading.Barrier(3)
        outcomes: list[BaseException | None] = []
        outcomes_lock = threading.Lock()

        def finalize(store: PostgresUsageStore) -> None:
            gate.wait()
            try:
                store._finalize_request_attempt_with_provider_metrics(
                    attempt=attempt,
                    organization_id="acme",
                    status="succeeded",
                    input_tokens=10,
                    output_tokens=4,
                    provider_metrics=None,
                    finance_observation=complete_observation(),
                    configured_estimate=complete_estimate(),
                )
            except BaseException as error:  # surfaced by assertions below
                outcome: BaseException | None = error
            else:
                outcome = None
            with outcomes_lock:
                outcomes.append(outcome)

        threads = (
            threading.Thread(target=finalize, args=(self.store,), daemon=True),
            threading.Thread(target=finalize, args=(second,), daemon=True),
        )
        for thread in threads:
            thread.start()
        gate.wait()
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())

        self.assertEqual(sum(outcome is None for outcome in outcomes), 1)
        failures = [outcome for outcome in outcomes if outcome is not None]
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], RequestAttemptStateError)
        self.assertEqual(str(failures[0]), "request_attempt_not_pending")

        with postgres_transaction(
            self.runtime_dsn,
            schema=self.schema,
            runtime_role=self.runtime_role,
            organization_id="acme",
        ) as connection:
            counts = connection.execute(
                "SELECT "
                "(SELECT COUNT(*) FROM gateway_usage_events) AS usage_count, "
                "(SELECT COUNT(*) FROM gateway_finance_attempt_evidence) AS finance_count, "
                "(SELECT COUNT(*) FROM gateway_request_attempt_events "
                " WHERE state <> 'pending') AS terminal_count"
            ).fetchone()
            self.assertEqual(
                (counts["usage_count"], counts["finance_count"], counts["terminal_count"]),
                (1, 1, 1),
            )
        self.assertEqual(self.store.verify_audit_chain(organization_id="acme").sequence, 2)

    def test_missing_query_index_fails_closed_and_is_not_repaired(self) -> None:
        drop = self.sql.SQL("DROP INDEX {}.gateway_finance_attempt_provider").format(
            self.sql.Identifier(self.schema)
        )
        restore = self.sql.SQL(
            "CREATE INDEX gateway_finance_attempt_provider ON "
            "{}.gateway_finance_attempt_evidence "
            "(organization_id, provider_schema_id, provider_service_tier, occurred_at)"
        ).format(self.sql.Identifier(self.schema))
        with self.psycopg.connect(self.owner_dsn) as connection:
            connection.execute(drop)
        try:
            with self.assertRaises(PostgresStorageError) as caught:
                PostgresUsageStore(
                    self.runtime_dsn,
                    organization_ids=("acme", "beta"),
                    schema=self.schema,
                    runtime_role=self.runtime_role,
                )
            self.assertEqual(caught.exception.code, "storage_schema_partial_upgrade")
            with self.psycopg.connect(self.owner_dsn) as connection:
                present = connection.execute(
                    "SELECT COUNT(*) FROM pg_indexes WHERE schemaname=%s AND indexname=%s",
                    (self.schema, "gateway_finance_attempt_provider"),
                ).fetchone()[0]
                self.assertEqual(present, 0)
        finally:
            with self.psycopg.connect(self.owner_dsn) as connection:
                connection.execute(restore)

    def test_incomplete_binding_guard_fails_closed_and_is_not_repaired(self) -> None:
        drop = self.sql.SQL(
            "DROP TRIGGER gateway_request_attempt_finance_binding_immutable "
            "ON {}.gateway_request_attempts"
        ).format(self.sql.Identifier(self.schema))
        incomplete = self.sql.SQL(
            "CREATE TRIGGER gateway_request_attempt_finance_binding_immutable "
            "BEFORE UPDATE OF configured_rate_card_id ON {}.gateway_request_attempts "
            "FOR EACH STATEMENT EXECUTE FUNCTION {}.portfolio_reject_mutation()"
        ).format(self.sql.Identifier(self.schema), self.sql.Identifier(self.schema))
        restore = self.sql.SQL(
            "CREATE TRIGGER gateway_request_attempt_finance_binding_immutable "
            "BEFORE UPDATE OF configured_rate_card_state, configured_rate_card_id, "
            "configured_rate_card_version, configured_rate_card_digest, "
            "configured_rate_card_currency ON {}.gateway_request_attempts "
            "FOR EACH STATEMENT EXECUTE FUNCTION {}.portfolio_reject_mutation()"
        ).format(self.sql.Identifier(self.schema), self.sql.Identifier(self.schema))
        with self.psycopg.connect(self.owner_dsn) as connection:
            connection.execute(drop)
            connection.execute(incomplete)
        try:
            with self.assertRaises(PostgresStorageError) as caught:
                PostgresUsageStore(
                    self.runtime_dsn,
                    organization_ids=("acme", "beta"),
                    schema=self.schema,
                    runtime_role=self.runtime_role,
                )
            self.assertEqual(caught.exception.code, "storage_schema_partial_upgrade")
        finally:
            with self.psycopg.connect(self.owner_dsn) as connection:
                connection.execute(drop)
                connection.execute(restore)

    def test_incomplete_consistency_guard_fails_closed_and_is_not_repaired(self) -> None:
        with self.psycopg.connect(self.owner_dsn) as connection:
            original = connection.execute(
                "SELECT pg_get_functiondef(p.oid) FROM pg_proc p "
                "JOIN pg_namespace n ON n.oid=p.pronamespace "
                "WHERE n.nspname=%s AND p.proname=%s",
                (self.schema, "enforce_finance_attempt_evidence_consistency"),
            ).fetchone()[0]
            connection.execute(
                self.sql.SQL(
                    "CREATE OR REPLACE FUNCTION {}.enforce_finance_attempt_evidence_consistency() "
                    "RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog "
                    "AS $body$ BEGIN RETURN NEW; END; $body$"
                ).format(self.sql.Identifier(self.schema))
            )
        try:
            with self.assertRaises(PostgresStorageError) as caught:
                PostgresUsageStore(
                    self.runtime_dsn,
                    organization_ids=("acme", "beta"),
                    schema=self.schema,
                    runtime_role=self.runtime_role,
                )
            self.assertEqual(caught.exception.code, "storage_schema_partial_upgrade")
            with self.psycopg.connect(self.owner_dsn) as connection:
                body = connection.execute(
                    "SELECT p.prosrc FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                    "WHERE n.nspname=%s AND p.proname=%s",
                    (self.schema, "enforce_finance_attempt_evidence_consistency"),
                ).fetchone()[0]
                self.assertEqual(" ".join(body.split()), "BEGIN RETURN NEW; END;")
        finally:
            with self.psycopg.connect(self.owner_dsn) as connection:
                connection.execute(original)


if __name__ == "__main__":
    unittest.main()
