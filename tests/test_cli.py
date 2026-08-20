from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import sqlite3
import signal
import tempfile
import tomllib
import unittest
from unittest import mock
from contextlib import redirect_stdout
from contextlib import redirect_stderr
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from hormuz.cli import (
    _audit_export,
    _audit_verify,
    _audit_since,
    _auth_token,
    _budget_for_scope,
    _client_config,
    _context_audit_export,
    _context_audit_since,
    _context_delete,
    _context_evidence_import,
    _context_export,
    _context_import,
    _context_list,
    _context_pack,
    _context_snapshot_import,
    _context_snapshot_show,
    _context_revalidate,
    _doctor,
    _lifecycle_remote_command,
    _policy_check,
    _status,
    _serve,
    build_parser,
    main,
)
from hormuz.audit_chain import (
    AUDIT_CHAIN_GENESIS_SHA256,
    AUDIT_CHAIN_SCHEMA_VERSION,
    AuditChainError,
)
from hormuz.config import (
    ConfigError,
    ContextInjectionPolicy,
    GatewayConfig,
    Identity,
    OIDCLoginConfig,
    OIDCIssuerConfig,
    SessionBrokerConfig,
)
from hormuz.context import ContextError
from hormuz.context_store import SQLiteContextRepository
from hormuz.store import UsageStore


ROOT = Path(__file__).resolve().parents[1]


def envelope_hash(envelope: dict[str, object]) -> str:
    snapshot = envelope["snapshot"]
    canonical = json.dumps(
        snapshot,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ClientConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = GatewayConfig.load(
            ROOT / "config.example.json",
            environ={"HORMUZ_TOKEN": "test-identity-token"},
        )

    def test_codex_configuration_uses_first_policy_allowed_openai_model(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            result = _client_config(self.config, "codex", "https://hormuz.example")

        self.assertEqual(result, 0)
        self.assertIn('model = "gpt-5.4-mini"', output.getvalue())
        self.assertIn('base_url = "https://hormuz.example/v1"', output.getvalue())
        self.assertIn('env_key = "HORMUZ_TOKEN"', output.getvalue())

    def test_sigterm_uses_nonblocking_idempotent_shutdown_request(self) -> None:
        callbacks: dict[int, object] = {}

        class FakeServer:
            request_shutdown_calls = 0
            closed = False
            draining = False

            def request_shutdown(self) -> bool:
                self.request_shutdown_calls += 1
                return self.request_shutdown_calls == 1

            def serve_forever(self) -> None:
                callback = callbacks[signal.SIGTERM]
                callback(signal.SIGTERM, None)  # type: ignore[operator]
                callback(signal.SIGTERM, None)  # type: ignore[operator]

            def begin_draining(self) -> bool:
                was_draining = self.draining
                self.draining = True
                return not was_draining

            def wait_for_in_flight(self, timeout_seconds: float) -> int:
                self.wait_timeout = timeout_seconds
                return 0

            def server_close(self) -> None:
                self.closed = True

        server = FakeServer()

        def capture_signal(number: int, callback: object) -> None:
            callbacks[number] = callback

        with (
            mock.patch("hormuz.cli.GatewayServer", return_value=server),
            mock.patch("hormuz.cli.signal.signal", side_effect=capture_signal),
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(_serve(self.config), 0)

        self.assertEqual(server.request_shutdown_calls, 2)
        self.assertTrue(server.draining)
        self.assertEqual(server.wait_timeout, 30)
        self.assertTrue(server.closed)

    def test_shutdown_grace_expiry_is_a_failed_exit(self) -> None:
        server = mock.Mock()
        server.wait_for_in_flight.return_value = 2

        with (
            mock.patch("hormuz.cli.GatewayServer", return_value=server),
            mock.patch("hormuz.cli.signal.signal"),
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
            self.assertLogs("hormuz", level="WARNING") as captured,
        ):
            self.assertEqual(_serve(self.config), 1)

        server.begin_draining.assert_called_once_with()
        server.wait_for_in_flight.assert_called_once_with(30)
        server.server_close.assert_called_once_with()
        self.assertIn("shutdown_grace_expired in_flight=2", captured.output[0])

    def test_shutdown_grace_configuration_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "hormuz.json"
            raw = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
            raw["listen"]["shutdown_grace_seconds"] = 45
            path.write_text(json.dumps(raw), encoding="utf-8")
            config = GatewayConfig.load(
                path,
                environ={"HORMUZ_TOKEN": "test-identity-token"},
            )
            self.assertEqual(config.listen.shutdown_grace_seconds, 45)

            for invalid in (0, 301):
                raw["listen"]["shutdown_grace_seconds"] = invalid
                path.write_text(json.dumps(raw), encoding="utf-8")
                with self.subTest(invalid=invalid), self.assertRaisesRegex(
                    ConfigError,
                    "listen.shutdown_grace_seconds",
                ):
                    GatewayConfig.load(
                        path,
                        environ={"HORMUZ_TOKEN": "test-identity-token"},
                    )

    def test_concurrent_request_configuration_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "hormuz.json"
            raw = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
            raw["listen"].pop("max_concurrent_requests")
            path.write_text(json.dumps(raw), encoding="utf-8")
            defaulted = GatewayConfig.load(
                path,
                environ={"HORMUZ_TOKEN": "test-identity-token"},
            )
            self.assertEqual(defaulted.listen.max_concurrent_requests, 128)

            raw["listen"]["max_concurrent_requests"] = 64
            path.write_text(json.dumps(raw), encoding="utf-8")
            config = GatewayConfig.load(
                path,
                environ={"HORMUZ_TOKEN": "test-identity-token"},
            )
            self.assertEqual(config.listen.max_concurrent_requests, 64)

            for invalid in (0, 10_001):
                raw["listen"]["max_concurrent_requests"] = invalid
                path.write_text(json.dumps(raw), encoding="utf-8")
                with self.subTest(invalid=invalid), self.assertRaisesRegex(
                    ConfigError,
                    "listen.max_concurrent_requests",
                ):
                    GatewayConfig.load(
                        path,
                        environ={"HORMUZ_TOKEN": "test-identity-token"},
                    )

    def test_connection_resource_configuration_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "hormuz.json"
            raw = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
            raw["listen"].pop("accept_backlog", None)
            raw["listen"].pop("max_connections")
            raw["listen"].pop("request_header_timeout_seconds")
            raw["listen"].pop("request_body_timeout_seconds", None)
            path.write_text(json.dumps(raw), encoding="utf-8")
            defaulted = GatewayConfig.load(
                path,
                environ={"HORMUZ_TOKEN": "test-identity-token"},
            )
            self.assertEqual(defaulted.listen.accept_backlog, 256)
            self.assertEqual(defaulted.listen.max_connections, 256)
            self.assertEqual(defaulted.listen.request_header_timeout_seconds, 15)
            self.assertEqual(defaulted.listen.request_body_timeout_seconds, 30)

            raw["listen"]["accept_backlog"] = 32
            raw["listen"]["max_connections"] = 64
            raw["listen"]["request_header_timeout_seconds"] = 20
            raw["listen"]["request_body_timeout_seconds"] = 45
            path.write_text(json.dumps(raw), encoding="utf-8")
            config = GatewayConfig.load(
                path,
                environ={"HORMUZ_TOKEN": "test-identity-token"},
            )
            self.assertEqual(config.listen.accept_backlog, 32)
            self.assertEqual(config.listen.max_connections, 64)
            self.assertEqual(config.listen.request_header_timeout_seconds, 20)
            self.assertEqual(config.listen.request_body_timeout_seconds, 45)

            for field, invalid_values in (
                ("accept_backlog", (0, 65_536)),
                ("max_connections", (0, 10_001)),
                ("request_header_timeout_seconds", (0, 121)),
                ("request_body_timeout_seconds", (0, 601)),
            ):
                for invalid in invalid_values:
                    raw["listen"][field] = invalid
                    path.write_text(json.dumps(raw), encoding="utf-8")
                    with self.subTest(field=field, invalid=invalid), self.assertRaisesRegex(
                        ConfigError,
                        f"listen.{field}",
                    ):
                        GatewayConfig.load(
                            path,
                            environ={"HORMUZ_TOKEN": "test-identity-token"},
                        )
                raw["listen"][field] = {
                    "accept_backlog": 32,
                    "max_connections": 64,
                    "request_header_timeout_seconds": 20,
                    "request_body_timeout_seconds": 45,
                }[field]

    def test_unknown_configuration_fields_fail_closed(self) -> None:
        def add_root_field(raw: dict[str, object]) -> None:
            raw["lissten"] = {}

        def add_listen_field(raw: dict[str, object]) -> None:
            raw["listen"]["max_conections"] = 64  # type: ignore[index]

        def add_upstreams_field(raw: dict[str, object]) -> None:
            raw["upstreams"]["other"] = {}  # type: ignore[index]

        def add_upstream_field(raw: dict[str, object]) -> None:
            raw["upstreams"]["openai"]["api_kee_env"] = "OTHER"  # type: ignore[index]

        def add_identity_field(raw: dict[str, object]) -> None:
            raw["identities"][0]["actor"] = "alice"  # type: ignore[index]

        def add_authentication_field(raw: dict[str, object]) -> None:
            raw["authentication"]["oidcc"] = {}  # type: ignore[index]

        def add_oidc_field(raw: dict[str, object]) -> None:
            raw["authentication"]["oidc"]["providers"] = []  # type: ignore[index]

        def add_model_route_field(raw: dict[str, object]) -> None:
            raw["model_routes"]["gpt-5.4-mini"][  # type: ignore[index]
                "output_cost_per_token"
            ] = 1

        def add_policies_field(raw: dict[str, object]) -> None:
            raw["policies"]["departments"] = {}  # type: ignore[index]

        def add_policy_field(raw: dict[str, object]) -> None:
            raw["policies"]["organization"]["monthly_buget_usd"] = 1  # type: ignore[index]

        cases = (
            ("root", add_root_field, "gateway configuration"),
            ("listen", add_listen_field, "listen"),
            ("upstreams", add_upstreams_field, "upstreams"),
            ("upstream", add_upstream_field, "upstreams.openai"),
            ("identity", add_identity_field, "identities[0]"),
            ("authentication", add_authentication_field, "authentication"),
            ("oidc", add_oidc_field, "authentication.oidc"),
            ("model route", add_model_route_field, "model_routes.gpt-5.4-mini"),
            ("policies", add_policies_field, "policies"),
            ("policy", add_policy_field, "policies.organization"),
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "hormuz.json"
            for name, mutate, expected_path in cases:
                with self.subTest(name=name):
                    raw = json.loads(
                        (ROOT / "config.example.json").read_text(encoding="utf-8")
                    )
                    mutate(raw)
                    path.write_text(json.dumps(raw), encoding="utf-8")
                    with self.assertRaises(ConfigError) as captured:
                        GatewayConfig.load(
                            path,
                            environ={"HORMUZ_TOKEN": "test-identity-token"},
                        )
                    self.assertIn(
                        f"Unknown {expected_path} fields",
                        str(captured.exception),
                    )

    def test_unknown_configuration_field_is_not_reflected_by_cli(self) -> None:
        sentinel = "company_secret_do_not_log_49f168ce"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "hormuz.json"
            raw = json.loads(
                (ROOT / "config.example.json").read_text(encoding="utf-8")
            )
            raw[sentinel] = True
            path.write_text(json.dumps(raw), encoding="utf-8")
            error = io.StringIO()
            with (
                mock.patch.dict(
                    os.environ,
                    {"HORMUZ_TOKEN": "test-identity-token"},
                    clear=True,
                ),
                redirect_stderr(error),
            ):
                self.assertEqual(main(["--config", str(path), "doctor"]), 2)
            self.assertIn("Unknown gateway configuration fields", error.getvalue())
            self.assertNotIn(sentinel, error.getvalue())

    def test_configuration_json_rejects_duplicate_members_without_reflection(self) -> None:
        sentinel = "company_secret_duplicate_key_3f2b91"
        cases = (
            '{"listen":{},"listen":{"port":8788}}',
            '{"listen":{},"\\u006cisten":{"port":8788}}',
            '{"listen":{"port":8787,"port":8788}}',
            '{"policies":{"organization":{},"organization":{"max_output_tokens":1}}}',
            '{"listen":{},"' + sentinel + '":1,"' + sentinel + '":2}',
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "hormuz.json"
            for raw in cases:
                with self.subTest(raw=raw[:48]):
                    path.write_text(raw, encoding="utf-8")
                    with self.assertRaisesRegex(
                        ConfigError,
                        "Configuration JSON contains duplicate object members",
                    ) as captured:
                        GatewayConfig.load(
                            path,
                            environ={"HORMUZ_TOKEN": "test-identity-token"},
                        )
                    self.assertNotIn(sentinel, str(captured.exception))

    def test_configuration_json_rejects_nonstandard_numbers_and_invalid_utf8(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "hormuz.json"
            for constant in ("NaN", "Infinity", "-Infinity"):
                with self.subTest(constant=constant):
                    path.write_text(
                        '{"max_request_bytes":' + constant + "}",
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(
                        ConfigError,
                        "Configuration JSON contains a non-standard number",
                    ):
                        GatewayConfig.load(
                            path,
                            environ={"HORMUZ_TOKEN": "test-identity-token"},
                        )

            path.write_bytes(b'{"listen":"\xff"}')
            with self.assertRaisesRegex(
                ConfigError,
                "Configuration file must be valid UTF-8 JSON",
            ):
                GatewayConfig.load(
                    path,
                    environ={"HORMUZ_TOKEN": "test-identity-token"},
                )

            baseline = (ROOT / "config.example.json").read_text(encoding="utf-8")
            path.write_text(
                baseline.replace(
                    '"input_cost_per_million": 0.75',
                    '"input_cost_per_million": 1e9999',
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ConfigError, "must be a non-negative finite number"):
                GatewayConfig.load(
                    path,
                    environ={"HORMUZ_TOKEN": "test-identity-token"},
                )

    def test_configuration_json_complexity_failures_have_fixed_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "hormuz.json"
            cases = (
                (
                    '{"max_request_bytes":' + ("9" * 5_000) + "}",
                    "Configuration file must be valid UTF-8 JSON",
                ),
                (
                    '{"listen":' + ("[" * 2_000) + "0" + ("]" * 2_000) + "}",
                    "Configuration JSON exceeds structural limits",
                ),
                (
                    '{"listen":[' + ("0," * 100_000) + "0]}",
                    "Configuration JSON exceeds structural limits",
                ),
            )
            for raw, expected in cases:
                with self.subTest(length=len(raw)):
                    path.write_text(raw, encoding="utf-8")
                    with self.assertRaisesRegex(
                        ConfigError,
                        expected,
                    ):
                        GatewayConfig.load(
                            path,
                            environ={"HORMUZ_TOKEN": "test-identity-token"},
                        )

    def test_configuration_decoder_recursion_has_fixed_structural_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "hormuz.json"
            path.write_text("{}", encoding="utf-8")
            with mock.patch("hormuz.config.json.loads", side_effect=RecursionError):
                with self.assertRaisesRegex(
                    ConfigError,
                    "Configuration JSON exceeds structural limits",
                ):
                    GatewayConfig.load(
                        path,
                        environ={"HORMUZ_TOKEN": "test-identity-token"},
                    )

    def test_configuration_file_size_is_bounded_before_json_decode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "hormuz.json"
            path.write_bytes(b'{"padding":"' + (b"x" * 1_048_576) + b'"}')
            with self.assertRaisesRegex(
                ConfigError,
                "Configuration file exceeds 1048576 bytes",
            ):
                GatewayConfig.load(
                    path,
                    environ={"HORMUZ_TOKEN": "test-identity-token"},
                )

    def test_exact_configuration_digest_is_enforced_and_reported(self) -> None:
        expected = hashlib.sha256(
            (ROOT / "config.example.json").read_bytes()
        ).hexdigest()
        config = GatewayConfig.load(
            ROOT / "config.example.json",
            environ={"HORMUZ_TOKEN": "test-identity-token"},
            expected_sha256=expected,
        )
        self.assertEqual(config.source_sha256, expected)

        with self.assertRaisesRegex(ConfigError, "Configuration digest mismatch"):
            GatewayConfig.load(
                ROOT / "config.example.json",
                environ={"HORMUZ_TOKEN": "test-identity-token"},
                expected_sha256="0" * 64,
            )
        with tempfile.TemporaryDirectory() as temporary:
            changed_path = Path(temporary) / "hormuz.json"
            changed_path.write_bytes(
                (ROOT / "config.example.json").read_bytes() + b"\n"
            )
            with self.assertRaisesRegex(ConfigError, "Configuration digest mismatch"):
                GatewayConfig.load(
                    changed_path,
                    environ={"HORMUZ_TOKEN": "test-identity-token"},
                    expected_sha256=expected,
                )
        for invalid in ("", "A" * 64, "g" * 64, "0" * 63, "0" * 65):
            with self.subTest(invalid=invalid[:16]), self.assertRaisesRegex(
                ConfigError,
                "Expected configuration SHA-256 must be 64 lowercase hexadecimal characters",
            ):
                GatewayConfig.load(
                    ROOT / "config.example.json",
                    environ={"HORMUZ_TOKEN": "test-identity-token"},
                    expected_sha256=invalid,
                )

    def test_cli_enforces_environment_or_argument_configuration_digest(self) -> None:
        expected = hashlib.sha256(
            (ROOT / "config.example.json").read_bytes()
        ).hexdigest()
        environment = {
            "HORMUZ_TOKEN": "test-identity-token",
            "OPENAI_API_KEY": "synthetic-openai-provider-key",
            "ANTHROPIC_API_KEY": "synthetic-anthropic-provider-key",
        }
        with (
            mock.patch.dict(
                os.environ,
                {**environment, "HORMUZ_CONFIG_SHA256": "0" * 64},
                clear=True,
            ),
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()) as error,
        ):
            self.assertEqual(
                main(["--config", str(ROOT / "config.example.json"), "doctor"]),
                2,
            )
        self.assertIn("Configuration digest mismatch", error.getvalue())

        with (
            mock.patch.dict(
                os.environ,
                {**environment, "HORMUZ_CONFIG_SHA256": "0" * 64},
                clear=True,
            ),
            redirect_stdout(io.StringIO()) as output,
            redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(
                main(
                    [
                        "--config",
                        str(ROOT / "config.example.json"),
                        "--expected-config-sha256",
                        expected,
                        "doctor",
                    ]
                ),
                0,
            )
        self.assertIn(f"configuration SHA-256: {expected}", output.getvalue())

    def test_policy_scope_references_fail_closed_without_reflection(self) -> None:
        sentinel = "company_secret_scope_467c8f17"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "hormuz.json"
            baseline = json.loads(
                (ROOT / "config.example.json").read_text(encoding="utf-8")
            )
            cases = ("team", "actor", "ambiguous_team")
            for case in cases:
                with self.subTest(case=case):
                    raw = json.loads(json.dumps(baseline))
                    environment = {"HORMUZ_TOKEN": "test-identity-token"}
                    if case == "team":
                        raw["policies"]["teams"][sentinel] = {
                            "max_output_tokens": 10
                        }
                        expected = "Policies reference unknown teams"
                    elif case == "actor":
                        raw["policies"]["actors"][sentinel] = {
                            "max_output_tokens": 10
                        }
                        expected = "Policies reference unknown actors"
                    else:
                        second_identity = json.loads(json.dumps(raw["identities"][0]))
                        second_identity.update(
                            {
                                "token_env": "HORMUZ_SECOND_TOKEN",
                                "actor_id": "bob",
                                "actor_name": "Bob Example",
                                "organization_id": "second-organization",
                            }
                        )
                        raw["identities"].append(second_identity)
                        environment["HORMUZ_SECOND_TOKEN"] = "second-identity-token"
                        expected = (
                            "Policy team IDs must identify exactly one organization"
                        )
                    path.write_text(json.dumps(raw), encoding="utf-8")
                    with self.assertRaises(ConfigError) as captured:
                        GatewayConfig.load(path, environ=environment)
                    self.assertIn(expected, str(captured.exception))
                    self.assertNotIn(sentinel, str(captured.exception))

    def test_doctor_reports_effective_request_resource_limits(self) -> None:
        output = io.StringIO()
        with (
            mock.patch.dict(
                os.environ,
                {
                    "OPENAI_API_KEY": "synthetic-openai-provider-key",
                    "ANTHROPIC_API_KEY": "synthetic-anthropic-provider-key",
                },
            ),
            redirect_stdout(output),
        ):
            result = _doctor(self.config)

        self.assertEqual(result, 0)
        self.assertIn("max concurrent requests: 128", output.getvalue())
        self.assertIn("accept backlog: 256", output.getvalue())
        self.assertIn("max concurrent connections: 256", output.getvalue())
        self.assertIn("request-header deadline: 15 seconds", output.getvalue())
        self.assertIn("request-body deadline: 30 seconds", output.getvalue())
        self.assertIn("upstream response deadline: 600 seconds", output.getvalue())

    def test_provider_upstreams_require_https_outside_loopback(self) -> None:
        invalid_urls = (
            "http://api.openai.com/v1",
            "http://192.0.2.10/v1",
            "https://user:password@api.openai.com/v1",
            "https://api.openai.com/v1?transport=unsafe",
            "https://api.openai.com/v1#unsafe",
        )
        for protocol in ("openai", "anthropic"):
            for invalid_url in invalid_urls:
                with (
                    self.subTest(protocol=protocol, invalid_url=invalid_url),
                    tempfile.TemporaryDirectory() as temporary,
                ):
                    raw = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
                    raw["upstreams"][protocol]["base_url"] = invalid_url
                    path = Path(temporary) / "hormuz.json"
                    path.write_text(json.dumps(raw), encoding="utf-8")
                    with self.assertRaisesRegex(ConfigError, f"upstreams.{protocol}.base_url"):
                        GatewayConfig.load(
                            path,
                            environ={"HORMUZ_TOKEN": "test-identity-token"},
                        )

        for protocol in ("openai", "anthropic"):
            for loopback_url in (
                "http://127.0.0.1:8000/v1",
                "http://127.0.0.2:8000/v1",
                "http://[::1]:8000/v1",
                "http://localhost:8000/v1",
            ):
                with (
                    self.subTest(protocol=protocol, loopback_url=loopback_url),
                    tempfile.TemporaryDirectory() as temporary,
                ):
                    raw = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
                    raw["upstreams"][protocol]["base_url"] = loopback_url
                    path = Path(temporary) / "hormuz.json"
                    path.write_text(json.dumps(raw), encoding="utf-8")
                    config = GatewayConfig.load(
                        path,
                        environ={"HORMUZ_TOKEN": "test-identity-token"},
                    )
                    self.assertEqual(config.upstreams[protocol].base_url, loopback_url)

    def test_claude_configuration_uses_gateway_bearer_token(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            result = _client_config(self.config, "claude", "https://hormuz.example")

        self.assertEqual(result, 0)
        self.assertIn("ANTHROPIC_BASE_URL=https://hormuz.example", output.getvalue())
        self.assertIn('ANTHROPIC_AUTH_TOKEN="${HORMUZ_TOKEN}"', output.getvalue())
        self.assertIn(
            "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1",
            output.getvalue(),
        )

    def test_client_configuration_emits_exact_granted_repository_scope_headers(self) -> None:
        configured = replace(
            self.config,
            organization_policy=replace(
                self.config.organization_policy,
                context_injection=ContextInjectionPolicy(
                    mode="optional",
                    allowed_repositories=("Xpounder-com/hormuz",),
                ),
            ),
        )

        codex = io.StringIO()
        with redirect_stdout(codex):
            self.assertEqual(
                _client_config(
                    configured,
                    "codex",
                    "https://hormuz.example",
                    repository="Xpounder-com/hormuz",
                    branch="main",
                    revision="abc123",
                ),
                0,
            )
        parsed = tomllib.loads(codex.getvalue())
        self.assertEqual(
            parsed["model_providers"]["hormuz"]["http_headers"],
            {
                "X-Hormuz-Repository": "Xpounder-com/hormuz",
                "X-Hormuz-Branch": "main",
                "X-Hormuz-Revision": "abc123",
            },
        )

        claude = io.StringIO()
        with redirect_stdout(claude):
            self.assertEqual(
                _client_config(
                    configured,
                    "claude",
                    "https://hormuz.example",
                    repository="Xpounder-com/hormuz",
                    branch="main",
                    revision="abc123",
                ),
                0,
            )
        self.assertIn("ANTHROPIC_CUSTOM_HEADERS", claude.getvalue())
        self.assertIn("X-Hormuz-Repository: Xpounder-com/hormuz", claude.getvalue())
        self.assertIn("X-Hormuz-Branch: main", claude.getvalue())
        self.assertIn("X-Hormuz-Revision: abc123", claude.getvalue())

        with self.assertRaisesRegex(ConfigError, "not granted"):
            _client_config(
                configured,
                "codex",
                "https://hormuz.example",
                repository="other/private",
            )

    def test_oidc_client_config_uses_credential_helpers_for_both_clients(self) -> None:
        issuer = "https://identity.example.com"
        identity = Identity(
            token_env="",
            token="",
            actor_id="alice",
            actor_name="Alice Example",
            team_id="engineering",
            team_name="Engineering",
            allowed_clients=("codex", "claude-code"),
            organization_id="xpounder",
            clearance="confidential",
            authentication_source=f"oidc:{issuer}",
        )
        config = replace(
            self.config,
            organization_policy=replace(
                self.config.organization_policy,
                context_injection=ContextInjectionPolicy(
                    mode="optional",
                    allowed_repositories=("Xpounder-com/hormuz",),
                ),
            ),
            oidc_issuers={issuer: OIDCIssuerConfig(issuer=issuer, audiences=("hormuz-api",))},
            identities_by_subject={(issuer, "subject-alice"): identity},
        )

        codex = io.StringIO()
        with redirect_stdout(codex):
            result = _client_config(
                config,
                "codex",
                "https://hormuz.example",
                actor_id="alice",
                auth_mode="oidc",
                repository="Xpounder-com/hormuz",
                branch="main",
            )
        self.assertEqual(result, 0)
        self.assertIn("[model_providers.hormuz.auth]", codex.getvalue())
        self.assertIn('command = "hormuz"', codex.getvalue())
        self.assertIn('args = ["auth", "token", "--env", "HORMUZ_OIDC_ACCESS_TOKEN"]', codex.getvalue())
        self.assertNotIn("env_key", codex.getvalue())
        parsed_codex = tomllib.loads(codex.getvalue())
        self.assertEqual(
            parsed_codex["model_providers"]["hormuz"]["auth"]["command"],
            "hormuz",
        )
        self.assertEqual(
            parsed_codex["model_providers"]["hormuz"]["http_headers"],
            {
                "X-Hormuz-Repository": "Xpounder-com/hormuz",
                "X-Hormuz-Branch": "main",
            },
        )

        claude = io.StringIO()
        with redirect_stdout(claude):
            result = _client_config(
                config,
                "claude",
                "https://hormuz.example",
                actor_id="alice",
                auth_mode="oidc",
                credential_env="COMPANY_OIDC_TOKEN",
                repository="Xpounder-com/hormuz",
                branch="main",
            )
        self.assertEqual(result, 0)
        self.assertIn('"ANTHROPIC_BASE_URL": "https://hormuz.example"', claude.getvalue())
        self.assertIn('"CLAUDE_CODE_API_KEY_HELPER_TTL_MS": "300000"', claude.getvalue())
        self.assertIn(
            '"CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY": "1"',
            claude.getvalue(),
        )
        self.assertIn("hormuz auth token --env COMPANY_OIDC_TOKEN", claude.getvalue())
        self.assertIn("X-Hormuz-Repository: Xpounder-com/hormuz", claude.getvalue())
        self.assertIn("X-Hormuz-Branch: main", claude.getvalue())
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", claude.getvalue())

    def test_session_client_config_uses_secure_store_helper_for_both_clients(self) -> None:
        issuer = "https://identity.example.com"
        identity = Identity(
            token_env="",
            token="",
            actor_id="alice",
            actor_name="Alice Example",
            team_id="engineering",
            team_name="Engineering",
            allowed_clients=("codex", "claude-code"),
            organization_id="xpounder",
            clearance="confidential",
            authentication_source=f"oidc:{issuer}",
        )
        config = replace(
            self.config,
            organization_policy=replace(
                self.config.organization_policy,
                context_injection=ContextInjectionPolicy(
                    mode="optional",
                    allowed_repositories=("Xpounder-com/hormuz",),
                ),
            ),
            oidc_issuers={
                issuer: OIDCIssuerConfig(
                    issuer=issuer,
                    audiences=("hormuz-api",),
                    login=OIDCLoginConfig(
                        client_id="hormuz-login",
                        client_secret_env="OIDC_SECRET",
                        client_secret="must-never-appear-client-secret",
                    ),
                )
            },
            identities_by_subject={(issuer, "subject-alice"): identity},
            session_broker=SessionBrokerConfig(
                enabled=True,
                database_path=Path("/tmp/hormuz-session-test.sqlite3"),
                public_base_url="https://hormuz.example",
                master_key_env="SESSION_KEY",
                master_key=b"m" * 32,
            ),
        )

        codex = io.StringIO()
        with redirect_stdout(codex):
            self.assertEqual(
                _client_config(
                    config,
                    "codex",
                    "https://hormuz.example",
                    actor_id="alice",
                    auth_mode="session",
                    profile="engineering-codex",
                    repository="Xpounder-com/hormuz",
                ),
                0,
            )
        parsed = tomllib.loads(codex.getvalue())
        self.assertEqual(
            parsed["model_providers"]["hormuz"]["auth"]["args"],
            [
                "auth",
                "token",
                "--gateway",
                "https://hormuz.example",
                "--profile",
                "engineering-codex",
            ],
        )
        self.assertEqual(
            parsed["model_providers"]["hormuz"]["http_headers"],
            {"X-Hormuz-Repository": "Xpounder-com/hormuz"},
        )

        claude = io.StringIO()
        with redirect_stdout(claude):
            self.assertEqual(
                _client_config(
                    config,
                    "claude",
                    "https://hormuz.example",
                    actor_id="alice",
                    auth_mode="session",
                    profile="engineering-claude",
                    repository="Xpounder-com/hormuz",
                ),
                0,
            )
        self.assertIn(
            "hormuz auth token --gateway-env HORMUZ_SESSION_GATEWAY --profile engineering-claude",
            claude.getvalue(),
        )
        self.assertIn("X-Hormuz-Repository: Xpounder-com/hormuz", claude.getvalue())
        self.assertIn('"HORMUZ_SESSION_GATEWAY": "https://hormuz.example"', claude.getvalue())
        self.assertIn(
            '"CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY": "1"',
            claude.getvalue(),
        )
        self.assertNotIn("must-never-appear", codex.getvalue() + claude.getvalue())

    def test_remote_lifecycle_cli_dispatches_all_connector_operations_without_local_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence_path = root / "evidence.json"
            evidence_path.write_text(
                json.dumps(
                    {
                        "schema_version": "hormuz.context-evidence.v1",
                        "organization_id": "xpounder",
                        "record_id": "retry",
                        "record_version": 1,
                        "signal": "ci_passed",
                        "evidence_ref": "ci:private:123",
                        "observed_at": "2026-08-16T12:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
            snapshot_path = root / "snapshot.json"
            snapshot_path.write_text(
                json.dumps(
                    {
                        "schema_version": "hormuz.context-lifecycle-envelope.v1",
                        "organization_id": "xpounder",
                        "repository_id": "acme/api",
                        "branch": "main",
                        "snapshot": {
                            "schema_version": "hormuz.context-lifecycle-snapshot.v1",
                            "repository_revision": "abc123",
                            "artifacts": [],
                        },
                    }
                ),
                encoding="utf-8",
            )
            commands = [
                (
                    [
                        "lifecycle",
                        "evidence",
                        "--input",
                        str(evidence_path),
                        "--gateway",
                        "https://hormuz.example",
                    ],
                    "record_evidence",
                ),
                (
                    [
                        "lifecycle",
                        "snapshot",
                        "--input",
                        str(snapshot_path),
                        "--expected-version",
                        "4",
                        "--gateway",
                        "https://hormuz.example",
                    ],
                    "put_snapshot",
                ),
                (
                    [
                        "lifecycle",
                        "revalidate",
                        "--repository",
                        "acme/api",
                        "--branch",
                        "main",
                        "--batch-size",
                        "25",
                        "--gateway",
                        "https://hormuz.example",
                    ],
                    "revalidate",
                ),
            ]
            with mock.patch.dict(os.environ, {"HORMUZ_TOKEN": "connector-secret"}):
                for argv, method_name in commands:
                    with self.subTest(command=method_name):
                        args = build_parser().parse_args(argv)
                        with mock.patch("hormuz.cli.ContextLifecycleClient") as client_type:
                            method = getattr(client_type.return_value, method_name)
                            method.return_value = {
                                "schema_version": "test-result.v1",
                                "status": "ok",
                            }
                            with redirect_stdout(output := io.StringIO()):
                                self.assertEqual(_lifecycle_remote_command(args), 0)
                        client_type.assert_called_once_with(
                            "https://hormuz.example",
                            credential="connector-secret",
                            allow_insecure_http=False,
                            timeout_seconds=30,
                        )
                        self.assertEqual(json.loads(output.getvalue())["status"], "ok")
                        if method_name == "put_snapshot":
                            self.assertEqual(method.call_args.kwargs["expected_version"], 4)
                        if method_name == "revalidate":
                            self.assertEqual(
                                method.call_args.kwargs,
                                {
                                    "repository_id": "acme/api",
                                    "branch": "main",
                                    "batch_size": 25,
                                },
                            )

    def test_remote_lifecycle_cli_rejects_missing_credential_and_duplicate_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "evidence.json"
            path.write_text(
                '{"schema_version":"one","schema_version":"two"}',
                encoding="utf-8",
            )
            args = build_parser().parse_args(
                [
                    "lifecycle",
                    "evidence",
                    "--input",
                    str(path),
                    "--gateway",
                    "https://hormuz.example",
                ]
            )
            with mock.patch.dict(os.environ, {}, clear=True), redirect_stderr(error := io.StringIO()):
                self.assertEqual(_lifecycle_remote_command(args), 1)
            self.assertIn("credential environment variable is not set", error.getvalue())
            with mock.patch.dict(os.environ, {"HORMUZ_TOKEN": "connector-secret"}):
                with redirect_stderr(error := io.StringIO()):
                    self.assertEqual(_lifecycle_remote_command(args), 2)
            self.assertIn("duplicate JSON object member", error.getvalue())

    def test_auth_token_prints_only_a_valid_environment_credential(self) -> None:
        output = io.StringIO()
        with mock.patch.dict(os.environ, {"COMPANY_TOKEN": "header.payload.signature"}):
            with redirect_stdout(output):
                self.assertEqual(_auth_token("COMPANY_TOKEN"), 0)
        self.assertEqual(output.getvalue(), "header.payload.signature\n")

        with mock.patch.dict(os.environ, {}, clear=True):
            with redirect_stderr(io.StringIO()):
                self.assertEqual(_auth_token("COMPANY_TOKEN"), 1)

        with mock.patch.dict(os.environ, {"HORMUZ_GATEWAY_URL": "https://hormuz.example"}):
            with mock.patch("hormuz.cli.session_access_token", return_value="hox_a_" + "a" * 43):
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(
                        _auth_token(None, gateway_env="HORMUZ_GATEWAY_URL"),
                        0,
                    )
                self.assertTrue(output.getvalue().startswith("hox_a_"))

    def test_client_config_rejects_configuration_injection_urls(self) -> None:
        with self.assertRaises(ConfigError):
            _client_config(self.config, "codex", 'https://hormuz.example/"\nmodel="attacker"')

    def test_status_accepts_dimension_and_scope_filters(self) -> None:
        args = build_parser().parse_args(
            ["status", "--group-by", "model", "--team", "engineering", "--actor", "alice", "--json", "--include-latency"]
        )

        self.assertEqual(args.group_by, "model")
        self.assertEqual(args.team, "engineering")
        self.assertEqual(args.actor, "alice")
        self.assertTrue(args.json)
        self.assertTrue(args.include_latency)

    def test_status_json_labels_versioned_estimates_and_unpriced_requests(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = replace(
                self.config,
                database_path=Path(temporary) / "usage.sqlite3",
            )
            store = UsageStore(config.database_path)
            store.record(
                identity=config.identities_by_actor["alice"],
                client="codex",
                protocol="openai",
                requested_model="gpt-5.4-mini",
                resolved_alias="gpt-5.4-mini",
                upstream_model="gpt-5.4-mini",
                actual_model="gpt-5.4-mini-2026-08-01",
                policy_action="allowed",
                status="succeeded",
                input_tokens=100,
                output_tokens=20,
                billable_tokens=120,
                cost_microusd=1_250,
                cost_basis="estimated",
                rate_card_version="rates-v1",
            )
            store.record(
                identity=config.identities_by_actor["alice"],
                client="codex",
                protocol="openai",
                requested_model="gpt-5.4-mini",
                resolved_alias="gpt-5.4-mini",
                upstream_model="gpt-5.4-mini",
                policy_action="allowed",
                status="failed",
                cost_basis="not_available",
                rate_card_version="rates-v1",
            )
            output = io.StringIO()
            with redirect_stdout(output):
                result = _status(
                    config,
                    argparse.Namespace(
                        group_by="person",
                        actor=None,
                        team=None,
                        json=True,
                    ),
                )

            self.assertEqual(result, 0)
            report = json.loads(output.getvalue())[0]
            self.assertEqual(report["billable_tokens"], 120)
            self.assertEqual(report["estimated_cost_microusd"], 1_250)
            self.assertEqual(report["estimated_cost_usd"], 0.00125)
            self.assertEqual(report["unpriced_requests"], 1)
            self.assertEqual(report["cost_bases"], ["estimated", "not_available"])
            self.assertEqual(report["rate_card_versions"], ["rates-v1"])

    def test_status_latency_view_is_opt_in_and_prints_p95_bucket_bounds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = replace(
                self.config,
                database_path=Path(temporary) / "usage.sqlite3",
            )
            UsageStore(config.database_path).record(
                identity=config.identities_by_actor["alice"],
                client="codex",
                protocol="openai",
                requested_model="gpt-5.4-mini",
                resolved_alias="gpt-5.4-mini",
                upstream_model="gpt-5.4-mini",
                policy_action="allowed",
                status="succeeded",
                gateway_latency_milliseconds=18,
                policy_latency_milliseconds=2,
                provider_latency_milliseconds=14,
            )
            common = {
                "group_by": "person",
                "actor": None,
                "team": None,
                "organization": None,
                "include_latency": True,
            }
            with redirect_stdout(json_output := io.StringIO()):
                self.assertEqual(
                    _status(config, argparse.Namespace(**common, json=True)),
                    0,
                )
            latency = json.loads(json_output.getvalue())[0]["latency"]
            self.assertEqual(latency["gateway"]["count"], 1)
            self.assertEqual(latency["provider"]["average_ms"], 14.0)

            with redirect_stdout(table_output := io.StringIO()):
                self.assertEqual(
                    _status(config, argparse.Namespace(**common, json=False)),
                    0,
                )
            lines = table_output.getvalue().splitlines()
            self.assertIn("GATEWAY_P95_BUCKET_MS", lines[0])
            self.assertTrue(lines[1].endswith("\t25\t5\t25\t-"))

    def test_billing_reconciliation_policy_is_strict_and_exact(self) -> None:
        policy = self.config.billing_reconciliation
        self.assertTrue(policy.enabled)
        self.assertEqual(policy.policy_version, "finance-review-v1")
        self.assertEqual(policy.to_dict()["max_absolute_variance_usd"], "25")
        self.assertEqual(policy.max_variance_basis_points, 500)
        self.assertTrue(policy.require_authenticated_source)
        self.assertRegex(policy.policy_sha256, r"^[0-9a-f]{64}$")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
            path = root / "hormuz.json"

            raw["billing_reconciliation"]["unknown"] = True
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(
                ConfigError,
                "Unknown billing_reconciliation fields",
            ):
                GatewayConfig.load(
                    path,
                    environ={"HORMUZ_TOKEN": "test-identity-token"},
                )

            raw["billing_reconciliation"].pop("unknown")
            raw["billing_reconciliation"]["max_absolute_variance_usd"] = 25.0
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "exact non-negative decimal string"):
                GatewayConfig.load(
                    path,
                    environ={"HORMUZ_TOKEN": "test-identity-token"},
                )

            raw["billing_reconciliation"] = {
                "enabled": True,
                "policy_version": "empty-v1",
            }
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "at least one rule"):
                GatewayConfig.load(
                    path,
                    environ={"HORMUZ_TOKEN": "test-identity-token"},
                )

            raw["billing_reconciliation"] = {
                "enabled": True,
                "max_unpriced_requests": 0,
            }
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "policy_version is required"):
                GatewayConfig.load(
                    path,
                    environ={"HORMUZ_TOKEN": "test-identity-token"},
                )

            raw.pop("billing_reconciliation")
            path.write_text(json.dumps(raw), encoding="utf-8")
            disabled = GatewayConfig.load(
                path,
                environ={"HORMUZ_TOKEN": "test-identity-token"},
            )
            self.assertFalse(disabled.billing_reconciliation.enabled)

    def test_context_database_is_separate_and_cannot_alias_usage(self) -> None:
        self.assertNotEqual(self.config.context_database_path, self.config.database_path)
        self.assertEqual(self.config.context_service.policy_version, "engineering-context-v1")
        self.assertEqual(self.config.context_service.max_token_budget, 32_768)
        self.assertEqual(self.config.context_service.max_items, 20)
        self.assertEqual(self.config.context_service.requests_per_minute, 60)
        self.assertFalse(self.config.context_service.allow_provisional)
        self.assertFalse(self.config.context_service.lifecycle.enabled)
        self.assertEqual(self.config.context_service.lifecycle.job_batch_size, 100)
        self.assertEqual(self.config.context_service.lifecycle.lease_seconds, 30)
        self.assertIsNotNone(self.config.context_service.lifecycle.policy)
        self.assertEqual(
            self.config.resolved_policy(
                self.config.identities_by_actor["alice"]
            ).context_injection.mode,
            "off",
        )
        self.assertEqual(
            self.config.resolved_policy(
                self.config.identities_by_actor["alice"]
            ).context_injection.allowed_repositories,
            (),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
            raw["database"] = "./shared.sqlite3"
            raw["context_database"] = "./shared.sqlite3"
            path = root / "hormuz.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "must be separate"):
                GatewayConfig.load(path, environ={"HORMUZ_TOKEN": "test-identity-token"})

            raw["context_database"] = "./context.sqlite3"
            raw["context_service"]["unknown_policy"] = True
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "Unknown context_service fields"):
                GatewayConfig.load(path, environ={"HORMUZ_TOKEN": "test-identity-token"})

    def test_context_injection_policy_is_strict_and_monotonic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
            raw["policies"]["organization"]["context_injection"] = {
                "mode": "optional",
                "allowed_clients": ["codex", "claude-code"],
                "allowed_models": ["gpt-5.4-mini", "claude-sonnet-5"],
                "allowed_repositories": ["Xpounder-com/hormuz", "Xpounder-com/web"],
                "max_classification": "confidential",
                "token_budget": 1000,
                "max_items": 5,
            }
            raw["policies"]["teams"]["engineering"]["context_injection"] = {
                "mode": "required",
                "allowed_clients": ["codex"],
                "allowed_models": ["gpt-5.4-mini"],
                "allowed_repositories": ["Xpounder-com/hormuz", "other/private"],
                "max_classification": "internal",
                "token_budget": 500,
                "max_items": 3,
            }
            raw["policies"]["actors"]["alice"] = {
                "context_injection": {"mode": "off", "token_budget": 750}
            }
            path = root / "hormuz.json"
            path.write_text(json.dumps(raw), encoding="utf-8")

            config = GatewayConfig.load(
                path,
                environ={"HORMUZ_TOKEN": "test-identity-token"},
            )
            effective = config.resolved_policy(
                config.identities_by_actor["alice"]
            ).context_injection
            self.assertEqual(effective.mode, "required")
            self.assertEqual(effective.allowed_clients, ("codex",))
            self.assertEqual(effective.allowed_models, ("gpt-5.4-mini",))
            self.assertEqual(effective.allowed_repositories, ("Xpounder-com/hormuz",))
            self.assertEqual(effective.max_classification, "internal")
            self.assertEqual(effective.token_budget, 500)
            self.assertEqual(effective.max_items, 3)

            raw["policies"]["actors"]["alice"]["context_injection"]["unknown"] = True
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "Unknown .*context_injection fields"):
                GatewayConfig.load(
                    path,
                    environ={"HORMUZ_TOKEN": "test-identity-token"},
                )

            raw["policies"]["actors"]["alice"]["context_injection"].pop("unknown")
            raw["policies"]["organization"]["context_injection"][
                "allowed_repositories"
            ] = ["unsafe repository"]
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "safe exact selectors"):
                GatewayConfig.load(
                    path,
                    environ={"HORMUZ_TOKEN": "test-identity-token"},
                )

            raw["policies"]["organization"]["context_injection"][
                "allowed_repositories"
            ] = ["Xpounder-com/hormuz"]
            raw["policies"]["organization"]["context_injection"][
                "max_classification"
            ] = "top-secret"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "max_classification must be"):
                GatewayConfig.load(
                    path,
                    environ={"HORMUZ_TOKEN": "test-identity-token"},
                )

    def test_model_usage_limits_are_strict_reference_checked_and_monotonic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
            raw["policies"]["organization"]["model_limits"] = {
                "gpt-5.5": {
                    "monthly_token_limit": 10_000,
                    "monthly_budget_usd": 100,
                    "per_actor_monthly_token_limit": 2_000,
                    "per_actor_monthly_budget_usd": 20,
                }
            }
            raw["policies"]["teams"]["engineering"]["model_limits"] = {
                "gpt-5.5": {
                    "monthly_token_limit": 8_000,
                    "monthly_budget_usd": 120,
                    "per_actor_monthly_token_limit": 1_500,
                }
            }
            raw["policies"]["actors"]["alice"] = {
                "model_limits": {
                    "gpt-5.5": {
                        "monthly_token_limit": 3_000,
                        "monthly_budget_usd": 30,
                        "per_actor_monthly_budget_usd": 10,
                    }
                }
            }
            path = root / "hormuz.json"
            path.write_text(json.dumps(raw), encoding="utf-8")

            config = GatewayConfig.load(
                path,
                environ={"HORMUZ_TOKEN": "test-identity-token"},
            )
            effective = config.resolved_policy(
                config.identities_by_actor["alice"]
            ).model_limits["gpt-5.5"]
            self.assertEqual(effective.monthly_token_limit, 3_000)
            self.assertEqual(effective.monthly_budget_usd, 30)
            self.assertEqual(effective.per_actor_monthly_token_limit, 1_500)
            self.assertEqual(effective.per_actor_monthly_budget_usd, 10)

            raw["policies"]["actors"]["alice"]["model_limits"]["gpt-5.5"] = {}
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "must configure at least one limit"):
                GatewayConfig.load(
                    path,
                    environ={"HORMUZ_TOKEN": "test-identity-token"},
                )

            raw["policies"]["actors"]["alice"]["model_limits"] = {
                "unknown-model": {"monthly_token_limit": 1}
            }
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "unknown model alias"):
                GatewayConfig.load(
                    path,
                    environ={"HORMUZ_TOKEN": "test-identity-token"},
                )

    def test_lifecycle_config_is_strict_and_requires_an_explicit_promoter(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
            path = root / "hormuz.json"

            raw["context_service"]["lifecycle"]["unknown"] = True
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "Unknown context_service.lifecycle fields"):
                GatewayConfig.load(path, environ={"HORMUZ_TOKEN": "test-identity-token"})

            raw["context_service"]["lifecycle"].pop("unknown")
            raw["context_service"]["lifecycle"]["enabled"] = True
            raw["identities"][0]["capabilities"] = []
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "no context_promoter"):
                GatewayConfig.load(path, environ={"HORMUZ_TOKEN": "test-identity-token"})

            raw["context_service"]["lifecycle"]["enabled"] = False
            raw["context_service"]["lifecycle"]["promotion_paths"] = []
            path.write_text(json.dumps(raw), encoding="utf-8")
            config = GatewayConfig.load(
                path,
                environ={"HORMUZ_TOKEN": "test-identity-token"},
            )
            self.assertFalse(config.context_service.lifecycle.enabled)

    def test_model_routes_snapshot_a_bounded_usd_rate_card_version(self) -> None:
        route = self.config.model_routes["gpt-5.4-mini"]
        self.assertEqual(route.rate_card_version, "example-2026-08-15-v1")
        self.assertEqual(route.currency, "USD")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
            path = root / "hormuz.json"
            raw["model_routes"]["gpt-5.4-mini"]["currency"] = "EUR"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "currency must be USD"):
                GatewayConfig.load(path, environ={"HORMUZ_TOKEN": "test-identity-token"})

            raw["model_routes"]["gpt-5.4-mini"]["currency"] = "USD"
            raw["model_routes"]["gpt-5.4-mini"]["rate_card_version"] = "bad\nversion"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "bounded single-line"):
                GatewayConfig.load(path, environ={"HORMUZ_TOKEN": "test-identity-token"})

    def test_model_routes_require_header_safe_identifiers(self) -> None:
        cases = (
            ("alias newline", "alias", "unsafe\r\nX-Injected: yes"),
            ("alias unicode", "alias", "unsafe-🚀"),
            ("upstream newline", "upstream", "unsafe\r\nX-Injected: yes"),
            ("upstream unicode", "upstream", "unsafe-🚀"),
            ("upstream oversized", "upstream", "m" * 513),
        )
        for name, location, invalid in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                raw = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
                route = raw["model_routes"]["gpt-5.4-mini"]
                if location == "alias":
                    del raw["model_routes"]["gpt-5.4-mini"]
                    raw["model_routes"][invalid] = route
                else:
                    route["upstream_model"] = invalid
                path = root / "hormuz.json"
                path.write_text(json.dumps(raw), encoding="utf-8")
                with self.assertRaisesRegex(ConfigError, "safe model identifier"):
                    GatewayConfig.load(
                        path,
                        environ={"HORMUZ_TOKEN": "test-identity-token"},
                    )

    def test_policy_check_reports_effective_team_dlp_without_provider_work(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = replace(
                self.config,
                database_path=Path(temporary) / "usage.sqlite3",
            )
            output = io.StringIO()
            with redirect_stdout(output):
                result = _policy_check(
                    config,
                    argparse.Namespace(
                        actor="alice",
                        client="codex",
                        protocol="openai",
                        model="gpt-5.4",
                        max_output_tokens=1000,
                    ),
                )

        self.assertEqual(result, 0)
        payload = json.loads(output.getvalue())
        self.assertRegex(payload["dlp_policy_version"], r"\Adlp-effective-v1:[0-9a-f]{32}\Z")
        rules = {rule["rule_id"]: rule for rule in payload["dlp_rules"]}
        self.assertEqual(rules["email_address"]["action"], "redact")
        self.assertEqual(rules["email_address"]["providers"], ["openai"])
        self.assertEqual(rules["email_address"]["models"], ["gpt-5.4"])

    def test_policy_check_reports_each_model_capacity_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
            path = root / "hormuz.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            config = GatewayConfig.load(
                path,
                environ={"HORMUZ_TOKEN": "test-identity-token"},
            )
            output = io.StringIO()
            with redirect_stdout(output):
                result = _policy_check(
                    config,
                    argparse.Namespace(
                        actor="alice",
                        client="codex",
                        protocol="openai",
                        model="gpt-5.5",
                        max_output_tokens=1_000,
                    ),
                )

        self.assertEqual(result, 0)
        limits = json.loads(output.getvalue())["model_limits"]
        self.assertEqual(
            [item["scope"] for item in limits],
            [
                "organization model",
                "organization model per-employee",
                "team model",
                "team model per-employee",
            ],
        )
        self.assertEqual(limits[0]["monthly_token_limit"], 10_000_000)
        self.assertEqual(limits[-1]["monthly_budget_usd"], 100.0)

    def test_dlp_approval_config_requires_key_and_organization_approver(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
            raw["identities"][0]["capabilities"] = ["dlp_approver"]
            raw["egress_controls"]["dlp"]["approval"] = {
                "enabled": True,
                "fingerprint_key_env": "DLP_FINGERPRINT_KEY",
            }
            raw["egress_controls"]["dlp"]["dictionaries"].append(
                {
                    "rule_id": "company.approval_term",
                    "action": "require_approval",
                    "providers": ["openai"],
                    "values_env": "DLP_APPROVAL_TERMS",
                }
            )
            path = root / "hormuz.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            key = base64.urlsafe_b64encode(b"k" * 32).decode("ascii")
            environment = {
                "HORMUZ_TOKEN": "test-identity-token",
                "DLP_FINGERPRINT_KEY": key,
                "DLP_APPROVAL_TERMS": json.dumps(["PROJECT-TRIDENT"]),
            }

            config = GatewayConfig.load(path, environ=environment)
            self.assertTrue(config.dlp_controls.approval.enabled)
            self.assertEqual(config.dlp_controls.approval.ttl_seconds, 900)
            self.assertEqual(
                config.identities_by_actor["alice"].capabilities,
                ("dlp_approver",),
            )
            self.assertNotIn(key, repr(config))

            invalid_environment = {**environment, "DLP_FINGERPRINT_KEY": "invalid"}
            with self.assertRaisesRegex(ConfigError, "decode to exactly 32 bytes|must be base64url"):
                GatewayConfig.load(path, environ=invalid_environment)

            raw["identities"][0]["capabilities"] = []
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "no dlp_approver"):
                GatewayConfig.load(path, environ=environment)

            raw["identities"][0]["capabilities"] = ["organization_owner"]
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, r"Unknown identities\[0\].capabilities"):
                GatewayConfig.load(path, environ=environment)

    def test_dlp_evaluate_cli_writes_only_aggregate_private_evidence(self) -> None:
        marker = "CLI-EVALUATION-CONTENT-NEVER-RETAIN"
        with tempfile.TemporaryDirectory() as temporary:
            corpus = Path(temporary) / "corpus.jsonl"
            output = Path(temporary) / "evaluation.json"
            corpus.write_text(
                "\n".join(
                    (
                        json.dumps(
                            {
                                "payload": {"input": f"employee@example.com {marker}"},
                                "expected_match": True,
                            }
                        ),
                        json.dumps(
                            {
                                "payload": {"input": "ordinary text"},
                                "expected_match": False,
                            }
                        ),
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            with mock.patch.dict(
                os.environ,
                {"HORMUZ_TOKEN": "test-identity-token"},
            ):
                exit_code = main(
                    [
                        "--config",
                        str(ROOT / "config.example.json"),
                        "dlp",
                        "evaluate",
                        "--rule-id",
                        "email_address",
                        "--corpus-id",
                        "email-eval-v1",
                        "--protocol",
                        "openai",
                        "--model",
                        "gpt-5.4-mini",
                        "--input",
                        str(corpus),
                        "--output",
                        str(output),
                    ]
                )

            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 0)
            self.assertEqual(report["confusion_matrix"]["true_positive"], 1)
            self.assertEqual(report["confusion_matrix"]["true_negative"], 1)
            self.assertFalse(report["privacy"]["payloads_retained"])
            self.assertNotIn(marker, output.read_text(encoding="utf-8"))
            self.assertEqual(os.stat(output).st_mode & 0o777, 0o600)

    def test_dlp_evaluate_cli_does_not_reflect_invalid_corpus_content(self) -> None:
        marker = "INVALID-CORPUS-CONTENT-NEVER-REFLECT"
        with tempfile.TemporaryDirectory() as temporary:
            corpus = Path(temporary) / "invalid.jsonl"
            output = Path(temporary) / "evaluation.json"
            corpus.write_text(
                json.dumps(
                    {
                        "payload": {"input": marker},
                        "expected_match": True,
                        "unexpected": marker,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with mock.patch.dict(
                os.environ,
                {"HORMUZ_TOKEN": "test-identity-token"},
            ), redirect_stderr(stderr := io.StringIO()):
                exit_code = main(
                    [
                        "--config",
                        str(ROOT / "config.example.json"),
                        "dlp",
                        "evaluate",
                        "--rule-id",
                        "email_address",
                        "--corpus-id",
                        "email-eval-v1",
                        "--protocol",
                        "openai",
                        "--model",
                        "gpt-5.4-mini",
                        "--input",
                        str(corpus),
                        "--output",
                        str(output),
                    ]
                )

            self.assertEqual(exit_code, 1)
            self.assertIn("must contain exactly payload and expected_match", stderr.getvalue())
            self.assertNotIn(marker, stderr.getvalue())
            self.assertFalse(output.exists())

    def test_dlp_evaluate_cli_rejects_unconfigured_upstream_scope_before_input(self) -> None:
        marker = "UNCONFIGURED-MODEL-NEVER-REFLECT"
        with mock.patch.dict(
            os.environ,
            {"HORMUZ_TOKEN": "test-identity-token"},
        ), redirect_stderr(stderr := io.StringIO()):
            exit_code = main(
                [
                    "--config",
                    str(ROOT / "config.example.json"),
                    "dlp",
                    "evaluate",
                    "--rule-id",
                    "email_address",
                    "--corpus-id",
                    "email-eval-v1",
                    "--protocol",
                    "openai",
                    "--model",
                    marker,
                    "--input",
                    "/path/that/must/not/be/read.jsonl",
                ]
            )

        self.assertEqual(exit_code, 1)
        self.assertIn("configured routed model not found for protocol", stderr.getvalue())
        self.assertNotIn(marker, stderr.getvalue())

    def test_usage_report_budget_matches_policy_scope(self) -> None:
        self.assertEqual(
            _budget_for_scope(self.config, "organization", {"scope_id": "organization"}),
            10_000,
        )
        self.assertEqual(
            _budget_for_scope(self.config, "team", {"scope_id": "engineering"}),
            5_000,
        )
        self.assertEqual(
            _budget_for_scope(self.config, "person", {"scope_id": "alice"}),
            500,
        )
        self.assertIsNone(
            _budget_for_scope(self.config, "model", {"scope_id": "gpt-5.4-mini"})
        )
        self.assertIsNone(
            _budget_for_scope(
                self.config,
                "organization",
                {"scope_id": "organization"},
                actor_filter="alice",
            )
        )
        self.assertIsNone(
            _budget_for_scope(
                self.config,
                "team",
                {"scope_id": "engineering"},
                actor_filter="alice",
            )
        )

    def test_audit_export_is_private_and_refuses_accidental_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = replace(self.config, database_path=root / "usage.sqlite3")
            output_path = root / "audit.jsonl"
            args = argparse.Namespace(
                kind="all",
                since="2026-08-01T00:00:00Z",
                output=str(output_path),
                force=False,
            )
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                self.assertEqual(_audit_export(config, args), 0)
            self.assertEqual(output_path.read_text(encoding="utf-8"), "")
            self.assertEqual(os.stat(output_path).st_mode & 0o777, 0o600)
            self.assertIn("sha256=", stderr.getvalue())

            with redirect_stderr(io.StringIO()):
                self.assertEqual(_audit_export(config, args), 2)
            args.force = True
            with redirect_stderr(io.StringIO()):
                self.assertEqual(_audit_export(config, args), 0)

            if hasattr(os, "O_NOFOLLOW"):
                symlink_target = root / "must-not-change.jsonl"
                symlink_target.write_text("preserve me", encoding="utf-8")
                symlink_path = root / "audit-symlink.jsonl"
                symlink_path.symlink_to(symlink_target)
                args.output = str(symlink_path)
                with redirect_stderr(io.StringIO()):
                    self.assertEqual(_audit_export(config, args), 2)
                self.assertEqual(symlink_target.read_text(encoding="utf-8"), "preserve me")

    def test_audit_chain_export_and_anchored_verification_are_content_free(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = replace(self.config, database_path=root / "usage.sqlite3")
            output_path = root / "audit-chain.jsonl"
            export_args = argparse.Namespace(
                kind="all",
                since="2026-08-01T00:00:00Z",
                output=str(output_path),
                force=False,
                chain=True,
            )
            with redirect_stderr(export_error := io.StringIO()):
                self.assertEqual(_audit_export(config, export_args), 0)
            self.assertEqual(output_path.read_bytes(), b"")
            self.assertEqual(os.stat(output_path).st_mode & 0o777, 0o600)
            self.assertIn(
                "chain_sha256=" + AUDIT_CHAIN_GENESIS_SHA256,
                export_error.getvalue(),
            )
            self.assertIn("chain_count=0", export_error.getvalue())

            verify_args = argparse.Namespace(
                input=str(output_path),
                expected_head=AUDIT_CHAIN_GENESIS_SHA256,
                expected_count=0,
                expected_sha256=hashlib.sha256(b"").hexdigest(),
            )
            with redirect_stdout(verify_output := io.StringIO()):
                self.assertEqual(_audit_verify(verify_args), 0)
            result = json.loads(verify_output.getvalue())
            self.assertEqual(result["status"], "verified")
            self.assertEqual(result["event_count"], 0)

            parser = build_parser()
            parsed_export = parser.parse_args(["audit-export", "--chain"])
            self.assertTrue(parsed_export.chain)
            parsed_context = parser.parse_args(
                ["context-audit-export", "--actor", "alice", "--chain"]
            )
            self.assertTrue(parsed_context.chain)
            parsed_verify = parser.parse_args(
                [
                    "audit-verify",
                    "--input",
                    str(output_path),
                    "--expected-head",
                    AUDIT_CHAIN_GENESIS_SHA256,
                    "--expected-count",
                    "0",
                ]
            )
            self.assertEqual(parsed_verify.command, "audit-verify")
            with redirect_stdout(main_output := io.StringIO()):
                self.assertEqual(
                    main(
                        [
                            "audit-verify",
                            "--input",
                            str(output_path),
                            "--expected-head",
                            AUDIT_CHAIN_GENESIS_SHA256,
                            "--expected-count",
                            "0",
                        ]
                    ),
                    0,
                )
            self.assertEqual(json.loads(main_output.getvalue())["status"], "verified")
            with redirect_stderr(io.StringIO()):
                self.assertEqual(_audit_export(config, export_args), 2)

            failed_path = root / "failed-chain.jsonl"
            export_args.output = str(failed_path)
            with mock.patch(
                "hormuz.cli.write_audit_chain",
                side_effect=AuditChainError("fixed writer failure"),
            ), redirect_stderr(io.StringIO()):
                self.assertEqual(_audit_export(config, export_args), 2)
            self.assertFalse(failed_path.exists())
            failed_path.write_text("preserve me", encoding="utf-8")
            export_args.force = True
            with mock.patch(
                "hormuz.cli.write_audit_chain",
                side_effect=AuditChainError("fixed writer failure"),
            ), redirect_stderr(io.StringIO()):
                self.assertEqual(_audit_export(config, export_args), 2)
            self.assertEqual(failed_path.read_text(encoding="utf-8"), "preserve me")

            tampered_path = root / "tampered.jsonl"
            tampered_path.write_bytes(b"hormuz-sentinel\n")
            tampered_args = argparse.Namespace(
                input=str(tampered_path),
                expected_head=AUDIT_CHAIN_GENESIS_SHA256,
                expected_count=0,
                expected_sha256=None,
            )
            with redirect_stderr(tampered_error := io.StringIO()):
                self.assertEqual(_audit_verify(tampered_args), 1)
            self.assertNotIn("hormuz-sentinel", tampered_error.getvalue())

    def test_audit_since_normalizes_to_utc(self) -> None:
        self.assertEqual(_audit_since("2026-08-01"), "2026-08-01T00:00:00+00:00")
        self.assertEqual(
            _audit_since("2026-08-01T02:00:00+02:00"),
            "2026-08-01T00:00:00+00:00",
        )
        with self.assertRaises(ValueError):
            _audit_since("not-a-timestamp")

    def test_context_pack_cli_uses_configured_actor_scope_and_explicit_content_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            records_path = Path(temporary) / "context.jsonl"
            records_path.write_text(
                json.dumps(
                    {
                        "id": "engineering-standard",
                        "title": "Retry standard",
                        "content": "Use bounded retry policy with jitter.",
                        "organization_id": "xpounder",
                        "visibility": "team",
                        "scope_id": "engineering",
                        "classification": "internal",
                        "source": {
                            "uri": "https://example.test/adr/17",
                            "revision": "git:abc123",
                        },
                        "repository_id": "acme/api",
                        "verification": "verified",
                        "verified_at": "2026-08-14T12:00:00Z",
                        "tags": ["reliability"],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            args = build_parser().parse_args(
                [
                    "context-pack",
                    "--records",
                    str(records_path),
                    "--query",
                    "retry policy",
                    "--organization",
                    "xpounder",
                    "--actor",
                    "alice",
                    "--repository",
                    "acme/api",
                    "--token-budget",
                    "500",
                    "--policy-version",
                    "policy-17",
                    "--as-of",
                    "2026-08-15T12:00:00Z",
                ]
            )
            output = io.StringIO()

            with redirect_stdout(output):
                self.assertEqual(_context_pack(self.config, args), 0)

            pack = json.loads(output.getvalue())
            self.assertEqual(pack["schema_version"], "hormuz.context-pack.v1")
            self.assertEqual(pack["retrieval_version"], "lexical-v1")
            self.assertEqual(pack["render_version"], "json-v1")
            self.assertEqual(pack["scope"]["actor_id"], "alice")
            self.assertEqual(pack["scope"]["team_id"], "engineering")
            self.assertEqual(pack["items"][0]["id"], "engineering-standard")
            self.assertEqual(pack["items"][0]["content"], "Use bounded retry policy with jitter.")

    def test_context_pack_cli_rejects_branch_without_repository(self) -> None:
        args = build_parser().parse_args(
            [
                "context-pack",
                "--records",
                "unused.jsonl",
                "--query",
                "retry",
                "--organization",
                "xpounder",
                "--actor",
                "alice",
                "--branch",
                "main",
                "--token-budget",
                "100",
            ]
        )
        with redirect_stderr(io.StringIO()):
            self.assertEqual(_context_pack(self.config, args), 2)

    def test_context_pack_cli_cannot_expand_identity_scope(self) -> None:
        base = [
            "context-pack",
            "--records",
            "unused.jsonl",
            "--query",
            "retry",
            "--actor",
            "alice",
            "--token-budget",
            "100",
        ]
        wrong_organization = build_parser().parse_args(
            [*base, "--organization", "another-organization"]
        )
        over_clearance = build_parser().parse_args(
            [*base, "--organization", "xpounder", "--clearance", "restricted"]
        )
        with redirect_stderr(io.StringIO()):
            self.assertEqual(_context_pack(self.config, wrong_organization), 2)
            self.assertEqual(_context_pack(self.config, over_clearance), 2)

    def test_persistent_context_cli_import_list_pack_export_and_delete(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = replace(self.config, context_database_path=root / "context.sqlite3")
            records_path = root / "records.jsonl"
            old = {
                "id": "retry-v1",
                "kind": "decision",
                "title": "Retry policy v1",
                "content": "Use one retry.",
                "owner_id": "alice",
                "organization_id": "xpounder",
                "visibility": "team",
                "scope_id": "engineering",
                "classification": "internal",
                "source": {
                    "uri": "https://example.test/adr/retries",
                    "revision": "git:one",
                    "item_key": "retry-v1",
                },
                "repository_id": "acme/api",
                "verification": "verified",
                "verification_evidence": ["ci:passed"],
                "effective_at": "2026-08-10T12:00:00Z",
                "verified_at": "2026-08-10T12:00:00Z",
                "tags": ["retry"],
            }
            new = {
                **old,
                "id": "retry-v2",
                "title": "Retry policy v2",
                "content": "Use bounded retries with jitter.",
                "source": {
                    **old["source"],
                    "revision": "git:two",
                    "item_key": "retry-v2",
                },
                "supersedes_id": "retry-v1",
            }
            records_path.write_text(
                json.dumps(new) + "\n" + json.dumps(old) + "\n",
                encoding="utf-8",
            )
            import_args = build_parser().parse_args(
                [
                    "context-import",
                    "--records",
                    str(records_path),
                    "--actor",
                    "alice",
                    "--policy-version",
                    "policy-2",
                ]
            )
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(_context_import(config, import_args), 0)
            self.assertEqual(json.loads(output.getvalue())["imported"], 2)
            with redirect_stdout(output := io.StringIO()):
                self.assertEqual(_context_import(config, import_args), 0)
            self.assertEqual(json.loads(output.getvalue())["already_present"], 2)

            list_args = build_parser().parse_args(
                [
                    "context-list",
                    "--actor",
                    "alice",
                    "--repository",
                    "acme/api",
                    "--as-of",
                    "2026-08-15T12:00:00Z",
                ]
            )
            with redirect_stdout(output := io.StringIO()):
                self.assertEqual(_context_list(config, list_args), 0)
            listed = json.loads(output.getvalue())
            self.assertEqual(listed["total"], 2)
            self.assertNotIn("content", listed["records"][0])

            pack_args = build_parser().parse_args(
                [
                    "context-pack",
                    "--query",
                    "retry jitter",
                    "--organization",
                    "xpounder",
                    "--actor",
                    "alice",
                    "--repository",
                    "acme/api",
                    "--token-budget",
                    "1000",
                    "--as-of",
                    "2026-08-15T12:00:00Z",
                ]
            )
            with redirect_stdout(output := io.StringIO()):
                self.assertEqual(_context_pack(config, pack_args), 0)
            packed = json.loads(output.getvalue())
            self.assertEqual(packed["schema_version"], "hormuz.context-pack.v1")
            self.assertEqual(packed["retrieval_version"], "lexical-v1")
            self.assertEqual(packed["render_version"], "json-v1")
            self.assertEqual([item["id"] for item in packed["items"]], ["retry-v2"])

            export_path = root / "export.jsonl"
            export_args = build_parser().parse_args(
                [
                    "context-export",
                    "--actor",
                    "alice",
                    "--repository",
                    "acme/api",
                    "--as-of",
                    "2026-08-15T12:00:00Z",
                    "--output",
                    str(export_path),
                ]
            )
            with redirect_stderr(io.StringIO()):
                self.assertEqual(_context_export(config, export_args), 0)
            self.assertEqual(os.stat(export_path).st_mode & 0o777, 0o600)
            exported = [json.loads(line) for line in export_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual({item["id"] for item in exported}, {"retry-v1", "retry-v2"})
            self.assertTrue(all("content" in item for item in exported))

            context_audit_path = root / "context-audit.jsonl"
            context_audit_args = build_parser().parse_args(
                [
                    "context-audit-export",
                    "--actor",
                    "alice",
                    "--since",
                    "2026-08-01T00:00:00Z",
                    "--output",
                    str(context_audit_path),
                ]
            )
            with redirect_stderr(io.StringIO()):
                self.assertEqual(_context_audit_export(config, context_audit_args), 0)
            self.assertEqual(os.stat(context_audit_path).st_mode & 0o777, 0o600)
            audit_text = context_audit_path.read_text(encoding="utf-8")
            audit_events = [json.loads(line) for line in audit_text.splitlines()]
            self.assertEqual(len(audit_events), 3)
            self.assertEqual(
                [event["event_type"] for event in audit_events],
                ["context.mutation", "context.mutation", "context.read"],
            )
            access_event = audit_events[-1]
            self.assertEqual(access_event["action"], "pack")
            self.assertEqual(access_event["actor_id"], "alice")
            self.assertEqual(access_event["repository_id"], "acme/api")
            self.assertEqual(access_event["selected_records"], 1)
            self.assertEqual({event["organization_id"] for event in audit_events}, {"xpounder"})
            self.assertNotIn("Use one retry", audit_text)
            self.assertNotIn("Retry policy", audit_text)
            self.assertNotIn("example.test/adr", audit_text)
            self.assertNotIn("retry jitter", audit_text)

            context_chain_path = root / "context-audit-chain.jsonl"
            context_chain_args = build_parser().parse_args(
                [
                    "context-audit-export",
                    "--actor",
                    "alice",
                    "--since",
                    "2026-08-01T00:00:00Z",
                    "--output",
                    str(context_chain_path),
                    "--chain",
                ]
            )
            with redirect_stderr(context_chain_error := io.StringIO()):
                self.assertEqual(
                    _context_audit_export(config, context_chain_args),
                    0,
                )
            context_chain_bytes = context_chain_path.read_bytes()
            chained_events = [
                json.loads(line) for line in context_chain_bytes.splitlines()
            ]
            self.assertEqual(len(chained_events), 3)
            self.assertTrue(
                all(
                    item["schema_version"] == AUDIT_CHAIN_SCHEMA_VERSION
                    for item in chained_events
                )
            )
            self.assertEqual(
                [item["event"]["event_type"] for item in chained_events],
                ["context.mutation", "context.mutation", "context.read"],
            )
            self.assertIn("chain_count=3", context_chain_error.getvalue())
            self.assertNotIn("Use one retry", context_chain_bytes.decode("utf-8"))
            self.assertNotIn("retry jitter", context_chain_bytes.decode("utf-8"))
            verify_context_args = argparse.Namespace(
                input=str(context_chain_path),
                expected_head=chained_events[-1]["chain_sha256"],
                expected_count=3,
                expected_sha256=hashlib.sha256(context_chain_bytes).hexdigest(),
            )
            with redirect_stdout(context_verify_output := io.StringIO()):
                self.assertEqual(_audit_verify(verify_context_args), 0)
            self.assertEqual(
                json.loads(context_verify_output.getvalue())["status"],
                "verified",
            )

            delete_args = build_parser().parse_args(
                [
                    "context-delete",
                    "--actor",
                    "alice",
                    "--record-id",
                    "retry-v2",
                    "--expected-version",
                    "1",
                ]
            )
            with redirect_stdout(io.StringIO()):
                self.assertEqual(_context_delete(config, delete_args), 0)

    def test_persistent_lifecycle_snapshot_controls_pack_and_uses_optimistic_versioning(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = replace(self.config, context_database_path=root / "context.sqlite3")
            records_path = root / "records.jsonl"
            dependency_uri = "repo://acme/api/config/retries.json"
            base = {
                "kind": "decision",
                "organization_id": "xpounder",
                "visibility": "team",
                "scope_id": "engineering",
                "classification": "internal",
                "repository_id": "acme/api",
                "branch": "main",
                "verification": "verified",
                "verification_evidence": ["ci:passed"],
                "effective_at": "2026-08-14T12:00:00Z",
                "verified_at": "2026-08-14T12:00:00Z",
                "tags": ["retry"],
            }
            current = {
                **base,
                "id": "current",
                "title": "Current retry policy",
                "content": "Use bounded retry policy with jitter.",
                "source": {
                    "uri": "https://example.test/current",
                    "revision": "git:current",
                    "item_key": "current",
                },
                "invalidation_rules": ["source_revision_changed"],
            }
            stale = {
                **base,
                "id": "stale-dependency",
                "title": "Legacy retry policy",
                "content": "Use the legacy retry policy.",
                "source": {
                    "uri": "https://example.test/stale",
                    "revision": "git:current",
                    "item_key": "stale-dependency",
                },
                "dependencies": [
                    {"uri": dependency_uri, "revision": "old", "sha256": "a" * 64}
                ],
            }
            records_path.write_text(
                json.dumps(current) + "\n" + json.dumps(stale) + "\n",
                encoding="utf-8",
            )
            import_args = build_parser().parse_args(
                ["context-import", "--records", str(records_path), "--actor", "alice"]
            )
            with redirect_stdout(io.StringIO()):
                self.assertEqual(_context_import(config, import_args), 0)

            snapshot_path = root / "snapshot.json"
            envelope = {
                "schema_version": "hormuz.context-lifecycle-envelope.v1",
                "organization_id": "xpounder",
                "repository_id": "acme/api",
                "branch": "main",
                "snapshot": {
                    "schema_version": "hormuz.context-lifecycle-snapshot.v1",
                    "repository_revision": "current",
                    "artifacts": [
                        {"uri": dependency_uri, "revision": "new", "sha256": "b" * 64}
                    ],
                },
            }
            snapshot_path.write_text(json.dumps(envelope), encoding="utf-8")
            snapshot_args = build_parser().parse_args(
                [
                    "context-snapshot-import",
                    "--snapshot",
                    str(snapshot_path),
                    "--actor",
                    "alice",
                    "--policy-version",
                    "lifecycle-v1",
                ]
            )
            with redirect_stdout(output := io.StringIO()):
                self.assertEqual(_context_snapshot_import(config, snapshot_args), 0)
            self.assertEqual(json.loads(output.getvalue())["storage"]["version"], 1)
            with redirect_stdout(output := io.StringIO()):
                self.assertEqual(_context_snapshot_import(config, snapshot_args), 0)
            self.assertEqual(json.loads(output.getvalue())["storage"]["version"], 1)

            show_args = build_parser().parse_args(
                [
                    "context-snapshot-show",
                    "--actor",
                    "alice",
                    "--repository",
                    "acme/api",
                    "--branch",
                    "main",
                ]
            )
            with redirect_stdout(output := io.StringIO()):
                self.assertEqual(_context_snapshot_show(config, show_args), 0)
            self.assertEqual(json.loads(output.getvalue())["snapshot_sha256"], envelope_hash(envelope))

            pack_args = build_parser().parse_args(
                [
                    "context-pack",
                    "--query",
                    "retry policy",
                    "--organization",
                    "xpounder",
                    "--actor",
                    "alice",
                    "--repository",
                    "acme/api",
                    "--branch",
                    "main",
                    "--token-budget",
                    "1000",
                    "--as-of",
                    "2026-08-15T12:00:00Z",
                ]
            )
            with redirect_stdout(output := io.StringIO()):
                self.assertEqual(_context_pack(config, pack_args), 0)
            packed = json.loads(output.getvalue())
            self.assertEqual([item["id"] for item in packed["items"]], ["current"])
            self.assertEqual(packed["lifecycle"]["outcome"], "partial")
            self.assertEqual(
                packed["exclusions"][0]["reason"],
                "dependency_revision_mismatch",
            )

            changed = json.loads(json.dumps(envelope))
            changed["snapshot"]["repository_revision"] = "next"
            snapshot_path.write_text(json.dumps(changed), encoding="utf-8")
            with redirect_stderr(io.StringIO()):
                self.assertEqual(_context_snapshot_import(config, snapshot_args), 2)
            snapshot_args.expected_version = 1
            with redirect_stdout(output := io.StringIO()):
                self.assertEqual(_context_snapshot_import(config, snapshot_args), 0)
            self.assertEqual(json.loads(output.getvalue())["storage"]["version"], 2)

    def test_lifecycle_cli_imports_evidence_and_revalidates_under_promoter_capability(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base_identity = self.config.identities_by_actor["alice"]
            promoter_identity = replace(
                base_identity,
                capabilities=("context_promoter",),
            )
            config = replace(
                self.config,
                context_database_path=root / "context.sqlite3",
                context_service=replace(
                    self.config.context_service,
                    lifecycle=replace(
                        self.config.context_service.lifecycle,
                        enabled=True,
                    ),
                ),
                identities_by_token={base_identity.token: promoter_identity},
            )
            records_path = root / "records.jsonl"
            records_path.write_text(
                json.dumps(
                    {
                        "id": "retry-observation",
                        "kind": "claim",
                        "title": "Retry observation",
                        "content": "Bounded retries passed the repository test suite.",
                        "organization_id": "xpounder",
                        "visibility": "team",
                        "scope_id": "engineering",
                        "classification": "internal",
                        "source": {
                            "uri": "repo://acme/api/retry.py",
                            "revision": "git:abc123",
                            "item_key": "retry-observation",
                        },
                        "repository_id": "acme/api",
                        "branch": "main",
                        "verification": "provisional",
                        "effective_at": "2026-08-15T12:00:00Z",
                        "invalidation_rules": ["source_revision_changed"],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            import_args = build_parser().parse_args(
                ["context-import", "--records", str(records_path), "--actor", "alice"]
            )
            provisional_text = records_path.read_text(encoding="utf-8")
            verified_payload = json.loads(provisional_text)
            verified_payload["verification"] = "verified"
            verified_payload["verification_evidence"] = ["manual:claim"]
            verified_payload["verified_at"] = "2026-08-15T12:30:00Z"
            records_path.write_text(json.dumps(verified_payload) + "\n", encoding="utf-8")
            with redirect_stderr(io.StringIO()):
                self.assertEqual(_context_import(config, import_args), 2)
            records_path.write_text(provisional_text, encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(_context_import(config, import_args), 0)

            snapshot_path = root / "snapshot.json"
            snapshot_path.write_text(
                json.dumps(
                    {
                        "schema_version": "hormuz.context-lifecycle-envelope.v1",
                        "organization_id": "xpounder",
                        "repository_id": "acme/api",
                        "branch": "main",
                        "snapshot": {
                            "schema_version": "hormuz.context-lifecycle-snapshot.v1",
                            "repository_revision": "abc123",
                            "artifacts": [],
                        },
                    }
                ),
                encoding="utf-8",
            )
            snapshot_args = build_parser().parse_args(
                [
                    "context-snapshot-import",
                    "--snapshot",
                    str(snapshot_path),
                    "--actor",
                    "alice",
                ]
            )
            with redirect_stdout(io.StringIO()):
                self.assertEqual(_context_snapshot_import(config, snapshot_args), 0)

            raw_reference = "github-actions:private-run:12345"
            evidence_path = root / "evidence.json"
            evidence_args = build_parser().parse_args(
                [
                    "context-evidence-import",
                    "--evidence",
                    str(evidence_path),
                    "--actor",
                    "alice",
                ]
            )
            for signal in ("commit_merged", "ci_passed"):
                evidence_path.write_text(
                    json.dumps(
                        {
                            "schema_version": "hormuz.context-evidence.v1",
                            "organization_id": "xpounder",
                            "record_id": "retry-observation",
                            "record_version": 1,
                            "signal": signal,
                            "evidence_ref": f"{raw_reference}:{signal}",
                            "observed_at": "2026-08-15T13:00:00Z",
                        }
                    ),
                    encoding="utf-8",
                )
                with redirect_stdout(output := io.StringIO()):
                    self.assertEqual(_context_evidence_import(config, evidence_args), 0)
                self.assertTrue(json.loads(output.getvalue())["created"])
                if signal == "commit_merged":
                    with redirect_stdout(output := io.StringIO()):
                        self.assertEqual(_context_evidence_import(config, evidence_args), 0)
                    self.assertFalse(json.loads(output.getvalue())["created"])
            self.assertNotIn(raw_reference.encode(), config.context_database_path.read_bytes())

            revalidate_args = build_parser().parse_args(
                [
                    "context-revalidate",
                    "--actor",
                    "alice",
                    "--repository",
                    "acme/api",
                    "--branch",
                    "main",
                    "--batch-size",
                    "1",
                ]
            )
            with redirect_stdout(output := io.StringIO()):
                self.assertEqual(_context_revalidate(config, revalidate_args), 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["promoted_records"], 1)
            revalidate_args.batch_size = 0
            with redirect_stderr(io.StringIO()):
                self.assertEqual(_context_revalidate(config, revalidate_args), 2)
            revalidate_args.batch_size = 1
            with sqlite3.connect(config.context_database_path) as connection:
                verification, version = connection.execute(
                    "SELECT verification, version FROM context_records WHERE id = ?",
                    ("retry-observation",),
                ).fetchone()
            self.assertEqual((verification, version), ("verified", 2))

            evidence_path.write_text(
                '{"schema_version":"hormuz.context-evidence.v1",'
                '"organization_id":"xpounder","organization_id":"another",'
                '"record_id":"retry-observation","record_version":2,'
                '"signal":"ci_failed","evidence_ref":"duplicate",'
                '"observed_at":"2026-08-15T14:00:00Z"}',
                encoding="utf-8",
            )
            with redirect_stderr(io.StringIO()):
                self.assertEqual(_context_evidence_import(config, evidence_args), 2)

            identity = config.identities_by_actor["alice"]
            denied_identity = replace(identity, capabilities=())
            denied = replace(
                config,
                identities_by_token={identity.token: denied_identity},
            )
            with redirect_stderr(io.StringIO()):
                self.assertEqual(_context_revalidate(denied, revalidate_args), 2)

            events = SQLiteContextRepository(config.context_database_path).audit_events(
                organization_id="xpounder",
                since=datetime(2026, 8, 1, tzinfo=timezone.utc),
            )
            event_types = {event["event_type"] for event in events}
            self.assertIn("context.evidence", event_types)
            self.assertIn("context.revalidation", event_types)
            serialized = json.dumps(events)
            self.assertNotIn(raw_reference, serialized)

    def test_lifecycle_snapshot_import_rejects_cross_organization_scope_before_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = replace(self.config, context_database_path=root / "context.sqlite3")
            path = root / "snapshot.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": "hormuz.context-lifecycle-envelope.v1",
                        "organization_id": "another-organization",
                        "repository_id": "acme/api",
                        "branch": "main",
                        "snapshot": {
                            "schema_version": "hormuz.context-lifecycle-snapshot.v1",
                            "repository_revision": "current",
                            "artifacts": [],
                        },
                    }
                ),
                encoding="utf-8",
            )
            args = build_parser().parse_args(
                ["context-snapshot-import", "--snapshot", str(path), "--actor", "alice"]
            )
            with redirect_stderr(io.StringIO()):
                self.assertEqual(_context_snapshot_import(config, args), 2)
            if config.context_database_path.exists():
                connection = sqlite3.connect(config.context_database_path)
                try:
                    count = connection.execute(
                        "SELECT COUNT(*) FROM context_lifecycle_snapshots"
                    ).fetchone()[0]
                finally:
                    connection.close()
                self.assertEqual(count, 0)

    def test_context_import_rejects_the_entire_batch_before_cross_scope_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = replace(self.config, context_database_path=root / "context.sqlite3")
            records_path = root / "records.jsonl"
            base = {
                "id": "allowed",
                "title": "Allowed",
                "content": "Allowed content",
                "organization_id": "xpounder",
                "visibility": "organization",
                "scope_id": "xpounder",
                "classification": "internal",
                "source": {"uri": "https://example.test/one", "revision": "1"},
            }
            denied = {
                **base,
                "id": "denied",
                "organization_id": "another-organization",
                "scope_id": "another-organization",
                "source": {"uri": "https://example.test/two", "revision": "1"},
            }
            records_path.write_text(
                json.dumps(base) + "\n" + json.dumps(denied) + "\n",
                encoding="utf-8",
            )
            args = build_parser().parse_args(
                ["context-import", "--records", str(records_path), "--actor", "alice"]
            )
            with redirect_stderr(io.StringIO()):
                self.assertEqual(_context_import(config, args), 2)
            if config.context_database_path.exists():
                connection = sqlite3.connect(config.context_database_path)
                try:
                    count = connection.execute("SELECT COUNT(*) FROM context_records").fetchone()[0]
                finally:
                    connection.close()
                self.assertEqual(count, 0)

    def test_context_audit_since_requires_timezone_and_normalizes_utc(self) -> None:
        self.assertEqual(
            _context_audit_since("2026-08-01T02:00:00+02:00"),
            datetime(2026, 8, 1, tzinfo=timezone.utc),
        )
        with self.assertRaisesRegex(ContextError, "timezone"):
            _context_audit_since("2026-08-01T00:00:00")


if __name__ == "__main__":
    unittest.main()
