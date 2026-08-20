"""Explicit accounting/security persistence routing during PostgreSQL adoption."""

from __future__ import annotations

from typing import Any

from .config import GatewayConfig
from .postgres import postgres_dsn_from_env
from .postgres_usage_store import PostgresUsageStore
from .store import UsageStore


_ACCOUNTING_METHODS = {
    "record",
    "import_provider_cost_report",
    "reconcile_provider_costs",
    "reserve_budget",
    "release_budget_reservation",
    "refresh_budget_reservation",
    "active_budget_reservations",
    "monthly_totals",
    "summary_rows",
    "report_rows",
    "record_admin_usage_read",
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
    """Route named store operations without hiding a split persistence boundary."""

    backend = "split-postgresql-accounting-sqlite-security"

    def __init__(self, accounting: PostgresUsageStore, security: UsageStore):
        self.accounting = accounting
        self.security = security
        self.security_database_path = security.path

    def __getattr__(self, name: str) -> Any:
        if name in _ACCOUNTING_METHODS:
            return getattr(self.accounting, name)
        if name in _SECURITY_METHODS:
            return getattr(self.security, name)
        raise AttributeError(name)

    def audit_events(self, *, since: str, kind: str = "all") -> list[dict[str, object]]:
        if kind not in {"all", "usage", "security"}:
            raise ValueError(f"Unsupported audit event kind: {kind}")
        events = self.accounting.audit_events(since=since, kind=kind)
        # Preserve pre-cutover SQLite evidence as well as current PostgreSQL events.
        events.extend(self.security.audit_events(since=since, kind=kind))
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
    accounting = PostgresUsageStore(
        postgres_dsn_from_env(
            environ,
            dsn_env=config.usage_storage.postgres_dsn_env,
        ),
        organization_ids=organizations,
        schema=config.usage_storage.postgres_schema,
        runtime_role=config.usage_storage.postgres_runtime_role,
    )
    return GatewayStoreRouter(accounting, UsageStore(config.database_path))
