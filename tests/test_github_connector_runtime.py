"""Offline production-path witnesses for the opt-in GitHub connector."""

from __future__ import annotations

from dataclasses import asdict, replace
from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
import http.client
import json
from pathlib import Path
import tempfile
import unittest

from hormuz.github_connector import GitHubOutcomeAdapter, GitHubOutcomeReceiver
from hormuz.github_http import GITHUB_EVENTS_PATH
from hormuz.github_webhook_auth import GitHubWebhookAuthenticator
from hormuz.outcome_connector_config import (
    OutcomeConnectorConfig,
    build_outcome_connector_config,
    resolve_outcome_connector_credentials,
)
from hormuz.outcome_wire import observation_from_mapping
from hormuz.portfolio_wire import PortfolioError
from hormuz.portfolio_repository import create_portfolio_repository
from hormuz.server import GatewayServer, serve_in_thread
from hormuz.store import UsageStore

if __package__:
    from ._portfolio_fixture import ADMIN, registry_config
    from ._registry_transition_fixture import sqlite_snapshot
else:
    from _portfolio_fixture import ADMIN, registry_config
    from _registry_transition_fixture import sqlite_snapshot


WEBHOOK_SECRET = "synthetic-github-runtime-webhook-secret-12345"
IDENTITY_KEY = "synthetic-github-runtime-identity-key-123456"
BETA_WEBHOOK_SECRET = "synthetic-beta-github-webhook-secret-123456"
BETA_IDENTITY_KEY = "synthetic-beta-github-identity-key-1234567"


def runtime_document() -> dict:
    return {
        "schema_id": "hormuz.outcome-connectors",
        "schema_version": 1,
        "github": [{
            "organization_id": "acme",
            "connector_id": "github-one",
            "webhook_secrets": [{
                "version": "webhook-v1",
                "environment_variable": "SYNTHETIC_GITHUB_WEBHOOK_SECRET",
            }],
            "identity_keys": [{
                "version": "identity-v1",
                "environment_variable": "SYNTHETIC_GITHUB_IDENTITY_KEY",
            }],
            "current_key_version": "identity-v1",
            "delivery_identity_key_version": "identity-v1",
        }],
    }


def runtime_config(root: Path):
    base = registry_config(root)
    unresolved = build_outcome_connector_config(runtime_document(), base.portfolio_control)
    resolved = resolve_outcome_connector_credentials(unresolved, {
        "SYNTHETIC_GITHUB_WEBHOOK_SECRET": WEBHOOK_SECRET,
        "SYNTHETIC_GITHUB_IDENTITY_KEY": IDENTITY_KEY,
    })
    return replace(base, outcome_connectors=resolved)


def payload(**changes) -> dict:
    value = {
        "action": "opened",
        "installation": {"id": 123},
        "repository": {"id": 456, "name": "SYNTHETIC_PRIVATE_REPOSITORY"},
        "pull_request": {
            "id": 1001,
            "created_at": "2026-09-01T10:00:00Z",
            "updated_at": "2026-09-01T10:00:00Z",
            "closed_at": None,
            "merged": False,
            "title": "SYNTHETIC_PRIVATE_TITLE",
            "body": "SYNTHETIC_PRIVATE_BODY",
            "head": {"sha": "a" * 40, "ref": "SYNTHETIC_PRIVATE_BRANCH"},
        },
    }
    value.update(changes)
    if value.get("pull_request") is None:
        value.pop("pull_request", None)
    return value


def encoded(value: dict) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def signed(raw: bytes, *, secret: str = WEBHOOK_SECRET, **changes: str) -> dict[str, str]:
    return {
        "X-Hub-Signature-256": "sha256=" + hmac.new(
            secret.encode("ascii"), raw, hashlib.sha256,
        ).hexdigest(),
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "30000000-0000-4000-8000-000000000001",
        **changes,
    }


class GitHubRuntimeConfigTests(unittest.TestCase):
    def setUp(self):
        self.base = registry_config(Path("/unused/github-runtime-config"))

    def error(self, action):
        with self.assertRaisesRegex(ValueError, "outcome_connector"):
            action()

    def test_strict_binding_and_secret_resolution(self):
        unresolved = build_outcome_connector_config(runtime_document(), self.base.portfolio_control)
        self.assertIsInstance(unresolved, OutcomeConnectorConfig)
        self.assertNotIn(WEBHOOK_SECRET, repr(unresolved))
        resolved = resolve_outcome_connector_credentials(unresolved, {
            "SYNTHETIC_GITHUB_WEBHOOK_SECRET": WEBHOOK_SECRET,
            "SYNTHETIC_GITHUB_IDENTITY_KEY": IDENTITY_KEY,
        })
        self.assertEqual(len(resolved.github), 1)
        self.assertNotIn(WEBHOOK_SECRET, repr(resolved))
        self.assertEqual(
            {kind for kind, _value in resolved.protected_values()},
            {"github_webhook_secret", "outcome_identity_key"},
        )

    def test_invalid_binding_missing_short_or_reused_secrets_fail_closed(self):
        invalid = runtime_document()
        invalid["github"][0]["connector_id"] = "unknown"
        self.error(lambda: build_outcome_connector_config(invalid, self.base.portfolio_control))
        unresolved = build_outcome_connector_config(runtime_document(), self.base.portfolio_control)
        self.error(lambda: resolve_outcome_connector_credentials(unresolved, {}))
        self.error(lambda: resolve_outcome_connector_credentials(unresolved, {
            "SYNTHETIC_GITHUB_WEBHOOK_SECRET": "short",
            "SYNTHETIC_GITHUB_IDENTITY_KEY": IDENTITY_KEY,
        }))
        self.error(lambda: resolve_outcome_connector_credentials(unresolved, {
            "SYNTHETIC_GITHUB_WEBHOOK_SECRET": WEBHOOK_SECRET,
            "SYNTHETIC_GITHUB_IDENTITY_KEY": WEBHOOK_SECRET,
        }))

    def test_record_key_rotation_retains_one_delivery_identity_version(self):
        document = runtime_document()
        channel = document["github"][0]
        channel["identity_keys"].append({
            "version": "identity-v2",
            "environment_variable": "SYNTHETIC_GITHUB_IDENTITY_KEY_V2",
        })
        channel["current_key_version"] = "identity-v2"
        resolved = resolve_outcome_connector_credentials(
            build_outcome_connector_config(document, self.base.portfolio_control),
            {
                "SYNTHETIC_GITHUB_WEBHOOK_SECRET": WEBHOOK_SECRET,
                "SYNTHETIC_GITHUB_IDENTITY_KEY": IDENTITY_KEY,
                "SYNTHETIC_GITHUB_IDENTITY_KEY_V2": "synthetic-github-runtime-identity-key-v2-123",
            },
        ).github[0]
        self.assertEqual(resolved.resolved_identity_keys().current_version, "identity-v2")
        self.assertEqual(resolved.delivery_identity_key_version, "identity-v1")


class GitHubNormalizerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.config = runtime_config(Path(temporary.name))
        channel = self.config.outcome_connectors.github[0]
        self.binding = self.config.portfolio_control.connectors[0]
        self.authenticator = GitHubWebhookAuthenticator(
            self.config,
            channel.organization_id,
            channel.connector_id,
            channel.resolved_webhook_secrets(),
            channel.resolved_identity_keys(),
            channel.delivery_identity_key_version,
        )
        self.adapter = GitHubOutcomeAdapter(self.authenticator)

    def normalize(self, value: dict, **headers: str) -> list[dict]:
        raw = encoded(value)
        verified = self.adapter.verify(
            binding=self.binding,
            headers=signed(raw, **headers),
            raw=raw,
        )
        return self.adapter.normalize(
            binding=self.binding,
            verified=verified,
            body=value,
        )

    def test_pull_request_allowlist_uses_timestamps_not_code_or_unsigned_headers(self):
        cases = (
            ("opened", False, "created"),
            ("reopened", False, "reopened"),
            ("synchronize", False, "started"),
            ("closed", True, "completed"),
            ("closed", False, "canceled"),
        )
        for action, merged, event_type in cases:
            with self.subTest(action=action, merged=merged):
                item = payload(action=action)
                item["pull_request"]["merged"] = merged
                item["pull_request"]["closed_at"] = "2026-09-01T10:05:00Z"
                projected = self.normalize(item, **{
                    "X-GitHub-Event": "check_run",
                    "X-GitHub-Delivery": "changed-unsigned-value",
                })
                self.assertEqual(len(projected), 1)
                observation = observation_from_mapping(projected[0], self.binding)
                self.assertEqual((observation.event_type, observation.quality_state),
                                 (event_type, "unknown"))
                self.assertEqual(observation.external_object_id, "1001")
                self.assertEqual(observation.container_id, "456")
                self.assertEqual(observation.ordering_domain, "source_updated_at_v1")
                serialized = json.dumps(asdict(observation))
                for marker in ("SYNTHETIC_PRIVATE", "aaaaaaaaaaaaaaaaaaaaaaaa"):
                    self.assertNotIn(marker, serialized)

    def test_review_and_check_conclusion_matrix_is_descriptive_and_bounded(self):
        for state, expected in (
            ("approved", ("accepted", "accepted")),
            ("changes_requested", ("defect_reported", "rejected")),
        ):
            item = payload(action="submitted", review={
                "id": 2001,
                "state": state,
                "submitted_at": "2026-09-01T11:00:00Z",
                "body": "SYNTHETIC_PRIVATE_REVIEW",
            })
            projected = self.normalize(item)[0]
            self.assertEqual((projected["event_type"], projected["quality_state"]), expected)
        for conclusion, expected in (
            ("success", ("completed", "accepted")),
            ("failure", ("defect_reported", "rejected")),
            ("cancelled", ("canceled", "not_applicable")),
            ("neutral", ("completed", "unknown")),
        ):
            item = payload(action="completed", pull_request=None, check_run={
                "id": 3001,
                "status": "completed",
                "conclusion": conclusion,
                "completed_at": "2026-09-01T12:00:00Z",
                "head_sha": "b" * 40,
                "pull_requests": [{"id": 1001}],
                "name": "SYNTHETIC_PRIVATE_CHECK",
            })
            projected = self.normalize(item)[0]
            self.assertEqual((projected["event_type"], projected["quality_state"]), expected)
        unassociated = payload(action="completed", pull_request=None, check_run={
            "id": 3002, "status": "completed", "conclusion": "failure",
            "completed_at": "2026-09-01T12:00:00Z", "pull_requests": [],
        })
        self.assertEqual(self.normalize(unassociated), [])

    def test_unsupported_control_and_malformed_allowlisted_shapes_never_project(self):
        control = {"action": "suspend", "installation": {"id": 123}}
        self.assertEqual(self.normalize(control), [])
        unsupported = payload(action="labeled")
        self.assertEqual(self.normalize(unsupported), [])
        malformed = payload()
        malformed["pull_request"]["id"] = True
        with self.assertRaises(PortfolioError) as caught:
            self.normalize(malformed)
        self.assertEqual(caught.exception.code, "invalid_request")
        missing_repository = payload()
        missing_repository.pop("repository")
        with self.assertRaises(PortfolioError) as caught:
            self.normalize(missing_repository)
        self.assertEqual(caught.exception.code, "invalid_request")
        ambiguous = payload(review={"id": 1}, check_run={"id": 2})
        with self.assertRaises(PortfolioError) as caught:
            self.normalize(ambiguous)
        self.assertEqual(caught.exception.code, "invalid_request")

    def test_unicode_raw_bytes_and_body_scope_fields_cannot_change_authority(self):
        item = payload(
            organization_id="beta",
            connector_id="github-other",
            work_scope_id="spoofed-scope",
        )
        item["pull_request"]["title"] = "SYNTHETIC_PRIVATE_\N{SNOWMAN}"
        raw = encoded(item)
        verified = self.adapter.verify(
            binding=self.binding,
            headers=signed(raw),
            raw=raw,
        )
        projected = self.adapter.normalize(
            binding=self.binding,
            verified=verified,
            body=item,
        )
        self.assertEqual((verified.organization_id, verified.connector_id), ("acme", "github-one"))
        self.assertEqual(projected[0]["external_object_id"], "1001")
        self.assertNotIn("SYNTHETIC_PRIVATE", json.dumps(projected))
        with self.assertRaises(PortfolioError) as caught:
            self.adapter.verify(
                binding=self.binding,
                headers=signed(raw),
                raw=raw.replace("\N{SNOWMAN}".encode(), b"?"),
            )
        self.assertEqual(caught.exception.code, "unauthenticated")


class GitHubChannelSelectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        base = registry_config(self.root)
        document = runtime_document()
        document["github"].append({
            "organization_id": "beta",
            "connector_id": "github-other",
            "webhook_secrets": [{
                "version": "beta-webhook-v1",
                "environment_variable": "SYNTHETIC_BETA_GITHUB_WEBHOOK_SECRET",
            }],
            "identity_keys": [{
                "version": "beta-identity-v1",
                "environment_variable": "SYNTHETIC_BETA_GITHUB_IDENTITY_KEY",
            }],
            "current_key_version": "beta-identity-v1",
            "delivery_identity_key_version": "beta-identity-v1",
        })
        unresolved = build_outcome_connector_config(document, base.portfolio_control)
        resolved = resolve_outcome_connector_credentials(unresolved, {
            "SYNTHETIC_GITHUB_WEBHOOK_SECRET": WEBHOOK_SECRET,
            "SYNTHETIC_GITHUB_IDENTITY_KEY": IDENTITY_KEY,
            "SYNTHETIC_BETA_GITHUB_WEBHOOK_SECRET": BETA_WEBHOOK_SECRET,
            "SYNTHETIC_BETA_GITHUB_IDENTITY_KEY": BETA_IDENTITY_KEY,
        })
        self.config = replace(base, outcome_connectors=resolved)
        UsageStore(self.config.database_path)
        self.repository = create_portfolio_repository(self.config).outcomes
        self.receiver = GitHubOutcomeReceiver(self.config, self.repository)

    def assert_error(self, code: str, raw: bytes, headers: dict[str, str]) -> None:
        before = sqlite_snapshot(self.config.database_path)
        with self.assertRaises(PortfolioError) as caught:
            self.receiver.ingest(headers, raw)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(sqlite_snapshot(self.config.database_path), before)

    def test_signature_and_signed_parent_select_exact_tenant_channel(self):
        acme = payload(
            organization_id="beta",
            connector_id="github-other",
            work_scope_id="spoofed-scope",
        )
        raw = encoded(acme)
        receipt = self.receiver.ingest(signed(raw), raw)
        self.assertEqual((receipt["organization_id"], receipt["connector_id"]),
                         ("acme", "github-one"))
        self.assert_error("forbidden", raw, signed(raw, secret=BETA_WEBHOOK_SECRET))

        unknown_installation = payload()
        unknown_installation["installation"]["id"] = 999
        raw = encoded(unknown_installation)
        self.assert_error("forbidden", raw, signed(raw))

        repository_mismatch = payload()
        repository_mismatch["repository"]["id"] = 654
        raw = encoded(repository_mismatch)
        self.assert_error("forbidden", raw, signed(raw))

        beta = payload()
        beta["installation"]["id"] = 987
        beta["repository"]["id"] = 654
        beta["pull_request"]["id"] = 2001
        raw = encoded(beta)
        receipt = self.receiver.ingest(signed(raw, secret=BETA_WEBHOOK_SECRET), raw)
        self.assertEqual((receipt["organization_id"], receipt["connector_id"]),
                         ("beta", "github-other"))


class GitHubHTTPRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = runtime_config(self.root)
        UsageStore(self.config.database_path)
        self.server = GatewayServer(self.config, environ={
            "SYNTHETIC_PROVIDER_KEY": "synthetic-provider-key",
        })
        self.thread = serve_in_thread(self.server)
        self.addCleanup(self.close_gateway)

    def close_gateway(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)

    def request(self, raw: bytes, headers: dict[str, str]):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        connection.request("POST", GITHUB_EVENTS_PATH, body=raw, headers={
            "Content-Type": "application/json",
            **headers,
        })
        response = connection.getresponse()
        body = json.loads(response.read())
        result = (response.status, dict(response.getheaders()), body)
        connection.close()
        return result

    def test_durable_receipt_precedes_ack_and_exact_replay_ignores_unsigned_headers(self):
        raw = encoded(payload())
        status, headers, receipt = self.request(raw, signed(raw))
        self.assertEqual(status, 202)
        self.assertEqual(receipt["disposition"], "accepted")
        self.assertEqual(receipt["accepted_event_count"], 1)
        self.assertIn("hormuz.connector-ingest-receipt", headers["X-Hormuz-Contract"])
        before = sqlite_snapshot(self.config.database_path)
        changed = signed(raw, **{
            "X-GitHub-Event": "installation",
            "X-GitHub-Delivery": "30000000-0000-4000-8000-000000000999",
        })
        self.assertEqual(self.request(raw, changed)[2], receipt)
        self.assertEqual(sqlite_snapshot(self.config.database_path), before)
        self.assertNotIn("SYNTHETIC_PRIVATE", repr(before))

    def test_invalid_signature_and_unsupported_event_are_content_free(self):
        raw = encoded(payload())
        headers = signed(raw)
        headers["X-Hub-Signature-256"] = "sha256=" + "0" * 64
        status, _response_headers, failure = self.request(raw, headers)
        self.assertEqual((status, failure["code"]), (401, "unauthenticated"))
        control = encoded({"action": "suspend", "installation": {"id": 123}})
        status, _response_headers, receipt = self.request(control, signed(control))
        self.assertEqual(status, 202)
        self.assertEqual((receipt["disposition"], receipt["accepted_event_count"]),
                         ("unsupported", 0))

    def test_duplicate_critical_headers_and_oversized_length_fail_before_read(self):
        raw = encoded(payload())
        signature = signed(raw)["X-Hub-Signature-256"]

        def malformed(headers: list[tuple[str, str]]) -> tuple[int, dict]:
            connection = http.client.HTTPConnection(
                "127.0.0.1", self.server.server_port, timeout=10,
            )
            connection.putrequest("POST", GITHUB_EVENTS_PATH)
            for name, value in headers:
                connection.putheader(name, value)
            connection.endheaders()
            response = connection.getresponse()
            result = response.status, json.loads(response.read())
            connection.close()
            return result

        common = [("Content-Type", "application/json"),
                  ("X-Hub-Signature-256", signature)]
        status, failure = malformed([
            *common,
            ("Content-Length", str(len(raw))),
            ("Content-Length", str(len(raw))),
        ])
        self.assertEqual((status, failure["code"]), (400, "invalid_request"))
        status, failure = malformed([
            ("Content-Type", "application/json"),
            ("Content-Length", "1048577"),
            ("X-Hub-Signature-256", signature),
        ])
        self.assertEqual((status, failure["code"]), (400, "invalid_request"))

    def test_late_check_failure_is_retained_without_replacing_newer_state(self):
        newer = payload(action="completed", pull_request=None, check_run={
            "id": 3001,
            "status": "completed",
            "conclusion": "success",
            "completed_at": "2026-09-01T12:00:00Z",
            "pull_requests": [{"id": 1001}],
        })
        older = payload(action="completed", pull_request=None, check_run={
            "id": 3002,
            "status": "completed",
            "conclusion": "failure",
            "completed_at": "2026-09-01T11:00:00Z",
            "pull_requests": [{"id": 1001}],
        })
        receipts = []
        for item in (newer, older):
            raw = encoded(item)
            status, _headers, receipt = self.request(raw, signed(raw))
            self.assertEqual(status, 202)
            receipts.append(receipt)
        principal = self.server.portfolio_service.authenticate(ADMIN)
        repository = self.server.portfolio_service.repository.outcomes
        contexts = [
            repository.context(
                principal,
                "github-one",
                receipt["source_delivery_id"] + ":0",
            )
            for receipt in receipts
        ]
        self.assertEqual({context["ordering_state"] for context in contexts},
                         {"authoritative", "late"})
        self.assertIn("late", {
            item["state"]
            for item in repository.coverage(principal)
            if item["connector_id"] == "github-one"
        })

    def test_storage_outage_cannot_acknowledge(self):
        from hormuz.outcome_repository import OutcomeRepository

        unavailable_config = replace(
            self.config,
            database_path=self.root / "unavailable.sqlite3",
        )
        receiver = GitHubOutcomeReceiver(
            unavailable_config,
            OutcomeRepository(unavailable_config, dsn=""),
        )
        raw = encoded(payload())
        with self.assertRaises(PortfolioError) as caught:
            receiver.ingest(signed(raw), raw)
        self.assertEqual(caught.exception.code, "unavailable")
        self.assertFalse(unavailable_config.database_path.exists())

    def test_independent_receivers_racing_exact_bytes_return_one_receipt(self):
        raw = encoded(payload())
        headers = signed(raw)
        second_repository = create_portfolio_repository(self.config).outcomes
        second = GitHubOutcomeReceiver(self.config, second_repository)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(
                lambda receiver: receiver.ingest(headers, raw),
                (self.server.github_outcome_receiver, second),
            ))
        self.assertEqual(results[0], results[1])
        snapshot = sqlite_snapshot(self.config.database_path)
        self.assertEqual(len(snapshot["rows"]["portfolio_outcome_receipts"]), 1)
        self.assertEqual(len(snapshot["rows"]["portfolio_outcome_events"]), 1)


if __name__ == "__main__":
    unittest.main()
