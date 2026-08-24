from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = ROOT / "tools" / "verify_live_client_conformance.py"
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "client_conformance" / "valid-v1.json"
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "live-client-conformance.yml"
_SPEC = importlib.util.spec_from_file_location("verify_live_client_conformance", TOOL_PATH)
assert _SPEC is not None and _SPEC.loader is not None
live = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = live
_SPEC.loader.exec_module(live)


class LiveClientConformanceTests(unittest.TestCase):
    def _fixture(self) -> dict[str, object]:
        return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_strict_metadata_fixture_is_valid_and_content_free(self) -> None:
        value = self._fixture()

        live.validate_evidence(value)
        live._assert_content_free(value, forbidden_values=("provider-key-never-persist",))

        complete = copy.deepcopy(value)
        anthropic = copy.deepcopy(complete["results"][0])
        anthropic.update(
            {
                "provider": "anthropic",
                "client": "claude-code",
                "client_version": "2.1.233",
                "requested_model": "claude-test",
                "routed_model": "claude-test",
                "provider_reported_model": "claude-test-20260824",
                "actor_id": "live-claude-reviewer",
            }
        )
        complete["results"].append(anthropic)
        complete["scope"] = "complete"
        live.validate_evidence(complete)

    def test_evidence_rejects_unknown_fields_and_content(self) -> None:
        value = self._fixture()
        value["prompt"] = "must never be retained"
        with self.assertRaisesRegex(live.LiveClientConformanceError, "evidence_schema_invalid"):
            live.validate_evidence(value)

        value = self._fixture()
        value["results"][0]["provider_request_id"] = "req_private"  # type: ignore[index]
        with self.assertRaisesRegex(live.LiveClientConformanceError, "evidence_schema_invalid"):
            live.validate_evidence(value)

        with self.assertRaisesRegex(live.LiveClientConformanceError, "content_entered_evidence"):
            live._assert_content_free(self._fixture(), forbidden_values=("gpt-test",))

    def test_client_environment_removes_provider_credentials_and_ambient_auth(self) -> None:
        provider_key = "provider-secret-value"
        environment = live._sanitized_client_environment(
            {
                "PATH": "/bin",
                "OPENAI_API_KEY": provider_key,
                "ANTHROPIC_API_KEY": "another-provider-key",
                "CUSTOM_PROVIDER_KEY": provider_key,
                "EMBEDDED_PROVIDER_KEY": f"prefix-{provider_key}-suffix",
                "SAFE": "visible",
            },
            provider_credentials=(provider_key, "another-provider-key"),
            credential_names=("CUSTOM_PROVIDER_KEY",),
        )

        self.assertEqual(environment, {"PATH": "/bin", "SAFE": "visible"})
        live._assert_client_environment_isolated(
            environment,
            provider_credentials=(provider_key, "another-provider-key"),
            credential_names=("CUSTOM_PROVIDER_KEY",),
        )
        with self.assertRaisesRegex(
            live.LiveClientConformanceError,
            "provider_credential_entered_client_environment",
        ):
            live._assert_client_environment_isolated(
                {"SAFE_LOOKING": f"prefix-{provider_key}-suffix"},
                provider_credentials=(provider_key,),
                credential_names=("CUSTOM_PROVIDER_KEY",),
            )

    def test_client_failure_output_is_reduced_to_a_fixed_content_free_code(self) -> None:
        self.assertEqual(
            live._client_failure_code("codex", "private prompt omitted; HTTP 429 rate_limit_error"),
            "codex_provider_rate_limited",
        )
        self.assertEqual(
            live._client_failure_code("claude-code", "private response omitted; model does not exist"),
            "claude_code_model_unavailable",
        )
        self.assertEqual(
            live._client_failure_code("codex", "arbitrary private output"),
            "codex_request_failed",
        )

    def test_credential_file_requires_private_regular_file_and_never_evaluates_shell(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "credentials.env"
            path.write_text("OPENAI_API_KEY=$(not-a-command)\n", encoding="utf-8")
            os.chmod(path, 0o600)

            self.assertEqual(live._read_credential_env_file(path), {"OPENAI_API_KEY": "$(not-a-command)"})

            os.chmod(path, 0o644)
            with self.assertRaisesRegex(live.LiveClientConformanceError, "credential_file_unsafe"):
                live._read_credential_env_file(path)

    def test_private_evidence_writer_uses_mode_0600(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "evidence.json"
            live._write_private_json(path, self._fixture())

            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), self._fixture())
            with self.assertRaisesRegex(live.LiveClientConformanceError, "evidence_path_exists"):
                live._write_private_json(path, {"replacement": "must-not-overwrite"})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), self._fixture())

    def test_source_revision_requires_exact_clean_head(self) -> None:
        head = SimpleNamespace(returncode=0, stdout="a" * 40 + "\n", stderr="")
        clean = SimpleNamespace(returncode=0, stdout="", stderr="")
        with mock.patch.object(live.subprocess, "run", side_effect=(head, clean)):
            self.assertEqual(live._source_revision("a" * 40), "a" * 40)

        with mock.patch.object(live.subprocess, "run", side_effect=(head, clean)):
            with self.assertRaisesRegex(live.LiveClientConformanceError, "source_revision_mismatch"):
                live._source_revision("b" * 40)

        dirty = SimpleNamespace(returncode=0, stdout="?? untracked.txt\n", stderr="")
        with mock.patch.object(live.subprocess, "run", side_effect=(head, dirty)):
            with self.assertRaisesRegex(live.LiveClientConformanceError, "source_worktree_dirty"):
                live._source_revision("a" * 40)

    def test_codex_runtime_resolves_the_native_executable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            node_modules = Path(temporary) / "node_modules"
            entrypoint = node_modules / "@openai" / "codex" / "bin" / "codex.js"
            runtime = (
                node_modules
                / "@openai"
                / "codex-linux-x64"
                / "vendor"
                / "x86_64-unknown-linux-musl"
                / "bin"
                / "codex"
            )
            entrypoint.parent.mkdir(parents=True)
            runtime.parent.mkdir(parents=True)
            entrypoint.write_text("#!/usr/bin/env node\n", encoding="utf-8")
            runtime.write_bytes(b"native-codex")
            runtime.chmod(0o700)

            self.assertEqual(live._client_runtime(entrypoint, "codex"), runtime.resolve())
            self.assertEqual(live._client_runtime(runtime, "claude-code"), runtime)

    def test_generated_gateway_config_loads_with_server_only_credentials(self) -> None:
        specs = (
            live._ClientSpec(
                provider="openai",
                client="codex",
                protocol="openai",
                model="gpt-test",
                executable=Path(sys.executable),
                runtime=Path(sys.executable),
                expected_version="0.147.0",
                identity_env="HORMUZ_LIVE_CODEX_IDENTITY_TOKEN",
                identity_token="codex-identity-token",
                actor_id="live-codex-reviewer",
                marker="HORMUZ_CODEX_LIVE_OK",
                synthetic_secret="sk-proj-secret-not-retained",
            ),
            live._ClientSpec(
                provider="anthropic",
                client="claude-code",
                protocol="anthropic",
                model="claude-test",
                executable=Path(sys.executable),
                runtime=Path(sys.executable),
                expected_version="2.1.233",
                identity_env="HORMUZ_LIVE_CLAUDE_IDENTITY_TOKEN",
                identity_token="claude-identity-token",
                actor_id="live-claude-reviewer",
                marker="HORMUZ_CLAUDE_LIVE_OK",
                synthetic_secret="sk-ant-secret-not-retained",
            ),
        )
        environment = {
            "HORMUZ_LIVE_CODEX_IDENTITY_TOKEN": specs[0].identity_token,
            "HORMUZ_LIVE_CLAUDE_IDENTITY_TOKEN": specs[1].identity_token,
            "HORMUZ_LIVE_RUNTIME_OPENAI_CREDENTIAL": "openai-provider-key",
            "HORMUZ_LIVE_RUNTIME_ANTHROPIC_CREDENTIAL": "anthropic-provider-key",
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "hormuz.json"
            path.write_text(json.dumps(live._gateway_config(specs)), encoding="utf-8")

            config = live.GatewayConfig.load(path, environ=environment)

        self.assertEqual(config.model_routes["gpt-test"].protocol, "openai")
        self.assertEqual(config.model_routes["claude-test"].protocol, "anthropic")
        self.assertEqual(config.identities_by_actor["live-codex-reviewer"].authentication_source, "static")

    def test_provider_result_requires_live_pre_egress_and_content_free_audit_evidence(self) -> None:
        spec = live._ClientSpec(
            provider="openai",
            client="codex",
            protocol="openai",
            model="gpt-test",
            executable=Path(sys.executable),
            runtime=Path(sys.executable),
            expected_version="0.147.0",
            identity_env="HORMUZ_LIVE_CODEX_IDENTITY_TOKEN",
            identity_token="identity-token-not-retained",
            actor_id="live-codex-reviewer",
            marker="HORMUZ_CODEX_LIVE_OK",
            synthetic_secret="sk-proj-secret-not-retained",
        )
        usage = {
            "schema_id": "hormuz.audit-event",
            "schema_version": 2,
            "event_type": "usage",
            "id": "usage-1",
            "occurred_at": "2026-08-24T20:00:00+00:00",
            "organization_id": "hormuz-live-client-conformance",
            "actor_id": "live-codex-reviewer",
            "actor_name": "Live Client Reviewer",
            "team_id": "release-conformance",
            "team_name": "Release Conformance",
            "identity_type": "human",
            "authentication_source": "static",
            "client": "codex",
            "protocol": "openai",
            "requested_model": "gpt-test",
            "resolved_alias": "gpt-test",
            "routed_model": "gpt-test",
            "provider_reported_model": "gpt-test-2026-08-24",
            "policy_version": "local-config-0123456789abcdef",
            "policy_action": "allowed+redacted",
            "status": "succeeded",
            "input_tokens": 30,
            "output_tokens": 6,
            "cache_read_tokens": 8,
            "cache_write_tokens": 0,
            "reasoning_tokens": 2,
            "cost_microusd": 42,
            "cost_basis": "configured_rate_card_estimate",
            "allocation_basis": "direct_gateway_request",
            "coverage": "gateway_captured_requests_only",
            "provider_request_id": "req_not_exported",
            "redaction_count": 1,
            "redaction_rules": ["openai_api_key"],
        }
        security = {
            "schema_id": "hormuz.audit-event",
            "schema_version": 2,
            "event_type": "security.secret",
            "id": "security-1",
            "occurred_at": "2026-08-24T20:00:00+00:00",
            "organization_id": "hormuz-live-client-conformance",
            "actor_id": "live-codex-reviewer",
            "actor_name": "Live Client Reviewer",
            "team_id": "release-conformance",
            "team_name": "Release Conformance",
            "identity_type": "human",
            "authentication_source": "static",
            "client": "codex",
            "protocol": "openai",
            "requested_model": "gpt-test",
            "policy_version": "local-config-0123456789abcdef",
            "coverage": "gateway_captured_requests_only",
            "action": "redacted",
            "detection_count": 1,
            "rules": ["openai_api_key"],
        }
        gateway = SimpleNamespace(
            store=mock.Mock(audit_events=mock.Mock(return_value=[usage, security])),
            _live_conformance_observations=[
                live._EgressObservation(
                    protocol="openai",
                    client="codex",
                    model="gpt-test",
                    streaming=True,
                    output_limit=64,
                    secret_absent=True,
                    redaction_marker_present=True,
                )
            ],
        )

        result = live._provider_result(gateway, spec)

        self.assertTrue(result["provider_request_id_present"])
        self.assertEqual(result["policy_action"], ["allowed+redacted"])
        self.assertNotIn("provider_request_id", result)

    def test_missing_acknowledgement_fails_before_credentials_or_clients(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            status = live.main(
                [
                    "--provider",
                    "openai",
                    "--openai-model",
                    "gpt-test",
                    "--evidence-out",
                    "unused.json",
                ]
            )

        self.assertEqual(status, 1)
        self.assertEqual(
            stderr.getvalue(),
            "live_client_conformance=failed code=acknowledgement_required\n",
        )

    def test_live_workflow_is_manual_least_privilege_and_exactly_pinned(self) -> None:
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

        self.assertIn("  workflow_dispatch:", workflow)
        self.assertNotIn("  pull_request:", workflow)
        self.assertNotIn("  push:", workflow)
        self.assertNotIn("  schedule:", workflow)
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertIn("environment: live-provider-conformance", workflow)
        self.assertIn('CODEX_VERSION: "0.147.0"', workflow)
        self.assertIn('CLAUDE_CODE_VERSION: "2.1.233"', workflow)
        self.assertEqual(workflow.count("secrets.HORMUZ_LIVE_OPENAI_PROVIDER_KEY"), 1)
        self.assertEqual(workflow.count("secrets.HORMUZ_LIVE_ANTHROPIC_PROVIDER_KEY"), 1)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertIn("retention-days: 7", workflow)


if __name__ == "__main__":
    unittest.main()
