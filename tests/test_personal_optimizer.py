from __future__ import annotations

import argparse
import contextlib
import http.client
import io
import json
import os
import shutil
import socket
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import hormuz.commands.personal as personal_commands
from hormuz.adapters import adapter_for, conformance_report
from hormuz.client_relay import LocalRelayServer, RelayOptimizer
from hormuz.client_versions import SUPPORTED_CLIENT_VERSIONS
from hormuz.compaction_runtime import ContextPreferenceStore, ContextRuntimeError
from hormuz.credential_store import CredentialStoreError, ProviderCredentialStore
from hormuz.execution_methods import (
    ExecutionMethodError,
    ExecutionRouter,
    JevChoiceAdapter,
    JevQualification,
    MethodRequest,
)
from hormuz.personal_metrics import (
    OptimizationMeasurement,
    PersonalMetricsError,
    PersonalMetricsStore,
    ProviderMeasurement,
    extract_provider_usage,
)
from hormuz.personal_optimization import (
    BehaviorEvent,
    BehaviorTracker,
    BoundedReadRetry,
    ConversationTurn,
    FrictionSignal,
    InterventionComparison,
    PersonalOptimizationError,
    RecoveryBuffer,
    TypedToolHistoryCompactor,
)
from hormuz.personal_adapter_example import ExampleAgentAdapter
from hormuz.personal_qualification import run_product_qualification
from hormuz.personal_profiles import (
    PersonalProfile,
    PersonalProfileError,
    PersonalProfileStore,
)
from hormuz.personal_runtime import run_personal_client


LOCAL_TOKEN = "hox_l_" + "l" * 43
DIRECT_TOKEN = "sk-direct-test-only"
COUNTERS = {"cl100k_base": len, "o200k_base": len}


class _MemoryKeyring:
    def __init__(self):
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.values.pop((service, username), None)


class _DirectProvider(ThreadingHTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), _DirectProviderHandler)
        self.requests: list[tuple[bytes, dict[str, str]]] = []


class _DirectProviderHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers["Content-Length"])
        body = self.rfile.read(length)
        self.server.requests.append((body, dict(self.headers)))
        response = json.dumps(
            {
                "id": "response-test",
                "usage": {
                    "input_tokens": 40,
                    "output_tokens": 5,
                    "input_tokens_details": {"cached_tokens": 10},
                    "output_tokens_details": {"reasoning_tokens": 2},
                },
            },
            separators=(",", ":"),
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, format: str, *args: object) -> None:
        return


class _AiderProvider(ThreadingHTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), _AiderProviderHandler)
        self.requests: list[tuple[bytes, dict[str, str]]] = []


class _AiderProviderHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers["Content-Length"])
        body = self.rfile.read(length)
        self.server.requests.append((body, dict(self.headers)))
        response = json.dumps(
            {
                "id": "chatcmpl-personal-aider",
                "object": "chat.completion",
                "created": 0,
                "model": "gpt-4.1-mini",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "OK"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 40,
                    "completion_tokens": 2,
                    "total_tokens": 42,
                },
            },
            separators=(",", ":"),
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, format: str, *args: object) -> None:
        return


class _TruncatedProvider(ThreadingHTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), _TruncatedProviderHandler)


class _TruncatedProviderHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802
        self.rfile.read(int(self.headers["Content-Length"]))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", "100")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(b"{}")
        self.wfile.flush()
        self.close_connection = True

    def log_message(self, format: str, *args: object) -> None:
        return


class PersonalContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.state = Path(self.temporary.name)
        self.state.chmod(0o700)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def profile(self, **changes) -> PersonalProfile:
        values = {
            "key": "personal-a",
            "mode": "direct",
            "agent": "codex",
            "provider": "openai",
            "endpoint": "https://api.openai.com",
            "model": "qualified-model",
            "credential_env": "OPENAI_API_KEY",
        }
        values.update(changes)
        return PersonalProfile(**values)

    def test_provider_credential_uses_distinct_secure_store_and_profile_has_no_secret(self) -> None:
        backend = _MemoryKeyring()
        credentials = ProviderCredentialStore(backend, trust_injected_backend=True)
        credentials.set("personal-a", DIRECT_TOKEN)
        self.assertEqual(credentials.get("personal-a"), DIRECT_TOKEN)

        profiles = PersonalProfileStore(self.state)
        profiles.save(self.profile())
        serialized = profiles.path_for("personal-a").read_text(encoding="utf-8")
        self.assertNotIn(DIRECT_TOKEN, serialized)
        self.assertEqual(profiles.load("personal-a"), self.profile())
        credentials.delete("personal-a")
        self.assertIsNone(credentials.get("personal-a"))

    def test_profile_save_rejects_an_unreadable_oversized_document(self) -> None:
        store = PersonalProfileStore(self.state)
        with (
            mock.patch("hormuz.personal_profiles.MAX_PROFILE_BYTES", 1),
            self.assertRaisesRegex(PersonalProfileError, "personal_profile_invalid"),
        ):
            store.save(self.profile())
        self.assertFalse(store.path_for("personal-a").exists())

    def test_profile_save_removes_the_link_after_a_post_link_failure(self) -> None:
        store = PersonalProfileStore(self.state)
        real_fsync = os.fsync
        calls = 0

        def fail_directory_fsync(descriptor: int) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("directory fsync failed")
            real_fsync(descriptor)

        with (
            mock.patch("hormuz.personal_profiles.os.fsync", side_effect=fail_directory_fsync),
            self.assertRaisesRegex(PersonalProfileError, "personal_profile_write_failed"),
        ):
            store.save(self.profile())
        self.assertFalse(store.path_for("personal-a").exists())

    def test_profile_modes_are_closed_and_managed_cannot_use_aider(self) -> None:
        store = PersonalProfileStore(self.state)
        store.save(self.profile())
        with self.assertRaisesRegex(Exception, "managed_agent_unsupported"):
            store.save(
                self.profile(
                    key="managed-aider",
                    mode="managed",
                    agent="aider",
                    provider="hormuz",
                    endpoint="https://gateway.example",
                    managed_profile="managed-source",
                    credential_env=None,
                )
            )

    def test_connect_is_create_only_and_remove_restores_transient_setup(self) -> None:
        backend = _MemoryKeyring()
        credentials = ProviderCredentialStore(backend, trust_injected_backend=True)

        def connect_args() -> argparse.Namespace:
            return argparse.Namespace(
                personal_command="connect",
                profile="personal-a",
                state_directory=self.state,
                mode="direct",
                agent="codex",
                provider="openai",
                endpoint="https://api.openai.com",
                model="qualified-model",
                credential_env="PERSONAL_TEST_PROVIDER_KEY",
                allow_loopback_http=False,
            )

        output = io.StringIO()
        errors = io.StringIO()
        with (
            mock.patch.object(
                personal_commands, "ProviderCredentialStore", return_value=credentials
            ),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(errors),
        ):
            with mock.patch.dict(
                os.environ, {"PERSONAL_TEST_PROVIDER_KEY": DIRECT_TOKEN}, clear=False
            ):
                self.assertEqual(personal_commands.run(connect_args()), 0)
            self.assertEqual(credentials.get("personal-a"), DIRECT_TOKEN)
            self.assertEqual(
                PersonalProfileStore(self.state).load("personal-a").credential_env,
                "PERSONAL_TEST_PROVIDER_KEY",
            )
            self.assertTrue(ContextPreferenceStore(self.state, "personal-a").load().enabled)

            with mock.patch.dict(
                os.environ,
                {"PERSONAL_TEST_PROVIDER_KEY": "sk-second-test-only"},
                clear=False,
            ):
                self.assertEqual(personal_commands.run(connect_args()), 1)
            self.assertEqual(credentials.get("personal-a"), DIRECT_TOKEN)

            with mock.patch.dict(
                os.environ,
                {"OTHER_PROVIDER_KEY": "sk-other-test-only"},
                clear=False,
            ):
                self.assertEqual(
                    personal_commands.run(
                        argparse.Namespace(
                            personal_command="credential",
                            profile="personal-a",
                            state_directory=self.state,
                            credential_env="OTHER_PROVIDER_KEY",
                        )
                    ),
                    2,
                )
            self.assertEqual(credentials.get("personal-a"), DIRECT_TOKEN)

            with mock.patch.dict(
                os.environ,
                {"PERSONAL_TEST_PROVIDER_KEY": "sk-second-test-only"},
                clear=False,
            ):
                self.assertEqual(
                    personal_commands.run(
                        argparse.Namespace(
                            personal_command="credential",
                            profile="personal-a",
                            state_directory=self.state,
                            credential_env="PERSONAL_TEST_PROVIDER_KEY",
                        )
                    ),
                    0,
                )
            self.assertEqual(credentials.get("personal-a"), "sk-second-test-only")

            self.assertEqual(
                personal_commands.run(
                    argparse.Namespace(
                        personal_command="off",
                        profile="personal-a",
                        state_directory=self.state,
                    )
                ),
                0,
            )
            self.assertFalse(ContextPreferenceStore(self.state, "personal-a").load().enabled)
            self.assertEqual(
                personal_commands.run(
                    argparse.Namespace(
                        personal_command="remove",
                        profile="personal-a",
                        state_directory=self.state,
                    )
                ),
                0,
            )
        self.assertIsNone(credentials.get("personal-a"))
        self.assertFalse(PersonalProfileStore(self.state).path_for("personal-a").exists())

    def test_failed_connect_restores_a_preexisting_keyring_credential(self) -> None:
        backend = _MemoryKeyring()
        credentials = ProviderCredentialStore(backend, trust_injected_backend=True)
        credentials.set("rollback-profile", "sk-preexisting-test-only")
        args = argparse.Namespace(
            personal_command="connect",
            profile="rollback-profile",
            state_directory=self.state,
            mode="direct",
            agent="codex",
            provider="openai",
            endpoint="https://api.openai.com",
            model="gpt-5.4",
            credential_env="PERSONAL_TEST_PROVIDER_KEY",
            allow_loopback_http=False,
        )
        with (
            mock.patch.object(
                personal_commands,
                "ProviderCredentialStore",
                return_value=credentials,
            ),
            mock.patch.object(
                ContextPreferenceStore,
                "save",
                side_effect=ContextRuntimeError("settings_write_failed"),
            ),
            mock.patch.dict(
                os.environ,
                {"PERSONAL_TEST_PROVIDER_KEY": "sk-new-test-only"},
                clear=False,
            ),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(personal_commands.run(args), 1)
        self.assertEqual(credentials.get("rollback-profile"), "sk-preexisting-test-only")
        self.assertFalse(
            PersonalProfileStore(self.state).path_for("rollback-profile").exists()
        )

    def test_failed_connect_restores_a_preexisting_preference(self) -> None:
        backend = _MemoryKeyring()
        credentials = ProviderCredentialStore(backend, trust_injected_backend=True)
        preference = ContextPreferenceStore(self.state, "rollback-preference")
        preference.save(True)
        args = argparse.Namespace(
            personal_command="connect",
            profile="rollback-preference",
            state_directory=self.state,
            mode="direct",
            agent="codex",
            provider="openai",
            endpoint="https://api.openai.com",
            model="gpt-5.4",
            credential_env="PERSONAL_TEST_PROVIDER_KEY",
            allow_loopback_http=False,
        )
        with (
            mock.patch.object(
                personal_commands,
                "ProviderCredentialStore",
                return_value=credentials,
            ),
            mock.patch.object(
                credentials,
                "set",
                side_effect=CredentialStoreError("credential_write_failed"),
            ),
            mock.patch.dict(
                os.environ,
                {"PERSONAL_TEST_PROVIDER_KEY": "sk-new-test-only"},
                clear=False,
            ),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(personal_commands.run(args), 1)
        self.assertTrue(preference.load().enabled)
        self.assertFalse(
            PersonalProfileStore(self.state).path_for("rollback-preference").exists()
        )

    def test_interrupted_connect_rolls_back_profile_and_credential(self) -> None:
        class InterruptingKeyring(_MemoryKeyring):
            def set_password(self, service: str, username: str, password: str) -> None:
                super().set_password(service, username, password)
                raise KeyboardInterrupt

        credentials = ProviderCredentialStore(
            InterruptingKeyring(), trust_injected_backend=True
        )
        args = argparse.Namespace(
            personal_command="connect",
            profile="interrupted-profile",
            state_directory=self.state,
            mode="direct",
            agent="codex",
            provider="openai",
            endpoint="https://api.openai.com",
            model="gpt-5.4",
            credential_env="PERSONAL_TEST_PROVIDER_KEY",
            allow_loopback_http=False,
        )
        with (
            mock.patch.object(
                personal_commands,
                "ProviderCredentialStore",
                return_value=credentials,
            ),
            mock.patch.dict(
                os.environ,
                {"PERSONAL_TEST_PROVIDER_KEY": "sk-interrupted-test-only"},
                clear=False,
            ),
        ):
            self.assertEqual(personal_commands.run(args), 130)
        self.assertIsNone(credentials.get("interrupted-profile"))
        self.assertFalse(
            PersonalProfileStore(self.state).path_for("interrupted-profile").exists()
        )

    def test_adapter_conformance_scrubs_provider_secret_and_keeps_local_tools(self) -> None:
        for name in ("codex", "claude-code", "aider"):
            with self.subTest(agent=name):
                report = conformance_report(adapter_for(name))
                self.assertTrue(report["passed"])

    def test_product_owned_qualification_fixture_passes_without_provider_calls(self) -> None:
        report = run_product_qualification()
        self.assertTrue(report["passed"])
        self.assertEqual(report["provider_calls"], 0)
        self.assertRegex(report["fixture_sha256"], r"^[0-9a-f]{64}$")
        self.assertGreaterEqual(len(report["checks"]), 9)
        self.assertEqual(report["qualification_kind"], "provider_free_contract")
        self.assertEqual(
            report["experiment_status"]["cycle_5"],
            "no_builtin_adapter_execution_yield",
        )
        self.assertTrue(
            all(
                check["scope"]
                in {"adapter_contract", "release_contract", "experimental_contract"}
                for check in report["checks"]
            )
        )

    def test_metrics_distinguish_missing_from_zero_and_report_whole_workload(self) -> None:
        store = PersonalMetricsStore(self.state, "personal-a")
        store.record_optimization(
            OptimizationMeasurement(
                eligible=False,
                applied=False,
                reason="no_eligible_result",
                before_bytes=100,
                after_bytes=100,
                before_tokens={"cl100k_base": 100, "o200k_base": 100},
                after_tokens={"cl100k_base": 100, "o200k_base": 100},
                overhead_us=8,
            )
        )
        store.record_optimization(
            OptimizationMeasurement(
                eligible=True,
                applied=True,
                reason="compacted",
                before_bytes=100,
                after_bytes=40,
                before_tokens={"cl100k_base": 100, "o200k_base": 100},
                after_tokens={"cl100k_base": 40, "o200k_base": 40},
                overhead_us=12,
            )
        )
        store.record_provider(
            ProviderMeasurement(
                succeeded=True,
                cancelled=False,
                response_bytes=20,
                time_to_first_byte_ms=5,
                total_latency_ms=9,
                usage={
                    "input_tokens": 0,
                    "output_tokens": 2,
                    "cache_read_tokens": None,
                    "cache_write_tokens": None,
                    "reasoning_tokens": 0,
                    "total_tokens": 2,
                    "actual_cost_microusd": None,
                },
            )
        )
        benefit = store.benefit(enabled=True)
        self.assertEqual(benefit["traffic"], {
            "total_requests": 2, "eligible_requests": 1, "transformed_requests": 1
        })
        self.assertEqual(
            benefit["estimated_token_reduction"]["all_captured_traffic"]["tokens"], 60
        )
        self.assertEqual(
            benefit["estimated_token_reduction"]["eligible_traffic"]["percent"], 60.0
        )
        self.assertTrue(
            benefit["estimated_token_reduction"]["all_captured_traffic"]["complete"]
        )
        self.assertEqual(
            benefit["request_byte_reduction"]["all_captured_traffic"]["bytes"], 60
        )
        self.assertEqual(benefit["provider_reported"]["input_tokens"]["total"], 0)
        self.assertEqual(benefit["provider_reported"]["total_tokens"]["total"], 2)
        self.assertEqual(benefit["provider_reported"]["cache_read_tokens"]["missing"], 1)
        self.assertEqual(
            benefit["provider_activity"],
            {"attempts": 1, "responses": 1, "failures": 0, "cancelled": 0},
        )
        self.assertIsNone(benefit["cost"]["actual_microusd"]["total"])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            personal_commands._print_document(benefit, as_json=False)
        self.assertIn("total=2", output.getvalue())
        self.assertIn("actual=unavailable microusd", output.getvalue())
        raw = store.path.read_text(encoding="utf-8")
        self.assertNotIn("prompt", raw)
        self.assertNotIn("response-test", raw)

    def test_metrics_serialize_updates_across_store_instances(self) -> None:
        workers = 16
        barrier = threading.Barrier(workers)
        failures: list[Exception] = []

        def record() -> None:
            try:
                barrier.wait(timeout=5)
                PersonalMetricsStore(self.state, "personal-a").record_session()
            except Exception as error:  # pragma: no cover - asserted below
                failures.append(error)

        threads = [threading.Thread(target=record) for _ in range(workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(failures, [])
        self.assertEqual(
            PersonalMetricsStore(self.state, "personal-a").snapshot()["counters"][
                "sessions_started"
            ],
            workers,
        )

    def test_usage_parser_handles_openai_sse_and_anthropic_json(self) -> None:
        sse = (
            b'data: {"type":"response.completed","response":{"usage":'
            b'{"input_tokens":10,"output_tokens":3,"input_tokens_details":'
            b'{"cached_tokens":4},"output_tokens_details":{"reasoning_tokens":1}}}}\n\n'
            b"data: [DONE]\n\n"
        )
        usage = extract_provider_usage(sse, "responses", "text/event-stream")
        self.assertEqual(
            (usage["input_tokens"], usage["cache_read_tokens"], usage["reasoning_tokens"]),
            (10, 4, 1),
        )
        anthropic = json.dumps({
            "usage": {"input_tokens": 12, "output_tokens": 4,
                      "cache_read_input_tokens": 5, "cache_creation_input_tokens": 2}
        }).encode()
        usage = extract_provider_usage(anthropic, "anthropic", "application/json")
        self.assertEqual((usage["cache_read_tokens"], usage["cache_write_tokens"]), (5, 2))

    def test_metrics_clear_refuses_a_replaced_symlink(self) -> None:
        store = PersonalMetricsStore(self.state, "personal-a")
        store.record_session()
        outside = self.state / "must-remain.json"
        outside.write_text("preserve", encoding="utf-8")
        store.path.unlink()
        store.path.symlink_to(outside)
        with self.assertRaisesRegex(PersonalMetricsError, "metrics_invalid"):
            store.clear()
        self.assertEqual(outside.read_text(encoding="utf-8"), "preserve")

    def test_measurement_failure_does_not_block_the_ordinary_relay_path(self) -> None:
        class FailingMetrics:
            def record_optimization(self, _measurement) -> None:
                raise PersonalMetricsError("metrics_write_failed")

        preference = ContextPreferenceStore(self.state, "personal-a")
        preference.save(True)
        optimizer = RelayOptimizer(
            preference_store=preference,
            client="codex",
            gateway_compatible=True,
            counters=COUNTERS,
            metrics=FailingMetrics(),  # type: ignore[arg-type]
        )
        paths = "".join(f"src/generated/item_{index}.py\n" for index in range(100))
        body = json.dumps(
            {
                "input": [
                    {
                        "type": "function_call",
                        "call_id": "call-1",
                        "name": "exec_command",
                        "arguments": json.dumps({"cmd": "rg --files src/generated"}),
                    },
                    {
                        "type": "function_call_output",
                        "call_id": "call-1",
                        "output": paths,
                    },
                ]
            }
        ).encode()
        changed, _headers = optimizer.prepare(body, "/v1/responses")
        self.assertNotEqual(changed, body)
        self.assertIsNone(optimizer.metrics)

    def test_claude_count_tokens_is_supported_passthrough_traffic(self) -> None:
        preference = ContextPreferenceStore(self.state, "personal-a")
        preference.save(True)
        metrics = PersonalMetricsStore(self.state, "personal-a")
        optimizer = RelayOptimizer(
            preference_store=preference,
            client="claude-code",
            gateway_compatible=True,
            counters=COUNTERS,
            metrics=metrics,
        )
        body = b'{"messages":[{"role":"user","content":"hello"}]}'
        changed, headers = optimizer.prepare(body, "/v1/messages/count_tokens")
        self.assertEqual(changed, body)
        self.assertEqual(headers, {})
        self.assertEqual(optimizer.status.code, "ready")
        benefit = metrics.benefit(enabled=True)
        self.assertEqual(benefit["traffic"]["total_requests"], 1)
        self.assertNotIn("unsupported_client", benefit["exceptions"])

    def test_optimizer_measures_the_actual_request_body_bytes(self) -> None:
        preference = ContextPreferenceStore(self.state, "personal-a")
        preference.save(True)
        metrics = PersonalMetricsStore(self.state, "personal-a")
        optimizer = RelayOptimizer(
            preference_store=preference,
            client="codex",
            gateway_compatible=True,
            counters=COUNTERS,
            metrics=metrics,
        )
        paths = "".join(
            f"src/generated/very_long_item_name_{index}.py\n"
            for index in range(100)
        )
        body = json.dumps(
            {
                "input": [
                    {
                        "type": "function_call",
                        "call_id": "call-1",
                        "name": "exec_command",
                        "arguments": json.dumps({"cmd": "rg --files src/generated"}),
                    },
                    {
                        "type": "function_call_output",
                        "call_id": "call-1",
                        "output": paths,
                    },
                ]
            },
            indent=4,
        ).encode()
        changed, _headers = optimizer.prepare(body, "/v1/responses")
        self.assertNotEqual(changed, body)
        snapshot = metrics.snapshot()
        self.assertEqual(
            snapshot["metrics"]["request_before_bytes"]["total"], len(body)
        )
        self.assertEqual(
            snapshot["metrics"]["request_after_bytes"]["total"], len(changed)
        )

    def test_public_adapter_example_preserves_non_provider_environment(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "HOME": "/tmp/example-home",
                "PATH": "/usr/bin",
                "OPENAI_API_KEY": "must-not-survive",
            },
            clear=True,
        ):
            plan = ExampleAgentAdapter().launch_plan(
                executable="example-agent",
                relay_origin="http://127.0.0.1:1234",
                local_credential=LOCAL_TOKEN,
                model="qualified-model",
            )
        self.assertEqual(plan.environment["HOME"], "/tmp/example-home")
        self.assertEqual(plan.environment["PATH"], "/usr/bin")
        self.assertNotIn("OPENAI_API_KEY", plan.environment)

    def test_managed_personal_run_uses_the_managed_client_version_pin(self) -> None:
        profile = self.profile(
            key="managed-personal",
            mode="managed",
            provider="hormuz",
            endpoint="http://127.0.0.1:9",
            allow_insecure_http=True,
            managed_profile="managed-source",
            credential_env=None,
        )
        with (
            mock.patch(
                "hormuz.personal_runtime.supported_client_executable",
                return_value="/usr/bin/true",
            ) as supported,
            mock.patch(
                "hormuz.personal_runtime.probe_gateway_capability",
                return_value=True,
            ),
        ):
            self.assertEqual(
                run_personal_client(
                    profile=profile,
                    state_directory=self.state,
                    upstream_credential=lambda: "hox_a_" + "a" * 43,
                    counters=COUNTERS,
                ),
                0,
            )
        supported.assert_called_once_with(
            "codex", expected_version=SUPPORTED_CLIENT_VERSIONS["codex"]
        )

    def test_session_measurement_failure_does_not_prevent_agent_launch(self) -> None:
        class FailingMetrics:
            def record_session(self, *, completed: bool = False) -> None:
                raise PersonalMetricsError("metrics_write_failed")

        profile = self.profile(
            key="metrics-fail-open",
            endpoint="http://127.0.0.1:9",
            allow_insecure_http=True,
        )
        with mock.patch(
            "hormuz.personal_runtime.PersonalMetricsStore",
            return_value=FailingMetrics(),
        ):
            self.assertEqual(
                run_personal_client(
                    profile=profile,
                    state_directory=self.state,
                    upstream_credential=lambda: DIRECT_TOKEN,
                    counters=COUNTERS,
                    executable="/usr/bin/true",
                ),
                0,
            )

    def test_execution_router_uses_exact_code_and_preserves_call_contract(self) -> None:
        request = MethodRequest(
            "step-1", "call-1", "json_get",
            {"document": {"items": [3, 7]}, "path": ["items", 1]},
            "read", False, False, 300,
        )
        result = ExecutionRouter().route(request)
        self.assertEqual((result.method, result.result, result.tool_call_id), ("deterministic", 7, "call-1"))
        self.assertTrue(result.model_step_avoided)
        unsafe = MethodRequest("step-2", "call-2", "json_count", [], "write", True, False)
        self.assertEqual(ExecutionRouter().route(unsafe).method, "model")

    def test_jev_requires_fixture_net_benefit_and_discloses_state_shape(self) -> None:
        def transport(_url, _headers, _body, _timeout):
            return json.dumps({
                "model": "jev-1.13.0",
                "answers": {"handler": {"type": "choice", "choice": "tests",
                                           "probabilities": {"tests": 0.91, "docs": 0.09},
                                           "confidence": 0.82}},
                "usage": {"input_tokens": 40, "output_tokens": 10},
            }).encode()

        jev = JevChoiceAdapter(
            credential="typesafe-test-key",
            qualification=JevQualification(True, 0.8, 100, 1000),
            developer_selected_provider=True,
            transport=transport,
        )
        request = MethodRequest(
            "step-1", "call-1", "handler_select", {"files": ["tests/a.py"]},
            "read", False, False, 500,
        )
        result = ExecutionRouter(jev=jev).route(
            request,
            options={"tests": "Testing work", "docs": "Documentation work"},
            instructions="Choose the supplied handler for this typed work item.",
        )
        self.assertEqual((result.method, result.result["choice"]), ("jev", "tests"))
        self.assertEqual(result.result["state_disclosure"]["top_level_fields"], ["files"])
        self.assertIsNone(result.usage["actual_cost_microusd"])

        unqualified = JevChoiceAdapter(
            credential="typesafe-test-key",
            qualification=JevQualification(False, 0.8, 100, 1000),
            developer_selected_provider=True,
            transport=transport,
        )
        self.assertEqual(
            ExecutionRouter(jev=unqualified).route(
                request, options={"tests": None, "docs": None}, instructions="Choose."
            ).method,
            "model",
        )
        with self.assertRaises(ExecutionMethodError):
            JevQualification(True, True, 100, 1000)  # type: ignore[arg-type]
        with self.assertRaises(ExecutionMethodError):
            JevChoiceAdapter(
                credential=" credential-with-space ",
                qualification=JevQualification(True, 0.8, 100, 1000),
                developer_selected_provider=True,
            )
        self.assertEqual(
            ExecutionRouter(jev=jev).route(
                request,
                options={"tests": object(), "docs": None},  # type: ignore[dict-item]
                instructions="Choose.",
            ).method,
            "model",
        )

    def test_semantic_experiment_preserves_constraints_questions_state_and_recovers(self) -> None:
        recovery = RecoveryBuffer()
        compactor = TypedToolHistoryCompactor(recovery, recent_turns=4)
        turns = (
            ConversationTurn("system", "Never write secrets.", ("Never write secrets.",)),
            ConversationTurn("user", "old resolved question " * 50),
            ConversationTurn("assistant", "old resolved answer " * 50),
            ConversationTurn("tool", "older state " * 50, tool_name="tests", tool_state="failed"),
            ConversationTurn("user", "What remains?", unresolved_question=True),
            ConversationTurn("assistant", "Investigating current state."),
            ConversationTurn("tool", "new state", tool_name="tests", tool_state="passed"),
            ConversationTurn("assistant", "Recent conclusion."),
        )
        result = compactor.compact(turns)
        self.assertGreater(result.before_bytes, result.after_bytes)
        self.assertIsNotNone(result.recovery_key)
        serialized = "\n".join(turn.content for turn in result.turns)
        self.assertIn("Never write secrets.", serialized)
        self.assertIn("What remains?", serialized)
        self.assertIn("passed", serialized)
        self.assertEqual(recovery.recover(result.recovery_key), turns)

    def test_behavior_signature_excludes_normal_loops_and_retries_only_safe_reads(self) -> None:
        tracker = BehaviorTracker()
        poll = BehaviorEvent(
            "check status", "status", "failure", "pending", "read", False, True,
            polling=True,
        )
        self.assertEqual(tracker.observe(poll).excluded_reason, "polling")
        failed = BehaviorEvent(
            "read config", "read_file", "failure", "temporarily_unavailable",
            "read", False, True,
        )
        self.assertFalse(tracker.observe(failed).possible_friction)
        signal = tracker.observe(failed)
        self.assertTrue(signal.possible_friction)
        self.assertEqual(BoundedReadRetry().decide(failed, signal).action, "retry_once")
        unsafe = BehaviorEvent(
            "publish release", "publish", "failure", "timeout", "write", True, True
        )
        unsafe_signal = tracker.observe(unsafe)
        unsafe_signal = tracker.observe(unsafe)
        self.assertEqual(BoundedReadRetry().decide(unsafe, unsafe_signal).action, "return_control")
        spoofed = FrictionSignal(
            True,
            "repeated_failed_tool_call",
            1,
            "observed_typed_events_only",
            "possible_friction_not_model_failure",
            None,
        )
        self.assertEqual(BoundedReadRetry().decide(failed, spoofed).action, "return_control")
        with self.assertRaises(PersonalOptimizationError):
            tracker.observe(
                BehaviorEvent(
                    "read config",
                    "read_file",
                    "failure",
                    "temporarily_unavailable",
                    "read",
                    False,
                    1,  # type: ignore[arg-type]
                )
            )
        with self.assertRaises(PersonalOptimizationError):
            InterventionComparison(-1, 0, 1.0, 1.0, False)

    def test_direct_relay_uses_provider_auth_records_usage_and_off_is_exact(self) -> None:
        provider = _DirectProvider()
        provider_thread = threading.Thread(target=provider.serve_forever, daemon=True)
        provider_thread.start()
        preference = ContextPreferenceStore(self.state, "personal-a")
        preference.save(True)
        metrics = PersonalMetricsStore(self.state, "personal-a")
        optimizer = RelayOptimizer(
            preference_store=preference,
            client="codex",
            gateway_compatible=True,
            counters=COUNTERS,
            metrics=metrics,
        )
        relay = LocalRelayServer(
            gateway=f"http://127.0.0.1:{provider.server_port}",
            client="codex",
            local_credential=LOCAL_TOKEN,
            gateway_credential=lambda: DIRECT_TOKEN,
            optimizer=optimizer,
            upstream_auth="openai",
            metrics=metrics,
        )
        relay_thread = threading.Thread(target=relay.serve_forever, daemon=True)
        relay_thread.start()
        try:
            paths = "".join(f"src/generated/very_long_item_name_{index}.py\n" for index in range(100))
            payload = {"model": "qualified-model", "input": [
                {"type": "function_call", "call_id": "call-1", "name": "exec_command",
                 "arguments": json.dumps({"cmd": "rg --files src/generated"})},
                {"type": "function_call_output", "call_id": "call-1", "output": paths},
            ]}
            original = json.dumps(payload, separators=(",", ":")).encode()
            connection = http.client.HTTPConnection("127.0.0.1", relay.server_port, timeout=5)
            connection.request("POST", "/v1/responses", body=original, headers={
                "Authorization": "Bearer " + LOCAL_TOKEN, "Content-Type": "application/json"
            })
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            response.read()
            connection.close()
            changed, headers = provider.requests[0]
            self.assertNotEqual(changed, original)
            normalized = {name.lower(): value for name, value in headers.items()}
            self.assertEqual(normalized["authorization"], "Bearer " + DIRECT_TOKEN)
            self.assertNotIn("x-hormuz-context-format", normalized)

            preference.save(False)
            off = b'{ "model" : "qualified-model", "input" : [] }\n'
            connection = http.client.HTTPConnection("127.0.0.1", relay.server_port, timeout=5)
            connection.request("POST", "/v1/responses", body=off, headers={
                "Authorization": "Bearer " + LOCAL_TOKEN, "Content-Type": "application/json"
            })
            response = connection.getresponse()
            response.read()
            connection.close()
            self.assertEqual(provider.requests[1][0], off)
            deadline = time.monotonic() + 2
            while (
                metrics.snapshot()["counters"]["provider_responses"] < 2
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            benefit = metrics.benefit(enabled=False)
            self.assertEqual(benefit["traffic"]["total_requests"], 2)
            self.assertEqual(benefit["provider_reported"]["cache_read_tokens"]["total"], 20)
        finally:
            relay.shutdown()
            relay_thread.join(timeout=2)
            relay.server_close()
            provider.shutdown()
            provider_thread.join(timeout=2)
            provider.server_close()

    def test_codex_model_catalog_probe_stays_local_and_requires_authentication(self) -> None:
        provider = _DirectProvider()
        provider_thread = threading.Thread(target=provider.serve_forever, daemon=True)
        provider_thread.start()
        relay = LocalRelayServer(
            gateway=f"http://127.0.0.1:{provider.server_port}",
            client="codex",
            local_credential=LOCAL_TOKEN,
            gateway_credential=lambda: DIRECT_TOKEN,
            optimizer=RelayOptimizer(
                preference_store=ContextPreferenceStore(self.state, "personal-a"),
                client="codex",
                gateway_compatible=True,
                counters=COUNTERS,
            ),
            upstream_auth="openai",
        )
        relay_thread = threading.Thread(target=relay.serve_forever, daemon=True)
        relay_thread.start()
        try:
            unauthenticated = http.client.HTTPConnection(
                "127.0.0.1", relay.server_port, timeout=5
            )
            unauthenticated.request("GET", "/v1/models?client_version=0.148.0")
            denied = unauthenticated.getresponse()
            self.assertEqual(denied.status, 401)
            denied.read()
            unauthenticated.close()

            authenticated = http.client.HTTPConnection(
                "127.0.0.1", relay.server_port, timeout=5
            )
            authenticated.request(
                "GET",
                "/v1/models?client_version=0.148.0",
                headers={"Authorization": "Bearer " + LOCAL_TOKEN},
            )
            response = authenticated.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read()), {"models": []})
            authenticated.close()
            self.assertEqual(provider.requests, [])
        finally:
            relay.shutdown()
            relay_thread.join(timeout=2)
            relay.server_close()
            provider.shutdown()
            provider_thread.join(timeout=2)
            provider.server_close()

    def test_truncated_upstream_response_records_one_failed_provider_attempt(self) -> None:
        provider = _TruncatedProvider()
        provider_thread = threading.Thread(target=provider.serve_forever, daemon=True)
        provider_thread.start()
        metrics = PersonalMetricsStore(self.state, "personal-a")
        preference = ContextPreferenceStore(self.state, "personal-a")
        preference.save(False)
        relay = LocalRelayServer(
            gateway=f"http://127.0.0.1:{provider.server_port}",
            client="codex",
            local_credential=LOCAL_TOKEN,
            gateway_credential=lambda: DIRECT_TOKEN,
            optimizer=RelayOptimizer(
                preference_store=preference,
                client="codex",
                gateway_compatible=True,
                counters=COUNTERS,
                metrics=metrics,
            ),
            upstream_auth="openai",
            metrics=metrics,
        )
        relay_thread = threading.Thread(target=relay.serve_forever, daemon=True)
        relay_thread.start()
        try:
            connection = http.client.HTTPConnection(
                "127.0.0.1", relay.server_port, timeout=5
            )
            connection.request(
                "POST",
                "/v1/responses",
                body=b'{"model":"gpt-5.4","input":[]}',
                headers={
                    "Authorization": "Bearer " + LOCAL_TOKEN,
                    "Content-Type": "application/json",
                },
            )
            response = connection.getresponse()
            try:
                response.read()
            except http.client.IncompleteRead:
                pass
            connection.close()
            snapshot = metrics.snapshot()
            self.assertEqual(snapshot["counters"]["provider_attempts"], 1)
            self.assertEqual(snapshot["counters"]["provider_responses"], 1)
            self.assertEqual(snapshot["counters"]["provider_failures"], 1)
        finally:
            relay.shutdown()
            relay_thread.join(timeout=2)
            relay.server_close()
            provider.shutdown()
            provider_thread.join(timeout=2)
            provider.server_close()

    def test_truncated_local_upload_records_no_provider_attempt(self) -> None:
        metrics = PersonalMetricsStore(self.state, "personal-a")
        preference = ContextPreferenceStore(self.state, "personal-a")
        preference.save(False)
        relay = LocalRelayServer(
            gateway="http://127.0.0.1:9",
            client="codex",
            local_credential=LOCAL_TOKEN,
            gateway_credential=lambda: DIRECT_TOKEN,
            optimizer=RelayOptimizer(
                preference_store=preference,
                client="codex",
                gateway_compatible=True,
                counters=COUNTERS,
                metrics=metrics,
            ),
            upstream_auth="openai",
            metrics=metrics,
        )
        relay_thread = threading.Thread(target=relay.serve_forever, daemon=True)
        relay_thread.start()
        client = socket.create_connection(("127.0.0.1", relay.server_port), timeout=2)
        try:
            request = (
                "POST /v1/responses HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{relay.server_port}\r\n"
                f"Authorization: Bearer {LOCAL_TOKEN}\r\n"
                "Content-Type: application/json\r\n"
                "Content-Length: 100\r\n"
                "\r\n"
                "{}"
            ).encode()
            client.sendall(request)
            client.shutdown(socket.SHUT_WR)
            self.assertEqual(client.recv(4096), b"")
            snapshot = metrics.snapshot()
            self.assertEqual(snapshot["counters"]["provider_attempts"], 0)
            self.assertEqual(snapshot["counters"]["provider_failures"], 0)
        finally:
            client.close()
            relay.shutdown()
            relay_thread.join(timeout=2)
            relay.server_close()

    def test_complete_personal_journey_uses_transient_codex_configuration(self) -> None:
        provider = _DirectProvider()
        provider_thread = threading.Thread(target=provider.serve_forever, daemon=True)
        provider_thread.start()
        backend = _MemoryKeyring()
        credentials = ProviderCredentialStore(backend, trust_injected_backend=True)
        sentinel = self.state / "existing-codex-config.toml"
        sentinel.write_text("model = 'developer-owned'\n", encoding="utf-8")
        sentinel.chmod(0o600)
        executable = self.state / "codex"
        executable.write_text(
            """#!/usr/bin/env python3
import http.client, json, os, re, sys, urllib.parse
if 'PERSONAL_TEST_PROVIDER_KEY' in os.environ:
    raise SystemExit(2)
provider = next(value for value in sys.argv if value.startswith('model_providers.hormuz_context_relay='))
origin = re.search(r'base_url=\"([^\"]+)/v1\"', provider).group(1)
url = urllib.parse.urlsplit(origin)
paths = ''.join(f'src/generated/very_long_item_name_{index}.py\\n' for index in range(100))
payload = {'model': 'qualified-model', 'input': [
    {'type': 'function_call', 'call_id': 'call-1', 'name': 'exec_command',
     'arguments': json.dumps({'cmd': 'rg --files src/generated'})},
    {'type': 'function_call_output', 'call_id': 'call-1', 'output': paths},
]}
body = json.dumps(payload, separators=(',', ':')).encode()
connection = http.client.HTTPConnection(url.hostname, url.port, timeout=5)
connection.request('POST', '/v1/responses', body=body, headers={
    'Authorization': 'Bearer ' + os.environ['HORMUZ_LOCAL_RELAY_TOKEN'],
    'Content-Type': 'application/json',
})
response = connection.getresponse()
response.read()
raise SystemExit(0 if response.status == 200 else 1)
""",
            encoding="utf-8",
        )
        executable.chmod(0o700)
        paths = "".join(
            f"src/generated/very_long_item_name_{index}.py\n" for index in range(100)
        )
        original = json.dumps(
            {
                "model": "qualified-model",
                "input": [
                    {
                        "type": "function_call",
                        "call_id": "call-1",
                        "name": "exec_command",
                        "arguments": json.dumps({"cmd": "rg --files src/generated"}),
                    },
                    {
                        "type": "function_call_output",
                        "call_id": "call-1",
                        "output": paths,
                    },
                ],
            },
            separators=(",", ":"),
        ).encode()

        def qualified_run(**kwargs):
            return run_personal_client(
                **kwargs, counters=COUNTERS, executable=str(executable)
            )

        connect = argparse.Namespace(
            personal_command="connect",
            profile="journey-codex",
            state_directory=self.state,
            mode="direct",
            agent="codex",
            provider="openai",
            endpoint=f"http://127.0.0.1:{provider.server_port}",
            model="qualified-model",
            credential_env="PERSONAL_TEST_PROVIDER_KEY",
            allow_loopback_http=True,
        )
        output = io.StringIO()
        errors = io.StringIO()
        try:
            with (
                mock.patch.object(
                    personal_commands, "ProviderCredentialStore", return_value=credentials
                ),
                mock.patch.object(
                    personal_commands, "run_personal_client", side_effect=qualified_run
                ),
                mock.patch.dict(
                    os.environ, {"PERSONAL_TEST_PROVIDER_KEY": DIRECT_TOKEN}, clear=False
                ),
                contextlib.redirect_stdout(output),
                contextlib.redirect_stderr(errors),
            ):
                self.assertEqual(personal_commands.run(connect), 0)
                os.environ["PERSONAL_TEST_PROVIDER_KEY"] = "sk-new-shell-value-test-only"
                command = lambda name: argparse.Namespace(
                    personal_command=name,
                    profile="journey-codex",
                    state_directory=self.state,
                )
                self.assertEqual(personal_commands.run(command("run")), 0)
                preference = ContextPreferenceStore(self.state, "journey-codex")
                benefit = PersonalMetricsStore(
                    self.state, "journey-codex"
                ).benefit(enabled=preference.load().enabled)
                self.assertEqual(benefit["traffic"]["transformed_requests"], 1)
                self.assertNotEqual(provider.requests[0][0], original)
                self.assertEqual(personal_commands.run(command("off")), 0)
                self.assertEqual(personal_commands.run(command("run")), 0)
                self.assertEqual(provider.requests[1][0], original)
                self.assertEqual(personal_commands.run(command("remove")), 0)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "model = 'developer-owned'\n")
            self.assertIsNone(credentials.get("journey-codex"))
        finally:
            provider.shutdown()
            provider_thread.join(timeout=2)
            provider.server_close()

    def test_aider_normal_work_reports_zero_structural_eligibility(self) -> None:
        provider = _DirectProvider()
        provider_thread = threading.Thread(target=provider.serve_forever, daemon=True)
        provider_thread.start()
        executable = self.state / "aider"
        executable.write_text(
            """#!/usr/bin/env python3
import http.client, json, os, urllib.parse
url = urllib.parse.urlsplit(os.environ['OPENAI_API_BASE'])
body = json.dumps({'model': 'qualified-model', 'messages': [
    {'role': 'user', 'content': 'ordinary work'}
]}, separators=(',', ':')).encode()
connection = http.client.HTTPConnection(url.hostname, url.port, timeout=5)
connection.request('POST', url.path + '/chat/completions', body=body, headers={
    'Authorization': 'Bearer ' + os.environ['OPENAI_API_KEY'],
    'Content-Type': 'application/json',
})
response = connection.getresponse()
response.read()
raise SystemExit(0 if response.status == 200 else 1)
""",
            encoding="utf-8",
        )
        executable.chmod(0o700)
        profile = self.profile(
            key="journey-aider", agent="aider", model="qualified-model",
            endpoint=f"http://127.0.0.1:{provider.server_port}",
            allow_insecure_http=True,
        )
        PersonalProfileStore(self.state).save(profile)
        ContextPreferenceStore(self.state, profile.key).save(True)
        try:
            self.assertEqual(
                run_personal_client(
                    profile=profile,
                    state_directory=self.state,
                    upstream_credential=lambda: DIRECT_TOKEN,
                    counters=COUNTERS,
                    executable=str(executable),
                ),
                0,
            )
            benefit = PersonalMetricsStore(self.state, profile.key).benefit(enabled=True)
            self.assertEqual(
                benefit["traffic"],
                {"total_requests": 1, "eligible_requests": 0, "transformed_requests": 0},
            )
            self.assertEqual(len(provider.requests), 1)
            headers = {name.lower(): value for name, value in provider.requests[0][1].items()}
            self.assertEqual(headers["authorization"], "Bearer " + DIRECT_TOKEN)
        finally:
            provider.shutdown()
            provider_thread.join(timeout=2)
            provider.server_close()

    @unittest.skipUnless(
        os.environ.get("HORMUZ_RUN_PERSONAL_AIDER_TEST") == "1" and shutil.which("aider"),
        "Set HORMUZ_RUN_PERSONAL_AIDER_TEST=1 and install Aider 0.86.2",
    )
    def test_official_aider_0862_completes_direct_personal_interaction(self) -> None:
        real_aider = shutil.which("aider")
        assert real_aider is not None
        provider = _AiderProvider()
        provider_thread = threading.Thread(target=provider.serve_forever, daemon=True)
        provider_thread.start()
        root = self.state / "actual-aider-root"
        root.mkdir()
        executable_directory = self.state / "actual-aider-bin"
        executable_directory.mkdir()
        wrapper = executable_directory / "aider"
        wrapper.write_text(
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            f"real = {real_aider!r}\n"
            "if sys.argv[1:] == ['--version']:\n"
            "    os.execv(real, [real, '--version'])\n"
            f"root = {str(root)!r}\n"
            "os.chdir(root)\n"
            "arguments = [real, *sys.argv[1:], '--message', 'Reply exactly OK.', "
            "'--no-git', '--no-stream', '--no-pretty', '--yes-always']\n"
            "os.execv(real, arguments)\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o700)
        profile = self.profile(
            key="actual-aider-0862",
            agent="aider",
            model="gpt-4.1-mini",
            endpoint=f"http://127.0.0.1:{provider.server_port}",
            allow_insecure_http=True,
        )
        PersonalProfileStore(self.state).save(profile)
        ContextPreferenceStore(self.state, profile.key).save(True)
        prior_path = os.environ.get("PATH")
        os.environ["PATH"] = str(executable_directory) + (
            ":" + prior_path if prior_path else ""
        )
        try:
            self.assertEqual(
                run_personal_client(
                    profile=profile,
                    state_directory=self.state,
                    upstream_credential=lambda: DIRECT_TOKEN,
                    counters=COUNTERS,
                ),
                0,
            )
            self.assertGreaterEqual(len(provider.requests), 1)
            payload = json.loads(provider.requests[-1][0])
            self.assertTrue(payload.get("messages"))
            headers = {
                name.lower(): value for name, value in provider.requests[-1][1].items()
            }
            self.assertEqual(headers["authorization"], "Bearer " + DIRECT_TOKEN)
            benefit = PersonalMetricsStore(self.state, profile.key).benefit(enabled=True)
            self.assertGreaterEqual(benefit["traffic"]["total_requests"], 1)
            self.assertEqual(benefit["traffic"]["eligible_requests"], 0)
            self.assertEqual(benefit["traffic"]["transformed_requests"], 0)
        finally:
            if prior_path is None:
                os.environ.pop("PATH", None)
            else:
                os.environ["PATH"] = prior_path
            provider.shutdown()
            provider_thread.join(timeout=2)
            provider.server_close()

    @unittest.skipUnless(
        os.environ.get("HORMUZ_RUN_PERSONAL_CODEX_TEST") == "1" and shutil.which("codex"),
        "Set HORMUZ_RUN_PERSONAL_CODEX_TEST=1 and install Codex CLI 0.148.0",
    )
    def test_official_codex_0148_completes_direct_personal_interaction(self) -> None:
        from tests.test_client_relay import _CodexToolGateway

        real_codex = shutil.which("codex")
        assert real_codex is not None
        provider = _CodexToolGateway()
        provider_thread = threading.Thread(target=provider.serve_forever, daemon=True)
        provider_thread.start()
        root = self.state / "actual-codex-root"
        generated = root / "generated" / "structural_context_repetition_for_hormuz"
        generated.mkdir(parents=True)
        for index in range(48):
            (generated / f"file_{index:03}.py").write_text(
                "# personal optimizer probe\n", encoding="utf-8"
            )
        executable_directory = self.state / "actual-codex-bin"
        executable_directory.mkdir()
        wrapper = executable_directory / "codex"
        wrapper.write_text(
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            f"real = {real_codex!r}\n"
            "if sys.argv[1:] == ['--version']:\n"
            "    os.execv(real, [real, '--version'])\n"
            f"root = {str(root)!r}\n"
            "arguments = [real, 'exec', '--ignore-user-config', '--skip-git-repo-check', "
            "'--ephemeral', '--dangerously-bypass-approvals-and-sandbox', '-C', root, "
            "*sys.argv[1:], 'Call exec_command with exactly `rg --files generated`, then finish.']\n"
            "os.execv(real, arguments)\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o700)
        profile = self.profile(
            key="actual-codex-0148",
            endpoint=f"http://127.0.0.1:{provider.server_port}",
            allow_insecure_http=True,
            model="gpt-5.4",
        )
        PersonalProfileStore(self.state).save(profile)
        ContextPreferenceStore(self.state, profile.key).save(True)
        prior_path = os.environ.get("PATH")
        os.environ["PATH"] = str(executable_directory) + (
            ":" + prior_path if prior_path else ""
        )
        try:
            self.assertEqual(
                run_personal_client(
                    profile=profile,
                    state_directory=self.state,
                    upstream_credential=lambda: DIRECT_TOKEN,
                    counters=COUNTERS,
                ),
                0,
            )
            output_requests = [
                (json.loads(body), headers)
                for body, headers in provider.requests
                if any(
                    isinstance(item, dict)
                    and item.get("type") == "function_call_output"
                    for item in json.loads(body).get("input", [])
                )
            ]
            self.assertEqual(len(output_requests), 1)
            payload, headers = output_requests[0]
            compact = next(
                item["output"]
                for item in payload["input"]
                if item.get("type") == "function_call_output"
            )
            self.assertIn("hormuz-path-list-v1", compact)
            normalized = {name.lower(): value for name, value in headers.items()}
            self.assertEqual(normalized["authorization"], "Bearer " + DIRECT_TOKEN)
            self.assertNotIn("x-hormuz-context-format", normalized)
            benefit = PersonalMetricsStore(self.state, profile.key).benefit(enabled=True)
            self.assertGreaterEqual(benefit["traffic"]["transformed_requests"], 1)
        finally:
            if prior_path is None:
                os.environ.pop("PATH", None)
            else:
                os.environ["PATH"] = prior_path
            provider.shutdown()
            provider_thread.join(timeout=2)
            provider.server_close()


if __name__ == "__main__":
    unittest.main()
