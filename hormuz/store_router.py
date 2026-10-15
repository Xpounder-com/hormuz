"""Resolve the v1 usage repository and compose separately owned repositories."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

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
from .finance_account_binding import FinanceAccountCandidate, UnavailableFinance
from .provider_reliability import ProviderAttemptMetrics, ProviderFailoverContext
from .store import UsageStore


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
        finance_account: FinanceAccountCandidate | UnavailableFinance | None = None,
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
        finance_account: FinanceAccountCandidate | UnavailableFinance | None = None,
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
            finance_account=finance_account,
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


def create_work_budget_request_repository(
    usage: UsageRepository,
) -> WorkBudgetRequestRepository | None:
    """Compose only the built-in adapters' typed atomic budget capability."""

    if type(usage) is UsageStore:
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
    return None


def create_usage_store(config: GatewayConfig, *, read_only: bool = False) -> UsageRepository:
    """Open only the customer-local SQLite evidence store."""
    if config.usage_storage.backend != "sqlite":
        raise ValueError("router_scope_retired_configuration")
    return UsageStore(config.database_path, audit_chain_organization_ids=config.organization_ids,
                      read_only=read_only)
