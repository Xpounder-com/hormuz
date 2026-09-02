"""Typed, content-free native-attempt finance runtime contracts."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from hormuz._persistence import RequestAttemptStateError
from hormuz.config import Identity
from hormuz.finance_attempts import (
    MAX_INTEGER,
    ConfiguredRateCardBinding,
    NativeUsageObservation,
    absent_native_observation,
    build_finance_attempt_event,
    estimate_configured_route,
    finance_attempt_event_from_row,
    finance_attempt_storage_row,
    repository_contract_binding,
    unavailable_estimate,
    validate_finance_attempt_event,
)
from hormuz.store import ReservationScope, StorageSchemaError, UsageStore
from hormuz.usage import ResponseUsageParser

if __package__:
    from ._sqlite import managed_sqlite_connection
else:
    from _sqlite import managed_sqlite_connection


DIGEST = "a" * 64


def binding() -> ConfiguredRateCardBinding:
    return ConfiguredRateCardBinding(
        rate_card_id="gateway-route-test",
        rate_card_version=1,
        rate_card_digest=DIGEST,
        currency="USD",
    )


def identity() -> Identity:
    return Identity(
        token_env="UNUSED_FINANCE_ATTEMPT_TOKEN",
        token="synthetic-unused-secret",
        actor_id="alice",
        actor_name="Alice",
        team_id="engineering",
        team_name="Engineering",
        organization_id="acme",
        identity_type="human",
        authentication_source="oidc",
    )


def begin(store: UsageStore, *, ttl_seconds: int = 60, protocol: str = "openai"):
    return store._begin_request_attempt_with_work_budget(
        identity=identity(),
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


def complete_observation():
    parser = ResponseUsageParser("openai", is_event_stream=False)
    parser.feed(json.dumps({
        "usage": {
            "input_tokens": 10,
            "input_tokens_details": {"cached_tokens": 2, "cache_write_tokens": 1},
            "output_tokens": 4,
            "total_tokens": 14,
        },
    }).encode())
    return parser.finish_with_finance().finance


def complete_estimate():
    return estimate_configured_route(
        binding(), complete_observation(),
        input_cost_per_million=2,
        cache_read_cost_per_million=1,
        cache_write_cost_per_million=3,
        output_cost_per_million=4,
    )


def replace_storage_row_fields(event, **updates):
    """Keep the canonical event bytes aligned while exercising database guards."""

    row = finance_attempt_storage_row(event)
    evidence = json.loads(str(row["evidence_json"]))
    evidence.update(updates)
    row.update(updates)
    row["evidence_json"] = json.dumps(
        evidence,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return row


class NativeAttemptParserTests(unittest.TestCase):
    def test_openai_complete_observation_and_v1_projection_share_one_parse(self) -> None:
        parser = ResponseUsageParser("openai", is_event_stream=False)
        parser.feed(json.dumps({
            "model": "gpt-test",
            "service_tier": "priority",
            "ignored_response_content": "must-not-persist",
            "usage": {
                "input_tokens": 10,
                "input_tokens_details": {
                    "cached_tokens": 2,
                    "cache_write_tokens": 1,
                    "ignored": "must-not-persist",
                },
                "output_tokens": 4,
                "output_tokens_details": {"reasoning_tokens": 3},
                "total_tokens": 14,
                "ignored": "must-not-persist",
            },
        }).encode())

        result = parser.finish_with_finance()
        self.assertEqual(
            (
                result.usage.input_tokens,
                result.usage.output_tokens,
                result.usage.cache_read_tokens,
                result.usage.cache_write_tokens,
                result.usage.reasoning_tokens,
                result.usage.provider_reported_model,
                result.usage.evidence_complete,
            ),
            # The finance observation adds cache-write without changing the
            # established OpenAI v1 Usage projection, where that field is 0.
            (10, 4, 2, 0, 3, "gpt-test", True),
        )
        observation = result.finance
        self.assertEqual(observation.state, "complete")
        self.assertIsNone(observation.reason_code)
        self.assertEqual(observation.provider_schema_id, "openai.responses.usage.v1")
        self.assertEqual(observation.provider_input_tokens, 10)
        self.assertEqual(observation.provider_output_tokens, 4)
        self.assertEqual(observation.cache_read_input_tokens, 2)
        self.assertEqual(observation.cache_write_input_tokens, 1)
        self.assertEqual(observation.reasoning_output_tokens, 3)
        self.assertEqual(observation.total_tokens, 14)
        self.assertEqual(observation.provider_service_tier, "priority")
        self.assertIsNone(observation.billable_input_tokens)
        self.assertIsNone(observation.billable_output_tokens)
        self.assertNotIn("ignored", observation.native_payload_json)
        self.assertNotIn("must-not-persist", observation.native_payload_json)
        self.assertEqual(
            observation.native_payload_digest,
            hashlib.sha256(observation.native_payload_json.encode()).hexdigest(),
        )

        estimate = estimate_configured_route(
            binding(), observation,
            input_cost_per_million=2,
            cache_read_cost_per_million=1,
            cache_write_cost_per_million=3,
            output_cost_per_million=4,
        )
        self.assertEqual(estimate.availability, "available")
        self.assertEqual(estimate.amount_microusd, 35)
        self.assertEqual(estimate.amount, "0.000035")
        self.assertEqual(estimate.reason_code, "estimated")
        self.assertFalse(estimate.provider_final)

    def test_anthropic_stream_combines_terminal_allowlist_without_double_counting(self) -> None:
        parser = ResponseUsageParser("anthropic", is_event_stream=True)
        events = (
            {
                "type": "message_start",
                "message": {
                    "model": "claude-test",
                    "usage": {
                        "input_tokens": 80,
                        "cache_read_input_tokens": 20,
                        "cache_creation_input_tokens": 10,
                        "cache_creation": {
                            "ephemeral_5m_input_tokens": 6,
                            "ephemeral_1h_input_tokens": 4,
                        },
                        "service_tier": "standard",
                        "inference_geo": "us",
                    },
                },
            },
            {
                "type": "message_delta",
                "usage": {
                    "output_tokens": 12,
                    "output_tokens_details": {"thinking_tokens": 3},
                    "server_tool_use": {
                        "web_search_requests": 1,
                        "web_fetch_requests": 2,
                    },
                },
            },
        )
        parser.feed("".join(f"data: {json.dumps(event)}\n\n" for event in events).encode())
        result = parser.finish_with_finance()

        self.assertEqual(
            (
                result.usage.input_tokens,
                result.usage.output_tokens,
                result.usage.cache_read_tokens,
                result.usage.cache_write_tokens,
                result.usage.reasoning_tokens,
            ),
            # Anthropic thinking is new native evidence; v1 reasoning stays
            # at its established zero projection.
            (80, 12, 20, 10, 0),
        )
        observation = result.finance
        self.assertEqual(observation.state, "complete")
        self.assertEqual(observation.total_tokens, 122)
        self.assertEqual(observation.cache_write_5m_input_tokens, 6)
        self.assertEqual(observation.cache_write_1h_input_tokens, 4)
        self.assertEqual(observation.server_tool_request_count, 3)
        self.assertEqual(observation.provider_service_tier, "standard")
        self.assertEqual(observation.provider_inference_geo, "us")

    def test_anthropic_total_overflow_is_partial_invalid_evidence(self) -> None:
        parser = ResponseUsageParser("anthropic", is_event_stream=False)
        parser.feed(json.dumps({
            "usage": {
                "input_tokens": MAX_INTEGER,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "output_tokens": 1,
            },
        }).encode())

        observation = parser.finish_with_finance().finance

        self.assertEqual(observation.state, "partial")
        self.assertEqual(observation.reason_code, "provider_usage_invalid")
        self.assertIsNone(observation.total_tokens)

    def test_missing_pricing_category_is_unavailable_never_zero(self) -> None:
        parser = ResponseUsageParser("openai", is_event_stream=False)
        parser.feed(json.dumps({
            "usage": {"input_tokens": 10, "output_tokens": 0, "total_tokens": 10},
        }).encode())
        result = parser.finish_with_finance()
        self.assertTrue(result.usage.evidence_complete)
        self.assertEqual(result.finance.state, "complete")
        estimate = estimate_configured_route(
            binding(), result.finance,
            input_cost_per_million=2,
            cache_read_cost_per_million=1,
            cache_write_cost_per_million=3,
            output_cost_per_million=4,
        )
        self.assertEqual(estimate.availability, "unavailable")
        self.assertIsNone(estimate.amount)
        self.assertIsNone(estimate.amount_microusd)
        self.assertEqual(estimate.currency, "USD")
        self.assertEqual(estimate.reason_code, "missing_native_usage")

    def test_configured_estimate_rounds_fractional_microusd_half_even(self) -> None:
        parser = ResponseUsageParser("openai", is_event_stream=False)
        parser.feed(json.dumps({
            "usage": {
                "input_tokens": 1,
                "input_tokens_details": {
                    "cached_tokens": 0,
                    "cache_write_tokens": 0,
                },
                "output_tokens": 0,
                "total_tokens": 1,
            },
        }).encode())
        observation = parser.finish_with_finance().finance
        rounded_up = estimate_configured_route(
            binding(), observation,
            input_cost_per_million="1.5",
            cache_read_cost_per_million=0,
            cache_write_cost_per_million=0,
            output_cost_per_million=0,
        )
        rounded_down = estimate_configured_route(
            binding(), observation,
            input_cost_per_million="2.5",
            cache_read_cost_per_million=0,
            cache_write_cost_per_million=0,
            output_cost_per_million=0,
        )
        self.assertEqual(
            (rounded_up.availability, rounded_up.amount_microusd, rounded_up.amount),
            ("available", 2, "0.000002"),
        )
        self.assertEqual(
            (rounded_down.availability, rounded_down.amount_microusd, rounded_down.amount),
            ("available", 2, "0.000002"),
        )

    def test_malformed_duplicate_nonfinite_overflow_and_unsafe_dimension_degrade(self) -> None:
        malformed = ResponseUsageParser("openai", is_event_stream=False)
        malformed.feed(b'{"usage":{"input_tokens":1,"input_tokens":2,"output_tokens":3,"total_tokens":4}}')
        self.assertEqual(malformed.finish_with_finance().finance.state, "absent")

        nonfinite = ResponseUsageParser("openai", is_event_stream=False)
        nonfinite.feed(b'{"usage":{"input_tokens":NaN,"output_tokens":3,"total_tokens":3}}')
        self.assertEqual(nonfinite.finish_with_finance().finance.state, "absent")

        partial = ResponseUsageParser("openai", is_event_stream=False)
        partial.feed(json.dumps({
            "service_tier": "unsafe tier\nsecret",
            "usage": {
                "input_tokens": 1,
                "output_tokens": 2,
                "total_tokens": 9223372036854775808,
            },
        }).encode())
        observation = partial.finish_with_finance().finance
        self.assertEqual(observation.state, "partial")
        self.assertEqual(observation.reason_code, "provider_usage_invalid")
        self.assertIsNone(observation.total_tokens)
        self.assertIsNone(observation.provider_service_tier)
        self.assertNotIn("secret", observation.native_payload_json)

        malformed_container = ResponseUsageParser("openai", is_event_stream=False)
        malformed_container.feed(json.dumps({
            "usage": {
                "input_tokens": 4,
                "input_tokens_details": 2,
                "output_tokens": 1,
                "total_tokens": 5,
            },
        }).encode())
        malformed_container_result = malformed_container.finish_with_finance()
        self.assertEqual(malformed_container_result.usage.cache_read_tokens, 0)
        self.assertEqual(malformed_container_result.finance.state, "partial")
        self.assertEqual(
            malformed_container_result.finance.reason_code,
            "provider_usage_invalid",
        )
        self.assertIsNone(
            malformed_container_result.finance.cache_read_input_tokens,
        )
        self.assertNotIn(
            "input_tokens_details",
            malformed_container_result.finance.native_payload_json,
        )

    def test_direct_observation_rejects_unallowlisted_nonfinite_and_inconsistent_payloads(self) -> None:
        cases = (
            (
                '{"secret":"must-not-persist"}',
                {"state": "partial", "reason_code": "provider_usage_invalid"},
            ),
            (
                '{"input_tokens":NaN,"output_tokens":2,"total_tokens":2}',
                {"state": "partial", "reason_code": "provider_usage_invalid"},
            ),
            (
                json.dumps({"service_tier": "\ud800"}, separators=(",", ":")),
                {"state": "partial", "reason_code": "provider_usage_invalid"},
            ),
            (
                json.dumps({"service_tier": "a" * 129}, separators=(",", ":")),
                {"state": "partial", "reason_code": "provider_usage_invalid"},
            ),
            (
                '{"input_tokens":1,"output_tokens":2,"total_tokens":3}',
                {
                    "state": "complete",
                    "reason_code": None,
                    "provider_input_tokens": 99,
                    "provider_output_tokens": 2,
                    "total_tokens": 3,
                },
            ),
        )
        for payload, overrides in cases:
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(ValueError, "finance_attempt_evidence_invalid"):
                    NativeUsageObservation(
                        provider_schema_id="openai.responses.usage.v1",
                        provider_schema_version=1,
                        native_payload_json=payload,
                        native_payload_digest=hashlib.sha256(payload.encode()).hexdigest(),
                        **overrides,
                    )


class NativeAttemptEventTests(unittest.TestCase):
    def test_unpriced_repository_identity_is_stable(self) -> None:
        first = repository_contract_binding()
        second = repository_contract_binding()
        self.assertEqual(first, second)
        self.assertEqual(first.rate_card_id, "usage-repository-v1-unpriced")
        with self.assertRaisesRegex(ValueError, "finance_attempt_evidence_invalid"):
            ConfiguredRateCardBinding("\ud800", 1, DIGEST, "USD")

    def test_observation_state_and_reason_pairs_are_unambiguous(self) -> None:
        with self.assertRaisesRegex(ValueError, "finance_attempt_evidence_invalid"):
            NativeUsageObservation(
                provider_schema_id="openai.responses.usage.v1",
                provider_schema_version=1,
                state="absent",
                reason_code="provider_usage_incomplete",
                native_payload_json=None,
                native_payload_digest=None,
            )

    def test_event_binds_terminal_timestamp_usage_and_rate_card(self) -> None:
        parser = ResponseUsageParser("openai", is_event_stream=False)
        parser.feed(json.dumps({
            "usage": {
                "input_tokens": 10,
                "input_tokens_details": {"cached_tokens": 2, "cache_write_tokens": 1},
                "output_tokens": 4,
                "total_tokens": 14,
            },
        }).encode())
        observation = parser.finish_with_finance().finance
        estimate = estimate_configured_route(
            binding(), observation,
            input_cost_per_million=2,
            cache_read_cost_per_million=1,
            cache_write_cost_per_million=3,
            output_cost_per_million=4,
        )
        event = build_finance_attempt_event(
            protocol="openai",
            organization_id="acme",
            request_attempt_id="attempt-1",
            terminal_attempt_event_id="22222222-2222-4222-8222-222222222222",
            usage_event_id="33333333-3333-4333-8333-333333333333",
            terminal_state="succeeded",
            occurred_at="2026-09-02T14:00:00.123456+00:00",
            observation=observation,
            estimate=estimate,
            binding=binding(),
            evidence_event_id="11111111-1111-4111-8111-111111111111",
        )
        validate_finance_attempt_event(event)
        self.assertEqual(
            event["occurred_at"],
            "2026-09-02T14:00:00.123456+00:00",
        )
        self.assertEqual(event["configured_rate_card_digest"], DIGEST)
        self.assertFalse(event["provider_final"])
        damaged = {**event, "usage_event_id": None}
        with self.assertRaisesRegex(ValueError, "finance_attempt_evidence_invalid"):
            validate_finance_attempt_event(damaged)
        noncanonical_time = {
            **event,
            "occurred_at": "2026-09-02T14:00:00.123456Z",
        }
        with self.assertRaisesRegex(ValueError, "finance_attempt_evidence_invalid"):
            validate_finance_attempt_event(noncanonical_time)
        with self.assertRaisesRegex(ValueError, "finance_attempt_evidence_invalid"):
            build_finance_attempt_event(
                protocol="anthropic",
                organization_id="acme",
                request_attempt_id="attempt-1",
                terminal_attempt_event_id="22222222-2222-4222-8222-222222222222",
                usage_event_id="33333333-3333-4333-8333-333333333333",
                terminal_state="succeeded",
                occurred_at="2026-09-02T14:00:00.123456+00:00",
                observation=observation,
                estimate=estimate,
                binding=binding(),
            )
        with self.assertRaisesRegex(ValueError, "finance_attempt_evidence_invalid"):
            build_finance_attempt_event(
                protocol="openai",
                organization_id="acme",
                request_attempt_id="attempt-1",
                terminal_attempt_event_id="22222222-2222-4222-8222-222222222222",
                usage_event_id=None,
                terminal_state="outcome_unknown",
                occurred_at="2026-09-02T14:00:00.123456+00:00",
                observation=absent_native_observation(
                    "openai", "provider_transport_ambiguous",
                ),
                estimate=unavailable_estimate(binding(), "missing_native_usage"),
                binding=binding(),
            )

        stored = finance_attempt_storage_row(event)
        stored["occurred_at"] = datetime.fromisoformat(
            "2026-09-02T19:30:00+05:30"
        )
        self.assertEqual(
            finance_attempt_event_from_row(stored)["occurred_at"],
            "2026-09-02T14:00:00+00:00",
        )

    def test_available_estimate_requires_every_native_pricing_category(self) -> None:
        with self.assertRaisesRegex(ValueError, "finance_attempt_evidence_invalid"):
            build_finance_attempt_event(
                protocol="openai",
                organization_id="acme",
                request_attempt_id="attempt-1",
                terminal_attempt_event_id="22222222-2222-4222-8222-222222222222",
                usage_event_id="33333333-3333-4333-8333-333333333333",
                terminal_state="succeeded",
                occurred_at="2026-09-02T14:00:00.123456+00:00",
                observation=absent_native_observation("openai"),
                estimate=complete_estimate(),
                binding=binding(),
            )

        parser = ResponseUsageParser("openai", is_event_stream=False)
        parser.feed(json.dumps({
            "usage": {
                "input_tokens": 10,
                "input_tokens_details": {
                    "cached_tokens": 2,
                    "cache_write_tokens": 1,
                },
                "output_tokens": 4,
            },
        }).encode())
        partial = parser.finish_with_finance().finance
        estimate = estimate_configured_route(
            binding(), partial,
            input_cost_per_million=2,
            cache_read_cost_per_million=1,
            cache_write_cost_per_million=3,
            output_cost_per_million=4,
        )
        self.assertEqual((partial.state, estimate.availability), ("partial", "available"))
        build_finance_attempt_event(
            protocol="openai",
            organization_id="acme",
            request_attempt_id="attempt-2",
            terminal_attempt_event_id="44444444-4444-4444-8444-444444444444",
            usage_event_id="55555555-5555-4555-8555-555555555555",
            terminal_state="succeeded",
            occurred_at="2026-09-02T14:00:00.123456+00:00",
            observation=partial,
            estimate=estimate,
            binding=binding(),
        )


class SQLiteFinanceAttemptStorageTests(unittest.TestCase):
    def test_available_estimate_without_observation_rolls_back_terminal_transition(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "usage.sqlite3"
            store = UsageStore(path)
            attempt = begin(store)

            with self.assertRaisesRegex(
                StorageSchemaError,
                "finance_attempt_evidence_invalid",
            ):
                store._finalize_request_attempt_with_provider_metrics(
                    attempt=attempt,
                    organization_id="acme",
                    status="succeeded",
                    provider_metrics=None,
                    finance_observation=None,
                    configured_estimate=complete_estimate(),
                )

            with managed_sqlite_connection(path) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT state FROM gateway_request_attempt_events ORDER BY sequence"
                    ).fetchall(),
                    [("pending",)],
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM gateway_finance_attempt_evidence"
                    ).fetchone()[0],
                    0,
                )

    def test_sqlite_guards_reject_unsupported_estimates_and_complete_overflow(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "usage.sqlite3"
            store = UsageStore(path)
            unsupported = begin(store)

            def remove_pricing_category(event):
                return replace_storage_row_fields(
                    event,
                    cache_write_input_tokens=None,
                )

            with mock.patch(
                "hormuz.store.finance_attempt_storage_row",
                side_effect=remove_pricing_category,
            ):
                with self.assertRaises(sqlite3.IntegrityError):
                    store._finalize_request_attempt_with_provider_metrics(
                        attempt=unsupported,
                        organization_id="acme",
                        status="succeeded",
                        provider_metrics=None,
                        finance_observation=complete_observation(),
                        configured_estimate=complete_estimate(),
                    )

            overflow = begin(store, protocol="anthropic")
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
                "hormuz.store.finance_attempt_storage_row",
                side_effect=overflow_total,
            ):
                with self.assertRaises(sqlite3.IntegrityError):
                    store._finalize_request_attempt_with_provider_metrics(
                        attempt=overflow,
                        organization_id="acme",
                        status="succeeded",
                        provider_metrics=None,
                        finance_observation=observation,
                        configured_estimate=estimate,
                    )

            with managed_sqlite_connection(path) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT state FROM gateway_request_attempt_events ORDER BY attempt_id, sequence"
                    ).fetchall(),
                    [("pending",), ("pending",)],
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM gateway_finance_attempt_evidence"
                    ).fetchone()[0],
                    0,
                )

    def test_protocol_profile_mismatch_rolls_back_the_terminal_transition(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "usage.sqlite3"
            store = UsageStore(path)
            attempt = begin(store)
            with self.assertRaisesRegex(
                StorageSchemaError,
                "finance_attempt_evidence_invalid",
            ):
                store._finalize_request_attempt_with_provider_metrics(
                    attempt=attempt,
                    organization_id="acme",
                    status="succeeded",
                    input_tokens=1,
                    output_tokens=1,
                    provider_metrics=None,
                    finance_observation=absent_native_observation("anthropic"),
                    configured_estimate=unavailable_estimate(
                        binding(), "missing_native_usage",
                    ),
                )
            with managed_sqlite_connection(path) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT state FROM gateway_request_attempt_events "
                        "WHERE attempt_id=? ORDER BY sequence",
                        (attempt.attempt_id,),
                    ).fetchall(),
                    [("pending",)],
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM gateway_usage_events",
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM gateway_finance_attempt_evidence",
                    ).fetchone()[0],
                    0,
                )

    def test_terminal_transition_is_one_atomic_query_friendly_finance_fact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "usage.sqlite3"
            store = UsageStore(path)
            attempt = begin(store)
            observation = complete_observation()
            estimate = estimate_configured_route(
                binding(), observation,
                input_cost_per_million=2,
                cache_read_cost_per_million=1,
                cache_write_cost_per_million=3,
                output_cost_per_million=4,
            )

            with managed_sqlite_connection(path) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM gateway_finance_attempt_evidence"
                    ).fetchone()[0],
                    0,
                )
                root = connection.execute(
                    "SELECT configured_rate_card_state, configured_rate_card_id, "
                    "configured_rate_card_version, configured_rate_card_digest, "
                    "configured_rate_card_currency FROM gateway_request_attempts"
                ).fetchone()
                self.assertEqual(root, ("configured", "gateway-route-test", 1, DIGEST, "USD"))

            store._finalize_request_attempt_with_provider_metrics(
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

            with managed_sqlite_connection(path) as connection:
                connection.row_factory = sqlite3.Row
                row = connection.execute(
                    "SELECT * FROM gateway_finance_attempt_evidence"
                ).fetchone()
                self.assertIsNotNone(row)
                terminal = connection.execute(
                    "SELECT id, occurred_at, usage_event_id FROM gateway_request_attempt_events "
                    "WHERE attempt_id=? AND state='succeeded'",
                    (attempt.attempt_id,),
                ).fetchone()
                self.assertEqual(row["terminal_attempt_event_id"], terminal["id"])
                self.assertEqual(row["occurred_at"], terminal["occurred_at"])
                self.assertEqual(row["usage_event_id"], terminal["usage_event_id"])
                self.assertEqual(
                    row["occurred_at"],
                    connection.execute(
                        "SELECT occurred_at FROM gateway_usage_events WHERE id=?",
                        (row["usage_event_id"],),
                    ).fetchone()[0],
                )
                self.assertEqual(row["observation_state"], "complete")
                self.assertEqual(row["cache_write_input_tokens"], 1)
                self.assertEqual(row["configured_estimate_microusd"], 35)
                self.assertEqual(row["configured_estimate_amount"], "0.000035")
                self.assertEqual(row["provider_final"], 0)
                self.assertNotIn("ignored", row["evidence_json"])
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM gateway_audit_chain_entries "
                        "WHERE source_schema_id='hormuz.finance-attempt-evidence'"
                    ).fetchone()[0],
                    1,
                )
            self.assertEqual(store.verify_audit_chain(organization_id="acme").sequence, 2)

            with self.assertRaisesRegex(RequestAttemptStateError, "request_attempt_not_pending"):
                store._finalize_request_attempt_with_provider_metrics(
                    attempt=attempt,
                    organization_id="acme",
                    status="succeeded",
                    provider_metrics=None,
                    finance_observation=observation,
                    configured_estimate=estimate,
                )
            with managed_sqlite_connection(path) as connection:
                with self.assertRaisesRegex(sqlite3.IntegrityError, "append_only"):
                    connection.execute(
                        "UPDATE gateway_finance_attempt_evidence SET provider_final=1"
                    )
                connection.rollback()
                with self.assertRaisesRegex(sqlite3.IntegrityError, "append_only"):
                    connection.execute("DELETE FROM gateway_finance_attempt_evidence")
                connection.rollback()
                with self.assertRaisesRegex(
                    sqlite3.IntegrityError,
                    "finance_attempt_binding_immutable",
                ):
                    connection.execute(
                        "UPDATE gateway_request_attempts "
                        "SET configured_rate_card_id='rewritten' WHERE attempt_id=?",
                        (attempt.attempt_id,),
                    )

    def test_binding_shape_is_storage_enforced_and_trigger_tampering_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "usage.sqlite3"
            store = UsageStore(path)
            attempt = begin(store)
            with managed_sqlite_connection(path) as connection:
                connection.row_factory = sqlite3.Row
                root = dict(connection.execute(
                    "SELECT * FROM gateway_request_attempts WHERE attempt_id=?",
                    (attempt.attempt_id,),
                ).fetchone())
                columns = tuple(root)
                placeholders = ", ".join("?" for _ in columns)
                for field, invalid in (
                    ("configured_rate_card_id", "-invalid"),
                    ("configured_rate_card_version", None),
                    ("configured_rate_card_version", 1.5),
                    ("configured_rate_card_digest", "g" * 64),
                    ("configured_rate_card_currency", "usd"),
                ):
                    candidate = {**root, "attempt_id": f"invalid-{field}", field: invalid}
                    with self.subTest(field=field, invalid=invalid), self.assertRaisesRegex(
                        sqlite3.IntegrityError,
                        "finance_attempt_binding_required",
                    ):
                        connection.execute(
                            f"INSERT INTO gateway_request_attempts ({', '.join(columns)}) "
                            f"VALUES ({placeholders})",
                            tuple(candidate[column] for column in columns),
                        )
                    connection.rollback()

                connection.execute(
                    "DROP TRIGGER gateway_request_attempt_finance_binding_immutable"
                )
                connection.execute(
                    "CREATE TRIGGER gateway_request_attempt_finance_binding_immutable "
                    "BEFORE UPDATE ON gateway_request_attempts BEGIN SELECT 1; END"
                )

            with self.assertRaisesRegex(
                StorageSchemaError,
                "storage_schema_partial_upgrade",
            ):
                UsageStore(path)
            with managed_sqlite_connection(path) as connection:
                trigger = connection.execute(
                    "SELECT sql FROM sqlite_master WHERE type='trigger' "
                    "AND name='gateway_request_attempt_finance_binding_immutable'"
                ).fetchone()[0]
                self.assertIn("BEGIN SELECT 1; END", trigger)

    def test_unknown_and_stale_paths_keep_holds_and_record_absence_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "usage.sqlite3"
            store = UsageStore(path)
            unknown = begin(store)
            partial_parser = ResponseUsageParser("openai", is_event_stream=False)
            partial_parser.feed(b'{"usage":{"input_tokens":7}}')
            partial = partial_parser.finish_with_finance().finance
            self.assertTrue(store._mark_request_attempt_outcome_unknown_with_provider_metrics(
                attempt=unknown,
                organization_id="acme",
                reason_code="provider_transport_ambiguous",
                provider_metrics=None,
                finance_observation=partial,
            ))
            self.assertFalse(store._mark_request_attempt_outcome_unknown_with_provider_metrics(
                attempt=unknown,
                organization_id="acme",
                reason_code="provider_transport_ambiguous",
                provider_metrics=None,
                finance_observation=partial,
            ))

            stale = begin(store, ttl_seconds=1)
            with managed_sqlite_connection(path) as connection:
                connection.execute(
                    "UPDATE gateway_budget_reservations SET expires_at='2000-01-01T00:00:00+00:00' "
                    "WHERE id=?",
                    (stale.reservation_id,),
                )
            self.assertEqual(store.sweep_stale_request_attempts(organization_id="acme"), 1)

            with managed_sqlite_connection(path) as connection:
                connection.row_factory = sqlite3.Row
                rows = connection.execute(
                    "SELECT request_attempt_id, observation_state, observation_reason_code, "
                    "usage_event_id, configured_estimate_availability, "
                    "configured_estimate_reason_code, configured_estimate_microusd "
                    "FROM gateway_finance_attempt_evidence ORDER BY request_attempt_id"
                ).fetchall()
                self.assertEqual(len(rows), 2)
                by_attempt = {row["request_attempt_id"]: row for row in rows}
                self.assertEqual(
                    tuple(by_attempt[unknown.attempt_id]),
                    (
                        unknown.attempt_id, "partial", "provider_transport_ambiguous",
                        None, "unavailable", "attempt_outcome_unknown", None,
                    ),
                )
                self.assertEqual(
                    tuple(by_attempt[stale.attempt_id]),
                    (
                        stale.attempt_id, "absent", "stale_pending",
                        None, "unavailable", "attempt_outcome_unknown", None,
                    ),
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM gateway_budget_reservations"
                    ).fetchone()[0],
                    2,
                )

    def test_sidecar_failure_rolls_back_terminal_transition_and_retry_is_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "usage.sqlite3"
            store = UsageStore(path)
            attempt = begin(store)
            with mock.patch.object(
                store,
                "_append_finance_attempt_evidence_in_connection",
                side_effect=StorageSchemaError("finance_attempt_evidence_invalid"),
            ):
                with self.assertRaisesRegex(StorageSchemaError, "finance_attempt_evidence_invalid"):
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
            with managed_sqlite_connection(path) as connection:
                self.assertEqual(
                    connection.execute("SELECT state FROM gateway_request_attempt_events").fetchall(),
                    [("pending",)],
                )
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM gateway_usage_events").fetchone()[0], 0)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM gateway_finance_attempt_evidence").fetchone()[0], 0)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM gateway_budget_reservations").fetchone()[0], 1)
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

    def test_missing_query_index_fails_closed_without_runtime_repair(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "usage.sqlite3"
            UsageStore(path)
            with managed_sqlite_connection(path) as connection:
                connection.execute("DROP INDEX gateway_finance_attempt_provider")

            with self.assertRaisesRegex(
                StorageSchemaError,
                "storage_schema_partial_upgrade",
            ):
                UsageStore(path)

            with managed_sqlite_connection(path) as connection:
                self.assertIsNone(connection.execute(
                    "SELECT sql FROM sqlite_master WHERE type='index' "
                    "AND name='gateway_finance_attempt_provider'"
                ).fetchone())

    def test_pre_migration_attempt_remains_an_explicit_unpriced_coverage_gap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "usage.sqlite3"
            with mock.patch.object(UsageStore, "schema_version", 10):
                predecessor = UsageStore(path)
                attempt = begin(predecessor)
                predecessor.finalize_request_attempt(
                    attempt=attempt,
                    organization_id="acme",
                    status="succeeded",
                    input_tokens=1,
                    output_tokens=1,
                )
            current = UsageStore(path)
            current.verify_ready()
            with managed_sqlite_connection(path) as connection:
                root = connection.execute(
                    "SELECT configured_rate_card_state, configured_rate_card_id, "
                    "configured_rate_card_version, configured_rate_card_digest, "
                    "configured_rate_card_currency FROM gateway_request_attempts"
                ).fetchone()
                self.assertEqual(root, ("legacy_unavailable", None, None, None, None))
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM gateway_finance_attempt_evidence"
                    ).fetchone()[0],
                    0,
                )
            self.assertEqual(current.verify_audit_chain(organization_id="acme").sequence, 1)


if __name__ == "__main__":
    unittest.main()
