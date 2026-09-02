"""Resolve the v1 usage repository and compose separately owned repositories."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Generic, Mapping, Protocol, TypeVar

from ._persistence import (
    ProviderReliabilityRepository,
    ProviderReliabilityTotals,
    RequestAttempt,
    ReservationScope,
    UsageRepository,
    WorkBudgetContext,
    WorkBudgetRequestRepository,
)
from .config import GatewayConfig, Identity
from .finance_attempts import (
    ConfiguredRateCardBinding,
    ConfiguredRouteEstimate,
    NativeUsageObservation,
)
from .postgres import PostgresConnectionPool, PostgresStorageError
from .postgres_usage_store import PostgresUsageStore
from .provider_reliability import ProviderAttemptMetrics, ProviderFailoverContext
from .store import UsageStore


RepositoryT = TypeVar("RepositoryT")
RepositoryT_co = TypeVar("RepositoryT_co", covariant=True)


class RepositoryFactory(Protocol[RepositoryT_co]):
    """Construct one repository with explicit configuration and a borrowed pool."""

    def __call__(
        self,
        config: GatewayConfig,
        *,
        environ: Mapping[str, str] | None = None,
        connection_pool: PostgresConnectionPool | None = None,
        read_only: bool = False,
    ) -> RepositoryT_co: ...


class _WorkBudgetRequestBegin(Protocol):
    def __call__(
        self,
        *,
        identity: Identity,
        client: str,
        protocol: str,
        requested_model: str,
        resolved_alias: str | None,
        upstream_model: str | None,
        policy_version: str,
        policy_action: str,
        redaction_count: int,
        redaction_rules: tuple[str, ...],
        scopes: tuple[ReservationScope, ...],
        reserved_tokens: int,
        reserved_cost_microusd: int,
        ttl_seconds: int,
        work_budget: WorkBudgetContext | None,
    ) -> RequestAttempt: ...


class _ProviderReliabilityBegin(Protocol):
    def __call__(
        self,
        *,
        identity: Identity,
        client: str,
        protocol: str,
        requested_model: str,
        resolved_alias: str | None,
        upstream_model: str | None,
        policy_version: str,
        policy_action: str,
        redaction_count: int,
        redaction_rules: tuple[str, ...],
        scopes: tuple[ReservationScope, ...],
        reserved_tokens: int,
        reserved_cost_microusd: int,
        ttl_seconds: int,
        work_budget: WorkBudgetContext | None,
        provider_failover: ProviderFailoverContext | None,
        configured_rate_card: ConfiguredRateCardBinding | None = None,
    ) -> RequestAttempt: ...


class _ProviderReliabilityFinalize(Protocol):
    def __call__(
        self,
        *,
        attempt: RequestAttempt,
        organization_id: str,
        status: str,
        provider_reported_model: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        reasoning_tokens: int = 0,
        cost_microusd: int = 0,
        provider_request_id: str | None = None,
        provider_metrics: ProviderAttemptMetrics,
        finance_observation: NativeUsageObservation | None = None,
        configured_estimate: ConfiguredRouteEstimate | None = None,
    ) -> None: ...


class _ProviderReliabilityMarkUnknown(Protocol):
    def __call__(
        self,
        *,
        attempt: RequestAttempt,
        organization_id: str,
        reason_code: str,
        provider_metrics: ProviderAttemptMetrics,
        finance_observation: NativeUsageObservation | None = None,
    ) -> bool: ...


class _ProviderReliabilityTotalsRead(Protocol):
    def __call__(
        self,
        *,
        actor_id: str,
        organization_id: str,
    ) -> ProviderReliabilityTotals: ...


@dataclass(frozen=True, repr=False)
class WorkBudgetRequestAdapter:
    """Typed bridge to the adapters' private atomic v1.1 transaction."""

    _begin: _WorkBudgetRequestBegin

    def begin_request_attempt(
        self,
        *,
        identity: Identity,
        client: str,
        protocol: str,
        requested_model: str,
        resolved_alias: str | None,
        upstream_model: str | None,
        policy_version: str,
        policy_action: str,
        redaction_count: int,
        redaction_rules: tuple[str, ...],
        scopes: tuple[ReservationScope, ...],
        reserved_tokens: int,
        reserved_cost_microusd: int,
        ttl_seconds: int,
        work_budget: WorkBudgetContext,
    ) -> RequestAttempt:
        return self._begin(
            identity=identity,
            client=client,
            protocol=protocol,
            requested_model=requested_model,
            resolved_alias=resolved_alias,
            upstream_model=upstream_model,
            policy_version=policy_version,
            policy_action=policy_action,
            redaction_count=redaction_count,
            redaction_rules=redaction_rules,
            scopes=scopes,
            reserved_tokens=reserved_tokens,
            reserved_cost_microusd=reserved_cost_microusd,
            ttl_seconds=ttl_seconds,
            work_budget=work_budget,
        )


@dataclass(frozen=True, repr=False)
class ProviderReliabilityAdapter:
    """Typed bridge to built-in atomic provider-reliability transactions."""

    _begin: _ProviderReliabilityBegin
    _finalize: _ProviderReliabilityFinalize
    _mark_unknown: _ProviderReliabilityMarkUnknown
    _read_totals: _ProviderReliabilityTotalsRead

    def begin_request_attempt(
        self,
        *,
        identity: Identity,
        client: str,
        protocol: str,
        requested_model: str,
        resolved_alias: str | None,
        upstream_model: str | None,
        policy_version: str,
        policy_action: str,
        redaction_count: int,
        redaction_rules: tuple[str, ...],
        scopes: tuple[ReservationScope, ...],
        reserved_tokens: int,
        reserved_cost_microusd: int,
        ttl_seconds: int,
        work_budget: WorkBudgetContext | None,
        provider_failover: ProviderFailoverContext | None,
        configured_rate_card: ConfiguredRateCardBinding | None = None,
    ) -> RequestAttempt:
        return self._begin(
            identity=identity,
            client=client,
            protocol=protocol,
            requested_model=requested_model,
            resolved_alias=resolved_alias,
            upstream_model=upstream_model,
            policy_version=policy_version,
            policy_action=policy_action,
            redaction_count=redaction_count,
            redaction_rules=redaction_rules,
            scopes=scopes,
            reserved_tokens=reserved_tokens,
            reserved_cost_microusd=reserved_cost_microusd,
            ttl_seconds=ttl_seconds,
            work_budget=work_budget,
            provider_failover=provider_failover,
            configured_rate_card=configured_rate_card,
        )

    def finalize_request_attempt(
        self,
        *,
        attempt: RequestAttempt,
        organization_id: str,
        status: str,
        provider_reported_model: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        reasoning_tokens: int = 0,
        cost_microusd: int = 0,
        provider_request_id: str | None = None,
        provider_metrics: ProviderAttemptMetrics,
        finance_observation: NativeUsageObservation | None = None,
        configured_estimate: ConfiguredRouteEstimate | None = None,
    ) -> None:
        self._finalize(
            attempt=attempt,
            organization_id=organization_id,
            status=status,
            provider_reported_model=provider_reported_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            reasoning_tokens=reasoning_tokens,
            cost_microusd=cost_microusd,
            provider_request_id=provider_request_id,
            provider_metrics=provider_metrics,
            finance_observation=finance_observation,
            configured_estimate=configured_estimate,
        )

    def mark_request_attempt_outcome_unknown(
        self,
        *,
        attempt: RequestAttempt,
        organization_id: str,
        reason_code: str,
        provider_metrics: ProviderAttemptMetrics,
        finance_observation: NativeUsageObservation | None = None,
    ) -> bool:
        return self._mark_unknown(
            attempt=attempt,
            organization_id=organization_id,
            reason_code=reason_code,
            provider_metrics=provider_metrics,
            finance_observation=finance_observation,
        )

    def totals(
        self,
        *,
        actor_id: str,
        organization_id: str,
    ) -> ProviderReliabilityTotals:
        return self._read_totals(
            actor_id=actor_id,
            organization_id=organization_id,
        )


@dataclass(frozen=True, repr=False)
class RepositoryBundle(Generic[RepositoryT]):
    """Separate typed owners; no cross-repository transaction or lifecycle."""

    usage: UsageRepository
    portfolio: RepositoryT


def create_work_budget_request_repository(
    usage: UsageRepository,
) -> WorkBudgetRequestRepository | None:
    """Compose only the built-in adapters' typed atomic budget capability."""

    if type(usage) is UsageStore:
        return WorkBudgetRequestAdapter(usage._begin_request_attempt_with_work_budget)
    if type(usage) is PostgresUsageStore:
        return WorkBudgetRequestAdapter(usage._begin_request_attempt_with_work_budget)
    return None


def create_provider_reliability_repository(
    usage: UsageRepository,
) -> ProviderReliabilityRepository | None:
    """Compose only the built-in adapters' provider-reliability capability."""

    if type(usage) is UsageStore:
        return ProviderReliabilityAdapter(
            usage._begin_request_attempt_with_work_budget,
            usage._finalize_request_attempt_with_provider_metrics,
            usage._mark_request_attempt_outcome_unknown_with_provider_metrics,
            usage._provider_reliability_totals,
        )
    if type(usage) is PostgresUsageStore:
        return ProviderReliabilityAdapter(
            usage._begin_request_attempt_with_work_budget,
            usage._finalize_request_attempt_with_provider_metrics,
            usage._mark_request_attempt_outcome_unknown_with_provider_metrics,
            usage._provider_reliability_totals,
        )
    return None


def create_usage_store(
    config: GatewayConfig,
    *,
    environ: Mapping[str, str] | None = None,
    connection_pool: PostgresConnectionPool | None = None,
    read_only: bool = False,
    organization_ids: tuple[str, ...] | None = None,
) -> UsageRepository:
    """Return the configured store, never placing a PostgreSQL DSN in config.

    ``organization_ids`` lets an authenticated server-local directory provide
    the PostgreSQL tenant allowlist when identities are enrolled dynamically.
    Omitting it preserves the immutable configuration-owned allowlist.
    """

    storage = config.usage_storage
    audit_chain_maximum_anchor_age_seconds = (
        config.audit_chain.maximum_anchor_age_seconds if config.audit_chain is not None else None
    )
    if storage.backend == "sqlite":
        return UsageStore(
            config.database_path,
            audit_chain_maximum_anchor_age_seconds=audit_chain_maximum_anchor_age_seconds,
            audit_chain_organization_ids=config.organization_ids,
            read_only=read_only,
        )
    if storage.backend != "postgresql":  # Configuration parsing prevents this path.
        raise PostgresStorageError("storage_backend_unsupported")
    environment = os.environ if environ is None else environ
    dsn = environment.get(storage.postgres_dsn_env, "")
    if not dsn:
        raise PostgresStorageError("postgres_dsn_unavailable")
    return PostgresUsageStore(
        dsn,
        organization_ids=(
            config.organization_ids if organization_ids is None else organization_ids
        ),
        schema=storage.postgres_schema,
        runtime_role=storage.postgres_runtime_role,
        connection_pool=connection_pool,
        audit_chain_maximum_anchor_age_seconds=audit_chain_maximum_anchor_age_seconds,
    )


def create_repository_bundle(
    config: GatewayConfig,
    *,
    portfolio_factory: RepositoryFactory[RepositoryT],
    environ: Mapping[str, str] | None = None,
    connection_pool: PostgresConnectionPool | None = None,
    read_only: bool = False,
    usage_organization_ids: tuple[str, ...] | None = None,
) -> RepositoryBundle[RepositoryT]:
    """Compose an explicitly supplied owner beside the unchanged v1 usage ledger.

    This helper registers no default portfolio implementation; the feature
    entry point supplies its separately gated factory explicitly. Each
    repository owns its SQL and transactions. A shared pool is caller-owned,
    including when either factory fails. Only the factories acquire their
    repository connections; this helper owns no transaction or pool lifecycle
    and promises no cross-repository atomicity.
    The legacy create_usage_store path does not call this composition helper.
    """

    if usage_organization_ids is None:
        usage = create_usage_store(
            config,
            environ=environ,
            connection_pool=connection_pool,
            read_only=read_only,
        )
    else:
        usage = create_usage_store(
            config,
            environ=environ,
            connection_pool=connection_pool,
            read_only=read_only,
            organization_ids=usage_organization_ids,
        )
    portfolio = portfolio_factory(
        config, environ=environ, connection_pool=connection_pool, read_only=read_only,
    )
    return RepositoryBundle(usage=usage, portfolio=portfolio)


def create_postgres_runtime_pool(
    config: GatewayConfig,
    *,
    environ: Mapping[str, str] | None = None,
) -> PostgresConnectionPool | None:
    """Build the long-running gateway pool only for the runtime credential.

    Operators use a separate direct migration credential, and policy-control
    commands retain their independently authenticated service boundary.
    """

    storage = config.usage_storage
    if storage.backend == "sqlite":
        return None
    if storage.backend != "postgresql":  # Configuration parsing prevents this path.
        raise PostgresStorageError("storage_backend_unsupported")
    environment = os.environ if environ is None else environ
    dsn = environment.get(storage.postgres_dsn_env, "")
    if not dsn:
        raise PostgresStorageError("postgres_dsn_unavailable")
    return PostgresConnectionPool(dsn, settings=storage.postgres_pool)


def postgres_migration_dsn(
    config: GatewayConfig,
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Return only the separately configured operator migration DSN."""

    if config.usage_storage.backend != "postgresql":
        raise PostgresStorageError("storage_backend_unsupported")
    environment = os.environ if environ is None else environ
    dsn = environment.get(config.usage_storage.postgres_migration_dsn_env, "")
    if not dsn:
        raise PostgresStorageError("postgres_migration_dsn_unavailable")
    return dsn
