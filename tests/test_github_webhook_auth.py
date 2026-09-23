"""Synthetic, offline witnesses for the GitHub App auth boundary."""

from __future__ import annotations

from dataclasses import asdict
from dataclasses import replace
import hashlib
import hmac
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from hormuz.github_webhook_auth import GitHubWebhookAuthenticator
from hormuz.outcome_ingest import OutcomeIngestor
from hormuz.outcome_wire import OutcomeKeys
from hormuz.portfolio_repository import create_portfolio_repository
from hormuz.portfolio_wire import PortfolioError
from hormuz.store import UsageStore

from tests._portfolio_fixture import registry_config
from tests._registry_transition_fixture import sqlite_snapshot


WEBHOOK_OLD = b"synthetic-github-webhook-secret-old-12345"
WEBHOOK_NEW = b"synthetic-github-webhook-secret-new-12345"
IDENTITY_KEYS = OutcomeKeys("identity-v1", {"identity-v1": b"i" * 32})


def signed(raw: bytes, *, secret: bytes = WEBHOOK_OLD, **headers: str) -> dict[str, str]:
    return {
        "X-Hub-Signature-256": "sha256=" + hmac.new(secret, raw, hashlib.sha256).hexdigest(),
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "10000000-0000-4000-8000-000000000001",
        **headers,
    }


class GitHubWebhookAuthTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.config = registry_config(Path(temporary.name))
        self.auth = GitHubWebhookAuthenticator(
            self.config, "acme", "github-one", {"old": WEBHOOK_OLD, "new": WEBHOOK_NEW},
            IDENTITY_KEYS, "identity-v1",
        )
        self.raw = json.dumps({
            "installation": {"id": 123}, "repository": {"id": 456},
            "action": "opened", "pull_request": {
                "id": 1001, "title": "SYNTHETIC_PRIVATE_TITLE",
                "body": "SYNTHETIC_PRIVATE_REVIEW_TEXT",
                "head": {"ref": "SYNTHETIC_PRIVATE_BRANCH"},
            },
        }, ensure_ascii=False, separators=(",", ":")).encode()

    def fail_code(self, code, action):
        with self.assertRaises(PortfolioError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)
        self.assertNotIn("SYNTHETIC_PRIVATE", str(caught.exception))

    def test_signature_must_match_exact_raw_bytes_before_parsing(self):
        with mock.patch("hormuz.github_webhook_auth.decode_source_body", side_effect=AssertionError("parsed")):
            self.fail_code("unauthenticated", lambda: self.auth.authenticate({}, self.raw))
            self.fail_code("unauthenticated", lambda: self.auth.authenticate(signed(self.raw), self.raw + b" "))
            self.fail_code("unauthenticated", lambda: self.auth.authenticate(
                signed(self.raw), self.raw.replace(b"opened", b"closed")))
            self.fail_code("unauthenticated", lambda: self.auth.authenticate(
                signed(self.raw), self.raw.replace(b"SYNTHETIC_PRIVATE", b"SYNTHETIC_PUBLIC")))
        self.fail_code("invalid_request", lambda: self.auth.authenticate(signed(self.raw), b""))
        self.fail_code("invalid_request", lambda: self.auth.authenticate(signed(self.raw), b"x" * (1048576 + 1)))

    def test_signed_installation_and_repository_must_match_server_binding(self):
        for field, value in (("installation", 987), ("repository", 654),
                             ("installation", True), ("repository", "456")):
            with self.subTest(field=field, value=value):
                body = json.loads(self.raw)
                body[field]["id"] = value
                raw = json.dumps(body, separators=(",", ":")).encode()
                self.fail_code("forbidden", lambda: self.auth.authenticate(signed(raw), raw))
        other = GitHubWebhookAuthenticator(self.config, "beta", "github-other",
                                           {"old": WEBHOOK_OLD}, IDENTITY_KEYS, "identity-v1")
        self.fail_code("forbidden", lambda: other.authenticate(signed(self.raw), self.raw))

    def test_unsigned_header_changes_cannot_change_tenant_or_replay_identity(self):
        first = self.auth.authenticate(signed(self.raw), self.raw)
        changed = signed(self.raw, **{
            "X-GitHub-Event": "check_run",
            "X-GitHub-Delivery": "10000000-0000-4000-8000-000000000999",
            "X-GitHub-Hook-ID": "999999999",
        })
        self.assertEqual(self.auth.authenticate(changed, self.raw), first)
        signature_only = {"X-Hub-Signature-256": signed(self.raw)["X-Hub-Signature-256"]}
        self.assertEqual(self.auth.authenticate(signature_only, self.raw), first)
        self.fail_code("invalid_request", lambda: self.auth.authenticate({
            "X-Hub-Signature-256": signed(self.raw)["X-Hub-Signature-256"],
            "x-hub-signature-256": signed(self.raw)["X-Hub-Signature-256"],
        }, self.raw))
        ordinary_duplicates = signed(self.raw, **{
            "Forwarded": "for=127.0.0.1", "forwarded": "for=127.0.0.2",
        })
        self.assertEqual(self.auth.authenticate(ordinary_duplicates, self.raw), first)
        self.fail_code("invalid_request", lambda: self.auth.authenticate(
            signed(self.raw, **{"X-Proxy": "bad\r\nheader"}), self.raw))
        self.fail_code("invalid_request", lambda: self.auth.authenticate(
            signed(self.raw, **{"Bad Header": "value"}), self.raw))
        self.fail_code("unauthenticated", lambda: self.auth.authenticate(
            {"X-Hub-Signature-256": signed(self.raw)["X-Hub-Signature-256"].upper()}, self.raw))

    def test_rotation_preserves_body_identity_and_metadata_excludes_content(self):
        old = self.auth.authenticate(signed(self.raw), self.raw)
        new = self.auth.authenticate(signed(self.raw, secret=WEBHOOK_NEW), self.raw)
        self.assertEqual(old.source_delivery_id, new.source_delivery_id)
        self.assertNotEqual(old.credential_version, new.credential_version)
        self.assertEqual((old.credential_version, new.credential_version), ("old", "new"))
        self.assertEqual(len(old.source_delivery_id), 64)
        self.assertNotEqual(old.source_delivery_id, hashlib.sha256(self.raw).hexdigest())
        self.assertEqual(set(asdict(old)), {
            "organization_id", "connector_id", "provider", "installation_id",
            "workspace_id", "source_delivery_id", "credential_version",
        })
        for marker in ("SYNTHETIC_PRIVATE_TITLE", "SYNTHETIC_PRIVATE_REVIEW_TEXT",
                       "SYNTHETIC_PRIVATE_BRANCH", "synthetic-github-webhook-secret"):
            self.assertNotIn(marker, repr(self.auth))
            self.assertNotIn(marker, repr(old))

    def test_existing_atomic_replay_seam_uses_signed_body_identity(self):
        # This deliberately unsupported adapter isolates the inherited receipt
        # seam from the production normalizer tested by the runtime suite.
        class UnsupportedFixtureAdapter:
            def __init__(self, authenticator):
                self.authenticator = authenticator

            def verify(self, *, binding, headers, raw):
                return self.authenticator.authenticate(headers, raw)

            def normalize(self, *, binding, verified, body):
                return []

        UsageStore(self.config.database_path)
        repository = create_portfolio_repository(self.config).outcomes
        ingestor = OutcomeIngestor(self.config, repository, "acme", "github-one",
                                   UnsupportedFixtureAdapter(self.auth), IDENTITY_KEYS)
        first = ingestor.ingest(signed(self.raw), self.raw)
        self.assertEqual(first["disposition"], "unsupported")
        snapshot = sqlite_snapshot(self.config.database_path)
        altered = signed(self.raw, **{"X-GitHub-Delivery": "10000000-0000-4000-8000-000000000999"})
        self.assertEqual(ingestor.ingest(altered, self.raw), first)
        self.assertEqual(sqlite_snapshot(self.config.database_path), snapshot)
        self.assertNotIn("SYNTHETIC_PRIVATE", repr(snapshot))

    def test_disabled_channel_cannot_be_constructed_or_used(self):
        disabled = replace(self.config, portfolio_control=replace(self.config.portfolio_control, connectors=()))
        self.fail_code("forbidden", lambda: GitHubWebhookAuthenticator(
            disabled, "acme", "github-one", {"old": WEBHOOK_OLD}, IDENTITY_KEYS, "identity-v1"))
        acme, beta = self.config.portfolio_control.connectors
        overlapping = replace(self.config, portfolio_control=replace(
            self.config.portfolio_control,
            connectors=(acme, replace(beta, installation_id="123", external_object_ids=("456",))),
        ))
        self.fail_code("forbidden", lambda: GitHubWebhookAuthenticator(
            overlapping, "acme", "github-one", {"old": WEBHOOK_OLD}, IDENTITY_KEYS, "identity-v1"))
        self.fail_code("invalid_request", lambda: GitHubWebhookAuthenticator(
            self.config, "acme", "github-one", {"old": WEBHOOK_OLD, "new": WEBHOOK_OLD},
            IDENTITY_KEYS, "identity-v1"))
        self.fail_code("unavailable", lambda: GitHubWebhookAuthenticator(
            self.config, "acme", "github-one", {"old": WEBHOOK_OLD},
            OutcomeKeys("other", {"other": b"o" * 32}), "identity-v1"))


if __name__ == "__main__":
    unittest.main()
