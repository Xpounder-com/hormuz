from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sqlite3
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
    _audit_since,
    _auth_token,
    _budget_for_scope,
    _client_config,
    _context_audit_export,
    _context_audit_since,
    _context_delete,
    _context_export,
    _context_import,
    _context_list,
    _context_pack,
    _context_snapshot_import,
    _context_snapshot_show,
    build_parser,
)
from hormuz.config import (
    ConfigError,
    GatewayConfig,
    Identity,
    OIDCLoginConfig,
    OIDCIssuerConfig,
    SessionBrokerConfig,
)
from hormuz.context import ContextError


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
                ),
                0,
            )
        self.assertIn(
            "hormuz auth token --gateway-env HORMUZ_SESSION_GATEWAY --profile engineering-claude",
            claude.getvalue(),
        )
        self.assertIn('"HORMUZ_SESSION_GATEWAY": "https://hormuz.example"', claude.getvalue())
        self.assertNotIn("must-never-appear", codex.getvalue() + claude.getvalue())

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
            ["status", "--group-by", "model", "--team", "engineering", "--actor", "alice", "--json"]
        )

        self.assertEqual(args.group_by, "model")
        self.assertEqual(args.team, "engineering")
        self.assertEqual(args.actor, "alice")
        self.assertTrue(args.json)

    def test_context_database_is_separate_and_cannot_alias_usage(self) -> None:
        self.assertNotEqual(self.config.context_database_path, self.config.database_path)
        self.assertEqual(self.config.context_service.policy_version, "engineering-context-v1")
        self.assertEqual(self.config.context_service.max_token_budget, 32_768)
        self.assertEqual(self.config.context_service.max_items, 20)
        self.assertEqual(self.config.context_service.requests_per_minute, 60)
        self.assertFalse(self.config.context_service.allow_provisional)
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
