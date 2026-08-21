from __future__ import annotations

from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import base64
import io
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

from hormuz.cli import main
from hormuz.postgres import POSTGRES_SCHEMA_VERSION, TENANT_TABLES, PostgresStorageError
from hormuz.tenant_lifecycle import (
    TENANT_EXPORT_SCHEMA,
    TenantLifecycleError,
    TenantLifecycleRuntimeGate,
    TenantLifecycleService,
    TenantLifecycleStatus,
    _encrypt_snapshot,
    _private_create,
    export_key_from_env,
)


class TenantLifecycleTests(unittest.TestCase):
    def test_export_key_requires_a_named_exact_256_bit_environment_value(self) -> None:
        key = b"k" * 32
        encoded = base64.urlsafe_b64encode(key).decode("ascii").rstrip("=")
        self.assertEqual(
            export_key_from_env("HORMUZ_TENANT_EXPORT_KEY", environ={"HORMUZ_TENANT_EXPORT_KEY": encoded}),
            key,
        )
        for environment, name, code in (
            ({}, "HORMUZ_TENANT_EXPORT_KEY", "tenant_export_key_unavailable"),
            ({"HORMUZ_TENANT_EXPORT_KEY": "invalid"}, "HORMUZ_TENANT_EXPORT_KEY", "tenant_export_key_unavailable"),
            ({"unsafe-name": encoded}, "unsafe-name", "invalid_export_key_environment"),
        ):
            with self.subTest(code=code), self.assertRaisesRegex(TenantLifecycleError, code):
                export_key_from_env(name, environ=environment)

    def test_restore_plan_verifies_encryption_integrity_and_never_prints_rows(self) -> None:
        key = b"z" * 32
        snapshot = {
            "schema": TENANT_EXPORT_SCHEMA,
            "organization_id": "tenant-a",
            "exported_at": "2026-08-20T00:00:00Z",
            "migration_version": POSTGRES_SCHEMA_VERSION,
            "tables": {table: [] for table in TENANT_TABLES},
        }
        snapshot["tables"]["gateway_usage_events"] = [
            {"provider_usage_json": {"not_retained_by_plan": "sensitive-value"}}
        ]
        envelope, expected_payload_sha256, _ciphertext_sha256 = _encrypt_snapshot(snapshot, key)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "tenant-a.hormuz"
            _private_create(path, envelope)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            plan = TenantLifecycleService.restore_plan(input_path=path, encryption_key=key)
            self.assertEqual(plan.organization_id, "tenant-a")
            self.assertEqual(plan.payload_sha256, expected_payload_sha256)
            self.assertEqual(plan.table_counts["gateway_usage_events"], 1)
            output = json.dumps(plan.to_dict(), sort_keys=True)
            self.assertNotIn("sensitive-value", output)

            tampered = json.loads(path.read_text(encoding="utf-8"))
            last = tampered["ciphertext"][-1]
            tampered["ciphertext"] = tampered["ciphertext"][:-1] + (
                "A" if last != "A" else "B"
            )
            path.write_text(json.dumps(tampered), encoding="utf-8")
            with self.assertRaisesRegex(TenantLifecycleError, "tenant_export_integrity_invalid"):
                TenantLifecycleService.restore_plan(input_path=path, encryption_key=key)

    def test_runtime_gate_accepts_only_active_lifecycle_state(self) -> None:
        class Cursor:
            def __init__(self, state: str):
                self.state = state
                self.calls = 0

            def execute(self, _query: str, _params: object | None = None) -> None:
                pass

            def fetchone(self) -> object:
                self.calls += 1
                if self.calls == 1:
                    return (
                        "tenant-a",
                        "alice",
                        "hormuz-authentication",
                        "1",
                        "hormuz_runtime",
                        False,
                        False,
                    )
                return (self.state,)

            def __enter__(self) -> "Cursor":
                return self

            def __exit__(self, *args: object) -> None:
                pass

        class Connection:
            def __init__(self, state: str):
                self.state = state
                self.closed = False

            @contextmanager
            def transaction(self):
                yield

            def cursor(self) -> Cursor:
                return Cursor(self.state)

            def close(self) -> None:
                self.closed = True

        identity = type("Identity", (), {"organization_id": "tenant-a", "actor_id": "alice"})()

        active_gate = TenantLifecycleRuntimeGate(
            "postgresql://runtime",
            connect=lambda *_args, **_kwargs: Connection("active"),
        )
        active_gate.require_active(identity)  # type: ignore[arg-type]

        inactive_gate = TenantLifecycleRuntimeGate(
            "postgresql://runtime",
            connect=lambda *_args, **_kwargs: Connection("deactivated"),
        )
        with self.assertRaisesRegex(TenantLifecycleError, "tenant_inactive"):
            inactive_gate.require_active(identity)  # type: ignore[arg-type]

    def test_re_onboard_requires_an_absent_tenant_and_only_removes_the_tombstone(self) -> None:
        class Cursor:
            def __init__(self, connection: "Connection") -> None:
                self.connection = connection
                self.query = ""
                self.rowcount = 0

            def execute(self, query: str, _params: object | None = None) -> None:
                self.query = query
                if query.startswith("DELETE FROM gateway_tenant_purge_tombstones"):
                    self.rowcount = 1

            def fetchone(self) -> object:
                if "pg_get_userbyid" in self.query:
                    return ("hormuz_owner", "hormuz_owner")
                if "set_config" in self.query:
                    return ("tenant-a", "tenant-lifecycle", "hormuz-cli", "1")
                if "pg_advisory_xact_lock" in self.query:
                    return (None,)
                if "SELECT 1 FROM tenants" in self.query:
                    return None
                return None

            def __enter__(self) -> "Cursor":
                return self

            def __exit__(self, *args: object) -> None:
                pass

        class Connection:
            @contextmanager
            def transaction(self):
                yield

            def cursor(self) -> Cursor:
                return Cursor(self)

            def close(self) -> None:
                pass

        service = TenantLifecycleService(
            "postgresql://owner",
            connect=lambda *_args, **_kwargs: Connection(),
        )
        result = service.re_onboard(organization_id="tenant-a")
        self.assertTrue(result.changed)
        self.assertEqual(result.to_dict()["next_step"], "identities_sync")

    def test_owner_cli_requires_exact_confirmation_and_never_loads_gateway_config(self) -> None:
        status = TenantLifecycleStatus(
            organization_id="tenant-a",
            state="deactivated",
            state_version=2,
            deactivated_at="2026-08-20T00:00:00Z",
            purge_not_before=None,
            changed=True,
            revoked_sessions=3,
            invalidated_enrollments=1,
        )
        service = mock.Mock()
        service.deactivate.return_value = status
        output = io.StringIO()
        with (
            mock.patch("hormuz.cli.tenant_lifecycle_service_from_env", return_value=service) as factory,
            redirect_stdout(output),
        ):
            self.assertEqual(
                main(
                    [
                        "storage", "tenant", "deactivate",
                        "--organization", "tenant-a",
                        "--reason-code", "administrative",
                        "--confirm-organization", "tenant-a",
                    ]
                ),
                0,
            )
        factory.assert_called_once_with(dsn_env="HORMUZ_POSTGRES_DSN", schema="hormuz")
        service.deactivate.assert_called_once_with(
            organization_id="tenant-a",
            reason_code="administrative",
        )
        self.assertEqual(json.loads(output.getvalue()), status.to_dict())

        error = io.StringIO()
        with (
            mock.patch("hormuz.cli.tenant_lifecycle_service_from_env", return_value=service) as blocked,
            redirect_stderr(error),
        ):
            self.assertEqual(
                main(
                    [
                        "storage", "tenant", "deactivate",
                        "--organization", "tenant-a",
                        "--reason-code", "administrative",
                        "--confirm-organization", "tenant-b",
                    ]
                ),
                2,
            )
        blocked.assert_called_once()
        self.assertEqual(
            error.getvalue(),
            "PostgreSQL storage error: tenant_lifecycle_confirmation_mismatch\n",
        )

    def test_lifecycle_migration_has_a_runtime_gate_and_owner_only_tombstone(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "hormuz/migrations/postgresql/0009_tenant_lifecycle.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE gateway_tenant_lifecycle", source)
        self.assertIn("CREATE TABLE gateway_tenant_exports", source)
        self.assertIn("CREATE TABLE gateway_tenant_purge_tombstones", source)
        self.assertIn("FORCE ROW LEVEL SECURITY", source)
        self.assertIn("purge_scheduled", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
