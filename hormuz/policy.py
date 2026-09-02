from __future__ import annotations

from dataclasses import dataclass

from ._persistence import ProviderReliabilityRepository, WorkBudgetRequestRepository
from .config import GatewayConfig, Identity, ModelRoute, PolicyAnalysisContext
from .finance_attempts import ConfiguredRateCardBinding
from .policy_document import PolicySnapshot
from .policy_runtime import PolicyRuntime
from .provider_reliability import ProviderFailoverContext
from .store import (
    MonthlyTotals,
    RequestAttempt,
    ReservationScope,
    StorageSchemaError,
    UsageRepository,
    WorkBudgetContext,
)


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    action: str
    reason: str
    requested_model: str
    resolved_alias: str | None
    route: ModelRoute | None
    max_output_tokens: int | None
    policy_version: str
    snapshot: PolicySnapshot


class PolicyEngine:
    def __init__(
        self,
        config: GatewayConfig | PolicyAnalysisContext,
        store: UsageRepository,
        *,
        policy_runtime: PolicyRuntime | None = None,
        work_budget_requests: WorkBudgetRequestRepository | None = None,
        provider_reliability_requests: ProviderReliabilityRepository | None = None,
    ):
        self.config = config
        self.store = store
        self._work_budget_requests = work_budget_requests
        self._provider_reliability_requests = provider_reliability_requests
        # Explicit-snapshot analysis must not initialize managed policy
        # storage. The gateway still resolves this property during startup,
        # while compare/preview/evaluate paths can remain strictly read-only.
        self._policy_runtime = policy_runtime

    @property
    def policy_runtime(self) -> PolicyRuntime:
        runtime = self._policy_runtime
        if runtime is None:
            runtime = PolicyRuntime(self.config)
            self._policy_runtime = runtime
        return runtime

    @policy_runtime.setter
    def policy_runtime(self, value: PolicyRuntime) -> None:
        self._policy_runtime = value

    def evaluate(
        self,
        *,
        identity: Identity,
        client: str,
        protocol: str,
        requested_model: str,
        requested_output_tokens: int | None,
        snapshot: PolicySnapshot | None = None,
    ) -> PolicyDecision:
        snapshot = snapshot or self.policy_runtime.snapshot_for(identity)
        policy = snapshot.effective_policy

        if identity.allowed_clients and client not in identity.allowed_clients:
            return self._deny(snapshot, requested_model, f"Identity is not authorized to use client {client}.")
        if policy.allowed_clients is not None and client not in policy.allowed_clients:
            return self._deny(snapshot, requested_model, f"Policy does not allow client {client}.")

        budget_decision = self._check_limits(snapshot, identity, requested_model)
        if budget_decision is not None:
            return budget_decision

        selected_alias = requested_model
        action = "allowed"
        reason = "Requested model is allowed by policy."
        route = self.config.model_routes.get(selected_alias)
        allowed_models = policy.allowed_models
        route_is_usable = route is not None and route.protocol == protocol
        model_is_allowed = allowed_models is None or selected_alias in allowed_models

        if not route_is_usable or not model_is_allowed:
            fallback = (policy.fallback_models or {}).get(protocol) or policy.fallback_model
            fallback_route = self.config.model_routes.get(fallback) if fallback else None
            fallback_allowed = allowed_models is None or fallback in allowed_models
            if fallback_route is None or fallback_route.protocol != protocol or not fallback_allowed:
                return self._deny(
                    snapshot,
                    requested_model,
                    f"Model {requested_model} is not allowed for {protocol}, and no compatible fallback is configured.",
                )
            selected_alias = fallback
            route = fallback_route
            action = "fallback"
            reason = f"Model {requested_model} is not allowed; routed to {fallback}."

        output_cap = policy.max_output_tokens
        if output_cap is not None and requested_output_tokens is not None and requested_output_tokens > output_cap:
            action = "capped" if action == "allowed" else f"{action}+capped"
            reason = f"{reason} Output limit reduced from {requested_output_tokens} to {output_cap}."

        return PolicyDecision(
            allowed=True,
            action=action,
            reason=reason,
            requested_model=requested_model,
            resolved_alias=selected_alias,
            route=route,
            max_output_tokens=output_cap,
            policy_version=snapshot.policy_version,
            snapshot=snapshot,
        )

    def reserve_budget(
        self,
        *,
        identity: Identity,
        decision: PolicyDecision,
        reserved_tokens: int,
        reserved_cost_microusd: int,
        ttl_seconds: int,
    ) -> str | None:
        return self.store.reserve_budget(
            identity=identity,
            scopes=self.budget_scopes(identity=identity, decision=decision),
            reserved_tokens=reserved_tokens,
            reserved_cost_microusd=reserved_cost_microusd,
            ttl_seconds=ttl_seconds,
        )

    def begin_request_attempt(
        self,
        *,
        identity: Identity,
        decision: PolicyDecision,
        client: str,
        protocol: str,
        policy_action: str,
        redaction_count: int,
        redaction_rules: tuple[str, ...],
        reserved_tokens: int,
        reserved_cost_microusd: int,
        ttl_seconds: int,
        work_budget: WorkBudgetContext | None = None,
        provider_failover: ProviderFailoverContext | None = None,
        configured_rate_card: ConfiguredRateCardBinding | None = None,
    ) -> RequestAttempt:
        upstream_model = decision.route.upstream_model if decision.route is not None else None
        scopes = self.budget_scopes(identity=identity, decision=decision)
        if provider_failover is not None or configured_rate_card is not None:
            if self._provider_reliability_requests is None:
                raise StorageSchemaError("storage_schema_partial_upgrade")
            return self._provider_reliability_requests.begin_request_attempt(
                identity=identity,
                client=client,
                protocol=protocol,
                requested_model=decision.requested_model,
                resolved_alias=decision.resolved_alias,
                upstream_model=upstream_model,
                policy_version=decision.policy_version,
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
        if work_budget is None:
            # The built-in v1 method still enters the atomic budget transaction
            # with missing attribution, so an effective work plan fails closed.
            return self.store.begin_request_attempt(
                identity=identity,
                client=client,
                protocol=protocol,
                requested_model=decision.requested_model,
                resolved_alias=decision.resolved_alias,
                upstream_model=upstream_model,
                policy_version=decision.policy_version,
                policy_action=policy_action,
                redaction_count=redaction_count,
                redaction_rules=redaction_rules,
                scopes=scopes,
                reserved_tokens=reserved_tokens,
                reserved_cost_microusd=reserved_cost_microusd,
                ttl_seconds=ttl_seconds,
            )
        if self._work_budget_requests is None:
            raise StorageSchemaError("storage_schema_partial_upgrade")
        return self._work_budget_requests.begin_request_attempt(
            identity=identity,
            client=client,
            protocol=protocol,
            requested_model=decision.requested_model,
            resolved_alias=decision.resolved_alias,
            upstream_model=upstream_model,
            policy_version=decision.policy_version,
            policy_action=policy_action,
            redaction_count=redaction_count,
            redaction_rules=redaction_rules,
            scopes=scopes,
            reserved_tokens=reserved_tokens,
            reserved_cost_microusd=reserved_cost_microusd,
            ttl_seconds=ttl_seconds,
            work_budget=work_budget,
        )

    def operational_failover(self, decision: PolicyDecision) -> PolicyDecision | None:
        """Return the one configured alternate only when effective policy allows it."""

        route = decision.route
        if route is None or route.failover_alias is None:
            return None
        failover_route = self.config.model_routes.get(route.failover_alias)
        if failover_route is None or failover_route.protocol != route.protocol:
            # GatewayConfig rejects this, while the analysis projection keeps
            # this guard so hand-constructed contexts still fail closed.
            return None
        allowed_models = decision.snapshot.effective_policy.allowed_models
        if allowed_models is not None and failover_route.alias not in allowed_models:
            return None
        return PolicyDecision(
            allowed=True,
            action=decision.action,
            reason=(
                f"{decision.reason} Operational failover may route to "
                f"{failover_route.alias} after an explicit capacity rejection."
            ),
            requested_model=decision.requested_model,
            resolved_alias=failover_route.alias,
            route=failover_route,
            max_output_tokens=decision.max_output_tokens,
            policy_version=decision.policy_version,
            snapshot=decision.snapshot,
        )

    @staticmethod
    def budget_scopes(*, identity: Identity, decision: PolicyDecision) -> tuple[ReservationScope, ...]:
        organization = decision.snapshot.organization_policy
        team = decision.snapshot.team_policy
        actor = decision.snapshot.actor_policy
        per_actor_cost_caps = [
            policy.per_actor_monthly_budget_usd
            for policy in (organization, team, actor)
            if policy is not None and policy.per_actor_monthly_budget_usd is not None
        ]
        if actor is not None and actor.monthly_budget_usd is not None:
            per_actor_cost_caps.append(actor.monthly_budget_usd)
        actor_cost_limit = min(per_actor_cost_caps) if per_actor_cost_caps else None
        scopes = [
            ReservationScope(
                name="organization",
                token_limit=organization.monthly_token_limit,
                cost_limit_microusd=_usd_to_microusd(organization.monthly_budget_usd),
            )
        ]
        if team is not None:
            scopes.append(
                ReservationScope(
                    name="team",
                    team_id=identity.team_id,
                    token_limit=team.monthly_token_limit,
                    cost_limit_microusd=_usd_to_microusd(team.monthly_budget_usd),
                )
            )
        scopes.append(
            ReservationScope(
                name="employee",
                actor_id=identity.actor_id,
                token_limit=actor.monthly_token_limit if actor is not None else None,
                cost_limit_microusd=_usd_to_microusd(actor_cost_limit),
            )
        )
        return tuple(scopes)

    def _check_limits(
        self,
        snapshot: PolicySnapshot,
        identity: Identity,
        requested_model: str,
    ) -> PolicyDecision | None:
        actor_totals = self.store.monthly_totals(
            actor_id=identity.actor_id,
            organization_id=identity.organization_id,
        )
        scopes: list[tuple[str, object, MonthlyTotals]] = [
            (
                "organization",
                snapshot.organization_policy,
                self.store.monthly_totals(organization_id=identity.organization_id),
            ),
        ]
        team_policy = snapshot.team_policy
        if team_policy is not None:
            scopes.append(
                (
                    "team",
                    team_policy,
                    self.store.monthly_totals(
                        team_id=identity.team_id,
                        organization_id=identity.organization_id,
                    ),
                )
            )
        actor_policy = snapshot.actor_policy
        if actor_policy is not None:
            scopes.append(("employee", actor_policy, actor_totals))

        for scope_name, scope_policy, totals in scopes:
            if scope_policy.monthly_token_limit is not None and totals.total_tokens >= scope_policy.monthly_token_limit:
                return self._deny(snapshot, requested_model, f"The {scope_name} monthly token limit has been reached.")
            if scope_policy.monthly_budget_usd is not None and totals.cost_usd >= scope_policy.monthly_budget_usd:
                return self._deny(snapshot, requested_model, f"The {scope_name} monthly AI budget has been reached.")
            if (
                scope_policy.per_actor_monthly_budget_usd is not None
                and actor_totals.cost_usd >= scope_policy.per_actor_monthly_budget_usd
            ):
                return self._deny(snapshot, requested_model, "The employee monthly AI budget has been reached.")
        return None

    def _deny(self, snapshot: PolicySnapshot, requested_model: str, reason: str) -> PolicyDecision:
        return PolicyDecision(
            allowed=False,
            action="denied",
            reason=reason,
            requested_model=requested_model,
            resolved_alias=None,
            route=None,
            max_output_tokens=None,
            policy_version=snapshot.policy_version,
            snapshot=snapshot,
        )


def _usd_to_microusd(value: float | None) -> int | None:
    return None if value is None else max(0, round(value * 1_000_000))
