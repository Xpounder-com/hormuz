from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .config import GatewayConfig, Identity, ModelRoute, Policy
from .store import MonthlyTotals, ReservationScope


class AccountingStore(Protocol):
    def monthly_totals(
        self,
        *,
        organization_id: str | None = None,
        actor_id: str | None = None,
        team_id: str | None = None,
        model_alias: str | None = None,
    ) -> MonthlyTotals: ...

    def reserve_budget(
        self,
        *,
        identity: Identity,
        scopes: tuple[ReservationScope, ...],
        reserved_tokens: int,
        reserved_cost_microusd: int,
        ttl_seconds: int,
    ) -> str | None: ...


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    action: str
    reason: str
    requested_model: str
    resolved_alias: str | None
    route: ModelRoute | None
    max_output_tokens: int | None


@dataclass(frozen=True)
class ModelCatalogDecision:
    allowed: bool
    reason: str
    aliases: tuple[str, ...]


class PolicyEngine:
    def __init__(self, config: GatewayConfig, store: AccountingStore):
        self.config = config
        self.store = store

    def evaluate(
        self,
        *,
        identity: Identity,
        client: str,
        protocol: str,
        requested_model: str,
        requested_output_tokens: int | None,
    ) -> PolicyDecision:
        policy = self.config.resolved_policy(identity)

        client_denial = self._client_denial_reason(identity, policy, client)
        if client_denial is not None:
            return self._deny(requested_model, client_denial)

        budget_decision = self._check_limits(identity, requested_model)
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
                    requested_model,
                    f"Model {requested_model} is not allowed for {protocol}, and no compatible fallback is configured.",
                )
            selected_alias = fallback
            route = fallback_route
            action = "fallback"
            reason = f"Model {requested_model} is not allowed; routed to {fallback}."

        model_budget_decision = self._check_model_limits(
            identity,
            requested_model=requested_model,
            model_alias=selected_alias,
        )
        if model_budget_decision is not None:
            return model_budget_decision

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
        )

    def model_catalog(
        self,
        *,
        identity: Identity,
        client: str,
        protocol: str,
    ) -> ModelCatalogDecision:
        """Return statically authorized aliases without spending or reserving budget."""
        policy = self.config.resolved_policy(identity)
        client_denial = self._client_denial_reason(identity, policy, client)
        if client_denial is not None:
            return ModelCatalogDecision(
                allowed=False,
                reason=client_denial,
                aliases=(),
            )
        allowed_models = policy.allowed_models
        aliases = tuple(
            sorted(
                alias
                for alias, route in self.config.model_routes.items()
                if route.protocol == protocol
                and (allowed_models is None or alias in allowed_models)
            )
        )
        return ModelCatalogDecision(
            allowed=True,
            reason="Authorized model aliases resolved from effective policy.",
            aliases=aliases,
        )

    def reserve_budget(
        self,
        *,
        identity: Identity,
        model_alias: str,
        reserved_tokens: int,
        reserved_cost_microusd: int,
        ttl_seconds: int,
    ) -> str | None:
        organization = self.config.organization_policy
        team = self.config.team_policies.get(identity.team_id)
        actor = self.config.actor_policies.get(identity.actor_id)
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
        scopes.extend(self.model_limit_scopes(identity, model_alias=model_alias))
        return self.store.reserve_budget(
            identity=identity,
            scopes=tuple(scopes),
            reserved_tokens=reserved_tokens,
            reserved_cost_microusd=reserved_cost_microusd,
            ttl_seconds=ttl_seconds,
        )

    def _check_limits(self, identity: Identity, requested_model: str) -> PolicyDecision | None:
        actor_totals = self.store.monthly_totals(
            organization_id=identity.organization_id,
            actor_id=identity.actor_id,
        )
        scopes: list[tuple[str, object, MonthlyTotals]] = [
            (
                "organization",
                self.config.organization_policy,
                self.store.monthly_totals(organization_id=identity.organization_id),
            ),
        ]
        team_policy = self.config.team_policies.get(identity.team_id)
        if team_policy is not None:
            scopes.append(
                (
                    "team",
                    team_policy,
                    self.store.monthly_totals(
                        organization_id=identity.organization_id,
                        team_id=identity.team_id,
                    ),
                )
            )
        actor_policy = self.config.actor_policies.get(identity.actor_id)
        if actor_policy is not None:
            scopes.append(("employee", actor_policy, actor_totals))

        for scope_name, scope_policy, totals in scopes:
            if scope_policy.monthly_token_limit is not None and totals.total_tokens >= scope_policy.monthly_token_limit:
                return self._deny(requested_model, f"The {scope_name} monthly token limit has been reached.")
            if scope_policy.monthly_budget_usd is not None and totals.cost_usd >= scope_policy.monthly_budget_usd:
                return self._deny(requested_model, f"The {scope_name} monthly AI budget has been reached.")
            if (
                scope_policy.per_actor_monthly_budget_usd is not None
                and actor_totals.cost_usd >= scope_policy.per_actor_monthly_budget_usd
            ):
                return self._deny(requested_model, "The employee monthly AI budget has been reached.")
        return None

    def _check_model_limits(
        self,
        identity: Identity,
        *,
        requested_model: str,
        model_alias: str,
    ) -> PolicyDecision | None:
        for scope in self.model_limit_scopes(identity, model_alias=model_alias):
            totals = self.store.monthly_totals(
                organization_id=identity.organization_id,
                actor_id=scope.actor_id,
                team_id=scope.team_id,
                model_alias=model_alias,
            )
            if (
                scope.token_limit is not None
                and totals.total_tokens >= scope.token_limit
            ):
                return self._deny(
                    requested_model,
                    f"The {scope.name} monthly token limit has been reached.",
                )
            if (
                scope.cost_limit_microusd is not None
                and totals.cost_microusd >= scope.cost_limit_microusd
            ):
                return self._deny(
                    requested_model,
                    f"The {scope.name} monthly AI budget has been reached.",
                )
        return None

    def model_limit_scopes(
        self,
        identity: Identity,
        *,
        model_alias: str,
    ) -> tuple[ReservationScope, ...]:
        """Return every independently enforced limit for one routed alias."""

        scopes: list[ReservationScope] = []
        policies = (
            ("organization model", self.config.organization_policy, None, None),
            (
                "team model",
                self.config.team_policies.get(identity.team_id),
                None,
                identity.team_id,
            ),
            (
                "employee model",
                self.config.actor_policies.get(identity.actor_id),
                identity.actor_id,
                None,
            ),
        )
        for name, policy, actor_id, team_id in policies:
            if policy is None or (limit := policy.model_limits.get(model_alias)) is None:
                continue
            if name == "employee model":
                token_limit = _minimum_limit(
                    limit.monthly_token_limit,
                    limit.per_actor_monthly_token_limit,
                )
                budget_limit = _minimum_limit(
                    limit.monthly_budget_usd,
                    limit.per_actor_monthly_budget_usd,
                )
                scopes.append(
                    ReservationScope(
                        name=name,
                        actor_id=actor_id,
                        model_alias=model_alias,
                        token_limit=token_limit,
                        cost_limit_microusd=_usd_to_microusd(budget_limit),
                    )
                )
                continue
            if (
                limit.monthly_token_limit is not None
                or limit.monthly_budget_usd is not None
            ):
                scopes.append(
                    ReservationScope(
                        name=name,
                        team_id=team_id,
                        model_alias=model_alias,
                        token_limit=limit.monthly_token_limit,
                        cost_limit_microusd=_usd_to_microusd(
                            limit.monthly_budget_usd
                        ),
                    )
                )
            if (
                limit.per_actor_monthly_token_limit is not None
                or limit.per_actor_monthly_budget_usd is not None
            ):
                scopes.append(
                    ReservationScope(
                        name=f"{name} per-employee",
                        actor_id=identity.actor_id,
                        team_id=team_id,
                        model_alias=model_alias,
                        token_limit=limit.per_actor_monthly_token_limit,
                        cost_limit_microusd=_usd_to_microusd(
                            limit.per_actor_monthly_budget_usd
                        ),
                    )
                )
        return tuple(scopes)

    @staticmethod
    def _client_denial_reason(identity: Identity, policy: Policy, client: str) -> str | None:
        if identity.allowed_clients and client not in identity.allowed_clients:
            return f"Identity is not authorized to use client {client}."
        allowed_clients = policy.allowed_clients
        if allowed_clients is not None and client not in allowed_clients:
            return f"Policy does not allow client {client}."
        return None

    @staticmethod
    def _deny(requested_model: str, reason: str) -> PolicyDecision:
        return PolicyDecision(
            allowed=False,
            action="denied",
            reason=reason,
            requested_model=requested_model,
            resolved_alias=None,
            route=None,
            max_output_tokens=None,
        )


def _usd_to_microusd(value: float | None) -> int | None:
    return None if value is None else max(0, round(value * 1_000_000))


def _minimum_limit(
    first: int | float | None,
    second: int | float | None,
) -> int | float | None:
    if first is None:
        return second
    if second is None:
        return first
    return min(first, second)
