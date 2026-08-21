from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from hormuz.config import GatewayConfig
from hormuz_context_experiment.cli import _context_pack, build_parser


ROOT = Path(__file__).resolve().parents[3]
TEST_ENVIRONMENT = {"HORMUZ_TOKEN": "test-identity-token"}


class ExperimentalContextCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = GatewayConfig.load(ROOT / "config.example.json", environ=TEST_ENVIRONMENT)

    def test_context_pack_uses_configured_actor_scope_and_explicit_content_file(self) -> None:
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

    def test_context_pack_rejects_branch_without_repository(self) -> None:
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

    def test_context_pack_cannot_expand_identity_scope(self) -> None:
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
        wrong_organization = build_parser().parse_args([*base, "--organization", "another-organization"])
        over_clearance = build_parser().parse_args(
            [*base, "--organization", "xpounder", "--clearance", "restricted"]
        )
        with redirect_stderr(io.StringIO()):
            self.assertEqual(_context_pack(self.config, wrong_organization), 2)
            self.assertEqual(_context_pack(self.config, over_clearance), 2)


if __name__ == "__main__":
    unittest.main()
