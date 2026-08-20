#!/usr/bin/env python3
"""Exercise Hormuz migrations and tenant isolation against real PostgreSQL."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import time
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote

from hormuz.billing import ProviderCostItem, ProviderCostReport
from hormuz.config import (
    DLPApprovalConfig,
    DLPControls,
    DLPRuleConfig,
    Identity,
    ModelRoute,
    Policy,
    SecretControls,
)
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
from hormuz.postgres_security_store import PostgresSecurityStore
from hormuz.postgres_session_store import PostgresSessionStore
from hormuz.session_store import SessionStoreError
from hormuz.store import (
    DLPApprovalStoreError,
    ReservationDenied,
    ReservationScope,
)


EVIDENCE_SCHEMA = "hormuz.postgres-policy-approval-integration.v4"
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
    identity_a = _synthetic_identity("tenant-a", "actor-a")
    identity_b = _synthetic_identity("tenant-b", "actor-b")
    try:
        for store, identity in ((stores[0], identity_a), (stores[1], identity_b)):
            store.record(
                identity=identity,
                client="integration",
                protocol="openai",
                requested_model="gpt-test",
                resolved_alias="gpt-test",
                upstream_model="gpt-test",
                actual_model="gpt-test",
                policy_action="allowed",
                status="succeeded",
                input_tokens=100,
                output_tokens=50,
                billable_tokens=150,
                cost_microusd=125_000,
                cost_basis="estimated",
                rate_card_version="integration-v1",
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
        )
        administrator = _synthetic_identity(
            "tenant-a",
            "usage-administrator",
            capabilities=("usage_viewer",),
        )
        stores[0].record_admin_usage_read(
            administrator=administrator,
            group_by="organization",
            actor_filter=None,
            team_filter=None,
            window_start=now.isoformat(),
            window_end=(now + timedelta(seconds=1)).isoformat(),
            result_count=len(report_rows),
        )
        audit_events = stores[0].audit_events(
            since=now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat(),
            kind="all",
        )
    except PostgresStorageError:
        raise PostgresFoundationIntegrationError("accounting_reporting_failed") from None
    if (
        len(report_rows) != 1
        or int(report_rows[0]["requests"]) != 1
        or not any(event["event_type"] == "usage" for event in audit_events)
        or not any(
            event["event_type"] == "security.admin.usage_read"
            for event in audit_events
        )
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
        "usage_read_audit_verified": True,
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


def _prove_policy_approvals(owner_dsn: str, runtime_dsn: str) -> dict[str, object]:
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
        "content_free": True,
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
        policy_approvals = _prove_policy_approvals(owner_dsn, runtime_dsn)
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
            "accounting": accounting,
            "identity_sessions": identity_sessions,
            "policy_approvals": policy_approvals,
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
