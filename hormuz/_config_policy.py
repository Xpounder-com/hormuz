"""Policy and policy-control configuration construction ownership."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from ._config_values import (
    _boolean,
    _environment_name,
    _object,
    _optional_integer,
    _optional_number,
    _optional_string,
    _optional_string_map,
    _optional_string_tuple,
    _postgres_identifier,
    _string,
    _string_tuple,
)
from .config import (
    BreakGlassConfig,
    ConfigError,
    Policy,
    PolicyControlConfig,
    SecretControls,
    UsageStorageConfig,
)


@dataclass(frozen=True)
class PolicyControlConstruction:
    config: PolicyControlConfig
    bootstrap_administrators_raw: tuple[Any, ...]


@dataclass(frozen=True)
class PolicyConstruction:
    secret_controls: SecretControls
    organization_policy: Policy
    team_policies: dict[str, Policy]
    actor_policies: dict[str, Policy]


def build_policy_control_domain(
    raw: dict[str, Any],
    *,
    usage_storage: UsageStorageConfig,
) -> PolicyControlConstruction:
    policy_control_raw = _object(raw.get("policy_control", {}), "policy_control")
    unsupported_policy_control_fields = set(policy_control_raw).difference(
        {
            "mode",
            "bootstrap_administrators",
            "postgres_control_dsn_env",
            "postgres_control_role",
            "break_glass",
        }
    )
    if unsupported_policy_control_fields:
        raise ConfigError(
            "policy_control contains unsupported fields: "
            + ", ".join(sorted(str(field) for field in unsupported_policy_control_fields))
        )
    policy_control_mode = _string(policy_control_raw.get("mode", "local"), "policy_control.mode")
    if policy_control_mode not in {"local", "postgresql"}:
        raise ConfigError("policy_control.mode must be local or postgresql")
    policy_control_dsn_env = _environment_name(
        policy_control_raw.get("postgres_control_dsn_env", "HORMUZ_POLICY_CONTROL_DSN"),
        "policy_control.postgres_control_dsn_env",
    )
    policy_control_role = _postgres_identifier(
        policy_control_raw.get("postgres_control_role", "hormuz_policy_control"),
        "policy_control.postgres_control_role",
    )
    break_glass_raw = _object(policy_control_raw.get("break_glass", {}), "policy_control.break_glass")
    unsupported_break_glass_fields = set(break_glass_raw).difference({"enabled", "token_env"})
    if unsupported_break_glass_fields:
        raise ConfigError(
            "policy_control.break_glass contains unsupported fields: "
            + ", ".join(sorted(str(field) for field in unsupported_break_glass_fields))
        )
    break_glass = BreakGlassConfig(
        enabled=_boolean(break_glass_raw.get("enabled", False), "policy_control.break_glass.enabled"),
        token_env=_environment_name(
            break_glass_raw.get("token_env", "HORMUZ_POLICY_BREAK_GLASS_TOKEN"),
            "policy_control.break_glass.token_env",
        ),
    )
    bootstrap_administrators_raw = policy_control_raw.get("bootstrap_administrators", [])
    if not isinstance(bootstrap_administrators_raw, list):
        raise ConfigError("policy_control.bootstrap_administrators must be an array")
    if policy_control_mode == "postgresql":
        if usage_storage.backend != "postgresql":
            raise ConfigError("policy_control.mode postgresql requires usage_storage.backend postgresql")
        if policy_control_dsn_env in {
            usage_storage.postgres_dsn_env,
            usage_storage.postgres_migration_dsn_env,
        }:
            raise ConfigError(
                "policy_control.postgres_control_dsn_env must name a credential distinct from "
                "the runtime and migration credentials"
            )
        if policy_control_role == usage_storage.postgres_runtime_role:
            raise ConfigError(
                "policy_control.postgres_control_role must differ from "
                "usage_storage.postgres_runtime_role"
            )
        if not bootstrap_administrators_raw:
            raise ConfigError("policy_control.bootstrap_administrators must contain at least one administrator")
        if "policies" in raw:
            raise ConfigError(
                "policies is not permitted when policy_control.mode is postgresql; "
                "stage an immutable policy document through the policy control service"
            )
    elif bootstrap_administrators_raw or break_glass.enabled:
        raise ConfigError(
            "policy_control.bootstrap_administrators and break_glass require policy_control.mode postgresql"
        )

    return PolicyControlConstruction(
        config=PolicyControlConfig(
            mode=policy_control_mode,
            postgres_control_dsn_env=policy_control_dsn_env,
            postgres_control_role=policy_control_role,
            break_glass=break_glass,
        ),
        bootstrap_administrators_raw=tuple(bootstrap_administrators_raw),
    )


def build_policy_domain(
    raw: dict[str, Any],
    *,
    policy_control_mode: str,
) -> PolicyConstruction:
    egress_raw = _object(raw.get("egress_controls", {}), "egress_controls")
    secret_controls = _secret_controls(egress_raw.get("secrets", {}))
    if policy_control_mode == "postgresql":
        organization_policy = Policy()
        team_policies: dict[str, Policy] = {}
        actor_policies: dict[str, Policy] = {}
    else:
        policies_raw = _object(raw.get("policies"), "policies")
        organization_policy = _policy(policies_raw.get("organization"), "policies.organization")
        team_policies = {
            scope_id: _policy(value, f"policies.teams.{scope_id}")
            for scope_id, value in _object(policies_raw.get("teams", {}), "policies.teams").items()
        }
        actor_policies = {
            scope_id: _policy(value, f"policies.actors.{scope_id}")
            for scope_id, value in _object(policies_raw.get("actors", {}), "policies.actors").items()
        }
    return PolicyConstruction(
        secret_controls=secret_controls,
        organization_policy=organization_policy,
        team_policies=team_policies,
        actor_policies=actor_policies,
    )


def resolve_secret_controls(controls: SecretControls, env: dict[str, str]) -> SecretControls:
    """Resolve configured detector values only after semantic config validation."""

    secret_values: list[tuple[str, str]] = []
    for env_name in controls.custom_secret_envs:
        secret_value = env.get(env_name, "")
        if not secret_value:
            raise ConfigError(f"Required custom secret environment variable is not set: {env_name}")
        if len(secret_value) < 8:
            raise ConfigError(f"Custom secret from {env_name} must be at least 8 characters")
        secret_values.append((f"custom:{env_name}", secret_value))
    return replace(controls, custom_secret_values=tuple(secret_values))


def _policy(value: Any, path: str) -> Policy:
    item = _object(value, path)
    return Policy(
        allowed_clients=_optional_string_tuple(item.get("allowed_clients"), f"{path}.allowed_clients"),
        allowed_models=_optional_string_tuple(item.get("allowed_models"), f"{path}.allowed_models"),
        fallback_model=_optional_string(item.get("fallback_model"), f"{path}.fallback_model"),
        fallback_models=_optional_string_map(item.get("fallback_models"), f"{path}.fallback_models"),
        max_output_tokens=_optional_integer(item.get("max_output_tokens"), f"{path}.max_output_tokens", minimum=1),
        monthly_token_limit=_optional_integer(item.get("monthly_token_limit"), f"{path}.monthly_token_limit", minimum=1),
        monthly_budget_usd=_optional_number(item.get("monthly_budget_usd"), f"{path}.monthly_budget_usd"),
        per_actor_monthly_budget_usd=_optional_number(
            item.get("per_actor_monthly_budget_usd"), f"{path}.per_actor_monthly_budget_usd"
        ),
    )


def _secret_controls(value: Any) -> SecretControls:
    item = _object(value, "egress_controls.secrets")
    mode = _string(item.get("mode", "redact"), "egress_controls.secrets.mode")
    if mode not in {"off", "redact", "deny"}:
        raise ConfigError("egress_controls.secrets.mode must be off, redact, or deny")
    builtins = _boolean(item.get("builtins", True), "egress_controls.secrets.builtins")
    secret_envs = _string_tuple(
        item.get("custom_secret_envs", []),
        "egress_controls.secrets.custom_secret_envs",
    )
    return SecretControls(
        mode=mode,
        builtins=builtins,
        custom_secret_envs=secret_envs,
    )
