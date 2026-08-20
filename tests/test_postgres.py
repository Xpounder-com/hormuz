from __future__ import annotations

from contextlib import contextmanager, redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest import mock

from hormuz.cli import build_parser, main
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
from hormuz.store_router import GatewayStoreRouter
from scripts.postgres_foundation_integration import (
    EVIDENCE_SCHEMA,
    PostgresFoundationIntegrationError,
    write_evidence,
)


ROOT = Path(__file__).resolve().parents[1]


class PostgresFoundationTests(unittest.TestCase):
    def test_postgres_usage_store_requires_explicit_scope_for_multiple_tenants(self) -> None:
        store = PostgresUsageStore(
            "postgresql://runtime:not-used@127.0.0.1/hormuz",
            organization_ids=("tenant-a", "tenant-b"),
        )
        with self.assertRaisesRegex(ValueError, "organization_id is required"):
            store.monthly_totals()
        with self.assertRaisesRegex(PostgresStorageError, "tenant_not_configured"):
            store.monthly_totals(organization_id="tenant-c")

    def test_split_store_routes_accounting_and_security_and_combines_audit(self) -> None:
        accounting = mock.Mock()
        security = mock.Mock()
        security.path = Path("security.sqlite3")
        accounting.record.return_value = "usage-id"
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
        router = GatewayStoreRouter(accounting, security)

        self.assertEqual(router.record(test=True), "usage-id")
        self.assertEqual(router.record_secret_event(test=True), "security-id")
        self.assertEqual(
            [event["id"] for event in router.audit_events(since="2026-08-20T00:00:00Z")],
            ["a", "b"],
        )
        accounting.record.assert_called_once_with(test=True)
        security.record_secret_event.assert_called_once_with(test=True)

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
            def execute(self, query: str, params: object | None = None) -> None:
                events.append((query, params))

            def fetchone(self) -> object:
                return (
                    "tenant-a",
                    "principal-a",
                    "codex",
                    "7",
                    "hormuz_runtime",
                    False,
                    False,
                )

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
        self.assertEqual(events[-1], "transaction-exit")

    def test_tenant_transaction_maps_integrity_denials_without_details(self) -> None:
        class DatabaseError(RuntimeError):
            def __init__(self, sqlstate: str):
                super().__init__("sensitive database detail")
                self.sqlstate = sqlstate

        class Cursor:
            def execute(self, _query: str, _params: object | None = None) -> None:
                pass

            def fetchone(self) -> object:
                return (
                    "tenant-a",
                    "principal-a",
                    "codex",
                    "1",
                    "hormuz_runtime",
                    False,
                    False,
                )

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
        self.assertTrue(value["accounting"]["usage_read_audit_verified"])
        self.assertEqual(value["isolation"]["missing_context_rows"], 0)
        self.assertEqual(value["isolation"]["cleared_context_rows"], 0)
        self.assertTrue(value["isolation"]["tenant_context_fields_bound"])
        self.assertTrue(value["tamper_detection"]["permissive_policy_rejected"])
        self.assertTrue(value["tamper_detection"]["runtime_owner_membership_rejected"])
        self.assertTrue(
            value["tamper_detection"]["unexpected_accounting_column_rejected"]
        )
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
