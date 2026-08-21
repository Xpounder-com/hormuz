"""Explicit current/legacy persistence routing during PostgreSQL adoption."""

from __future__ import annotations

from typing import Any

from .config import GatewayConfig
from .postgres import postgres_dsn_from_env
from .policy_projection import verify_runtime_policy_projection
from .postgres_security_store import PostgresSecurityStore
from .postgres_usage_store import PostgresUsageStore
from .store import UsageStore


_ACCOUNTING_METHODS = {
    "record",
    "import_provider_cost_report",
    "reconcile_provider_costs",
    "allocate_provider_costs",
    "reserve_budget",
    "release_budget_reservation",
    "refresh_budget_reservation",
    "active_budget_reservations",
    "monthly_totals",
    "summary_rows",
    "report_rows",
    "coverage_summary",
    "record_admin_usage_read",
    "record_admin_audit_read",
}

_SECURITY_METHODS = {
    "record_secret_event",
    "record_dlp_event",
    "authorize_or_request_dlp_approval",
    "get_dlp_approval_request",
    "approve_dlp_approval_request",
    "record_dlp_approval_model_mismatch",
    "monthly_secret_totals",
    "monthly_dlp_approval_totals",
}


class GatewayStoreRouter:
    """Route current operations to PostgreSQL while retaining legacy audit evidence."""

    backend = "postgresql-accounting-security-with-sqlite-legacy-audit"

    def __init__(
        self,
        accounting: PostgresUsageStore,
        security: PostgresSecurityStore | UsageStore,
        legacy_security: UsageStore | None = None,
    ):
        self.accounting = accounting
        self.security = security
        self.legacy_security = legacy_security or (
            security if isinstance(security, UsageStore) else None
        )
        self.security_database_path = (
            self.legacy_security.path if self.legacy_security is not None else None
        )

    def __getattr__(self, name: str) -> Any:
        if name in _ACCOUNTING_METHODS:
            return getattr(self.accounting, name)
        if name in _SECURITY_METHODS:
            return getattr(self.security, name)
        raise AttributeError(name)

    def audit_events(
        self,
        *,
        since: str,
        kind: str = "all",
        organization_id: str | None = None,
        until: str | None = None,
    ) -> list[dict[str, object]]:
        if kind not in {"all", "usage", "security"}:
            raise ValueError(f"Unsupported audit event kind: {kind}")
        options: dict[str, object] = {"since": since, "kind": kind}
        if organization_id is not None:
            options["organization_id"] = organization_id
        if until is not None:
            options["until"] = until
        events = self.accounting.audit_events(**options)
        # Preserve pre-cutover SQLite evidence alongside current PostgreSQL events.
        events.extend(self.security.audit_events(**options))
        if self.legacy_security is not None and self.legacy_security is not self.security:
            events.extend(self.legacy_security.audit_events(**options))
        unique = {
            (str(event.get("event_type")), str(event.get("id"))): event
            for event in events
        }
        return sorted(
            unique.values(),
            key=lambda event: (str(event["occurred_at"]), str(event["id"])),
        )


def gateway_store(config: GatewayConfig, *, environ: dict[str, str] | None = None):
    """Build the configured store; the DSN is read only from the named environment."""

    if config.usage_storage.backend == "sqlite":
        return UsageStore(config.database_path)
    organizations = tuple(
        sorted({identity.organization_id for identity in config.identities_by_actor.values()})
    )
    dsn = postgres_dsn_from_env(
        environ,
        dsn_env=config.usage_storage.postgres_dsn_env,
    )
    verify_runtime_policy_projection(
        config,
        dsn,
        schema=config.usage_storage.postgres_schema,
        runtime_role=config.usage_storage.postgres_runtime_role,
    )
    accounting = PostgresUsageStore(
        dsn,
        organization_ids=organizations,
        schema=config.usage_storage.postgres_schema,
        runtime_role=config.usage_storage.postgres_runtime_role,
    )
    security = PostgresSecurityStore(
        dsn,
        organization_ids=organizations,
        schema=config.usage_storage.postgres_schema,
        runtime_role=config.usage_storage.postgres_runtime_role,
    )
    return GatewayStoreRouter(
        accounting,
        security,
        legacy_security=UsageStore(config.database_path),
    )
