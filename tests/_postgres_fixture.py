from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler
from urllib.parse import quote
from uuid import uuid4

import hormuz.postgres as postgres_module
from hormuz._attribution_schema import TABLE_DDL as ATTRIBUTION_TABLES
from hormuz._outcome_schema import TABLE_DDL as OUTCOME_TABLES
from hormuz._finance_schema import TABLE_DDL as FINANCE_TABLES
from hormuz._budget_schema import TABLE_DDL as BUDGET_TABLES
from hormuz._portfolio_schema import TABLE_DDL as REGISTRY_TABLES
from hormuz._provider_reliability_schema import TABLE_DDL as PROVIDER_RELIABILITY_TABLES
from hormuz._finance_attempt_schema import FINANCE_ATTEMPT_TABLE
from hormuz.config import GatewayConfig, Identity, PostgresPoolConfig
from hormuz.policy_control import PolicyControlService
from hormuz.postgres import (
    PostgresConnectionPool,
    migrate_postgres,
    postgres_transaction,
)
from hormuz.postgres_usage_store import PostgresUsageStore


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "contracts"


def without_finance_attempt_successor(snapshot: dict[str, object]) -> dict[str, object]:
    """Normalize schema-15 additions for older cumulative transition proofs."""

    normalized = copy.deepcopy(snapshot)
    rows = normalized["rows"]
    assert isinstance(rows, dict)
    rows.pop(FINANCE_ATTEMPT_TABLE, None)
    attempts = rows.get("gateway_request_attempts")
    if isinstance(attempts, list):
        stripped = []
        for item in attempts:
            value = json.loads(item[0])
            for column in (
                "configured_rate_card_state",
                "configured_rate_card_id",
                "configured_rate_card_version",
                "configured_rate_card_digest",
                "configured_rate_card_currency",
            ):
                value.pop(column, None)
            stripped.append((json.dumps(value, ensure_ascii=False, separators=(",", ":")),))
        rows["gateway_request_attempts"] = stripped
    migrations = rows.get("hormuz_schema_migrations")
    if isinstance(migrations, list):
        rows["hormuz_schema_migrations"] = [
            item for item in migrations if json.loads(item[0])["version"] != 15
        ]
    shape = normalized["shape"]
    assert isinstance(shape, list)
    normalized["shape"] = [
        item for item in shape
        if not str(item[0]).startswith("gateway_finance_attempt_")
        and item[0] != "gateway_request_attempt_event_organization_id"
    ]
    return normalized


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
class PostgresTestCase(unittest.TestCase):
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
        cls.custody_control_role = f"hormuz_custody_control_{suffix}"
        cls.custody_control_password = "hormuz-custody-control-test-password"
        cls.custody_control_dsn = _runtime_dsn(
            cls.owner_dsn,
            cls.custody_control_role,
            cls.custody_control_password,
        )
        cls.custody_executor_role = f"hormuz_custody_executor_{suffix}"
        cls.custody_executor_password = "hormuz-custody-executor-test-password"
        cls.custody_executor_dsn = _runtime_dsn(
            cls.owner_dsn,
            cls.custody_executor_role,
            cls.custody_executor_password,
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
                cursor.execute(
                    sql.SQL(
                        "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS"
                    ).format(sql.Identifier(cls.custody_control_role), sql.Literal(cls.custody_control_password))
                )
                cursor.execute(
                    sql.SQL(
                        "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS"
                    ).format(sql.Identifier(cls.custody_executor_role), sql.Literal(cls.custody_executor_password))
                )
        first = migrate_postgres(
            cls.owner_dsn,
            schema=cls.schema,
            runtime_role=cls.runtime_role,
            policy_control_role=cls.policy_control_role,
            custody_control_role=cls.custody_control_role,
            custody_executor_role=cls.custody_executor_role,
        )
        second = migrate_postgres(
            cls.owner_dsn,
            schema=cls.schema,
            runtime_role=cls.runtime_role,
            policy_control_role=cls.policy_control_role,
            custody_control_role=cls.custody_control_role,
            custody_executor_role=cls.custody_executor_role,
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
                cursor.execute(cls.sql.SQL("DROP ROLE IF EXISTS {}").format(cls.sql.Identifier(cls.custody_control_role)))
                cursor.execute(cls.sql.SQL("DROP ROLE IF EXISTS {}").format(cls.sql.Identifier(cls.custody_executor_role)))

    def setUp(self) -> None:
        with self.psycopg.connect(self.owner_dsn, autocommit=True) as connection:
            with connection.cursor() as cursor:
                tables = (
                    "custody_runtime_projection_acks",
                    "custody_runtime_projection_barriers",
                    "custody_runtime_replicas",
                    "custody_runtime_projection_restrictions",
                    "custody_runtime_projection_heads",
                    "custody_envelope_attestations",
                    "custody_lifecycle_events",
                    "custody_lifecycle_chain_heads",
                    "custody_lifecycle_asset_identities",
                    "custody_execution_events",
                    "custody_execution_attempts",
                    "custody_deletion_events",
                    "custody_control_events",
                    "custody_operation_approvals",
                    "custody_operation_intents",
                    "custody_administrators",
                    "custody_tenants",
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
                cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname=%s", (self.schema,))
                present = {row[0] for row in cursor.fetchall()}
                additive_tables = tuple(table for table in (
                    *REGISTRY_TABLES, *ATTRIBUTION_TABLES, *OUTCOME_TABLES,
                    *FINANCE_TABLES, *BUDGET_TABLES, *PROVIDER_RELIABILITY_TABLES,
                    FINANCE_ATTEMPT_TABLE,
                ) if table in present)
                immutable_tables = tuple(table for table in (
                    *ATTRIBUTION_TABLES, *OUTCOME_TABLES, *FINANCE_TABLES,
                    *BUDGET_TABLES, *PROVIDER_RELIABILITY_TABLES,
                    FINANCE_ATTEMPT_TABLE,
                ) if table in present)
                # This class owns its unique disposable schema. Reset the exact
                # FK-connected fixture tables together; never truncate a shared
                # user schema or disable production runtime protections.
                with connection.transaction():
                    for table in immutable_tables:
                        cursor.execute(self.sql.SQL("ALTER TABLE {}.{} DISABLE TRIGGER {}").format(
                            self.sql.Identifier(self.schema), self.sql.Identifier(table),
                            self.sql.Identifier(table + "_immutable")))
                    cursor.execute(self.sql.SQL("TRUNCATE TABLE {}").format(self.sql.SQL(", ").join(
                        self.sql.SQL("{}.{}").format(self.sql.Identifier(self.schema), self.sql.Identifier(table))
                        for table in (*tables, *additive_tables)
                    )))
                    for table in immutable_tables:
                        cursor.execute(self.sql.SQL("ALTER TABLE {}.{} ENABLE TRIGGER {}").format(
                            self.sql.Identifier(self.schema), self.sql.Identifier(table),
                            self.sql.Identifier(table + "_immutable")))
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

    def _managed_custody_config(
        self,
        *,
        bootstrap_bob: bool = True,
        include_oidc: bool = False,
        authorization_ttl_seconds: int = 900,
        lifecycle: bool = False,
        include_retirement_fixture_assets: bool = False,
    ) -> tuple[GatewayConfig, dict[str, str], str | None]:
        """Return a managed-custody config with separate control credentials."""

        value = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        value["usage_storage"] = {
            "backend": "postgresql",
            "postgres_dsn_env": "TEST_POSTGRES_RUNTIME_DSN",
            "postgres_migration_dsn_env": "TEST_POSTGRES_MIGRATION_DSN",
            "postgres_schema": self.schema,
            "postgres_runtime_role": self.runtime_role,
        }
        if bootstrap_bob:
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
                                    "subject": "runtime-user",
                                    "actor_id": "runtime-user",
                                    "actor_name": "Runtime User",
                                    "team_id": "engineering",
                                    "team_name": "Engineering",
                                    "organization_id": "xpounder",
                                    "identity_type": "human",
                                    "clearance": "confidential",
                                    "allowed_clients": ["codex"],
                                }
                            ],
                        }
                    ]
                }
            }
        value["key_custody"] = {
            "backend": "openbao-transit",
            "endpoint_url": "http://127.0.0.1:8200",
            "token_env": "HORMUZ_OPENBAO_TOKEN",
            "transit_mount": "transit",
            "key_references": {
                "provider_credential": "provider-key",
                "identity_connector_secret": "identity-key",
                "session_material": "session-key",
                "approval_fingerprint": "approval-key",
                "data_encryption": "data-key",
            },
        }
        administrators: list[dict[str, str]] = [
            {"organization_id": "xpounder", "actor_id": "alice"}
        ]
        if bootstrap_bob:
            administrators.append({"organization_id": "xpounder", "actor_id": "bob"})
        value["custody_control"] = {
            "mode": "postgresql",
            "postgres_control_dsn_env": "TEST_POSTGRES_CUSTODY_CONTROL_DSN",
            "postgres_control_role": self.custody_control_role,
            "authorization_ttl_seconds": authorization_ttl_seconds,
            "bootstrap_administrators": administrators,
        }
        value["custody_retention"] = {
            "retention_days": 365,
            "legal_hold": False,
        }
        value["custody_executor"] = {
            "postgres_executor_dsn_env": "TEST_POSTGRES_CUSTODY_EXECUTOR_DSN",
            "postgres_executor_role": self.custody_executor_role,
            "pending_attempt_ttl_seconds": authorization_ttl_seconds,
        }
        if lifecycle:
            lifecycle_assets: list[dict[str, object]] = [
                {
                    "asset_type": "provider_credential",
                    "asset_id": "openai-primary",
                    "generation": 1,
                    "binding": {"protocol": "openai"},
                },
                {
                    "asset_type": "provider_credential",
                    "asset_id": "anthropic-primary",
                    "generation": 1,
                    "binding": {"protocol": "anthropic"},
                },
                *[
                    {
                        "asset_type": "key_reference",
                        "asset_id": f"{purpose}-current",
                        "generation": 1,
                        "binding": {"purpose": purpose, "key_reference": reference},
                    }
                    for purpose, reference in value["key_custody"]["key_references"].items()  # type: ignore[index]
                ],
            ]
            if include_retirement_fixture_assets:
                lifecycle_assets.extend(
                    [
                        {
                            "asset_type": "key_reference",
                            "asset_id": "provider-credential-prior",
                            "generation": 1,
                            "binding": {"purpose": "provider_credential", "key_reference": "provider-key-prior"},
                        },
                        {
                            "asset_type": "envelope",
                            "asset_id": "openai-primary-envelope",
                            "generation": 1,
                            "binding": {
                                "path": "/private/hormuz-test-openai.envelope",
                                "provider_credential_asset_id": "openai-primary",
                                "provider_credential_generation": 1,
                                "key_reference_asset_id": "provider-credential-prior",
                                "key_reference_generation": 1,
                            },
                        },
                        {
                            "asset_type": "envelope",
                            "asset_id": "openai-current-envelope",
                            "generation": 1,
                            "binding": {
                                "path": "/private/hormuz-test-openai-current.envelope",
                                "provider_credential_asset_id": "openai-primary",
                                "provider_credential_generation": 1,
                                "key_reference_asset_id": "provider_credential-current",
                                "key_reference_generation": 1,
                            },
                        },
                    ]
                )
            value["custody_lifecycle"] = {
                "freshness_lease_seconds": 5,
                "assets": lifecycle_assets,
            }
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "hormuz-managed-custody.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        environment = {
            "HORMUZ_TOKEN": "custody-test-alice-token",
            "TEST_POSTGRES_RUNTIME_DSN": self.runtime_dsn,
            "TEST_POSTGRES_MIGRATION_DSN": self.owner_dsn,
            "TEST_POSTGRES_CUSTODY_CONTROL_DSN": self.custody_control_dsn,
            "TEST_POSTGRES_CUSTODY_EXECUTOR_DSN": self.custody_executor_dsn,
            "HORMUZ_CUSTODY_ADMIN_TOKEN": "custody-test-alice-token",
            "HORMUZ_OPENBAO_TOKEN": "openbao-test-token-value",
        }
        if bootstrap_bob:
            environment["HORMUZ_BOB_TOKEN"] = "custody-test-bob-token"
            environment["HORMUZ_CUSTODY_BOB_TOKEN"] = "custody-test-bob-token"
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
