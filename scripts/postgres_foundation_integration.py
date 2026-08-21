#!/usr/bin/env python3
"""Exercise Hormuz migrations and tenant isolation against real PostgreSQL."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import tempfile
import time
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote

from hormuz.billing import ProviderCostItem, ProviderCostReport
from hormuz.config import (
    AuthorizationProfile,
    DLPApprovalConfig,
    DLPControls,
    DLPRuleConfig,
    GatewayConfig,
    Identity,
    ModelRoute,
    Policy,
    ProviderCacheCapability,
    ProviderCachePolicy,
    PolicyTeamBinding,
    SecretControls,
    SessionBrokerConfig,
)
from hormuz.auth import AuthenticationError, Authenticator
from hormuz.postgres import (
    POSTGRES_SCHEMA_VERSION,
    PostgresStorageError,
    TenantContext,
    migrate_postgres,
    tenant_transaction,
    verify_postgres,
)
from hormuz.postgres_usage_store import PostgresUsageStore
from hormuz.identity_projection import sync_identity_projection
from hormuz.policy_projection import (
    sync_policy_projection,
    verify_runtime_policy_projection,
)
from hormuz.postgres_policy_store import PolicyAdminError, PostgresPolicyStore
from hormuz.policy_runtime import PolicyRuntime
from hormuz.postgres_security_store import PostgresSecurityStore
from hormuz.postgres_session_store import PostgresSessionStore
from hormuz.postgres_directory import PostgresDirectoryStore
from hormuz.directory import (
    HORMUZ_GROUP_EXTENSION,
    HORMUZ_USER_EXTENSION,
    SCIM_GROUP_SCHEMA,
    SCIM_USER_SCHEMA,
    DirectoryError,
)
from hormuz.session import SessionBroker
from hormuz.session_store import SessionStoreError
from hormuz.tenant_lifecycle import (
    TenantLifecycleError,
    TenantLifecycleRuntimeGate,
    TenantLifecycleService,
)
try:  # Support both ``python scripts/...`` and package imports in unit tests.
    from scripts.postgres_repository_conformance import (
        RepositoryConformanceError,
        prove_repository_conformance,
    )
except ModuleNotFoundError:  # pragma: no cover - direct-script launcher path
    from postgres_repository_conformance import (  # type: ignore[no-redef]
        RepositoryConformanceError,
        prove_repository_conformance,
    )
from hormuz.store import (
    DLPApprovalStoreError,
    ReservationDenied,
    ReservationScope,
    SecurityStoreError,
)


EVIDENCE_SCHEMA = "hormuz.postgres-policy-administration-integration.v13"
DEFAULT_IMAGE = (
    "postgres@sha256:"
    "57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777"
)
IMAGE_REFERENCE = re.compile(r"postgres@sha256:[0-9a-f]{64}\Z")
MAX_COMMAND_OUTPUT_BYTES = 1024 * 1024
FINAL_STARTUP_MARKER = "PostgreSQL init process complete; ready for start up."
PORT_OUTPUT = re.compile(r"127\.0\.0\.1:([0-9]{1,5})\Z")
ROOT = Path(__file__).resolve().parents[1]


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
                            "INSERT INTO hormuz.gateway_tenant_lifecycle (tenant_id) VALUES (%s)",
                            (f"tenant-{suffix}",),
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


def _synthetic_identity(
    tenant: str,
    actor: str,
    *,
    capabilities: tuple[str, ...] = (),
) -> Identity:
    return Identity(
        token_env="INTEGRATION_ONLY",
        token="integration-only-not-persisted",
        actor_id=actor,
        actor_name="Synthetic Actor",
        team_id="engineering",
        team_name="Engineering",
        organization_id=tenant,
        capabilities=capabilities,
    )


def _prove_accounting_store(runtime_dsn: str) -> dict[str, object]:
    organizations = ("tenant-a", "tenant-b")
    stores = (
        PostgresUsageStore(runtime_dsn, organization_ids=organizations),
        PostgresUsageStore(runtime_dsn, organization_ids=organizations),
    )
    security_store = PostgresSecurityStore(runtime_dsn, organization_ids=organizations)
    identity_a = _synthetic_identity("tenant-a", "actor-a")
    identity_b = _synthetic_identity("tenant-b", "actor-b")
    governance_policy_version = "hpv_v1_" + "a" * 64
    try:
        for store, identity, policy_action in (
            (stores[0], identity_a, "fallback+capped"),
            (stores[1], identity_b, "allowed"),
        ):
            store.record(
                identity=identity,
                client="integration",
                protocol="openai",
                requested_model="gpt-test",
                resolved_alias="gpt-test",
                upstream_model="gpt-test",
                actual_model="gpt-test",
                policy_action=policy_action,
                status="succeeded",
                input_tokens=100,
                output_tokens=50,
                billable_tokens=150,
                cost_microusd=125_000,
                cost_basis="estimated",
                rate_card_version="integration-v1",
                governance_policy_version=governance_policy_version,
            )
    except PostgresStorageError:
        raise PostgresFoundationIntegrationError("accounting_usage_record_failed") from None
    try:
        totals_a = stores[0].monthly_totals(organization_id="tenant-a")
        totals_b = stores[1].monthly_totals(organization_id="tenant-b")
    except PostgresStorageError:
        raise PostgresFoundationIntegrationError("accounting_usage_read_failed") from None
    if (
        totals_a.requests != 1
        or totals_b.requests != 1
        or totals_a.total_tokens != 150
        or totals_b.total_tokens != 150
    ):
        raise PostgresFoundationIntegrationError("accounting_tenant_isolation_failed")
    now = datetime.now(timezone.utc)
    try:
        report_rows = stores[0].report_rows(
            group_by="organization",
            organization_id="tenant-a",
            include_latency=True,
            include_outcomes=True,
        )
        coverage = stores[0].coverage_summary(
            organization_id="tenant-a",
        )
        administrator = _synthetic_identity(
            "tenant-a",
            "usage-administrator",
            capabilities=("usage_viewer",),
        )
        audit_window_end = (now + timedelta(minutes=1)).isoformat()
        stores[0].record_admin_usage_read(
            administrator=administrator,
            access_scope="organization",
            group_by="organization",
            actor_filter=None,
            team_filter=None,
            window_start=now.isoformat(),
            window_end=audit_window_end,
            result_count=len(report_rows),
        )
        audit_administrator = _synthetic_identity(
            "tenant-a",
            "audit-administrator",
            capabilities=("audit_viewer",),
        )
        stores[0].record_admin_audit_read(
            administrator=audit_administrator,
            kind="usage",
            window_start=now.isoformat(),
            window_end=audit_window_end,
            result_count=1,
        )
        audit_read_denied = False
        try:
            stores[0].record_admin_audit_read(
                administrator=_synthetic_identity("tenant-a", "not-an-auditor"),
                kind="usage",
                window_start=now.isoformat(),
                window_end=audit_window_end,
                result_count=1,
            )
        except SecurityStoreError as error:
            audit_read_denied = str(error) == "audit_viewer_capability_required"
        audit_events = stores[0].audit_events(
            since=now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat(),
            until=audit_window_end,
            kind="all",
            organization_id="tenant-a",
        )
        security_events = security_store.audit_events(
            since=now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat(),
            until=audit_window_end,
            kind="security",
            organization_id="tenant-a",
        )
    except PostgresStorageError:
        raise PostgresFoundationIntegrationError("accounting_reporting_failed") from None
    if (
        len(report_rows) != 1
        or int(report_rows[0]["requests"]) != 1
        or report_rows[0].get("outcomes")
        != {
            "model_fallback_requests": 1,
            "output_capped_requests": 1,
            "reservation_budget_denied_requests": 0,
        }
        or int(coverage["accounted_gateway_requests"]) != 1
        or int(coverage["identity_bound_gateway_requests"]) != 1
        or int(coverage["unattributed_accounted_gateway_requests"]) != 0
        or coverage["identity_type_requests"]
        != {"human": 1, "service_account": 0, "ci": 0, "connector": 0}
        or not any(event["event_type"] == "usage" for event in audit_events)
        or not any(
            event["event_type"] == "usage"
            and event.get("governance_policy_version") == governance_policy_version
            and event.get("schema_version") == 4
            for event in audit_events
        )
        or not any(
            event["event_type"] == "security.admin.usage_read"
            for event in audit_events
        )
        or not audit_read_denied
        or not any(
            event["event_type"] == "security.admin.audit_read"
            and event.get("decision_actor_id") == "audit-administrator"
            and event.get("organization_id") == "tenant-a"
            for event in audit_events
        )
        or any(event.get("organization_id") != "tenant-a" for event in audit_events)
        or any(event.get("organization_id") != "tenant-a" for event in security_events)
    ):
        raise PostgresFoundationIntegrationError("accounting_reporting_mismatch")

    scope = (
        ReservationScope(name="organization", token_limit=1_000),
    )

    def reserve(store: PostgresUsageStore, identity: Identity) -> str | None:
        try:
            return store.reserve_budget(
                identity=identity,
                scopes=scope,
                reserved_tokens=600,
                reserved_cost_microusd=0,
                ttl_seconds=60,
            )
        except ReservationDenied:
            return None

    competing_a = _synthetic_identity("tenant-a", "competing-a")
    competing_b = _synthetic_identity("tenant-a", "competing-b")
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = (
                executor.submit(reserve, stores[0], competing_a),
                executor.submit(reserve, stores[1], competing_b),
            )
            reservation_ids = tuple(future.result(timeout=20) for future in futures)
    except PostgresStorageError:
        raise PostgresFoundationIntegrationError("accounting_budget_write_failed") from None
    allowed = tuple(value for value in reservation_ids if value is not None)
    if len(allowed) != 1 or stores[0].active_budget_reservations(
        organization_id="tenant-a"
    ) != 1:
        raise PostgresFoundationIntegrationError("atomic_budget_reservation_failed")
    stores[0].release_budget_reservation(
        allowed[0],
        organization_id="tenant-a",
    )

    report = ProviderCostReport(
        provider="openai",
        source_sha256="a" * 64,
        page_count=1,
        bucket_count=1,
        report_start=now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat(),
        report_end=(now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).isoformat(),
        items=(
            ProviderCostItem(
                bucket_start=now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat(),
                bucket_end=(now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).isoformat(),
                amount_usd="1.25",
                currency="USD",
                provider_scope_kind="unscoped",
                provider_scope_id=None,
            ),
        ),
    )
    try:
        imported = stores[0].import_provider_cost_report(
            organization_id="tenant-a",
            report=report,
        )
    except PostgresStorageError:
        raise PostgresFoundationIntegrationError("accounting_provider_cost_import_failed") from None
    try:
        duplicate = stores[1].import_provider_cost_report(
            organization_id="tenant-a",
            report=report,
        )
    except PostgresStorageError:
        raise PostgresFoundationIntegrationError("accounting_provider_cost_idempotency_failed") from None
    try:
        reconciled = stores[0].reconcile_provider_costs(
            organization_id="tenant-a",
            provider="openai",
            import_id=imported.import_id,
        )
    except PostgresStorageError as error:
        if error.code.startswith("provider_cost_"):
            raise PostgresFoundationIntegrationError(error.code) from None
        raise PostgresFoundationIntegrationError("accounting_provider_cost_reconcile_failed") from None
    if (
        not imported.created
        or duplicate.created
        or imported.import_id != duplicate.import_id
        or reconciled["provider_cost_usd"] != "1.25"
        or reconciled["gateway_requests"] != 1
    ):
        raise PostgresFoundationIntegrationError("provider_cost_accounting_failed")
    return {
        "tenant_scoped_usage_verified": True,
        "usage_rows_per_tenant": 1,
        "usage_reporting_verified": True,
        "usage_coverage_summary_verified": True,
        "usage_read_audit_verified": True,
        "audit_read_authorization_verified": True,
        "audit_reader_tenant_scope_verified": True,
        "exact_governance_policy_version_verified": True,
        "atomic_budget_competitors": 2,
        "atomic_budget_allowed": 1,
        "atomic_budget_denied": 1,
        "reservation_release_verified": True,
        "provider_cost_idempotency_verified": True,
        "provider_reconciliation_verified": True,
    }


def _identity_config(*, changed: bool = False) -> object:
    subjects: dict[tuple[str, str], Identity] = {}
    for suffix in ("a", "b"):
        identity = Identity(
            token_env="",
            token="",
            actor_id=f"human-{suffix}",
            actor_name="Synthetic Human",
            team_id="engineering",
            team_name="Engineering",
            organization_id=f"tenant-{suffix}",
            clearance="internal",
            allowed_clients=("codex", "claude-code"),
            capabilities=("session_admin",) if suffix == "a" else (),
            authentication_source=f"oidc:https://issuer.example/{suffix}",
        )
        subject = (
            f"subject-{suffix}-reassigned"
            if changed and suffix == "a"
            else f"subject-{suffix}"
        )
        subjects[(f"https://issuer.example/{suffix}", subject)] = identity
    return SimpleNamespace(identities_by_token={}, identities_by_subject=subjects)


def _issue_session(
    first: PostgresSessionStore,
    second: PostgresSessionStore,
    *,
    organization_id: str,
    suffix: str,
) -> object:
    stage = "create"
    try:
        secret = "integration-enrollment-" + secrets.token_urlsafe(32)
        enrollment = first.create_enrollment(
            issuer=f"https://issuer.example/{suffix}",
            client_name="codex",
            enrollment_secret=secret,
            organization_id=organization_id,
        )
        stage = "begin"
        state = first.new_authorization_state(enrollment.enrollment_id)
        browser_cookie = "browser-" + secrets.token_urlsafe(32)
        nonce = "nonce-" + secrets.token_urlsafe(32)
        verifier = "verifier-" + secrets.token_urlsafe(64)
        second.begin_authorization(
            enrollment_id=enrollment.enrollment_id,
            state=state,
            browser_cookie=browser_cookie,
            nonce=nonce,
            pkce_verifier=verifier,
        )
        stage = "consume"
        flow = first.consume_callback(state=state, browser_cookie=browser_cookie)
        if flow.nonce != nonce or flow.pkce_verifier != verifier:
            raise PostgresFoundationIntegrationError("session_shared_flow_mismatch")
        stage = "authorize"
        second.authorize_enrollment(
            enrollment_id=enrollment.enrollment_id,
            subject=f"subject-{suffix}",
            organization_id=organization_id,
            actor_id=f"human-{suffix}",
            team_id="engineering",
            clearance="internal",
        )
        stage = "redeem"
        return first.redeem_enrollment(
            enrollment_id=enrollment.enrollment_id,
            enrollment_secret=secret,
        )
    except SessionStoreError as error:
        raise PostgresFoundationIntegrationError(
            "session_" + stage + "_" + error.code
        ) from None


def _prove_identity_sessions(owner_dsn: str, runtime_dsn: str) -> dict[str, object]:
    config = _identity_config()
    synced = sync_identity_projection(config, owner_dsn)  # type: ignore[arg-type]
    repeated = sync_identity_projection(config, owner_dsn)  # type: ignore[arg-type]
    if (
        synced.changed_organizations != 2
        or synced.changed_principals != 2
        or repeated.changed_organizations != 0
        or repeated.changed_principals != 0
    ):
        raise PostgresFoundationIntegrationError("identity_sync_not_idempotent")
    master_key = b"integration-session-key-value!"[:32].ljust(32, b"x")
    stores = (
        PostgresSessionStore(
            runtime_dsn,
            organization_ids=("tenant-a", "tenant-b"),
            master_key=master_key,
            access_ttl_seconds=600,
            absolute_ttl_seconds=43_200,
            enrollment_ttl_seconds=300,
        ),
        PostgresSessionStore(
            runtime_dsn,
            organization_ids=("tenant-a", "tenant-b"),
            master_key=master_key,
            access_ttl_seconds=600,
            absolute_ttl_seconds=43_200,
            enrollment_ttl_seconds=300,
        ),
    )
    try:
        first_pair = _issue_session(
            stores[0], stores[1], organization_id="tenant-a", suffix="a"
        )
        principal = stores[1].authenticate_access(first_pair.access_token)
        if principal.organization_id != "tenant-a" or principal.actor_id != "human-a":
            raise PostgresFoundationIntegrationError("session_cross_instance_auth_failed")

        def rotate(store: PostgresSessionStore) -> tuple[str, object | None]:
            try:
                return "rotated", store.refresh(first_pair.refresh_token)
            except SessionStoreError as error:
                return error.code, None

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = tuple(
                future.result(timeout=20)
                for future in (
                    executor.submit(rotate, stores[0]),
                    executor.submit(rotate, stores[1]),
                )
            )
        codes = sorted(value[0] for value in outcomes)
        if codes != ["refresh_replay_detected", "rotated"]:
            raise PostgresFoundationIntegrationError(
                "session_refresh_atomicity_failed_" + "_".join(codes)
            )
        winner = next(value[1] for value in outcomes if value[0] == "rotated")
        try:
            stores[0].authenticate_access(winner.access_token)
        except SessionStoreError as error:
            if error.code != "invalid_session_credential":
                raise PostgresFoundationIntegrationError(
                    "session_replay_revocation_failed_" + error.code
                ) from None
        else:
            raise PostgresFoundationIntegrationError("session_replay_revocation_failed_active")

        second_pair = _issue_session(
            stores[1], stores[0], organization_id="tenant-a", suffix="a"
        )
        changed = sync_identity_projection(  # type: ignore[arg-type]
            _identity_config(changed=True), owner_dsn
        )
        if changed.changed_principals != 1 or changed.revoked_sessions < 1:
            raise PostgresFoundationIntegrationError("identity_change_not_propagated")
        try:
            stores[0].authenticate_access(second_pair.access_token)
        except SessionStoreError as error:
            if error.code != "invalid_session_credential":
                raise PostgresFoundationIntegrationError("identity_session_not_invalidated") from None
        else:
            raise PostgresFoundationIntegrationError("identity_session_not_invalidated")
    except PostgresFoundationIntegrationError:
        raise
    except (PostgresStorageError, SessionStoreError) as error:
        raise PostgresFoundationIntegrationError(
            "identity_session_runtime_" + error.code
        ) from None
    return {
        "configuration_projection_verified": True,
        "idempotent_sync_verified": True,
        "cross_instance_enrollment_verified": True,
        "cross_instance_authentication_verified": True,
        "atomic_refresh_competitors": 2,
        "atomic_refresh_rotated": 1,
        "atomic_refresh_replay_denied": 1,
        "refresh_replay_family_revoked": True,
        "identity_change_revocation_verified": True,
        "tenant_routing_tag_is_keyed": True,
    }


def _directory_policy_config(*, changed: bool = False) -> GatewayConfig:
    """Build a real policy runtime configuration for the SCIM proof.

    The proof deliberately uses an ordinary ``GatewayConfig`` instead of a
    resolver double: policy version materialization must validate and select
    the same profile/binding contract that the gateway uses at request time.
    """

    base = GatewayConfig.load(
        ROOT / "config.example.json",
        environ={"HORMUZ_TOKEN": "integration-directory-config-token"},
    )
    admin_a = _synthetic_identity(
        "tenant-a",
        "directory-admin-a",
        capabilities=("identity_admin", "policy_admin"),
    )
    admin_b = _synthetic_identity(
        "tenant-b",
        "directory-admin-b",
        capabilities=("identity_admin", "policy_admin"),
    )
    team_id = "directory-platform" if changed else "directory-engineering"
    profile = AuthorizationProfile(
        organization_id="tenant-a",
        policy_id="directory-engineering-standard",
        team_id=team_id,
        team_name="Directory Platform" if changed else "Directory Engineering",
        clearance="internal",
        allowed_clients=("codex",),
        capabilities=("usage_self_viewer",),
        policy=Policy(
            allowed_clients=("codex",),
            allowed_models=("gpt-5.4" if changed else "gpt-5.4-mini",),
            max_output_tokens=512,
        ),
    )
    config = replace(
        base,
        identities_by_token={
            "integration-directory-admin-a": admin_a,
            "integration-directory-admin-b": admin_b,
        },
        identities_by_subject={},
        organization_policy=Policy(
            allowed_clients=("codex", "claude-code"),
            allowed_models=("gpt-5.4-mini", "gpt-5.4"),
            max_output_tokens=1024,
        ),
        team_policies={},
        actor_policies={},
        team_dlp_overlays={},
        actor_dlp_overlays={},
        authorization_profiles={profile.policy_id: profile},
        team_bindings=(
            PolicyTeamBinding(
                organization_id="tenant-a",
                scim_group_external_id="directory-engineering-a",
                team_id=profile.team_id,
                policy_id=profile.policy_id,
            ),
        ),
        unbound_scim_group_action="deny",
        unbound_scim_group_fallback=None,
        session_broker=SessionBrokerConfig(
            enabled=True,
            backend="postgresql",
            public_base_url="https://session.integration.example",
        ),
    )
    config.validate_references()
    return config


def _prove_shared_scim_directory(runtime_dsn: str) -> dict[str, object]:
    """Prove policy-owned SCIM lifecycle resolution against active PostgreSQL state."""

    issuer = "https://directory.integration.example"
    routing_key = b"directory-routing-key-for-integration"[:32].ljust(32, b"d")
    config = _directory_policy_config()
    admin_a = config.identities_by_token["integration-directory-admin-a"]
    admin_b = config.identities_by_token["integration-directory-admin-b"]
    policy_store = PostgresPolicyStore(
        runtime_dsn,
        organization_ids=("tenant-a", "tenant-b"),
    )
    prior = policy_store.active(identity=admin_a)
    baseline = policy_store.stage(identity=admin_a, config=config)
    baseline_activation = policy_store.activate(
        identity=admin_a,
        version_id=baseline.version_id,
        expected_active_version_id=(prior.version_id if prior is not None else None),
    )
    if not baseline_activation.changed:
        raise PostgresFoundationIntegrationError("directory_policy_activation_failed")
    runtime = PolicyRuntime(config, policy_store)
    store = PostgresDirectoryStore(
        runtime_dsn,
        trusted_issuers=(issuer,),
        routing_key=routing_key,
        authorization_resolver=runtime,
    )
    user_payload = {
        "schemas": [SCIM_USER_SCHEMA, HORMUZ_USER_EXTENSION],
        "externalId": "directory-user-a",
        "userName": "directory-user-a@example.invalid",
        "displayName": "Directory User A",
        "active": True,
        HORMUZ_USER_EXTENSION: {"issuer": issuer, "subject": "directory-subject-a"},
    }
    try:
        user = store.create_user(administrator=admin_a, value=user_payload)
        user_id = str(user.resource["id"])
        group_payload = {
            "schemas": [SCIM_GROUP_SCHEMA, HORMUZ_GROUP_EXTENSION],
            "externalId": "directory-engineering-a",
            "displayName": "Directory Engineering A",
            "members": [{"value": user_id}],
            HORMUZ_GROUP_EXTENSION: {"active": True},
        }
        group = store.create_group(administrator=admin_a, value=group_payload)
        group_id = str(group.resource["id"])
        identity = store.identity_for_subject(issuer, "directory-subject-a")
        if (
            identity is None
            or identity.organization_id != "tenant-a"
            or identity.actor_id != user_id
            or identity.team_id != "directory-engineering"
        ):
            raise PostgresFoundationIntegrationError("directory_identity_resolution_failed")

        sessions = PostgresSessionStore(
            runtime_dsn,
            organization_ids=("tenant-a", "tenant-b"),
            master_key=b"directory-session-key-for-integration"[:32].ljust(32, b"s"),
            access_ttl_seconds=600,
            absolute_ttl_seconds=43_200,
            enrollment_ttl_seconds=300,
        )

        def issue_directory_session(label: str, authorized_identity: Identity) -> object:
            enrollment_secret = "directory-enrollment-" + label + "-" + "a" * 32
            enrollment = sessions.create_enrollment(
                issuer=issuer,
                client_name="codex",
                enrollment_secret=enrollment_secret,
                organization_id="tenant-a",
            )
            state = sessions.new_authorization_state(enrollment.enrollment_id)
            browser_cookie = "directory-browser-" + label + "-" + "b" * 32
            sessions.begin_authorization(
                enrollment_id=enrollment.enrollment_id,
                state=state,
                browser_cookie=browser_cookie,
                nonce="directory-oidc-" + label + "-" + "c" * 32,
                pkce_verifier="directory-pkce-" + label + "-" + "d" * 48,
            )
            sessions.consume_callback(state=state, browser_cookie=browser_cookie)
            sessions.authorize_enrollment(
                enrollment_id=enrollment.enrollment_id,
                subject="directory-subject-a",
                organization_id="tenant-a",
                actor_id=user_id,
                team_id=authorized_identity.team_id,
                clearance=authorized_identity.clearance,
            )
            return sessions.redeem_enrollment(
                enrollment_id=enrollment.enrollment_id,
                enrollment_secret=enrollment_secret,
            )

        pair = issue_directory_session("before-unassignment", identity)
        if sessions.authenticate_access(pair.access_token).actor_id != user_id:
            raise PostgresFoundationIntegrationError("directory_session_enrollment_failed")

        workload_payload = {
            "externalId": "directory-workload-b",
            "displayName": "Directory Workload B",
            "identityType": "ci",
            "active": True,
            "issuer": issuer,
            "subject": "directory-workload-subject-b",
            "teamId": "directory-platform",
            "teamName": "Directory Platform",
            "clearance": "internal",
            "allowedClients": ["codex"],
            "capabilities": [],
        }
        identity_only_admin = replace(
            admin_a,
            actor_id="directory-identity-only-admin-a",
            capabilities=("identity_admin",),
        )
        try:
            store.create_workload(administrator=identity_only_admin, value=workload_payload)
        except DirectoryError as error:
            if error.code != "policy_admin_capability_required":
                raise PostgresFoundationIntegrationError(
                    "directory_workload_authority_error_invalid"
                ) from None
        else:
            raise PostgresFoundationIntegrationError("directory_identity_admin_granted_workload")
        workload = store.create_workload(administrator=admin_b, value=workload_payload)
        if store.identity_for_subject(issuer, "directory-workload-subject-b") is None:
            raise PostgresFoundationIntegrationError("directory_workload_resolution_failed")
        if store.organizations_for_issuer(issuer) != ("tenant-a", "tenant-b"):
            raise PostgresFoundationIntegrationError("directory_issuer_routing_failed")

        collision = dict(workload_payload)
        collision["externalId"] = "directory-workload-collision"
        collision["subject"] = "directory-subject-a"
        try:
            store.create_workload(administrator=admin_b, value=collision)
        except DirectoryError as error:
            if error.code != "directory_subject_conflict":
                raise PostgresFoundationIntegrationError("directory_subject_conflict_invalid") from None
        else:
            raise PostgresFoundationIntegrationError("directory_subject_collision_allowed")

        removed_group = dict(group_payload)
        removed_group["members"] = []
        removed = store.replace_group(
            administrator=admin_a,
            resource_id=group_id,
            value=removed_group,
            if_match=str(group.resource["meta"]["version"]),
        )
        try:
            store.identity_for_subject(issuer, "directory-subject-a")
        except DirectoryError as error:
            if error.code != "directory_subject_unassigned":
                raise PostgresFoundationIntegrationError(
                    "directory_unassignment_error_" + error.code
                ) from None
        else:
            raise PostgresFoundationIntegrationError("directory_unassignment_allowed")
        try:
            sessions.authenticate_access(pair.access_token)
        except SessionStoreError as error:
            if error.code != "invalid_session_credential":
                raise PostgresFoundationIntegrationError("directory_session_revocation_error_invalid") from None
        else:
            raise PostgresFoundationIntegrationError("directory_session_not_revoked")

        readded = store.replace_group(
            administrator=admin_a,
            resource_id=group_id,
            value=group_payload,
            if_match=str(removed.resource["meta"]["version"]),
        )
        if not readded.changed:
            raise PostgresFoundationIntegrationError("directory_reauthorization_not_applied")
        reauthorized = store.identity_for_subject(issuer, "directory-subject-a")
        if (
            reauthorized is None
            or reauthorized.team_id != "directory-engineering"
            or reauthorized.authorization_profile_id != "directory-engineering-standard"
        ):
            raise PostgresFoundationIntegrationError("directory_reauthorization_failed")
        profile_change_pair = issue_directory_session(
            "before-policy-change", reauthorized
        )

        changed_config = _directory_policy_config(changed=True)
        changed = policy_store.stage(identity=admin_a, config=changed_config)
        changed_activation = policy_store.activate(
            identity=admin_a,
            version_id=changed.version_id,
            expected_active_version_id=baseline.version_id,
        )
        if not changed_activation.changed:
            raise PostgresFoundationIntegrationError("directory_policy_change_not_activated")
        broker = SessionBroker(config, Authenticator(config, store), sessions)
        try:
            broker.authenticate(profile_change_pair.access_token)
        except AuthenticationError as error:
            if error.code != "session_authorization_removed":
                raise PostgresFoundationIntegrationError(
                    "directory_policy_change_session_error_invalid"
                ) from None
        else:
            raise PostgresFoundationIntegrationError("directory_policy_change_session_active")
        changed_identity = store.identity_for_subject(issuer, "directory-subject-a")
        if (
            changed_identity is None
            or changed_identity.team_id != "directory-platform"
            or runtime.resolve(changed_identity).config.resolved_policy(changed_identity).allowed_models
            != ("gpt-5.4",)
        ):
            raise PostgresFoundationIntegrationError("directory_active_policy_binding_not_applied")
        renewed_pair = issue_directory_session("after-policy-change", changed_identity)
        if broker.authenticate(renewed_pair.access_token).team_id != "directory-platform":
            raise PostgresFoundationIntegrationError("directory_policy_change_reenrollment_failed")

        unbound_group = {
            "schemas": [SCIM_GROUP_SCHEMA, HORMUZ_GROUP_EXTENSION],
            "externalId": "directory-unbound-a",
            "displayName": "Directory Unbound A",
            "members": [{"value": user_id}],
            HORMUZ_GROUP_EXTENSION: {"active": True},
        }
        store.create_group(administrator=admin_a, value=unbound_group)
        try:
            store.identity_for_subject(issuer, "directory-subject-a")
        except DirectoryError as error:
            if error.code != "directory_subject_group_unbound":
                raise PostgresFoundationIntegrationError(
                    "directory_unbound_group_error_" + error.code
                ) from None
        else:
            raise PostgresFoundationIntegrationError("directory_unbound_group_allowed")
    except PostgresFoundationIntegrationError:
        raise
    except (DirectoryError, PolicyAdminError, PostgresStorageError, SessionStoreError) as error:
        raise PostgresFoundationIntegrationError(
            "directory_runtime_" + error.code
        ) from None

    psycopg, _sql = _require_driver()
    try:
        with psycopg.connect(runtime_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT count(*) FROM hormuz.gateway_directory_subject_routes")
    except Exception as error:
        if getattr(error, "sqlstate", None) != "42501":
            raise PostgresFoundationIntegrationError("directory_global_index_access_error") from None
    else:
        raise PostgresFoundationIntegrationError("directory_global_index_visible")
    return {
        "shared_scim_crud_verified": True,
        "generic_oidc_subject_resolution_verified": True,
        "keyed_global_route_lookup_verified": True,
        "raw_global_route_table_denied": True,
        "cross_tenant_subject_collision_denied": True,
        "directory_session_projection_verified": True,
        "directory_unassignment_revokes_session": True,
        "policy_owned_group_authorization_verified": True,
        "active_policy_binding_resolution_verified": True,
        "active_policy_change_revokes_session": True,
        "active_policy_change_reenrollment_verified": True,
        "unbound_scim_group_default_denied": True,
        "identity_admin_direct_workload_denied": True,
    }


def _policy_config(*, changed: bool = False) -> object:
    identity_config = _identity_config()
    identities = {
        identity.actor_id: identity
        for identity in identity_config.identities_by_subject.values()
    }
    return SimpleNamespace(
        identities_by_token={},
        identities_by_subject=identity_config.identities_by_subject,
        identities_by_actor=identities,
        model_routes={
            "integration-model": ModelRoute(
                alias="integration-model",
                protocol="openai",
                upstream_model="integration-upstream",
                rate_card_version="integration-v1",
            )
        },
        organization_policy=Policy(
            allowed_models=("integration-model",),
            max_output_tokens=2048 if changed else 1024,
        ),
        team_policies={},
        actor_policies={},
        authorization_profiles={},
        team_bindings=(),
        unbound_scim_group_action="deny",
        unbound_scim_group_fallback=None,
        secret_controls=SecretControls(mode="redact"),
        dlp_controls=DLPControls(
            policy_version="integration-dlp-v2" if changed else "integration-dlp-v1",
            rules=(
                DLPRuleConfig(
                    rule_id="integration-rule",
                    category="integration",
                    confidence="high",
                    action="require_approval",
                ),
            ),
            approval=DLPApprovalConfig(
                enabled=True,
                fingerprint_key_env="INTEGRATION_APPROVAL_KEY",
                ttl_seconds=900,
            ),
        ),
        team_dlp_overlays={},
        actor_dlp_overlays={},
    )


def _strict_cache_policy_config() -> GatewayConfig:
    """Build a real v5 strict-cache config for PostgreSQL materialization."""

    base = _directory_policy_config()
    capabilities = {
        alias: ProviderCacheCapability(
            protocol=route.protocol,
            upstream_model=route.upstream_model,
            operations=(
                ("/v1/responses",)
                if route.protocol == "openai"
                else ("/v1/messages", "/v1/messages/count_tokens")
            ),
            capability_version=f"integration-{route.protocol}-cache-v1",
            reviewed_at=date.today(),
            source_urls=("https://example.invalid/provider-cache-contract",),
            # This proves catalog persistence and runtime validation, not a
            # live vendor no-cache assertion.
            strict_no_cache="unsupported",
        )
        for alias, route in base.model_routes.items()
    }
    result = replace(
        base,
        organization_policy=replace(
            base.organization_policy,
            provider_cache=ProviderCachePolicy(
                mode="disabled",
                capability_max_age_days=30,
            ),
        ),
        provider_cache_capabilities=capabilities,
    )
    result.validate_references()
    return result


def _prove_policy_administration_and_approvals(
    owner_dsn: str,
    runtime_dsn: str,
) -> dict[str, object]:
    config = _policy_config()
    synced = sync_policy_projection(config, owner_dsn)  # type: ignore[arg-type]
    repeated = sync_policy_projection(config, owner_dsn)  # type: ignore[arg-type]
    if synced.changed_organizations != 2 or repeated.changed_organizations != 0:
        raise PostgresFoundationIntegrationError("policy_sync_not_idempotent")
    verify_runtime_policy_projection(config, runtime_dsn)  # type: ignore[arg-type]
    changed_config = _policy_config(changed=True)
    try:
        verify_runtime_policy_projection(changed_config, runtime_dsn)  # type: ignore[arg-type]
    except PostgresStorageError as error:
        if error.code != "policy_projection_stale":
            raise PostgresFoundationIntegrationError("policy_stale_error_invalid") from None
    else:
        raise PostgresFoundationIntegrationError("policy_stale_projection_accepted")
    changed = sync_policy_projection(changed_config, owner_dsn)  # type: ignore[arg-type]
    if changed.changed_organizations != 2:
        raise PostgresFoundationIntegrationError("policy_change_not_applied")
    verify_runtime_policy_projection(changed_config, runtime_dsn)  # type: ignore[arg-type]

    policy_stores = (
        PostgresPolicyStore(
            runtime_dsn,
            organization_ids=("tenant-a", "tenant-b"),
        ),
        PostgresPolicyStore(
            runtime_dsn,
            organization_ids=("tenant-a", "tenant-b"),
        ),
    )
    policy_admin = _synthetic_identity(
        "tenant-a",
        "policy-admin-a",
        capabilities=("policy_admin",),
    )
    initial_version = policy_stores[0].stage(  # type: ignore[arg-type]
        identity=policy_admin,
        config=config,
    )
    repeated_version = policy_stores[1].stage(  # type: ignore[arg-type]
        identity=policy_admin,
        config=config,
    )
    if (
        not initial_version.staged
        or repeated_version.staged
        or initial_version.version_id != repeated_version.version_id
        or initial_version.projection_sha256 != repeated_version.projection_sha256
    ):
        raise PostgresFoundationIntegrationError("policy_version_stage_not_idempotent")
    first_activation = policy_stores[0].activate(
        identity=policy_admin,
        version_id=initial_version.version_id,
        expected_active_version_id=None,
    )
    changed_version = policy_stores[1].stage(  # type: ignore[arg-type]
        identity=policy_admin,
        config=changed_config,
    )
    second_activation = policy_stores[1].activate(
        identity=policy_admin,
        version_id=changed_version.version_id,
        expected_active_version_id=initial_version.version_id,
    )
    observed = policy_stores[0].active(identity=policy_admin)
    if (
        not first_activation.changed
        or first_activation.activation_sequence != 1
        or not changed_version.staged
        or not second_activation.changed
        or second_activation.activation_sequence != 2
        or observed is None
        or observed.version_id != changed_version.version_id
        or observed.activation_sequence != 2
    ):
        raise PostgresFoundationIntegrationError("policy_activation_not_converged")
    rolled_back = policy_stores[0].activate(
        identity=policy_admin,
        version_id=initial_version.version_id,
        expected_active_version_id=changed_version.version_id,
        rollback=True,
    )
    rollback_observed = policy_stores[1].active(identity=policy_admin)
    if (
        not rolled_back.changed
        or rolled_back.action != "rolled_back"
        or rolled_back.activation_sequence != 3
        or rollback_observed is None
        or rollback_observed.version_id != initial_version.version_id
        or rollback_observed.activation_sequence != 3
    ):
        raise PostgresFoundationIntegrationError("policy_rollback_not_converged")
    try:
        policy_stores[0].stage(  # type: ignore[arg-type]
            identity=_synthetic_identity("tenant-a", "not-an-admin"),
            config=config,
        )
    except PolicyAdminError as error:
        if error.code != "policy_admin_capability_required":
            raise PostgresFoundationIntegrationError(
                "policy_admin_capability_error_invalid"
            ) from None
    else:
        raise PostgresFoundationIntegrationError("policy_admin_capability_not_enforced")
    try:
        policy_stores[1].activate(
            identity=_synthetic_identity(
                "tenant-b",
                "policy-admin-b",
                capabilities=("policy_admin",),
            ),
            version_id=initial_version.version_id,
            expected_active_version_id=None,
        )
    except PolicyAdminError as error:
        if error.code != "policy_version_not_found":
            raise PostgresFoundationIntegrationError(
                "policy_version_cross_tenant_error_invalid"
            ) from None
    else:
        raise PostgresFoundationIntegrationError("policy_version_cross_tenant_visible")

    strict_config = _strict_cache_policy_config()
    strict_version = policy_stores[0].stage(  # type: ignore[arg-type]
        identity=policy_admin,
        config=strict_config,
    )
    strict_activation = policy_stores[0].activate(
        identity=policy_admin,
        version_id=strict_version.version_id,
        expected_active_version_id=initial_version.version_id,
    )
    strict_runtime = PolicyRuntime(strict_config, policy_stores[1])  # type: ignore[arg-type]
    strict_resolved = strict_runtime.resolve(policy_admin)
    if (
        not strict_activation.changed
        or strict_activation.activation_sequence != 4
        or strict_resolved.version_id != strict_version.version_id
        or not strict_resolved.config.resolved_policy(
            policy_admin
        ).provider_cache.strict_no_cache_required
        or strict_resolved.config.provider_cache_capabilities
        != strict_config.provider_cache_capabilities
    ):
        raise PostgresFoundationIntegrationError(
            "provider_cache_catalog_v5_materialization_failed"
        )
    try:
        policy_stores[1].activate(
            identity=_synthetic_identity(
                "tenant-b",
                "policy-admin-b",
                capabilities=("policy_admin",),
            ),
            version_id=strict_version.version_id,
            expected_active_version_id=None,
        )
    except PolicyAdminError as error:
        if error.code != "policy_version_not_found":
            raise PostgresFoundationIntegrationError(
                "provider_cache_catalog_cross_tenant_error_invalid"
            ) from None
    else:
        raise PostgresFoundationIntegrationError(
            "provider_cache_catalog_cross_tenant_visible"
        )

    organizations = ("tenant-a", "tenant-b")
    stores = (
        PostgresSecurityStore(runtime_dsn, organization_ids=organizations),
        PostgresSecurityStore(runtime_dsn, organization_ids=organizations),
    )
    requester = _synthetic_identity("tenant-a", "actor-a")
    approver = _synthetic_identity(
        "tenant-a",
        "approver-a",
        capabilities=("dlp_approver",),
    )
    fingerprint = "hdf_v1_" + "a" * 64
    arguments = {
        "identity": requester,
        "client": "codex",
        "protocol": "openai",
        "requested_model": "integration-model",
        "routed_model": "integration-upstream",
        "policy_version": "integration-dlp-v2",
        "payload_fingerprint": fingerprint,
        "rules": ("integration-rule",),
        "detection_count": 1,
        "ttl_seconds": 900,
    }
    pending = stores[0].authorize_or_request_dlp_approval(**arguments)
    try:
        stores[1].get_dlp_approval_request(
            pending.request_id,
            organization_id="tenant-b",
        )
    except DLPApprovalStoreError as error:
        if error.code != "approval_request_not_found":
            raise PostgresFoundationIntegrationError(
                "approval_cross_tenant_error_invalid_" + error.code
            ) from None
    else:
        raise PostgresFoundationIntegrationError("approval_cross_tenant_visible")
    stores[1].approve_dlp_approval_request(
        pending.request_id,
        approver=approver,
        ttl_seconds=900,
    )

    def consume(store: PostgresSecurityStore) -> object:
        return store.authorize_or_request_dlp_approval(**arguments)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(
            future.result(timeout=20)
            for future in (
                executor.submit(consume, stores[0]),
                executor.submit(consume, stores[1]),
            )
        )
    if sorted(item.status for item in outcomes) != ["consumed", "pending"]:
        raise PostgresFoundationIntegrationError("approval_atomic_consumption_failed")
    consumed = next(item for item in outcomes if item.status == "consumed")
    next_pending = next(item for item in outcomes if item.status == "pending")

    same_actor_approver = _synthetic_identity(
        "tenant-a",
        "actor-a",
        capabilities=("dlp_approver",),
    )
    try:
        stores[0].approve_dlp_approval_request(
            next_pending.request_id,
            approver=same_actor_approver,
            ttl_seconds=900,
        )
    except DLPApprovalStoreError as error:
        if error.code != "approval_self_approval_forbidden":
            raise PostgresFoundationIntegrationError("approval_self_error_invalid") from None
    else:
        raise PostgresFoundationIntegrationError("approval_self_approval_allowed")
    stores[0].approve_dlp_approval_request(
        next_pending.request_id,
        approver=approver,
        ttl_seconds=900,
    )
    mutated = stores[1].authorize_or_request_dlp_approval(
        **{**arguments, "routed_model": "integration-mutated"}
    )
    if mutated.status != "pending":
        raise PostgresFoundationIntegrationError("approval_model_binding_failed")
    second_consumed = stores[0].authorize_or_request_dlp_approval(**arguments)
    if second_consumed.status != "consumed" or second_consumed.request_id != next_pending.request_id:
        raise PostgresFoundationIntegrationError("approval_exact_retry_failed")

    stores[1].record_dlp_approval_model_mismatch(
        consumed.request_id,
        organization_id="tenant-a",
        actual_model="integration-unexpected",
    )
    stores[0].record_secret_event(
        identity=requester,
        client="codex",
        protocol="openai",
        requested_model="integration-model",
        action="redacted",
        detection_count=1,
        rules=("openai-key",),
    )
    stores[1].record_dlp_event(
        identity=requester,
        client="codex",
        protocol="openai",
        requested_model="integration-model",
        routed_model="integration-upstream",
        action="approval_required",
        redaction_count=0,
        policy_version="integration-dlp-v2",
        findings=(
            {
                "rule_id": "integration-rule",
                "category": "integration",
                "confidence": "high",
                "action": "require_approval",
                "count": 1,
            },
        ),
    )
    secret_totals = stores[1].monthly_secret_totals(organization_id="tenant-a")
    approval_totals = stores[0].monthly_dlp_approval_totals(
        organization_id="tenant-a"
    )
    events = stores[1].audit_events(
        since=datetime.now(timezone.utc).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        ).isoformat(),
        kind="security",
    )
    event_types = {str(event["event_type"]) for event in events}
    if (
        secret_totals.events != 2
        or approval_totals.consumed != 2
        or approval_totals.model_mismatches != 1
        or not {"security.secret", "security.dlp", "security.dlp.approval"}.issubset(
            event_types
        )
    ):
        raise PostgresFoundationIntegrationError("approval_security_evidence_mismatch")
    return {
        "configuration_projection_verified": True,
        "idempotent_sync_verified": True,
        "stale_projection_rejected": True,
        "cross_instance_request_verified": True,
        "cross_tenant_request_hidden": True,
        "self_approval_denied": True,
        "atomic_retry_competitors": 2,
        "atomic_retry_consumed": 1,
        "atomic_retry_blocked_pending": 1,
        "exact_payload_model_policy_binding_verified": True,
        "model_mismatch_audited": True,
        "security_events_shared": True,
        "immutable_policy_versions_verified": True,
        "policy_stage_idempotent": True,
        "atomic_activation_verified": True,
        "cross_instance_active_version_verified": True,
        "rollback_verified": True,
        "policy_admin_capability_verified": True,
        "policy_version_cross_tenant_hidden": True,
        "active_policy_activation_sequence": 4,
        "provider_cache_catalog_v5_verified": True,
        "provider_cache_catalog_cross_tenant_hidden": True,
        "content_free": True,
    }


def _expect_verification_code(owner_dsn: str, expected_code: str) -> None:
    try:
        verify_postgres(owner_dsn)
    except PostgresStorageError as error:
        if error.code == expected_code:
            return
    raise PostgresFoundationIntegrationError("verification_tamper_not_detected")


def _prove_tenant_lifecycle(owner_dsn: str, runtime_dsn: str) -> dict[str, object]:
    """Exercise the irreversible path only after every other tenant-b proof."""

    organization_id = "tenant-b"
    now = datetime.now(timezone.utc)
    master_key = b"integration-session-key-value!"[:32].ljust(32, b"x")
    session_store = PostgresSessionStore(
        runtime_dsn,
        organization_ids=("tenant-a", organization_id),
        master_key=master_key,
        access_ttl_seconds=600,
        absolute_ttl_seconds=43_200,
        enrollment_ttl_seconds=300,
    )
    try:
        pair = _issue_session(
            session_store,
            session_store,
            organization_id=organization_id,
            suffix="b",
        )
        runtime_gate = TenantLifecycleRuntimeGate(runtime_dsn)
        runtime_gate.require_active(_synthetic_identity(organization_id, "human-b"))

        service = TenantLifecycleService(owner_dsn, clock=lambda: now)
        deactivated = service.deactivate(
            organization_id=organization_id,
            reason_code="administrative",
        )
        if (
            not deactivated.changed
            or deactivated.revoked_sessions < 1
            or deactivated.state != "deactivated"
        ):
            raise PostgresFoundationIntegrationError("tenant_deactivation_not_effective")
        try:
            runtime_gate.require_active(_synthetic_identity(organization_id, "human-b"))
        except TenantLifecycleError as error:
            if error.code != "tenant_inactive":
                raise PostgresFoundationIntegrationError("tenant_gate_error_invalid") from None
        else:
            raise PostgresFoundationIntegrationError("tenant_deactivation_runtime_allowed")
        try:
            session_store.authenticate_access(pair.access_token)
        except SessionStoreError as error:
            if error.code != "tenant_inactive":
                raise PostgresFoundationIntegrationError("tenant_session_error_invalid") from None
        else:
            raise PostgresFoundationIntegrationError("tenant_session_not_revoked")

        with tempfile.TemporaryDirectory() as temporary:
            export_path = Path(temporary) / "tenant-b.hormuz"
            receipt = service.export(
                organization_id=organization_id,
                encryption_key=b"tenant-lifecycle-export-key".ljust(32, b"x"),
                output=export_path,
            )
            plan = TenantLifecycleService.restore_plan(
                input_path=export_path,
                encryption_key=b"tenant-lifecycle-export-key".ljust(32, b"x"),
            )
            export_mode = export_path.stat().st_mode & 0o777
            if (
                export_mode != 0o600
                or plan.organization_id != organization_id
                or plan.table_counts.get("gateway_human_sessions", 0) < 1
                or plan.table_counts.get("gateway_usage_events", 0) < 1
            ):
                raise PostgresFoundationIntegrationError("tenant_export_restore_plan_invalid")
            scheduled = service.schedule_purge(
                organization_id=organization_id,
                export_id=receipt.export_id,
                retention_days=1,
            )
            if not scheduled.changed or scheduled.state != "purge_scheduled":
                raise PostgresFoundationIntegrationError("tenant_purge_not_scheduled")
            try:
                service.purge(
                    organization_id=organization_id,
                    export_id=receipt.export_id,
                    confirm_ciphertext_sha256=receipt.ciphertext_sha256,
                )
            except TenantLifecycleError as error:
                if error.code != "tenant_purge_retention_pending":
                    raise PostgresFoundationIntegrationError("tenant_retention_error_invalid") from None
            else:
                raise PostgresFoundationIntegrationError("tenant_purge_retention_bypassed")

            future_service = TenantLifecycleService(
                owner_dsn,
                clock=lambda: now + timedelta(days=1, seconds=1),
            )
            purged = future_service.purge(
                organization_id=organization_id,
                export_id=receipt.export_id,
                confirm_ciphertext_sha256=receipt.ciphertext_sha256,
            )
            if purged.organization_id != organization_id:
                raise PostgresFoundationIntegrationError("tenant_purge_result_invalid")

        try:
            runtime_gate.require_active(_synthetic_identity(organization_id, "human-b"))
        except TenantLifecycleError as error:
            if error.code != "tenant_lifecycle_missing":
                raise PostgresFoundationIntegrationError("tenant_purge_gate_error_invalid") from None
        else:
            raise PostgresFoundationIntegrationError("tenant_purge_runtime_allowed")

        psycopg, _sql = _require_driver()
        with psycopg.connect(owner_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT export_id, export_ciphertext_sha256 "
                    "FROM hormuz.gateway_tenant_purge_tombstones WHERE tenant_id = %s",
                    (organization_id,),
                )
                tombstone = cursor.fetchone()
        if tombstone != (receipt.export_id, receipt.ciphertext_sha256):
            raise PostgresFoundationIntegrationError("tenant_purge_tombstone_invalid")
        try:
            sync_identity_projection(_identity_config(changed=True), owner_dsn)  # type: ignore[arg-type]
        except PostgresStorageError as error:
            if error.code != "tenant_reonboard_required":
                raise PostgresFoundationIntegrationError("tenant_tombstone_error_invalid") from None
        else:
            raise PostgresFoundationIntegrationError("tenant_tombstone_reonboarded_implicitly")
    except PostgresFoundationIntegrationError:
        raise
    except (PostgresStorageError, SessionStoreError, TenantLifecycleError) as error:
        raise PostgresFoundationIntegrationError("tenant_lifecycle_" + error.code) from None
    except Exception:
        raise PostgresFoundationIntegrationError("tenant_lifecycle_probe_failed") from None
    return {
        "runtime_gate_active_before_deactivation": True,
        "deactivation_blocks_runtime": True,
        "active_human_session_revoked": True,
        "encrypted_export_verified": True,
        "restore_plan_content_free": True,
        "private_export_mode": "0600",
        "purge_retention_enforced": True,
        "hard_purge_verified": True,
        "owner_only_tombstone_retained": True,
        "tombstone_blocks_implicit_reonboarding": True,
    }


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

        with psycopg.connect(owner_dsn, autocommit=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "ALTER TABLE hormuz.gateway_usage_events "
                    "ADD COLUMN unexpected_column text"
                )
        _expect_verification_code(owner_dsn, "accounting_table_columns_invalid")
        with psycopg.connect(owner_dsn, autocommit=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "ALTER TABLE hormuz.gateway_usage_events "
                    "DROP COLUMN unexpected_column"
                )
                cursor.execute(
                    "ALTER TABLE hormuz.gateway_dlp_approval_requests "
                    "ADD COLUMN prompt text"
                )
        _expect_verification_code(owner_dsn, "accounting_table_columns_invalid")
        with psycopg.connect(owner_dsn, autocommit=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "ALTER TABLE hormuz.gateway_dlp_approval_requests "
                    "DROP COLUMN prompt"
                )
        verify_postgres(owner_dsn)
    except PostgresFoundationIntegrationError:
        raise
    except Exception:
        raise PostgresFoundationIntegrationError("verification_tamper_probe_failed") from None
    return {
        "permissive_policy_rejected": True,
        "runtime_owner_membership_rejected": True,
        "unexpected_accounting_column_rejected": True,
        "unexpected_security_column_rejected": True,
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
        accounting = _prove_accounting_store(runtime_dsn)
        identity_sessions = _prove_identity_sessions(owner_dsn, runtime_dsn)
        policy_administration = _prove_policy_administration_and_approvals(
            owner_dsn,
            runtime_dsn,
        )
        repository_conformance = prove_repository_conformance(runtime_dsn)
        shared_directory = _prove_shared_scim_directory(runtime_dsn)
        tamper_detection = _prove_verifier_tamper_detection(admin_dsn, owner_dsn)
        tenant_lifecycle = _prove_tenant_lifecycle(owner_dsn, runtime_dsn)
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
            "accounting": accounting,
            "identity_sessions": identity_sessions,
            "repository_conformance": repository_conformance,
            "shared_directory": shared_directory,
            "policy_administration": policy_administration,
            "tamper_detection": tamper_detection,
            "tenant_lifecycle": tenant_lifecycle,
            "content_free": True,
        }
    except PostgresStorageError as error:
        primary_error = PostgresFoundationIntegrationError(error.code)
    except RepositoryConformanceError as error:
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
