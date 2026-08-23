from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import replace
import http.client
import io
import json
import os
from pathlib import Path
import sqlite3
import socket
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote
from unittest import mock
from uuid import uuid4

import hormuz.postgres as postgres_module
from hormuz.audit_chain import build_audit_chain_checkpoint
from hormuz.cli import build_parser, main
from hormuz.config import GatewayConfig, Identity, PostgresPoolConfig, UsageStorageConfig
from hormuz.contracts import validate_contract, validate_policy_control_event
from hormuz.evidence import EvidenceStorageError
from hormuz.policy import PolicyEngine
from hormuz.policy_control import PolicyControlService
from hormuz.policy_repository import PolicyAdministrator, PolicyControlError
from hormuz.policy_runtime import PolicyRuntime
from hormuz.postgres import (
    PostgresConnectionPool,
    PostgresStorageError,
    migrate_postgres,
    postgres_transaction,
    verify_postgres_schema,
)
from hormuz.postgres_policy_store import PostgresPolicyControlStore
from hormuz.postgres_usage_store import PostgresUsageStore
from hormuz.server import GatewayServer, serve_in_thread
from hormuz.store import RequestAttemptStateError, ReservationDenied, ReservationScope, UsageStore
from hormuz.store_router import create_usage_store


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "contracts"


class _BlockingReplicaBudgetProviderHandler(BaseHTTPRequestHandler):
    """Hold one provider response so another gateway observes its reservation."""

    protocol_version = "HTTP/1.1"
    first_request_started = threading.Event()
    release_first_response = threading.Event()
    request_count = 0
    request_lock = threading.Lock()

    @classmethod
    def reset(cls) -> None:
        cls.first_request_started = threading.Event()
        cls.release_first_response = threading.Event()
        cls.request_count = 0

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        with self.request_lock:
            type(self).request_count += 1
            ordinal = type(self).request_count

        if ordinal != 1:
            self._send_json({"error": "unexpected provider admission"}, status=500)
            return

        type(self).first_request_started.set()
        if not type(self).release_first_response.wait(timeout=10):
            self._send_json({"error": "provider release timed out"}, status=503)
            return

        self._send_json(
            {
                "id": "resp_replica_budget",
                "object": "response",
                "status": "completed",
                "model": payload["model"],
                "output": [],
                "usage": {
                    "input_tokens": 1,
                    "output_tokens": 0,
                    "total_tokens": 1,
                },
            }
        )

    def _send_json(self, value: dict[str, object], *, status: int = 200) -> None:
        body = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("x-request-id", "req_replica_budget")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


class _ReplicaPolicyProviderHandler(BaseHTTPRequestHandler):
    """Record only provider admission for cross-replica policy tests."""

    protocol_version = "HTTP/1.1"
    request_count = 0
    request_lock = threading.Lock()

    @classmethod
    def reset(cls) -> None:
        cls.request_count = 0

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        with self.request_lock:
            type(self).request_count += 1

        self._send_json(
            {
                "id": "resp_replica_policy",
                "object": "response",
                "status": "completed",
                "model": payload["model"],
                "output": [],
                "usage": {
                    "input_tokens": 1,
                    "output_tokens": 0,
                    "total_tokens": 1,
                },
            }
        )

    def _send_json(self, value: dict[str, object], *, status: int = 200) -> None:
        body = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("x-request-id", "req_replica_policy")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


@unittest.skipUnless(
    os.environ.get("HORMUZ_TEST_POSTGRES_DSN"),
    "Set HORMUZ_TEST_POSTGRES_DSN and install hormuz[postgres] to run PostgreSQL conformance",
)
class PostgresUsageStoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            import psycopg
            import psycopg_pool
            from psycopg import sql
        except ImportError as error:  # pragma: no cover - skip guard documents the dependency
            raise unittest.SkipTest("Psycopg and psycopg-pool are not installed") from error
        cls.psycopg = psycopg
        cls.psycopg_pool = psycopg_pool
        cls.sql = sql
        cls.owner_dsn = os.environ["HORMUZ_TEST_POSTGRES_DSN"]
        suffix = uuid4().hex[:12]
        cls.schema = f"hormuz_test_{suffix}"
        cls.runtime_role = f"hormuz_runtime_{suffix}"
        cls.runtime_password = "hormuz-runtime-test-password"
        cls.runtime_dsn = _runtime_dsn(cls.owner_dsn, cls.runtime_role, cls.runtime_password)
        cls.policy_control_role = f"hormuz_policy_control_{suffix}"
        cls.policy_control_password = "hormuz-policy-control-test-password"
        cls.policy_control_dsn = _runtime_dsn(
            cls.owner_dsn,
            cls.policy_control_role,
            cls.policy_control_password,
        )
        cls.addClassCleanup(cls._cleanup_test_resources)
        with psycopg.connect(cls.owner_dsn, autocommit=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS"
                    ).format(sql.Identifier(cls.runtime_role), sql.Literal(cls.runtime_password))
                )
                cursor.execute(
                    sql.SQL(
                        "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS"
                    ).format(sql.Identifier(cls.policy_control_role), sql.Literal(cls.policy_control_password))
                )
        first = migrate_postgres(
            cls.owner_dsn,
            schema=cls.schema,
            runtime_role=cls.runtime_role,
            policy_control_role=cls.policy_control_role,
        )
        second = migrate_postgres(
            cls.owner_dsn,
            schema=cls.schema,
            runtime_role=cls.runtime_role,
            policy_control_role=cls.policy_control_role,
        )
        if first != second:
            raise AssertionError("PostgreSQL migrations are not idempotent")

    @classmethod
    def _cleanup_test_resources(cls) -> None:
        if not hasattr(cls, "psycopg"):
            return
        with cls.psycopg.connect(cls.owner_dsn, autocommit=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(cls.sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(cls.sql.Identifier(cls.schema)))
                cursor.execute(cls.sql.SQL("DROP ROLE IF EXISTS {}").format(cls.sql.Identifier(cls.runtime_role)))
                cursor.execute(cls.sql.SQL("DROP ROLE IF EXISTS {}").format(cls.sql.Identifier(cls.policy_control_role)))

    def setUp(self) -> None:
        with self.psycopg.connect(self.owner_dsn, autocommit=True) as connection:
            with connection.cursor() as cursor:
                tables = (
                    "policy_control_events",
                    "policy_active_versions",
                    "policy_versions",
                    "policy_administrators",
                    "policy_tenants",
                    "gateway_audit_chain_checkpoints",
                    "gateway_audit_chain_entries",
                    "gateway_audit_chain_heads",
                    "gateway_audit_chain_epochs",
                    "gateway_request_attempt_events",
                    "gateway_budget_reservations",
                    "gateway_request_attempts",
                    "gateway_secret_events",
                    "gateway_usage_events",
                )
                cursor.execute(
                    self.sql.SQL("TRUNCATE TABLE {}")
                    .format(
                        self.sql.SQL(", ").join(
                            self.sql.SQL("{}.{}").format(
                                self.sql.Identifier(self.schema),
                                self.sql.Identifier(table),
                            )
                            for table in tables
                        )
                    )
                )
        self.store = PostgresUsageStore(
            self.runtime_dsn,
            organization_ids=("acme", "beta"),
            schema=self.schema,
            runtime_role=self.runtime_role,
        )

    def _runtime_pool(self, **overrides: int) -> PostgresConnectionPool:
        pool = PostgresConnectionPool(
            self.runtime_dsn,
            settings=PostgresPoolConfig(**overrides),
        )
        self.addCleanup(pool.close)
        return pool

    def _create_schema_v2_fixture(self) -> str:
        """Apply the exact bundled v1/v2 PostgreSQL migrations, but not v3."""

        schema = f"{self.schema}_v2_{uuid4().hex[:8]}"
        quoted_schema = postgres_module._quote_identifier(schema)
        quoted_runtime_role = postgres_module._quote_identifier(self.runtime_role)
        quoted_policy_control_role = postgres_module._quote_identifier(self.policy_control_role)
        with self.psycopg.connect(self.owner_dsn, autocommit=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(self.sql.SQL("CREATE SCHEMA {}").format(self.sql.Identifier(schema)))
                cursor.execute(
                    self.sql.SQL(
                        """
                        CREATE TABLE {}.hormuz_schema_migrations (
                            version INTEGER PRIMARY KEY,
                            state TEXT NOT NULL,
                            applied_at TIMESTAMPTZ
                        )
                        """
                    ).format(self.sql.Identifier(schema))
                )
                for version in (1, 2):
                    cursor.execute(
                        postgres_module._migration_sql(
                            version,
                            quoted_schema,
                            quoted_runtime_role,
                            quoted_policy_control_role,
                        )
                    )
                    cursor.execute(
                        self.sql.SQL(
                            "INSERT INTO {}.hormuz_schema_migrations (version, state, applied_at) "
                            "VALUES (%s, 'applied', CURRENT_TIMESTAMP)"
                        ).format(self.sql.Identifier(schema)),
                        (version,),
                    )
        self.addCleanup(self._drop_schema, schema)
        return schema

    def _insert_v2_evidence(self, schema: str) -> None:
        """Emulate evidence written by the v2 binary before the v4 migration exists."""

        with postgres_transaction(
            self.runtime_dsn,
            schema=schema,
            runtime_role=self.runtime_role,
            organization_id="acme",
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO gateway_usage_events (
                        id, occurred_at, evidence_schema_id, evidence_schema_version,
                        organization_id, actor_id, actor_name, team_id, team_name,
                        identity_type, authentication_source, client, protocol, requested_model,
                        resolved_alias, upstream_model, provider_reported_model, policy_version,
                        policy_action, status, input_tokens, output_tokens, cache_read_tokens,
                        cache_write_tokens, reasoning_tokens, cost_microusd, cost_basis,
                        allocation_basis, coverage, provider_request_id, redaction_count, redaction_rules
                    ) VALUES (
                        %s, TIMESTAMPTZ '2026-01-01T00:00:00+00:00', %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        "legacy-usage-v2",
                        "hormuz.audit-event",
                        2,
                        "acme",
                        "alice",
                        "Alice",
                        "engineering",
                        "Engineering",
                        "human",
                        "static",
                        "codex",
                        "openai",
                        "gpt-v2",
                        "gpt-v2",
                        "gpt-upstream",
                        "gpt-upstream",
                        "policy-v2",
                        "allowed",
                        "succeeded",
                        10,
                        2,
                        0,
                        0,
                        0,
                        120,
                        "configured_rate_card_estimate",
                        "direct_gateway_request",
                        "gateway_captured_requests_only",
                        None,
                        0,
                        "[]",
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO gateway_secret_events (
                        id, occurred_at, evidence_schema_id, evidence_schema_version,
                        organization_id, actor_id, actor_name, team_id, team_name,
                        identity_type, authentication_source, client, protocol, requested_model,
                        policy_version, coverage, action, detection_count, rules
                    ) VALUES (
                        %s, TIMESTAMPTZ '2026-01-01T00:00:01+00:00', %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        "legacy-secret-v2",
                        "hormuz.audit-event",
                        2,
                        "acme",
                        "alice",
                        "Alice",
                        "engineering",
                        "Engineering",
                        "human",
                        "static",
                        "codex",
                        "openai",
                        "gpt-v2",
                        "policy-v2",
                        "gateway_captured_requests_only",
                        "redacted",
                        1,
                        '["openai_api_key"]',
                    ),
                )

    def _drop_schema(self, schema: str) -> None:
        with self.psycopg.connect(self.owner_dsn, autocommit=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(self.sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(self.sql.Identifier(schema)))

    def _schema_v4_snapshot(self, schema: str) -> dict[str, list[tuple[object, ...]]]:
        with self.psycopg.connect(self.owner_dsn) as connection:
            with connection.cursor() as cursor:
                return {
                    table: cursor.execute(
                        self.sql.SQL("SELECT * FROM {}.{} ORDER BY 1").format(
                            self.sql.Identifier(schema),
                            self.sql.Identifier(table),
                        )
                    ).fetchall()
                    for table in (
                        "hormuz_schema_migrations",
                        "gateway_usage_events",
                        "gateway_secret_events",
                        "gateway_budget_reservations",
                        "gateway_request_attempts",
                        "gateway_request_attempt_events",
                        "gateway_audit_chain_epochs",
                        "gateway_audit_chain_heads",
                        "gateway_audit_chain_entries",
                        "gateway_audit_chain_checkpoints",
                    )
                }

    def _managed_config(
        self,
        *,
        include_bob: bool = False,
        bootstrap_bob: bool = False,
        include_oidc: bool = False,
        break_glass: bool = False,
    ) -> tuple[GatewayConfig, dict[str, str], str | None]:
        """Return an isolated managed-policy config and its process environment."""

        value = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        value.pop("policies")
        value["usage_storage"] = {
            "backend": "postgresql",
            "postgres_dsn_env": "TEST_POSTGRES_RUNTIME_DSN",
            "postgres_migration_dsn_env": "TEST_POSTGRES_MIGRATION_DSN",
            "postgres_schema": self.schema,
            "postgres_runtime_role": self.runtime_role,
        }
        if include_bob:
            value["identities"].append(  # type: ignore[index]
                {
                    "token_env": "HORMUZ_BOB_TOKEN",
                    "actor_id": "bob",
                    "actor_name": "Bob Example",
                    "team_id": "engineering",
                    "team_name": "Engineering",
                    "organization_id": "xpounder",
                    "identity_type": "human",
                    "clearance": "confidential",
                    "allowed_clients": ["codex", "claude-code"],
                }
            )
        issuer: str | None = None
        if include_oidc:
            issuer = "http://127.0.0.1:9444"
            value["authentication"] = {
                "oidc": {
                    "issuers": [
                        {
                            "issuer": issuer,
                            "audiences": ["hormuz-api"],
                            "algorithms": ["RS256"],
                            "allow_insecure_http": True,
                            "subjects": [
                                {
                                    "subject": "runtime-alice",
                                    "actor_id": "oidc-runtime-alice",
                                    "actor_name": "OIDC Runtime Alice",
                                    "team_id": "engineering",
                                    "team_name": "Engineering",
                                    "organization_id": "xpounder",
                                    "clearance": "confidential",
                                    "allowed_clients": ["codex", "claude-code"],
                                }
                            ],
                        }
                    ]
                }
            }
        bootstrap_administrators: list[dict[str, str]] = [
            {"organization_id": "xpounder", "actor_id": "alice"}
        ]
        if bootstrap_bob:
            bootstrap_administrators.append({"organization_id": "xpounder", "actor_id": "bob"})
        policy_control: dict[str, object] = {
            "mode": "postgresql",
            "postgres_control_dsn_env": "TEST_POSTGRES_POLICY_CONTROL_DSN",
            "postgres_control_role": self.policy_control_role,
            "bootstrap_administrators": bootstrap_administrators,
        }
        if break_glass:
            policy_control["break_glass"] = {
                "enabled": True,
                "token_env": "HORMUZ_POLICY_BREAK_GLASS_TOKEN",
            }
        value["policy_control"] = policy_control
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "hormuz-managed.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        environment = {
            "HORMUZ_TOKEN": "policy-test-alice-token",
            "TEST_POSTGRES_RUNTIME_DSN": self.runtime_dsn,
            "TEST_POSTGRES_MIGRATION_DSN": self.owner_dsn,
            "TEST_POSTGRES_POLICY_CONTROL_DSN": self.policy_control_dsn,
            "HORMUZ_POLICY_ADMIN_TOKEN": "policy-test-alice-token",
        }
        if include_bob:
            environment["HORMUZ_BOB_TOKEN"] = "policy-test-bob-token"
            environment["HORMUZ_POLICY_BOB_TOKEN"] = "policy-test-bob-token"
        if break_glass:
            environment["HORMUZ_POLICY_BREAK_GLASS_TOKEN"] = "policy-break-glass-secret-value"
        return GatewayConfig.load(path, environ=environment), environment, issuer

    def _policy_document(self, *, openai_model: str = "gpt-5.4-mini", actor_blocked: bool = False) -> dict[str, object]:
        actors: dict[str, object] = {"alice": {"allowed_models": []}} if actor_blocked else {}
        return {
            "schema_id": "hormuz.policy-document",
            "schema_version": 1,
            "organization_id": "xpounder",
            "policies": {
                "organization": {
                    "allowed_clients": ["codex", "claude-code"],
                    "allowed_models": [openai_model, "claude-sonnet-5"],
                    "max_output_tokens": 32000,
                    "monthly_budget_usd": 10000,
                    "per_actor_monthly_budget_usd": 500,
                },
                "teams": {
                    "engineering": {
                        "allowed_models": [openai_model, "claude-sonnet-5"],
                        "fallback_models": {"openai": openai_model, "anthropic": "claude-sonnet-5"},
                        "max_output_tokens": 16000,
                        "monthly_budget_usd": 5000,
                    }
                },
                "actors": actors,
            },
            "egress_controls": {
                "openai": {"allow_response_storage": False, "allow_background": False},
                "secrets": {"mode": "redact"},
            },
        }

    def _stage(
        self,
        service: PolicyControlService,
        *,
        environment: dict[str, str],
        document: dict[str, object],
        credential_env: str = "HORMUZ_POLICY_ADMIN_TOKEN",
    ):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "policy.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return service.stage(
            organization_id="xpounder",
            credential_env=credential_env,
            policy_path=path,
        )

    def test_migration_is_visible_to_the_restricted_runtime_role(self) -> None:
        status = verify_postgres_schema(
            self.runtime_dsn,
            schema=self.schema,
            runtime_role=self.runtime_role,
        )
        self.assertTrue(status.complete)
        self.assertEqual(status.version, 4)

    def test_policy_control_role_verifies_only_the_shared_migration_ledger(self) -> None:
        with self.assertRaises(PostgresStorageError) as raised:
            verify_postgres_schema(
                self.policy_control_dsn,
                schema=self.schema,
                runtime_role=self.policy_control_role,
            )
        self.assertEqual(raised.exception.code, "storage_schema_partial_upgrade")

        status = verify_postgres_schema(
            self.policy_control_dsn,
            schema=self.schema,
            runtime_role=self.policy_control_role,
            verify_runtime_schema=False,
        )
        self.assertTrue(status.complete)
        self.assertEqual(status.version, 4)

    def test_schema_v2_upgrade_preserves_evidence_and_rejects_an_old_reader(self) -> None:
        schema = self._create_schema_v2_fixture()
        self._insert_v2_evidence(schema)
        with postgres_transaction(
            self.runtime_dsn,
            schema=schema,
            runtime_role=self.runtime_role,
            organization_id="acme",
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO gateway_budget_reservations (
                        id, created_at, expires_at, organization_id, actor_id, team_id,
                        reserved_tokens, reserved_cost_microusd
                    ) VALUES (%s, CURRENT_TIMESTAMP, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        "legacy-reservation-v2",
                        "2999-01-01T00:00:00+00:00",
                        "acme",
                        "alice",
                        "engineering",
                        12,
                        120,
                    ),
                )

        with self.assertRaises(PostgresStorageError) as raised:
            PostgresUsageStore(
                self.runtime_dsn,
                organization_ids=("acme", "beta"),
                schema=schema,
                runtime_role=self.runtime_role,
            )
        self.assertEqual(raised.exception.code, "storage_schema_unavailable")

        status = migrate_postgres(
            self.owner_dsn,
            schema=schema,
            runtime_role=self.runtime_role,
            policy_control_role=self.policy_control_role,
        )
        self.assertEqual(status.version, 4)
        store = PostgresUsageStore(
            self.runtime_dsn,
            organization_ids=("acme", "beta"),
            schema=schema,
            runtime_role=self.runtime_role,
        )
        self.assertEqual(store.active_budget_reservations(organization_id="acme"), 1)
        audit = store.audit_events(since="2000-01-01T00:00:00+00:00", organization_id="acme")
        self.assertEqual(
            [(event["event_type"], event["requested_model"]) for event in audit],
            [("usage", "gpt-v2"), ("security.secret", "gpt-v2")],
        )

        attempt = store.begin_request_attempt(
            identity=_identity("acme"),
            client="codex",
            protocol="openai",
            requested_model="gpt-v3",
            resolved_alias="gpt-v3",
            upstream_model="gpt-upstream",
            policy_version="policy-v3",
            policy_action="allowed",
            redaction_count=0,
            redaction_rules=(),
            scopes=(ReservationScope(name="organization", cost_limit_microusd=1_000),),
            reserved_tokens=20,
            reserved_cost_microusd=200,
            ttl_seconds=60,
        )
        self.assertTrue(
            store.mark_request_attempt_outcome_unknown(
                attempt=attempt,
                organization_id="acme",
                reason_code="provider_transport_ambiguous",
            )
        )
        self.assertEqual(store.active_budget_reservations(organization_id="acme"), 2)

        before = self._schema_v4_snapshot(schema)
        with mock.patch("hormuz.postgres.POSTGRES_SCHEMA_VERSION", 2):
            with self.assertRaises(PostgresStorageError) as raised:
                verify_postgres_schema(
                    self.runtime_dsn,
                    schema=schema,
                    runtime_role=self.runtime_role,
                )
        self.assertEqual(raised.exception.code, "storage_schema_newer_than_binary")
        self.assertEqual(self._schema_v4_snapshot(schema), before)

    def test_partial_v3_upgrade_from_schema_v2_fails_before_materializing_ledger_tables(self) -> None:
        schema = self._create_schema_v2_fixture()
        self._insert_v2_evidence(schema)
        with self.psycopg.connect(self.owner_dsn) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        self.sql.SQL(
                            "INSERT INTO {}.hormuz_schema_migrations (version, state) VALUES (3, 'applying')"
                        ).format(self.sql.Identifier(schema))
                    )
                    cursor.execute(
                        self.sql.SQL("SELECT COUNT(*) FROM {}.gateway_usage_events").format(
                            self.sql.Identifier(schema)
                        )
                    )
                    usage_count = cursor.fetchone()[0]

        with self.assertRaises(PostgresStorageError) as raised:
            migrate_postgres(
                self.owner_dsn,
                schema=schema,
                runtime_role=self.runtime_role,
                policy_control_role=self.policy_control_role,
            )
        self.assertEqual(raised.exception.code, "storage_schema_partial_upgrade")
        with self.assertRaises(PostgresStorageError) as raised:
            PostgresUsageStore(
                self.runtime_dsn,
                organization_ids=("acme", "beta"),
                schema=schema,
                runtime_role=self.runtime_role,
            )
        self.assertEqual(raised.exception.code, "storage_schema_partial_upgrade")

        with self.psycopg.connect(self.owner_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT to_regclass(%s)", (f"{schema}.gateway_request_attempts",))
                self.assertIsNone(cursor.fetchone()[0])
                cursor.execute("SELECT to_regclass(%s)", (f"{schema}.gateway_request_attempt_events",))
                self.assertIsNone(cursor.fetchone()[0])
                cursor.execute(
                    self.sql.SQL("SELECT COUNT(*) FROM {}.gateway_usage_events").format(
                        self.sql.Identifier(schema)
                    )
                )
                self.assertEqual(cursor.fetchone()[0], usage_count)

    def test_incomplete_schema_v2_fails_before_v3_can_advance_the_ledger(self) -> None:
        schema = self._create_schema_v2_fixture()
        with self.psycopg.connect(self.owner_dsn) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        self.sql.SQL("DROP TABLE {}.gateway_usage_events").format(
                            self.sql.Identifier(schema)
                        )
                    )

        with self.assertRaises(PostgresStorageError) as raised:
            migrate_postgres(
                self.owner_dsn,
                schema=schema,
                runtime_role=self.runtime_role,
                policy_control_role=self.policy_control_role,
            )
        self.assertEqual(raised.exception.code, "storage_schema_partial_upgrade")
        with self.assertRaises(PostgresStorageError) as raised:
            PostgresUsageStore(
                self.runtime_dsn,
                organization_ids=("acme", "beta"),
                schema=schema,
                runtime_role=self.runtime_role,
            )
        self.assertEqual(raised.exception.code, "storage_schema_partial_upgrade")

        with self.psycopg.connect(self.owner_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT to_regclass(%s)", (f"{schema}.gateway_request_attempts",))
                self.assertIsNone(cursor.fetchone()[0])
                cursor.execute("SELECT to_regclass(%s)", (f"{schema}.gateway_request_attempt_events",))
                self.assertIsNone(cursor.fetchone()[0])
                cursor.execute(
                    self.sql.SQL(
                        "SELECT version, state FROM {}.hormuz_schema_migrations ORDER BY version"
                    ).format(self.sql.Identifier(schema))
                )
                self.assertEqual(cursor.fetchall(), [(1, "applied"), (2, "applied")])

    def test_noncontiguous_v2_migration_ledger_fails_before_v3_can_advance(self) -> None:
        schema = self._create_schema_v2_fixture()
        with self.psycopg.connect(self.owner_dsn) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        self.sql.SQL("DELETE FROM {}.hormuz_schema_migrations WHERE version = 1").format(
                            self.sql.Identifier(schema)
                        )
                    )

        with self.assertRaises(PostgresStorageError) as raised:
            migrate_postgres(
                self.owner_dsn,
                schema=schema,
                runtime_role=self.runtime_role,
                policy_control_role=self.policy_control_role,
            )
        self.assertEqual(raised.exception.code, "storage_schema_partial_upgrade")
        with self.assertRaises(PostgresStorageError) as raised:
            PostgresUsageStore(
                self.runtime_dsn,
                organization_ids=("acme", "beta"),
                schema=schema,
                runtime_role=self.runtime_role,
            )
        self.assertEqual(raised.exception.code, "storage_schema_partial_upgrade")

        with self.psycopg.connect(self.owner_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT to_regclass(%s)", (f"{schema}.gateway_request_attempts",))
                self.assertIsNone(cursor.fetchone()[0])
                cursor.execute("SELECT to_regclass(%s)", (f"{schema}.gateway_request_attempt_events",))
                self.assertIsNone(cursor.fetchone()[0])
                cursor.execute(
                    self.sql.SQL(
                        "SELECT version, state FROM {}.hormuz_schema_migrations ORDER BY version"
                    ).format(self.sql.Identifier(schema))
                )
                self.assertEqual(cursor.fetchall(), [(2, "applied")])

    def test_configured_router_uses_only_the_runtime_dsn(self) -> None:
        config = GatewayConfig.load(
            ROOT / "config.example.json",
            environ={"HORMUZ_TOKEN": "test-identity-token"},
        )
        config = replace(
            config,
            usage_storage=UsageStorageConfig(
                backend="postgresql",
                postgres_dsn_env="TEST_POSTGRES_RUNTIME_DSN",
                postgres_migration_dsn_env="TEST_POSTGRES_MIGRATION_DSN",
                postgres_schema=self.schema,
                postgres_runtime_role=self.runtime_role,
            ),
        )
        store = create_usage_store(
            config,
            environ={"TEST_POSTGRES_RUNTIME_DSN": self.runtime_dsn},
        )
        self.assertIsInstance(store, PostgresUsageStore)
        self.assertEqual(store.organization_ids, ("xpounder",))

    def test_storage_cli_uses_the_operator_dsn_only_for_migration(self) -> None:
        config_value = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        config_value["usage_storage"] = {
            "backend": "postgresql",
            "postgres_dsn_env": "TEST_POSTGRES_RUNTIME_DSN",
            "postgres_migration_dsn_env": "TEST_POSTGRES_MIGRATION_DSN",
            "postgres_schema": self.schema,
            "postgres_runtime_role": self.runtime_role,
        }
        config_value.pop("policies")
        config_value["policy_control"] = {
            "mode": "postgresql",
            "postgres_control_dsn_env": "TEST_POSTGRES_POLICY_CONTROL_DSN",
            "postgres_control_role": self.policy_control_role,
            "bootstrap_administrators": [
                {"organization_id": "xpounder", "actor_id": "alice"}
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            config_path = Path(temporary) / "gateway.json"
            config_path.write_text(json.dumps(config_value), encoding="utf-8")
            environment = {
                "HORMUZ_TOKEN": "test-identity-token",
                "TEST_POSTGRES_RUNTIME_DSN": self.runtime_dsn,
                "TEST_POSTGRES_MIGRATION_DSN": self.owner_dsn,
                "TEST_POSTGRES_POLICY_CONTROL_DSN": self.policy_control_dsn,
            }
            with mock.patch.dict(os.environ, environment, clear=True):
                migration = io.StringIO()
                with redirect_stdout(migration):
                    self.assertEqual(main(["--config", str(config_path), "storage", "migrate"]), 0)
                self.assertEqual(migration.getvalue(), "PostgreSQL usage storage migration is current: v4\n")

                verification = io.StringIO()
                with redirect_stdout(verification):
                    self.assertEqual(main(["--config", str(config_path), "storage", "verify"]), 0)
                self.assertEqual(verification.getvalue(), "usage storage verified: postgresql\n")

    def test_policy_control_bootstrap_activation_rollback_and_request_pinning(self) -> None:
        config, environment, _issuer = self._managed_config()
        service = PolicyControlService(config, environ=environment)

        administrators = service.bootstrap(
            organization_id="xpounder",
            credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
        )
        self.assertEqual(len(administrators), 1)
        self.assertEqual(administrators[0].actor_id, "alice")

        first = self._stage(service, environment=environment, document=self._policy_document())
        first_activation = service.activate(
            organization_id="xpounder",
            credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
            version_id=first.version_id,
        )
        self.assertEqual(first_activation.generation, 1)

        runtime_one = PolicyRuntime(config, environ=environment)
        runtime_two = PolicyRuntime(config, environ=environment)
        identity = config.identities_by_actor["alice"]
        with tempfile.TemporaryDirectory() as temporary:
            evidence_store = UsageStore(Path(temporary) / "usage.sqlite3")
            engine = PolicyEngine(config, evidence_store, policy_runtime=runtime_one)
            pinned = engine.evaluate(
                identity=identity,
                client="codex",
                protocol="openai",
                requested_model="gpt-5.4-mini",
                requested_output_tokens=100,
            )
            self.assertTrue(pinned.allowed)
            self.assertEqual(pinned.policy_version, first.version_id)

            second = self._stage(
                service,
                environment=environment,
                document=self._policy_document(openai_model="gpt-5.4"),
            )
            second_activation = service.activate(
                organization_id="xpounder",
                credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
                version_id=second.version_id,
            )
            self.assertEqual(second_activation.generation, 2)
            self.assertEqual(runtime_one.snapshot_for(identity).policy_version, second.version_id)
            self.assertEqual(runtime_two.snapshot_for(identity).policy_version, second.version_id)
            # The decision created before activation holds the exact policy
            # version used for that request's accounting and reservation path.
            self.assertEqual(pinned.snapshot.policy_version, first.version_id)
            evidence_store.record(
                identity=identity,
                client="codex",
                protocol="openai",
                requested_model="gpt-5.4-mini",
                resolved_alias="gpt-5.4-mini",
                upstream_model="gpt-5.4-mini",
                policy_version=pinned.policy_version,
                policy_action="allowed",
                status="succeeded",
            )
            self.assertEqual(
                evidence_store.audit_events(
                    since="2000-01-01T00:00:00+00:00",
                    organization_id="xpounder",
                )[0]["policy_version"],
                first.version_id,
            )

        rollback = service.rollback(
            organization_id="xpounder",
            credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
            version_id=first.version_id,
        )
        self.assertEqual(rollback.action, "policy_rolled_back")
        self.assertEqual(rollback.generation, 3)
        status = service.status(organization_id="xpounder", credential_env="HORMUZ_POLICY_ADMIN_TOKEN")
        self.assertTrue(status.initialized)
        self.assertEqual(status.active.version_id if status.active else None, first.version_id)
        self.assertEqual({version.version_id for version in status.versions}, {first.version_id, second.version_id})

        with self.psycopg.connect(self.owner_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self.sql.SQL(
                        "SELECT event_schema_id, event_schema_version, organization_id, occurred_at, event_type, "
                        "actor_kind, actor_identity_key, target_identity_key, version_id, generation, reason_code, "
                        "change_summary "
                        "FROM {}.policy_control_events ORDER BY occurred_at"
                    ).format(self.sql.Identifier(self.schema))
                )
                events = cursor.fetchall()
        self.assertEqual(
            [event[4] for event in events],
            [
                "bootstrap_initialized",
                "policy_staged",
                "policy_activated",
                "policy_staged",
                "policy_activated",
                "policy_rolled_back",
            ],
        )
        self.assertTrue(all(event[0] == "hormuz.policy-control-event" and event[1] == 1 for event in events))
        event_fields = (
            "event_schema_id",
            "event_schema_version",
            "organization_id",
            "occurred_at",
            "event_type",
            "actor_kind",
            "actor_identity_key",
            "target_identity_key",
            "version_id",
            "generation",
            "reason_code",
            "change_summary",
        )
        for event in events:
            event_payload = dict(zip(event_fields, event, strict=True))
            event_payload["occurred_at"] = event_payload["occurred_at"].isoformat()
            validate_policy_control_event(event_payload)
        staged_summaries = [event[11] for event in events if event[4] == "policy_staged"]
        self.assertTrue(all("gpt-5.4" not in summary and "10000" not in summary for summary in staged_summaries))

    def test_policy_cli_uses_the_authenticated_service_boundary(self) -> None:
        config, environment, _issuer = self._managed_config()
        with tempfile.TemporaryDirectory() as temporary:
            policy_path = Path(temporary) / "policy.json"
            policy_path.write_text(json.dumps(self._policy_document()), encoding="utf-8")
            with mock.patch.dict(os.environ, environment, clear=True):
                bootstrap_output = io.StringIO()
                with redirect_stdout(bootstrap_output):
                    self.assertEqual(
                        main(
                            [
                                "--config",
                                str(config.source_path),
                                "policy",
                                "bootstrap",
                                "--organization",
                                "xpounder",
                                "--credential-env",
                                "HORMUZ_POLICY_ADMIN_TOKEN",
                            ]
                        ),
                        0,
                    )
                self.assertIn("policy bootstrap initialized", bootstrap_output.getvalue())
                stage_output = io.StringIO()
                with redirect_stdout(stage_output):
                    self.assertEqual(
                        main(
                            [
                                "--config",
                                str(config.source_path),
                                "policy",
                                "stage",
                                "--organization",
                                "xpounder",
                                "--credential-env",
                                "HORMUZ_POLICY_ADMIN_TOKEN",
                                "--file",
                                str(policy_path),
                            ]
                        ),
                        0,
                    )
                self.assertIn("policy staged: organization=xpounder version=sha256:", stage_output.getvalue())
                version_id = stage_output.getvalue().split("version=", 1)[1].split()[0]
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(
                        main(
                            [
                                "--config",
                                str(config.source_path),
                                "policy",
                                "activate",
                                "--organization",
                                "xpounder",
                                "--credential-env",
                                "HORMUZ_POLICY_ADMIN_TOKEN",
                                "--version",
                                version_id,
                            ]
                        ),
                        0,
                    )
                client_output = io.StringIO()
                with redirect_stdout(client_output):
                    self.assertEqual(
                        main(
                            [
                                "--config",
                                str(config.source_path),
                                "client-config",
                                "codex",
                                "--url",
                                "https://hormuz.example",
                            ]
                        ),
                        0,
                    )
                self.assertIn('model = "gpt-5.4-mini"', client_output.getvalue())
                status_output = io.StringIO()
                with redirect_stdout(status_output):
                    self.assertEqual(
                        main(
                            [
                                "--config",
                                str(config.source_path),
                                "policy",
                                "status",
                                "--organization",
                                "xpounder",
                                "--credential-env",
                                "HORMUZ_POLICY_ADMIN_TOKEN",
                                "--json",
                            ]
                        ),
                        0,
                    )
        status = json.loads(status_output.getvalue())
        validate_contract(status)
        self.assertEqual(status["administrators"][0]["actor_id"], "alice")
        parsed = build_parser().parse_args(
            [
                "policy",
                "stage",
                "--organization",
                "xpounder",
                "--credential-env",
                "HORMUZ_POLICY_ADMIN_TOKEN",
                "--file",
                "policy.json",
            ]
        )
        self.assertFalse(hasattr(parsed, "actor"))

    def test_policy_bootstrap_cannot_drift_and_non_administrator_cannot_change_policy(self) -> None:
        config, environment, _issuer = self._managed_config(include_bob=True)
        service = PolicyControlService(config, environ=environment)
        service.bootstrap(organization_id="xpounder", credential_env="HORMUZ_POLICY_ADMIN_TOKEN")

        # The initialization marker is the first post-bootstrap authority
        # lookup. Even a later configuration that no longer describes the
        # tenant must not be consulted before that database check.
        original_identity = config.identities_by_token[environment["HORMUZ_POLICY_ADMIN_TOKEN"]]
        config_without_tenant = replace(
            config,
            identities_by_token={
                environment["HORMUZ_POLICY_ADMIN_TOKEN"]: replace(original_identity, organization_id="moved-tenant")
            },
        )
        with self.assertRaises(PolicyControlError) as raised:
            PolicyControlService(config_without_tenant, environ=environment).bootstrap(
                organization_id="xpounder",
                credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
            )
        self.assertEqual(raised.exception.code, "policy_bootstrap_already_initialized")

        with self.assertRaises(PolicyControlError) as raised:
            self._stage(
                service,
                environment=environment,
                credential_env="HORMUZ_POLICY_BOB_TOKEN",
                document=self._policy_document(),
            )
        self.assertEqual(raised.exception.code, "policy_administrator_required")

        drifted_config, drifted_environment, _issuer = self._managed_config(
            include_bob=True,
            bootstrap_bob=True,
        )
        drifted_service = PolicyControlService(drifted_config, environ=drifted_environment)
        with self.assertRaises(PolicyControlError) as raised:
            drifted_service.bootstrap(
                organization_id="xpounder",
                credential_env="HORMUZ_POLICY_BOB_TOKEN",
            )
        self.assertEqual(raised.exception.code, "policy_bootstrap_already_initialized")
        with self.assertRaises(PolicyControlError) as raised:
            self._stage(
                drifted_service,
                environment=drifted_environment,
                credential_env="HORMUZ_POLICY_BOB_TOKEN",
                document=self._policy_document(),
            )
        self.assertEqual(raised.exception.code, "policy_administrator_required")

        status = service.status(organization_id="xpounder", credential_env="HORMUZ_POLICY_ADMIN_TOKEN")
        self.assertEqual([(admin.authentication_kind, admin.actor_id) for admin in status.administrators], [("static", "alice")])

    def test_explicit_oidc_administrator_is_separate_from_runtime_entitlement(self) -> None:
        config, environment, issuer = self._managed_config(include_oidc=True)
        assert issuer is not None
        service = PolicyControlService(config, environ=environment)
        service.bootstrap(organization_id="xpounder", credential_env="HORMUZ_POLICY_ADMIN_TOKEN")
        granted = service.grant_oidc_administrator(
            organization_id="xpounder",
            credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
            issuer=issuer,
            subject="unmapped-policy-admin",
        )
        self.assertEqual((granted.organization_id, granted.issuer, granted.subject), ("xpounder", issuer, "unmapped-policy-admin"))

        blocked = self._stage(service, environment=environment, document=self._policy_document(actor_blocked=True))
        service.activate(
            organization_id="xpounder",
            credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
            version_id=blocked.version_id,
        )
        identity = config.identities_by_actor["alice"]
        decision = PolicyEngine(
            config,
            UsageStore(Path(tempfile.mkdtemp()) / "usage.sqlite3"),
            policy_runtime=PolicyRuntime(config, environ=environment),
        ).evaluate(
            identity=identity,
            client="codex",
            protocol="openai",
            requested_model="gpt-5.4-mini",
            requested_output_tokens=100,
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.action, "denied")
        # Alice remains a policy authority even while the active policy denies
        # her inference request; authorization roles do not imply entitlement.
        self._stage(service, environment=environment, document=self._policy_document())

        status = service.status(organization_id="xpounder", credential_env="HORMUZ_POLICY_ADMIN_TOKEN")
        self.assertIn(
            ("oidc", issuer, "unmapped-policy-admin"),
            [(admin.authentication_kind, admin.issuer, admin.subject) for admin in status.administrators],
        )

    def test_policy_roles_are_separated_and_break_glass_requires_admin_loss(self) -> None:
        config, environment, issuer = self._managed_config(include_oidc=True, break_glass=True)
        assert issuer is not None
        service = PolicyControlService(config, environ=environment)
        service.bootstrap(organization_id="xpounder", credential_env="HORMUZ_POLICY_ADMIN_TOKEN")
        version = self._stage(service, environment=environment, document=self._policy_document())
        service.activate(
            organization_id="xpounder",
            credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
            version_id=version.version_id,
        )

        with self.psycopg.connect(self.runtime_dsn) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(self.sql.SQL("SET LOCAL ROLE {}").format(self.sql.Identifier(self.runtime_role)))
                    cursor.execute(
                        self.sql.SQL("SET LOCAL search_path TO {}, pg_catalog").format(self.sql.Identifier(self.schema))
                    )
                    cursor.execute("SELECT set_config('hormuz.organization_id', %s, true)", ("xpounder",))
                    cursor.execute("SELECT COUNT(*) FROM policy_active_versions")
                    self.assertEqual(cursor.fetchone()[0], 1)
                    with self.assertRaises(self.psycopg.Error):
                        cursor.execute("SELECT COUNT(*) FROM policy_administrators")

        with self.psycopg.connect(self.policy_control_dsn) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        self.sql.SQL("SET LOCAL ROLE {}").format(self.sql.Identifier(self.policy_control_role))
                    )
                    cursor.execute(
                        self.sql.SQL("SET LOCAL search_path TO {}, pg_catalog").format(self.sql.Identifier(self.schema))
                    )
                    cursor.execute("SELECT set_config('hormuz.organization_id', %s, true)", ("xpounder",))
                    with self.assertRaises(self.psycopg.Error):
                        cursor.execute("UPDATE policy_versions SET author_kind = 'oidc'")
                    with self.assertRaises(self.psycopg.Error):
                        cursor.execute("UPDATE policy_tenants SET initialized_at = initialized_at")

        with self.assertRaises(PolicyControlError) as raised:
            service.break_glass_recover(
                organization_id="xpounder",
                recovery_secret=environment["HORMUZ_POLICY_BREAK_GLASS_TOKEN"],
                issuer=issuer,
                subject="recovery-administrator",
                reason_code="all_administrators_lost",
            )
        self.assertEqual(raised.exception.code, "policy_break_glass_not_required")

        secondary = service.grant_oidc_administrator(
            organization_id="xpounder",
            credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
            issuer=issuer,
            subject="secondary-administrator",
        )
        repository = PostgresPolicyControlStore(
            self.policy_control_dsn,
            config=config,
            schema=self.schema,
            policy_control_role=self.policy_control_role,
        )
        with self.assertRaises(PolicyControlError) as raised:
            repository.grant_administrator(
                organization_id="xpounder",
                caller=PolicyAdministrator(
                    organization_id="xpounder",
                    authentication_kind="static",
                    actor_id="alice",
                ),
                administrator=PolicyAdministrator(
                    organization_id="xpounder",
                    authentication_kind="static",
                    actor_id="not-a-new-administrator",
                ),
            )
        self.assertEqual(raised.exception.code, "policy_static_administrator_grant_denied")
        service.revoke_static_administrator(
            organization_id="xpounder",
            credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
            actor_id="alice",
        )
        with self.assertRaises(PolicyControlError) as raised:
            repository.revoke_administrator(
                organization_id="xpounder",
                caller=secondary,
                administrator=secondary,
            )
        self.assertEqual(raised.exception.code, "policy_last_administrator_revoke_denied")

        # This owner-only mutation simulates a real loss of every authority.
        # Normal policy-control commands cannot perform this mutation because
        # revoking the final administrator is explicitly rejected.
        with self.psycopg.connect(self.owner_dsn) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        self.sql.SQL(
                            "UPDATE {}.policy_administrators "
                            "SET active = FALSE, revoked_at = CURRENT_TIMESTAMP, "
                            "revoked_by_kind = 'oidc', revoked_by_identity_key = 'owner-recovery-simulation'"
                        ).format(
                            self.sql.Identifier(self.schema)
                        )
                    )
        recovered = service.break_glass_recover(
            organization_id="xpounder",
            recovery_secret=environment["HORMUZ_POLICY_BREAK_GLASS_TOKEN"],
            issuer=issuer,
            subject="recovery-administrator",
            reason_code="all_administrators_lost",
        )
        self.assertEqual((recovered.issuer, recovered.subject), (issuer, "recovery-administrator"))
        recovered_status = repository.status(
            organization_id="xpounder",
            caller=PolicyAdministrator(
                organization_id="xpounder",
                authentication_kind="oidc",
                issuer=issuer,
                subject="recovery-administrator",
            ),
        )
        self.assertEqual(
            [(administrator.issuer, administrator.subject) for administrator in recovered_status.administrators],
            [(issuer, "recovery-administrator")],
        )
        with self.psycopg.connect(self.owner_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self.sql.SQL(
                        "SELECT event_type, actor_kind, reason_code FROM {}.policy_control_events "
                        "WHERE event_type = 'break_glass_recovered'"
                    ).format(self.sql.Identifier(self.schema))
                )
                event = cursor.fetchone()
        self.assertEqual(event, ("break_glass_recovered", "break_glass", "all_administrators_lost"))

    def test_runtime_role_fails_closed_without_an_organization_context(self) -> None:
        self.store.record(
            identity=_identity("acme"),
            client="codex",
            protocol="openai",
            requested_model="gpt-test",
            resolved_alias="gpt-test",
            upstream_model="gpt-upstream",
            policy_action="allowed",
            status="succeeded",
        )
        with self.psycopg.connect(self.runtime_dsn) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(self.sql.SQL("SET LOCAL ROLE {}").format(self.sql.Identifier(self.runtime_role)))
                    cursor.execute(
                        self.sql.SQL("SET LOCAL search_path TO {}, pg_catalog").format(
                            self.sql.Identifier(self.schema)
                        )
                    )
                    cursor.execute(
                        "SELECT COUNT(*) FROM gateway_usage_events"
                    )
                    self.assertEqual(cursor.fetchone()[0], 0)
                    cursor.execute("SELECT set_config('hormuz.organization_id', %s, true)", ("acme",))
                    cursor.execute("SELECT COUNT(*) FROM gateway_usage_events")
                    self.assertEqual(cursor.fetchone()[0], 1)
                    cursor.execute("SELECT set_config('hormuz.organization_id', %s, true)", ("beta",))
                    cursor.execute("SELECT COUNT(*) FROM gateway_usage_events")
                    self.assertEqual(cursor.fetchone()[0], 0)

    def test_newer_or_partial_schema_fails_closed_without_mutating_evidence(self) -> None:
        self.store.record(
            identity=_identity("acme"),
            client="codex",
            protocol="openai",
            requested_model="gpt-test",
            resolved_alias="gpt-test",
            upstream_model="gpt-upstream",
            policy_action="allowed",
            status="succeeded",
        )
        with self.psycopg.connect(self.owner_dsn) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        self.sql.SQL(
                            "SELECT COUNT(*) FROM {}.gateway_usage_events"
                        ).format(self.sql.Identifier(self.schema))
                    )
                    before = cursor.fetchone()[0]
                    cursor.execute(
                        self.sql.SQL(
                            "INSERT INTO {}.hormuz_schema_migrations (version, state) VALUES (5, 'applying')"
                        ).format(self.sql.Identifier(self.schema))
                    )
        with self.assertRaises(PostgresStorageError) as raised:
            PostgresUsageStore(
                self.runtime_dsn,
                organization_ids=("acme", "beta"),
                schema=self.schema,
                runtime_role=self.runtime_role,
            )
        self.assertEqual(raised.exception.code, "storage_schema_partial_upgrade")

        with self.psycopg.connect(self.owner_dsn) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        self.sql.SQL(
                            "UPDATE {}.hormuz_schema_migrations SET state = 'applied' WHERE version = 5"
                        ).format(self.sql.Identifier(self.schema))
                    )
        with self.assertRaises(PostgresStorageError) as raised:
            PostgresUsageStore(
                self.runtime_dsn,
                organization_ids=("acme", "beta"),
                schema=self.schema,
                runtime_role=self.runtime_role,
            )
        self.assertEqual(raised.exception.code, "storage_schema_newer_than_binary")

        with self.psycopg.connect(self.owner_dsn) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        self.sql.SQL(
                            "SELECT COUNT(*) FROM {}.gateway_usage_events"
                        ).format(self.sql.Identifier(self.schema))
                    )
                    self.assertEqual(cursor.fetchone()[0], before)
                    cursor.execute(
                        self.sql.SQL(
                            "DELETE FROM {}.hormuz_schema_migrations WHERE version = 5"
                        ).format(self.sql.Identifier(self.schema))
                    )

    def test_sqlite_and_postgres_have_equivalent_normalized_outcomes(self) -> None:
        identity = _identity("acme")
        with tempfile.TemporaryDirectory() as temporary:
            sqlite_store = UsageStore(Path(temporary) / "usage.sqlite3")
            for store in (sqlite_store, self.store):
                store.record(
                    identity=identity,
                    client="codex",
                    protocol="openai",
                    requested_model="gpt-test",
                    resolved_alias="gpt-test",
                    upstream_model="gpt-upstream",
                    provider_reported_model="gpt-provider",
                    policy_version="policy-v1",
                    policy_action="allowed+redacted",
                    status="succeeded",
                    input_tokens=101,
                    output_tokens=23,
                    cache_read_tokens=11,
                    reasoning_tokens=7,
                    cost_microusd=1234,
                    redaction_count=1,
                    redaction_rules=("openai_api_key",),
                )
                store.record_secret_event(
                    identity=identity,
                    client="codex",
                    protocol="openai",
                    requested_model="gpt-test",
                    policy_version="policy-v1",
                    action="redacted",
                    detection_count=1,
                    rules=("openai_api_key",),
                )
            sqlite_events = sqlite_store.audit_events(
                since="2000-01-01T00:00:00+00:00",
                organization_id="acme",
            )
            postgres_events = self.store.audit_events(
                since="2000-01-01T00:00:00+00:00",
                organization_id="acme",
            )
            self.assertEqual(_normalized_events(sqlite_events), _normalized_events(postgres_events))
            self.assertEqual(
                sqlite_store.report_rows(group_by="organization", organization_id="acme"),
                self.store.report_rows(group_by="organization", organization_id="acme"),
            )

            attempt_scope = (ReservationScope(name="organization", cost_limit_microusd=10_000),)
            for store in (sqlite_store, self.store):
                terminal_attempt = store.begin_request_attempt(
                    identity=identity,
                    client="codex",
                    protocol="openai",
                    requested_model="gpt-terminal",
                    resolved_alias="gpt-terminal",
                    upstream_model="gpt-upstream",
                    policy_version="policy-v3",
                    policy_action="allowed",
                    redaction_count=0,
                    redaction_rules=(),
                    scopes=attempt_scope,
                    reserved_tokens=20,
                    reserved_cost_microusd=100,
                    ttl_seconds=60,
                )
                store.finalize_request_attempt(
                    attempt=terminal_attempt,
                    organization_id="acme",
                    status="succeeded",
                    input_tokens=10,
                    output_tokens=2,
                    cost_microusd=80,
                )
                unknown_attempt = store.begin_request_attempt(
                    identity=identity,
                    client="codex",
                    protocol="openai",
                    requested_model="gpt-unknown",
                    resolved_alias="gpt-unknown",
                    upstream_model="gpt-upstream",
                    policy_version="policy-v3",
                    policy_action="allowed",
                    redaction_count=0,
                    redaction_rules=(),
                    scopes=attempt_scope,
                    reserved_tokens=30,
                    reserved_cost_microusd=200,
                    ttl_seconds=60,
                )
                self.assertTrue(
                    store.mark_request_attempt_outcome_unknown(
                        attempt=unknown_attempt,
                        organization_id="acme",
                        reason_code="provider_transport_ambiguous",
                    )
                )

            sqlite_connection = sqlite3.connect(sqlite_store.path)
            sqlite_attempts = [
                (str(row[0]), int(row[1]), str(row[2]), row[3], bool(row[4]))
                for row in sqlite_connection.execute(
                    """
                    SELECT root.requested_model, event.sequence, event.state,
                           event.reason_code, event.usage_event_id IS NOT NULL
                    FROM gateway_request_attempts AS root
                    JOIN gateway_request_attempt_events AS event ON event.attempt_id = root.attempt_id
                    WHERE root.organization_id = ?
                    ORDER BY root.requested_model, event.sequence
                    """,
                    ("acme",),
                ).fetchall()
            ]
            sqlite_connection.close()
            with postgres_transaction(
                self.runtime_dsn,
                schema=self.schema,
                runtime_role=self.runtime_role,
                organization_id="acme",
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT root.requested_model, event.sequence, event.state,
                               event.reason_code, event.usage_event_id IS NOT NULL AS has_usage_event
                        FROM gateway_request_attempts AS root
                        JOIN gateway_request_attempt_events AS event ON event.attempt_id = root.attempt_id
                        WHERE root.organization_id = %s
                        ORDER BY root.requested_model, event.sequence
                        """,
                        ("acme",),
                    )
                    postgres_attempts = [
                        (
                            str(row["requested_model"]),
                            int(row["sequence"]),
                            str(row["state"]),
                            row["reason_code"],
                            bool(row["has_usage_event"]),
                        )
                        for row in cursor.fetchall()
                    ]
            self.assertEqual(sqlite_attempts, postgres_attempts)
            self.assertEqual(
                sqlite_attempts,
                [
                    ("gpt-terminal", 1, "pending", None, False),
                    ("gpt-terminal", 2, "succeeded", None, True),
                    ("gpt-unknown", 1, "pending", None, False),
                    ("gpt-unknown", 2, "outcome_unknown", "provider_transport_ambiguous", False),
                ],
            )
            self.assertEqual(sqlite_store.active_budget_reservations(organization_id="acme"), 1)
            self.assertEqual(self.store.active_budget_reservations(organization_id="acme"), 1)

            sqlite_connection = sqlite3.connect(sqlite_store.path)
            sqlite_connection.execute("UPDATE gateway_usage_events SET evidence_schema_version = 1")
            sqlite_connection.execute("UPDATE gateway_secret_events SET evidence_schema_version = 1")
            sqlite_connection.commit()
            sqlite_connection.close()
            with self.psycopg.connect(self.owner_dsn) as connection:
                with connection.transaction():
                    with connection.cursor() as cursor:
                        cursor.execute(
                            self.sql.SQL(
                                "UPDATE {}.gateway_usage_events SET evidence_schema_version = 1"
                            ).format(self.sql.Identifier(self.schema))
                        )
                        cursor.execute(
                            self.sql.SQL(
                                "UPDATE {}.gateway_secret_events SET evidence_schema_version = 1"
                            ).format(self.sql.Identifier(self.schema))
                        )
            self.assertEqual(
                _normalized_events(
                    sqlite_store.audit_events(
                        since="2000-01-01T00:00:00+00:00",
                        organization_id="acme",
                    )
                ),
                _normalized_events(
                    self.store.audit_events(
                        since="2000-01-01T00:00:00+00:00",
                        organization_id="acme",
                    )
                ),
            )

    def test_contract_fixtures_and_historical_version_are_materialized(self) -> None:
        fixtures = json.loads((FIXTURES / "valid-v1.json").read_text(encoding="utf-8"))
        identity = _identity("acme")
        event_id = self.store.record(
            identity=identity,
            client="codex",
            protocol="openai",
            requested_model="gpt-test",
            resolved_alias="gpt-test",
            upstream_model="gpt-upstream",
            policy_version="policy-v1",
            policy_action="allowed",
            status="succeeded",
        )
        self.store.record_secret_event(
            identity=identity,
            client="codex",
            protocol="openai",
            requested_model="gpt-test",
            policy_version="policy-v1",
            action="redacted",
            detection_count=1,
            rules=("openai_api_key",),
        )
        current = self.store.audit_events(since="2000-01-01T00:00:00+00:00", organization_id="acme")
        self.assertEqual(set(current[0]), set(fixtures["audit_usage_v2"]))
        self.assertEqual(set(current[1]), set(fixtures["audit_security_v2"]))

        with self.psycopg.connect(self.owner_dsn) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        self.sql.SQL(
                            "UPDATE {}.gateway_usage_events SET evidence_schema_version = 1 WHERE id = %s"
                        ).format(self.sql.Identifier(self.schema)),
                        (event_id,),
                    )
        historical = self.store.audit_events(since="2000-01-01T00:00:00+00:00", organization_id="acme")
        self.assertEqual(set(historical[0]), set(fixtures["audit_usage_v1"]))

    def test_malformed_evidence_fails_closed_without_content(self) -> None:
        event_id = self.store.record(
            identity=_identity("acme"),
            client="codex",
            protocol="openai",
            requested_model="gpt-test",
            resolved_alias="gpt-test",
            upstream_model="gpt-upstream",
            policy_action="allowed",
            status="succeeded",
        )
        with self.psycopg.connect(self.owner_dsn) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        self.sql.SQL(
                            "UPDATE {}.gateway_usage_events SET redaction_rules = %s WHERE id = %s"
                        ).format(self.sql.Identifier(self.schema)),
                        ('["must-not-leak", 1]', event_id),
                    )
        with self.assertRaises(EvidenceStorageError) as raised:
            self.store.audit_events(since="2000-01-01T00:00:00+00:00", organization_id="acme")
        self.assertEqual(raised.exception.code, "stored_evidence_malformed")
        self.assertNotIn("must-not-leak", str(raised.exception))

    def test_tenant_scope_and_budget_reservation_concurrency_match_sqlite_contract(self) -> None:
        acme = _identity("acme")
        beta = _identity("beta")
        for identity in (acme, beta):
            self.store.record(
                identity=identity,
                client="codex",
                protocol="openai",
                requested_model="gpt-test",
                resolved_alias="gpt-test",
                upstream_model="gpt-upstream",
                policy_action="allowed",
                status="succeeded",
                cost_microusd=400,
            )
        self.assertEqual(self.store.monthly_totals(organization_id="acme").requests, 1)
        self.assertEqual(self.store.monthly_totals(organization_id="beta").requests, 1)
        scope = ReservationScope(name="organization", cost_limit_microusd=1_000)
        first = self.store.reserve_budget(
            identity=acme,
            scopes=(scope,),
            reserved_tokens=1,
            reserved_cost_microusd=600,
            ttl_seconds=60,
        )
        second = self.store.reserve_budget(
            identity=beta,
            scopes=(scope,),
            reserved_tokens=1,
            reserved_cost_microusd=600,
            ttl_seconds=60,
        )
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)

        stores = (
            PostgresUsageStore(
                self.runtime_dsn,
                organization_ids=("acme", "beta"),
                schema=self.schema,
                runtime_role=self.runtime_role,
            ),
            PostgresUsageStore(
                self.runtime_dsn,
                organization_ids=("acme", "beta"),
                schema=self.schema,
                runtime_role=self.runtime_role,
            ),
        )
        barrier = threading.Barrier(2)
        outcomes: list[str] = []
        outcome_lock = threading.Lock()

        def reserve(store: PostgresUsageStore) -> None:
            barrier.wait()
            try:
                store.reserve_budget(
                    identity=acme,
                    scopes=(ReservationScope(name="actor", actor_id="alice", cost_limit_microusd=1_800),),
                    reserved_tokens=1,
                    reserved_cost_microusd=600,
                    ttl_seconds=60,
                )
                outcome = "allowed"
            except ReservationDenied:
                outcome = "denied"
            with outcome_lock:
                outcomes.append(outcome)

        threads = [threading.Thread(target=reserve, args=(store,)) for store in stores]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(sorted(outcomes), ["allowed", "denied"])

    def test_commit_time_audit_chain_serializes_multi_instance_writes_and_is_tenant_isolated(self) -> None:
        stores = (
            PostgresUsageStore(
                self.runtime_dsn,
                organization_ids=("acme", "beta"),
                schema=self.schema,
                runtime_role=self.runtime_role,
            ),
            PostgresUsageStore(
                self.runtime_dsn,
                organization_ids=("acme", "beta"),
                schema=self.schema,
                runtime_role=self.runtime_role,
            ),
        )
        barrier = threading.Barrier(3)
        errors: list[BaseException] = []

        def append(store: PostgresUsageStore, actor: str) -> None:
            try:
                barrier.wait(timeout=10)
                store.record(
                    identity=replace(_identity("acme"), actor_id=actor, actor_name=actor.title()),
                    client="codex",
                    protocol="openai",
                    requested_model="gpt-test",
                    resolved_alias="gpt-test",
                    upstream_model="gpt-upstream",
                    policy_action="allowed",
                    status="succeeded",
                )
            except BaseException as error:  # The assertion below reports a real serialization failure.
                errors.append(error)

        threads = [
            threading.Thread(target=append, args=(stores[0], "alice")),
            threading.Thread(target=append, args=(stores[1], "bob")),
        ]
        for thread in threads:
            thread.start()
        barrier.wait(timeout=10)
        for thread in threads:
            thread.join(timeout=15)

        self.assertFalse(errors, errors)
        head = self.store.verify_audit_chain(organization_id="acme")
        self.assertEqual((head.chain_epoch, head.sequence), (1, 2))
        checkpoint = build_audit_chain_checkpoint(head)
        self.store.record_audit_chain_checkpoint(
            checkpoint=checkpoint,
            artifact_sha256="a" * 64,
            anchor_backend="test-object-lock",
            object_version="version-1",
        )
        self.assertEqual(self.store.verify_audit_chain(organization_id="acme", checkpoint=checkpoint), head)

        self.store.record(
            identity=_identity("beta"),
            client="codex",
            protocol="openai",
            requested_model="gpt-test",
            resolved_alias="gpt-test",
            upstream_model="gpt-upstream",
            policy_action="allowed",
            status="succeeded",
        )
        with self.assertRaises(PostgresStorageError) as raised:
            self.store.verify_audit_chain(organization_id="beta", checkpoint=checkpoint)
        self.assertEqual(raised.exception.code, "audit_chain_tenant_mismatch")

    def test_commit_time_audit_chain_rolls_back_and_runtime_cannot_rewrite_history(self) -> None:
        with mock.patch.object(
            self.store,
            "_append_audit_chain_entry_in_cursor",
            side_effect=PostgresStorageError("audit_chain_test_failure"),
        ):
            with self.assertRaises(PostgresStorageError) as raised:
                self.store.record(
                    identity=_identity("acme"),
                    client="codex",
                    protocol="openai",
                    requested_model="gpt-test",
                    resolved_alias="gpt-test",
                    upstream_model="gpt-upstream",
                    policy_action="allowed",
                    status="succeeded",
                )
        self.assertEqual(raised.exception.code, "audit_chain_test_failure")
        with self.psycopg.connect(self.owner_dsn) as connection:
            with connection.cursor() as cursor:
                counts = {}
                for table in ("gateway_usage_events", "gateway_audit_chain_epochs", "gateway_audit_chain_entries"):
                    cursor.execute(
                        self.sql.SQL("SELECT COUNT(*) FROM {}.{}").format(
                            self.sql.Identifier(self.schema),
                            self.sql.Identifier(table),
                        )
                    )
                    counts[table] = cursor.fetchone()[0]
        self.assertEqual(counts, {table: 0 for table in counts})

        self.store.record(
            identity=_identity("acme"),
            client="codex",
            protocol="openai",
            requested_model="gpt-test",
            resolved_alias="gpt-test",
            upstream_model="gpt-upstream",
            policy_action="allowed",
            status="succeeded",
        )
        with postgres_transaction(
            self.runtime_dsn,
            schema=self.schema,
            runtime_role=self.runtime_role,
            organization_id="acme",
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT has_table_privilege(current_user, %s, 'UPDATE') AS can_update, "
                    "has_table_privilege(current_user, %s, 'DELETE') AS can_delete",
                    (
                        f"{self.schema}.gateway_audit_chain_entries",
                        f"{self.schema}.gateway_audit_chain_entries",
                    ),
                )
                privileges = cursor.fetchone()
                self.assertFalse(privileges["can_update"])
                self.assertFalse(privileges["can_delete"])
        self.store.verify_ready()
        with self.assertRaises(PostgresStorageError) as raised:
            with postgres_transaction(
                self.runtime_dsn,
                schema=self.schema,
                runtime_role=self.runtime_role,
                organization_id="acme",
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("DELETE FROM gateway_audit_chain_entries WHERE organization_id = %s", ("acme",))
        self.assertEqual(raised.exception.code, "storage_access_denied")

    def test_attempt_ledger_is_append_only_tenant_scoped_and_conservative(self) -> None:
        identity = _identity("acme")
        scope = ReservationScope(name="organization", cost_limit_microusd=1_000)
        attempt = self.store.begin_request_attempt(
            identity=identity,
            client="codex",
            protocol="openai",
            requested_model="gpt-test",
            resolved_alias="gpt-test",
            upstream_model="gpt-upstream",
            policy_version="policy-v1",
            policy_action="allowed",
            redaction_count=0,
            redaction_rules=(),
            scopes=(scope,),
            reserved_tokens=20,
            reserved_cost_microusd=600,
            ttl_seconds=60,
        )

        with postgres_transaction(
            self.runtime_dsn,
            schema=self.schema,
            runtime_role=self.runtime_role,
            organization_id="acme",
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT attempt_id, reserved_cost_microusd FROM gateway_request_attempts WHERE attempt_id = %s",
                    (attempt.attempt_id,),
                )
                self.assertEqual(dict(cursor.fetchone()), {"attempt_id": attempt.attempt_id, "reserved_cost_microusd": 600})
                cursor.execute(
                    "SELECT sequence, state FROM gateway_request_attempt_events WHERE attempt_id = %s",
                    (attempt.attempt_id,),
                )
                self.assertEqual([dict(row) for row in cursor.fetchall()], [{"sequence": 1, "state": "pending"}])

        with postgres_transaction(
            self.runtime_dsn,
            schema=self.schema,
            runtime_role=self.runtime_role,
            organization_id="beta",
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) AS count FROM gateway_request_attempts")
                self.assertEqual(cursor.fetchone()["count"], 0)

        with self.assertRaises(PostgresStorageError) as raised:
            with postgres_transaction(
                self.runtime_dsn,
                schema=self.schema,
                runtime_role=self.runtime_role,
                organization_id="acme",
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE gateway_request_attempts SET policy_version = 'rewrite-attempt' WHERE attempt_id = %s",
                        (attempt.attempt_id,),
                    )
        self.assertEqual(raised.exception.code, "storage_access_denied")

        beta_usage_event_id = self.store.record(
            identity=_identity("beta"),
            client="codex",
            protocol="openai",
            requested_model="gpt-test",
            resolved_alias="gpt-test",
            upstream_model="gpt-upstream",
            policy_action="allowed",
            status="succeeded",
        )
        with self.assertRaises(PostgresStorageError):
            with postgres_transaction(
                self.runtime_dsn,
                schema=self.schema,
                runtime_role=self.runtime_role,
                organization_id="acme",
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO gateway_request_attempt_events (
                            id, attempt_id, organization_id, occurred_at,
                            event_schema_id, event_schema_version, sequence, state,
                            reason_code, usage_event_id
                        ) VALUES (%s, %s, %s, now(), %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            str(uuid4()),
                            attempt.attempt_id,
                            "acme",
                            "hormuz.request-attempt-event",
                            1,
                            2,
                            "succeeded",
                            None,
                            beta_usage_event_id,
                        ),
                    )

        with self.psycopg.connect(self.owner_dsn) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute("SELECT set_config('hormuz.organization_id', %s, true)", ("acme",))
                    cursor.execute(
                        self.sql.SQL(
                            "UPDATE {}.gateway_budget_reservations SET expires_at = %s WHERE id = %s"
                        ).format(self.sql.Identifier(self.schema)),
                        ("2000-01-01T00:00:00+00:00", attempt.reservation_id),
                    )

        self.assertEqual(self.store.sweep_stale_request_attempts(organization_id="acme"), 1)
        self.assertEqual(self.store.active_budget_reservations(organization_id="acme"), 1)
        with self.assertRaises(ReservationDenied):
            self.store.reserve_budget(
                identity=identity,
                scopes=(scope,),
                reserved_tokens=1,
                reserved_cost_microusd=500,
                ttl_seconds=60,
            )
        with self.assertRaises(RequestAttemptStateError):
            self.store.finalize_request_attempt(
                attempt=attempt,
                organization_id="acme",
                status="failed",
            )

        with postgres_transaction(
            self.runtime_dsn,
            schema=self.schema,
            runtime_role=self.runtime_role,
            organization_id="acme",
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT sequence, state, reason_code, usage_event_id "
                    "FROM gateway_request_attempt_events WHERE attempt_id = %s ORDER BY sequence",
                    (attempt.attempt_id,),
                )
                self.assertEqual(
                    [dict(row) for row in cursor.fetchall()],
                    [
                        {"sequence": 1, "state": "pending", "reason_code": None, "usage_event_id": None},
                        {
                            "sequence": 2,
                            "state": "outcome_unknown",
                            "reason_code": "stale_pending",
                            "usage_event_id": None,
                        },
                    ],
                )

    def test_two_gateway_instances_share_atomic_organization_budget_reservations(self) -> None:
        """A durable reservation made through one gateway constrains the other.

        The lower-level store test above proves the advisory-lock algorithm.
        This test deliberately exercises the full request path through two
        independently constructed gateway servers and their separate runtime
        pools: authentication, policy evaluation, reservation, provider
        admission, evidence accounting, and reservation release.
        """

        _BlockingReplicaBudgetProviderHandler.reset()
        provider = ThreadingHTTPServer(("127.0.0.1", 0), _BlockingReplicaBudgetProviderHandler)
        provider_thread = threading.Thread(target=provider.serve_forever, daemon=True)
        provider_thread.start()

        gateways: list[GatewayServer] = []
        gateway_threads: list[threading.Thread] = []
        first_request_thread: threading.Thread | None = None
        try:
            request_body = {"model": "gpt-5.4-mini", "input": "replica budget probe"}
            max_output_tokens = 1
            reserved_body = {**request_body, "store": False, "max_output_tokens": max_output_tokens}
            reserved_cost_microusd = len(
                json.dumps(reserved_body, separators=(",", ":")).encode("utf-8")
            ) * 1_000_000
            organization_budget_usd = reserved_cost_microusd * 1.5 / 1_000_000
            self.assertGreater(reserved_cost_microusd, 0)
            self.assertLessEqual(reserved_cost_microusd, round(organization_budget_usd * 1_000_000))
            self.assertGreater(reserved_cost_microusd * 2, round(organization_budget_usd * 1_000_000))

            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                config_value = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
                config_value["usage_storage"] = {
                    "backend": "postgresql",
                    "postgres_dsn_env": "TEST_POSTGRES_RUNTIME_DSN",
                    "postgres_migration_dsn_env": "TEST_POSTGRES_MIGRATION_DSN",
                    "postgres_schema": self.schema,
                    "postgres_runtime_role": self.runtime_role,
                }
                config_value["upstreams"]["openai"] = {
                    "base_url": f"http://127.0.0.1:{provider.server_port}/v1",
                    "api_key_env": "TEST_REPLICA_OPENAI_KEY",
                    "allow_response_storage": False,
                    "allow_background": False,
                }
                config_value["upstreams"]["anthropic"][
                    "api_key_env"
                ] = "TEST_REPLICA_ANTHROPIC_KEY"
                config_value["model_routes"]["gpt-5.4-mini"] = {
                    "protocol": "openai",
                    "upstream_model": "gpt-5.4-mini",
                    "input_cost_per_million": 1_000_000,
                    "cache_read_cost_per_million": 0,
                    "cache_write_cost_per_million": 0,
                    "output_cost_per_million": 0,
                }
                config_value["policies"]["organization"]["max_output_tokens"] = max_output_tokens
                config_value["policies"]["teams"]["engineering"]["max_output_tokens"] = max_output_tokens
                config_value["policies"]["organization"]["monthly_budget_usd"] = organization_budget_usd

                environment = {
                    "HORMUZ_TOKEN": "replica-budget-employee-token",
                    "TEST_POSTGRES_RUNTIME_DSN": self.runtime_dsn,
                    "TEST_POSTGRES_MIGRATION_DSN": self.owner_dsn,
                    "TEST_REPLICA_OPENAI_KEY": "replica-budget-provider-key",
                    "TEST_REPLICA_ANTHROPIC_KEY": "replica-budget-anthropic-key",
                }
                configs: list[GatewayConfig] = []
                for index in range(2):
                    config_value["listen"]["port"] = _free_port()
                    config_path = root / f"gateway-{index}.json"
                    config_path.write_text(json.dumps(config_value), encoding="utf-8")
                    configs.append(GatewayConfig.load(config_path, environ=environment))
                with mock.patch.dict(os.environ, environment, clear=False):
                    for config in configs:
                        gateways.append(GatewayServer(config))

            for gateway in gateways:
                gateway_threads.append(serve_in_thread(gateway))

            def send_request(gateway: GatewayServer) -> tuple[int, bytes]:
                connection = http.client.HTTPConnection("127.0.0.1", gateway.server_port, timeout=5)
                try:
                    connection.request(
                        "POST",
                        "/v1/responses",
                        body=json.dumps(request_body),
                        headers={
                            "Authorization": "Bearer replica-budget-employee-token",
                            "Content-Type": "application/json",
                        },
                    )
                    response = connection.getresponse()
                    return response.status, response.read()
                finally:
                    connection.close()

            first_outcome: list[tuple[int, bytes] | BaseException] = []

            def send_first_request() -> None:
                try:
                    first_outcome.append(send_request(gateways[0]))
                except BaseException as error:  # pragma: no cover - reported by the assertion below
                    first_outcome.append(error)

            first_request_thread = threading.Thread(target=send_first_request, daemon=True)
            first_request_thread.start()
            self.assertTrue(_BlockingReplicaBudgetProviderHandler.first_request_started.wait(timeout=5))

            denied_status, denied_body = send_request(gateways[1])
            self.assertEqual(denied_status, 403, denied_body)
            self.assertEqual(json.loads(denied_body)["error"]["code"], "hormuz_budget_denied")
            self.assertEqual(_BlockingReplicaBudgetProviderHandler.request_count, 1)

            _BlockingReplicaBudgetProviderHandler.release_first_response.set()
            first_request_thread.join(timeout=10)
            self.assertFalse(first_request_thread.is_alive())
            self.assertEqual(len(first_outcome), 1)
            self.assertNotIsInstance(first_outcome[0], BaseException)
            first_status, first_body = first_outcome[0]
            self.assertEqual(first_status, 200, first_body)
            self.assertEqual(_BlockingReplicaBudgetProviderHandler.request_count, 1)

            totals = gateways[0].store.monthly_totals(organization_id="xpounder")
            self.assertEqual((totals.requests, totals.denied_requests), (2, 1))
            self.assertEqual((totals.input_tokens, totals.output_tokens), (1, 0))
            self.assertEqual(totals.cost_microusd, 1_000_000)
            self.assertEqual(gateways[1].store.active_budget_reservations(organization_id="xpounder"), 0)
            events = gateways[0].store.audit_events(
                since="2000-01-01T00:00:00+00:00",
                organization_id="xpounder",
            )
            self.assertEqual(
                {(event["status"], event["policy_action"]) for event in events},
                {("succeeded", "allowed"), ("denied", "budget_reservation_denied")},
            )

            shared_store = PostgresUsageStore(
                self.runtime_dsn,
                organization_ids=("xpounder", "beta"),
                schema=self.schema,
                runtime_role=self.runtime_role,
            )
            self.assertEqual(shared_store.monthly_totals(organization_id="beta").requests, 0)
        finally:
            _BlockingReplicaBudgetProviderHandler.release_first_response.set()
            if first_request_thread is not None:
                first_request_thread.join(timeout=10)
            for gateway in gateways[: len(gateway_threads)]:
                gateway.shutdown()
            for gateway in gateways:
                gateway.server_close()
            for thread in gateway_threads:
                thread.join(timeout=10)
            provider.shutdown()
            provider.server_close()
            provider_thread.join(timeout=10)

    def test_two_gateway_instances_converge_on_policy_activation_and_rollback(self) -> None:
        """A committed policy pointer governs both replicas before provider egress."""

        _ReplicaPolicyProviderHandler.reset()
        provider = ThreadingHTTPServer(("127.0.0.1", 0), _ReplicaPolicyProviderHandler)
        provider_thread = threading.Thread(target=provider.serve_forever, daemon=True)
        provider_thread.start()

        gateways: list[GatewayServer] = []
        gateway_threads: list[threading.Thread] = []
        try:
            config, environment, _issuer = self._managed_config()
            service = PolicyControlService(config, environ=environment)
            service.bootstrap(organization_id="xpounder", credential_env="HORMUZ_POLICY_ADMIN_TOKEN")
            permissive = self._stage(
                service,
                environment=environment,
                document=self._policy_document(),
            )
            initial_activation = service.activate(
                organization_id="xpounder",
                credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
                version_id=permissive.version_id,
            )
            self.assertEqual(initial_activation.generation, 1)

            upstreams = dict(config.upstreams)
            upstreams["openai"] = replace(
                upstreams["openai"],
                base_url=f"http://127.0.0.1:{provider.server_port}/v1",
                api_key_env="TEST_REPLICA_POLICY_OPENAI_KEY",
            )
            upstreams["anthropic"] = replace(
                upstreams["anthropic"],
                api_key_env="TEST_REPLICA_POLICY_ANTHROPIC_KEY",
            )
            environment.update(
                {
                    "TEST_REPLICA_POLICY_OPENAI_KEY": "replica-policy-provider-key",
                    "TEST_REPLICA_POLICY_ANTHROPIC_KEY": "replica-policy-anthropic-key",
                }
            )
            configs = tuple(
                replace(
                    config,
                    listen=replace(config.listen, port=_free_port()),
                    upstreams=dict(upstreams),
                )
                for _ in range(2)
            )
            with mock.patch.dict(os.environ, environment, clear=False):
                gateways = [GatewayServer(replica_config) for replica_config in configs]

            self.assertIsNotNone(gateways[0].postgres_pool)
            self.assertIsNotNone(gateways[1].postgres_pool)
            self.assertIsNot(gateways[0].postgres_pool, gateways[1].postgres_pool)
            gateway_threads = [serve_in_thread(gateway) for gateway in gateways]

            def send_request(gateway: GatewayServer) -> tuple[int, bytes]:
                connection = http.client.HTTPConnection("127.0.0.1", gateway.server_port, timeout=5)
                try:
                    connection.request(
                        "POST",
                        "/v1/responses",
                        body=json.dumps({"model": "gpt-5.4-mini", "input": "replica policy probe"}),
                        headers={
                            "Authorization": "Bearer policy-test-alice-token",
                            "Content-Type": "application/json",
                        },
                    )
                    response = connection.getresponse()
                    return response.status, response.read()
                finally:
                    connection.close()

            initial_status, initial_body = send_request(gateways[0])
            self.assertEqual(initial_status, 200, initial_body)
            self.assertEqual(_ReplicaPolicyProviderHandler.request_count, 1)

            stricter = self._stage(
                service,
                environment=environment,
                document=self._policy_document(actor_blocked=True),
            )
            strict_activation = service.activate(
                organization_id="xpounder",
                credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
                version_id=stricter.version_id,
            )
            self.assertEqual(strict_activation.generation, 2)

            denied_status, denied_body = send_request(gateways[1])
            self.assertEqual(denied_status, 403, denied_body)
            self.assertEqual(json.loads(denied_body)["error"]["code"], "hormuz_policy_denied")
            self.assertEqual(_ReplicaPolicyProviderHandler.request_count, 1)

            rollback = service.rollback(
                organization_id="xpounder",
                credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
                version_id=permissive.version_id,
            )
            self.assertEqual(rollback.action, "policy_rolled_back")
            self.assertEqual(rollback.generation, 3)

            restored_status, restored_body = send_request(gateways[1])
            self.assertEqual(restored_status, 200, restored_body)
            self.assertEqual(_ReplicaPolicyProviderHandler.request_count, 2)

            totals = gateways[0].store.monthly_totals(organization_id="xpounder")
            self.assertEqual((totals.requests, totals.denied_requests), (3, 1))
            events = gateways[0].store.audit_events(
                since="2000-01-01T00:00:00+00:00",
                organization_id="xpounder",
            )
            self.assertEqual(len(events), 3)
            self.assertEqual(
                {(event["status"], event["policy_action"], event["policy_version"]) for event in events},
                {
                    ("succeeded", "allowed", permissive.version_id),
                    ("denied", "denied", stricter.version_id),
                },
            )

            status = service.status(organization_id="xpounder", credential_env="HORMUZ_POLICY_ADMIN_TOKEN")
            self.assertEqual(status.active.version_id if status.active else None, permissive.version_id)
            self.assertEqual(status.active.generation if status.active else None, 3)

            shared_store = PostgresUsageStore(
                self.runtime_dsn,
                organization_ids=("xpounder", "beta"),
                schema=self.schema,
                runtime_role=self.runtime_role,
            )
            self.assertEqual(shared_store.monthly_totals(organization_id="beta").requests, 0)
        finally:
            for gateway in gateways[: len(gateway_threads)]:
                gateway.shutdown()
            for gateway in gateways:
                gateway.server_close()
            for thread in gateway_threads:
                thread.join(timeout=10)
            provider.shutdown()
            provider.server_close()
            provider_thread.join(timeout=10)

    def test_failed_replica_fails_closed_while_sibling_and_replacement_remain_usable(self) -> None:
        """A local pool loss is isolated and a fresh gateway instance recovers.

        This is deliberately a deterministic process-level runtime-pool failure
        proof. It does not claim database failover or high availability.
        """

        _ReplicaPolicyProviderHandler.reset()
        provider = ThreadingHTTPServer(("127.0.0.1", 0), _ReplicaPolicyProviderHandler)
        provider_thread = threading.Thread(target=provider.serve_forever, daemon=True)
        provider_thread.start()

        active_gateways: list[GatewayServer] = []
        gateway_threads: list[threading.Thread] = []
        failed_gateway: GatewayServer | None = None
        failed_gateway_thread: threading.Thread | None = None
        try:
            config, environment, _issuer = self._managed_config()
            service = PolicyControlService(config, environ=environment)
            service.bootstrap(organization_id="xpounder", credential_env="HORMUZ_POLICY_ADMIN_TOKEN")
            active_policy = self._stage(
                service,
                environment=environment,
                document=self._policy_document(),
            )
            service.activate(
                organization_id="xpounder",
                credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
                version_id=active_policy.version_id,
            )

            upstreams = dict(config.upstreams)
            upstreams["openai"] = replace(
                upstreams["openai"],
                base_url=f"http://127.0.0.1:{provider.server_port}/v1",
                api_key_env="TEST_REPLICA_RECOVERY_OPENAI_KEY",
            )
            upstreams["anthropic"] = replace(
                upstreams["anthropic"],
                api_key_env="TEST_REPLICA_RECOVERY_ANTHROPIC_KEY",
            )
            environment.update(
                {
                    "TEST_REPLICA_RECOVERY_OPENAI_KEY": "replica-recovery-provider-key",
                    "TEST_REPLICA_RECOVERY_ANTHROPIC_KEY": "replica-recovery-anthropic-key",
                }
            )
            configs = tuple(
                replace(
                    config,
                    listen=replace(config.listen, port=_free_port()),
                    upstreams=dict(upstreams),
                )
                for _ in range(2)
            )
            with mock.patch.dict(os.environ, environment, clear=False):
                active_gateways = [GatewayServer(replica_config) for replica_config in configs]

            self.assertIsNotNone(active_gateways[0].postgres_pool)
            self.assertIsNotNone(active_gateways[1].postgres_pool)
            self.assertIsNot(active_gateways[0].postgres_pool, active_gateways[1].postgres_pool)
            gateway_threads = [serve_in_thread(gateway) for gateway in active_gateways]

            def send_get(gateway: GatewayServer, path: str) -> tuple[int, bytes]:
                connection = http.client.HTTPConnection("127.0.0.1", gateway.server_port, timeout=5)
                try:
                    connection.request("GET", path)
                    response = connection.getresponse()
                    return response.status, response.read()
                finally:
                    connection.close()

            def send_request(gateway: GatewayServer, *, input_value: str) -> tuple[int, bytes]:
                connection = http.client.HTTPConnection("127.0.0.1", gateway.server_port, timeout=5)
                try:
                    connection.request(
                        "POST",
                        "/v1/responses",
                        body=json.dumps({"model": "gpt-5.4-mini", "input": input_value}),
                        headers={
                            "Authorization": "Bearer policy-test-alice-token",
                            "Content-Type": "application/json",
                        },
                    )
                    response = connection.getresponse()
                    return response.status, response.read()
                finally:
                    connection.close()

            failed_gateway = active_gateways.pop(0)
            failed_gateway_thread = gateway_threads.pop(0)
            self.assertIsNotNone(failed_gateway.postgres_pool)
            failed_gateway.postgres_pool.close()

            failed_ready_status, failed_ready_body = send_get(failed_gateway, "/ready")
            self.assertEqual(failed_ready_status, 503, failed_ready_body)
            failed_readiness = json.loads(failed_ready_body)
            validate_contract(failed_readiness)
            self.assertEqual(failed_readiness["reason"], "dependency_unavailable")

            secret_input = "replica-local-secret-must-not-leak"
            failed_status, failed_body = send_request(failed_gateway, input_value=secret_input)
            self.assertEqual(failed_status, 503, failed_body)
            failed_response = json.loads(failed_body)
            self.assertEqual(failed_response["error"]["code"], "hormuz_storage_unavailable")
            self.assertNotIn(secret_input, repr(failed_response))
            self.assertEqual(_ReplicaPolicyProviderHandler.request_count, 0)

            healthy_gateway = active_gateways[0]
            healthy_ready_status, healthy_ready_body = send_get(healthy_gateway, "/ready")
            self.assertEqual(healthy_ready_status, 200, healthy_ready_body)
            healthy_status, healthy_body = send_request(healthy_gateway, input_value="healthy sibling probe")
            self.assertEqual(healthy_status, 200, healthy_body)
            self.assertEqual(_ReplicaPolicyProviderHandler.request_count, 1)

            failed_gateway.shutdown()
            failed_gateway.server_close()
            failed_gateway_thread.join(timeout=10)
            self.assertFalse(failed_gateway_thread.is_alive())
            failed_gateway = None
            failed_gateway_thread = None

            replacement_config = replace(
                config,
                listen=replace(config.listen, port=_free_port()),
                upstreams=dict(upstreams),
            )
            with mock.patch.dict(os.environ, environment, clear=False):
                replacement = GatewayServer(replacement_config)
            self.assertIsNotNone(replacement.postgres_pool)
            self.assertIsNot(replacement.postgres_pool, healthy_gateway.postgres_pool)
            active_gateways.append(replacement)
            gateway_threads.append(serve_in_thread(replacement))

            replacement_ready_status, replacement_ready_body = send_get(replacement, "/ready")
            self.assertEqual(replacement_ready_status, 200, replacement_ready_body)
            replacement_status, replacement_body = send_request(
                replacement,
                input_value="replacement recovery probe",
            )
            self.assertEqual(replacement_status, 200, replacement_body)
            self.assertEqual(_ReplicaPolicyProviderHandler.request_count, 2)

            totals = healthy_gateway.store.monthly_totals(organization_id="xpounder")
            self.assertEqual((totals.requests, totals.denied_requests), (2, 0))
            events = healthy_gateway.store.audit_events(
                since="2000-01-01T00:00:00+00:00",
                organization_id="xpounder",
            )
            self.assertEqual(len(events), 2)
            self.assertEqual({event["status"] for event in events}, {"succeeded"})

            shared_store = PostgresUsageStore(
                self.runtime_dsn,
                organization_ids=("xpounder", "beta"),
                schema=self.schema,
                runtime_role=self.runtime_role,
            )
            self.assertEqual(shared_store.monthly_totals(organization_id="beta").requests, 0)
        finally:
            if failed_gateway is not None:
                failed_gateway.shutdown()
                failed_gateway.server_close()
            if failed_gateway_thread is not None:
                failed_gateway_thread.join(timeout=10)
            for gateway in active_gateways[: len(gateway_threads)]:
                gateway.shutdown()
            for gateway in active_gateways:
                gateway.server_close()
            for thread in gateway_threads:
                thread.join(timeout=10)
            provider.shutdown()
            provider.server_close()
            provider_thread.join(timeout=10)

    def test_rolling_runtime_login_rotation_keeps_ready_replacement_and_tenant_isolation(self) -> None:
        """A new NOINHERIT login can replace an old runtime login safely.

        The stable restricted ``runtime_role`` remains the authorization role.
        This exercises the real rolling process boundary: start a ready
        replacement using a distinct login member, drain the old process, then
        revoke only the old login. Hormuz deliberately does not hot-reload a
        DSN in a live process.
        """

        _ReplicaPolicyProviderHandler.reset()
        provider = ThreadingHTTPServer(("127.0.0.1", 0), _ReplicaPolicyProviderHandler)
        provider_thread = threading.Thread(target=provider.serve_forever, daemon=True)
        provider_thread.start()

        suffix = uuid4().hex[:8]
        old_login = f"hormuz_runtime_old_{suffix}"
        replacement_login = f"hormuz_runtime_new_{suffix}"
        old_dsn = _runtime_dsn(self.owner_dsn, old_login, "hormuz-old-runtime-password")
        replacement_dsn = _runtime_dsn(
            self.owner_dsn,
            replacement_login,
            "hormuz-new-runtime-password",
        )
        gateways: list[GatewayServer] = []
        gateway_threads: list[threading.Thread] = []
        try:
            with self.psycopg.connect(self.owner_dsn, autocommit=True) as connection:
                with connection.cursor() as cursor:
                    for login, password in (
                        (old_login, "hormuz-old-runtime-password"),
                        (replacement_login, "hormuz-new-runtime-password"),
                    ):
                        cursor.execute(
                            self.sql.SQL(
                                "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER "
                                "NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS"
                            ).format(self.sql.Identifier(login), self.sql.Literal(password))
                        )
                        cursor.execute(
                            self.sql.SQL("GRANT {} TO {}").format(
                                self.sql.Identifier(self.runtime_role),
                                self.sql.Identifier(login),
                            )
                        )

            config, environment, _issuer = self._managed_config()
            service = PolicyControlService(config, environ=environment)
            service.bootstrap(organization_id="xpounder", credential_env="HORMUZ_POLICY_ADMIN_TOKEN")
            active_policy = self._stage(
                service,
                environment=environment,
                document=self._policy_document(),
            )
            service.activate(
                organization_id="xpounder",
                credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
                version_id=active_policy.version_id,
            )

            upstreams = dict(config.upstreams)
            upstreams["openai"] = replace(
                upstreams["openai"],
                base_url=f"http://127.0.0.1:{provider.server_port}/v1",
                api_key_env="TEST_RUNTIME_ROTATION_OPENAI_KEY",
            )
            upstreams["anthropic"] = replace(
                upstreams["anthropic"],
                api_key_env="TEST_RUNTIME_ROTATION_ANTHROPIC_KEY",
            )
            old_config = replace(
                config,
                listen=replace(config.listen, port=_free_port()),
                upstreams=upstreams,
            )
            replacement_config = replace(
                config,
                listen=replace(config.listen, port=_free_port()),
                upstreams=upstreams,
            )
            common_environment = {
                **environment,
                "TEST_RUNTIME_ROTATION_OPENAI_KEY": "runtime-rotation-openai-key",
                "TEST_RUNTIME_ROTATION_ANTHROPIC_KEY": "runtime-rotation-anthropic-key",
            }
            old_environment = {
                **common_environment,
                "TEST_POSTGRES_RUNTIME_DSN": old_dsn,
            }
            replacement_environment = {
                **common_environment,
                "TEST_POSTGRES_RUNTIME_DSN": replacement_dsn,
            }

            with mock.patch.dict(os.environ, old_environment, clear=False):
                old_gateway = GatewayServer(old_config)
            gateways.append(old_gateway)
            old_thread = serve_in_thread(old_gateway)
            gateway_threads.append(old_thread)

            def send_get(gateway: GatewayServer, path: str) -> tuple[int, bytes]:
                connection = http.client.HTTPConnection("127.0.0.1", gateway.server_port, timeout=5)
                try:
                    connection.request("GET", path)
                    response = connection.getresponse()
                    return response.status, response.read()
                finally:
                    connection.close()

            def send_request(gateway: GatewayServer, *, input_value: str) -> tuple[int, bytes]:
                connection = http.client.HTTPConnection("127.0.0.1", gateway.server_port, timeout=5)
                try:
                    connection.request(
                        "POST",
                        "/v1/responses",
                        body=json.dumps({"model": "gpt-5.4-mini", "input": input_value}),
                        headers={
                            "Authorization": "Bearer policy-test-alice-token",
                            "Content-Type": "application/json",
                        },
                    )
                    response = connection.getresponse()
                    return response.status, response.read()
                finally:
                    connection.close()

            old_ready_status, old_ready_body = send_get(old_gateway, "/ready")
            self.assertEqual(old_ready_status, 200, old_ready_body)
            old_status, old_body = send_request(old_gateway, input_value="old runtime login probe")
            self.assertEqual(old_status, 200, old_body)

            # The replacement is built from a separately injected runtime
            # credential. It must become ready before the old login is revoked
            # or customer traffic is moved.
            with mock.patch.dict(os.environ, replacement_environment, clear=False):
                replacement_gateway = GatewayServer(replacement_config)
            gateways.append(replacement_gateway)
            replacement_thread = serve_in_thread(replacement_gateway)
            gateway_threads.append(replacement_thread)

            replacement_ready_status, replacement_ready_body = send_get(replacement_gateway, "/ready")
            self.assertEqual(replacement_ready_status, 200, replacement_ready_body)
            replacement_status, replacement_body = send_request(
                replacement_gateway,
                input_value="replacement runtime login probe",
            )
            self.assertEqual(replacement_status, 200, replacement_body)

            # Draining owns existing work and closes the old pool before the
            # operator disables the superseded login.
            old_gateway.shutdown()
            old_gateway.server_close()
            old_thread.join(timeout=10)
            self.assertFalse(old_thread.is_alive())
            self.assertIsNotNone(old_gateway.postgres_pool)
            self.assertTrue(old_gateway.postgres_pool.closed)

            with self.psycopg.connect(self.owner_dsn, autocommit=True) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(self.sql.SQL("ALTER ROLE {} NOLOGIN").format(self.sql.Identifier(old_login)))

            with self.assertRaises(PostgresStorageError):
                PostgresConnectionPool(
                    old_dsn,
                    settings=PostgresPoolConfig(
                        min_connections=1,
                        max_connections=1,
                        acquire_timeout_seconds=1,
                        max_waiting=1,
                        max_lifetime_seconds=1800,
                        max_idle_seconds=120,
                    ),
                )

            # The replacement keeps serving through the same stable runtime
            # authorization role, including transaction-local RLS isolation.
            replacement_ready_status, replacement_ready_body = send_get(replacement_gateway, "/ready")
            self.assertEqual(replacement_ready_status, 200, replacement_ready_body)
            replacement_status, replacement_body = send_request(
                replacement_gateway,
                input_value="post-revocation replacement probe",
            )
            self.assertEqual(replacement_status, 200, replacement_body)
            self.assertEqual(_ReplicaPolicyProviderHandler.request_count, 3)

            rotated_store = PostgresUsageStore(
                replacement_dsn,
                organization_ids=("xpounder", "beta"),
                schema=self.schema,
                runtime_role=self.runtime_role,
            )
            self.assertEqual(rotated_store.monthly_totals(organization_id="xpounder").requests, 3)
            self.assertEqual(rotated_store.monthly_totals(organization_id="beta").requests, 0)
        finally:
            for gateway, thread in zip(gateways, gateway_threads):
                if thread.is_alive():
                    gateway.shutdown()
            for gateway in gateways:
                gateway.server_close()
            for thread in gateway_threads:
                thread.join(timeout=10)
            provider.shutdown()
            provider.server_close()
            provider_thread.join(timeout=10)
            with self.psycopg.connect(self.owner_dsn, autocommit=True) as connection:
                with connection.cursor() as cursor:
                    for login in (old_login, replacement_login):
                        cursor.execute(self.sql.SQL("DROP ROLE IF EXISTS {}").format(self.sql.Identifier(login)))

    def test_terminated_idle_backend_connection_is_replaced_before_replica_egress(self) -> None:
        """A replica replaces a stale backend connection without affecting its sibling.

        This is deliberately a bounded connection-churn proof. It does not
        claim PostgreSQL database outage recovery or high availability.
        """

        _ReplicaPolicyProviderHandler.reset()
        provider = ThreadingHTTPServer(("127.0.0.1", 0), _ReplicaPolicyProviderHandler)
        provider_thread = threading.Thread(target=provider.serve_forever, daemon=True)
        provider_thread.start()

        gateways: list[GatewayServer] = []
        gateway_threads: list[threading.Thread] = []
        try:
            config, environment, _issuer = self._managed_config()
            service = PolicyControlService(config, environ=environment)
            service.bootstrap(organization_id="xpounder", credential_env="HORMUZ_POLICY_ADMIN_TOKEN")
            active_policy = self._stage(
                service,
                environment=environment,
                document=self._policy_document(),
            )
            service.activate(
                organization_id="xpounder",
                credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
                version_id=active_policy.version_id,
            )

            upstreams = dict(config.upstreams)
            upstreams["openai"] = replace(
                upstreams["openai"],
                base_url=f"http://127.0.0.1:{provider.server_port}/v1",
                api_key_env="TEST_REPLICA_CHURN_OPENAI_KEY",
            )
            upstreams["anthropic"] = replace(
                upstreams["anthropic"],
                api_key_env="TEST_REPLICA_CHURN_ANTHROPIC_KEY",
            )
            environment.update(
                {
                    "TEST_REPLICA_CHURN_OPENAI_KEY": "replica-churn-provider-key",
                    "TEST_REPLICA_CHURN_ANTHROPIC_KEY": "replica-churn-anthropic-key",
                }
            )
            configs = tuple(
                replace(
                    config,
                    listen=replace(config.listen, port=_free_port()),
                    upstreams=dict(upstreams),
                )
                for _ in range(2)
            )
            with mock.patch.dict(os.environ, environment, clear=False):
                gateways = [GatewayServer(replica_config) for replica_config in configs]

            self.assertIsNotNone(gateways[0].postgres_pool)
            self.assertIsNotNone(gateways[1].postgres_pool)
            self.assertIsNot(gateways[0].postgres_pool, gateways[1].postgres_pool)
            gateway_threads = [serve_in_thread(gateway) for gateway in gateways]

            def send_get(gateway: GatewayServer, path: str) -> tuple[int, bytes]:
                connection = http.client.HTTPConnection("127.0.0.1", gateway.server_port, timeout=5)
                try:
                    connection.request("GET", path)
                    response = connection.getresponse()
                    return response.status, response.read()
                finally:
                    connection.close()

            def send_request(gateway: GatewayServer, *, input_value: str) -> tuple[int, bytes]:
                connection = http.client.HTTPConnection("127.0.0.1", gateway.server_port, timeout=5)
                try:
                    connection.request(
                        "POST",
                        "/v1/responses",
                        body=json.dumps({"model": "gpt-5.4-mini", "input": input_value}),
                        headers={
                            "Authorization": "Bearer policy-test-alice-token",
                            "Content-Type": "application/json",
                        },
                    )
                    response = connection.getresponse()
                    return response.status, response.read()
                finally:
                    connection.close()

            churned_gateway, sibling_gateway = gateways
            self.assertIsNotNone(churned_gateway.postgres_pool)
            with churned_gateway.postgres_pool.connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid() AS backend_pid")
                    stale_backend = cursor.fetchone()
            assert stale_backend is not None
            stale_backend_pid = int(stale_backend["backend_pid"])

            with self.psycopg.connect(self.owner_dsn, autocommit=True) as owner:
                with owner.cursor() as cursor:
                    cursor.execute("SELECT pg_terminate_backend(%s)", (stale_backend_pid,))
                    self.assertTrue(cursor.fetchone()[0])

            churned_ready_status, churned_ready_body = send_get(churned_gateway, "/ready")
            self.assertEqual(churned_ready_status, 200, churned_ready_body)
            with churned_gateway.postgres_pool.connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid() AS backend_pid")
                    replacement_backend = cursor.fetchone()
            assert replacement_backend is not None
            self.assertNotEqual(stale_backend_pid, int(replacement_backend["backend_pid"]))

            churned_status, churned_body = send_request(
                churned_gateway,
                input_value="replacement backend gateway probe",
            )
            self.assertEqual(churned_status, 200, churned_body)
            self.assertEqual(_ReplicaPolicyProviderHandler.request_count, 1)

            sibling_ready_status, sibling_ready_body = send_get(sibling_gateway, "/ready")
            self.assertEqual(sibling_ready_status, 200, sibling_ready_body)
            sibling_status, sibling_body = send_request(
                sibling_gateway,
                input_value="independent sibling gateway probe",
            )
            self.assertEqual(sibling_status, 200, sibling_body)
            self.assertEqual(_ReplicaPolicyProviderHandler.request_count, 2)

            totals = churned_gateway.store.monthly_totals(organization_id="xpounder")
            self.assertEqual((totals.requests, totals.denied_requests), (2, 0))
            events = churned_gateway.store.audit_events(
                since="2000-01-01T00:00:00+00:00",
                organization_id="xpounder",
            )
            self.assertEqual(len(events), 2)
            self.assertEqual({event["status"] for event in events}, {"succeeded"})

            shared_store = PostgresUsageStore(
                self.runtime_dsn,
                organization_ids=("xpounder", "beta"),
                schema=self.schema,
                runtime_role=self.runtime_role,
            )
            self.assertEqual(shared_store.monthly_totals(organization_id="beta").requests, 0)
        finally:
            for gateway in gateways[: len(gateway_threads)]:
                gateway.shutdown()
            for gateway in gateways:
                gateway.server_close()
            for thread in gateway_threads:
                thread.join(timeout=10)
            provider.shutdown()
            provider.server_close()
            provider_thread.join(timeout=10)

    def test_runtime_pool_reuses_connections_without_tenant_state_leakage(self) -> None:
        pool = self._runtime_pool(
            min_connections=1,
            max_connections=1,
            acquire_timeout_seconds=2,
            max_waiting=2,
            max_lifetime_seconds=1800,
            max_idle_seconds=120,
        )
        store = PostgresUsageStore(
            self.runtime_dsn,
            organization_ids=("acme", "beta"),
            schema=self.schema,
            runtime_role=self.runtime_role,
            connection_pool=pool,
        )
        for organization_id in ("acme", "beta"):
            store.record(
                identity=_identity(organization_id),
                client="codex",
                protocol="openai",
                requested_model="gpt-test",
                resolved_alias="gpt-test",
                upstream_model="gpt-upstream",
                policy_action="allowed",
                status="succeeded",
            )
        store.verify_ready()

        observations: list[tuple[int, str, int]] = []
        for organization_id in ("acme", "beta"):
            with postgres_transaction(
                self.runtime_dsn,
                schema=self.schema,
                runtime_role=self.runtime_role,
                organization_id=organization_id,
                connection_pool=pool,
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT pg_backend_pid() AS backend_pid,
                               current_setting('hormuz.organization_id', true) AS organization_id
                        """
                    )
                    row = cursor.fetchone()
                    cursor.execute("SELECT COUNT(*) AS event_count FROM gateway_usage_events")
                    count = cursor.fetchone()
            assert row is not None and count is not None
            observations.append((int(row["backend_pid"]), str(row["organization_id"]), int(count["event_count"])))

        self.assertEqual(observations[0][0], observations[1][0])
        self.assertEqual(observations, [(observations[0][0], "acme", 1), (observations[0][0], "beta", 1)])

        with pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT current_setting('hormuz.organization_id', true) AS organization_id,
                           current_setting('search_path') AS search_path
                    """
                )
                reset = cursor.fetchone()
        assert reset is not None
        self.assertIn(reset["organization_id"], (None, ""))
        self.assertNotIn(self.schema, str(reset["search_path"]))

    def test_runtime_pool_saturation_fails_closed_before_tenant_query(self) -> None:
        pool = self._runtime_pool(
            min_connections=1,
            max_connections=1,
            acquire_timeout_seconds=1,
            max_waiting=1,
            max_lifetime_seconds=1800,
            max_idle_seconds=120,
        )
        outcomes: list[str] = []
        finished = threading.Event()

        def acquire_second_connection() -> None:
            try:
                with postgres_transaction(
                    self.runtime_dsn,
                    schema=self.schema,
                    runtime_role=self.runtime_role,
                    organization_id="acme",
                    connection_pool=pool,
                ):
                    outcomes.append("unexpected_connection")
            except PostgresStorageError as error:
                outcomes.append(error.code)
            finally:
                finished.set()

        with pool.connection():
            worker = threading.Thread(target=acquire_second_connection)
            worker.start()
            self.assertTrue(finished.wait(timeout=5))
            worker.join(timeout=5)
        self.assertEqual(outcomes, ["storage_pool_exhausted"])

    def test_runtime_pool_replaces_a_terminated_idle_connection(self) -> None:
        pool = self._runtime_pool(
            min_connections=1,
            max_connections=1,
            acquire_timeout_seconds=3,
            max_waiting=2,
            max_lifetime_seconds=1800,
            max_idle_seconds=120,
        )
        with postgres_transaction(
            self.runtime_dsn,
            schema=self.schema,
            runtime_role=self.runtime_role,
            organization_id="acme",
            connection_pool=pool,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid() AS backend_pid")
                first = cursor.fetchone()
        assert first is not None
        first_pid = int(first["backend_pid"])

        with self.psycopg.connect(self.owner_dsn, autocommit=True) as owner:
            with owner.cursor() as cursor:
                cursor.execute("SELECT pg_terminate_backend(%s)", (first_pid,))
                self.assertTrue(cursor.fetchone()[0])

        with postgres_transaction(
            self.runtime_dsn,
            schema=self.schema,
            runtime_role=self.runtime_role,
            organization_id="acme",
            connection_pool=pool,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid() AS backend_pid")
                second = cursor.fetchone()
        assert second is not None
        self.assertNotEqual(first_pid, int(second["backend_pid"]))

    def test_unknown_organization_fails_closed(self) -> None:
        with self.assertRaises(PostgresStorageError) as raised:
            self.store.monthly_totals(organization_id="unknown")
        self.assertEqual(raised.exception.code, "storage_organization_not_configured")


def _identity(organization_id: str) -> Identity:
    return Identity(
        token_env="TEST_TOKEN",
        token="postgres-test-employee-token",
        actor_id="alice",
        actor_name="Alice",
        team_id="engineering",
        team_name="Engineering",
        organization_id=organization_id,
    )


def _normalized_events(events: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {
            key: value
            for key, value in event.items()
            if key not in {"id", "occurred_at"}
        }
        for event in events
    ]


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _runtime_dsn(owner_dsn: str, role: str, password: str) -> str:
    prefix, separator, database = owner_dsn.rpartition("/")
    if not separator or not database:
        raise ValueError("HORMUZ_TEST_POSTGRES_DSN must contain a database name")
    authority = prefix.split("//", 1)[1].rsplit("@", 1)[-1]
    return f"postgresql://{quote(role, safe='')}:{quote(password, safe='')}@{authority}/{database}"


if __name__ == "__main__":
    unittest.main()
