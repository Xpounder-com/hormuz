from __future__ import annotations

import http.client
import json
import os
import shlex
import shutil
import sqlite3
import socket
import subprocess
import tempfile
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from hormuz.config import ConfigError, GatewayConfig, ModelRoute, Policy
from hormuz.budget_runtime import configured_model_id, configured_route_rate_card
from hormuz.contracts import relay_contract_header, validate_audit_event, validate_contract
from hormuz.custody import KEY_PURPOSE_PROVIDER_CREDENTIAL, EnvelopeCipher, GeneratedDataKey
from hormuz.custody_runtime import write_envelope_file
from hormuz.policy import PolicyEngine
from hormuz.postgres import PostgresStorageError
from hormuz.server import GatewayServer, serve_in_thread
from hormuz.store import UsageStore
from hormuz.usage import ResponseUsageParser

if __package__:
    from ._sqlite import managed_sqlite_connection
else:
    from _sqlite import managed_sqlite_connection


GATEWAY_TOKEN = "company-user-token-never-forward"
CLAUDE_ONLY_TOKEN = "company-claude-only-token-never-forward"
OPENAI_KEY = "provider-openai-secret"
ANTHROPIC_KEY = "provider-anthropic-secret"


def _write_rotating_client_auth_helper(root: Path) -> tuple[Path, Path]:
    """Return stale material once, then the current synthetic session token."""
    count = root / "client-auth-helper-count"
    helper = root / "client-auth-helper.py"
    helper.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, sys\n"
        "path = pathlib.Path(sys.argv[1])\n"
        "calls = int(path.read_text()) + 1 if path.exists() else 1\n"
        "path.write_text(str(calls))\n"
        "print('expired-session-token' if calls == 1 else sys.argv[2])\n",
        encoding="utf-8",
    )
    helper.chmod(0o700)
    return helper, count


class FakeProviderHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    requests: list[dict[str, object]] = []
    lock = threading.Lock()
    delayed_stream_started = threading.Event()
    delayed_stream_release = threading.Event()
    delayed_stream_first_chunk = (
        b"event: response.output_text.delta\n"
        b'data: {"type":"response.output_text.delta","delta":"FIRST"}\n\n'
    )

    def do_POST(self) -> None:  # noqa: N802
        request_path = self.path.partition("?")[0]
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        with self.lock:
            self.requests.append(
                {
                    "path": self.path,
                    "headers": {name.lower(): value for name, value in self.headers.items()},
                    "body": body,
                }
            )

        if body.get("force_rate_limit") is True:
            self._send_json(
                {"error": {"message": "Rate limit", "type": "rate_limit_error"}},
                status=429,
                request_id="req_rate_limited",
            )
            return

        if body.get("force_primary_rate_limit") is True and body.get("model") == "gpt-test-fast":
            self._send_json(
                {"error": {"message": "Primary model rate limit", "type": "rate_limit_error"}},
                status=429,
                request_id="req_primary_rate_limited",
            )
            return

        if (
            body.get("force_primary_overload") is True
            and body.get("model") in {"gpt-test-fast", "claude-test"}
        ):
            self._send_json(
                {"error": {"message": "Primary model overloaded", "type": "overloaded_error"}},
                status=529,
                request_id="req_primary_overloaded",
            )
            return

        if body.get("force_primary_bad_request") is True and body.get("model") == "gpt-test-fast":
            self._send_json(
                {"error": {"message": "Bad request", "type": "invalid_request_error"}},
                status=400,
                request_id="req_primary_bad_request",
            )
            return

        if request_path.endswith("/responses"):
            if body.get("force_delayed_stream") is True:
                self._send_delayed_openai_stream(body["model"])
                return
            payload = {
                "id": "resp_test",
                "object": "response",
                "status": "completed",
                "model": body["model"],
                "output": [],
                "usage": {
                    "input_tokens": 120,
                    "output_tokens": 30,
                    "input_tokens_details": {"cached_tokens": 20},
                    "output_tokens_details": {"reasoning_tokens": 7},
                    "total_tokens": 150,
                },
            }
            if body.get("force_missing_usage") is True:
                payload.pop("usage")
            cache_write_tokens = body.get("force_cache_write_tokens")
            if type(cache_write_tokens) is int and cache_write_tokens >= 0:
                payload["usage"]["input_tokens_details"]["cache_write_tokens"] = cache_write_tokens
            if body.get("force_response_failed") is True:
                payload["status"] = "failed"
            if body.get("stream") is True:
                self._send_openai_stream(payload)
                return
            self._send_json(payload, request_id="req_openai_test")
            return

        if request_path.endswith("/messages"):
            structured = isinstance(body.get("output_config"), dict) and isinstance(
                body["output_config"].get("format"), dict
            )
            response_text = '{"title":"Gateway compatibility test"}' if structured else "ok"
            usage = {
                "input_tokens": 80,
                "cache_creation_input_tokens": 10,
                "cache_read_input_tokens": 20,
                "output_tokens": 12,
            }
            if body.get("stream") is not True:
                self._send_json(
                    {
                        "id": "msg_test",
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "text", "text": response_text}],
                        "model": body["model"],
                        "stop_reason": "end_turn",
                        "stop_sequence": None,
                        "usage": usage,
                    },
                    request_id="req_anthropic_test",
                )
                return
            events = [
                (
                    "message_start",
                    {
                        "type": "message_start",
                        "message": {
                            "id": "msg_test",
                            "type": "message",
                            "role": "assistant",
                            "content": [],
                            "model": body["model"],
                            "stop_reason": None,
                            "stop_sequence": None,
                            "usage": {
                                "input_tokens": 80,
                                "cache_creation_input_tokens": 10,
                                "cache_read_input_tokens": 20,
                                "output_tokens": 0,
                            },
                        },
                    },
                ),
                (
                    "content_block_start",
                    {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                ),
                (
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": response_text},
                    },
                ),
                ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                (
                    "message_delta",
                    {
                        "type": "message_delta",
                        "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                        **(
                            {}
                            if body.get("force_missing_usage") is True
                            else {"usage": {"output_tokens": 12}}
                        ),
                    },
                ),
                ("message_stop", {"type": "message_stop"}),
            ]
            body_bytes = "".join(
                f"event: {event}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n" for event, payload in events
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body_bytes)))
            self.send_header("request-id", "req_anthropic_test")
            self.end_headers()
            self.wfile.write(body_bytes)
            return

        if request_path.endswith("/messages/count_tokens"):
            self._send_json({"input_tokens": 42}, request_id="req_anthropic_count_test")
            return

        self._send_json({"error": "unexpected path"}, status=404)

    def _send_json(self, value, *, status: int = 200, request_id: str | None = None) -> None:
        data = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        if request_id:
            self.send_header("x-request-id", request_id)
        self.end_headers()
        self.wfile.write(data)

    def _send_openai_stream(self, completed_response: dict) -> None:
        message_id = "msg_gateway_probe"
        text = "GATEWAY_OK"
        in_progress = {**completed_response, "status": "in_progress", "output": [], "usage": None}
        terminal_status = completed_response.get("status", "completed")
        completed_message = {
            "id": message_id,
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": text, "annotations": [], "logprobs": []}],
        }
        completed = {
            **completed_response,
            "output": [completed_message] if terminal_status == "completed" else [],
        }
        completed_events = [
            {"type": "response.created", "response": in_progress, "sequence_number": 0},
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {**completed_message, "status": "in_progress", "content": []},
                "sequence_number": 1,
            },
            {
                "type": "response.content_part.added",
                "item_id": message_id,
                "output_index": 0,
                "content_index": 0,
                "part": {"type": "output_text", "text": "", "annotations": [], "logprobs": []},
                "sequence_number": 2,
            },
            {
                "type": "response.output_text.delta",
                "item_id": message_id,
                "output_index": 0,
                "content_index": 0,
                "delta": text,
                "logprobs": [],
                "sequence_number": 3,
            },
            {
                "type": "response.output_text.done",
                "item_id": message_id,
                "output_index": 0,
                "content_index": 0,
                "text": text,
                "logprobs": [],
                "sequence_number": 4,
            },
            {
                "type": "response.content_part.done",
                "item_id": message_id,
                "output_index": 0,
                "content_index": 0,
                "part": completed_message["content"][0],
                "sequence_number": 5,
            },
            {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": completed_message,
                "sequence_number": 6,
            },
            {"type": "response.completed", "response": completed, "sequence_number": 7},
        ]
        events = completed_events if terminal_status == "completed" else [
            completed_events[0],
            {
                "type": f"response.{terminal_status}",
                "response": completed,
                "sequence_number": 1,
            },
        ]
        body_bytes = "".join(
            f"event: {event['type']}\ndata: {json.dumps(event, separators=(',', ':'))}\n\n" for event in events
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body_bytes)))
        self.send_header("x-request-id", "req_codex_probe")
        self.end_headers()
        self.wfile.write(body_bytes)

    def _send_delayed_openai_stream(self, model: str) -> None:
        completed_event = {
            "type": "response.completed",
            "response": {
                "model": model,
                "usage": {
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "input_tokens_details": {"cached_tokens": 0},
                    "output_tokens_details": {"reasoning_tokens": 0},
                },
            },
        }
        final_chunk = (
            "event: response.completed\n"
            f"data: {json.dumps(completed_event, separators=(',', ':'))}\n\n"
        ).encode("utf-8")
        first_chunk = type(self).delayed_stream_first_chunk
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(first_chunk) + len(final_chunk)))
        self.send_header("x-request-id", "req_delayed_stream")
        self.end_headers()
        self.wfile.write(first_chunk)
        self.wfile.flush()
        type(self).delayed_stream_started.set()
        type(self).delayed_stream_release.wait(timeout=5)
        self.wfile.write(final_chunk)
        self.wfile.flush()

    def log_message(self, format: str, *args: object) -> None:
        pass


class ModelRouteCostTests(unittest.TestCase):
    def test_reservation_covers_every_terminal_input_partition_and_rounds_up(self) -> None:
        route = ModelRoute(
            alias="premium-cache",
            protocol="anthropic",
            upstream_model="model",
            input_cost_per_million=1,
            cache_read_cost_per_million=25,
            cache_write_cost_per_million=100,
            output_cost_per_million=2,
        )

        reservation = route.estimate_reservation_cost_microusd(input_tokens=3, output_tokens=2)
        terminal_costs = (
            route.estimate_cost_microusd(
                input_tokens=3, output_tokens=2, cache_read_tokens=0, cache_write_tokens=0,
            ),
            route.estimate_cost_microusd(
                input_tokens=0, output_tokens=2, cache_read_tokens=3, cache_write_tokens=0,
            ),
            route.estimate_cost_microusd(
                input_tokens=0, output_tokens=2, cache_read_tokens=0, cache_write_tokens=3,
            ),
        )

        self.assertEqual(reservation, 304)
        self.assertGreaterEqual(reservation, max(terminal_costs))
        fractional = ModelRoute(
            alias="fractional",
            protocol="openai",
            upstream_model="model",
            input_cost_per_million=0.1,
        )
        self.assertEqual(
            fractional.estimate_reservation_cost_microusd(input_tokens=1, output_tokens=0),
            1,
        )

    def test_reservation_covers_large_terminal_rounding(self) -> None:
        route = ModelRoute(
            alias="large-bound",
            protocol="openai",
            upstream_model="model",
            output_cost_per_million=4.316109535003818,
        )
        output_tokens = 7_760_635_130_464_772

        reservation = route.estimate_reservation_cost_microusd(
            input_tokens=0,
            output_tokens=output_tokens,
        )
        terminal = route.estimate_cost_microusd(
            input_tokens=0,
            output_tokens=output_tokens,
            cache_read_tokens=0,
            cache_write_tokens=0,
        )

        self.assertGreaterEqual(reservation, terminal)


class ConfiguredModelIdentityTests(unittest.TestCase):
    def test_route_alias_is_preserved_or_deterministically_normalized(self) -> None:
        self.assertEqual(
            configured_model_id(
                resolved_alias="managed-route",
                upstream_model="vendor/model",
                requested_model="requested",
            ),
            "managed-route",
        )
        normalized = configured_model_id(
            resolved_alias="vendor/model",
            upstream_model="provider/model",
            requested_model="requested",
        )
        self.assertRegex(normalized, r"\Aconfigured-model-sha256:[0-9a-f]{64}\Z")
        self.assertEqual(
            normalized,
            configured_model_id(
                resolved_alias="vendor/model",
                upstream_model="changed/provider-model",
                requested_model="changed-request",
            ),
        )
        reserved_alias = configured_model_id(
            resolved_alias=normalized,
            upstream_model="provider/model",
            requested_model="requested",
        )
        self.assertRegex(
            reserved_alias, r"\Aconfigured-model-sha256:[0-9a-f]{64}\Z",
        )
        self.assertNotEqual(reserved_alias, normalized)


class ResponseUsageEvidenceTests(unittest.TestCase):
    def test_success_evidence_requires_valid_input_and_output_tokens(self) -> None:
        complete = ResponseUsageParser("openai", is_event_stream=False)
        complete.feed(json.dumps({
            "usage": {"input_tokens": 10, "output_tokens": 0},
        }).encode())
        self.assertTrue(complete.finish().evidence_complete)

        incomplete_values = (
            {"usage": {"input_tokens": 10}},
            {"usage": {"input_tokens": 10, "output_tokens": True}},
            {"usage": {"input_tokens": -1, "output_tokens": 2}},
        )
        for value in incomplete_values:
            with self.subTest(value=value):
                parser = ResponseUsageParser("openai", is_event_stream=False)
                parser.feed(json.dumps(value).encode())
                self.assertFalse(parser.finish().evidence_complete)

        malformed = ResponseUsageParser("openai", is_event_stream=False)
        malformed.feed(b'{"usage":{"input_tokens":10')
        self.assertFalse(malformed.finish().evidence_complete)

    def test_anthropic_stream_requires_terminal_output_usage(self) -> None:
        message_start = {
            "type": "message_start",
            "message": {
                "model": "synthetic-anthropic",
                "usage": {"input_tokens": 10, "output_tokens": 0},
            },
        }
        for terminal_usage in ({}, {"usage": {}}, {"usage": {"output_tokens": True}}):
            with self.subTest(terminal_usage=terminal_usage):
                parser = ResponseUsageParser("anthropic", is_event_stream=True)
                parser.feed(
                    (
                        "data: " + json.dumps(message_start) + "\n\n"
                        "data: " + json.dumps({"type": "message_delta", **terminal_usage}) + "\n\n"
                    ).encode()
                )
                self.assertFalse(parser.finish().evidence_complete)

        malformed_final = ResponseUsageParser("anthropic", is_event_stream=True)
        malformed_final.feed(
            (
                "data: " + json.dumps(message_start) + "\n\n"
                "data: " + json.dumps({
                    "type": "message_delta", "usage": {"output_tokens": 12},
                }) + "\n\n"
                "data: " + json.dumps({"type": "message_delta"}) + "\n\n"
            ).encode()
        )
        self.assertFalse(malformed_final.finish().evidence_complete)

        complete = ResponseUsageParser("anthropic", is_event_stream=True)
        complete.feed(
            (
                "data: " + json.dumps(message_start) + "\n\n"
                "data: " + json.dumps({
                    "type": "message_delta", "usage": {"output_tokens": 12},
                }) + "\n\n"
            ).encode()
        )
        usage = complete.finish()
        self.assertTrue(usage.evidence_complete)
        self.assertEqual((usage.input_tokens, usage.output_tokens), (10, 12))


class GatewayIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        FakeProviderHandler.requests = []
        FakeProviderHandler.delayed_stream_started.clear()
        FakeProviderHandler.delayed_stream_release.clear()
        self.provider = ThreadingHTTPServer(("127.0.0.1", 0), FakeProviderHandler)
        self.provider_thread = threading.Thread(target=self.provider.serve_forever, daemon=True)
        self.provider_thread.start()

        os.environ["TEST_GATEWAY_TOKEN"] = GATEWAY_TOKEN
        os.environ["TEST_CLAUDE_ONLY_TOKEN"] = CLAUDE_ONLY_TOKEN
        os.environ["TEST_OPENAI_KEY"] = OPENAI_KEY
        os.environ["TEST_ANTHROPIC_KEY"] = ANTHROPIC_KEY
        self.config_path = self.root / "gateway.json"
        self.config_path.write_text(
            json.dumps(self._config(self.provider.server_port, _free_port())),
            encoding="utf-8",
        )
        self.config = GatewayConfig.load(self.config_path)
        self.gateway = GatewayServer(self.config)
        self.gateway_thread = serve_in_thread(self.gateway)

    def tearDown(self) -> None:
        self.gateway.shutdown()
        self.gateway.server_close()
        self.provider.shutdown()
        self.provider.server_close()
        self.temporary.cleanup()

    def test_openai_request_falls_back_caps_and_never_forwards_employee_token(self) -> None:
        status, headers, response = self._post(
            "/v1/responses",
            {
                "model": "unapproved-model",
                "input": "This content must not be stored by the gateway",
                "max_output_tokens": 900,
                "stream": False,
            },
        )

        self.assertEqual(status, 200)
        self.assertEqual(headers["x-hormuz-policy-decision"], "fallback+capped")
        self.assertEqual(json.loads(response)["model"], "gpt-test-fast")
        upstream = FakeProviderHandler.requests[-1]
        self.assertEqual(upstream["body"]["model"], "gpt-test-fast")
        self.assertEqual(upstream["body"]["max_output_tokens"], 100)
        self.assertIs(upstream["body"]["store"], False)
        self.assertEqual(upstream["headers"]["authorization"], f"Bearer {OPENAI_KEY}")
        self.assertNotIn(GATEWAY_TOKEN, upstream["headers"].values())

        totals = self.gateway.store.monthly_totals(actor_id="alice")
        self.assertEqual(totals.input_tokens, 120)
        self.assertEqual(totals.output_tokens, 30)
        self.assertEqual(totals.cache_read_tokens, 20)
        self.assertEqual(totals.reasoning_tokens, 7)
        self.assertGreater(totals.cost_microusd, 0)

    def test_openai_native_cache_write_cost_settles_usage_and_budget_consistently(self) -> None:
        config_value = self._config(self.provider.server_port, _free_port())
        config_value["model_routes"]["engineering-fast"]["cache_write_cost_per_million"] = 5
        self._restart_gateway(config_value)

        status, _, _ = self._post(
            "/v1/responses",
            {
                "model": "engineering-fast",
                "input": "cache-write accounting",
                "stream": False,
                "force_cache_write_tokens": 10,
            },
        )

        self.assertEqual(status, 200)
        with managed_sqlite_connection(self.gateway.store.path) as connection:
            row = connection.execute(
                "SELECT usage.cost_microusd, finance.configured_estimate_microusd, "
                "finance.cache_write_input_tokens "
                "FROM gateway_finance_attempt_evidence AS finance "
                "JOIN gateway_usage_events AS usage "
                "ON usage.organization_id=finance.organization_id "
                "AND usage.id=finance.usage_event_id"
            ).fetchone()
        self.assertEqual(row, (202, 202, 10))
        self.assertEqual(self.gateway.store.monthly_totals(actor_id="alice").cost_microusd, 202)

    def test_openai_failed_terminal_stream_is_not_recorded_as_succeeded(self) -> None:
        status, headers, body = self._post(
            "/v1/responses",
            {
                "model": "engineering-fast",
                "input": "known provider failure",
                "stream": True,
                "force_response_failed": True,
            },
        )

        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "text/event-stream")
        self.assertIn(b"response.failed", body)
        with managed_sqlite_connection(self.gateway.store.path) as connection:
            row = connection.execute(
                "SELECT terminal.state, usage.status, finance.terminal_state "
                "FROM gateway_finance_attempt_evidence AS finance "
                "JOIN gateway_request_attempt_events AS terminal "
                "ON terminal.organization_id=finance.organization_id "
                "AND terminal.id=finance.terminal_attempt_event_id "
                "JOIN gateway_usage_events AS usage "
                "ON usage.organization_id=finance.organization_id "
                "AND usage.id=finance.usage_event_id"
            ).fetchone()
        self.assertEqual(row, ("failed", "failed", "failed"))
        self.assertEqual(self.gateway.store.active_budget_reservations(), 0)

    def test_gateway_uses_an_encrypted_provider_credential_at_startup(self) -> None:
        """The runtime must prefer the sealed source over any plaintext env value."""

        self.gateway.shutdown()
        self.gateway.server_close()
        envelope_path = self.root / "openai.envelope"
        data_key = b"K" * 32
        provider = mock.Mock()
        provider.generate_data_key.return_value = GeneratedDataKey(
            key_reference="alias/hormuz-provider",
            plaintext=data_key,
            encrypted=b"test-wrapped-data-key",
        )
        provider.decrypt_data_key.return_value = data_key
        envelope = EnvelopeCipher(provider).seal(
            OPENAI_KEY.encode("utf-8"),
            organization_id="organization",
            purpose=KEY_PURPOSE_PROVIDER_CREDENTIAL,
            key_reference="alias/hormuz-provider",
        )
        write_envelope_file(envelope_path, envelope)

        config_value = self._config(self.provider.server_port, _free_port())
        config_value["key_custody"] = {
            "backend": "aws-kms",
            "region": "us-east-1",
            "key_references": {"provider_credential": "alias/hormuz-provider"},
        }
        config_value["upstreams"]["openai"] = {
            "base_url": f"http://127.0.0.1:{self.provider.server_port}",
            "api_key_envelope": "./openai.envelope",
        }
        self.config_path.write_text(json.dumps(config_value), encoding="utf-8")
        # A fallback bug would make this different value reach the upstream.
        os.environ["TEST_OPENAI_KEY"] = "unexpected-plaintext-fallback"
        with mock.patch("hormuz.custody_runtime.create_data_key_provider", return_value=provider):
            self.config = GatewayConfig.load(self.config_path)
            self.gateway = GatewayServer(self.config)
        self.gateway_thread = serve_in_thread(self.gateway)

        status, _, _ = self._post("/v1/responses", {"model": "engineering-fast", "input": "hello"})
        self.assertEqual(status, 200)
        self.assertEqual(FakeProviderHandler.requests[-1]["headers"]["authorization"], f"Bearer {OPENAI_KEY}")
        self.assertEqual(provider.decrypt_data_key.call_count, 1)
        self.assertNotIn(OPENAI_KEY.encode("utf-8"), envelope_path.read_bytes())

    def test_policy_and_evidence_contracts_are_versioned_without_mutating_provider_body(self) -> None:
        status, headers, body = self._get("/health", token=None)
        self.assertEqual(status, 200)
        self.assertEqual(headers["x-hormuz-contract"], "hormuz.gateway-health;v=1")
        validate_contract(json.loads(body))

        status, headers, body = self._get("/v1/gateway/whoami")
        self.assertEqual(status, 200)
        identity = json.loads(body)
        validate_contract(identity)
        self.assertEqual(identity["identity_type"], "human")

        status, headers, response = self._post(
            "/v1/responses",
            {"model": "engineering-fast", "input": "hello"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers["x-hormuz-contract"], relay_contract_header())
        self.assertNotIn("schema_id", json.loads(response))

        audit = self.gateway.store.audit_events(since="2000-01-01T00:00:00+00:00")
        self.assertEqual(len(audit), 1)
        validate_audit_event(audit[0])
        self.assertEqual(audit[0]["routed_model"], "gpt-test-fast")
        self.assertEqual(audit[0]["provider_reported_model"], "gpt-test-fast")
        self.assertTrue(audit[0]["policy_version"].startswith("local-config-"))

        status, headers, body = self._post(
            "/v1/responses",
            {"model": "engineering-fast", "input": "hello"},
            token="wrong-token",
        )
        self.assertEqual(status, 401)
        self.assertEqual(headers["x-hormuz-contract"], "hormuz.gateway-error;v=3")
        validate_contract(json.loads(body))

    def test_liveness_and_readiness_are_unauthenticated_versioned_probes_without_provider_egress(self) -> None:
        before = len(FakeProviderHandler.requests)

        status, headers, body = self._get("/health", token=None)
        self.assertEqual(status, 200)
        self.assertEqual(headers["x-hormuz-contract"], "hormuz.gateway-health;v=1")
        liveness = json.loads(body)
        validate_contract(liveness)
        self.assertEqual(liveness["status"], "ok")

        status, headers, body = self._get("/ready", token=None)
        self.assertEqual(status, 200)
        self.assertEqual(headers["x-hormuz-contract"], "hormuz.gateway-readiness;v=1")
        readiness = json.loads(body)
        validate_contract(readiness)
        self.assertEqual(readiness, {
            "schema_id": "hormuz.gateway-readiness",
            "schema_version": 1,
            "status": "ready",
            "service": "hormuz",
            "reason": None,
        })
        self.assertEqual(len(FakeProviderHandler.requests), before)

    def test_readiness_dependency_failure_and_draining_are_content_free(self) -> None:
        class UnavailableStore:
            def verify_ready(self) -> None:
                raise PostgresStorageError("company-database-host-must-not-leak")

        before = len(FakeProviderHandler.requests)
        self.gateway.store = UnavailableStore()
        status, headers, body = self._get("/ready", token=None)
        self.assertEqual(status, 503)
        self.assertEqual(headers["x-hormuz-contract"], "hormuz.gateway-readiness;v=1")
        dependency_unavailable = json.loads(body)
        validate_contract(dependency_unavailable)
        self.assertEqual(dependency_unavailable["status"], "not_ready")
        self.assertEqual(dependency_unavailable["reason"], "dependency_unavailable")
        self.assertNotIn("company-database-host-must-not-leak", repr(dependency_unavailable))
        self.assertEqual(len(FakeProviderHandler.requests), before)

        self.gateway._accepting_requests.clear()
        status, _, body = self._get("/ready", token=None)
        self.assertEqual(status, 503)
        draining = json.loads(body)
        validate_contract(draining)
        self.assertEqual(draining["reason"], "draining")

    def test_readiness_fails_closed_when_the_active_policy_check_is_unavailable(self) -> None:
        unavailable_policy_runtime = mock.Mock()
        unavailable_policy_runtime.verify_active_policies.side_effect = PostgresStorageError("policy-store-unavailable")
        self.gateway.policy_engine.policy_runtime = unavailable_policy_runtime
        before = len(FakeProviderHandler.requests)

        status, headers, body = self._get("/ready", token=None)

        self.assertEqual(status, 503)
        self.assertEqual(headers["x-hormuz-contract"], "hormuz.gateway-readiness;v=1")
        response = json.loads(body)
        validate_contract(response)
        self.assertEqual(response["reason"], "dependency_unavailable")
        self.assertEqual(len(FakeProviderHandler.requests), before)

    def test_shutdown_marks_the_gateway_draining_before_listener_close(self) -> None:
        self.gateway.shutdown()

        self.assertEqual(self.gateway.readiness_reason(), "draining")

    def test_storage_interruption_fails_closed_before_provider_egress_without_content_leakage(self) -> None:
        class UnavailableStore:
            def monthly_totals(self, **_kwargs):
                raise PostgresStorageError("storage_unavailable")

        unavailable_store = UnavailableStore()
        self.gateway.store = unavailable_store
        self.gateway.policy_engine.store = unavailable_store
        before = len(FakeProviderHandler.requests)
        request_content = "company-secret-input-must-not-leak"

        status, headers, body = self._post(
            "/v1/responses",
            {"model": "engineering-fast", "input": request_content},
        )
        self.assertEqual(status, 503)
        self.assertEqual(headers["x-hormuz-error-code"], "hormuz_storage_unavailable")
        response = json.loads(body)
        self.assertEqual(response["error"]["code"], "hormuz_storage_unavailable")
        self.assertNotIn(request_content, repr(response))
        self.assertEqual(len(FakeProviderHandler.requests), before)

        status, headers, body = self._get("/v1/gateway/usage")
        self.assertEqual(status, 503)
        self.assertEqual(headers["x-hormuz-contract"], "hormuz.gateway-error;v=3")
        response = json.loads(body)
        validate_contract(response)
        self.assertEqual(response["error"]["code"], "hormuz_storage_unavailable")

    def test_provider_rate_limit_is_evidence_not_an_additional_hormuz_denial(self) -> None:
        status, headers, body = self._post(
            "/v1/responses",
            {"model": "engineering-fast", "input": "hello", "force_rate_limit": True},
        )
        self.assertEqual(status, 429)
        self.assertEqual(headers["x-hormuz-contract"], relay_contract_header())
        self.assertNotIn("schema_id", json.loads(body))

        audit = self.gateway.store.audit_events(since="2000-01-01T00:00:00+00:00")
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]["policy_action"], "allowed")
        self.assertEqual(audit[0]["status"], "rate_limited")
        validate_audit_event(audit[0])

    def test_explicit_rate_limit_uses_one_policy_allowed_failover_with_separate_evidence(self) -> None:
        self._restart_gateway(self._config_with_failover())

        status, headers, body = self._post(
            "/v1/responses",
            {
                "model": "engineering-fast",
                "input": "bounded failover",
                "force_primary_rate_limit": True,
            },
        )

        self.assertEqual(status, 200, body)
        self.assertEqual(headers["x-hormuz-routed-model"], "gpt-test-deep")
        self.assertEqual(headers["x-hormuz-failover"], "v1;reason=provider_rate_limited")
        self.assertIn("hormuz_upstream_headers;dur=", headers["server-timing"])
        self.assertEqual(
            [request["body"]["model"] for request in FakeProviderHandler.requests],
            ["gpt-test-fast", "gpt-test-deep"],
        )

        audit = self.gateway.store.audit_events(since="2000-01-01T00:00:00+00:00")
        self.assertEqual(
            [(event["status"], event["routed_model"]) for event in audit],
            [("rate_limited", "gpt-test-fast"), ("succeeded", "gpt-test-deep")],
        )
        connection = sqlite3.connect(self.gateway.store.path)
        attempts = {
            attempt_id: (resolved_alias, upstream_model)
            for attempt_id, resolved_alias, upstream_model in connection.execute(
                "SELECT attempt_id, resolved_alias, upstream_model FROM gateway_request_attempts"
            ).fetchall()
        }
        link = connection.execute(
            "SELECT original_attempt_id, failover_attempt_id, trigger_status, reason_code "
            "FROM gateway_provider_failover_events"
        ).fetchone()
        metrics = connection.execute(
            "SELECT a.upstream_model, m.provider_status, m.response_headers_us, "
            "m.first_body_byte_us, m.total_us, m.provider_bytes_read, m.downstream_bytes_sent "
            "FROM gateway_provider_attempt_metrics m "
            "JOIN gateway_request_attempts a ON a.attempt_id=m.attempt_id "
            "ORDER BY a.created_at"
        ).fetchall()
        connection.close()
        self.assertIsNotNone(link)
        assert link is not None
        self.assertEqual(attempts[link[0]], ("engineering-fast", "gpt-test-fast"))
        self.assertEqual(attempts[link[1]], ("engineering-deep", "gpt-test-deep"))
        self.assertEqual(link[2:], (429, "provider_rate_limited"))
        self.assertEqual([row[1] for row in metrics], [429, 200])
        for row in metrics:
            self.assertIsNotNone(row[2])
            self.assertGreaterEqual(row[4], row[2])
            self.assertGreaterEqual(row[5], row[6])

    def test_explicit_overload_can_fail_over_but_never_more_than_one_hop(self) -> None:
        self._restart_gateway(self._config_with_failover())
        status, headers, _ = self._post(
            "/v1/responses",
            {
                "model": "engineering-fast",
                "input": "overload failover",
                "force_primary_overload": True,
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers["x-hormuz-failover"], "v1;reason=provider_overloaded")
        self.assertEqual(len(FakeProviderHandler.requests), 2)

        config_value = self._config_with_failover()
        config_value["model_routes"]["engineering-deep"]["failover_alias"] = "engineering-fast"
        self._restart_gateway(config_value)
        before = len(FakeProviderHandler.requests)
        status, headers, _ = self._post(
            "/v1/responses",
            {"model": "engineering-fast", "input": "one hop", "force_rate_limit": True},
        )
        self.assertEqual(status, 429)
        self.assertEqual(headers["x-hormuz-failover"], "v1;reason=provider_rate_limited")
        self.assertEqual(len(FakeProviderHandler.requests) - before, 2)

    def test_anthropic_overload_uses_the_same_bounded_failover_contract(self) -> None:
        config_value = self._config(self.provider.server_port, _free_port())
        config_value["model_routes"]["claude-standard"]["failover_alias"] = "claude-haiku-4-5"
        self._restart_gateway(config_value)

        status, headers, body = self._post(
            "/v1/messages",
            {
                "model": "claude-standard",
                "messages": [{"role": "user", "content": "bounded failover"}],
                "max_tokens": 20,
                "force_primary_overload": True,
            },
        )

        self.assertEqual(status, 200, body)
        self.assertEqual(headers["x-hormuz-routed-model"], "claude-test-haiku")
        self.assertEqual(headers["x-hormuz-failover"], "v1;reason=provider_overloaded")
        self.assertEqual(
            [request["body"]["model"] for request in FakeProviderHandler.requests],
            ["claude-test", "claude-test-haiku"],
        )
        audit = self.gateway.store.audit_events(since="2000-01-01T00:00:00+00:00")
        self.assertEqual(
            [(event["status"], event["routed_model"]) for event in audit],
            [("failed", "claude-test"), ("succeeded", "claude-test-haiku")],
        )

    def test_failover_stays_off_when_policy_or_request_semantics_do_not_allow_it(self) -> None:
        config_value = self._config_with_failover()
        config_value["policies"]["organization"]["allowed_models"].remove("engineering-deep")
        self._restart_gateway(config_value)
        before = len(FakeProviderHandler.requests)
        status, headers, _ = self._post(
            "/v1/responses",
            {
                "model": "engineering-fast",
                "input": "policy blocks alternate",
                "force_primary_rate_limit": True,
            },
        )
        self.assertEqual(status, 429)
        self.assertNotIn("x-hormuz-failover", headers)
        self.assertEqual(len(FakeProviderHandler.requests) - before, 1)

        before = len(FakeProviderHandler.requests)
        status, headers, _ = self._post(
            "/v1/responses",
            {
                "model": "engineering-fast",
                "input": "stored work is not replay-safe",
                "store": True,
                "force_primary_rate_limit": True,
            },
        )
        self.assertEqual(status, 429)
        self.assertNotIn("x-hormuz-failover", headers)
        self.assertEqual(len(FakeProviderHandler.requests) - before, 1)

        config_value = self._config_with_failover()
        config_value["upstreams"]["openai"]["allow_background"] = True
        config_value["upstreams"]["openai"]["allow_response_storage"] = True
        self._restart_gateway(config_value)
        before = len(FakeProviderHandler.requests)
        status, headers, _ = self._post(
            "/v1/responses",
            {
                "model": "engineering-fast",
                "input": "background is not replay-safe",
                "background": True,
                "force_primary_rate_limit": True,
            },
        )
        self.assertEqual(status, 429)
        self.assertNotIn("x-hormuz-failover", headers)
        self.assertEqual(len(FakeProviderHandler.requests) - before, 1)

        self._restart_gateway(self._config_with_failover())
        before = len(FakeProviderHandler.requests)
        status, headers, _ = self._post(
            "/v1/responses",
            {
                "model": "engineering-fast",
                "input": "client error",
                "force_primary_bad_request": True,
            },
        )
        self.assertEqual(status, 400)
        self.assertNotIn("x-hormuz-failover", headers)
        self.assertEqual(len(FakeProviderHandler.requests) - before, 1)

    def test_failover_configuration_requires_a_distinct_same_protocol_route(self) -> None:
        cases = (
            ("unknown", "missing", "unknown failover alias"),
            ("self", "engineering-fast", "cannot fail over to itself"),
            ("cross-protocol", "claude-standard", "must use protocol openai"),
        )
        for name, failover_alias, message in cases:
            with self.subTest(name=name):
                config_value = self._config(self.provider.server_port, _free_port())
                config_value["model_routes"]["engineering-fast"]["failover_alias"] = failover_alias
                path = self.root / f"invalid-{name}.json"
                path.write_text(json.dumps(config_value), encoding="utf-8")
                with self.assertRaisesRegex(ConfigError, message):
                    GatewayConfig.load(path)

        config_value = self._config(self.provider.server_port, _free_port())
        config_value["model_routes"]["engineering-fast"]["failover_alias"] = "engineering-deep"
        config_value["model_routes"]["engineering-deep"]["upstream_model"] = "gpt-test-fast"
        path = self.root / "invalid-same-model.json"
        path.write_text(json.dumps(config_value), encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "distinct upstream model"):
            GatewayConfig.load(path)

    def test_missing_upstream_credential_fails_before_creating_an_attempt(self) -> None:
        self.gateway.upstream_credentials["openai"] = ""
        with mock.patch("hormuz.server.urllib.request.urlopen") as urlopen:
            status, headers, _ = self._post(
                "/v1/responses",
                {"model": "engineering-fast", "input": "must-not-create-an-attempt"},
            )

        self.assertEqual(status, 503)
        self.assertEqual(headers["x-hormuz-error-code"], "gateway_upstream_not_configured")
        urlopen.assert_not_called()
        self.assertEqual(self.gateway.store.monthly_totals(actor_id="alice").requests, 0)
        self.assertEqual(self.gateway.store.active_budget_reservations(), 0)
        connection = sqlite3.connect(self.gateway.store.path)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM gateway_request_attempts").fetchone()[0], 0)
        connection.close()

    def test_ambiguous_provider_transport_keeps_a_conservative_unknown_attempt(self) -> None:
        self._restart_gateway(self._config_with_failover())
        before = len(FakeProviderHandler.requests)
        request_content = "must-not-enter-request-attempt-evidence"
        with mock.patch(
            "hormuz.server.urllib.request.urlopen",
            side_effect=urllib.error.URLError("provider-connection-interrupted"),
        ):
            status, headers, body = self._post(
                "/v1/responses",
                {"model": "engineering-fast", "input": request_content},
            )

        self.assertEqual(status, 502)
        self.assertEqual(headers["x-hormuz-error-code"], "gateway_upstream_error")
        self.assertEqual(len(FakeProviderHandler.requests), before)
        self.assertNotIn(request_content, body.decode("utf-8"))
        self.assertEqual(self.gateway.store.monthly_totals(actor_id="alice").requests, 0)
        self.assertEqual(self.gateway.store.active_budget_reservations(), 1)

        connection = sqlite3.connect(self.gateway.store.path)
        root_columns = [row[1] for row in connection.execute("PRAGMA table_info(gateway_request_attempts)").fetchall()]
        root = connection.execute(
            "SELECT requested_model, reserved_cost_microusd FROM gateway_request_attempts"
        ).fetchone()
        events = connection.execute(
            "SELECT sequence, state, reason_code, usage_event_id FROM gateway_request_attempt_events ORDER BY sequence"
        ).fetchall()
        connection.close()
        self.assertNotIn("input", root_columns)
        self.assertEqual(root[0], "engineering-fast")
        self.assertGreater(root[1], 0)
        self.assertEqual(
            events,
            [(1, "pending", None, None), (2, "outcome_unknown", "provider_transport_ambiguous", None)],
        )

    def test_success_without_provider_usage_keeps_the_conservative_reservation(self) -> None:
        status, headers, body = self._post(
            "/v1/responses",
            {
                "model": "engineering-fast",
                "input": "response without usable provider usage",
                "max_output_tokens": 20,
                "force_missing_usage": True,
            },
        )

        self.assertEqual(status, 200)
        self.assertEqual(headers["x-hormuz-contract"], relay_contract_header())
        self.assertNotIn("usage", json.loads(body))
        self.assertEqual(self.gateway.store.monthly_totals(actor_id="alice").requests, 0)
        self.assertEqual(self.gateway.store.active_budget_reservations(), 1)

        connection = sqlite3.connect(self.gateway.store.path)
        reserved = connection.execute(
            "SELECT reserved_cost_microusd FROM gateway_request_attempts"
        ).fetchone()[0]
        events = connection.execute(
            "SELECT sequence,state,reason_code,usage_event_id "
            "FROM gateway_request_attempt_events ORDER BY sequence"
        ).fetchall()
        usage_count = connection.execute("SELECT COUNT(*) FROM gateway_usage_events").fetchone()[0]
        metrics = connection.execute(
            "SELECT provider_status, first_body_byte_us, total_us, "
            "provider_bytes_read, downstream_bytes_sent "
            "FROM gateway_provider_attempt_metrics"
        ).fetchone()
        connection.close()
        self.assertGreater(reserved, 0)
        self.assertEqual(usage_count, 0)
        self.assertIsNotNone(metrics)
        assert metrics is not None
        self.assertEqual(metrics[0], 200)
        self.assertIsNotNone(metrics[1])
        self.assertGreaterEqual(metrics[2], metrics[1])
        self.assertEqual(metrics[3:], (len(body), len(body)))
        self.assertEqual(
            events,
            [(1, "pending", None, None), (2, "outcome_unknown", "provider_transport_ambiguous", None)],
        )

    def test_non_event_stream_uses_available_reads_for_first_byte_metrics(self) -> None:
        payload = json.dumps(
            {
                "id": "resp_incremental",
                "object": "response",
                "status": "completed",
                "model": "gpt-test-fast",
                "output": [],
                "usage": {
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "total_tokens": 2,
                },
            },
            separators=(",", ":"),
        ).encode("utf-8")

        class IncrementalJSONResponse:
            status = 200
            headers = {"Content-Type": "application/json", "x-request-id": "req_incremental"}

            def __init__(self) -> None:
                midpoint = len(payload) // 2
                self.chunks = [payload[:midpoint], payload[midpoint:], b""]
                self.read1_calls = 0

            def getcode(self) -> int:
                return self.status

            def read(self, _size: int) -> bytes:
                raise AssertionError("ordinary provider responses must use read1 when available")

            def read1(self, _size: int) -> bytes:
                self.read1_calls += 1
                return self.chunks.pop(0)

            def close(self) -> None:
                return None

        upstream_response = IncrementalJSONResponse()
        with mock.patch("hormuz.server.urllib.request.urlopen", return_value=upstream_response):
            status, _, body = self._post(
                "/v1/responses",
                {"model": "engineering-fast", "input": "incremental JSON response"},
            )

        self.assertEqual(status, 200)
        self.assertEqual(body, payload)
        self.assertEqual(upstream_response.read1_calls, 3)
        connection = sqlite3.connect(self.gateway.store.path)
        metrics = connection.execute(
            "SELECT provider_status, first_body_byte_us, total_us, "
            "provider_bytes_read, downstream_bytes_sent "
            "FROM gateway_provider_attempt_metrics"
        ).fetchone()
        connection.close()
        self.assertIsNotNone(metrics)
        assert metrics is not None
        self.assertEqual(metrics[0], 200)
        self.assertIsNotNone(metrics[1])
        self.assertGreaterEqual(metrics[2], metrics[1])
        self.assertEqual(metrics[3:], (len(payload), len(payload)))

    def test_anthropic_attempt_reserves_the_most_expensive_input_partition(self) -> None:
        with mock.patch(
            "hormuz.server.urllib.request.urlopen",
            side_effect=urllib.error.URLError("provider-connection-interrupted"),
        ):
            status, _, _ = self._post(
                "/v1/messages",
                {
                    "model": "claude-standard",
                    "messages": [{"role": "user", "content": "premium cache write"}],
                    "max_tokens": 1,
                },
            )

        self.assertEqual(status, 502)
        connection = sqlite3.connect(self.gateway.store.path)
        reserved_tokens, reserved_cost_microusd = connection.execute(
            "SELECT reserved_tokens, reserved_cost_microusd FROM gateway_request_attempts"
        ).fetchone()
        connection.close()
        reserved_input_tokens = reserved_tokens - 1
        route = self.config.model_routes["claude-standard"]
        self.assertEqual(
            reserved_cost_microusd,
            route.estimate_reservation_cost_microusd(
                input_tokens=reserved_input_tokens,
                output_tokens=1,
            ),
        )
        self.assertGreater(
            reserved_cost_microusd,
            route.estimate_cost_microusd(
                input_tokens=reserved_input_tokens,
                output_tokens=1,
                cache_read_tokens=0,
                cache_write_tokens=0,
            ),
        )

    def test_interrupted_provider_response_becomes_unknown_without_replay(self) -> None:
        self._restart_gateway(self._config_with_failover())
        partial_body = json.dumps({
            "usage": {
                "input_tokens": 10,
                "input_tokens_details": {
                    "cached_tokens": 2,
                    "cache_write_tokens": 1,
                },
                "output_tokens": 4,
                "total_tokens": 14,
            },
        }).encode()

        class InterruptedResponse:
            status = 200
            headers = {"Content-Type": "application/json", "x-request-id": "req_truncated"}

            def getcode(self) -> int:
                return self.status

            def read(self, _size: int) -> bytes:
                raise http.client.IncompleteRead(partial_body, 1)

            def close(self) -> None:
                return None

        before = len(FakeProviderHandler.requests)
        with mock.patch("hormuz.server.urllib.request.urlopen", return_value=InterruptedResponse()):
            status, headers, body = self._post(
                "/v1/responses",
                {"model": "engineering-fast", "input": "interrupted provider response"},
            )

        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "application/json")
        self.assertEqual(body, b"")
        self.assertEqual(len(FakeProviderHandler.requests), before)
        self.assertEqual(self.gateway.store.monthly_totals(actor_id="alice").requests, 0)
        self.assertEqual(self.gateway.store.active_budget_reservations(), 1)

        connection = sqlite3.connect(self.gateway.store.path)
        events = connection.execute(
            "SELECT sequence, state, reason_code, usage_event_id FROM gateway_request_attempt_events ORDER BY sequence"
        ).fetchall()
        metrics = connection.execute(
            "SELECT provider_status, first_body_byte_us, total_us, "
            "provider_bytes_read, downstream_bytes_sent "
            "FROM gateway_provider_attempt_metrics"
        ).fetchone()
        finance = connection.execute(
            "SELECT observation_state, observation_reason_code, provider_input_tokens, "
            "provider_output_tokens, cache_read_input_tokens, cache_write_input_tokens, "
            "configured_estimate_availability, configured_estimate_reason_code "
            "FROM gateway_finance_attempt_evidence"
        ).fetchone()
        connection.close()
        self.assertIsNotNone(metrics)
        assert metrics is not None
        self.assertEqual(metrics[0], 200)
        self.assertIsNotNone(metrics[1])
        self.assertGreaterEqual(metrics[2], metrics[1])
        self.assertEqual(metrics[3:], (len(partial_body), 0))
        self.assertEqual(
            events,
            [(1, "pending", None, None), (2, "outcome_unknown", "provider_stream_interrupted", None)],
        )
        self.assertEqual(
            finance,
            (
                "partial", "provider_stream_interrupted", 10, 4, 2, 1,
                "unavailable", "attempt_outcome_unknown",
            ),
        )

    def test_event_stream_releases_available_chunk_before_provider_completion(self) -> None:
        connection = http.client.HTTPConnection("127.0.0.1", self.gateway.server_port, timeout=5)
        connection.request(
            "POST",
            "/v1/responses",
            body=json.dumps(
                {
                    "model": "engineering-fast",
                    "input": "latency probe",
                    "stream": True,
                    "force_delayed_stream": True,
                }
            ),
            headers={
                "Authorization": f"Bearer {GATEWAY_TOKEN}",
                "Content-Type": "application/json",
            },
        )
        response = connection.getresponse()
        expected = FakeProviderHandler.delayed_stream_first_chunk
        first_read_finished = threading.Event()
        first_read: list[bytes] = []

        def read_first_chunk() -> None:
            received = bytearray()
            while len(received) < len(expected):
                chunk = response.read(len(expected) - len(received))
                if not chunk:
                    break
                received.extend(chunk)
            first_read.append(bytes(received))
            first_read_finished.set()

        reader = threading.Thread(target=read_first_chunk, daemon=True)
        reader.start()
        try:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.getheader("Content-Type"), "text/event-stream")
            self.assertTrue(FakeProviderHandler.delayed_stream_started.wait(timeout=1))
            route = self.config.model_routes["engineering-fast"]
            expected_binding = configured_route_rate_card(
                alias=route.alias,
                protocol=route.protocol,
                upstream_model=route.upstream_model,
                input_cost_per_million=route.input_cost_per_million,
                cache_read_cost_per_million=route.cache_read_cost_per_million,
                cache_write_cost_per_million=route.cache_write_cost_per_million,
                output_cost_per_million=route.output_cost_per_million,
            )
            database = sqlite3.connect(self.gateway.store.path)
            binding = database.execute(
                "SELECT configured_rate_card_state, configured_rate_card_id, "
                "configured_rate_card_version, configured_rate_card_digest, "
                "configured_rate_card_currency FROM gateway_request_attempts"
            ).fetchone()
            pending = database.execute(
                "SELECT state FROM gateway_request_attempt_events ORDER BY sequence DESC LIMIT 1"
            ).fetchone()[0]
            sidecar_count = database.execute(
                "SELECT COUNT(*) FROM gateway_finance_attempt_evidence"
            ).fetchone()[0]
            database.close()
            self.assertEqual(
                binding,
                (
                    "configured",
                    expected_binding["id"],
                    expected_binding["version"],
                    expected_binding["content_digest"],
                    expected_binding["currency"],
                ),
            )
            self.assertEqual(pending, "pending")
            self.assertEqual(sidecar_count, 0)
            self.assertTrue(
                first_read_finished.wait(timeout=2),
                "gateway held an available provider event until stream completion",
            )
            self.assertEqual(first_read, [expected])
            self.assertFalse(FakeProviderHandler.delayed_stream_release.is_set())
        finally:
            FakeProviderHandler.delayed_stream_release.set()
            reader.join(timeout=5)

        remainder = response.read()
        connection.close()
        self.assertIn(b"event: response.completed", remainder)
        self.assertEqual(self.gateway.store.monthly_totals(actor_id="alice").requests, 1)
        database = sqlite3.connect(self.gateway.store.path)
        finance = database.execute(
            "SELECT observation_state, provider_input_tokens, provider_output_tokens, "
            "configured_estimate_availability, configured_rate_card_digest "
            "FROM gateway_finance_attempt_evidence"
        ).fetchone()
        database.close()
        self.assertEqual(
            finance,
            ("partial", 1, 1, "unavailable", expected_binding["content_digest"]),
        )

    def test_downstream_disconnect_closes_provider_stream_and_keeps_unknown_attempt(self) -> None:
        self._restart_gateway(self._config_with_failover())
        class CloseTrackedEventStream:
            status = 200
            headers = {"Content-Type": "text/event-stream", "x-request-id": "req_cancelled"}

            def __init__(self) -> None:
                self.closed = threading.Event()
                self.read1_calls = 0

            def getcode(self) -> int:
                return self.status

            def read(self, _size: int) -> bytes:
                raise AssertionError("event streams must use read1 when the provider exposes it")

            def read1(self, _size: int) -> bytes:
                self.read1_calls += 1
                return b'event: response.output_text.delta\ndata: {"delta":"cancel"}\n\n'

            def close(self) -> None:
                self.closed.set()

        upstream_response = CloseTrackedEventStream()
        with (
            mock.patch("hormuz.server.urllib.request.urlopen", return_value=upstream_response),
            mock.patch(
                "hormuz.server.GatewayRequestHandler._write_downstream_chunk",
                side_effect=BrokenPipeError,
            ),
        ):
            status, headers, body = self._post(
                "/v1/responses",
                {"model": "engineering-fast", "input": "cancel provider work", "stream": True},
            )

        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "text/event-stream")
        self.assertEqual(body, b"")
        self.assertTrue(upstream_response.closed.is_set())
        self.assertEqual(upstream_response.read1_calls, 1)
        self.assertEqual(self.gateway.store.monthly_totals(actor_id="alice").requests, 0)
        self.assertEqual(self.gateway.store.active_budget_reservations(), 1)

        connection = sqlite3.connect(self.gateway.store.path)
        events = connection.execute(
            "SELECT sequence, state, reason_code, usage_event_id FROM gateway_request_attempt_events ORDER BY sequence"
        ).fetchall()
        connection.close()
        self.assertEqual(
            events,
            [(1, "pending", None, None), (2, "outcome_unknown", "provider_stream_interrupted", None)],
        )

    def test_post_relay_finalization_failure_leaves_pending_evidence_without_buffering(self) -> None:
        before = len(FakeProviderHandler.requests)
        with mock.patch.object(
            type(self.gateway.provider_reliability_store),
            "finalize_request_attempt",
            side_effect=sqlite3.OperationalError("test-finalization-interruption"),
        ):
            status, headers, body = self._post(
                "/v1/responses",
                {"model": "engineering-fast", "input": "stream-compatible completion", "stream": True},
            )

        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "text/event-stream")
        self.assertIn("response.completed", body.decode("utf-8"))
        self.assertEqual(len(FakeProviderHandler.requests), before + 1)
        self.assertEqual(self.gateway.store.monthly_totals(actor_id="alice").requests, 0)
        self.assertEqual(self.gateway.store.active_budget_reservations(), 1)

        connection = sqlite3.connect(self.gateway.store.path)
        events = connection.execute(
            "SELECT sequence, state, reason_code, usage_event_id FROM gateway_request_attempt_events ORDER BY sequence"
        ).fetchall()
        connection.close()
        self.assertEqual(events, [(1, "pending", None, None)])

    def test_openai_background_mode_is_denied_by_default(self) -> None:
        before = len(FakeProviderHandler.requests)
        status, _, response = self._post(
            "/v1/responses",
            {"model": "engineering-fast", "input": "hello", "background": True},
        )

        self.assertEqual(status, 403)
        self.assertEqual(len(FakeProviderHandler.requests), before)
        self.assertEqual(json.loads(response)["error"]["code"], "hormuz_provider_policy_denied")
        self.assertEqual(self.gateway.store.monthly_totals(actor_id="alice").denied_requests, 1)

    def test_admin_can_explicitly_allow_openai_storage_and_background_mode(self) -> None:
        config_value = self._config(self.provider.server_port, _free_port())
        config_value["upstreams"]["openai"]["allow_response_storage"] = True
        config_value["upstreams"]["openai"]["allow_background"] = True
        self._restart_gateway(config_value)

        status, _, _ = self._post(
            "/v1/responses",
            {
                "model": "engineering-fast",
                "input": "hello",
                "background": True,
                "store": True,
            },
        )

        self.assertEqual(status, 200)
        upstream = FakeProviderHandler.requests[-1]
        self.assertIs(upstream["body"]["background"], True)
        self.assertIs(upstream["body"]["store"], True)

    def test_anthropic_stream_is_relayed_and_usage_is_recorded(self) -> None:
        status, headers, response = self._post(
            "/v1/messages",
            {
                "model": "claude-sonnet-5",
                "messages": [{"role": "user", "content": "hello"}],
                "max_tokens": 50,
                "stream": True,
            },
            extra_headers={"Anthropic-Version": "2023-06-01"},
        )

        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "text/event-stream")
        self.assertIn("event: message_stop", response.decode("utf-8"))
        upstream = FakeProviderHandler.requests[-1]
        self.assertEqual(upstream["body"]["model"], "claude-sonnet-5")
        self.assertEqual(upstream["headers"]["x-api-key"], ANTHROPIC_KEY)
        self.assertNotIn("authorization", upstream["headers"])

        totals = self.gateway.store.monthly_totals(actor_id="alice")
        self.assertEqual(totals.input_tokens, 80)
        self.assertEqual(totals.output_tokens, 12)
        self.assertEqual(totals.cache_read_tokens, 20)
        self.assertEqual(totals.cache_write_tokens, 10)

    def test_anthropic_stream_without_terminal_usage_keeps_the_reservation(self) -> None:
        status, headers, response = self._post(
            "/v1/messages",
            {
                "model": "claude-sonnet-5",
                "messages": [{"role": "user", "content": "missing terminal usage"}],
                "max_tokens": 50,
                "stream": True,
                "force_missing_usage": True,
            },
            extra_headers={"Anthropic-Version": "2023-06-01"},
        )

        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "text/event-stream")
        self.assertIn(b"event: message_stop", response)
        self.assertEqual(self.gateway.store.monthly_totals(actor_id="alice").requests, 0)
        self.assertEqual(self.gateway.store.active_budget_reservations(), 1)

        connection = sqlite3.connect(self.gateway.store.path)
        events = connection.execute(
            "SELECT sequence,state,reason_code,usage_event_id "
            "FROM gateway_request_attempt_events ORDER BY sequence"
        ).fetchall()
        usage_count = connection.execute(
            "SELECT COUNT(*) FROM gateway_usage_events"
        ).fetchone()[0]
        connection.close()
        self.assertEqual(usage_count, 0)
        self.assertEqual(
            events,
            [
                (1, "pending", None, None),
                (2, "outcome_unknown", "provider_transport_ambiguous", None),
            ],
        )

    def test_disallowed_client_is_rejected_before_provider_call(self) -> None:
        before = len(FakeProviderHandler.requests)
        status, _, response = self._post(
            "/v1/responses",
            {"model": "engineering-fast", "input": "hello"},
            token=CLAUDE_ONLY_TOKEN,
        )
        self.assertEqual(status, 403)
        self.assertEqual(len(FakeProviderHandler.requests), before)
        self.assertEqual(json.loads(response)["error"]["code"], "hormuz_policy_denied")
        self.assertEqual(self.gateway.store.monthly_totals(actor_id="bob").denied_requests, 1)

    def test_nested_policy_can_restrict_but_cannot_relax_organization_policy(self) -> None:
        organization = Policy(
            allowed_clients=("codex",),
            allowed_models=("engineering-fast",),
            max_output_tokens=100,
            monthly_budget_usd=100,
            fallback_models={"openai": "engineering-fast"},
        )
        team = Policy(
            allowed_clients=("codex", "claude-code"),
            allowed_models=("engineering-fast", "engineering-deep"),
            max_output_tokens=500,
            monthly_budget_usd=500,
            fallback_models={"anthropic": "claude-standard"},
        )

        resolved = organization.overlaid(team)

        self.assertEqual(resolved.allowed_clients, ("codex",))
        self.assertEqual(resolved.allowed_models, ("engineering-fast",))
        self.assertEqual(resolved.max_output_tokens, 100)
        self.assertEqual(resolved.monthly_budget_usd, 100)
        self.assertEqual(
            resolved.fallback_models,
            {"openai": "engineering-fast", "anthropic": "claude-standard"},
        )

    def test_budget_is_enforced_before_provider_call(self) -> None:
        self.gateway.store.record(
            identity=next(iter(self.config.identities_by_token.values())),
            client="codex",
            protocol="openai",
            requested_model="engineering-fast",
            resolved_alias="engineering-fast",
            upstream_model="gpt-test-fast",
            policy_action="allowed",
            status="succeeded",
            cost_microusd=1_100_000,
        )
        before = len(FakeProviderHandler.requests)
        status, _, response = self._post(
            "/v1/responses",
            {"model": "engineering-fast", "input": "hello"},
        )
        self.assertEqual(status, 403)
        self.assertEqual(len(FakeProviderHandler.requests), before)
        self.assertIn("budget", json.loads(response)["error"]["message"].lower())

    def test_request_is_denied_when_conservative_reservation_would_exceed_budget(self) -> None:
        config_value = self._config(self.provider.server_port, _free_port())
        config_value["policies"]["organization"]["monthly_budget_usd"] = 0.00001
        self._restart_gateway(config_value)
        before = len(FakeProviderHandler.requests)

        status, _, response = self._post(
            "/v1/responses",
            {"model": "engineering-fast", "input": "hello"},
        )

        self.assertEqual(status, 403)
        self.assertEqual(len(FakeProviderHandler.requests), before)
        self.assertEqual(json.loads(response)["error"]["code"], "hormuz_budget_denied")
        self.assertEqual(self.gateway.store.active_budget_reservations(), 0)

    def test_invalid_token_is_rejected(self) -> None:
        status, _, _ = self._post(
            "/v1/responses",
            {"model": "engineering-fast", "input": "hello"},
            token="wrong-token",
        )
        self.assertEqual(status, 401)

    def test_secret_is_redacted_before_provider_and_audited(self) -> None:
        secret = "sk-" + "proj-" + ("A" * 24)
        status, headers, _ = self._post(
            "/v1/responses",
            {
                "model": "engineering-fast",
                "input": f"Never forward this credential: {secret}",
            },
        )

        self.assertEqual(status, 200)
        self.assertEqual(headers["x-hormuz-policy-decision"], "allowed+redacted")
        self.assertEqual(headers["x-hormuz-redactions"], "1")
        upstream = FakeProviderHandler.requests[-1]
        serialized = json.dumps(upstream["body"])
        self.assertNotIn(secret, serialized)
        self.assertIn("[REDACTED:HORMUZ_SECRET]", serialized)
        totals = self.gateway.store.monthly_totals(actor_id="alice")
        self.assertEqual(totals.redaction_count, 1)
        secret_totals = self.gateway.store.monthly_secret_totals(actor_id="alice")
        self.assertEqual(secret_totals.events, 1)
        self.assertEqual(secret_totals.detections, 1)
        self.assertEqual(secret_totals.redacted_requests, 1)
        summary = self.gateway.store.summary_rows()
        self.assertEqual(summary[0]["redactions"], 1)

    def test_gateway_identity_and_provider_credentials_are_always_exact_protected_values(self) -> None:
        status, headers, _ = self._post(
            "/v1/responses",
            {
                "model": "engineering-fast",
                "input": f"identity={GATEWAY_TOKEN} provider={OPENAI_KEY}",
            },
        )

        self.assertEqual(status, 200)
        self.assertEqual(headers["x-hormuz-redactions"], "2")
        upstream = json.dumps(FakeProviderHandler.requests[-1]["body"])
        self.assertNotIn(GATEWAY_TOKEN, upstream)
        self.assertNotIn(OPENAI_KEY, upstream)
        self.assertEqual(upstream.count("[REDACTED:HORMUZ_SECRET]"), 2)

    def test_secret_deny_mode_blocks_provider_and_records_metadata(self) -> None:
        config_value = self._config(self.provider.server_port, _free_port())
        config_value["egress_controls"] = {
            "secrets": {"mode": "deny", "builtins": True, "custom_secret_envs": []}
        }
        self._restart_gateway(config_value)
        before = len(FakeProviderHandler.requests)
        secret = "sk-ant-" + ("B" * 24)

        status, _, response = self._post(
            "/v1/responses",
            {"model": "engineering-fast", "input": f"credential={secret}"},
        )

        self.assertEqual(status, 403)
        self.assertEqual(len(FakeProviderHandler.requests), before)
        self.assertEqual(json.loads(response)["error"]["code"], "hormuz_secret_detected")
        totals = self.gateway.store.monthly_totals(actor_id="alice")
        self.assertEqual(totals.denied_requests, 1)
        self.assertEqual(totals.redaction_count, 1)
        secret_totals = self.gateway.store.monthly_secret_totals(actor_id="alice")
        self.assertEqual(secret_totals.denied_requests, 1)

    def test_token_count_redaction_has_security_audit_without_usage_charge(self) -> None:
        secret = "sk-" + "proj-" + ("E" * 24)
        status, headers, _ = self._post(
            "/v1/messages/count_tokens",
            {
                "model": "claude-sonnet-5",
                "messages": [{"role": "user", "content": f"credential={secret}"}],
            },
            extra_headers={"Anthropic-Version": "2023-06-01"},
        )

        self.assertEqual(status, 200)
        self.assertEqual(headers["x-hormuz-redactions"], "1")
        self.assertEqual(self.gateway.store.monthly_totals(actor_id="alice").requests, 0)
        secret_totals = self.gateway.store.monthly_secret_totals(actor_id="alice")
        self.assertEqual(secret_totals.events, 1)
        self.assertEqual(secret_totals.detections, 1)
        self.assertEqual(secret_totals.redacted_requests, 1)
        self.assertNotIn(secret, json.dumps(FakeProviderHandler.requests[-1]["body"]))

    @unittest.skipUnless(shutil.which("codex"), "Codex CLI is not installed")
    def test_installed_codex_routes_through_gateway(self) -> None:
        before = len(FakeProviderHandler.requests)
        environment = os.environ.copy()
        environment["TEST_GATEWAY_TOKEN"] = GATEWAY_TOKEN
        command = [
            "codex",
            "exec",
            "--ignore-user-config",
            "--skip-git-repo-check",
            "--ephemeral",
            "--sandbox",
            "read-only",
            "-C",
            str(self.root),
            "-m",
            "engineering-fast",
            "-c",
            'model_provider="company_gateway"',
            "-c",
            'model_providers.company_gateway.name="Hormuz"',
            "-c",
            f'model_providers.company_gateway.base_url="http://127.0.0.1:{self.gateway.server_port}/v1"',
            "-c",
            'model_providers.company_gateway.env_key="TEST_GATEWAY_TOKEN"',
            "-c",
            'model_providers.company_gateway.wire_api="responses"',
            "Reply with exactly GATEWAY_OK and do not call tools.",
        ]
        result = subprocess.run(command, env=environment, text=True, capture_output=True, timeout=30)
        self.assertEqual(
            result.returncode,
            0,
            msg=(
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}\n"
                f"provider_requests:\n{json.dumps(FakeProviderHandler.requests[before:], indent=2)}"
            ),
        )
        self.assertIn("GATEWAY_OK", result.stdout + result.stderr)
        self.assertGreater(len(FakeProviderHandler.requests), before)
        upstream = FakeProviderHandler.requests[-1]
        self.assertEqual(upstream["body"]["model"], "gpt-test-fast")
        self.assertIs(upstream["body"]["stream"], True)
        self.assertEqual(upstream["headers"]["authorization"], f"Bearer {OPENAI_KEY}")

    @unittest.skipUnless(shutil.which("codex"), "Codex CLI is not installed")
    def test_installed_codex_command_auth_recovers_after_401_without_duplicate_provider_egress(self) -> None:
        helper, count = _write_rotating_client_auth_helper(self.root)
        before = len(FakeProviderHandler.requests)
        provider = (
            "{name=\"Hormuz\",base_url=\"http://127.0.0.1:"
            + str(self.gateway.server_port)
            + "/v1\",wire_api=\"responses\",requires_openai_auth=false,auth={command="
            + json.dumps(str(helper))
            + ",args=["
            + json.dumps(str(count))
            + ","
            + json.dumps(GATEWAY_TOKEN)
            + "],refresh_interval_ms=0}}"
        )
        environment = os.environ.copy()
        for name in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_ORGANIZATION", "OPENAI_PROJECT", "CODEX_API_KEY"):
            environment.pop(name, None)
        result = subprocess.run(
            [
                "codex",
                "exec",
                "--ignore-user-config",
                "--skip-git-repo-check",
                "--ephemeral",
                "--sandbox",
                "read-only",
                "-C",
                str(self.root),
                "-m",
                "engineering-fast",
                "-c",
                'model_provider="hormuz_connector"',
                "-c",
                "model_providers.hormuz_connector=" + provider,
                "Reply with exactly GATEWAY_OK and do not call tools.",
            ],
            env=environment,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        calls = int(count.read_text()) if count.exists() else 0
        self.assertEqual(result.returncode, 0, msg=f"codex_401_recovery_failed:helper_calls={calls}")
        self.assertEqual(calls, 2)
        self.assertIn("GATEWAY_OK", result.stdout + result.stderr)
        self.assertEqual(len(FakeProviderHandler.requests), before + 1)
        self.assertTrue(str(FakeProviderHandler.requests[-1]["path"]).partition("?")[0].endswith("/responses"))

    @unittest.skipUnless(
        os.environ.get("HORMUZ_RUN_CLAUDE_CLIENT_TEST") == "1"
        and (shutil.which("claude") or shutil.which("npx")),
        "Set HORMUZ_RUN_CLAUDE_CLIENT_TEST=1 and install Claude Code or npx",
    )
    def test_official_claude_code_routes_through_gateway(self) -> None:
        before = len(FakeProviderHandler.requests)
        debug_path = self.root / "claude-debug.log"
        environment = os.environ.copy()
        environment["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{self.gateway.server_port}"
        environment["ANTHROPIC_API_KEY"] = GATEWAY_TOKEN
        environment.pop("ANTHROPIC_AUTH_TOKEN", None)
        environment["DISABLE_AUTOUPDATER"] = "1"
        environment["CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT"] = "1"
        claude = shutil.which("claude")
        command = ([claude] if claude else ["npx", "-y", "@anthropic-ai/claude-code"]) + [
            "-p",
            "--bare",
            "--debug",
            "api",
            "--debug-file",
            str(debug_path),
            "--no-session-persistence",
            "--tools",
            "",
            "--model",
            "claude-sonnet-5",
            "Reply with exactly ok and do not call tools.",
        ]
        result = subprocess.run(
            command,
            cwd=self.root,
            env=environment,
            text=True,
            capture_output=True,
            timeout=45,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=(
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}\n"
                f"debug:\n{debug_path.read_text(encoding='utf-8') if debug_path.exists() else '<missing>'}\n"
                f"provider_requests:\n{json.dumps(FakeProviderHandler.requests[before:], indent=2)}"
            ),
        )
        self.assertIn("ok", result.stdout.lower())
        self.assertGreater(len(FakeProviderHandler.requests), before)
        generation_requests = [
            request
            for request in FakeProviderHandler.requests[before:]
            if str(request["path"]).partition("?")[0].endswith("/messages")
        ]
        self.assertTrue(generation_requests)
        upstream = generation_requests[-1]
        self.assertEqual(upstream["body"]["model"], "claude-sonnet-5")
        self.assertIs(upstream["body"]["stream"], True)
        self.assertEqual(upstream["headers"]["x-api-key"], ANTHROPIC_KEY)
        self.assertNotIn("authorization", upstream["headers"])

    @unittest.skipUnless(
        os.environ.get("HORMUZ_RUN_CLAUDE_CLIENT_TEST") == "1"
        and (shutil.which("claude") or shutil.which("npx")),
        "Set HORMUZ_RUN_CLAUDE_CLIENT_TEST=1 and install Claude Code or npx",
    )
    def test_official_claude_code_refreshes_after_401_and_requires_explicit_request_retry(self) -> None:
        helper, count = _write_rotating_client_auth_helper(self.root)
        settings_path = self.root / "claude-401-settings.json"
        settings_path.write_text(
            json.dumps(
                {
                    "apiKeyHelper": shlex.join([str(helper), str(count), GATEWAY_TOKEN]),
                    "env": {
                        "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{self.gateway.server_port}",
                        "CLAUDE_CODE_API_KEY_HELPER_TTL_MS": "60000",
                        "ANTHROPIC_API_KEY": "",
                        "ANTHROPIC_AUTH_TOKEN": "",
                        "CLAUDE_CODE_OAUTH_TOKEN": "",
                        "DISABLE_AUTOUPDATER": "1",
                        "CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT": "1",
                    },
                }
            ),
            encoding="utf-8",
        )
        environment = os.environ.copy()
        for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"):
            environment.pop(name, None)
        claude = shutil.which("claude")
        command = ([claude] if claude else ["npx", "-y", "@anthropic-ai/claude-code"]) + [
            "-p",
            "--bare",
            "--no-session-persistence",
            "--tools",
            "",
            "--settings",
            str(settings_path),
            "--model",
            "claude-sonnet-5",
            "Reply with exactly ok and do not call tools.",
        ]
        before = len(FakeProviderHandler.requests)
        rejected = subprocess.run(
            command,
            cwd=self.root,
            env=environment,
            text=True,
            capture_output=True,
            timeout=45,
            check=False,
        )
        calls_after_rejection = int(count.read_text()) if count.exists() else 0
        self.assertNotEqual(rejected.returncode, 0)
        self.assertEqual(calls_after_rejection, 2)
        self.assertEqual(len(FakeProviderHandler.requests), before)

        retried = subprocess.run(
            command,
            cwd=self.root,
            env=environment,
            text=True,
            capture_output=True,
            timeout=45,
            check=False,
        )
        calls_after_retry = int(count.read_text()) if count.exists() else 0
        self.assertEqual(retried.returncode, 0, msg=f"claude_401_recovery_failed:helper_calls={calls_after_retry}")
        self.assertEqual(calls_after_retry, 3)
        self.assertIn("ok", retried.stdout.lower())
        retry_generation_requests = [
            request
            for request in FakeProviderHandler.requests[before:]
            if str(request["path"]).partition("?")[0].endswith("/messages")
        ]
        self.assertTrue(retry_generation_requests)

        control_before = len(FakeProviderHandler.requests)
        control = subprocess.run(
            command,
            cwd=self.root,
            env=environment,
            text=True,
            capture_output=True,
            timeout=45,
            check=False,
        )
        self.assertEqual(control.returncode, 0, msg="claude_clean_credential_control_failed")
        control_generation_requests = [
            request
            for request in FakeProviderHandler.requests[control_before:]
            if str(request["path"]).partition("?")[0].endswith("/messages")
        ]
        self.assertEqual(len(retry_generation_requests), len(control_generation_requests))

    def _post(self, path: str, body: dict, *, extra_headers: dict[str, str] | None = None, token: str = GATEWAY_TOKEN):
        connection = http.client.HTTPConnection("127.0.0.1", self.gateway.server_port, timeout=5)
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        headers.update(extra_headers or {})
        connection.request("POST", path, body=json.dumps(body), headers=headers)
        response = connection.getresponse()
        data = response.read()
        response_headers = {name.lower(): value for name, value in response.getheaders()}
        connection.close()
        return response.status, response_headers, data

    def _get(self, path: str, *, token: str | None = GATEWAY_TOKEN):
        connection = http.client.HTTPConnection("127.0.0.1", self.gateway.server_port, timeout=5)
        headers = {} if token is None else {"Authorization": f"Bearer {token}"}
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        data = response.read()
        response_headers = {name.lower(): value for name, value in response.getheaders()}
        connection.close()
        return response.status, response_headers, data

    def _restart_gateway(self, config_value: dict) -> None:
        self.gateway.shutdown()
        self.gateway.server_close()
        self.config_path.write_text(json.dumps(config_value), encoding="utf-8")
        self.config = GatewayConfig.load(self.config_path)
        self.gateway = GatewayServer(self.config)
        self.gateway_thread = serve_in_thread(self.gateway)

    def _config_with_failover(self) -> dict:
        config_value = self._config(self.provider.server_port, _free_port())
        config_value["model_routes"]["engineering-fast"]["failover_alias"] = "engineering-deep"
        return config_value

    def _config(self, provider_port: int, gateway_port: int) -> dict:
        return {
            "listen": {"host": "127.0.0.1", "port": gateway_port},
            "database": "./usage.sqlite3",
            "upstreams": {
                "openai": {"base_url": f"http://127.0.0.1:{provider_port}", "api_key_env": "TEST_OPENAI_KEY"},
                "anthropic": {
                    "base_url": f"http://127.0.0.1:{provider_port}",
                    "api_key_env": "TEST_ANTHROPIC_KEY",
                },
            },
            "identities": [
                {
                    "token_env": "TEST_GATEWAY_TOKEN",
                    "actor_id": "alice",
                    "actor_name": "Alice",
                    "team_id": "engineering",
                    "team_name": "Engineering",
                    "allowed_clients": ["codex", "claude-code"],
                },
                {
                    "token_env": "TEST_CLAUDE_ONLY_TOKEN",
                    "actor_id": "bob",
                    "actor_name": "Bob",
                    "team_id": "engineering",
                    "team_name": "Engineering",
                    "allowed_clients": ["claude-code"],
                },
            ],
            "model_routes": {
                "engineering-fast": {
                    "protocol": "openai",
                    "upstream_model": "gpt-test-fast",
                    "input_cost_per_million": 1,
                    "cache_read_cost_per_million": 0.1,
                    "output_cost_per_million": 2,
                },
                "engineering-deep": {"protocol": "openai", "upstream_model": "gpt-test-deep"},
                "claude-standard": {
                    "protocol": "anthropic",
                    "upstream_model": "claude-test",
                    "input_cost_per_million": 3,
                    "cache_read_cost_per_million": 0.3,
                    "cache_write_cost_per_million": 3.75,
                    "output_cost_per_million": 15,
                },
                "claude-sonnet-5": {"protocol": "anthropic", "upstream_model": "claude-sonnet-5"},
                "claude-haiku-4-5": {"protocol": "anthropic", "upstream_model": "claude-test-haiku"},
                "claude-haiku-4-5-20251001": {
                    "protocol": "anthropic",
                    "upstream_model": "claude-test-haiku",
                },
            },
            "policies": {
                "organization": {
                    "allowed_clients": ["codex", "claude-code"],
                    "allowed_models": [
                        "engineering-fast",
                        "engineering-deep",
                        "claude-standard",
                        "claude-sonnet-5",
                        "claude-haiku-4-5",
                        "claude-haiku-4-5-20251001",
                    ],
                    "max_output_tokens": 100,
                    "monthly_budget_usd": 1,
                },
                "teams": {
                    "engineering": {
                        "allowed_models": [
                            "engineering-fast",
                            "engineering-deep",
                            "claude-standard",
                            "claude-sonnet-5",
                            "claude-haiku-4-5",
                            "claude-haiku-4-5-20251001",
                        ],
                        "fallback_models": {
                            "openai": "engineering-fast",
                            "anthropic": "claude-sonnet-5",
                        },
                    }
                },
                "actors": {},
            },
        }


class ExternalProxyIngressIntegrationTests(unittest.TestCase):
    """Exercise the customer-controlled TLS proxy boundary at the HTTP edge."""

    ingress_credential = "customer-proxy-credential-with-sufficient-length"

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        FakeProviderHandler.requests = []
        self.provider = ThreadingHTTPServer(("127.0.0.1", 0), FakeProviderHandler)
        self.provider_thread = threading.Thread(target=self.provider.serve_forever, daemon=True)
        self.provider_thread.start()

        os.environ["TEST_GATEWAY_TOKEN"] = GATEWAY_TOKEN
        os.environ["TEST_CLAUDE_ONLY_TOKEN"] = CLAUDE_ONLY_TOKEN
        os.environ["TEST_OPENAI_KEY"] = OPENAI_KEY
        os.environ["TEST_ANTHROPIC_KEY"] = ANTHROPIC_KEY
        os.environ["TEST_INGRESS_CREDENTIAL"] = self.ingress_credential

        self.config_path = self.root / "gateway.json"
        self.config_value = GatewayIntegrationTests._config(self, self.provider.server_port, _free_port())
        self.config_value["ingress"] = {
            "mode": "external_tls_proxy",
            "trusted_proxy_cidrs": ["127.0.0.1/32"],
            "credential_env": "TEST_INGRESS_CREDENTIAL",
        }
        self._start_gateway()

    def tearDown(self) -> None:
        self.gateway.shutdown()
        self.gateway.server_close()
        self.provider.shutdown()
        self.provider.server_close()
        os.environ.pop("TEST_INGRESS_CREDENTIAL", None)
        self.temporary.cleanup()

    def test_every_route_rejects_missing_or_wrong_ingress_before_side_effects(self) -> None:
        before = len(FakeProviderHandler.requests)
        cases = (
            ("GET", "/health", None),
            ("GET", "/ready", None),
            ("OPTIONS", "/v1/responses", None),
            ("POST", "/v1/responses", {"model": "engineering-fast", "input": "blocked"}),
            ("DELETE", "/v1/unknown", None),
        )
        for method, path, body in cases:
            with self.subTest(method=method, path=path):
                status, headers, response = self._request(method, path, body=body)
                self._assert_ingress_denied(status, headers, response)

                status, headers, response = self._request(
                    method,
                    path,
                    body=body,
                    headers={"X-Hormuz-Ingress-Credential": "wrong-proxy-credential"},
                )
                self._assert_ingress_denied(status, headers, response)

        self.assertEqual(len(FakeProviderHandler.requests), before)
        self.assertEqual(self.gateway.store.monthly_totals(actor_id="alice").requests, 0)

    def test_untrusted_source_is_denied_even_with_the_proxy_credential(self) -> None:
        self.config_value["ingress"]["trusted_proxy_cidrs"] = ["10.42.0.0/16"]
        self._restart_gateway()

        status, headers, response = self._request(
            "POST",
            "/v1/responses",
            body={"model": "engineering-fast", "input": "blocked"},
            headers={
                "Authorization": f"Bearer {GATEWAY_TOKEN}",
                "X-Hormuz-Ingress-Credential": self.ingress_credential,
            },
        )

        self._assert_ingress_denied(status, headers, response)
        self.assertEqual(FakeProviderHandler.requests, [])
        self.assertEqual(self.gateway.store.monthly_totals(actor_id="alice").requests, 0)

    def test_duplicate_ingress_credentials_fail_closed(self) -> None:
        connection = http.client.HTTPConnection("127.0.0.1", self.gateway.server_port, timeout=5)
        connection.putrequest("GET", "/health")
        connection.putheader("X-Hormuz-Ingress-Credential", self.ingress_credential)
        connection.putheader("X-Hormuz-Ingress-Credential", self.ingress_credential)
        connection.endheaders()
        response = connection.getresponse()
        body = response.read()
        headers = {name.lower(): value for name, value in response.getheaders()}
        connection.close()

        self._assert_ingress_denied(response.status, headers, body)
        self.assertEqual(FakeProviderHandler.requests, [])

    def test_trusted_proxy_request_is_allowed_and_does_not_forward_proxy_headers(self) -> None:
        status, _, response = self._request(
            "POST",
            "/v1/responses",
            body={"model": "engineering-fast", "input": "allowed"},
            headers={
                "Authorization": f"Bearer {GATEWAY_TOKEN}",
                "X-Hormuz-Ingress-Credential": self.ingress_credential,
                "Forwarded": "for=203.0.113.40;proto=https",
                "X-Forwarded-For": "203.0.113.40",
                "X-Forwarded-Proto": "https",
                "X-Real-IP": "203.0.113.40",
            },
        )

        self.assertEqual(status, 200, response)
        self.assertEqual(self.gateway.store.monthly_totals(actor_id="alice").requests, 1)
        self.assertEqual(len(FakeProviderHandler.requests), 1)
        upstream_headers = FakeProviderHandler.requests[0]["headers"]
        assert isinstance(upstream_headers, dict)
        for name in (
            "x-hormuz-ingress-credential",
            "forwarded",
            "x-forwarded-for",
            "x-forwarded-proto",
            "x-real-ip",
        ):
            self.assertNotIn(name, upstream_headers)

        status, _, response = self._request(
            "GET",
            "/health",
            headers={"X-Hormuz-Ingress-Credential": self.ingress_credential},
        )
        self.assertEqual(status, 200, response)

    def _start_gateway(self) -> None:
        self.config_path.write_text(json.dumps(self.config_value), encoding="utf-8")
        self.config = GatewayConfig.load(self.config_path)
        self.gateway = GatewayServer(self.config)
        self.gateway_thread = serve_in_thread(self.gateway)

    def _restart_gateway(self) -> None:
        self.gateway.shutdown()
        self.gateway.server_close()
        self._start_gateway()

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", self.gateway.server_port, timeout=5)
        request_headers = dict(headers or {})
        if body is not None:
            request_headers.setdefault("Content-Type", "application/json")
        connection.request(method, path, body=None if body is None else json.dumps(body), headers=request_headers)
        response = connection.getresponse()
        response_body = response.read()
        response_headers = {name.lower(): value for name, value in response.getheaders()}
        connection.close()
        return response.status, response_headers, response_body

    def _assert_ingress_denied(self, status: int, headers: dict[str, str], response: bytes) -> None:
        self.assertEqual(status, 401, response)
        self.assertEqual(headers["x-hormuz-contract"], "hormuz.gateway-error;v=3")
        payload = json.loads(response)
        validate_contract(payload)
        self.assertEqual(payload["error"], {"code": "unauthorized", "message": "Missing or invalid Hormuz ingress credential"})


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


if __name__ == "__main__":
    unittest.main()
