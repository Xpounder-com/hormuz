from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import threading
import time
import tomllib
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from hormuz.cli import _mcp_config, main
from hormuz.mcp import (
    MAX_INPUT_LINE_BYTES,
    MAX_RESPONSE_BYTES,
    MODERN_PROTOCOL_VERSION,
    TOOL_NAME,
    ContextGatewayError,
    ContextPackClient,
    MCPConfigurationError,
    StdioMCPServer,
    _read_json_response,
    validate_gateway_url,
    validate_tool_arguments,
)


ROOT = Path(__file__).resolve().parents[1]
TEST_TOKEN = "mcp-company-token-never-return"


def _pack() -> dict[str, object]:
    return {
        "schema_version": "hormuz.context-pack.v1",
        "pack_id": "ctxpack_0123456789abcdef01234567",
        "manifest_sha256": "a" * 64,
        "query": "retry policy",
        "policy_version": "engineering-v1",
        "as_of": "2026-08-15T00:00:00Z",
        "scope": {
            "organization_id": "xpounder",
            "team_id": "engineering",
            "actor_id": "alice",
            "clearance": "internal",
            "repository_id": "Xpounder-com/hormuz",
            "branch": "main",
        },
        "token_budget": 500,
        "estimated_tokens": 0,
        "eligible_records": 0,
        "matched_records": 0,
        "selected_records": 0,
        "items": [],
    }


class _FakeContextClient:
    def __init__(self, *, delay: float = 0) -> None:
        self.arguments: list[object] = []
        self.delay = delay

    def create_pack(self, arguments: object) -> dict[str, object]:
        self.arguments.append(arguments)
        if self.delay:
            time.sleep(self.delay)
        return _pack()


class _GatewayHandler(BaseHTTPRequestHandler):
    requests: list[dict[str, object]] = []
    response_status = 200
    response_body: object = _pack()
    response_content_type = "application/json"
    response_headers: dict[str, str] = {}

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.__class__.requests.append(
            {
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "body": json.loads(body),
            }
        )
        payload = json.dumps(self.__class__.response_body).encode("utf-8")
        self.send_response(self.__class__.response_status)
        self.send_header("Content-Type", self.__class__.response_content_type)
        self.send_header("Content-Length", str(len(payload)))
        for name, value in self.__class__.response_headers.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        return


class MCPProtocolTests(unittest.TestCase):
    def _serve(self, messages: list[dict[str, object]], client=None) -> list[dict[str, object]]:
        input_stream = io.StringIO("".join(json.dumps(message) + "\n" for message in messages))
        output_stream = io.StringIO()
        StdioMCPServer(
            client or _FakeContextClient(),
            input_stream=input_stream,
            output_stream=output_stream,
        ).serve_forever()
        return [json.loads(line) for line in output_stream.getvalue().splitlines()]

    def test_legacy_initialize_lists_and_calls_the_governed_context_tool(self) -> None:
        client = _FakeContextClient()
        responses = self._serve(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "clientInfo": {"name": "codex", "version": "test"},
                        "capabilities": {},
                    },
                },
                {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                {
                    "jsonrpc": "2.0",
                    "id": "call-1",
                    "method": "tools/call",
                    "params": {
                        "name": TOOL_NAME,
                        "arguments": {
                            "query": "retry policy",
                            "token_budget": 500,
                            "repository_id": "Xpounder-com/hormuz",
                        },
                    },
                },
            ],
            client,
        )
        by_id = {response["id"]: response for response in responses}

        self.assertEqual(by_id[1]["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(by_id[2]["result"]["tools"][0]["name"], TOOL_NAME)
        self.assertTrue(by_id[2]["result"]["tools"][0]["annotations"]["readOnlyHint"])
        result = by_id["call-1"]["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["pack_id"], _pack()["pack_id"])
        self.assertNotIn("resultType", result)
        self.assertEqual(client.arguments[0]["token_budget"], 500)

    def test_modern_discovery_and_tool_call_require_per_request_protocol_metadata(self) -> None:
        meta = {
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": MODERN_PROTOCOL_VERSION,
                "io.modelcontextprotocol/clientInfo": {"name": "future-client", "version": "1"},
                "io.modelcontextprotocol/clientCapabilities": {},
            }
        }
        responses = self._serve(
            [
                {"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": meta},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": meta},
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        **meta,
                        "name": TOOL_NAME,
                        "arguments": {"query": "retry", "token_budget": 100},
                    },
                },
            ]
        )
        by_id = {response["id"]: response for response in responses}

        self.assertIn(MODERN_PROTOCOL_VERSION, by_id[1]["result"]["supportedVersions"])
        self.assertEqual(by_id[1]["result"]["resultType"], "complete")
        self.assertEqual(by_id[2]["result"]["tools"][0]["name"], TOOL_NAME)
        self.assertEqual(by_id[2]["result"]["resultType"], "complete")
        self.assertEqual(by_id[3]["result"]["resultType"], "complete")

    def test_uninitialized_and_unsupported_protocols_fail_with_stable_json_rpc_errors(self) -> None:
        responses = self._serve(
            [
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "server/discover",
                    "params": {
                        "_meta": {"io.modelcontextprotocol/protocolVersion": "2099-01-01"}
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "server/discover",
                    "params": {
                        "_meta": {
                            "io.modelcontextprotocol/protocolVersion": MODERN_PROTOCOL_VERSION,
                            "io.modelcontextprotocol/clientCapabilities": {},
                        }
                    },
                },
            ]
        )
        by_id = {response["id"]: response for response in responses}
        self.assertEqual(by_id[1]["error"]["code"], -32002)
        self.assertEqual(by_id[2]["error"]["code"], -32022)
        self.assertIn(MODERN_PROTOCOL_VERSION, by_id[2]["error"]["data"]["supportedVersions"])
        self.assertEqual(by_id[3]["error"]["code"], -32602)

    def test_non_standard_json_constants_are_rejected(self) -> None:
        input_stream = io.StringIO(
            '{"jsonrpc":"2.0","id":NaN,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}\n'
        )
        output_stream = io.StringIO()
        StdioMCPServer(
            _FakeContextClient(),
            input_stream=input_stream,
            output_stream=output_stream,
        ).serve_forever()
        response = json.loads(output_stream.getvalue())
        self.assertEqual(response["error"]["code"], -32700)

    def test_oversized_stdio_message_is_rejected_before_parsing(self) -> None:
        input_stream = io.StringIO(
            " " * (MAX_INPUT_LINE_BYTES + 10)
            + "\n"
            + json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18"},
                }
            )
            + "\n"
        )
        output_stream = io.StringIO()
        StdioMCPServer(
            _FakeContextClient(),
            input_stream=input_stream,
            output_stream=output_stream,
        ).serve_forever()
        responses = [json.loads(line) for line in output_stream.getvalue().splitlines()]
        self.assertEqual(responses[0]["error"]["code"], -32600)
        self.assertEqual(responses[1]["id"], 1)
        self.assertEqual(responses[1]["result"]["protocolVersion"], "2025-06-18")

    def test_cancelled_tool_call_does_not_return_context(self) -> None:
        responses = self._serve(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": "2025-11-25"},
                },
                {
                    "jsonrpc": "2.0",
                    "id": "slow",
                    "method": "tools/call",
                    "params": {
                        "name": TOOL_NAME,
                        "arguments": {"query": "retry", "token_budget": 100},
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/cancelled",
                    "params": {"requestId": "slow", "reason": "client cancelled"},
                },
            ],
            _FakeContextClient(delay=0.05),
        )
        self.assertEqual([response["id"] for response in responses], [1])

    def test_tool_errors_are_data_and_do_not_expose_internal_exceptions(self) -> None:
        class FailingClient:
            def create_pack(self, arguments: object) -> dict[str, object]:
                raise RuntimeError("INTERNAL-SECRET")

        responses = self._serve(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18"},
                },
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": TOOL_NAME,
                        "arguments": {"query": "retry", "token_budget": 100},
                    },
                },
            ],
            FailingClient(),
        )
        result = {response["id"]: response for response in responses}[2]["result"]
        self.assertTrue(result["isError"])
        self.assertEqual(
            result["structuredContent"]["error"]["code"],
            "context_gateway_unavailable",
        )
        self.assertNotIn("INTERNAL-SECRET", json.dumps(result))


class ContextPackClientTests(unittest.TestCase):
    def setUp(self) -> None:
        _GatewayHandler.requests = []
        _GatewayHandler.response_status = 200
        _GatewayHandler.response_body = _pack()
        _GatewayHandler.response_content_type = "application/json"
        _GatewayHandler.response_headers = {}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _GatewayHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_calls_the_authenticated_rest_boundary_without_identity_override_fields(self) -> None:
        client = ContextPackClient(self.base_url, "COMPANY_TOKEN", timeout_seconds=2)
        with mock.patch.dict(os.environ, {"COMPANY_TOKEN": TEST_TOKEN}):
            pack = client.create_pack(
                {
                    "query": "retry policy",
                    "token_budget": 500,
                    "repository_id": "Xpounder-com/hormuz",
                    "branch": "main",
                }
            )

        self.assertEqual(pack["pack_id"], _pack()["pack_id"])
        self.assertEqual(len(_GatewayHandler.requests), 1)
        request = _GatewayHandler.requests[0]
        self.assertEqual(request["path"], "/v1/context/packs")
        self.assertEqual(request["authorization"], f"Bearer {TEST_TOKEN}")
        self.assertEqual(request["body"]["repository_id"], "Xpounder-com/hormuz")
        for forbidden in ("organization_id", "team_id", "actor_id", "policy_version", "as_of"):
            self.assertNotIn(forbidden, request["body"])

    def test_resolves_a_fresh_session_credential_for_every_context_request(self) -> None:
        credentials = iter(("first-session-token", "second-session-token"))
        provider = mock.Mock(side_effect=lambda: next(credentials))
        client = ContextPackClient(
            self.base_url,
            credential_provider=provider,
            timeout_seconds=2,
        )

        client.create_pack({"query": "first", "token_budget": 100})
        client.create_pack({"query": "second", "token_budget": 100})

        self.assertEqual(provider.call_count, 2)
        self.assertEqual(
            [request["authorization"] for request in _GatewayHandler.requests],
            ["Bearer first-session-token", "Bearer second-session-token"],
        )

    def test_sanitizes_session_provider_failures_and_invalid_credentials(self) -> None:
        for provider in (
            mock.Mock(side_effect=RuntimeError("INTERNAL-REFRESH-SECRET")),
            mock.Mock(return_value="invalid\ncredential"),
            mock.Mock(return_value="invalid-\udcff-credential"),
        ):
            with self.subTest(provider=provider):
                client = ContextPackClient(
                    self.base_url,
                    credential_provider=provider,
                    timeout_seconds=2,
                )
                with self.assertRaises(ContextGatewayError) as raised:
                    client.create_pack({"query": "retry", "token_budget": 100})
                self.assertEqual(raised.exception.code, "context_auth_unavailable")
                self.assertNotIn("INTERNAL-REFRESH-SECRET", raised.exception.message)
                self.assertNotIn("invalid", raised.exception.message.lower())

    def test_maps_gateway_policy_error_without_exposing_credential(self) -> None:
        _GatewayHandler.response_status = 403
        _GatewayHandler.response_body = {
            "error": {
                "code": "context_policy_denied",
                "message": "Requested token budget exceeds organization context policy",
            }
        }
        client = ContextPackClient(self.base_url, "COMPANY_TOKEN", timeout_seconds=2)
        with mock.patch.dict(os.environ, {"COMPANY_TOKEN": TEST_TOKEN}):
            with self.assertRaises(ContextGatewayError) as raised:
                client.create_pack({"query": "retry", "token_budget": 9999})

        self.assertEqual(raised.exception.code, "context_policy_denied")
        self.assertNotIn(TEST_TOKEN, raised.exception.message)

    def test_does_not_follow_gateway_redirects(self) -> None:
        _GatewayHandler.response_status = 307
        _GatewayHandler.response_body = {"error": "redirect"}
        _GatewayHandler.response_headers = {"Location": f"{self.base_url}/credential-leak"}
        client = ContextPackClient(self.base_url, "COMPANY_TOKEN", timeout_seconds=2)
        with mock.patch.dict(os.environ, {"COMPANY_TOKEN": TEST_TOKEN}):
            with self.assertRaises(ContextGatewayError) as raised:
                client.create_pack({"query": "retry", "token_budget": 100})
        self.assertEqual(raised.exception.code, "context_gateway_error")
        self.assertEqual(len(_GatewayHandler.requests), 1)
        self.assertEqual(_GatewayHandler.requests[0]["path"], "/v1/context/packs")

    def test_rejects_non_json_and_oversized_gateway_responses(self) -> None:
        _GatewayHandler.response_content_type = "text/plain"
        client = ContextPackClient(self.base_url, "COMPANY_TOKEN", timeout_seconds=2)
        with mock.patch.dict(os.environ, {"COMPANY_TOKEN": TEST_TOKEN}):
            with self.assertRaises(ContextGatewayError) as raised:
                client.create_pack({"query": "retry", "token_budget": 100})
        self.assertEqual(raised.exception.code, "context_gateway_invalid_response")

        class Headers:
            def get_content_type(self) -> str:
                return "application/json"

        class OversizedResponse(io.BytesIO):
            headers = Headers()

        with self.assertRaises(ContextGatewayError) as oversized:
            _read_json_response(OversizedResponse(b"x" * (MAX_RESPONSE_BYTES + 1)))
        self.assertEqual(oversized.exception.code, "context_gateway_invalid_response")

    def test_requires_https_except_for_loopback_and_rejects_url_credentials(self) -> None:
        self.assertEqual(validate_gateway_url("http://localhost:8787/"), "http://localhost:8787")
        self.assertEqual(validate_gateway_url("https://hormuz.example"), "https://hormuz.example")
        for value in (
            "http://hormuz.example",
            "https://user:password@hormuz.example",
            "https://hormuz.example?token=secret",
            "https://hormuz.example/#fragment",
            "https://hormuz.example/unsafe path",
        ):
            with self.subTest(value=value):
                with self.assertRaises(MCPConfigurationError):
                    validate_gateway_url(value)

    def test_tool_contract_rejects_identity_injection_and_invalid_scope(self) -> None:
        with self.assertRaises(ContextGatewayError) as raised:
            validate_tool_arguments(
                {"query": "retry", "token_budget": 100, "organization_id": "attacker"}
            )
        self.assertEqual(raised.exception.code, "context_invalid_request")
        with self.assertRaises(ContextGatewayError):
            validate_tool_arguments(
                {"query": "retry", "token_budget": 100, "repository_id": "repo\nforged"}
            )
        with self.assertRaises(ContextGatewayError):
            validate_tool_arguments({"query": "retry", "token_budget": 100, "branch": "main"})


class MCPConfigurationTests(unittest.TestCase):
    def test_config_generators_emit_no_secret_values(self) -> None:
        codex = io.StringIO()
        with redirect_stdout(codex):
            self.assertEqual(
                _mcp_config(
                    "codex",
                    "https://hormuz.example",
                    credential_env="COMPANY_HORMUZ_TOKEN",
                ),
                0,
            )
        parsed_codex = tomllib.loads(codex.getvalue())
        server = parsed_codex["mcp_servers"]["hormuz"]
        self.assertEqual(server["command"], "hormuz")
        self.assertEqual(server["env_vars"], ["COMPANY_HORMUZ_TOKEN"])
        self.assertNotIn(TEST_TOKEN, codex.getvalue())

        claude = io.StringIO()
        with redirect_stdout(claude):
            self.assertEqual(
                _mcp_config(
                    "claude",
                    "https://hormuz.example",
                    credential_env="COMPANY_HORMUZ_TOKEN",
                ),
                0,
            )
        parsed_claude = json.loads(claude.getvalue())
        server = parsed_claude["mcpServers"]["hormuz"]
        self.assertEqual(server["env"]["COMPANY_HORMUZ_TOKEN"], "${COMPANY_HORMUZ_TOKEN}")
        self.assertNotIn(TEST_TOKEN, claude.getvalue())

    def test_mcp_config_does_not_require_a_server_configuration_file(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            result = main(
                [
                    "--config",
                    "/definitely/missing/hormuz.json",
                    "mcp-config",
                    "codex",
                    "--url",
                    "https://hormuz.example",
                ]
            )
        self.assertEqual(result, 0)
        self.assertIn("[mcp_servers.hormuz]", output.getvalue())

    def test_profile_configs_are_secret_free_for_codex_and_claude(self) -> None:
        codex = io.StringIO()
        with redirect_stdout(codex):
            self.assertEqual(
                _mcp_config(
                    "codex",
                    "https://hormuz.example",
                    profile="engineering",
                ),
                0,
            )
        codex_server = tomllib.loads(codex.getvalue())["mcp_servers"]["hormuz"]
        self.assertEqual(
            codex_server["args"],
            [
                "mcp",
                "--url",
                "https://hormuz.example",
                "--profile",
                "engineering",
                "--timeout-seconds",
                "30",
            ],
        )
        self.assertNotIn("env_vars", codex_server)

        claude = io.StringIO()
        with redirect_stdout(claude):
            self.assertEqual(
                _mcp_config(
                    "claude",
                    "https://hormuz.example",
                    profile="engineering",
                ),
                0,
            )
        claude_server = json.loads(claude.getvalue())["mcpServers"]["hormuz"]
        self.assertEqual(claude_server["args"], codex_server["args"])
        self.assertNotIn("env", claude_server)

    def test_profile_cli_resolves_the_secure_session_per_request(self) -> None:
        captured: dict[str, object] = {}

        def fake_server(**kwargs: object) -> int:
            captured.update(kwargs)
            provider = kwargs["credential_provider"]
            self.assertTrue(callable(provider))
            self.assertEqual(provider(), "session-token-one")  # type: ignore[operator]
            self.assertEqual(provider(), "session-token-two")  # type: ignore[operator]
            return 0

        with mock.patch("hormuz.cli.run_mcp_server", side_effect=fake_server):
            with mock.patch(
                "hormuz.cli.session_access_token",
                side_effect=("session-token-one", "session-token-two"),
            ) as access:
                self.assertEqual(
                    main(
                        [
                            "mcp",
                            "--url",
                            "https://hormuz.example",
                            "--profile",
                            "engineering",
                        ]
                    ),
                    0,
                )
        self.assertIsNone(captured["credential_env"])
        self.assertEqual(access.call_count, 2)
        access.assert_called_with(
            gateway="https://hormuz.example",
            profile="engineering",
            allow_insecure_http=False,
        )

    def test_profile_and_environment_modes_are_mutually_exclusive(self) -> None:
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                main(
                    [
                        "mcp",
                        "--url",
                        "https://hormuz.example",
                        "--profile",
                        "engineering",
                        "--credential-env",
                        "COMPANY_TOKEN",
                    ]
                )
        self.assertEqual(raised.exception.code, 2)

    def test_explicit_empty_environment_name_remains_invalid(self) -> None:
        with redirect_stderr(error := io.StringIO()):
            self.assertEqual(
                main(
                    [
                        "mcp-config",
                        "codex",
                        "--url",
                        "https://hormuz.example",
                        "--credential-env",
                        "",
                    ]
                ),
                2,
            )
        self.assertIn("MCP configuration error", error.getvalue())

    def test_profile_config_requires_https_unless_loopback_is_explicitly_allowed(self) -> None:
        with redirect_stderr(error := io.StringIO()):
            self.assertEqual(
                main(
                    [
                        "mcp-config",
                        "codex",
                        "--url",
                        "http://127.0.0.1:8787",
                        "--profile",
                        "engineering",
                    ]
                ),
                2,
            )
        self.assertIn("MCP configuration error", error.getvalue())

        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(
                main(
                    [
                        "mcp-config",
                        "codex",
                        "--url",
                        "http://127.0.0.1:8787",
                        "--profile",
                        "engineering",
                        "--allow-insecure-http",
                    ]
                ),
                0,
            )
        self.assertIn('"--allow-insecure-http"', output.getvalue())

    def test_actual_module_entrypoint_completes_a_legacy_handshake(self) -> None:
        messages = "\n".join(
            json.dumps(message)
            for message in (
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18"},
                },
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            )
        ) + "\n"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "hormuz",
                "mcp",
                "--url",
                "http://127.0.0.1:9",
            ],
            cwd=ROOT,
            input=messages,
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        responses = {value["id"]: value for value in map(json.loads, result.stdout.splitlines())}
        self.assertEqual(responses[1]["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(responses[2]["result"]["tools"][0]["name"], TOOL_NAME)
        self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
