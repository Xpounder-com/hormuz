from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from uuid import uuid4

from hormuz.cli import main
from hormuz.config import GatewayConfig, UsageStorageConfig
from hormuz.postgres import (
    POSTGRES_SCHEMA_VERSION,
    PostgresStorageError,
    bootstrap_postgres_deployment,
    migrate_postgres,
    postgres_transaction,
    verify_postgres_schema,
)
from hormuz.postgres_usage_store import PostgresUsageStore
from hormuz.provider_reliability import ProviderAttemptMetrics, ProviderFailoverContext
from hormuz.store import ReservationScope
from hormuz.store_router import create_provider_reliability_repository, create_usage_store
if __package__:
    from ._postgres_fixture import ROOT, PostgresTestCase, _identity, _runtime_dsn
else:  # Isolated wheel compatibility discovery uses the tests directory as its import root.
    from _postgres_fixture import ROOT, PostgresTestCase, _identity, _runtime_dsn


class PostgresMigrationRLSTests(PostgresTestCase):
    def test_deployment_bootstrap_rejects_startup_role_impersonation(self) -> None:
        suffix = uuid4().hex[:12]
        runtime_role = f"hormuz_runtime_i_{suffix}"
        policy_role = f"hormuz_policy_i_{suffix}"
        custody_role = f"hormuz_custody_i_{suffix}"
        executor_role = f"hormuz_executor_i_{suffix}"
        impersonated_runtime_dsn = self.psycopg.conninfo.make_conninfo(
            self.owner_dsn,
            options=f"-c role={self.runtime_role}",
        )

        with self.psycopg.connect(impersonated_runtime_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT session_user, current_user")
                session_user, current_user = map(str, cursor.fetchone())
        self.assertNotEqual(session_user, current_user)
        self.assertEqual(current_user, self.runtime_role)

        with self.assertRaisesRegex(
            PostgresStorageError,
            "postgres_bootstrap_runtime_identity_invalid",
        ):
            bootstrap_postgres_deployment(
                self.owner_dsn,
                impersonated_runtime_dsn,
                schema=f"hormuz_impersonation_{suffix}",
                runtime_role=runtime_role,
                policy_control_role=policy_role,
                custody_control_role=custody_role,
                custody_executor_role=executor_role,
            )

    def test_deployment_bootstrap_separates_managed_login_from_schema_owner(self) -> None:
        suffix = uuid4().hex[:12]
        schema = f"hormuz_bootstrap_{suffix}"
        runtime_login = f"hormuz_login_{suffix}"
        unexpected_member = f"hormuz_old_login_{suffix}"
        runtime_role = f"hormuz_runtime_b_{suffix}"
        policy_role = f"hormuz_policy_b_{suffix}"
        custody_role = f"hormuz_custody_b_{suffix}"
        executor_role = f"hormuz_executor_b_{suffix}"
        password = "hormuz-managed-runtime-login-password"
        runtime_dsn = _runtime_dsn(self.owner_dsn, runtime_login, password)
        roles = (runtime_role, policy_role, custody_role, executor_role)
        try:
            with self.psycopg.connect(self.owner_dsn, autocommit=True) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        self.sql.SQL(
                            "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB "
                            "NOCREATEROLE INHERIT NOREPLICATION NOBYPASSRLS"
                        ).format(
                            self.sql.Identifier(runtime_login),
                            self.sql.Literal(password),
                        )
                    )

            first = bootstrap_postgres_deployment(
                self.owner_dsn,
                runtime_dsn,
                schema=schema,
                runtime_role=runtime_role,
                policy_control_role=policy_role,
                custody_control_role=custody_role,
                custody_executor_role=executor_role,
            )
            second = bootstrap_postgres_deployment(
                self.owner_dsn,
                runtime_dsn,
                schema=schema,
                runtime_role=runtime_role,
                policy_control_role=policy_role,
                custody_control_role=custody_role,
                custody_executor_role=executor_role,
            )
            self.assertEqual(first, second)
            self.assertEqual(first.schema_version, POSTGRES_SCHEMA_VERSION)
            self.assertEqual(first.restricted_roles, 4)
            self.assertTrue(first.runtime_login_restricted)
            self.assertTrue(first.runtime_membership_verified)

            with self.psycopg.connect(self.owner_dsn) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT rolcanlogin, rolinherit, rolsuper, rolcreatedb,
                               rolcreaterole, rolreplication, rolbypassrls
                        FROM pg_roles WHERE rolname = %s
                        """,
                        (runtime_login,),
                    )
                    self.assertEqual(
                        cursor.fetchone(),
                        (True, False, False, False, False, False, False),
                    )
                    cursor.execute(
                        """
                        SELECT granted.rolname
                        FROM pg_auth_members AS membership
                        JOIN pg_roles AS granted ON granted.oid = membership.roleid
                        JOIN pg_roles AS member ON member.oid = membership.member
                        WHERE member.rolname = %s
                        """,
                        (runtime_login,),
                    )
                    self.assertEqual(cursor.fetchall(), [(runtime_role,)])

            with self.assertRaises(self.psycopg.errors.InsufficientPrivilege):
                with self.psycopg.connect(runtime_dsn) as connection:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            self.sql.SQL(
                                "SELECT COUNT(*) FROM {}.gateway_usage_events"
                            ).format(self.sql.Identifier(schema))
                        )
            status = verify_postgres_schema(
                runtime_dsn,
                schema=schema,
                runtime_role=runtime_role,
            )
            self.assertTrue(status.complete)

            with self.psycopg.connect(self.owner_dsn, autocommit=True) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        self.sql.SQL("GRANT {} TO {} WITH ADMIN OPTION").format(
                            self.sql.Identifier(runtime_role),
                            self.sql.Identifier(runtime_login),
                        )
                    )
            with self.assertRaisesRegex(
                PostgresStorageError,
                "postgres_bootstrap_authorization_membership_unsafe",
            ):
                bootstrap_postgres_deployment(
                    self.owner_dsn,
                    runtime_dsn,
                    schema=schema,
                    runtime_role=runtime_role,
                    policy_control_role=policy_role,
                    custody_control_role=custody_role,
                    custody_executor_role=executor_role,
                )
            with self.psycopg.connect(self.owner_dsn, autocommit=True) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        self.sql.SQL("REVOKE ADMIN OPTION FOR {} FROM {}").format(
                            self.sql.Identifier(runtime_role),
                            self.sql.Identifier(runtime_login),
                        )
                    )

            with self.psycopg.connect(self.owner_dsn, autocommit=True) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        self.sql.SQL("CREATE ROLE {} NOLOGIN").format(
                            self.sql.Identifier(unexpected_member)
                        )
                    )
                    cursor.execute(
                        self.sql.SQL("GRANT {} TO {}").format(
                            self.sql.Identifier(runtime_role),
                            self.sql.Identifier(unexpected_member),
                        )
                    )
            with self.assertRaisesRegex(
                PostgresStorageError,
                "postgres_bootstrap_authorization_membership_unsafe",
            ):
                bootstrap_postgres_deployment(
                    self.owner_dsn,
                    runtime_dsn,
                    schema=schema,
                    runtime_role=runtime_role,
                    policy_control_role=policy_role,
                    custody_control_role=custody_role,
                    custody_executor_role=executor_role,
                )
            with self.psycopg.connect(self.owner_dsn, autocommit=True) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        self.sql.SQL("REVOKE {} FROM {}").format(
                            self.sql.Identifier(runtime_role),
                            self.sql.Identifier(unexpected_member),
                        )
                    )
                    cursor.execute(
                        self.sql.SQL("GRANT {} TO {}").format(
                            self.sql.Identifier(runtime_login),
                            self.sql.Identifier(unexpected_member),
                        )
                    )
            with self.assertRaisesRegex(
                PostgresStorageError,
                "postgres_bootstrap_runtime_membership_unsafe",
            ):
                bootstrap_postgres_deployment(
                    self.owner_dsn,
                    runtime_dsn,
                    schema=schema,
                    runtime_role=runtime_role,
                    policy_control_role=policy_role,
                    custody_control_role=custody_role,
                    custody_executor_role=executor_role,
                )
        finally:
            with self.psycopg.connect(self.owner_dsn, autocommit=True) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        self.sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                            self.sql.Identifier(schema)
                        )
                    )
                    cursor.execute(
                        self.sql.SQL("DROP ROLE IF EXISTS {}").format(
                            self.sql.Identifier(unexpected_member)
                        )
                    )
                    cursor.execute(
                        self.sql.SQL("DROP ROLE IF EXISTS {}").format(
                            self.sql.Identifier(runtime_login)
                        )
                    )
                    for role in roles:
                        cursor.execute(
                            self.sql.SQL("DROP ROLE IF EXISTS {}").format(
                                self.sql.Identifier(role)
                            )
                        )

    def test_provider_reliability_totals_are_actor_and_tenant_scoped(self) -> None:
        reliability = create_provider_reliability_repository(self.store)
        assert reliability is not None
        original = self.store.begin_request_attempt(
            identity=_identity("acme"),
            client="codex",
            protocol="openai",
            requested_model="engineering-fast",
            resolved_alias="engineering-fast",
            upstream_model="gpt-fast",
            policy_version="policy-v1",
            policy_action="allowed",
            redaction_count=0,
            redaction_rules=(),
            scopes=(),
            reserved_tokens=10,
            reserved_cost_microusd=20,
            ttl_seconds=60,
        )
        reliability.finalize_request_attempt(
            attempt=original,
            organization_id="acme",
            status="rate_limited",
            provider_metrics=ProviderAttemptMetrics(429, 1_000, None, 1_500, 0, 0),
        )
        alternate = reliability.begin_request_attempt(
            identity=_identity("acme"),
            client="codex",
            protocol="openai",
            requested_model="engineering-fast",
            resolved_alias="engineering-deep",
            upstream_model="gpt-deep",
            policy_version="policy-v1",
            policy_action="allowed",
            redaction_count=0,
            redaction_rules=(),
            scopes=(),
            reserved_tokens=10,
            reserved_cost_microusd=20,
            ttl_seconds=60,
            work_budget=None,
            provider_failover=ProviderFailoverContext(
                original_attempt_id=original.attempt_id,
                trigger_status=429,
                reason_code="provider_rate_limited",
            ),
        )
        reliability.finalize_request_attempt(
            attempt=alternate,
            organization_id="acme",
            status="succeeded",
            provider_metrics=ProviderAttemptMetrics(200, 800, 1_100, 1_600, 12, 12),
        )

        totals = reliability.totals(
            actor_id="alice",
            organization_id="acme",
        )
        self.assertEqual(totals.attempt_count, 2)
        self.assertEqual(totals.live_provider_request_count, 1)
        self.assertEqual(totals.provider_attempt_record_count, 2)
        self.assertEqual(totals.latency_header_sample_count, 2)
        self.assertEqual(totals.latency_first_body_byte_sample_count, 1)
        self.assertEqual(totals.latency_total_sample_count, 2)
        self.assertEqual(totals.failover_link_record_count, 1)
        self.assertEqual(totals.outcome_unknown_count, 0)
        self.assertEqual(
            reliability.totals(
                actor_id="bob",
                organization_id="acme",
            ).attempt_count,
            0,
        )
        self.assertEqual(
            reliability.totals(
                actor_id="alice",
                organization_id="beta",
            ).attempt_count,
            0,
        )

    def test_migration_is_visible_to_the_restricted_runtime_role(self) -> None:
        status = verify_postgres_schema(
            self.runtime_dsn,
            schema=self.schema,
            runtime_role=self.runtime_role,
        )
        self.assertTrue(status.complete)
        self.assertEqual(status.version, POSTGRES_SCHEMA_VERSION)

    def test_schema_v8_missing_custody_evidence_trigger_fails_closed(self) -> None:
        """A migration ledger alone cannot stand in for v2 evidence guards."""

        schema = self._create_schema_v2_fixture()
        status = migrate_postgres(
            self.owner_dsn,
            schema=schema,
            runtime_role=self.runtime_role,
            policy_control_role=self.policy_control_role,
            custody_control_role=self.custody_control_role,
            custody_executor_role=self.custody_executor_role,
        )
        self.assertEqual(status.version, POSTGRES_SCHEMA_VERSION)
        with self.psycopg.connect(self.owner_dsn, autocommit=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self.sql.SQL("DROP TRIGGER custody_deletion_events_contract_required ON {}.custody_deletion_events").format(
                        self.sql.Identifier(schema)
                    )
                )

        with self.assertRaises(PostgresStorageError) as raised:
            verify_postgres_schema(
                self.runtime_dsn,
                schema=schema,
                runtime_role=self.runtime_role,
            )
        self.assertEqual(raised.exception.code, "storage_schema_partial_upgrade")

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
        self.assertEqual(status.version, POSTGRES_SCHEMA_VERSION)
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
            custody_control_role=self.custody_control_role,
            custody_executor_role=self.custody_executor_role,
        )
        self.assertEqual(status.version, POSTGRES_SCHEMA_VERSION)
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
                custody_control_role=self.custody_control_role,
                custody_executor_role=self.custody_executor_role,
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
                custody_control_role=self.custody_control_role,
                custody_executor_role=self.custody_executor_role,
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
                custody_control_role=self.custody_control_role,
                custody_executor_role=self.custody_executor_role,
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
                self.assertEqual(
                    migration.getvalue(),
                    f"PostgreSQL usage storage migration is current: v{POSTGRES_SCHEMA_VERSION}\n",
                )

                verification = io.StringIO()
                with redirect_stdout(verification):
                    self.assertEqual(main(["--config", str(config_path), "storage", "verify"]), 0)
                self.assertEqual(verification.getvalue(), "usage storage verified: postgresql\n")
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
        newer_version = POSTGRES_SCHEMA_VERSION + 1
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
                            "INSERT INTO {}.hormuz_schema_migrations (version, state) VALUES (%s, 'applying')"
                        ).format(self.sql.Identifier(self.schema)),
                        (newer_version,),
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
                            "UPDATE {}.hormuz_schema_migrations SET state = 'applied' WHERE version = %s"
                        ).format(self.sql.Identifier(self.schema)),
                        (newer_version,),
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
                            "DELETE FROM {}.hormuz_schema_migrations WHERE version = %s"
                        ).format(self.sql.Identifier(self.schema)),
                        (newer_version,),
                    )


if __name__ == "__main__":
    unittest.main()
