from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from hormuz.cli import build_parser, main
from hormuz.client_conformance import (
    ClientConformanceError,
    ClientConformanceRunner,
    _run_bounded,
)


MARKER = "HORMUZ_CLIENT_OK_1"
EXPECTED_VERSION = "9.8.7"
EXECUTABLE_SHA256 = hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest()


class FakeProcessRunner:
    def __init__(self, client: str, *, marker: str = MARKER, exit_code: int = 0) -> None:
        self.client = client
        self.marker = marker
        self.exit_code = exit_code
        self.calls: list[tuple[list[str], dict[str, str], Path, float, int]] = []
        self.settings_contents: str | None = None
        self.client_home_exists = False

    def __call__(
        self,
        command: list[str],
        environment: dict[str, str],
        cwd: Path,
        timeout_seconds: float,
        maximum_output_bytes: int,
    ) -> tuple[int, bytes]:
        self.calls.append(
            (list(command), dict(environment), cwd, timeout_seconds, maximum_output_bytes)
        )
        if command[1:] == ["--version"]:
            name = "codex-cli" if self.client == "codex" else "claude"
            return 0, f"{name} 9.8.7\n".encode()
        home_name = "CODEX_HOME" if self.client == "codex" else "CLAUDE_CONFIG_DIR"
        self.client_home_exists = Path(environment[home_name]).is_dir()
        if self.client == "codex":
            output_path = Path(command[command.index("--output-last-message") + 1])
            output_path.write_text(self.marker + "\n", encoding="utf-8")
            return self.exit_code, b"transient codex output that is not retained"
        settings = Path(command[command.index("--settings") + 1])
        self.settings_contents = settings.read_text(encoding="utf-8")
        return self.exit_code, json.dumps(
            {
                "type": "result",
                "result": self.marker,
                "session_id": "sensitive-session-id",
            }
        ).encode()


class ClientConformanceRunnerTests(unittest.TestCase):
    def test_codex_uses_isolated_environment_and_final_message_file(self) -> None:
        secret = "employee-gateway-token-must-not-persist"
        runner = FakeProcessRunner("codex")
        with mock.patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "provider-key-must-not-reach-client",
                "ANTHROPIC_API_KEY": "other-provider-key-must-not-reach-client",
                "CUSTOM_SECRET": "host-secret-must-not-reach-client",
            },
            clear=False,
        ):
            result = ClientConformanceRunner(
                "codex",
                gateway="http://127.0.0.1:8787",
                credential=secret,
                expected_version=EXPECTED_VERSION,
                expected_executable_sha256=EXECUTABLE_SHA256,
                allow_insecure_http=True,
                executable=sys.executable,
                process_runner=runner,
                clock=lambda: 50.0,
            ).run(model="engineering-fast")

        self.assertEqual(len(runner.calls), 2)
        command, environment, cwd, timeout, maximum = runner.calls[1]
        self.assertEqual(command[1], "exec")
        self.assertIn("--ignore-user-config", command)
        self.assertIn("--strict-config", command)
        self.assertIn("--ephemeral", command)
        self.assertIn("--output-last-message", command)
        self.assertIn('model_provider="company_gateway"', command)
        for restriction in (
            "features.shell_tool=false",
            "features.multi_agent=false",
            'web_search="disabled"',
            "check_for_update_on_startup=false",
            "analytics.enabled=false",
            "feedback.enabled=false",
        ):
            self.assertIn(restriction, command)
        self.assertTrue(any("/v1" in value for value in command))
        self.assertEqual(environment["HORMUZ_CLIENT_CONFORMANCE_TOKEN"], secret)
        self.assertEqual(environment["HOME"], str(cwd))
        self.assertEqual(environment["CODEX_HOME"], str(cwd / "codex-home"))
        self.assertTrue(runner.client_home_exists)
        for forbidden in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "CUSTOM_SECRET"):
            self.assertNotIn(forbidden, environment)
        self.assertEqual(timeout, 120)
        self.assertEqual(maximum, 1024 * 1024)

        self.assertEqual(result["schema_version"], "hormuz.client-conformance.v1")
        self.assertEqual(
            result["client"],
            {
                "name": "OpenAI Codex CLI",
                "version": EXPECTED_VERSION,
                "executable_sha256": EXECUTABLE_SHA256,
            },
        )
        self.assertEqual(result["provider_protocol"], "openai")
        self.assertEqual(result["gateway_interface"], "POST /v1/responses")
        self.assertEqual(result["gateway_transport"], "loopback_http")
        self.assertTrue(result["assurances"]["client_marker_verified"])
        serialized = json.dumps(result, sort_keys=True)
        for forbidden in (secret, MARKER, "127.0.0.1", "Reply with exactly"):
            self.assertNotIn(forbidden, serialized)

    def test_claude_uses_bare_structured_output_and_environment_backed_helper(self) -> None:
        secret = "employee-token"
        runner = FakeProcessRunner("claude")
        result = ClientConformanceRunner(
            "claude",
            gateway="https://hormuz.example/v1",
            credential=secret,
            expected_version=EXPECTED_VERSION,
            expected_executable_sha256=EXECUTABLE_SHA256,
            executable=sys.executable,
            process_runner=runner,
            clock=lambda: 25.0,
        ).run(model="claude-engineering")

        command, environment, cwd, _timeout, _maximum = runner.calls[1]
        self.assertIn("--bare", command)
        self.assertIn("--output-format", command)
        self.assertIn("json", command)
        self.assertIn("--no-session-persistence", command)
        self.assertIn("--settings", command)
        self.assertIn("dontAsk", command)
        self.assertEqual(environment["HORMUZ_CLIENT_CONFORMANCE_TOKEN"], secret)
        self.assertEqual(environment["ANTHROPIC_BASE_URL"], "https://hormuz.example")
        self.assertEqual(environment["CLAUDE_CONFIG_DIR"], str(cwd / "claude-home"))
        self.assertTrue(runner.client_home_exists)
        self.assertNotIn("ANTHROPIC_API_KEY", environment)
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", environment)
        settings = Path(command[command.index("--settings") + 1])
        self.assertEqual(settings.name, "claude-settings.json")
        self.assertIsNotNone(runner.settings_contents)
        self.assertNotIn(secret, runner.settings_contents or "")
        self.assertIn("HORMUZ_CLIENT_CONFORMANCE_TOKEN", runner.settings_contents or "")
        self.assertEqual(
            result["client"],
            {
                "name": "Anthropic Claude Code",
                "version": EXPECTED_VERSION,
                "executable_sha256": EXECUTABLE_SHA256,
            },
        )
        self.assertEqual(result["provider_protocol"], "anthropic")
        self.assertEqual(result["gateway_interface"], "POST /v1/messages")
        self.assertNotIn("sensitive-session-id", json.dumps(result))

    def test_invalid_inputs_and_unverified_results_fail_closed(self) -> None:
        for values, code in (
            ({"client": "other"}, "invalid_client"),
            ({"gateway": "http://hormuz.example"}, "insecure_gateway"),
            ({"credential": "bad\ncredential"}, "invalid_credential"),
            ({"expected_version": "not-a-version"}, "invalid_expected_version"),
            ({"expected_executable_sha256": "abc"}, "invalid_expected_executable_sha256"),
            ({"expected_executable_sha256": "f" * 64}, "executable_digest_mismatch"),
            ({"timeout_seconds": 4}, "invalid_timeout"),
        ):
            arguments = {
                "client": "codex",
                "gateway": "https://hormuz.example",
                "credential": "employee-token",
                "expected_version": EXPECTED_VERSION,
                "expected_executable_sha256": EXECUTABLE_SHA256,
                "executable": sys.executable,
                "process_runner": FakeProcessRunner("codex"),
            }
            arguments.update(values)
            with self.subTest(code=code), self.assertRaises(ClientConformanceError) as caught:
                ClientConformanceRunner(**arguments)
            self.assertEqual(caught.exception.code, code)

        for fake, code in (
            (FakeProcessRunner("codex", marker="WRONG"), "marker_mismatch"),
            (FakeProcessRunner("codex", exit_code=7), "client_failed"),
        ):
            with self.subTest(code=code):
                instance = ClientConformanceRunner(
                    "codex",
                    gateway="https://hormuz.example",
                    credential="employee-token",
                    expected_version=EXPECTED_VERSION,
                    expected_executable_sha256=EXECUTABLE_SHA256,
                    executable=sys.executable,
                    process_runner=fake,
                )
                with self.assertRaises(ClientConformanceError) as caught:
                    instance.run(model="engineering-fast")
                self.assertEqual(caught.exception.code, code)
                self.assertNotIn("WRONG", str(caught.exception))

        version_mismatch = ClientConformanceRunner(
            "codex",
            gateway="https://hormuz.example",
            credential="employee-token",
            expected_version="1.2.3",
            expected_executable_sha256=EXECUTABLE_SHA256,
            executable=sys.executable,
            process_runner=FakeProcessRunner("codex"),
        )
        with self.assertRaisesRegex(ClientConformanceError, "client_version_mismatch"):
            version_mismatch.run(model="engineering-fast")

        instance = ClientConformanceRunner(
            "codex",
            gateway="https://hormuz.example",
            credential="employee-token",
            expected_version=EXPECTED_VERSION,
            expected_executable_sha256=EXECUTABLE_SHA256,
            executable=sys.executable,
            process_runner=FakeProcessRunner("codex"),
        )
        with self.assertRaisesRegex(ClientConformanceError, "invalid_model"):
            instance.run(model="bad\nmodel")

    def test_bounded_process_runner_stops_timeout_and_oversized_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ClientConformanceError, "client_output_too_large"):
                _run_bounded(
                    [sys.executable, "-c", "import sys; sys.stdout.write('x' * 8192)"],
                    {},
                    root,
                    5,
                    1024,
                )
            with self.assertRaisesRegex(ClientConformanceError, "client_timeout"):
                _run_bounded(
                    [sys.executable, "-c", "import time; time.sleep(5)"],
                    {},
                    root,
                    0.1,
                    1024,
                )

    def test_checked_in_live_evidence_uses_content_free_schema(self) -> None:
        root = Path(__file__).resolve().parents[1]
        value = json.loads(
            (
                root
                / "evidence/client-conformance-codex-openai-2026-08-19.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            set(value),
            {
                "assurances",
                "client",
                "client_exit_code",
                "gateway_interface",
                "gateway_transport",
                "generated_at",
                "latency_milliseconds",
                "probe_version",
                "provider_protocol",
                "requested_model",
                "runner",
                "schema_version",
                "status",
            },
        )
        self.assertEqual(
            set(value["assurances"]),
            {
                "client_marker_verified",
                "client_output_retained",
                "client_persistence_disabled",
                "client_version_verified",
                "employee_credential_retained",
                "executable_sha256_verified",
                "fixed_content_probe",
                "gateway_url_retained",
                "host_environment_sanitized",
                "isolated_empty_workspace",
                "prompt_retained",
                "provider_credential_removed_from_client_environment",
                "response_content_retained",
            },
        )
        self.assertEqual(value["schema_version"], "hormuz.client-conformance.v1")
        self.assertEqual(
            value["client"],
            {
                "name": "OpenAI Codex CLI",
                "version": "0.147.0",
                "executable_sha256": "134063e133f0b4244fa3b251acf973d4fe4b4aeeacbdc135211bf480f59f1477",
            },
        )
        self.assertEqual(value["status"], "verified")
        self.assertNotIn(MARKER, json.dumps(value))


class ClientConformanceCLITests(unittest.TestCase):
    def test_parser_exposes_bounded_opt_in_command(self) -> None:
        args = build_parser().parse_args(
            [
                "client-conformance",
                "--client",
                "claude",
                "--gateway",
                "https://hormuz.example",
                "--model",
                "claude-engineering",
                "--expected-version",
                "2.1.233",
                "--expected-executable-sha256",
                "b" * 64,
            ]
        )
        self.assertEqual(args.command, "client-conformance")
        self.assertEqual(args.timeout_seconds, 120)
        self.assertEqual(args.credential_env, "HORMUZ_TOKEN")

    def test_cli_uses_named_credential_and_writes_content_free_result(self) -> None:
        verified = {
            "schema_version": "hormuz.client-conformance.v1",
            "status": "verified",
        }
        runner = mock.Mock()
        runner.run.return_value = verified
        with (
            mock.patch.dict(os.environ, {"TEST_HORMUZ_TOKEN": "employee-token"}),
            mock.patch("hormuz.cli.ClientConformanceRunner", return_value=runner) as constructor,
            mock.patch("hormuz.cli.write_conformance_result") as writer,
        ):
            result = main(
                [
                    "client-conformance",
                    "--client",
                    "codex",
                    "--gateway",
                    "https://hormuz.example",
                    "--model",
                    "engineering-fast",
                    "--credential-env",
                    "TEST_HORMUZ_TOKEN",
                    "--executable",
                    "/opt/codex",
                    "--expected-version",
                    "0.147.0",
                    "--expected-executable-sha256",
                    "a" * 64,
                    "--output",
                    "evidence.json",
                ]
            )
        self.assertEqual(result, 0)
        constructor.assert_called_once_with(
            "codex",
            gateway="https://hormuz.example",
            credential="employee-token",
            expected_version="0.147.0",
            expected_executable_sha256="a" * 64,
            timeout_seconds=120,
            allow_insecure_http=False,
            executable="/opt/codex",
        )
        runner.run.assert_called_once_with(model="engineering-fast")
        writer.assert_called_once_with(verified, "evidence.json", force=False)

    def test_cli_missing_credential_is_content_free_and_does_not_load_config(self) -> None:
        error = io.StringIO()
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch("hormuz.cli.GatewayConfig.load") as load,
            redirect_stderr(error),
        ):
            result = main(
                [
                    "client-conformance",
                    "--client",
                    "claude",
                    "--gateway",
                    "https://hormuz.example",
                    "--model",
                    "claude-engineering",
                    "--expected-version",
                    "2.1.233",
                    "--expected-executable-sha256",
                    "b" * 64,
                ]
            )
        self.assertEqual(result, 1)
        self.assertEqual(error.getvalue(), "client conformance failed: credential_not_set\n")
        load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
