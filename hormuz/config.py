from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ListenConfig:
    host: str = "127.0.0.1"
    port: int = 8787


@dataclass(frozen=True)
class UpstreamConfig:
    base_url: str
    api_key_env: str
    allow_response_storage: bool = False
    allow_background: bool = False


@dataclass(frozen=True)
class SecretControls:
    mode: str = "redact"
    builtins: bool = True
    custom_secret_envs: tuple[str, ...] = ()
    custom_secret_values: tuple[tuple[str, str], ...] = field(default=(), repr=False)


@dataclass(frozen=True)
class Identity:
    token_env: str
    token: str
    actor_id: str
    actor_name: str
    team_id: str
    team_name: str
    allowed_clients: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModelRoute:
    alias: str
    protocol: str
    upstream_model: str
    input_cost_per_million: float = 0
    cache_read_cost_per_million: float = 0
    cache_write_cost_per_million: float = 0
    output_cost_per_million: float = 0

    def estimate_cost_microusd(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int,
        cache_write_tokens: int,
    ) -> int:
        # OpenAI reports cached tokens as a subset of input_tokens. Anthropic
        # reports uncached, cache-read, and cache-write input as separate fields.
        uncached_input = (
            input_tokens
            if self.protocol == "anthropic"
            else max(0, input_tokens - cache_read_tokens - cache_write_tokens)
        )
        usd = (
            uncached_input * self.input_cost_per_million
            + cache_read_tokens * self.cache_read_cost_per_million
            + cache_write_tokens * self.cache_write_cost_per_million
            + output_tokens * self.output_cost_per_million
        ) / 1_000_000
        return max(0, round(usd * 1_000_000))


@dataclass(frozen=True)
class Policy:
    allowed_clients: tuple[str, ...] | None = None
    allowed_models: tuple[str, ...] | None = None
    fallback_model: str | None = None
    fallback_models: dict[str, str] | None = None
    max_output_tokens: int | None = None
    monthly_token_limit: int | None = None
    monthly_budget_usd: float | None = None
    per_actor_monthly_budget_usd: float | None = None

    def overlaid(self, other: "Policy | None") -> "Policy":
        if other is None:
            return self
        fallback_models = dict(self.fallback_models or {})
        fallback_models.update(other.fallback_models or {})
        return Policy(
            # A narrower scope can restrict an organization policy, but it
            # cannot grant a client/model the organization did not allow.
            allowed_clients=_intersection(self.allowed_clients, other.allowed_clients),
            allowed_models=_intersection(self.allowed_models, other.allowed_models),
            fallback_model=other.fallback_model if other.fallback_model is not None else self.fallback_model,
            fallback_models=fallback_models or None,
            max_output_tokens=_minimum(self.max_output_tokens, other.max_output_tokens),
            monthly_token_limit=_minimum(self.monthly_token_limit, other.monthly_token_limit),
            monthly_budget_usd=_minimum(self.monthly_budget_usd, other.monthly_budget_usd),
            per_actor_monthly_budget_usd=_minimum(
                self.per_actor_monthly_budget_usd,
                other.per_actor_monthly_budget_usd,
            ),
        )


@dataclass(frozen=True)
class GatewayConfig:
    source_path: Path
    listen: ListenConfig
    database_path: Path
    upstreams: dict[str, UpstreamConfig]
    identities_by_token: dict[str, Identity]
    model_routes: dict[str, ModelRoute]
    organization_policy: Policy
    secret_controls: SecretControls = field(default_factory=SecretControls)
    team_policies: dict[str, Policy] = field(default_factory=dict)
    actor_policies: dict[str, Policy] = field(default_factory=dict)
    max_request_bytes: int = 25 * 1024 * 1024
    upstream_timeout_seconds: int = 600

    @classmethod
    def load(cls, path: str | Path, *, environ: dict[str, str] | None = None) -> "GatewayConfig":
        source_path = Path(path).expanduser().resolve()
        try:
            raw = json.loads(source_path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise ConfigError(f"Configuration file does not exist: {source_path}") from error
        except json.JSONDecodeError as error:
            raise ConfigError(f"Invalid JSON in {source_path}: {error}") from error
        if not isinstance(raw, dict):
            raise ConfigError("Gateway configuration must be a JSON object")

        env = os.environ if environ is None else environ
        listen_raw = _object(raw.get("listen", {}), "listen")
        host = _string(listen_raw.get("host", "127.0.0.1"), "listen.host")
        port = _integer(listen_raw.get("port", 8787), "listen.port", minimum=1, maximum=65535)

        database_value = _string(raw.get("database", "./hormuz.sqlite3"), "database")
        database_path = Path(database_value).expanduser()
        if not database_path.is_absolute():
            database_path = (source_path.parent / database_path).resolve()

        upstreams_raw = _object(raw.get("upstreams"), "upstreams")
        upstreams: dict[str, UpstreamConfig] = {}
        for protocol in ("openai", "anthropic"):
            item = _object(upstreams_raw.get(protocol), f"upstreams.{protocol}")
            base_url = _string(item.get("base_url"), f"upstreams.{protocol}.base_url").rstrip("/")
            parsed = urlparse(base_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ConfigError(f"upstreams.{protocol}.base_url must be an HTTP(S) URL")
            upstreams[protocol] = UpstreamConfig(
                base_url=base_url,
                api_key_env=_string(item.get("api_key_env"), f"upstreams.{protocol}.api_key_env"),
                allow_response_storage=_boolean(
                    item.get("allow_response_storage", False),
                    f"upstreams.{protocol}.allow_response_storage",
                ),
                allow_background=_boolean(
                    item.get("allow_background", False),
                    f"upstreams.{protocol}.allow_background",
                ),
            )

        identities_raw = raw.get("identities")
        if not isinstance(identities_raw, list) or not identities_raw:
            raise ConfigError("identities must be a non-empty array")
        identities_by_token: dict[str, Identity] = {}
        for index, value in enumerate(identities_raw):
            item = _object(value, f"identities[{index}]")
            prefix = f"identities[{index}]"
            token_env = _string(item.get("token_env"), f"{prefix}.token_env")
            token = env.get(token_env, "")
            if not token:
                raise ConfigError(f"Required identity token environment variable is not set: {token_env}")
            if len(token) < 16:
                raise ConfigError(f"Identity token from {token_env} must be at least 16 characters")
            if token in identities_by_token:
                raise ConfigError(f"Identity tokens must be unique; duplicate value from {token_env}")
            identity = Identity(
                token_env=token_env,
                token=token,
                actor_id=_string(item.get("actor_id"), f"{prefix}.actor_id"),
                actor_name=_string(item.get("actor_name"), f"{prefix}.actor_name"),
                team_id=_string(item.get("team_id"), f"{prefix}.team_id"),
                team_name=_string(item.get("team_name"), f"{prefix}.team_name"),
                allowed_clients=_string_tuple(item.get("allowed_clients", []), f"{prefix}.allowed_clients"),
            )
            identities_by_token[token] = identity

        routes_raw = _object(raw.get("model_routes"), "model_routes")
        if not routes_raw:
            raise ConfigError("model_routes must contain at least one route")
        model_routes: dict[str, ModelRoute] = {}
        for alias, value in routes_raw.items():
            if not isinstance(alias, str) or not alias.strip():
                raise ConfigError("model_routes keys must be non-empty strings")
            item = _object(value, f"model_routes.{alias}")
            protocol = _string(item.get("protocol"), f"model_routes.{alias}.protocol")
            if protocol not in upstreams:
                raise ConfigError(f"model_routes.{alias}.protocol must be openai or anthropic")
            model_routes[alias] = ModelRoute(
                alias=alias,
                protocol=protocol,
                upstream_model=_string(item.get("upstream_model"), f"model_routes.{alias}.upstream_model"),
                input_cost_per_million=_number(item.get("input_cost_per_million", 0), f"model_routes.{alias}.input_cost_per_million"),
                cache_read_cost_per_million=_number(item.get("cache_read_cost_per_million", 0), f"model_routes.{alias}.cache_read_cost_per_million"),
                cache_write_cost_per_million=_number(item.get("cache_write_cost_per_million", 0), f"model_routes.{alias}.cache_write_cost_per_million"),
                output_cost_per_million=_number(item.get("output_cost_per_million", 0), f"model_routes.{alias}.output_cost_per_million"),
            )

        egress_raw = _object(raw.get("egress_controls", {}), "egress_controls")
        secret_controls = _secret_controls(egress_raw.get("secrets", {}), env)
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

        config = cls(
            source_path=source_path,
            listen=ListenConfig(host=host, port=port),
            database_path=database_path,
            upstreams=upstreams,
            identities_by_token=identities_by_token,
            model_routes=model_routes,
            organization_policy=organization_policy,
            secret_controls=secret_controls,
            team_policies=team_policies,
            actor_policies=actor_policies,
            max_request_bytes=_integer(raw.get("max_request_bytes", 25 * 1024 * 1024), "max_request_bytes", minimum=1024),
            upstream_timeout_seconds=_integer(raw.get("upstream_timeout_seconds", 600), "upstream_timeout_seconds", minimum=1),
        )
        config.validate_references()
        return config

    def validate_references(self) -> None:
        policies = [self.organization_policy, *self.team_policies.values(), *self.actor_policies.values()]
        for policy in policies:
            for alias in policy.allowed_models or ():
                if alias not in self.model_routes:
                    raise ConfigError(f"Policy references unknown model alias: {alias}")
            if policy.fallback_model is not None and policy.fallback_model not in self.model_routes:
                raise ConfigError(f"Policy references unknown fallback model alias: {policy.fallback_model}")
            for protocol, alias in (policy.fallback_models or {}).items():
                route = self.model_routes.get(alias)
                if route is None:
                    raise ConfigError(f"Policy references unknown fallback model alias: {alias}")
                if route.protocol != protocol:
                    raise ConfigError(f"Policy fallback {alias} does not use protocol {protocol}")
        limits_require_request_bound = any(
            policy.monthly_token_limit is not None
            or policy.monthly_budget_usd is not None
            or policy.per_actor_monthly_budget_usd is not None
            for policy in policies
        )
        if limits_require_request_bound:
            for identity in self.identities_by_token.values():
                if self.resolved_policy(identity).max_output_tokens is None:
                    raise ConfigError(
                        f"Identity {identity.actor_id} needs an effective max_output_tokens policy "
                        "when monthly token or budget limits are configured"
                    )

    def identity_for_token(self, token: str) -> Identity | None:
        return self.identities_by_token.get(token)

    def resolved_policy(self, identity: Identity) -> Policy:
        return (
            self.organization_policy
            .overlaid(self.team_policies.get(identity.team_id))
            .overlaid(self.actor_policies.get(identity.actor_id))
        )


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


def _secret_controls(value: Any, env: dict[str, str]) -> SecretControls:
    item = _object(value, "egress_controls.secrets")
    mode = _string(item.get("mode", "redact"), "egress_controls.secrets.mode")
    if mode not in {"off", "redact", "deny"}:
        raise ConfigError("egress_controls.secrets.mode must be off, redact, or deny")
    builtins = _boolean(item.get("builtins", True), "egress_controls.secrets.builtins")
    secret_envs = _string_tuple(
        item.get("custom_secret_envs", []),
        "egress_controls.secrets.custom_secret_envs",
    )
    secret_values: list[tuple[str, str]] = []
    for env_name in secret_envs:
        secret_value = env.get(env_name, "")
        if not secret_value:
            raise ConfigError(f"Required custom secret environment variable is not set: {env_name}")
        if len(secret_value) < 8:
            raise ConfigError(f"Custom secret from {env_name} must be at least 8 characters")
        secret_values.append((f"custom:{env_name}", secret_value))
    return SecretControls(
        mode=mode,
        builtins=builtins,
        custom_secret_envs=secret_envs,
        custom_secret_values=tuple(secret_values),
    )


def _object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{path} must be an object")
    return value


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{path} must be a non-empty string")
    return value.strip()


def _boolean(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{path} must be a boolean")
    return value


def _optional_string(value: Any, path: str) -> str | None:
    if value is None:
        return None
    return _string(value, path)


def _string_tuple(value: Any, path: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ConfigError(f"{path} must be an array of strings")
    result = tuple(_string(item, f"{path}[]") for item in value)
    if len(result) != len(set(result)):
        raise ConfigError(f"{path} cannot contain duplicates")
    return result


def _optional_string_tuple(value: Any, path: str) -> tuple[str, ...] | None:
    if value is None:
        return None
    return _string_tuple(value, path)


def _optional_string_map(value: Any, path: str) -> dict[str, str] | None:
    if value is None:
        return None
    item = _object(value, path)
    result: dict[str, str] = {}
    for key, mapped_value in item.items():
        protocol = _string(key, f"{path} key")
        if protocol not in {"openai", "anthropic"}:
            raise ConfigError(f"{path} keys must be openai or anthropic")
        result[protocol] = _string(mapped_value, f"{path}.{protocol}")
    return result


def _integer(value: Any, path: str, *, minimum: int, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{path} must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        suffix = f" and at most {maximum}" if maximum is not None else ""
        raise ConfigError(f"{path} must be at least {minimum}{suffix}")
    return value


def _optional_integer(value: Any, path: str, *, minimum: int) -> int | None:
    if value is None:
        return None
    return _integer(value, path, minimum=minimum)


def _number(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ConfigError(f"{path} must be a non-negative number")
    return float(value)


def _optional_number(value: Any, path: str) -> float | None:
    if value is None:
        return None
    return _number(value, path)


def _intersection(parent: tuple[str, ...] | None, child: tuple[str, ...] | None) -> tuple[str, ...] | None:
    if parent is None:
        return child
    if child is None:
        return parent
    allowed = set(parent)
    return tuple(value for value in child if value in allowed)


def _minimum(parent: int | float | None, child: int | float | None):
    if parent is None:
        return child
    if child is None:
        return parent
    return min(parent, child)
