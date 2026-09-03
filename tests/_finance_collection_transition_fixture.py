"""Populate every accepted domain before provider-collection transition probes."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hormuz.config import UsageStorageConfig
from hormuz.portfolio_config import PortfolioPrincipal
from hormuz.portfolio_repository import create_portfolio_repository
from hormuz.postgres import migrate_postgres
from hormuz.postgres_usage_store import PostgresUsageStore
from hormuz.provider_reliability import (
    ProviderAttemptMetrics,
    ProviderFailoverContext,
)
from hormuz.store import UsageStore
from hormuz.store_router import create_provider_reliability_repository

if __package__:
    from ._attribution_fixture import seed_attribution_metadata
    from ._finance_fixture import seed_finance
    from ._outcome_fixture import seed_outcome_metadata
    from ._portfolio_fixture import ADMIN, registry_config, seed_registry_metadata
    from ._registry_transition_fixture import seed_registry_ledger
else:
    from _attribution_fixture import seed_attribution_metadata
    from _finance_fixture import seed_finance
    from _outcome_fixture import seed_outcome_metadata
    from _portfolio_fixture import ADMIN, registry_config, seed_registry_metadata
    from _registry_transition_fixture import seed_registry_ledger


def _seed_budget(config, registry_writes, environment):
    repositories = create_portfolio_repository(config, environ=environment)
    repository = repositories.budgets
    if repository is None:
        raise RuntimeError("finance_collection_predecessor_budget_missing")
    scope = registry_writes[2][3][1]
    now = datetime.now(timezone.utc)
    principal = PortfolioPrincipal("acme", "alice", ("portfolio_admin",))
    plan = repository.create_plan(
        principal,
        {
            "schema_id": "hormuz.work-budget-plan-request",
            "schema_version": 1,
            "budget_plan_id": None,
            "expected_version": None,
            "work_scope": {
                "work_scope_id": scope["work_scope_id"],
                "version": scope["version"],
            },
            "window": {
                "start_at": (now - timedelta(days=1)).isoformat(
                    timespec="microseconds"
                ).replace("+00:00", "Z"),
                "end_at": (now + timedelta(days=1)).isoformat(
                    timespec="microseconds"
                ).replace("+00:00", "Z"),
            },
            "currency": "USD",
            "amount": "25",
            "allowed_models": None,
            "output_token_cap": None,
            "per_request_cost_cap": None,
            "reason_code": "created",
        },
    )
    active = repository.activate_plan(
        principal,
        plan["budget_plan_id"],
        {
            "schema_id": "hormuz.work-budget-plan-activation-request",
            "schema_version": 1,
            "version": plan["version"],
            "expected_active_version": None,
            "expected_activation_generation": 0,
            "reason_code": "accepted",
        },
    )
    return {
        "budget_plan_id": active["budget_plan_id"],
        "version": active["version"],
        "active_version": active["active_version"],
        "activation_generation": active["activation_generation"],
    }


def _seed_provider_reliability(store, identity):
    repository = create_provider_reliability_repository(store)
    if repository is None:
        raise RuntimeError("finance_collection_predecessor_reliability_missing")
    arguments = {
        "identity": identity,
        "client": "codex",
        "protocol": "openai",
        "requested_model": "synthetic",
        "resolved_alias": "synthetic",
        "upstream_model": "synthetic-primary",
        "policy_version": "finance-collection-predecessor-policy",
        "policy_action": "allowed",
        "redaction_count": 0,
        "redaction_rules": (),
        "scopes": (),
        "reserved_tokens": 10,
        "reserved_cost_microusd": 20,
        "ttl_seconds": 60,
    }
    original = store.begin_request_attempt(**arguments)
    repository.finalize_request_attempt(
        attempt=original,
        organization_id="acme",
        status="rate_limited",
        provider_metrics=ProviderAttemptMetrics(429, 1000, None, 1500, 0, 0),
    )
    failover = repository.begin_request_attempt(
        **{
            **arguments,
            "resolved_alias": "synthetic-failover",
            "upstream_model": "synthetic-secondary",
        },
        work_budget=None,
        provider_failover=ProviderFailoverContext(
            original_attempt_id=original.attempt_id,
            trigger_status=429,
            reason_code="provider_rate_limited",
        ),
    )
    repository.finalize_request_attempt(
        attempt=failover,
        organization_id="acme",
        status="succeeded",
        input_tokens=7,
        output_tokens=3,
        cost_microusd=11,
        provider_metrics=ProviderAttemptMetrics(200, 800, 1100, 1600, 12, 12),
    )
    return {
        "original_attempt_id": original.attempt_id,
        "failover_attempt_id": failover.attempt_id,
    }


def _seed(config, store, environment):
    seed_registry_ledger(store)
    registry_writes, _ = seed_registry_metadata(config, environ=environment)
    _, _, _, attribution_attempt_id = seed_attribution_metadata(
        config,
        environ=environment,
    )
    outcome = seed_outcome_metadata(config, environ=environment)
    finance = seed_finance(config, environ=environment)
    reliability = _seed_provider_reliability(
        store,
        config.identities_by_token[ADMIN],
    )
    budget = _seed_budget(config, registry_writes, environment)
    return {
        "attribution_attempt_id": attribution_attempt_id,
        "outcome_delivery_count": len(outcome["deliveries"]),
        "finance_receipt_id": finance.receipt_id,
        "budget": budget,
        "reliability": reliability,
    }


def seed_sqlite_collection_predecessor(path: Path):
    config = replace(registry_config(path.parent), database_path=path)
    store = UsageStore(path)
    return _seed(config, store, None)


def seed_postgres_collection_predecessor(
    *,
    owner_dsn: str,
    runtime_dsn: str,
    schema: str,
    runtime_role: str,
    policy_control_role: str,
    custody_control_role: str,
    custody_executor_role: str,
):
    migrate_postgres(
        owner_dsn,
        schema=schema,
        runtime_role=runtime_role,
        policy_control_role=policy_control_role,
        custody_control_role=custody_control_role,
        custody_executor_role=custody_executor_role,
    )
    config = replace(
        registry_config(Path("/unused/finance-collection-predecessor")),
        usage_storage=UsageStorageConfig(
            backend="postgresql",
            postgres_schema=schema,
            postgres_runtime_role=runtime_role,
        ),
    )
    store = PostgresUsageStore(
        runtime_dsn,
        schema=schema,
        runtime_role=runtime_role,
        organization_ids=("acme", "beta"),
    )
    return _seed(config, store, {"HORMUZ_POSTGRES_DSN": runtime_dsn})
