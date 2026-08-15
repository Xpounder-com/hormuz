from __future__ import annotations

import argparse
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from contextlib import redirect_stderr
from dataclasses import replace
from pathlib import Path

from hormuz.cli import (
    _audit_export,
    _audit_since,
    _budget_for_scope,
    _client_config,
    _context_pack,
    build_parser,
)
from hormuz.config import GatewayConfig


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
        self.assertIn('ANTHROPIC_BASE_URL="https://hormuz.example"', output.getvalue())
        self.assertIn('ANTHROPIC_AUTH_TOKEN="${HORMUZ_TOKEN}"', output.getvalue())

    def test_status_accepts_dimension_and_scope_filters(self) -> None:
        args = build_parser().parse_args(
            ["status", "--group-by", "model", "--team", "engineering", "--actor", "alice", "--json"]
        )

        self.assertEqual(args.group_by, "model")
        self.assertEqual(args.team, "engineering")
        self.assertEqual(args.actor, "alice")
        self.assertTrue(args.json)

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
                        "organization_id": "acme",
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
                    "acme",
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
                "acme",
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


if __name__ == "__main__":
    unittest.main()
