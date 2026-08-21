from __future__ import annotations

from contextlib import contextmanager, redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest import mock
from types import SimpleNamespace

from hormuz.cli import build_parser, main
from hormuz.config import (
    ContextInjectionPolicy,
    DLPApprovalConfig,
    DLPControls,
    DLPRuleConfig,
    Identity,
    ModelRoute,
    Policy,
    SecretControls,
)
from hormuz.identity_projection import (
    IdentitySyncResult,
    configured_organization_ids,
    identity_projection,
    principal_projection_sha256,
    projection_sha256,
)
from hormuz.policy_projection import (
    PolicySyncResult,
    policy_projection,
    policy_projection_sha256,
)
from hormuz.postgres_policy_store import (
    PolicyAdminError,
    PostgresPolicyStore,
    version_id_from_sha256,
)
from hormuz.postgres import (
    POSTGRES_SCHEMA_VERSION,
    TENANT_TABLES,
    PostgresFoundationStatus,
    PostgresStorageError,
    TenantContext,
    _verify_migration_prefix,
    load_postgres_migrations,
    postgres_dsn_from_env,
    tenant_transaction,
    validate_postgres_identifier,
    validate_tenant_id,
)
from hormuz.postgres_usage_store import PostgresUsageStore
from hormuz.postgres_security_store import PostgresSecurityStore
from hormuz.postgres_session_store import PostgresSessionStore
from hormuz.store import SecurityStoreError
from hormuz.store_router import GatewayStoreRouter, gateway_store
from scripts.postgres_foundation_integration import (
    EVIDENCE_SCHEMA,
    PostgresFoundationIntegrationError,
    write_evidence,
)


ROOT = Path(__file__).resolve().parents[1]


class PostgresFoundationTests(unittest.TestCase):
    def test_policy_projection_is_secret_free_deterministic_and_tenant_partitioned(self) -> None:
        alice = Identity(
            token_env="ALICE_TOKEN",
            token="identity-secret",
            actor_id="alice",
            actor_name="Alice",
            team_id="engineering",
            team_name="Engineering",
            organization_id="tenant-a",
        )
        bob = Identity(
            token_env="BOB_TOKEN",
            token="other-identity-secret",
            actor_id="bob",
            actor_name="Bob",
            team_id="marketing",
            team_name="Marketing",
            organization_id="tenant-b",
        )
        config = SimpleNamespace(
            identities_by_token={alice.token: alice, bob.token: bob},
            identities_by_subject={},
            identities_by_actor={"alice": alice, "bob": bob},
            model_routes={
                "reasoning": ModelRoute("reasoning", "openai", "gpt-5.4"),
            },
            organization_policy=Policy(allowed_models=("reasoning",)),
            team_policies={"engineering": Policy(max_output_tokens=2048)},
            actor_policies={},
            secret_controls=SecretControls(
                custom_secret_envs=("COMPANY_SECRET",),
                custom_secret_values=(("company_secret", "never-store-this"),),
            ),
            dlp_controls=DLPControls(
                policy_version="policy-v1",
                rules=(
                    DLPRuleConfig(
                        "customer-id",
                        "customer",
                        "high",
                        "require_approval",
                        values_env="CUSTOMER_IDS",
                        exact_values=("customer-secret",),
                    ),
                ),
                approval=DLPApprovalConfig(
                    enabled=True,
                    fingerprint_key_env="APPROVAL_KEY",
                    fingerprint_key=b"k" * 32,
                    fingerprint_key_source="resolved-secret-source",
                ),
            ),
            team_dlp_overlays={},
            actor_dlp_overlays={},
        )

        value = policy_projection(config, "tenant-a")
        serialized = json.dumps(value, sort_keys=True)
        self.assertEqual(value["schema"], "hormuz.policy-projection.v5")
        self.assertNotIn("context_injection", serialized)
        self.assertEqual(
            policy_projection_sha256(value),
            policy_projection_sha256(policy_projection(config, "tenant-a")),
        )
        self.assertIn("engineering", serialized)
        self.assertNotIn("marketing", serialized)
        for secret in (
            "identity-secret",
            "other-identity-secret",
            "never-store-this",
            "customer-secret",
            "resolved-secret-source",
        ):
            self.assertNotIn(secret, serialized)

        fingerprint = policy_projection_sha256(value)
        config.organization_policy = Policy(
            allowed_models=("reasoning",),
            context_injection=ContextInjectionPolicy(
                mode="required",
                allowed_repositories=("deprecated/repository",),
                token_budget=999,
            ),
        )
        self.assertEqual(
            policy_projection_sha256(policy_projection(config, "tenant-a")),
            fingerprint,
        )

    def test_identity_projection_is_secret_free_deterministic_and_tenant_partitioned(self) -> None:
        first = Identity(
            token_env="FIRST_TOKEN",
            token="first-secret-value",
            actor_id="alice",
            actor_name="Alice",
            team_id="engineering",
            team_name="Engineering",
            organization_id="tenant-a",
            allowed_clients=("codex",),
        )
        second = Identity(
            token_env="SECOND_TOKEN",
            token="different-secret-value",
            actor_id="alice",
            actor_name="Alice",
            team_id="engineering",
            team_name="Engineering",
            organization_id="tenant-a",
            allowed_clients=("codex",),
        )
        other = Identity(
            token_env="OTHER_TOKEN",
            token="other-secret-value",
            actor_id="bob",
            actor_name="Bob",
            team_id="marketing",
            team_name="Marketing",
            organization_id="tenant-b",
        )
        config_a = SimpleNamespace(
            identities_by_token={first.token: first, other.token: other},
            identities_by_subject={},
        )
        config_b = SimpleNamespace(
            identities_by_token={second.token: second, other.token: other},
            identities_by_subject={},
        )
        self.assertEqual(configured_organization_ids(config_a), ("tenant-a", "tenant-b"))
        value = identity_projection(config_a, "tenant-a")
        self.assertEqual(projection_sha256(value), projection_sha256(identity_projection(config_b, "tenant-a")))
        serialized = json.dumps(value, sort_keys=True)
        self.assertNotIn("secret", serialized)
        self.assertNotIn("bob", serialized)

    def test_oidc_subject_change_updates_principal_authorization_fingerprint(self) -> None:
        identity = Identity(
            token_env="",
            token="",
            actor_id="alice",
            actor_name="Alice",
            team_id="engineering",
            team_name="Engineering",
            organization_id="tenant-a",
            allowed_clients=("codex",),
        )
        first = SimpleNamespace(
            identities_by_token={},
            identities_by_subject={("https://id.example", "subject-a"): identity},
        )
        second = SimpleNamespace(
            identities_by_token={},
            identities_by_subject={("https://id.example", "subject-b"): identity},
        )
        first_principal = identity_projection(first, "tenant-a")["principals"][0]
        second_principal = identity_projection(second, "tenant-a")["principals"][0]
        self.assertNotEqual(
            principal_projection_sha256(first_principal),
            principal_projection_sha256(second_principal),
        )

    def test_postgres_session_routing_tag_is_keyed_and_unambiguous(self) -> None:
        store = PostgresSessionStore(
            "postgresql://not-connected",
            organization_ids=("tenant-a", "tenant-b"),
            master_key=b"m" * 32,
            access_ttl_seconds=600,
            absolute_ttl_seconds=43_200,
            enrollment_ttl_seconds=300,
        )
        tag = store._routing_tag("tenant-a")
        self.assertRegex(tag, r"[0-9a-f]{24}\Z")
        self.assertNotIn("tenant", tag)
        routed = "hox_a_" + tag + "_" + "s" * 32
        self.assertEqual(store._route(routed, "hox_a_", "invalid"), "tenant-a")

    def test_postgres_usage_store_requires_explicit_scope_for_multiple_tenants(self) -> None:
        store = PostgresUsageStore(
            "postgresql://runtime:not-used@127.0.0.1/hormuz",
            organization_ids=("tenant-a", "tenant-b"),
        )
        with self.assertRaisesRegex(ValueError, "organization_id is required"):
            store.monthly_totals()
        with self.assertRaisesRegex(PostgresStorageError, "tenant_not_configured"):
            store.monthly_totals(organization_id="tenant-c")

    def test_postgres_usage_audit_rechecks_scope_before_database_io(self) -> None:
        store = PostgresUsageStore(
            "postgresql://runtime:not-used@127.0.0.1/hormuz",
            organization_ids=("tenant-a", "tenant-b"),
        )
        manager = Identity(
            token_env="",
            token="",
            actor_id="manager",
            actor_name="Engineering Manager",
            team_id="engineering",
            team_name="Engineering",
            organization_id="tenant-a",
            capabilities=("usage_team_viewer",),
        )
        with self.assertRaisesRegex(
            SecurityStoreError,
            "usage_report_scope_forbidden",
        ):
            store.record_admin_usage_read(
                administrator=manager,
                access_scope="team",
                group_by="person",
                actor_filter=None,
                team_filter="engineering",
                window_start="2026-08-01T00:00:00+00:00",
                window_end="2026-08-01T00:01:00+00:00",
                result_count=1,
            )
        with self.assertRaisesRegex(
            SecurityStoreError,
            "usage_admin_audit_scope_mismatch",
        ):
            store.record_admin_usage_read(
                administrator=manager,
                access_scope="organization",
                group_by="model",
                actor_filter=None,
                team_filter="engineering",
                window_start="2026-08-01T00:00:00+00:00",
                window_end="2026-08-01T00:01:00+00:00",
                result_count=1,
            )

    def test_postgres_security_store_requires_explicit_scope_for_multiple_tenants(self) -> None:
        store = PostgresSecurityStore(
            "postgresql://runtime:not-used@127.0.0.1/hormuz",
            organization_ids=("tenant-a", "tenant-b"),
        )
        with self.assertRaisesRegex(ValueError, "organization_id is required"):
            store.monthly_secret_totals()
        with self.assertRaisesRegex(PostgresStorageError, "tenant_not_configured"):
            store.monthly_secret_totals(organization_id="tenant-c")

    def test_policy_administration_rejects_unauthorized_or_malformed_inputs_before_io(self) -> None:
        store = PostgresPolicyStore(
            "postgresql://runtime:not-used@127.0.0.1/hormuz",
            organization_ids=("tenant-a", "tenant-b"),
        )
        unauthorized = Identity(
            token_env="",
            token="",
            actor_id="alice",
            actor_name="Alice",
            team_id="engineering",
            team_name="Engineering",
            organization_id="tenant-a",
        )
        with self.assertRaisesRegex(
            PolicyAdminError,
            "policy_admin_capability_required",
        ):
            store.stage(identity=unauthorized, config=SimpleNamespace())  # type: ignore[arg-type]

        administrator = Identity(
            token_env="",
            token="",
            actor_id="admin",
            actor_name="Policy Admin",
            team_id="security",
            team_name="Security",
            organization_id="tenant-a",
            capabilities=("policy_admin",),
        )
        with self.assertRaisesRegex(PolicyAdminError, "policy_version_id_invalid"):
            store.activate(
                identity=administrator,
                version_id="not-a-version",
                expected_active_version_id=None,
            )
        self.assertEqual(version_id_from_sha256("a" * 64), "hpv_v1_" + "a" * 64)
        with self.assertRaisesRegex(
            PolicyAdminError,
            "policy_projection_sha256_invalid",
        ):
            version_id_from_sha256("A" * 64)

    def test_split_store_routes_accounting_and_security_and_combines_audit(self) -> None:
        accounting = mock.Mock()
        security = mock.Mock()
        legacy = mock.Mock()
        legacy.path = Path("security.sqlite3")
        accounting.record.return_value = "usage-id"
        accounting.record_admin_audit_read.return_value = "audit-read-id"
        security.record_secret_event.return_value = "security-id"
        accounting.audit_events.return_value = [
            {"event_type": "usage", "id": "a", "occurred_at": "2026-08-20T00:00:00Z"}
        ]
        security.audit_events.return_value = [
            {
                "event_type": "security.dlp",
                "id": "b",
                "occurred_at": "2026-08-20T00:00:01Z",
            }
        ]
        legacy.audit_events.return_value = [
            {
                "event_type": "security.secret",
                "id": "c",
                "occurred_at": "2026-08-19T23:59:59Z",
            }
        ]
        router = GatewayStoreRouter(accounting, security, legacy_security=legacy)

        self.assertEqual(router.record(test=True), "usage-id")
        self.assertEqual(router.record_admin_audit_read(test=True), "audit-read-id")
        self.assertEqual(router.record_secret_event(test=True), "security-id")
        self.assertEqual(
            [
                event["id"]
                for event in router.audit_events(
                    since="2026-08-20T00:00:00Z",
                    kind="security",
                    organization_id="tenant-a",
                    until="2026-08-21T00:00:00Z",
                )
            ],
            ["c", "a", "b"],
        )
        accounting.record.assert_called_once_with(test=True)
        accounting.record_admin_audit_read.assert_called_once_with(test=True)
        security.record_secret_event.assert_called_once_with(test=True)
        expected_audit_options = {
            "since": "2026-08-20T00:00:00Z",
            "kind": "security",
            "organization_id": "tenant-a",
            "until": "2026-08-21T00:00:00Z",
        }
        accounting.audit_events.assert_called_once_with(**expected_audit_options)
        security.audit_events.assert_called_once_with(**expected_audit_options)
        legacy.audit_events.assert_called_once_with(**expected_audit_options)
        self.assertEqual(router.security_database_path, Path("security.sqlite3"))

    def test_postgres_gateway_store_fails_closed_before_building_on_stale_policy(self) -> None:
        config = SimpleNamespace(
            usage_storage=SimpleNamespace(
                backend="postgresql",
                postgres_dsn_env="HORMUZ_POSTGRES_DSN",
                postgres_schema="hormuz",
                postgres_runtime_role="hormuz_runtime",
            ),
            identities_by_actor={
                "alice": SimpleNamespace(organization_id="tenant-a")
            },
            database_path=Path("legacy.sqlite3"),
        )
        with (
            mock.patch(
                "hormuz.store_router.postgres_dsn_from_env",
                return_value="postgresql://runtime:not-printed@localhost/hormuz",
            ),
            mock.patch(
                "hormuz.store_router.verify_runtime_policy_projection",
                side_effect=PostgresStorageError("policy_projection_stale"),
            ) as verify,
            mock.patch("hormuz.store_router.PostgresUsageStore") as accounting,
            self.assertRaisesRegex(PostgresStorageError, "policy_projection_stale"),
        ):
            gateway_store(config)
        verify.assert_called_once()
        accounting.assert_not_called()

    def test_packaged_migrations_are_contiguous_and_match_target(self) -> None:
        migrations = load_postgres_migrations()

        self.assertEqual(
            [migration.version for migration in migrations],
            list(range(1, POSTGRES_SCHEMA_VERSION + 1)),
        )
        self.assertEqual(len(migrations[0].sha256), 64)
        migration_source = "\n".join(migration.sql for migration in migrations)
        for table in TENANT_TABLES:
            self.assertIn(f"CREATE TABLE {table}", migration_source)
        self.assertIn("FORCE ROW LEVEL SECURITY", migrations[0].sql)
        self.assertIn("current_setting(''hormuz.tenant_id'', true)", migrations[0].sql)

    def test_migration_ledger_requires_exact_prefix_and_checksum(self) -> None:
        migration = load_postgres_migrations()[0]
        _verify_migration_prefix(
            {migration.version: (migration.name, migration.sha256)},
            (migration,),
        )

        for applied, code in (
            ({2: (migration.name, migration.sha256)}, "migration_ledger_gap"),
            ({1: (migration.name, "0" * 64)}, "migration_checksum_mismatch"),
            (
                {
                    1: (migration.name, migration.sha256),
                    2: ("future", "f" * 64),
                },
                "schema_newer_than_binary",
            ),
        ):
            with self.subTest(code=code), self.assertRaisesRegex(
                PostgresStorageError,
                code,
            ):
                _verify_migration_prefix(applied, (migration,))

    def test_dsn_is_read_only_from_a_bounded_environment_name(self) -> None:
        dsn = "postgresql://migration-user:secret@db.example/hormuz"
        self.assertEqual(
            postgres_dsn_from_env({"SAFE_POSTGRES_DSN": dsn}, dsn_env="SAFE_POSTGRES_DSN"),
            dsn,
        )
        for environment, name, code in (
            ({}, "SAFE_POSTGRES_DSN", "postgres_dsn_unavailable"),
            (
                {"SAFE_POSTGRES_DSN": "value\nother"},
                "SAFE_POSTGRES_DSN",
                "postgres_dsn_unavailable",
            ),
            ({"unsafe-name": dsn}, "unsafe-name", "invalid_dsn_environment"),
        ):
            with self.subTest(code=code), self.assertRaisesRegex(
                PostgresStorageError,
                code,
            ):
                postgres_dsn_from_env(environment, dsn_env=name)

    def test_identifiers_and_tenant_ids_are_restricted_before_sql(self) -> None:
        self.assertEqual(validate_postgres_identifier("hormuz_runtime", "role"), "hormuz_runtime")
        self.assertEqual(validate_tenant_id("tenant-acme:prod"), "tenant-acme:prod")
        for value in ('hormuz";drop schema public;--', "MixedCase", "", "a" * 64):
            with self.subTest(value=value), self.assertRaises(PostgresStorageError):
                validate_postgres_identifier(value, "role")
        for value in (" tenant", "tenant/value", "", "x" * 129):
            with self.subTest(value=value), self.assertRaises(PostgresStorageError):
                validate_tenant_id(value)
        for values in (
            ("tenant-a", "", "codex", 1),
            ("tenant-a", "principal-a", "bad\nclient", 1),
            ("tenant-a", "principal-a", "codex", 0),
            ("tenant-a", "principal-a", "codex", True),
        ):
            with self.subTest(values=values), self.assertRaises(PostgresStorageError):
                TenantContext(*values)  # type: ignore[arg-type]

    def test_tenant_transaction_uses_transaction_local_bound_context(self) -> None:
        events: list[object] = []

        class Cursor:
            def __init__(self) -> None:
                self.fetchone_calls = 0

            def execute(self, query: str, params: object | None = None) -> None:
                events.append((query, params))

            def fetchone(self) -> object:
                self.fetchone_calls += 1
                if self.fetchone_calls == 1:
                    return (
                        "tenant-a",
                        "principal-a",
                        "codex",
                        "7",
                        "hormuz_runtime",
                        False,
                        False,
                    )
                return ("active",)

            def __enter__(self) -> "Cursor":
                events.append("cursor-enter")
                return self

            def __exit__(self, *args: object) -> None:
                events.append("cursor-exit")

        class Connection:
            @contextmanager
            def transaction(self):
                events.append("transaction-enter")
                yield
                events.append("transaction-exit")

            def cursor(self) -> Cursor:
                return Cursor()

        connection = Connection()
        context = TenantContext("tenant-a", "principal-a", "codex", 7)
        with tenant_transaction(connection, context) as yielded:  # type: ignore[arg-type]
            self.assertIs(yielded, connection)
            events.append("application-query")

        self.assertEqual(events[0], "transaction-enter")
        self.assertIn(
            (
                "SELECT "
                "set_config('hormuz.tenant_id', %s, true), "
                "set_config('hormuz.principal_id', %s, true), "
                "set_config('hormuz.client_id', %s, true), "
                "set_config('hormuz.authorization_version', %s, true), "
                "current_user, rolsuper, rolbypassrls "
                "FROM pg_roles WHERE rolname = current_user",
                ("tenant-a", "principal-a", "codex", "7"),
            ),
            events,
        )
        self.assertIn(
            (
                "SELECT state FROM gateway_tenant_lifecycle "
                "WHERE tenant_id = %s",
                ("tenant-a",),
            ),
            events,
        )
        self.assertIn(
            (
                "SELECT pg_advisory_xact_lock_shared(hashtextextended(%s, 0))",
                ("hormuz:tenant-lifecycle:hormuz:tenant-a",),
            ),
            events,
        )
        self.assertEqual(events[-1], "transaction-exit")

    def test_tenant_transaction_maps_integrity_denials_without_details(self) -> None:
        class DatabaseError(RuntimeError):
            def __init__(self, sqlstate: str):
                super().__init__("sensitive database detail")
                self.sqlstate = sqlstate

        class Cursor:
            def __init__(self) -> None:
                self.fetchone_calls = 0

            def execute(self, _query: str, _params: object | None = None) -> None:
                pass

            def fetchone(self) -> object:
                self.fetchone_calls += 1
                if self.fetchone_calls == 1:
                    return (
                        "tenant-a",
                        "principal-a",
                        "codex",
                        "1",
                        "hormuz_runtime",
                        False,
                        False,
                    )
                return ("active",)

            def __enter__(self) -> "Cursor":
                return self

            def __exit__(self, *args: object) -> None:
                pass

        class Connection:
            def __init__(self, sqlstate: str):
                self.sqlstate = sqlstate

            @contextmanager
            def transaction(self):
                yield
                raise DatabaseError(self.sqlstate)

            def cursor(self) -> Cursor:
                return Cursor()

        for sqlstate, code in (
            ("42501", "tenant_policy_denied"),
            ("23503", "tenant_foreign_key_denied"),
            ("23505", "tenant_uniqueness_denied"),
            ("23514", "tenant_immutability_denied"),
            ("08006", "tenant_transaction_failed"),
        ):
            with self.subTest(sqlstate=sqlstate), self.assertRaisesRegex(
                PostgresStorageError,
                code,
            ) as raised:
                with tenant_transaction(
                    Connection(sqlstate),  # type: ignore[arg-type]
                    TenantContext("tenant-a", "principal-a", "codex", 1),
                ):
                    pass
            self.assertNotIn("sensitive database detail", str(raised.exception))

    def test_tenant_transaction_rejects_owner_or_privileged_connection(self) -> None:
        class Cursor:
            def execute(self, _query: str, _params: object | None = None) -> None:
                pass

            def fetchone(self) -> object:
                return (
                    "tenant-a",
                    "principal-a",
                    "codex",
                    "1",
                    "hormuz_owner",
                    False,
                    False,
                )

            def __enter__(self) -> "Cursor":
                return self

            def __exit__(self, *args: object) -> None:
                pass

        class Connection:
            @contextmanager
            def transaction(self):
                yield

            def cursor(self) -> Cursor:
                return Cursor()

        with self.assertRaisesRegex(
            PostgresStorageError,
            "runtime_connection_role_invalid",
        ):
            with tenant_transaction(  # type: ignore[arg-type]
                Connection(),
                TenantContext("tenant-a", "principal-a", "codex", 1),
            ):
                self.fail("owner connection must not reach application work")

    def test_storage_cli_does_not_require_gateway_config_or_print_dsn(self) -> None:
        args = build_parser().parse_args(["storage", "migrate"])
        self.assertEqual(args.dsn_env, "HORMUZ_POSTGRES_DSN")
        status = PostgresFoundationStatus(
            schema="hormuz",
            runtime_role="hormuz_runtime",
            target_version=1,
            applied_versions=(1,),
            verified=True,
        )
        output = io.StringIO()
        secret_dsn = "postgresql://owner:top-secret@localhost/hormuz"
        with (
            mock.patch.dict("os.environ", {"HORMUZ_POSTGRES_DSN": secret_dsn}),
            mock.patch("hormuz.cli.migrate_postgres_from_env", return_value=status) as migrate,
            redirect_stdout(output),
        ):
            self.assertEqual(main(["storage", "migrate"]), 0)

        migrate.assert_called_once_with(
            dsn_env="HORMUZ_POSTGRES_DSN",
            schema="hormuz",
            runtime_role="hormuz_runtime",
        )
        self.assertEqual(json.loads(output.getvalue()), status.to_dict())
        self.assertNotIn(secret_dsn, output.getvalue())

    def test_storage_cli_returns_content_free_error_code(self) -> None:
        output = io.StringIO()
        with (
            mock.patch(
                "hormuz.cli.verify_postgres_from_env",
                side_effect=PostgresStorageError("postgres_connection_failed"),
            ),
            redirect_stderr(output),
        ):
            self.assertEqual(main(["storage", "verify"]), 2)
        self.assertEqual(
            output.getvalue().strip(),
            "PostgreSQL storage error: postgres_connection_failed",
        )

    def test_identity_sync_cli_uses_owner_dsn_without_printing_it(self) -> None:
        config = SimpleNamespace(
            usage_storage=SimpleNamespace(
                postgres_dsn_env="HORMUZ_OWNER_DSN",
                postgres_schema="hormuz",
            )
        )
        result = IdentitySyncResult(
            organizations=2,
            changed_organizations=1,
            changed_principals=1,
            revoked_sessions=3,
        )
        secret_dsn = "postgresql://owner:secret-value@localhost/hormuz"
        output = io.StringIO()
        with (
            mock.patch("hormuz.cli.GatewayConfig.load", return_value=config),
            mock.patch("hormuz.cli.postgres_dsn_from_env", return_value=secret_dsn),
            mock.patch("hormuz.cli.sync_identity_projection", return_value=result) as sync,
            redirect_stdout(output),
        ):
            self.assertEqual(main(["identities", "sync"]), 0)
        sync.assert_called_once_with(config, secret_dsn, schema="hormuz")
        self.assertEqual(json.loads(output.getvalue()), result.to_dict())
        self.assertNotIn(secret_dsn, output.getvalue())

    def test_policy_sync_cli_uses_owner_dsn_without_printing_it(self) -> None:
        config = SimpleNamespace(
            usage_storage=SimpleNamespace(
                postgres_dsn_env="HORMUZ_OWNER_DSN",
                postgres_schema="hormuz",
            )
        )
        result = PolicySyncResult(organizations=2, changed_organizations=1)
        secret_dsn = "postgresql://owner:secret-value@localhost/hormuz"
        output = io.StringIO()
        with (
            mock.patch("hormuz.cli.GatewayConfig.load", return_value=config),
            mock.patch("hormuz.cli.postgres_dsn_from_env", return_value=secret_dsn),
            mock.patch("hormuz.cli.sync_policy_projection", return_value=result) as sync,
            redirect_stdout(output),
        ):
            self.assertEqual(main(["policies", "sync"]), 0)
        sync.assert_called_once_with(config, secret_dsn, schema="hormuz")
        self.assertEqual(json.loads(output.getvalue()), result.to_dict())
        self.assertNotIn(secret_dsn, output.getvalue())

    def test_checked_in_integration_evidence_is_content_free_and_bounded(self) -> None:
        value = json.loads(
            (ROOT / "evidence/postgres-foundation-integration-2026-08-20.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(value["schema"], EVIDENCE_SCHEMA)
        self.assertTrue(value["content_free"])
        self.assertEqual(value["migration"]["target_version"], POSTGRES_SCHEMA_VERSION)
        self.assertTrue(value["migration"]["idempotent"])
        self.assertTrue(value["accounting"]["tenant_scoped_usage_verified"])
        self.assertEqual(value["accounting"]["atomic_budget_competitors"], 2)
        self.assertEqual(value["accounting"]["atomic_budget_allowed"], 1)
        self.assertEqual(value["accounting"]["atomic_budget_denied"], 1)
        self.assertTrue(value["accounting"]["provider_cost_idempotency_verified"])
        self.assertTrue(value["accounting"]["provider_reconciliation_verified"])
        self.assertTrue(value["accounting"]["usage_reporting_verified"])
        self.assertTrue(value["accounting"]["usage_coverage_summary_verified"])
        self.assertTrue(value["accounting"]["usage_read_audit_verified"])
        self.assertTrue(value["accounting"]["audit_read_authorization_verified"])
        self.assertTrue(value["accounting"]["audit_reader_tenant_scope_verified"])
        self.assertTrue(value["identity_sessions"]["configuration_projection_verified"])
        self.assertTrue(value["identity_sessions"]["cross_instance_enrollment_verified"])
        self.assertEqual(value["identity_sessions"]["atomic_refresh_competitors"], 2)
        self.assertEqual(value["identity_sessions"]["atomic_refresh_rotated"], 1)
        self.assertEqual(value["identity_sessions"]["atomic_refresh_replay_denied"], 1)
        self.assertTrue(value["identity_sessions"]["refresh_replay_family_revoked"])
        self.assertTrue(value["identity_sessions"]["identity_change_revocation_verified"])
        repository_conformance = value["repository_conformance"]
        self.assertTrue(repository_conformance["sqlite_postgresql_semantic_parity"])
        self.assertTrue(repository_conformance["usage_security_contract"])
        self.assertTrue(repository_conformance["session_contract"])
        self.assertTrue(repository_conformance["directory_contract"])
        self.assertTrue(repository_conformance["tenant_scoped_negative_reads"])
        self.assertEqual(
            repository_conformance["postgresql_only_contracts"],
            ["policy_administration", "tenant_lifecycle"],
        )
        self.assertEqual(
            repository_conformance["excluded_contracts"],
            ["deprecated_builtin_context"],
        )
        self.assertTrue(value["shared_directory"]["shared_scim_crud_verified"])
        self.assertTrue(value["shared_directory"]["generic_oidc_subject_resolution_verified"])
        self.assertTrue(value["shared_directory"]["keyed_global_route_lookup_verified"])
        self.assertTrue(value["shared_directory"]["raw_global_route_table_denied"])
        self.assertTrue(value["shared_directory"]["cross_tenant_subject_collision_denied"])
        self.assertTrue(value["shared_directory"]["directory_session_projection_verified"])
        self.assertTrue(value["shared_directory"]["directory_unassignment_revokes_session"])
        self.assertTrue(value["shared_directory"]["policy_owned_group_authorization_verified"])
        self.assertTrue(value["shared_directory"]["active_policy_binding_resolution_verified"])
        self.assertTrue(value["shared_directory"]["active_policy_change_revokes_session"])
        self.assertTrue(value["shared_directory"]["active_policy_change_reenrollment_verified"])
        self.assertTrue(value["shared_directory"]["unbound_scim_group_default_denied"])
        self.assertTrue(value["shared_directory"]["identity_admin_direct_workload_denied"])
        policy_administration = value["policy_administration"]
        self.assertTrue(policy_administration["configuration_projection_verified"])
        self.assertTrue(policy_administration["idempotent_sync_verified"])
        self.assertTrue(policy_administration["stale_projection_rejected"])
        self.assertTrue(policy_administration["cross_instance_request_verified"])
        self.assertTrue(policy_administration["cross_tenant_request_hidden"])
        self.assertTrue(policy_administration["self_approval_denied"])
        self.assertEqual(policy_administration["atomic_retry_competitors"], 2)
        self.assertEqual(policy_administration["atomic_retry_consumed"], 1)
        self.assertEqual(policy_administration["atomic_retry_blocked_pending"], 1)
        self.assertTrue(
            policy_administration["exact_payload_model_policy_binding_verified"]
        )
        self.assertTrue(policy_administration["model_mismatch_audited"])
        self.assertTrue(policy_administration["security_events_shared"])
        self.assertTrue(
            policy_administration["immutable_policy_versions_verified"]
        )
        self.assertTrue(policy_administration["policy_stage_idempotent"])
        self.assertTrue(policy_administration["atomic_activation_verified"])
        self.assertTrue(
            policy_administration["cross_instance_active_version_verified"]
        )
        self.assertTrue(policy_administration["rollback_verified"])
        self.assertTrue(policy_administration["policy_admin_capability_verified"])
        self.assertTrue(
            policy_administration["policy_version_cross_tenant_hidden"]
        )
        self.assertEqual(
            policy_administration["active_policy_activation_sequence"],
            4,
        )
        self.assertTrue(policy_administration["provider_cache_catalog_v5_verified"])
        self.assertTrue(
            policy_administration["provider_cache_catalog_cross_tenant_hidden"]
        )
        self.assertEqual(value["isolation"]["missing_context_rows"], 0)
        self.assertEqual(value["isolation"]["cleared_context_rows"], 0)
        self.assertTrue(value["isolation"]["tenant_context_fields_bound"])
        self.assertTrue(value["tamper_detection"]["permissive_policy_rejected"])
        self.assertTrue(value["tamper_detection"]["runtime_owner_membership_rejected"])
        self.assertTrue(
            value["tamper_detection"]["unexpected_accounting_column_rejected"]
        )
        self.assertTrue(
            value["tamper_detection"]["unexpected_security_column_rejected"]
        )
        lifecycle = value["tenant_lifecycle"]
        self.assertTrue(lifecycle["runtime_gate_active_before_deactivation"])
        self.assertTrue(lifecycle["deactivation_blocks_runtime"])
        self.assertTrue(lifecycle["active_human_session_revoked"])
        self.assertTrue(lifecycle["encrypted_export_verified"])
        self.assertTrue(lifecycle["restore_plan_content_free"])
        self.assertEqual(lifecycle["private_export_mode"], "0600")
        self.assertTrue(lifecycle["purge_retention_enforced"])
        self.assertTrue(lifecycle["hard_purge_verified"])
        self.assertTrue(lifecycle["owner_only_tombstone_retained"])
        self.assertTrue(lifecycle["tombstone_blocks_implicit_reonboarding"])
        serialized = json.dumps(value, sort_keys=True)
        for forbidden in (
            "password",
            "postgresql://",
            "Synthetic A",
            "workspace-a",
            "tenant-a",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_postgres_dependency_and_blocking_workflows_are_locked(self) -> None:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(
            project["project"]["optional-dependencies"]["postgres"],
            ["psycopg[binary]>=3.3.4,<3.4"],
        )

        lock = (ROOT / "deploy/postgres/requirements.lock").read_text(encoding="utf-8")
        self.assertIn("psycopg==3.3.4", lock)
        self.assertIn("psycopg-binary==3.3.4", lock)
        self.assertIn("--hash=sha256:", lock)

        image = (
            "postgres@sha256:"
            "57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777"
        )
        for workflow_path in (
            ROOT / ".github/workflows/ci.yml",
            ROOT / ".github/workflows/release.yml",
        ):
            workflow = workflow_path.read_text(encoding="utf-8")
            with self.subTest(workflow=workflow_path.name):
                self.assertIn("deploy/postgres/requirements.lock", workflow)
                self.assertIn("scripts/postgres_foundation_integration.py", workflow)
                self.assertIn(image, workflow)

    def test_integration_evidence_is_private_and_refuses_overwrite(self) -> None:
        value = {
            "schema": EVIDENCE_SCHEMA,
            "content_free": True,
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "evidence.json"
            write_evidence(value, path, force=False)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaisesRegex(PostgresFoundationIntegrationError, "output_exists"):
                write_evidence(value, path, force=False)


if __name__ == "__main__":
    unittest.main()
