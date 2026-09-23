"""Provider-free production-path witnesses for the Linear connector runtime."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import hmac
import http.client
import json
from pathlib import Path
import sqlite3
import tempfile
import time
from types import MappingProxyType
import unittest
from unittest import mock

from hormuz.audit_chain import AuditChainError, AuditChainSource, build_audit_chain_entry
from hormuz.linear_connector import LinearOutcomeAdapter, LinearOutcomeReceiver
from hormuz.linear_evidence import validate_linear_evidence
from hormuz.linear_http import LINEAR_EVENTS_PATH
from hormuz.linear_webhook_auth import LinearWebhookAuthenticator
from hormuz.outcome_connector_config import (
    LinearWebhookSecretReference,
    OutcomeConnectorConfig,
    build_outcome_connector_config,
    resolve_outcome_connector_credentials,
)
from hormuz.portfolio_config import PortfolioConnectorBinding
from hormuz.portfolio_repository import create_portfolio_repository
from hormuz.portfolio_service import PortfolioService
from hormuz.portfolio_wire import BINDINGS, SCOPES, PortfolioError
from hormuz.server import GatewayServer, serve_in_thread
from hormuz.store import UsageStore

if __package__:
    from ._portfolio_fixture import ADMIN, binding_request, create_request, registry_config
    from ._registry_transition_fixture import sqlite_snapshot
else:
    from _portfolio_fixture import ADMIN, binding_request, create_request, registry_config
    from _registry_transition_fixture import sqlite_snapshot


WORKSPACE = "10000000-0000-4000-8000-000000000001"
PROJECT = "20000000-0000-4000-8000-000000000001"
PROJECT_TWO = "20000000-0000-4000-8000-000000000002"
WEBHOOK = "30000000-0000-4000-8000-000000000001"
TEAM = "40000000-0000-4000-8000-000000000001"
ISSUE = "50000000-0000-4000-8000-000000000001"
CYCLE = "70000000-0000-4000-8000-000000000001"
CYCLE_TWO = "70000000-0000-4000-8000-000000000002"
INITIATIVE = "80000000-0000-4000-8000-000000000001"
DELIVERY = "60000000-0000-4000-8000-000000000001"
WEBHOOK_SECRET = "synthetic-linear-webhook-secret-123456789"
PREVIOUS_SECRET = "synthetic-linear-previous-secret-12345678"
IDENTITY_KEY = "synthetic-linear-identity-key-1234567890"
ROTATED_IDENTITY_KEY = "synthetic-linear-identity-key-rotation-2"
ROTATED_WEBHOOK_SECRET = "synthetic-linear-rotated-secret-12345678"
NOW_MS = int(datetime(2026, 9, 23, 12, tzinfo=timezone.utc).timestamp() * 1000)


def linear_base_config(root: Path):
    base = registry_config(root)
    binding = PortfolioConnectorBinding(
        "acme",
        "linear-one",
        "linear",
        None,
        WORKSPACE,
        (PROJECT,),
    )
    authority = replace(
        base.portfolio_control,
        connectors=base.portfolio_control.connectors + (binding,),
    )
    return replace(base, portfolio_control=authority)


def runtime_document(*, previous: bool = False) -> dict:
    channel = {
        "organization_id": "acme",
        "connector_id": "linear-one",
        "binding_version": 1,
        "source_webhook_id": WEBHOOK,
        "source_team_ids": [TEAM],
        "typed_enrollment": {
            "initiative_ids": [INITIATIVE],
            "project_ids": [PROJECT],
            "cycle_ids": [CYCLE],
            "issue_ids": [ISSUE],
        },
        "active_webhook_secret": {
            "version": "linear-v2",
            "environment_variable": "SYNTHETIC_LINEAR_WEBHOOK_SECRET",
        },
        "previous_webhook_secret": None,
        "identity_keys": [{
            "version": "1",
            "environment_variable": "SYNTHETIC_LINEAR_IDENTITY_KEY",
        }],
        "current_key_version": "1",
        "body_fingerprint_key_version": "1",
        "source_fact_key_version": "1",
        "registered_by": "alice",
    }
    if previous:
        channel["previous_webhook_secret"] = {
            "version": "linear-v1",
            "environment_variable": "SYNTHETIC_LINEAR_PREVIOUS_SECRET",
            "expires_at": "2026-09-23T12:01:00Z",
        }
    return {
        "schema_id": "hormuz.outcome-connectors",
        "schema_version": 2,
        "github": [],
        "linear": [channel],
    }


def runtime_config(root: Path, *, previous: bool = False):
    base = linear_base_config(root)
    unresolved = build_outcome_connector_config(
        runtime_document(previous=previous),
        base.portfolio_control,
    )
    resolved = resolve_outcome_connector_credentials(unresolved, {
        "SYNTHETIC_LINEAR_WEBHOOK_SECRET": WEBHOOK_SECRET,
        "SYNTHETIC_LINEAR_PREVIOUS_SECRET": PREVIOUS_SECRET,
        "SYNTHETIC_LINEAR_IDENTITY_KEY": IDENTITY_KEY,
    })
    return replace(base, outcome_connectors=resolved)


def payload(**changes) -> dict:
    value = {
        "action": "create",
        "type": "Issue",
        "organizationId": WORKSPACE,
        "webhookId": WEBHOOK,
        "webhookTimestamp": NOW_MS,
        "createdAt": "2026-09-23T12:00:00Z",
        "data": {
            "id": ISSUE,
            "teamId": TEAM,
            "projectId": PROJECT,
            "cycleId": CYCLE,
            "updatedAt": "2026-09-23T12:00:00Z",
            "startedAt": "2026-09-23T12:00:00Z",
            "title": "SYNTHETIC_PRIVATE_LINEAR_TITLE",
            "description": "SYNTHETIC_PRIVATE_LINEAR_DESCRIPTION",
        },
    }
    value.update(changes)
    return value


def encoded(value: dict) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def signed(
    raw: bytes,
    *,
    secret: str = WEBHOOK_SECRET,
    delivery: str = DELIVERY,
    event: str = "Issue",
) -> dict[str, str]:
    return {
        "Linear-Signature": hmac.new(
            secret.encode("ascii"), raw, hashlib.sha256,
        ).hexdigest(),
        "Linear-Delivery": delivery,
        "Linear-Event": event,
    }


class LinearRuntimeConfigTests(unittest.TestCase):
    def setUp(self):
        self.base = linear_base_config(Path("/unused/linear-runtime-config"))

    def test_v1_github_shape_remains_accepted_and_v2_linear_is_strict(self):
        github = {
            "schema_id": "hormuz.outcome-connectors",
            "schema_version": 1,
            "github": [{
                "organization_id": "acme",
                "connector_id": "github-one",
                "webhook_secrets": [{
                    "version": "webhook-v1",
                    "environment_variable": "GITHUB_WEBHOOK",
                }],
                "identity_keys": [{
                    "version": "identity-v1",
                    "environment_variable": "GITHUB_IDENTITY",
                }],
                "current_key_version": "identity-v1",
                "delivery_identity_key_version": "identity-v1",
            }],
        }
        legacy = build_outcome_connector_config(github, self.base.portfolio_control)
        self.assertEqual((len(legacy.github), legacy.linear), (1, ()))

        unresolved = build_outcome_connector_config(
            runtime_document(previous=True), self.base.portfolio_control,
        )
        self.assertIsInstance(unresolved, OutcomeConnectorConfig)
        self.assertNotIn(WEBHOOK_SECRET, repr(unresolved))
        resolved = resolve_outcome_connector_credentials(unresolved, {
            "SYNTHETIC_LINEAR_WEBHOOK_SECRET": WEBHOOK_SECRET,
            "SYNTHETIC_LINEAR_PREVIOUS_SECRET": PREVIOUS_SECRET,
            "SYNTHETIC_LINEAR_IDENTITY_KEY": IDENTITY_KEY,
        })
        self.assertEqual(len(resolved.linear), 1)
        self.assertEqual(
            {kind for kind, _value in resolved.protected_values()},
            {"linear_webhook_secret", "outcome_identity_key"},
        )
        self.assertNotIn(WEBHOOK_SECRET, repr(resolved))

        for mutate in (
            lambda item: item.update(extra="forbidden"),
            lambda item: item["linear"][0].update(binding_version=True),
            lambda item: item["linear"][0]["typed_enrollment"].update(
                project_ids=[]
            ),
            lambda item: item["linear"][0].update(source_team_ids=[]),
        ):
            invalid = json.loads(json.dumps(runtime_document()))
            mutate(invalid)
            with self.subTest(document=invalid), self.assertRaisesRegex(
                ValueError, "outcome_connector_configuration_invalid",
            ):
                build_outcome_connector_config(invalid, self.base.portfolio_control)

    def test_workspace_owner_and_webhook_route_are_globally_unique(self):
        beta = PortfolioConnectorBinding(
            "beta", "linear-two", "linear", None, WORKSPACE, (PROJECT,),
        )
        authority = replace(
            self.base.portfolio_control,
            connectors=self.base.portfolio_control.connectors + (beta,),
        )
        with self.assertRaisesRegex(
            ValueError, "outcome_connector_configuration_invalid",
        ):
            build_outcome_connector_config(runtime_document(), authority)

        duplicate = runtime_document()
        second = json.loads(json.dumps(duplicate["linear"][0]))
        second.update(
            organization_id="beta",
            connector_id="linear-two",
            source_webhook_id=WEBHOOK,
        )
        second["active_webhook_secret"]["environment_variable"] = "BETA_SECRET"
        second["identity_keys"][0]["environment_variable"] = "BETA_IDENTITY"
        duplicate["linear"].append(second)
        with self.assertRaisesRegex(
            ValueError, "outcome_connector_configuration_invalid",
        ):
            build_outcome_connector_config(duplicate, authority)


class LinearAuthenticationAndNormalizationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.config = runtime_config(Path(temporary.name), previous=True)
        self.channel = self.config.outcome_connectors.linear[0]
        self.binding = self.config.portfolio_control.connectors[-1]
        self.authenticator = LinearWebhookAuthenticator(self.config, self.channel)
        self.adapter = LinearOutcomeAdapter(self.channel)

    def authenticate(self, value: dict, *, secret=WEBHOOK_SECRET, now_ms=NOW_MS):
        raw = encoded(value)
        verified, body = self.authenticator.authenticate(
            signed(raw, secret=secret, event=value.get("type", "Issue")),
            raw,
            now_ms=now_ms,
        )
        return verified, body, raw

    def test_hmac_precedes_parse_and_rotation_expiry(self):
        malformed = b'{"duplicate":1,"duplicate":2}'
        with self.assertRaises(PortfolioError) as caught:
            self.authenticator.authenticate(
                signed(malformed, secret="x" * 40), malformed, now_ms=NOW_MS,
            )
        self.assertEqual(caught.exception.code, "unauthenticated")
        with self.assertRaises(PortfolioError) as caught:
            self.authenticator.authenticate(
                signed(malformed), malformed, now_ms=NOW_MS,
            )
        self.assertEqual(caught.exception.code, "invalid_request")

        verified, _body, _raw = self.authenticate(payload(), secret=PREVIOUS_SECRET)
        self.assertEqual(verified.credential_version, "linear-v1")
        with self.assertRaises(PortfolioError) as caught:
            self.authenticate(
                payload(), secret=PREVIOUS_SECRET, now_ms=NOW_MS + 61_000,
            )
        self.assertEqual(caught.exception.code, "unauthenticated")

    def test_signed_scope_typed_enrollment_and_raw_bytes_fail_closed(self):
        for changed, code in (
            ({"organizationId": "90000000-0000-4000-8000-000000000001"}, "forbidden"),
            ({"webhookId": "90000000-0000-4000-8000-000000000001"}, "forbidden"),
            ({"type": "Unknown"}, "invalid_request"),
            ({"data": {"id": "90000000-0000-4000-8000-000000000001"}}, "forbidden"),
            ({"organization_id": "beta"}, "forbidden"),
        ):
            value = payload(**changed)
            raw = encoded(value)
            with self.subTest(changed=changed), self.assertRaises(PortfolioError) as caught:
                self.authenticator.authenticate(signed(raw), raw, now_ms=NOW_MS)
            self.assertEqual(caught.exception.code, code)

        value = payload()
        value["data"]["teamId"] = "90000000-0000-4000-8000-000000000001"
        raw = encoded(value)
        with self.assertRaises(PortfolioError) as caught:
            self.authenticator.authenticate(signed(raw), raw, now_ms=NOW_MS)
        self.assertEqual(caught.exception.code, "forbidden")

        raw = encoded(payload())
        mutated = raw.replace(b"PRIVATE_LINEAR_TITLE", b"PRIVATE_LINEAR_OTHER")
        with self.assertRaises(PortfolioError) as caught:
            self.authenticator.authenticate(signed(raw), mutated, now_ms=NOW_MS)
        self.assertEqual(caught.exception.code, "unauthenticated")

    def test_lifecycle_state_relationships_and_content_exclusion(self):
        verified, body, _raw = self.authenticate(payload())
        projection = self.adapter.normalize(
            binding=self.binding, verified=verified, body=body,
        )
        self.assertEqual(
            (
                projection.context["lifecycle"],
                projection.context["normalized_state"],
                projection.context["relationship_coverage"],
            ),
            ("created", "in_progress", "complete"),
        )
        self.assertEqual(
            projection.context["relationships"],
            [
                {"kind": "cycle_issue", "parent": {"kind": "cycle", "id": CYCLE}},
                {"kind": "project_issue", "parent": {"kind": "project", "id": PROJECT}},
            ],
        )
        self.assertEqual(projection.outcome["event_type"], "created")
        self.assertNotIn("SYNTHETIC_PRIVATE", json.dumps(projection.source_fact))

        value = payload(action="update", updatedFrom={"archivedAt": None})
        value["data"]["archivedAt"] = "2026-09-23T12:01:00Z"
        value["data"]["parentId"] = "90000000-0000-4000-8000-000000000001"
        verified, body, _raw = self.authenticate(value)
        projection = self.adapter.normalize(
            binding=self.binding, verified=verified, body=body,
        )
        self.assertEqual(projection.context["lifecycle"], "archived")
        self.assertEqual(projection.context["relationship_coverage"], "partial")

        value = payload(
            action="update",
            updatedFrom={"archivedAt": "2026-09-23T12:01:00Z"},
        )
        value["data"]["archivedAt"] = None
        verified, body, _raw = self.authenticate(value)
        projection = self.adapter.normalize(
            binding=self.binding, verified=verified, body=body,
        )
        self.assertEqual(projection.context["lifecycle"], "restored")
        self.assertEqual(projection.outcome["event_type"], "reopened")

        value = payload(action="remove")
        verified, body, _raw = self.authenticate(value)
        projection = self.adapter.normalize(
            binding=self.binding, verified=verified, body=body,
        )
        self.assertEqual(projection.context["lifecycle"], "deleted")
        self.assertEqual(
            (projection.outcome["event_type"], projection.outcome["state"]),
            ("deleted", "tombstoned"),
        )

    def test_update_outcomes_require_matching_transition_field(self):
        unrelated = payload(action="update", updatedFrom={"title": "before"})
        verified, body, _raw = self.authenticate(unrelated)
        projection = self.adapter.normalize(
            binding=self.binding,
            verified=verified,
            body=body,
        )
        self.assertEqual(projection.context["normalized_state"], "in_progress")
        self.assertIsNone(projection.outcome)

        cases = (
            ("startedAt", "started"),
            ("completedAt", "completed"),
            ("canceledAt", "canceled"),
        )
        for field, expected in cases:
            value = payload(action="update", updatedFrom={field: None})
            if field != "startedAt":
                value["data"][field] = "2026-09-23T12:01:00Z"
            verified, body, _raw = self.authenticate(value)
            projection = self.adapter.normalize(
                binding=self.binding,
                verified=verified,
                body=body,
            )
            with self.subTest(field=field):
                self.assertEqual(projection.outcome["event_type"], expected)

        state_change = payload(
            action="update",
            updatedFrom={"state": {"type": "unstarted"}},
        )
        state_change["data"].pop("startedAt")
        state_change["data"]["state"] = {"type": "started"}
        verified, body, _raw = self.authenticate(state_change)
        projection = self.adapter.normalize(
            binding=self.binding,
            verified=verified,
            body=body,
        )
        self.assertEqual(projection.outcome["event_type"], "started")

    def test_explicit_empty_relationship_sets_are_complete(self):
        cases = (
            (
                "Issue",
                {
                    "id": ISSUE,
                    "teamId": TEAM,
                    "projectId": None,
                    "cycleId": None,
                    "updatedAt": "2026-09-23T12:00:00Z",
                },
            ),
            (
                "Project",
                {
                    "id": PROJECT,
                    "teamId": TEAM,
                    "initiatives": [],
                    "updatedAt": "2026-09-23T12:00:00Z",
                },
            ),
            (
                "Initiative",
                {
                    "id": INITIATIVE,
                    "teamId": TEAM,
                    "parentInitiatives": [],
                    "updatedAt": "2026-09-23T12:00:00Z",
                },
            ),
        )
        for event_type, data in cases:
            value = payload(type=event_type, data=data)
            verified, body, _raw = self.authenticate(value)
            projection = self.adapter.normalize(
                binding=self.binding,
                verified=verified,
                body=body,
            )
            with self.subTest(event_type=event_type):
                self.assertEqual(projection.context["relationships"], [])
                self.assertEqual(
                    projection.context["relationship_coverage"],
                    "complete",
                )

    def test_all_parent_entity_types_and_relationship_limits_are_closed(self):
        cases = (
            (
                "Project",
                {
                    "id": PROJECT,
                    "teamId": TEAM,
                    "updatedAt": "2026-09-23T12:00:00Z",
                    "initiatives": [{"id": INITIATIVE}],
                },
                "complete",
            ),
            (
                "Initiative",
                {
                    "id": INITIATIVE,
                    "teamId": TEAM,
                    "updatedAt": "2026-09-23T12:00:00Z",
                },
                "unknown",
            ),
            (
                "Cycle",
                {
                    "id": CYCLE,
                    "teamId": TEAM,
                    "updatedAt": "2026-09-23T12:00:00Z",
                },
                "not_applicable",
            ),
        )
        for event_type, data, coverage in cases:
            value = payload(type=event_type, data=data)
            verified, body, _raw = self.authenticate(value)
            projection = self.adapter.normalize(
                binding=self.binding,
                verified=verified,
                body=body,
            )
            self.assertEqual(projection.context["object"]["kind"], event_type.lower())
            self.assertEqual(projection.context["relationship_coverage"], coverage)
            self.assertIsNone(projection.outcome)

        value = payload(
            type="Initiative",
            data={
                "id": INITIATIVE,
                "teamId": TEAM,
                "updatedAt": "2026-09-23T12:00:00Z",
                "parentInitiativeId": INITIATIVE,
            },
        )
        verified, body, _raw = self.authenticate(value)
        with self.assertRaises(PortfolioError) as caught:
            self.adapter.normalize(
                binding=self.binding,
                verified=verified,
                body=body,
            )
        self.assertEqual(caught.exception.code, "invalid_request")

        parents = [
            {"id": f"{index:08x}-0000-4000-8000-000000000001"}
            for index in range(1, 101)
        ]
        value["data"] = {
            "id": INITIATIVE,
            "teamId": TEAM,
            "updatedAt": "2026-09-23T12:00:00Z",
            "parentInitiativeId": "90000000-0000-4000-8000-000000000001",
            "parentInitiatives": parents,
        }
        verified, body, _raw = self.authenticate(value)
        with self.assertRaises(PortfolioError) as caught:
            self.adapter.normalize(
                binding=self.binding,
                verified=verified,
                body=body,
            )
        self.assertEqual(caught.exception.code, "invalid_request")


class LinearSQLiteRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = runtime_config(self.root)
        UsageStore(self.config.database_path)
        self.repositories = create_portfolio_repository(self.config)
        self.receiver = LinearOutcomeReceiver(self.config, self.repositories.linear)

    def rows(self, table: str) -> list[dict]:
        connection = sqlite3.connect(self.config.database_path)
        connection.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in connection.execute(
                f"SELECT * FROM {table}",
            ).fetchall()]
        finally:
            connection.close()

    def ingest(
        self,
        value: dict | None = None,
        *,
        delivery: str = DELIVERY,
        now_ms: int = NOW_MS,
    ) -> dict:
        raw = encoded(payload() if value is None else value)
        return self.receiver.ingest(
            signed(raw, delivery=delivery), raw, now_ms=now_ms,
        )

    def assert_error(self, code: str, value: dict, *, delivery=DELIVERY, now_ms=NOW_MS):
        raw = encoded(value)
        before = sqlite_snapshot(self.config.database_path)
        with self.assertRaises(PortfolioError) as caught:
            self.receiver.ingest(
                signed(raw, delivery=delivery), raw, now_ms=now_ms,
            )
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(sqlite_snapshot(self.config.database_path), before)

    def test_atomic_metadata_commit_audit_chain_and_content_exclusion(self):
        receipt = self.ingest()
        self.assertEqual(
            (receipt["disposition"], receipt["accepted_event_count"]),
            ("accepted", 2),
        )
        self.assertEqual(len(self.rows("portfolio_linear_source_binding_versions")), 1)
        self.assertEqual(len(self.rows("gateway_linear_delivery_receipts")), 1)
        context = self.rows("portfolio_linear_context_events")
        self.assertEqual(len(context), 1)
        self.assertEqual(
            (context[0]["ordering_state"], context[0]["scope_state"]),
            ("current", "unmatched"),
        )
        self.assertEqual(len(self.rows("portfolio_outcome_receipts")), 1)
        self.assertEqual(len(self.rows("portfolio_outcome_events")), 1)
        sources = {
            row["source_schema_id"]
            for row in self.rows("gateway_audit_chain_entries")
        }
        self.assertTrue({
            "hormuz.linear-source-binding-version",
            "hormuz.linear-delivery-receipt",
            "hormuz.linear-context-event",
        }.issubset(sources))
        database_bytes = self.config.database_path.read_bytes()
        self.assertNotIn(b"SYNTHETIC_PRIVATE_LINEAR_TITLE", database_bytes)
        self.assertNotIn(b"SYNTHETIC_PRIVATE_LINEAR_DESCRIPTION", database_bytes)

    def test_unrelated_issue_update_does_not_duplicate_state_outcome(self):
        self.ingest()
        update = payload(action="update", updatedFrom={"title": "before"})
        update["data"]["updatedAt"] = "2026-09-23T12:00:01Z"
        result = self.ingest(
            update,
            delivery="60000000-0000-4000-8000-000000000010",
        )
        self.assertEqual(result["accepted_event_count"], 1)
        self.assertEqual(len(self.rows("portfolio_linear_context_events")), 2)
        self.assertEqual(len(self.rows("portfolio_outcome_events")), 1)

    def test_linear_audit_evidence_rejects_extra_content_and_digest_tampering(self):
        self.ingest()
        context = json.loads(
            self.rows("portfolio_linear_context_events")[0]["evidence_json"]
        )
        context["title"] = "SYNTHETIC_PRIVATE_LINEAR_TITLE"
        with self.assertRaisesRegex(ValueError, "linear_evidence_invalid"):
            validate_linear_evidence("hormuz.linear-context-event", context)
        with self.assertRaisesRegex(AuditChainError, "audit_chain_event_malformed"):
            build_audit_chain_entry(
                context,
                chain_version=1,
                chain_epoch=1,
                sequence=1,
                previous_digest=None,
                entry_schema_version=2,
                source=AuditChainSource(
                    "hormuz.linear-context-event",
                    1,
                    context["context_event_id"],
                ),
            )

        binding = json.loads(
            self.rows("portfolio_linear_source_binding_versions")[0]["evidence_json"]
        )
        binding["credential_version"] = "linear-v999"
        with self.assertRaisesRegex(ValueError, "linear_evidence_invalid"):
            validate_linear_evidence(
                "hormuz.linear-source-binding-version",
                binding,
            )

    def test_exact_body_and_source_fact_replays_do_not_duplicate_rows(self):
        first = self.ingest()
        before = sqlite_snapshot(self.config.database_path)
        changed_delivery = "60000000-0000-4000-8000-000000000002"
        self.assertEqual(self.ingest(delivery=changed_delivery), first)
        self.assertEqual(sqlite_snapshot(self.config.database_path), before)

        retried = payload(webhookTimestamp=NOW_MS + 1_000)
        self.assertEqual(
            self.ingest(
                retried,
                delivery="60000000-0000-4000-8000-000000000003",
                now_ms=NOW_MS + 1_000,
            ),
            first,
        )
        self.assertEqual(sqlite_snapshot(self.config.database_path), before)

    def test_secret_rotation_requires_and_commits_next_binding_version(self):
        first = self.ingest()
        before = sqlite_snapshot(self.config.database_path)
        channel = self.config.outcome_connectors.linear[0]
        rotated = replace(
            channel,
            active_webhook_secret=LinearWebhookSecretReference(
                "linear-v3",
                "SYNTHETIC_LINEAR_ROTATED_SECRET",
                None,
                ROTATED_WEBHOOK_SECRET.encode("ascii"),
            ),
            previous_webhook_secret=replace(
                channel.active_webhook_secret,
                expires_at="2026-09-23T12:01:00.000000Z",
            ),
        )
        changed = payload(action="update")
        changed["data"]["updatedAt"] = "2026-09-23T12:00:01Z"
        raw = encoded(changed)

        invalid_config = replace(
            self.config,
            outcome_connectors=replace(
                self.config.outcome_connectors,
                linear=(rotated,),
            ),
        )
        invalid_receiver = LinearOutcomeReceiver(
            invalid_config,
            create_portfolio_repository(invalid_config).linear,
        )
        with self.assertRaises(PortfolioError) as caught:
            invalid_receiver.ingest(
                signed(
                    raw,
                    secret=ROTATED_WEBHOOK_SECRET,
                    delivery="60000000-0000-4000-8000-000000000007",
                ),
                raw,
                now_ms=NOW_MS,
            )
        self.assertEqual(caught.exception.code, "version_conflict")
        self.assertEqual(sqlite_snapshot(self.config.database_path), before)

        rotated = replace(rotated, binding_version=2)
        rotated_config = replace(
            self.config,
            outcome_connectors=replace(
                self.config.outcome_connectors,
                linear=(rotated,),
            ),
        )
        receiver = LinearOutcomeReceiver(
            rotated_config,
            create_portfolio_repository(rotated_config).linear,
        )
        accepted = receiver.ingest(
            signed(
                raw,
                secret=ROTATED_WEBHOOK_SECRET,
                delivery="60000000-0000-4000-8000-000000000007",
            ),
            raw,
            now_ms=NOW_MS,
        )
        self.assertEqual(accepted["disposition"], "accepted")
        self.assertEqual(
            [row["version"] for row in self.rows("portfolio_linear_source_binding_versions")],
            [1, 2],
        )

        original = encoded(payload())
        self.assertEqual(
            receiver.ingest(
                signed(original, secret=WEBHOOK_SECRET),
                original,
                now_ms=NOW_MS,
            ),
            first,
        )

        rollback = payload(action="update", updatedFrom={"startedAt": None})
        rollback["data"]["updatedAt"] = "2026-09-23T12:00:02Z"
        self.assert_error(
            "version_conflict",
            rollback,
            delivery="60000000-0000-4000-8000-000000000011",
        )

    def test_current_identity_key_rotation_preserves_existing_binding(self):
        self.ingest()
        channel = self.config.outcome_connectors.linear[0]
        next_key = replace(
            channel.identity_keys[0],
            version="2",
            environment_variable="SYNTHETIC_LINEAR_IDENTITY_KEY_2",
            value=ROTATED_IDENTITY_KEY.encode("ascii"),
        )
        rotated = replace(
            channel,
            identity_keys=(*channel.identity_keys, next_key),
            current_key_version="2",
        )
        rotated_config = replace(
            self.config,
            outcome_connectors=replace(
                self.config.outcome_connectors,
                linear=(rotated,),
            ),
        )
        receiver = LinearOutcomeReceiver(
            rotated_config,
            create_portfolio_repository(rotated_config).linear,
        )
        update = payload(action="update", updatedFrom={"title": "before"})
        update["data"]["updatedAt"] = "2026-09-23T12:00:01Z"
        raw = encoded(update)
        result = receiver.ingest(
            signed(
                raw,
                delivery="60000000-0000-4000-8000-000000000012",
            ),
            raw,
            now_ms=NOW_MS,
        )
        self.assertEqual(result["disposition"], "accepted")
        self.assertEqual(len(self.rows("portfolio_linear_source_binding_versions")), 1)
        self.assertEqual(len(self.rows("portfolio_linear_context_events")), 2)

    def test_conflicting_delivery_and_unknown_stale_body_fail_closed(self):
        self.ingest()
        conflict = payload()
        conflict["data"]["updatedAt"] = "2026-09-23T12:00:01Z"
        self.assert_error("idempotency_conflict", conflict)

        stale_now = NOW_MS + 120_000
        original = payload()
        before = sqlite_snapshot(self.config.database_path)
        self.assertEqual(self.ingest(original, now_ms=stale_now)["disposition"], "accepted")
        self.assertEqual(sqlite_snapshot(self.config.database_path), before)
        unknown = payload()
        unknown["data"]["updatedAt"] = "2026-09-23T12:00:02Z"
        self.assert_error(
            "unauthenticated",
            unknown,
            delivery="60000000-0000-4000-8000-000000000004",
            now_ms=stale_now,
        )

    def test_concurrent_replay_commits_one_receipt(self):
        raw = encoded(payload())
        headers = signed(raw)

        def ingest(_index):
            return self.receiver.ingest(headers, raw, now_ms=NOW_MS)

        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(ingest, range(6)))
        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual(len(self.rows("gateway_linear_delivery_receipts")), 1)
        self.assertEqual(len(self.rows("portfolio_linear_context_events")), 1)

    def test_late_revision_is_descriptive_and_does_not_replace_current(self):
        newest = payload(action="update")
        newest["data"]["updatedAt"] = "2026-09-23T12:10:00Z"
        self.ingest(newest)
        older = payload(action="update")
        older["data"]["updatedAt"] = "2026-09-23T12:05:00Z"
        self.ingest(
            older,
            delivery="60000000-0000-4000-8000-000000000005",
        )
        states = [
            row["ordering_state"]
            for row in sorted(
                self.rows("portfolio_linear_context_events"),
                key=lambda item: item["commit_sequence"],
            )
        ]
        self.assertEqual(states, ["current", "late"])

    def test_partial_relationship_revision_does_not_supersede_complete_context(self):
        self.ingest()
        partial = payload(action="update")
        partial["data"]["updatedAt"] = "2026-09-23T12:01:00Z"
        partial["data"]["parentId"] = "90000000-0000-4000-8000-000000000001"
        self.ingest(
            partial,
            delivery="60000000-0000-4000-8000-000000000008",
        )
        rows = sorted(
            self.rows("portfolio_linear_context_events"),
            key=lambda item: item["commit_sequence"],
        )
        latest = json.loads(rows[-1]["evidence_json"])
        self.assertEqual(
            (latest["ordering_state"], latest["relationship_coverage"]),
            ("current", "partial"),
        )
        self.assertIsNone(latest["supersedes_context_event_id"])
        validate_linear_evidence("hormuz.linear-context-event", latest)

    def test_complete_project_and_cycle_move_supersedes_without_rewriting_history(self):
        authority = self.config.portfolio_control
        linear_binding = replace(
            authority.connectors[-1],
            external_object_ids=(PROJECT, PROJECT_TWO),
        )
        channel = self.config.outcome_connectors.linear[0]
        typed = dict(channel.typed_enrollment)
        typed["project"] = (PROJECT, PROJECT_TWO)
        typed["cycle"] = (CYCLE, CYCLE_TWO)
        channel = replace(
            channel,
            typed_enrollment=MappingProxyType(typed),
        )
        self.config = replace(
            self.config,
            portfolio_control=replace(
                authority,
                connectors=(*authority.connectors[:-1], linear_binding),
            ),
            outcome_connectors=replace(
                self.config.outcome_connectors,
                linear=(channel,),
            ),
        )
        self.repositories = create_portfolio_repository(self.config)
        self.receiver = LinearOutcomeReceiver(self.config, self.repositories.linear)

        self.ingest()
        moved = payload(action="update")
        moved["data"]["updatedAt"] = "2026-09-23T12:01:00Z"
        moved["data"]["projectId"] = PROJECT_TWO
        moved["data"]["cycleId"] = CYCLE_TWO
        self.ingest(
            moved,
            delivery="60000000-0000-4000-8000-000000000009",
        )
        rows = sorted(
            self.rows("portfolio_linear_context_events"),
            key=lambda item: item["commit_sequence"],
        )
        earlier, latest = (json.loads(row["evidence_json"]) for row in rows)
        self.assertEqual(
            earlier["relationships"],
            [
                {"kind": "cycle_issue", "parent": {"kind": "cycle", "id": CYCLE}},
                {"kind": "project_issue", "parent": {"kind": "project", "id": PROJECT}},
            ],
        )
        self.assertEqual(
            latest["relationships"],
            [
                {"kind": "cycle_issue", "parent": {"kind": "cycle", "id": CYCLE_TWO}},
                {"kind": "project_issue", "parent": {"kind": "project", "id": PROJECT_TWO}},
            ],
        )
        self.assertEqual(
            latest["supersedes_context_event_id"],
            earlier["context_event_id"],
        )
        self.assertEqual(len(rows), 2)

    def test_explicit_empty_issue_relationships_supersede_complete_context(self):
        self.ingest()
        cleared = payload(
            action="update",
            updatedFrom={"projectId": PROJECT, "cycleId": CYCLE},
        )
        cleared["data"]["updatedAt"] = "2026-09-23T12:01:00Z"
        cleared["data"]["projectId"] = None
        cleared["data"]["cycleId"] = None
        self.ingest(
            cleared,
            delivery="60000000-0000-4000-8000-000000000013",
        )
        rows = sorted(
            self.rows("portfolio_linear_context_events"),
            key=lambda item: item["commit_sequence"],
        )
        earlier, latest = (json.loads(row["evidence_json"]) for row in rows)
        self.assertEqual(latest["relationships"], [])
        self.assertEqual(latest["relationship_coverage"], "complete")
        self.assertEqual(
            latest["supersedes_context_event_id"],
            earlier["context_event_id"],
        )

    def test_future_source_time_cannot_match_a_registry_binding(self):
        service = PortfolioService(self.config, self.repositories.registry)
        status, scope = service.dispatch(
            ADMIN,
            "POST",
            SCOPES,
            body=json.dumps(create_request()).encode("utf-8"),
            idempotency_key="linear-future-scope",
        )
        self.assertEqual(status, 201)
        request = binding_request(
            scope,
            connector_id="linear-one",
            external_object_id=PROJECT,
        )
        status, _binding = service.dispatch(
            ADMIN,
            "POST",
            BINDINGS,
            body=json.dumps(request).encode("utf-8"),
            idempotency_key="linear-future-binding",
        )
        self.assertEqual(status, 201)

        future = payload(action="update", createdAt="2026-09-24T12:00:00Z")
        future["data"]["updatedAt"] = "2026-09-24T12:00:00Z"
        self.ingest(future)
        context = json.loads(
            self.rows("portfolio_linear_context_events")[0]["evidence_json"]
        )
        self.assertEqual(context["scope_state"], "excluded")
        self.assertIsNone(context["binding"])
        validate_linear_evidence("hormuz.linear-context-event", context)

    def test_failure_after_staged_rows_rolls_back_everything(self):
        repository = self.repositories.linear
        original = repository._append_audit

        def fail(sql, event, schema_id):
            if schema_id == "hormuz.linear-delivery-receipt":
                raise RuntimeError("synthetic failure before commit")
            return original(sql, event, schema_id)

        before = sqlite_snapshot(self.config.database_path)
        with mock.patch.object(repository, "_append_audit", side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
                self.ingest()
        self.assertEqual(sqlite_snapshot(self.config.database_path), before)
        self.assertEqual(self.ingest()["disposition"], "accepted")

    def test_storage_rejects_cross_tenant_workspace_and_cross_route_webhook(self):
        self.ingest()
        source = self.rows("portfolio_linear_source_binding_versions")[0]

        def conflicting(**changes):
            row = dict(source)
            row.update(changes)
            connection = sqlite3.connect(self.config.database_path)
            try:
                with connection:
                    connection.execute(
                        "INSERT INTO portfolio_linear_source_binding_versions "
                        f"({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
                        tuple(row.values()),
                    )
            finally:
                connection.close()

        with self.assertRaisesRegex(
            sqlite3.IntegrityError, "linear_binding_cardinality_conflict",
        ):
            conflicting(
                organization_id="beta",
                connector_id="linear-two",
                binding_event_id="90000000-0000-4000-8000-000000000001",
                source_webhook_id="90000000-0000-4000-8000-000000000002",
                request_digest="1" * 64,
            )
        with self.assertRaisesRegex(
            sqlite3.IntegrityError, "linear_binding_cardinality_conflict",
        ):
            conflicting(
                connector_id="linear-two",
                binding_event_id="90000000-0000-4000-8000-000000000003",
                source_workspace_id="90000000-0000-4000-8000-000000000004",
                request_digest="2" * 64,
            )


class LinearHTTPRuntimeTests(unittest.TestCase):
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
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=10,
        )
        connection.request("POST", LINEAR_EVENTS_PATH, body=raw, headers={
            "Content-Type": "application/json",
            **headers,
        })
        response = connection.getresponse()
        body = json.loads(response.read())
        result = response.status, dict(response.getheaders()), body
        connection.close()
        return result

    def test_http_200_follows_durable_commit_and_outage_is_non_200(self):
        raw = encoded(payload(webhookTimestamp=int(time.time() * 1000)))
        status, headers, receipt = self.request(raw, signed(raw))
        self.assertEqual(status, 200)
        self.assertEqual(receipt["disposition"], "accepted")
        self.assertIn("hormuz.connector-ingest-receipt", headers["X-Hormuz-Contract"])
        connection = sqlite3.connect(self.config.database_path)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM gateway_linear_delivery_receipts"
                ).fetchone()[0],
                1,
            )
        finally:
            connection.close()

        second = payload(webhookTimestamp=int(time.time() * 1000))
        second["data"]["updatedAt"] = "2026-09-23T12:00:01Z"
        raw = encoded(second)
        before = sqlite_snapshot(self.config.database_path)
        with mock.patch.object(
            self.server.linear_outcome_receiver.repository,
            "replay_body",
            side_effect=PortfolioError("unavailable"),
        ):
            status, _headers, failure = self.request(
                raw,
                signed(
                    raw,
                    delivery="60000000-0000-4000-8000-000000000006",
                ),
            )
        self.assertEqual((status, failure["code"]), (503, "unavailable"))
        self.assertEqual(sqlite_snapshot(self.config.database_path), before)

    def test_four_second_budget_returns_non_200_without_commit(self):
        raw = encoded(payload(webhookTimestamp=int(time.time() * 1000)))
        before = sqlite_snapshot(self.config.database_path)

        def delayed(**_kwargs):
            time.sleep(4.05)
            return None

        with mock.patch.object(
            self.server.linear_outcome_receiver.repository,
            "replay_body",
            side_effect=delayed,
        ):
            status, _headers, failure = self.request(raw, signed(raw))
        self.assertEqual((status, failure["code"]), (503, "unavailable"))
        self.assertEqual(sqlite_snapshot(self.config.database_path), before)


if __name__ == "__main__":
    unittest.main()
