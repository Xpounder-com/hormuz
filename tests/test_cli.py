from __future__ import annotations

import argparse
import io
import json
import os
import tempfile
import tomllib
import unittest
from unittest import mock
from contextlib import redirect_stdout
from contextlib import redirect_stderr
from dataclasses import replace
from pathlib import Path

from hormuz.cli import (
    _audit_export,
    _audit_since,
    _auth_token,
    _budget_for_scope,
    _client_config,
    _status,
    build_parser,
    main,
)
from hormuz.config import ConfigError, GatewayConfig, Identity, OIDCIssuerConfig
from hormuz.contracts import validate_contract
from hormuz.store import UsageStore


ROOT = Path(__file__).resolve().parents[1]


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

if __name__ == "__main__":
    unittest.main()
