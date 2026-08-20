"""Secret-free configuration-seeded PostgreSQL policy projection."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Iterator

from .config import (
    ContextInjectionPolicy,
    DLPPolicyOverlay,
    DLPRuleConfig,
    GatewayConfig,
    ModelUsageLimit,
    Policy,
)
from .identity_projection import configured_organization_ids
from .postgres import (
    DEFAULT_POSTGRES_RUNTIME_ROLE,
    DEFAULT_POSTGRES_SCHEMA,
    PostgresStorageError,
    TenantContext,
    _open_connection,
    tenant_transaction,
    validate_postgres_identifier,
)


@dataclass(frozen=True)
class PolicySyncResult:
    organizations: int
    changed_organizations: int

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "hormuz.policy-sync.v1",
            "organizations": self.organizations,
            "changed_organizations": self.changed_organizations,
        }


def _sorted_optional(values: tuple[str, ...] | None) -> list[str] | None:
    return None if values is None else sorted(set(values))


def _context_policy(value: ContextInjectionPolicy) -> dict[str, object]:
    return {
        "mode": value.mode,
        "allowed_clients": _sorted_optional(value.allowed_clients),
        "allowed_models": _sorted_optional(value.allowed_models),
        "allowed_repositories": _sorted_optional(value.allowed_repositories),
        "max_classification": value.max_classification,
        "token_budget": value.token_budget,
        "max_items": value.max_items,
    }


def _model_limit(value: ModelUsageLimit) -> dict[str, object]:
    return {
        "monthly_token_limit": value.monthly_token_limit,
        "monthly_budget_usd": value.monthly_budget_usd,
        "per_actor_monthly_token_limit": value.per_actor_monthly_token_limit,
        "per_actor_monthly_budget_usd": value.per_actor_monthly_budget_usd,
    }


def _policy(value: Policy) -> dict[str, object]:
    return {
        "allowed_clients": _sorted_optional(value.allowed_clients),
        "allowed_models": _sorted_optional(value.allowed_models),
        "fallback_model": value.fallback_model,
        "fallback_models": dict(sorted((value.fallback_models or {}).items())),
        "max_output_tokens": value.max_output_tokens,
        "monthly_token_limit": value.monthly_token_limit,
        "monthly_budget_usd": value.monthly_budget_usd,
        "per_actor_monthly_budget_usd": value.per_actor_monthly_budget_usd,
        "model_limits": {
            alias: _model_limit(limit)
            for alias, limit in sorted(value.model_limits.items())
        },
        "context_injection": _context_policy(value.context_injection),
    }


def _rule(value: DLPRuleConfig) -> dict[str, object]:
    # exact_values and the resolved fingerprint key are intentionally absent.
    return {
        "rule_id": value.rule_id,
        "category": value.category,
        "confidence": value.confidence,
        "action": value.action,
        "providers": sorted(set(value.providers)),
        "models": sorted(set(value.models)),
        "values_env": value.values_env,
    }


def _overlay(value: DLPPolicyOverlay) -> dict[str, object]:
    return {
        "policy_version": value.policy_version,
        "rules": [_rule(rule) for rule in sorted(value.rules, key=lambda item: item.rule_id)],
    }


def policy_projection(config: GatewayConfig, organization_id: str) -> dict[str, object]:
    organizations = configured_organization_ids(config)
    if organization_id not in organizations:
        raise PostgresStorageError("policy_projection_tenant_not_configured")
    identities = tuple(
        identity
        for identity in config.identities_by_actor.values()
        if identity.organization_id == organization_id
    )
    actor_ids = {identity.actor_id for identity in identities}
    team_ids = {identity.team_id for identity in identities}
    return {
        "schema": "hormuz.policy-projection.v1",
        "organization_id": organization_id,
        "model_routes": {
            alias: {
                "protocol": route.protocol,
                "upstream_model": route.upstream_model,
                "rate_card_version": route.rate_card_version,
                "currency": route.currency,
                "input_cost_per_million": route.input_cost_per_million,
                "cache_read_cost_per_million": route.cache_read_cost_per_million,
                "cache_write_cost_per_million": route.cache_write_cost_per_million,
                "output_cost_per_million": route.output_cost_per_million,
            }
            for alias, route in sorted(config.model_routes.items())
        },
        "organization_policy": _policy(config.organization_policy),
        "team_policies": {
            team_id: _policy(config.team_policies[team_id])
            for team_id in sorted(team_ids & set(config.team_policies))
        },
        "actor_policies": {
            actor_id: _policy(config.actor_policies[actor_id])
            for actor_id in sorted(actor_ids & set(config.actor_policies))
        },
        "secret_controls": {
            "mode": config.secret_controls.mode,
            "builtins": config.secret_controls.builtins,
            "custom_secret_envs": sorted(set(config.secret_controls.custom_secret_envs)),
        },
        "dlp_controls": {
            "policy_version": config.dlp_controls.policy_version,
            "rules": [
                _rule(rule)
                for rule in sorted(config.dlp_controls.rules, key=lambda item: item.rule_id)
            ],
            "approval": {
                "enabled": config.dlp_controls.approval.enabled,
                "fingerprint_key_env": config.dlp_controls.approval.fingerprint_key_env,
                "ttl_seconds": config.dlp_controls.approval.ttl_seconds,
            },
        },
        "team_dlp_overlays": {
            team_id: _overlay(config.team_dlp_overlays[team_id])
            for team_id in sorted(team_ids & set(config.team_dlp_overlays))
        },
        "actor_dlp_overlays": {
            actor_id: _overlay(config.actor_dlp_overlays[actor_id])
            for actor_id in sorted(actor_ids & set(config.actor_dlp_overlays))
        },
    }


def policy_projection_sha256(value: dict[str, object]) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@contextmanager
def _owner_policy_transaction(
    connection: object,
    *,
    schema: str,
    organization_id: str,
) -> Iterator[object]:
    quoted_schema = '"' + schema + '"'
    try:
        with connection.transaction():  # type: ignore[attr-defined]
            with connection.cursor() as cursor:  # type: ignore[attr-defined]
                cursor.execute(
                    "SELECT pg_get_userbyid(nspowner), current_user "
                    "FROM pg_namespace WHERE nspname = %s",
                    (schema,),
                )
                owner = cursor.fetchone()
                if not isinstance(owner, (tuple, list)) or len(owner) != 2 or owner[0] != owner[1]:
                    raise PostgresStorageError("policy_sync_role_not_schema_owner")
                cursor.execute(
                    "SELECT set_config('hormuz.tenant_id', %s, true), "
                    "set_config('hormuz.principal_id', 'policy-sync', true), "
                    "set_config('hormuz.client_id', 'hormuz-cli', true), "
                    "set_config('hormuz.authorization_version', '1', true)",
                    (organization_id,),
                )
                if cursor.fetchone() != (
                    organization_id,
                    "policy-sync",
                    "hormuz-cli",
                    "1",
                ):
                    raise PostgresStorageError("policy_sync_scope_not_bound")
                cursor.execute(f"SET LOCAL search_path TO {quoted_schema}, pg_catalog")
            yield connection
    except PostgresStorageError:
        raise
    except Exception:
        raise PostgresStorageError("policy_sync_failed") from None


def sync_policy_projection(
    config: GatewayConfig,
    dsn: str,
    *,
    schema: str = DEFAULT_POSTGRES_SCHEMA,
    connect: object | None = None,
) -> PolicySyncResult:
    schema = validate_postgres_identifier(schema, "postgres_schema")
    organizations = configured_organization_ids(config)
    connection = _open_connection(dsn, connect)  # type: ignore[arg-type]
    changed = 0
    try:
        for organization_id in organizations:
            projection = policy_projection(config, organization_id)
            fingerprint = policy_projection_sha256(projection)
            serialized = json.dumps(
                projection,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
            now = datetime.now(timezone.utc)
            with _owner_policy_transaction(
                connection,
                schema=schema,
                organization_id=organization_id,
            ):
                with connection.cursor() as cursor:  # type: ignore[attr-defined]
                    cursor.execute(
                        "INSERT INTO tenants (tenant_id, display_name) VALUES (%s, %s) "
                        "ON CONFLICT (tenant_id) DO NOTHING",
                        (organization_id, organization_id),
                    )
                    cursor.execute(
                        "SELECT projection_sha256 FROM gateway_policy_projections "
                        "WHERE tenant_id = %s FOR UPDATE",
                        (organization_id,),
                    )
                    current = cursor.fetchone()
                    if current is not None and str(current[0]) == fingerprint:
                        continue
                    changed += 1
                    cursor.execute(
                        "INSERT INTO gateway_policy_projections "
                        "(tenant_id, projection_sha256, projection_json, applied_at) "
                        "VALUES (%s, %s, %s::jsonb, %s) "
                        "ON CONFLICT (tenant_id) DO UPDATE SET "
                        "projection_sha256 = EXCLUDED.projection_sha256, "
                        "projection_json = EXCLUDED.projection_json, "
                        "applied_at = EXCLUDED.applied_at",
                        (organization_id, fingerprint, serialized, now),
                    )
    finally:
        connection.close()
    return PolicySyncResult(len(organizations), changed)


def verify_runtime_policy_projection(
    config: GatewayConfig,
    dsn: str,
    *,
    schema: str = DEFAULT_POSTGRES_SCHEMA,
    runtime_role: str = DEFAULT_POSTGRES_RUNTIME_ROLE,
    connect: object | None = None,
) -> None:
    schema = validate_postgres_identifier(schema, "postgres_schema")
    runtime_role = validate_postgres_identifier(runtime_role, "runtime_role")
    connection = _open_connection(dsn, connect)  # type: ignore[arg-type]
    quoted_schema = '"' + schema + '"'
    try:
        for organization_id in configured_organization_ids(config):
            context = TenantContext(organization_id, "policy-verifier", "hormuz-startup", 1)
            with tenant_transaction(connection, context, runtime_role=runtime_role):
                with connection.cursor() as cursor:  # type: ignore[attr-defined]
                    cursor.execute(f"SET LOCAL search_path TO {quoted_schema}, pg_catalog")
                    cursor.execute(
                        "SELECT projection_sha256 FROM gateway_policy_projections "
                        "WHERE tenant_id = %s",
                        (organization_id,),
                    )
                    row = cursor.fetchone()
                    expected = policy_projection_sha256(
                        policy_projection(config, organization_id)
                    )
                    if row is None or str(row[0]) != expected:
                        raise PostgresStorageError("policy_projection_stale")
    finally:
        connection.close()
