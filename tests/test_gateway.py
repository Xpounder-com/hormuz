from __future__ import annotations

import http.client
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from hormuz.config import GatewayConfig, Policy
from hormuz.contracts import relay_contract_header, validate_audit_event, validate_contract
from hormuz.policy import PolicyEngine
from hormuz.postgres import PostgresStorageError
from hormuz.server import GatewayServer, serve_in_thread
from hormuz.store import UsageStore


GATEWAY_TOKEN = "company-user-token-never-forward"
CLAUDE_ONLY_TOKEN = "company-claude-only-token-never-forward"
OPENAI_KEY = "provider-openai-secret"
ANTHROPIC_KEY = "provider-anthropic-secret"


class FakeProviderHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    requests: list[dict[str, object]] = []
    lock = threading.Lock()

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

        if request_path.endswith("/responses"):
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
                        "usage": {"output_tokens": 12},
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
        completed_message = {
            "id": message_id,
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": text, "annotations": [], "logprobs": []}],
        }
        completed = {**completed_response, "output": [completed_message]}
        events = [
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
        body_bytes = "".join(
            f"event: {event['type']}\ndata: {json.dumps(event, separators=(',', ':'))}\n\n" for event in events
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body_bytes)))
        self.send_header("x-request-id", "req_codex_probe")
        self.end_headers()
        self.wfile.write(body_bytes)

    def log_message(self, format: str, *args: object) -> None:
        pass


class GatewayIntegrationTests(unittest.TestCase):
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
        self.assertEqual(headers["x-hormuz-contract"], "hormuz.gateway-error;v=2")
        validate_contract(json.loads(body))

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
        self.assertEqual(headers["x-hormuz-contract"], "hormuz.gateway-error;v=2")
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
        self.assertEqual(upstream["headers"]["authorization"], f"Bearer {OPENAI_KEY}")

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
        self.assertEqual(upstream["headers"]["x-api-key"], ANTHROPIC_KEY)
        self.assertNotIn("authorization", upstream["headers"])

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


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


if __name__ == "__main__":
    unittest.main()
