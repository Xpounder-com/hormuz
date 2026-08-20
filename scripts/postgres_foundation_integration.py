#!/usr/bin/env python3
"""Exercise Hormuz migrations and tenant isolation against real PostgreSQL."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import time
from typing import Any
from urllib.parse import quote

from hormuz.postgres import (
    POSTGRES_SCHEMA_VERSION,
    PostgresStorageError,
    TenantContext,
    migrate_postgres,
    tenant_transaction,
    verify_postgres,
)


EVIDENCE_SCHEMA = "hormuz.postgres-foundation-integration.v1"
DEFAULT_IMAGE = (
    "postgres@sha256:"
    "57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777"
)
IMAGE_REFERENCE = re.compile(r"postgres@sha256:[0-9a-f]{64}\Z")
MAX_COMMAND_OUTPUT_BYTES = 1024 * 1024
FINAL_STARTUP_MARKER = "PostgreSQL init process complete; ready for start up."
PORT_OUTPUT = re.compile(r"127\.0\.0\.1:([0-9]{1,5})\Z")


class PostgresFoundationIntegrationError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _execute(command: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        raise PostgresFoundationIntegrationError("docker_unavailable") from None
    for value in (completed.stdout, completed.stderr):
        if len(value.encode("utf-8")) > MAX_COMMAND_OUTPUT_BYTES:
            raise PostgresFoundationIntegrationError("docker_output_invalid")
    return completed


def _runtime_dsn(port: int, role: str, password: str) -> str:
    return (
        f"postgresql://{quote(role, safe='')}:{quote(password, safe='')}"
        f"@127.0.0.1:{port}/postgres"
    )


def _require_driver() -> Any:
    try:
        import psycopg
        from psycopg import sql
    except ImportError:
        raise PostgresFoundationIntegrationError("postgres_driver_unavailable") from None
    return psycopg, sql


def _wait_for_postgres(container: str, admin_dsn: str, psycopg: Any) -> str:
    for _attempt in range(120):
        logs = _execute(["docker", "logs", container], timeout=5)
        if FINAL_STARTUP_MARKER in (logs.stdout + logs.stderr):
            try:
                with psycopg.connect(admin_dsn, connect_timeout=2) as connection:
                    with connection.cursor() as cursor:
                        cursor.execute("SHOW server_version_num")
                        row = cursor.fetchone()
                        if row is not None and re.fullmatch(r"[0-9]{5,6}", str(row[0])):
                            return str(row[0])
            except Exception:
                pass
        time.sleep(0.25)
    raise PostgresFoundationIntegrationError("postgres_not_ready")


def _create_roles(admin_dsn: str, owner_password: str, runtime_password: str) -> None:
    psycopg, sql = _require_driver()
    try:
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB "
                        "NOCREATEROLE NOINHERIT NOBYPASSRLS"
                    ).format(
                        sql.Identifier("hormuz_owner"),
                        sql.Literal(owner_password),
                    )
                )
                cursor.execute(
                    sql.SQL(
                        "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB "
                        "NOCREATEROLE NOINHERIT NOBYPASSRLS"
                    ).format(
                        sql.Identifier("hormuz_runtime"),
                        sql.Literal(runtime_password),
                    )
                )
                cursor.execute("GRANT CREATE ON DATABASE postgres TO hormuz_owner")
    except Exception:
        raise PostgresFoundationIntegrationError("role_setup_failed") from None


@contextmanager
def _owner_tenant_transaction(connection: Any, tenant_id: str) -> Any:
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('hormuz.tenant_id', %s, true), "
                "set_config('hormuz.principal_id', 'integration-provisioner', true), "
                "set_config('hormuz.client_id', 'postgres-foundation-integration', true), "
                "set_config('hormuz.authorization_version', '1', true)",
                (tenant_id,),
            )
            if cursor.fetchone() != (
                tenant_id,
                "integration-provisioner",
                "postgres-foundation-integration",
                "1",
            ):
                raise PostgresFoundationIntegrationError("owner_tenant_context_not_bound")
        yield connection


def _provision_synthetic_tenants(owner_dsn: str) -> None:
    psycopg, _sql = _require_driver()
    try:
        with psycopg.connect(owner_dsn) as connection:
            for suffix in ("a", "b"):
                with _owner_tenant_transaction(connection, f"tenant-{suffix}"):
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "INSERT INTO hormuz.tenants (tenant_id, display_name) VALUES (%s, %s)",
                            (f"tenant-{suffix}", f"Synthetic {suffix.upper()}"),
                        )
                        cursor.execute(
                            "INSERT INTO hormuz.workspaces "
                            "(tenant_id, workspace_id, display_name) VALUES (%s, %s, %s)",
                            (
                                f"tenant-{suffix}",
                                f"workspace-{suffix}",
                                f"Workspace {suffix.upper()}",
                            ),
                        )
    except PostgresStorageError as error:
        raise PostgresFoundationIntegrationError(error.code) from None
    except Exception:
        raise PostgresFoundationIntegrationError("tenant_provisioning_failed") from None


def _expect_transaction_denied(
    connection: Any,
    operation: Any,
    *,
    expected_code: str,
    failure_code: str,
) -> None:
    try:
        with tenant_transaction(
            connection,
            TenantContext(
                tenant_id="tenant-a",
                principal_id="runtime-integration",
                client_id="postgres-foundation-integration",
                authorization_version=1,
            ),
        ):
            with connection.cursor() as cursor:
                operation(cursor)
    except PostgresStorageError as error:
        if error.code == expected_code:
            return
        raise PostgresFoundationIntegrationError(failure_code) from None
    raise PostgresFoundationIntegrationError(failure_code)


def _expect_owner_immutability_denied(connection: Any) -> None:
    try:
        with _owner_tenant_transaction(connection, "tenant-a"):
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE hormuz.workspaces SET tenant_id = 'tenant-b' "
                    "WHERE tenant_id = 'tenant-a' AND workspace_id = 'workspace-a'"
                )
    except PostgresFoundationIntegrationError:
        raise
    except Exception as error:
        if getattr(error, "sqlstate", None) == "23514":
            return
    raise PostgresFoundationIntegrationError("tenant_id_update_not_denied")


def _prove_runtime_isolation(runtime_dsn: str, owner_dsn: str) -> dict[str, object]:
    psycopg, _sql = _require_driver()
    try:
        with psycopg.connect(runtime_dsn) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute("SELECT count(*) FROM hormuz.workspaces")
                    missing_context_rows = int(cursor.fetchone()[0])

            with tenant_transaction(
                connection,
                TenantContext("tenant-a", "runtime-integration", "integration", 1),
            ):
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT current_setting('hormuz.principal_id'), "
                        "current_setting('hormuz.client_id'), "
                        "current_setting('hormuz.authorization_version')"
                    )
                    tenant_context_fields_bound = cursor.fetchone() == (
                        "runtime-integration",
                        "integration",
                        "1",
                    )
                    cursor.execute("SELECT count(*) FROM hormuz.workspaces")
                    tenant_a_rows = int(cursor.fetchone()[0])
                    cursor.execute(
                        "SELECT count(*) FROM hormuz.workspaces WHERE tenant_id = 'tenant-b'"
                    )
                    cross_tenant_rows = int(cursor.fetchone()[0])

            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute("SELECT count(*) FROM hormuz.workspaces")
                    cleared_context_rows = int(cursor.fetchone()[0])

            with tenant_transaction(
                connection,
                TenantContext("tenant-b", "runtime-integration", "integration", 1),
            ):
                with connection.cursor() as cursor:
                    cursor.execute("SELECT count(*) FROM hormuz.workspaces")
                    tenant_b_rows = int(cursor.fetchone()[0])

            _expect_transaction_denied(
                connection,
                lambda cursor: cursor.execute(
                    "INSERT INTO hormuz.workspaces "
                    "(tenant_id, workspace_id, display_name) VALUES "
                    "('tenant-b', 'forbidden', 'Forbidden')"
                ),
                expected_code="tenant_policy_denied",
                failure_code="cross_tenant_write_not_denied",
            )
            _expect_transaction_denied(
                connection,
                lambda cursor: cursor.execute(
                    "INSERT INTO hormuz.projects "
                    "(tenant_id, workspace_id, project_id) VALUES "
                    "('tenant-a', 'workspace-b', 'bad-fk')"
                ),
                expected_code="tenant_foreign_key_denied",
                failure_code="cross_tenant_foreign_key_not_denied",
            )

        with psycopg.connect(owner_dsn) as connection:
            _expect_owner_immutability_denied(connection)
    except PostgresFoundationIntegrationError:
        raise
    except Exception:
        raise PostgresFoundationIntegrationError("runtime_isolation_failed") from None

    if (
        missing_context_rows != 0
        or cleared_context_rows != 0
        or tenant_a_rows != 1
        or tenant_b_rows != 1
        or cross_tenant_rows != 0
        or not tenant_context_fields_bound
    ):
        raise PostgresFoundationIntegrationError("tenant_visibility_mismatch")
    return {
        "missing_context_rows": missing_context_rows,
        "cleared_context_rows": cleared_context_rows,
        "tenant_a_visible_rows": tenant_a_rows,
        "tenant_b_visible_rows": tenant_b_rows,
        "cross_tenant_visible_rows": cross_tenant_rows,
        "tenant_context_fields_bound": tenant_context_fields_bound,
        "cross_tenant_write_denied": True,
        "cross_tenant_foreign_key_denied": True,
        "tenant_id_update_denied": True,
    }


def _expect_verification_code(owner_dsn: str, expected_code: str) -> None:
    try:
        verify_postgres(owner_dsn)
    except PostgresStorageError as error:
        if error.code == expected_code:
            return
    raise PostgresFoundationIntegrationError("verification_tamper_not_detected")


def _prove_verifier_tamper_detection(admin_dsn: str, owner_dsn: str) -> dict[str, bool]:
    psycopg, _sql = _require_driver()
    try:
        with psycopg.connect(owner_dsn, autocommit=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute("DROP POLICY tenant_isolation ON hormuz.workspaces")
                cursor.execute(
                    "CREATE POLICY tenant_isolation ON hormuz.workspaces "
                    "USING (true) WITH CHECK (true)"
                )
        _expect_verification_code(owner_dsn, "tenant_policy_definition_invalid")
        with psycopg.connect(owner_dsn, autocommit=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute("DROP POLICY tenant_isolation ON hormuz.workspaces")
                cursor.execute(
                    "CREATE POLICY tenant_isolation ON hormuz.workspaces "
                    "USING (tenant_id = NULLIF(current_setting('hormuz.tenant_id', true), '')) "
                    "WITH CHECK "
                    "(tenant_id = NULLIF(current_setting('hormuz.tenant_id', true), ''))"
                )

        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute("GRANT hormuz_owner TO hormuz_runtime")
        _expect_verification_code(owner_dsn, "runtime_role_has_memberships")
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute("REVOKE hormuz_owner FROM hormuz_runtime")
        verify_postgres(owner_dsn)
    except PostgresFoundationIntegrationError:
        raise
    except Exception:
        raise PostgresFoundationIntegrationError("verification_tamper_probe_failed") from None
    return {
        "permissive_policy_rejected": True,
        "runtime_owner_membership_rejected": True,
    }


def run_integration(*, image: str = DEFAULT_IMAGE) -> dict[str, object]:
    if IMAGE_REFERENCE.fullmatch(image) is None:
        raise PostgresFoundationIntegrationError("invalid_postgres_image")
    psycopg, _sql = _require_driver()
    nonce = secrets.token_hex(8)
    container = "hormuz-postgres-foundation-" + nonce
    admin_password = secrets.token_urlsafe(32)
    owner_password = secrets.token_urlsafe(32)
    runtime_password = secrets.token_urlsafe(32)
    launched = False
    primary_error: PostgresFoundationIntegrationError | None = None
    cleanup_error: PostgresFoundationIntegrationError | None = None
    evidence: dict[str, object] | None = None
    runtime_image = image
    try:
        inspected = _execute(
            ["docker", "image", "inspect", image, "--format", "{{json .RepoDigests}}"],
            timeout=15,
        )
        if inspected.returncode != 0:
            runtime_image = image.split("@", maxsplit=1)[1]
            inspected = _execute(
                [
                    "docker",
                    "image",
                    "inspect",
                    runtime_image,
                    "--format",
                    "{{json .RepoDigests}}",
                ],
                timeout=15,
            )
        try:
            digests = json.loads(inspected.stdout)
        except (json.JSONDecodeError, RecursionError):
            raise PostgresFoundationIntegrationError("postgres_image_unavailable") from None
        if inspected.returncode != 0 or not isinstance(digests, list) or image not in digests:
            raise PostgresFoundationIntegrationError("postgres_image_unavailable")

        result = _execute(
            [
                "docker",
                "run",
                "--rm",
                "--detach",
                "--pull",
                "never",
                "--publish",
                "127.0.0.1::5432",
                "--name",
                container,
                "--env",
                "POSTGRES_PASSWORD=" + admin_password,
                runtime_image,
            ],
            timeout=30,
        )
        if result.returncode != 0:
            raise PostgresFoundationIntegrationError("container_start_failed")
        launched = True
        port_result = _execute(["docker", "port", container, "5432/tcp"], timeout=10)
        match = PORT_OUTPUT.fullmatch(port_result.stdout.strip())
        if port_result.returncode != 0 or match is None:
            raise PostgresFoundationIntegrationError("container_port_unavailable")
        port = int(match.group(1))
        if not 1 <= port <= 65535:
            raise PostgresFoundationIntegrationError("container_port_unavailable")

        admin_dsn = _runtime_dsn(port, "postgres", admin_password)
        postgres_version = _wait_for_postgres(container, admin_dsn, psycopg)
        _create_roles(admin_dsn, owner_password, runtime_password)
        owner_dsn = _runtime_dsn(port, "hormuz_owner", owner_password)
        runtime_dsn = _runtime_dsn(port, "hormuz_runtime", runtime_password)

        first = migrate_postgres(owner_dsn)
        second = migrate_postgres(owner_dsn)
        verified = verify_postgres(owner_dsn)
        if first != second or second != verified:
            raise PostgresFoundationIntegrationError("migration_idempotency_failed")
        _provision_synthetic_tenants(owner_dsn)
        isolation = _prove_runtime_isolation(runtime_dsn, owner_dsn)
        tamper_detection = _prove_verifier_tamper_detection(admin_dsn, owner_dsn)
        evidence = {
            "schema": EVIDENCE_SCHEMA,
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "runner": {
                "postgres_image": image,
                "postgres_server_version_num": postgres_version,
            },
            "migration": {
                "target_version": POSTGRES_SCHEMA_VERSION,
                "applied_versions": list(verified.applied_versions),
                "idempotent": True,
                "checksums_verified": True,
                "runtime_role_verified": True,
                "forced_rls_verified": True,
                "privileges_verified": True,
                "policy_definitions_verified": True,
                "trigger_definition_verified": True,
            },
            "isolation": isolation,
            "tamper_detection": tamper_detection,
            "content_free": True,
        }
    except PostgresStorageError as error:
        primary_error = PostgresFoundationIntegrationError(error.code)
    except PostgresFoundationIntegrationError as error:
        primary_error = error
    finally:
        if launched:
            removed = _execute(["docker", "rm", "--force", container], timeout=20)
            if removed.returncode != 0:
                cleanup_error = PostgresFoundationIntegrationError("container_cleanup_failed")
    if primary_error is not None:
        raise primary_error
    if cleanup_error is not None:
        raise cleanup_error
    if evidence is None:
        raise PostgresFoundationIntegrationError("integration_evidence_missing")
    return evidence


def write_evidence(value: dict[str, object], output: Path, *, force: bool) -> None:
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and not force:
        raise PostgresFoundationIntegrationError("output_exists")
    flags = os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if force else os.O_EXCL)
    try:
        descriptor = os.open(output, flags, 0o600)
    except OSError:
        raise PostgresFoundationIntegrationError("output_unavailable") from None
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    try:
        evidence = run_integration(image=args.image)
        if args.output is not None:
            write_evidence(evidence, args.output, force=args.force)
        else:
            print(json.dumps(evidence, sort_keys=True, separators=(",", ":")))
    except PostgresFoundationIntegrationError as error:
        print(f"PostgreSQL foundation integration error: {error.code}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
