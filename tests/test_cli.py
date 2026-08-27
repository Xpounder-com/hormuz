from __future__ import annotations

import argparse
import io
import json
import os
import signal
import tempfile
import tomllib
import unittest
from unittest import mock
from contextlib import redirect_stdout
from contextlib import redirect_stderr
from dataclasses import replace
from pathlib import Path

from hormuz.cli import (
    _audit_anchor,
    _audit_chain,
    _audit_export,
    _audit_since,
    _auth_token,
    _budget_for_scope,
    _client_config,
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
from hormuz.store import UsageStore


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
            self.assertEqual(main(["contract-manifest"]), 0)
        manifest = json.loads(output.getvalue())
        self.assertEqual(manifest["schema_id"], "hormuz.policy-evidence-manifest")
        self.assertEqual(manifest["schema_version"], 1)

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
        custody_executor = build_parser().parse_args(["custody-executor", "register-assets"])
        self.assertEqual(custody_executor.command, "custody-executor")
        self.assertEqual(custody_executor.custody_executor_command, "register-assets")
        anchor = build_parser().parse_args(["audit-anchor", "--kind", "security"])
        self.assertEqual(anchor.command, "audit-anchor")
        self.assertEqual(anchor.kind, "security")
        chain = build_parser().parse_args(
            ["audit-chain", "epoch", "--checkpoint", "/secure/checkpoint.json", "--reason", "restore", "--confirm", "START_NEW_AUDIT_CHAIN_EPOCH"]
        )
        self.assertEqual(chain.command, "audit-chain")
        self.assertEqual(chain.audit_chain_command, "epoch")

if __name__ == "__main__":
    unittest.main()
