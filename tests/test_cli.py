from __future__ import annotations

import argparse
import io
import json
import os
import signal
import stat
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
    _audit_anchor,
    _audit_chain,
    _audit_export,
    _audit_since,
    _auth_token,
    _budget_for_scope,
    _client_config,
    _normalize_command_argv,
    _serve,
    _status,
    _write_audit_chain_checkpoint,
    build_parser,
    main,
)
from hormuz.config import (
    AuditAnchorConfig,
    AuditChainConfig,
    ConfigError,
    GatewayConfig,
    Identity,
    KeyCustodyConfig,
    OIDCIssuerConfig,
)
from hormuz.contracts import validate_contract
from hormuz.custody import AuditAnchorReceipt, CustodyError, parse_audit_anchor_artifact
from hormuz.audit_chain import (
    build_audit_chain_checkpoint,
    parse_audit_chain_checkpoint,
    serialize_audit_chain_checkpoint,
)
from hormuz.policy_document import PolicyDocument
from hormuz.policy_repository import PolicyActivation, PolicyHistory, PolicyLifecycleEvent, PolicyVersionRecord
from hormuz.store import MonthlyTotals, UsageStore


ROOT = Path(__file__).resolve().parents[1]


class _RecordingAuditAnchor:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def anchor(self, artifact: bytes, **kwargs: object) -> AuditAnchorReceipt:
        self.calls.append({"artifact": artifact, **kwargs})
        return AuditAnchorReceipt(
            backend="test-anchor",
            artifact_id=kwargs["artifact_id"],  # type: ignore[arg-type]
            artifact_sha256="a" * 64,
            head_digest=kwargs["head_digest"],  # type: ignore[arg-type]
            object_version="version-1",
        )


class ServeSignalTests(unittest.TestCase):
    def test_sigterm_marks_drain_and_dispatches_blocking_shutdown_to_a_helper(self) -> None:
        config = GatewayConfig.load(
            ROOT / "config.example.json",
            environ={"HORMUZ_TOKEN": "test-identity-token"},
        )
        server = mock.Mock()
        server.upstream_credentials = {}
        handlers: dict[int, object] = {}
        shutdown_thread = mock.Mock()

        def register_handler(signum: int, handler: object) -> None:
            handlers[signum] = handler

        def serve_forever() -> None:
            handler = handlers[signal.SIGTERM]
            handler(signal.SIGTERM, None)  # type: ignore[operator]
            handler(signal.SIGTERM, None)  # type: ignore[operator]

        server.serve_forever.side_effect = serve_forever
        with (
            mock.patch("hormuz.cli.GatewayServer", return_value=server),
            mock.patch("hormuz.cli.signal.signal", side_effect=register_handler),
            mock.patch("hormuz.cli.threading.Thread", return_value=shutdown_thread) as thread,
        ):
            self.assertEqual(_serve(config), 0)

        self.assertEqual(server.begin_drain.call_count, 2)
        thread.assert_called_once_with(target=server.shutdown, name="hormuz-sigterm-shutdown", daemon=True)
        shutdown_thread.start.assert_called_once_with()
        server.shutdown.assert_not_called()
        server.server_close.assert_called_once_with()


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

    def test_claude_configuration_uses_gateway_bearer_token(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            result = _client_config(self.config, "claude", "https://hormuz.example")

        self.assertEqual(result, 0)
        self.assertIn("ANTHROPIC_BASE_URL=https://hormuz.example", output.getvalue())
        self.assertIn('ANTHROPIC_AUTH_TOKEN="${HORMUZ_TOKEN}"', output.getvalue())

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

        claude = io.StringIO()
        with redirect_stdout(claude):
            result = _client_config(
                config,
                "claude",
                "https://hormuz.example",
                actor_id="alice",
                auth_mode="oidc",
                credential_env="COMPANY_OIDC_TOKEN",
            )
        self.assertEqual(result, 0)
        self.assertIn('"ANTHROPIC_BASE_URL": "https://hormuz.example"', claude.getvalue())
        self.assertIn('"CLAUDE_CODE_API_KEY_HELPER_TTL_MS": "300000"', claude.getvalue())
        self.assertIn("hormuz auth token --env COMPANY_OIDC_TOKEN", claude.getvalue())
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", claude.getvalue())

    def test_auth_token_prints_only_a_valid_environment_credential(self) -> None:
        output = io.StringIO()
        with mock.patch.dict(os.environ, {"COMPANY_TOKEN": "header.payload.signature"}):
            with redirect_stdout(output):
                self.assertEqual(_auth_token("COMPANY_TOKEN"), 0)
        self.assertEqual(output.getvalue(), "header.payload.signature\n")

        with mock.patch.dict(os.environ, {}, clear=True):
            with redirect_stderr(io.StringIO()):
                self.assertEqual(_auth_token("COMPANY_TOKEN"), 1)

    def test_client_config_rejects_configuration_injection_urls(self) -> None:
        with self.assertRaises(ConfigError):
            _client_config(self.config, "codex", 'https://hormuz.example/"\nmodel="attacker"')

    def test_status_accepts_dimension_and_scope_filters(self) -> None:
        args = build_parser().parse_args(
            ["status", "--group-by", "model", "--team", "engineering", "--actor", "alice", "--json"]
        )

        self.assertEqual(args.group_by, "model")
        self.assertEqual(args.team, "engineering")
        self.assertEqual(args.actor, "alice")
        self.assertTrue(args.json)

    def test_usage_storage_configuration_is_safe_and_storage_cli_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_value = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
            config_value["database"] = str(root / "usage.sqlite3")
            config_value["usage_storage"] = {
                "backend": "postgresql",
                "postgres_dsn_env": "COMPANY_POSTGRES_RUNTIME_DSN",
                "postgres_migration_dsn_env": "COMPANY_POSTGRES_MIGRATION_DSN",
                "postgres_schema": "company_hormuz",
                "postgres_runtime_role": "company_hormuz_runtime",
                "postgres_pool": {
                    "min_connections": 2,
                    "max_connections": 6,
                    "acquire_timeout_seconds": 4,
                    "max_waiting": 12,
                    "max_lifetime_seconds": 1800,
                    "max_idle_seconds": 120,
                },
            }
            config_path = root / "postgres.json"
            config_path.write_text(json.dumps(config_value), encoding="utf-8")
            config = GatewayConfig.load(
                config_path,
                environ={"HORMUZ_TOKEN": "test-identity-token"},
            )
            self.assertEqual(config.usage_storage.backend, "postgresql")
            self.assertEqual(config.usage_storage.postgres_schema, "company_hormuz")
            self.assertEqual(config.usage_storage.postgres_pool.min_connections, 2)
            self.assertEqual(config.usage_storage.postgres_pool.max_connections, 6)
            self.assertEqual(config.usage_storage.postgres_pool.max_waiting, 12)
            self.assertNotIn("postgresql://", config_path.read_text(encoding="utf-8"))

            stderr = io.StringIO()
            with mock.patch.dict(os.environ, {"HORMUZ_TOKEN": "test-identity-token"}, clear=True):
                with redirect_stderr(stderr):
                    self.assertEqual(main(["--config", str(config_path), "storage", "verify"]), 2)
            self.assertEqual(stderr.getvalue(), "storage error: postgres_dsn_unavailable\n")

            config_value["usage_storage"]["postgres_schema"] = "company;drop"
            config_path.write_text(json.dumps(config_value), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "safe PostgreSQL identifier"):
                GatewayConfig.load(config_path, environ={"HORMUZ_TOKEN": "test-identity-token"})

            config_value["usage_storage"]["postgres_schema"] = "company_hormuz"
            config_value["usage_storage"]["postgres_pool"]["min_connections"] = 7
            config_value["usage_storage"]["postgres_pool"]["max_connections"] = 6
            config_path.write_text(json.dumps(config_value), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "must not exceed"):
                GatewayConfig.load(config_path, environ={"HORMUZ_TOKEN": "test-identity-token"})

            config_value["usage_storage"]["postgres_pool"] = {"max_waiting": 0}
            config_path.write_text(json.dumps(config_value), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "max_waiting"):
                GatewayConfig.load(config_path, environ={"HORMUZ_TOKEN": "test-identity-token"})

            config_value["usage_storage"] = {"backend": "postgresql", "postgres_dsn": "literal-secret"}
            config_path.write_text(json.dumps(config_value), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "configuration_unsupported_fields"):
                GatewayConfig.load(config_path, environ={"HORMUZ_TOKEN": "test-identity-token"})

            config_value["usage_storage"] = {
                "backend": "postgresql",
                "postgres_dsn_env": "COMPANY_POSTGRES_DSN",
                "postgres_migration_dsn_env": "COMPANY_POSTGRES_DSN",
            }
            config_path.write_text(json.dumps(config_value), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "separate credentials"):
                GatewayConfig.load(config_path, environ={"HORMUZ_TOKEN": "test-identity-token"})

            config_value["usage_storage"] = {"backend": "sqlite", "postgres_pool": {}}
            config_path.write_text(json.dumps(config_value), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "requires usage_storage.backend postgresql"):
                GatewayConfig.load(config_path, environ={"HORMUZ_TOKEN": "test-identity-token"})

    def test_sqlite_storage_cli_verifies_and_migrates_without_postgres_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_value = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
            config_value["database"] = str(root / "usage.sqlite3")
            config_path = root / "sqlite.json"
            config_path.write_text(json.dumps(config_value), encoding="utf-8")
            with mock.patch.dict(os.environ, {"HORMUZ_TOKEN": "test-identity-token"}, clear=True):
                verify = io.StringIO()
                with redirect_stdout(verify):
                    self.assertEqual(main(["--config", str(config_path), "storage", "verify"]), 0)
                self.assertEqual(verify.getvalue(), "usage storage verified: sqlite\n")

                migrate = io.StringIO()
                with redirect_stdout(migrate):
                    self.assertEqual(main(["--config", str(config_path), "storage", "migrate"]), 0)
                self.assertEqual(migrate.getvalue(), "SQLite usage storage migration is current\n")

    def test_contract_manifest_requires_no_configuration(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["contract", "manifest"]), 0)
        manifest = json.loads(output.getvalue())
        self.assertEqual(manifest["schema_id"], "hormuz.policy-evidence-manifest")
        self.assertEqual(manifest["schema_version"], 1)

    def test_hidden_hyphenated_commands_normalize_to_the_spaced_tree(self) -> None:
        aliases = (
            (["contract-manifest"], ["contract", "manifest"]),
            (["policy-check"], ["policy", "check"]),
            (["client-config"], ["client", "config"]),
            (["audit-export"], ["audit", "export"]),
            (["audit-anchor"], ["audit", "anchor"]),
            (["audit-chain"], ["audit", "chain"]),
            (["custody-executor"], ["custody", "executor"]),
            (
                ["policy", "break-glass", "recover"],
                ["policy", "recover"],
            ),
            (
                ["policy", "administrator", "revoke-static"],
                ["policy", "administrator", "retire", "static"],
            ),
            (
                ["custody", "administrator", "revoke-static"],
                ["custody", "administrator", "retire", "static"],
            ),
            (
                ["custody", "evidence", "deletion-check"],
                ["custody", "evidence", "deletion", "check"],
            ),
            (
                ["custody", "executor", "register-assets"],
                ["custody", "executor", "register", "assets"],
            ),
        )
        for legacy, primary in aliases:
            with self.subTest(legacy=legacy):
                self.assertEqual(
                    _normalize_command_argv(["--config", "hormuz.json", *legacy]),
                    ["--config", "hormuz.json", *primary],
                )

    def test_every_primary_command_token_is_a_separate_unhyphenated_word(self) -> None:
        pending = [build_parser()]
        paths: list[tuple[str, ...]] = [()]
        while pending:
            parser = pending.pop()
            prefix = paths.pop()
            for action in parser._actions:
                if not isinstance(action, argparse._SubParsersAction):
                    continue
                for command, child in action.choices.items():
                    path = (*prefix, command)
                    with self.subTest(command=" ".join(path)):
                        self.assertNotIn("-", command)
                    pending.append(child)
                    paths.append(path)

    def test_status_json_uses_the_versioned_usage_report_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = replace(self.config, database_path=Path(temporary) / "usage.sqlite3")
            identity = next(iter(config.identities_by_token.values()))
            UsageStore(config.database_path).record(
                identity=identity,
                client="codex",
                protocol="openai",
                requested_model="gpt-5.4-mini",
                resolved_alias="gpt-5.4-mini",
                upstream_model="gpt-5.4-mini",
                policy_action="allowed",
                status="succeeded",
                input_tokens=10,
                output_tokens=2,
            )
            args = argparse.Namespace(group_by="person", actor=None, team=None, json=True)
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(_status(config, args), 0)
            report = json.loads(output.getvalue())
            validate_contract(report)
        self.assertEqual(report["schema_id"], "hormuz.usage-report")
        self.assertEqual(report["rows"][0]["scope_id"], identity.actor_id)

    def test_local_policy_version_excludes_static_credential_value(self) -> None:
        first = GatewayConfig.load(
            ROOT / "config.example.json",
            environ={"HORMUZ_TOKEN": "first-static-credential"},
        )
        second = GatewayConfig.load(
            ROOT / "config.example.json",
            environ={"HORMUZ_TOKEN": "second-static-credential"},
        )
        self.assertEqual(first.policy_version, second.policy_version)
        self.assertNotIn("first-static-credential", first.policy_version)

    def test_policy_validate_is_local_and_reports_the_immutable_version(self) -> None:
        output = io.StringIO()
        fixture = ROOT / "tests" / "fixtures" / "policies" / "policy-document-v1.json"
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch("hormuz.cli.GatewayConfig.load") as runtime_load,
            mock.patch("hormuz.cli.PolicyControlService") as service,
            redirect_stdout(output),
        ):
            result = main(
                [
                    "--config",
                    str(ROOT / "config.example.json"),
                    "policy",
                    "validate",
                    str(fixture),
                ]
            )

        self.assertEqual(result, 0)
        runtime_load.assert_not_called()
        service.assert_not_called()
        self.assertRegex(
            output.getvalue(),
            r"^policy valid: organization=xpounder version=sha256:[0-9a-f]{64} teams=1 actors=0\n$",
        )

    def test_policy_validate_reports_actionable_content_safe_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "invalid-policy.json"
            value = json.loads(
                (ROOT / "tests" / "fixtures" / "policies" / "policy-document-v1.json").read_text(
                    encoding="utf-8"
                )
            )
            value["policies"]["teams"] = {
                "do-not-echo-sensitive-team": {"allowed_models": "do-not-echo-sensitive-value"}
            }
            path.write_text(json.dumps(value), encoding="utf-8")

            stderr = io.StringIO()
            with (
                mock.patch.dict(os.environ, {}, clear=True),
                redirect_stderr(stderr),
            ):
                result = main(
                    [
                        "--config",
                        str(ROOT / "config.example.json"),
                        "policy",
                        "validate",
                        str(path),
                    ]
                )

        self.assertEqual(result, 2)
        self.assertIn("policy validation failed: policy_document_invalid", stderr.getvalue())
        self.assertIn("reason: policies.teams.*.allowed_models must be an array", stderr.getvalue())
        self.assertNotIn("do-not-echo-sensitive-team", stderr.getvalue())
        self.assertNotIn("do-not-echo-sensitive-value", stderr.getvalue())

    def test_policy_validate_explains_an_unreadable_path(self) -> None:
        stderr = io.StringIO()
        missing = ROOT / "missing-policy-document.json"
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            redirect_stderr(stderr),
        ):
            result = main(
                [
                    "--config",
                    str(ROOT / "config.example.json"),
                    "policy",
                    "validate",
                    str(missing),
                ]
            )

        self.assertEqual(result, 2)
        self.assertIn("policy validation failed: policy_document_unavailable", stderr.getvalue())
        self.assertIn("hint: Check that the path exists", stderr.getvalue())
        self.assertNotIn(str(missing), stderr.getvalue())

    def test_policy_templates_is_stable_and_requires_no_configuration(self) -> None:
        output = io.StringIO()
        with (
            mock.patch("hormuz.cli.GatewayConfig.load") as runtime_load,
            mock.patch("hormuz.cli.GatewayConfig.load_policy_validation_context") as context_load,
            redirect_stdout(output),
        ):
            self.assertEqual(main(["policy", "templates"]), 0)

        runtime_load.assert_not_called()
        context_load.assert_not_called()
        self.assertEqual(
            output.getvalue(),
            "Available policy templates:\n"
            "  standard  Balanced daily-use policy using configured clients and models, "
            "secret redaction, and a 16,000-token output cap.\n"
            "  strict    Conservative policy using configured clients and models, secret denial, "
            "and a 4,000-token output cap.\n"
            "  lockdown  Emergency deny-all policy with empty client and model allowlists and "
            "secret denial.\n",
        )

    def test_policy_show_history_and_export_use_stable_admin_surfaces(self) -> None:
        context = GatewayConfig.load_policy_validation_context(ROOT / "config.example.json")
        document = PolicyDocument.from_json_bytes(
            (ROOT / "tests" / "fixtures" / "policies" / "policy-document-v1.json").read_bytes(),
            config=context,
        )
        identity_key = "static:" + "a" * 64
        created_at = datetime(2026, 8, 27, tzinfo=timezone.utc)
        version = PolicyVersionRecord(
            organization_id="xpounder",
            version_id=document.version_id,
            content_sha256=document.content_sha256,
            created_at=created_at,
            author_kind="static",
            author_identity_key=identity_key,
            change_summary=document.redacted_change_summary(),
            document=document,
        )
        history = PolicyHistory(
            organization_id="xpounder",
            limit=1,
            has_more=True,
            events=(
                PolicyLifecycleEvent(
                    organization_id="xpounder",
                    event_type="policy_activated",
                    version_id=document.version_id,
                    content_sha256=document.content_sha256,
                    occurred_at=created_at,
                    actor_kind="static",
                    actor_identity_key=identity_key,
                    generation=1,
                    change_summary=document.redacted_change_summary(),
                ),
            ),
        )
        with (
            tempfile.TemporaryDirectory() as temporary,
            mock.patch("hormuz.cli.GatewayConfig.load", return_value=mock.sentinel.config),
            mock.patch("hormuz.cli.PolicyControlService") as service_type,
        ):
            service = service_type.return_value
            service.policy_version.return_value = version
            service.history.return_value = history

            show_output = io.StringIO()
            with redirect_stdout(show_output):
                self.assertEqual(
                    main(
                        [
                            "policy",
                            "show",
                            "--organization",
                            "xpounder",
                            "--version",
                            document.version_id,
                        ]
                    ),
                    0,
                )
            self.assertEqual(json.loads(show_output.getvalue()), document.to_mapping())
            service.policy_version.assert_called_with(
                organization_id="xpounder",
                credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
                version_id=document.version_id,
            )

            history_output = io.StringIO()
            with redirect_stdout(history_output):
                self.assertEqual(
                    main(["policy", "history", "--organization", "xpounder", "--limit", "1", "--json"]),
                    0,
                )
            history_contract = json.loads(history_output.getvalue())
            validate_contract(history_contract)
            self.assertEqual(history_contract["schema_id"], "hormuz.policy-history")
            self.assertEqual(history_contract["events"][0]["event_type"], "policy_activated")
            self.assertTrue(history_contract["has_more"])
            service.history.assert_called_once_with(
                organization_id="xpounder",
                credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
                limit=1,
            )

            export_path = Path(temporary) / "active-policy.json"
            export_output = io.StringIO()
            with redirect_stdout(export_output):
                self.assertEqual(
                    main(
                        [
                            "policy",
                            "export",
                            "--organization",
                            "xpounder",
                            "--output",
                            str(export_path),
                        ]
                    ),
                    0,
                )
            self.assertEqual(os.stat(export_path).st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(export_path.read_text(encoding="utf-8")), document.to_mapping())
            self.assertIn(f"policy exported: organization=xpounder version={document.version_id}", export_output.getvalue())
            service.policy_version.assert_called_with(
                organization_id="xpounder",
                credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
                version_id=None,
            )

            stderr = io.StringIO()
            with redirect_stderr(stderr):
                self.assertEqual(
                    main(
                        [
                            "policy",
                            "export",
                            "--organization",
                            "xpounder",
                            "--output",
                            str(export_path),
                        ]
                    ),
                    2,
                )
            self.assertIn("policy export failed: policy_output_exists", stderr.getvalue())

    def test_policy_apply_and_rollback_expose_guarded_intuitive_commands(self) -> None:
        active_version = "sha256:" + "a" * 64
        candidate_version = "sha256:" + "b" * 64
        activation = PolicyActivation(
            organization_id="xpounder",
            version_id=candidate_version,
            generation=7,
            activated_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
            activated_by_kind="static",
            activated_by_identity_key="static:" + "c" * 64,
            action="policy_activated",
        )
        with (
            mock.patch("hormuz.cli.GatewayConfig.load", return_value=mock.sentinel.config),
            mock.patch("hormuz.cli.PolicyControlService") as service_type,
        ):
            service = service_type.return_value
            service.apply.return_value = activation
            service.activate.return_value = activation
            service.rollback.return_value = activation

            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(
                    main(
                        [
                            "policy",
                            "apply",
                            "candidate.json",
                            "--organization",
                            "xpounder",
                            "--if-active",
                            active_version,
                        ]
                    ),
                    0,
                )
            self.assertIn(f"policy applied: organization=xpounder version={candidate_version}", output.getvalue())
            service.apply.assert_called_once_with(
                organization_id="xpounder",
                credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
                policy_path="candidate.json",
                if_active_version_id=active_version,
            )

            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    main(
                        [
                            "policy",
                            "rollback",
                            "--organization",
                            "xpounder",
                            "--if-active",
                            candidate_version,
                        ]
                    ),
                    0,
                )
            service.rollback.assert_called_once_with(
                organization_id="xpounder",
                credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
                version_id=None,
                if_active_version_id=candidate_version,
            )

            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    main(
                        [
                            "policy",
                            "activate",
                            "--organization",
                            "xpounder",
                            "--version",
                            candidate_version,
                            "--if-active",
                            active_version,
                        ]
                    ),
                    0,
                )
            service.activate.assert_called_once_with(
                organization_id="xpounder",
                credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
                version_id=candidate_version,
                if_active_version_id=active_version,
            )

    def test_policy_compare_emits_semantic_contract_and_uses_document_exit_codes(self) -> None:
        context = GatewayConfig.load_policy_validation_context(ROOT / "config.example.json")
        fixture = json.loads(
            (ROOT / "tests" / "fixtures" / "policies" / "policy-document-v1.json").read_text(
                encoding="utf-8"
            )
        )
        baseline_document = PolicyDocument.from_mapping(fixture, config=context)
        candidate_mapping = json.loads(json.dumps(fixture))
        candidate_mapping["policies"]["organization"]["max_output_tokens"] = 4_000
        candidate_mapping["policies"]["teams"]["0team"] = {"allowed_clients": ["codex"]}
        candidate_mapping["policies"]["teams"]["alpha"] = {"allowed_clients": ["codex"]}
        candidate_document = PolicyDocument.from_mapping(candidate_mapping, config=context)
        created_at = datetime(2026, 8, 27, tzinfo=timezone.utc)
        baseline = PolicyVersionRecord(
            organization_id="xpounder",
            version_id=baseline_document.version_id,
            content_sha256=baseline_document.content_sha256,
            created_at=created_at,
            author_kind="static",
            author_identity_key="static:" + "a" * 64,
            change_summary=baseline_document.redacted_change_summary(),
            document=baseline_document,
        )

        with (
            tempfile.TemporaryDirectory() as temporary,
            mock.patch("hormuz.cli.GatewayConfig.load", return_value=self.config),
            mock.patch("hormuz.cli.PolicyControlService") as service_type,
        ):
            candidate_path = Path(temporary) / "candidate.json"
            candidate_path.write_text(json.dumps(candidate_mapping), encoding="utf-8")
            service_type.return_value.policy_version.return_value = baseline
            output = io.StringIO()
            with redirect_stdout(output):
                result = main(
                    [
                        "policy",
                        "compare",
                        str(candidate_path),
                        "--organization",
                        "xpounder",
                        "--json",
                    ]
                )

        self.assertEqual(result, 1)
        comparison = json.loads(output.getvalue())
        validate_contract(comparison)
        self.assertEqual(comparison["schema_id"], "hormuz.policy-comparison")
        self.assertEqual(comparison["baseline"]["version_id"], baseline_document.version_id)
        self.assertEqual(comparison["candidate"]["version_id"], candidate_document.version_id)
        self.assertEqual(
            comparison["changes"],
            [
                {
                    "after": 4_000,
                    "before": 32_000,
                    "change_type": "changed",
                    "path": "policies.organization.max_output_tokens",
                },
                {
                    "after": ["codex"],
                    "before": None,
                    "change_type": "added",
                    "path": "policies.teams.alpha.allowed_clients",
                },
                {
                    "after": ["codex"],
                    "before": None,
                    "change_type": "added",
                    "path": 'policies.teams["0team"].allowed_clients',
                },
            ],
        )
        service_type.return_value.policy_version.assert_called_once_with(
            organization_id="xpounder",
            credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
            version_id=None,
        )

    def test_policy_compare_returns_zero_for_reordered_allowlists(self) -> None:
        context = GatewayConfig.load_policy_validation_context(ROOT / "config.example.json")
        fixture = json.loads(
            (ROOT / "tests" / "fixtures" / "policies" / "policy-document-v1.json").read_text(
                encoding="utf-8"
            )
        )
        baseline_document = PolicyDocument.from_mapping(fixture, config=context)
        candidate_mapping = json.loads(json.dumps(fixture))
        candidate_mapping["policies"]["organization"]["allowed_clients"].reverse()
        candidate_mapping["policies"]["organization"]["allowed_models"].reverse()
        candidate_document = PolicyDocument.from_mapping(candidate_mapping, config=context)
        baseline = PolicyVersionRecord(
            organization_id="xpounder",
            version_id=baseline_document.version_id,
            content_sha256=baseline_document.content_sha256,
            created_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
            author_kind="static",
            author_identity_key="static:" + "a" * 64,
            change_summary=baseline_document.redacted_change_summary(),
            document=baseline_document,
        )

        with (
            tempfile.TemporaryDirectory() as temporary,
            mock.patch("hormuz.cli.GatewayConfig.load", return_value=self.config),
            mock.patch("hormuz.cli.PolicyControlService") as service_type,
        ):
            candidate_path = Path(temporary) / "candidate.json"
            candidate_path.write_text(json.dumps(candidate_mapping), encoding="utf-8")
            service_type.return_value.policy_version.return_value = baseline
            output = io.StringIO()
            with redirect_stdout(output):
                result = main(
                    [
                        "policy",
                        "compare",
                        str(candidate_path),
                        "--organization",
                        "xpounder",
                        "--json",
                    ]
                )

        self.assertEqual(result, 0)
        comparison = json.loads(output.getvalue())
        validate_contract(comparison)
        self.assertTrue(comparison["identical"])
        self.assertEqual(comparison["changes"], [])
        self.assertNotEqual(candidate_document.version_id, baseline_document.version_id)
        self.assertEqual(comparison["candidate"]["version_id"], candidate_document.version_id)

    def test_policy_preview_pins_active_before_saved_candidate_and_denies_with_three(self) -> None:
        context = GatewayConfig.load_policy_validation_context(ROOT / "config.example.json")
        fixture = json.loads(
            (ROOT / "tests" / "fixtures" / "policies" / "policy-document-v1.json").read_text(
                encoding="utf-8"
            )
        )
        baseline_document = PolicyDocument.from_mapping(fixture, config=context)
        candidate_mapping = json.loads(json.dumps(fixture))
        candidate_mapping["policies"]["actors"]["alice"] = {"allowed_models": []}
        candidate_document = PolicyDocument.from_mapping(candidate_mapping, config=context)
        created_at = datetime(2026, 8, 27, tzinfo=timezone.utc)

        def version(document: PolicyDocument) -> PolicyVersionRecord:
            return PolicyVersionRecord(
                organization_id="xpounder",
                version_id=document.version_id,
                content_sha256=document.content_sha256,
                created_at=created_at,
                author_kind="static",
                author_identity_key="static:" + "a" * 64,
                change_summary=document.redacted_change_summary(),
                document=document,
            )

        usage_store = mock.Mock()
        usage_store.monthly_totals.return_value = MonthlyTotals()
        with (
            mock.patch("hormuz.cli.GatewayConfig.load", return_value=self.config),
            mock.patch("hormuz.cli.PolicyControlService") as service_type,
            mock.patch("hormuz.cli.create_usage_store", return_value=usage_store) as create_store,
        ):
            service = service_type.return_value
            service.policy_version.side_effect = [version(baseline_document), version(candidate_document)]
            output = io.StringIO()
            with redirect_stdout(output):
                result = main(
                    [
                        "policy",
                        "preview",
                        "--version",
                        candidate_document.version_id,
                        "--organization",
                        "xpounder",
                        "--actor",
                        "alice",
                        "--client",
                        "codex",
                        "--protocol",
                        "openai",
                        "--model",
                        "gpt-5.4-mini",
                        "--max-output-tokens",
                        "1000",
                        "--json",
                    ]
                )

        self.assertEqual(result, 3)
        preview = json.loads(output.getvalue())
        validate_contract(preview)
        self.assertEqual(preview["schema_id"], "hormuz.policy-preview")
        self.assertEqual(preview["usage_basis"], "current")
        self.assertTrue(preview["baseline"]["decision"]["allowed"])
        self.assertFalse(preview["candidate"]["decision"]["allowed"])
        self.assertEqual(
            service.policy_version.call_args_list,
            [
                mock.call(
                    organization_id="xpounder",
                    credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
                    version_id=None,
                ),
                mock.call(
                    organization_id="xpounder",
                    credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
                    version_id=candidate_document.version_id,
                ),
            ],
        )
        create_store.assert_called_once_with(self.config, read_only=True)
        self.assertEqual(usage_store.monthly_totals.call_count, 3)

    def test_policy_scenarios_create_add_and_validate_are_offline_and_canonical(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            suite_path = Path(temporary) / "scenarios.json"
            with (
                mock.patch("hormuz.cli.GatewayConfig.load") as runtime_load,
                mock.patch("hormuz.cli.GatewayConfig.load_policy_validation_context") as context_load,
                mock.patch("hormuz.cli.PolicyControlService") as service,
            ):
                created = io.StringIO()
                with redirect_stdout(created):
                    self.assertEqual(
                        main(
                            [
                                "--config",
                                str(Path(temporary) / "missing-config.json"),
                                "policy",
                                "scenarios",
                                "create",
                                "--organization",
                                "xpounder",
                                "--id",
                                "z-large",
                                "--actor",
                                "alice",
                                "--client",
                                "codex",
                                "--protocol",
                                "openai",
                                "--model",
                                "gpt-5.4-mini",
                                "--max-output-tokens",
                                "20000",
                                "--output",
                                str(suite_path),
                            ]
                        ),
                        0,
                    )
                added = io.StringIO()
                with redirect_stdout(added):
                    self.assertEqual(
                        main(
                            [
                                "policy",
                                "scenarios",
                                "add",
                                str(suite_path),
                                "--id",
                                "a-default",
                                "--actor",
                                "alice",
                                "--client",
                                "codex",
                                "--protocol",
                                "openai",
                                "--model",
                                "gpt-5.4-mini",
                                "--max-output-tokens",
                                "1000",
                            ]
                        ),
                        0,
                    )
                validated = io.StringIO()
                with redirect_stdout(validated):
                    self.assertEqual(
                        main(["policy", "scenarios", "validate", str(suite_path)]),
                        0,
                    )

            runtime_load.assert_not_called()
            context_load.assert_not_called()
            service.assert_not_called()
            self.assertEqual(stat.S_IMODE(suite_path.stat().st_mode), 0o600)
            suite = json.loads(suite_path.read_text(encoding="utf-8"))
            validate_contract(suite)
            self.assertEqual(suite["schema_id"], "hormuz.policy-scenario-suite")
            self.assertEqual(
                [scenario["id"] for scenario in suite["scenarios"]],
                ["a-default", "z-large"],
            )
            self.assertIn("scenarios=1", created.getvalue())
            self.assertIn("scenarios=2", added.getvalue())
            self.assertIn("policy scenarios valid", validated.getvalue())

    def test_policy_evaluate_pins_versions_emits_contract_and_securely_saves_it(self) -> None:
        context = GatewayConfig.load_policy_validation_context(ROOT / "config.example.json")
        fixture = json.loads(
            (ROOT / "tests" / "fixtures" / "policies" / "policy-document-v1.json").read_text(
                encoding="utf-8"
            )
        )
        baseline_document = PolicyDocument.from_mapping(fixture, config=context)
        candidate_mapping = json.loads(json.dumps(fixture))
        candidate_mapping["policies"]["actors"]["alice"] = {"allowed_models": []}
        candidate_document = PolicyDocument.from_mapping(candidate_mapping, config=context)
        created_at = datetime(2026, 8, 27, tzinfo=timezone.utc)

        def version(document: PolicyDocument) -> PolicyVersionRecord:
            return PolicyVersionRecord(
                organization_id="xpounder",
                version_id=document.version_id,
                content_sha256=document.content_sha256,
                created_at=created_at,
                author_kind="static",
                author_identity_key="static:" + "a" * 64,
                change_summary=document.redacted_change_summary(),
                document=document,
            )

        usage_store = mock.Mock()
        usage_store.monthly_totals.return_value = MonthlyTotals()
        with (
            tempfile.TemporaryDirectory() as temporary,
            mock.patch("hormuz.cli.GatewayConfig.load", return_value=self.config),
            mock.patch("hormuz.cli.PolicyControlService") as service_type,
            mock.patch("hormuz.cli.create_usage_store", return_value=usage_store) as create_store,
        ):
            suite_path = Path(temporary) / "scenarios.json"
            suite_path.write_text(
                json.dumps(
                    {
                        "schema_id": "hormuz.policy-scenario-suite",
                        "schema_version": 1,
                        "organization_id": "xpounder",
                        "scenarios": [
                            {
                                "id": "codex-default",
                                "actor_id": "alice",
                                "client": "codex",
                                "protocol": "openai",
                                "requested_model": "gpt-5.4-mini",
                                "requested_output_tokens": 1000,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            result_path = Path(temporary) / "evaluation.json"
            service = service_type.return_value
            service.policy_version.side_effect = [version(baseline_document), version(candidate_document)]
            output = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(output), redirect_stderr(stderr):
                result = main(
                    [
                        "policy",
                        "evaluate",
                        "--version",
                        candidate_document.version_id,
                        "--organization",
                        "xpounder",
                        "--scenarios",
                        str(suite_path),
                        "--output",
                        str(result_path),
                        "--json",
                    ]
                )

            self.assertEqual(result, 1)
            evaluation = json.loads(output.getvalue())
            validate_contract(evaluation)
            self.assertEqual(evaluation, json.loads(result_path.read_text(encoding="utf-8")))
            self.assertEqual(stat.S_IMODE(result_path.stat().st_mode), 0o600)
            self.assertEqual(evaluation["schema_id"], "hormuz.policy-evaluation")
            self.assertEqual(evaluation["summary"]["changed_count"], 1)
            self.assertTrue(evaluation["scenarios"][0]["baseline"]["decision"]["allowed"])
            self.assertFalse(evaluation["scenarios"][0]["candidate"]["decision"]["allowed"])
            self.assertIn("policy evaluation saved:", stderr.getvalue())
            self.assertEqual(
                service.policy_version.call_args_list,
                [
                    mock.call(
                        organization_id="xpounder",
                        credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
                        version_id=None,
                    ),
                    mock.call(
                        organization_id="xpounder",
                        credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
                        version_id=candidate_document.version_id,
                    ),
                ],
            )
            create_store.assert_called_once_with(self.config, read_only=True)
            self.assertEqual(usage_store.monthly_totals.call_count, 3)

    def test_policy_evaluate_returns_zero_when_behavior_is_unchanged(self) -> None:
        context = GatewayConfig.load_policy_validation_context(ROOT / "config.example.json")
        document = PolicyDocument.from_json_bytes(
            (ROOT / "tests" / "fixtures" / "policies" / "policy-document-v1.json").read_bytes(),
            config=context,
        )
        version = PolicyVersionRecord(
            organization_id="xpounder",
            version_id=document.version_id,
            content_sha256=document.content_sha256,
            created_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
            author_kind="static",
            author_identity_key="static:" + "a" * 64,
            change_summary=document.redacted_change_summary(),
            document=document,
        )
        usage_store = mock.Mock()
        usage_store.monthly_totals.return_value = MonthlyTotals()
        with (
            tempfile.TemporaryDirectory() as temporary,
            mock.patch("hormuz.cli.GatewayConfig.load", return_value=self.config),
            mock.patch("hormuz.cli.PolicyControlService") as service_type,
            mock.patch("hormuz.cli.create_usage_store", return_value=usage_store),
        ):
            suite_path = Path(temporary) / "scenarios.json"
            suite_path.write_text(
                json.dumps(
                    {
                        "schema_id": "hormuz.policy-scenario-suite",
                        "schema_version": 1,
                        "organization_id": "xpounder",
                        "scenarios": [
                            {
                                "id": "same",
                                "actor_id": "alice",
                                "client": "codex",
                                "protocol": "openai",
                                "requested_model": "gpt-5.4-mini",
                                "requested_output_tokens": None,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            service_type.return_value.policy_version.side_effect = [version, version]
            with redirect_stdout(io.StringIO()):
                result = main(
                    [
                        "policy",
                        "evaluate",
                        "--version",
                        document.version_id,
                        "--organization",
                        "xpounder",
                        "--scenarios",
                        str(suite_path),
                    ]
                )

        self.assertEqual(result, 0)

    def test_legacy_policy_check_output_remains_byte_for_byte_compatible(self) -> None:
        usage_store = mock.Mock()
        usage_store.monthly_totals.return_value = MonthlyTotals()
        request = [
            "--actor",
            "alice",
            "--client",
            "codex",
            "--protocol",
            "openai",
            "--model",
            "gpt-5.4-mini",
            "--max-output-tokens",
            "1000",
        ]
        with (
            mock.patch("hormuz.cli.GatewayConfig.load", return_value=self.config),
            mock.patch("hormuz.cli.create_usage_store", return_value=usage_store),
        ):
            legacy = io.StringIO()
            with redirect_stdout(legacy):
                legacy_result = main(["policy-check", *request])
            primary = io.StringIO()
            with redirect_stdout(primary):
                primary_result = main(["policy", "check", *request])

        self.assertEqual(legacy_result, primary_result)
        self.assertEqual(legacy.getvalue(), primary.getvalue())
        validate_contract(json.loads(legacy.getvalue()))
        self.assertEqual(json.loads(legacy.getvalue())["schema_id"], "hormuz.policy-decision")

    def test_policy_create_is_offline_private_and_matches_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output_path = Path(temporary) / "standard.json"
            output = io.StringIO()
            with (
                mock.patch.dict(os.environ, {}, clear=True),
                mock.patch("hormuz.cli.GatewayConfig.load") as runtime_load,
                mock.patch("hormuz.cli.PolicyControlService") as service,
                redirect_stdout(output),
            ):
                result = main(
                    [
                        "--config",
                        str(ROOT / "config.example.json"),
                        "policy",
                        "create",
                        "--output",
                        str(output_path),
                    ]
                )

            self.assertEqual(result, 0)
            runtime_load.assert_not_called()
            service.assert_not_called()
            self.assertEqual(os.stat(output_path).st_mode & 0o777, 0o600)
            context = GatewayConfig.load_policy_validation_context(ROOT / "config.example.json")
            document = PolicyDocument.from_json_bytes(output_path.read_bytes(), config=context)
            self.assertEqual(document.organization_id, "xpounder")
            self.assertEqual(document.organization_policy.allowed_clients, ("claude-code", "codex"))
            self.assertEqual(
                document.organization_policy.allowed_models,
                ("claude-opus-5", "claude-sonnet-5", "gpt-5.4", "gpt-5.4-mini", "gpt-5.5"),
            )
            self.assertEqual(document.organization_policy.max_output_tokens, 16_000)
            self.assertIsNone(document.organization_policy.monthly_budget_usd)
            self.assertIsNone(document.organization_policy.fallback_models)
            self.assertEqual(document.secret_mode, "redact")
            self.assertIn(f"template=standard organization=xpounder version={document.version_id}", output.getvalue())

            validation = io.StringIO()
            with (
                mock.patch.dict(os.environ, {}, clear=True),
                redirect_stdout(validation),
            ):
                self.assertEqual(
                    main(
                        [
                            "--config",
                            str(ROOT / "config.example.json"),
                            "policy",
                            "validate",
                            str(output_path),
                        ]
                    ),
                    0,
                )
            self.assertIn(document.version_id, validation.getvalue())

    def test_policy_create_requires_explicit_tenant_for_multitenant_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_value = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
            second_identity = dict(config_value["identities"][0])
            second_identity.update(
                {
                    "token_env": "SECOND_ORGANIZATION_TOKEN",
                    "actor_id": "bob",
                    "actor_name": "Bob Example",
                    "team_id": "operations",
                    "team_name": "Operations",
                    "organization_id": "second-organization",
                    "allowed_clients": ["claude-code"],
                }
            )
            config_value["identities"].append(second_identity)
            config_path = root / "multitenant.json"
            config_path.write_text(json.dumps(config_value), encoding="utf-8")
            output_path = root / "policy.json"

            stderr = io.StringIO()
            with mock.patch.dict(os.environ, {}, clear=True), redirect_stderr(stderr):
                result = main(
                    [
                        "--config",
                        str(config_path),
                        "policy",
                        "create",
                        "--output",
                        str(output_path),
                    ]
                )
            self.assertEqual(result, 2)
            self.assertIn("policy creation failed: policy_template_organization_required", stderr.getvalue())
            self.assertIn("hint: Pass --organization", stderr.getvalue())
            self.assertNotIn("xpounder", stderr.getvalue())
            self.assertNotIn("second-organization", stderr.getvalue())
            self.assertFalse(output_path.exists())

            with mock.patch.dict(os.environ, {}, clear=True), redirect_stdout(io.StringIO()):
                self.assertEqual(
                    main(
                        [
                            "--config",
                            str(config_path),
                            "policy",
                            "create",
                            "--organization",
                            "second-organization",
                            "--output",
                            str(output_path),
                        ]
                    ),
                    0,
                )
            context = GatewayConfig.load_policy_validation_context(config_path)
            document = PolicyDocument.from_json_bytes(output_path.read_bytes(), config=context)
            self.assertEqual(document.organization_id, "second-organization")
            self.assertEqual(document.organization_policy.allowed_clients, ("claude-code",))

    def test_policy_create_refuses_overwrite_and_symlinks_unless_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output_path = root / "policy.json"
            base_args = [
                "--config",
                str(ROOT / "config.example.json"),
                "policy",
                "create",
                "--output",
                str(output_path),
            ]
            with mock.patch.dict(os.environ, {}, clear=True), redirect_stdout(io.StringIO()):
                self.assertEqual(main(base_args), 0)
            original = output_path.read_bytes()

            stderr = io.StringIO()
            with mock.patch.dict(os.environ, {}, clear=True), redirect_stderr(stderr):
                self.assertEqual(main([*base_args, "--template", "strict"]), 2)
            self.assertIn("policy creation failed: policy_output_exists", stderr.getvalue())
            self.assertNotIn(str(output_path), stderr.getvalue())
            self.assertEqual(output_path.read_bytes(), original)

            with mock.patch.dict(os.environ, {}, clear=True), redirect_stdout(io.StringIO()):
                self.assertEqual(main([*base_args, "--template", "strict", "--force"]), 0)
            self.assertNotEqual(output_path.read_bytes(), original)

            symlink_target = root / "must-not-change.json"
            symlink_target.write_text("preserve me", encoding="utf-8")
            symlink_path = root / "policy-link.json"
            symlink_path.symlink_to(symlink_target)
            symlink_args = [
                "--config",
                str(ROOT / "config.example.json"),
                "policy",
                "create",
                "--output",
                str(symlink_path),
                "--force",
            ]
            stderr = io.StringIO()
            with mock.patch.dict(os.environ, {}, clear=True), redirect_stderr(stderr):
                self.assertEqual(main(symlink_args), 2)
            self.assertIn("policy creation failed: policy_output_symlink_refused", stderr.getvalue())
            self.assertNotIn(str(symlink_path), stderr.getvalue())
            self.assertEqual(symlink_target.read_text(encoding="utf-8"), "preserve me")

            if hasattr(os, "mkfifo"):
                fifo_path = root / "policy.fifo"
                os.mkfifo(fifo_path)
                fifo_args = [
                    "--config",
                    str(ROOT / "config.example.json"),
                    "policy",
                    "create",
                    "--output",
                    str(fifo_path),
                    "--force",
                ]
                stderr = io.StringIO()
                with mock.patch.dict(os.environ, {}, clear=True), redirect_stderr(stderr):
                    self.assertEqual(main(fifo_args), 2)
                self.assertIn("policy creation failed: policy_output_not_regular", stderr.getvalue())
                self.assertNotIn(str(fifo_path), stderr.getvalue())
                self.assertTrue(stat.S_ISFIFO(os.lstat(fifo_path).st_mode))

    def test_policy_create_failures_do_not_repeat_submitted_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output_path = Path(temporary) / "policy.json"
            base_args = [
                "--config",
                str(ROOT / "config.example.json"),
                "policy",
                "create",
                "--output",
                str(output_path),
            ]

            stderr = io.StringIO()
            with mock.patch.dict(os.environ, {}, clear=True), redirect_stderr(stderr):
                self.assertEqual(main([*base_args, "--template", "private-template-name"]), 2)
            self.assertIn("policy creation failed: policy_template_unknown", stderr.getvalue())
            self.assertNotIn("private-template-name", stderr.getvalue())
            self.assertNotIn(str(output_path), stderr.getvalue())

            stderr = io.StringIO()
            with mock.patch.dict(os.environ, {}, clear=True), redirect_stderr(stderr):
                self.assertEqual(main([*base_args, "--organization", "private-tenant-name"]), 2)
            self.assertIn("policy creation failed: policy_template_organization_unknown", stderr.getvalue())
            self.assertNotIn("private-tenant-name", stderr.getvalue())
            self.assertNotIn(str(output_path), stderr.getvalue())

            stderr = io.StringIO()
            with mock.patch.dict(os.environ, {}, clear=True), redirect_stderr(stderr):
                self.assertEqual(main([*base_args, "--monthly-budget-usd", "nan"]), 2)
            self.assertIn("policy creation failed: policy_document_invalid", stderr.getvalue())
            self.assertNotIn("nan", stderr.getvalue().lower())
            self.assertNotIn(str(output_path), stderr.getvalue())
            self.assertFalse(output_path.exists())

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

    def test_audit_since_normalizes_to_utc(self) -> None:
        self.assertEqual(_audit_since("2026-08-01"), "2026-08-01T00:00:00+00:00")
        self.assertEqual(
            _audit_since("2026-08-01T02:00:00+02:00"),
            "2026-08-01T00:00:00+00:00",
        )
        with self.assertRaises(ValueError):
            _audit_since("not-a-timestamp")

    def test_audit_anchor_emits_a_verified_metadata_only_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = replace(
                self.config,
                database_path=Path(temporary) / "usage.sqlite3",
                key_custody=KeyCustodyConfig(
                    backend="aws-kms",
                    region="us-east-1",
                    key_references={
                        "provider_credential": "alias/provider",
                        "data_encryption": "alias/data",
                    },
                ),
                audit_anchor=AuditAnchorConfig(
                    backend="aws-s3-object-lock",
                    region="us-east-1",
                    bucket="hormuz-audit-bucket",
                    prefix="immutable/audit",
                    retention_days=365,
                    legal_hold=False,
                ),
            )
            identity = next(iter(config.identities_by_token.values()))
            UsageStore(config.database_path).record(
                identity=identity,
                client="codex",
                protocol="openai",
                requested_model="gpt-5.4-mini",
                resolved_alias="gpt-5.4-mini",
                upstream_model="gpt-5.4-mini",
                policy_action="allowed",
                status="succeeded",
            )
            sink = _RecordingAuditAnchor()
            stderr = io.StringIO()
            args = argparse.Namespace(kind="all", since="2026-08-01T00:00:00Z")
            with mock.patch("hormuz.cli.create_audit_anchor_sink", return_value=sink), redirect_stderr(stderr):
                self.assertEqual(_audit_anchor(config, args), 0)
            artifact = parse_audit_anchor_artifact(sink.calls[0]["artifact"])  # type: ignore[arg-type]
            self.assertEqual(artifact["event_count"], 1)
            self.assertNotIn("prompt", repr(artifact))
            self.assertIn("audit_anchor=test-anchor", stderr.getvalue())
            self.assertIn(f"artifact_id={artifact['artifact_id']}", stderr.getvalue())
            self.assertNotIn("Alice Example", stderr.getvalue())

    def test_audit_chain_anchor_verify_and_explicit_epoch_use_canonical_checkpoint_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = replace(
                self.config,
                database_path=root / "usage.sqlite3",
                key_custody=KeyCustodyConfig(
                    backend="aws-kms",
                    region="us-east-1",
                    key_references={
                        "provider_credential": "alias/provider",
                        "data_encryption": "alias/data",
                    },
                ),
                audit_anchor=AuditAnchorConfig(
                    backend="aws-s3-object-lock",
                    region="us-east-1",
                    bucket="hormuz-audit-bucket",
                    prefix="immutable/audit",
                    retention_days=365,
                    legal_hold=False,
                ),
                audit_chain=AuditChainConfig(maximum_anchor_age_seconds=3600),
            )
            identity = next(iter(config.identities_by_token.values()))
            UsageStore(config.database_path).record(
                identity=identity,
                client="codex",
                protocol="openai",
                requested_model="gpt-5.4-mini",
                resolved_alias="gpt-5.4-mini",
                upstream_model="gpt-5.4-mini",
                policy_action="allowed",
                status="succeeded",
            )
            checkpoint_path = root / "checkpoint.json"
            sink = _RecordingAuditAnchor()
            anchor_args = argparse.Namespace(
                audit_chain_command="anchor",
                output=str(checkpoint_path),
                force=False,
            )
            anchor_output = io.StringIO()
            with mock.patch("hormuz.cli.create_audit_anchor_sink", return_value=sink), redirect_stdout(anchor_output):
                self.assertEqual(_audit_chain(config, anchor_args), 0)
            checkpoint = parse_audit_chain_checkpoint(checkpoint_path.read_bytes())
            self.assertEqual(parse_audit_chain_checkpoint(sink.calls[0]["artifact"]), checkpoint)  # type: ignore[arg-type]
            self.assertEqual(os.stat(checkpoint_path).st_mode & 0o777, 0o600)
            self.assertIn("audit_chain_anchor=test-anchor", anchor_output.getvalue())
            self.assertNotIn("Alice Example", anchor_output.getvalue())

            verify_args = argparse.Namespace(audit_chain_command="verify", checkpoint=str(checkpoint_path))
            verify_output = io.StringIO()
            with redirect_stdout(verify_output):
                self.assertEqual(_audit_chain(config, verify_args), 0)
            self.assertIn("audit_chain_verified=true", verify_output.getvalue())

            epoch_args = argparse.Namespace(
                audit_chain_command="epoch",
                checkpoint=str(checkpoint_path),
                reason="migration",
                confirm="START_NEW_AUDIT_CHAIN_EPOCH",
            )
            epoch_output = io.StringIO()
            with redirect_stdout(epoch_output):
                self.assertEqual(_audit_chain(config, epoch_args), 0)
            self.assertIn("audit_chain_epoch_started=true", epoch_output.getvalue())

            status_args = argparse.Namespace(audit_chain_command="status")
            status_output = io.StringIO()
            with redirect_stdout(status_output):
                self.assertEqual(_audit_chain(config, status_args), 0)
            self.assertIn("chain_epoch=2", status_output.getvalue())
            self.assertIn("anchor_overdue=false", status_output.getvalue())

    def test_audit_chain_epoch_rejects_a_checkpoint_for_another_tenant(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = replace(self.config, database_path=root / "usage.sqlite3")
            identity = next(iter(config.identities_by_token.values()))
            other_identity = replace(identity, organization_id="other-organization")
            store = UsageStore(config.database_path)
            store.record(
                identity=other_identity,
                client="codex",
                protocol="openai",
                requested_model="gpt-5.4-mini",
                resolved_alias="gpt-5.4-mini",
                upstream_model="gpt-5.4-mini",
                policy_action="allowed",
                status="succeeded",
            )
            checkpoint_path = root / "other-checkpoint.json"
            checkpoint_path.write_bytes(
                serialize_audit_chain_checkpoint(
                    build_audit_chain_checkpoint(store.audit_chain_head(organization_id="other-organization"))
                )
            )
            args = argparse.Namespace(
                audit_chain_command="epoch",
                checkpoint=str(checkpoint_path),
                reason="restore",
                confirm="START_NEW_AUDIT_CHAIN_EPOCH",
            )
            with self.assertRaises(CustodyError) as raised:
                _audit_chain(config, args)
            self.assertEqual(raised.exception.code, "audit_chain_tenant_mismatch")

    def test_audit_chain_checkpoint_write_failure_preserves_the_existing_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            checkpoint_path = Path(temporary) / "checkpoint.json"
            checkpoint_path.write_bytes(b"previous-trusted-checkpoint")

            with mock.patch("hormuz.cli.os.write", side_effect=OSError("disk full")):
                with self.assertRaises(CustodyError) as raised:
                    _write_audit_chain_checkpoint(checkpoint_path, b"new-checkpoint", force=True)

            self.assertEqual(raised.exception.code, "audit_chain_checkpoint_write_unavailable")
            self.assertEqual(checkpoint_path.read_bytes(), b"previous-trusted-checkpoint")
            self.assertEqual(list(checkpoint_path.parent.glob(".checkpoint.json.*.tmp")), [])

    def test_custody_and_audit_anchor_commands_are_explicit(self) -> None:
        seal = build_parser().parse_args(
            [
                "custody",
                "seal",
                "--purpose",
                "provider_credential",
                "--input-env",
                "COMPANY_OPENAI_KEY",
                "--output",
                "/etc/hormuz/openai.envelope",
            ]
        )
        self.assertEqual(seal.command, "custody")
        self.assertEqual(seal.custody_command, "seal")
        custody_executor = build_parser().parse_args(["custody", "executor", "register", "assets"])
        self.assertEqual(custody_executor.command, "custody")
        self.assertEqual(custody_executor.custody_command, "executor")
        self.assertEqual(custody_executor.custody_executor_command, "register-assets")
        anchor = build_parser().parse_args(["audit", "anchor", "--kind", "security"])
        self.assertEqual(anchor.command, "audit")
        self.assertEqual(anchor.audit_command, "anchor")
        self.assertEqual(anchor.kind, "security")
        chain = build_parser().parse_args(
            ["audit", "chain", "epoch", "--checkpoint", "/secure/checkpoint.json", "--reason", "restore", "--confirm", "START_NEW_AUDIT_CHAIN_EPOCH"]
        )
        self.assertEqual(chain.command, "audit")
        self.assertEqual(chain.audit_command, "chain")
        self.assertEqual(chain.audit_chain_command, "epoch")

if __name__ == "__main__":
    unittest.main()
