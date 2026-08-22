from __future__ import annotations

import json
import hashlib
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


class ConfigError(ValueError):
    pass


_ENVIRONMENT_NAME_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_POSTGRES_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


_DEPRECATED_CONTEXT_CONFIGURATION_KEYS = frozenset(
    {
        "context_cache",
        "context_database",
        "context_injection",
        "context_lifecycle",
        "context_packs",
        "context_retrieval",
        "context_service",
        "context_storage",
    }
)
_DEPRECATED_CONTEXT_CAPABILITIES = frozenset(
    {"context_injector", "context_promoter", "context_retriever"}
)
_CONTEXT_EXPERIMENT_MOVED_MESSAGE = (
    "context_experiment_moved: legacy context configuration is not supported by the core gateway; "
    "migrate it to hormuz-context-experiment"
)


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
class UsageStorageConfig:
    """Configuration for the metadata-only usage and audit repository.

    SQLite remains the default local adapter. PostgreSQL is deliberately
    opt-in and reads its DSNs from process environment variables so a
    connection credential never needs to appear in the Hormuz JSON file.
    """

    backend: str = "sqlite"
    postgres_dsn_env: str = "HORMUZ_POSTGRES_DSN"
    postgres_migration_dsn_env: str = "HORMUZ_POSTGRES_MIGRATION_DSN"
    postgres_schema: str = "hormuz"
    postgres_runtime_role: str = "hormuz_runtime"


@dataclass(frozen=True)
class BootstrapAdministrator:
    """A one-time configuration seed for a tenant policy authority.

    Static identities are supported only for a local bootstrap credential. OIDC
    identities use the stable issuer/subject pair; neither e-mail nor group
    display names are accepted as policy-authority keys.
    """

    organization_id: str
    authentication_kind: str
    actor_id: str | None = None
    issuer: str | None = None
    subject: str | None = None


@dataclass(frozen=True)
class BreakGlassConfig:
    """Explicitly opt-in recovery configuration, never a normal admin role."""

    enabled: bool = False
    token_env: str = "HORMUZ_POLICY_BREAK_GLASS_TOKEN"


@dataclass(frozen=True)
class PolicyControlConfig:
    """Control-plane configuration that is intentionally narrower than policy.

    ``postgresql`` means policy authority and active versions are shared
    runtime state. The bootstrap list is read only by the initial bootstrap
    command; normal authorization is read from PostgreSQL.
    """

    mode: str = "local"
    bootstrap_administrators: tuple[BootstrapAdministrator, ...] = ()
    postgres_control_dsn_env: str = "HORMUZ_POLICY_CONTROL_DSN"
    postgres_control_role: str = "hormuz_policy_control"
    break_glass: BreakGlassConfig = field(default_factory=BreakGlassConfig)


@dataclass(frozen=True)
class Identity:
    token_env: str
    token: str = field(repr=False)
    actor_id: str
    actor_name: str
    team_id: str
    team_name: str
    allowed_clients: tuple[str, ...] = ()
    organization_id: str = "organization"
    clearance: str = "internal"
    identity_type: str = "human"
    authentication_source: str = "static"


@dataclass(frozen=True)
class OIDCIssuerConfig:
    issuer: str
    audiences: tuple[str, ...]
    jwks_uri: str | None = None
    algorithms: tuple[str, ...] = ("RS256",)
    clock_skew_seconds: int = 60
    discovery_cache_seconds: int = 3600
    allow_insecure_http: bool = False


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
    oidc_issuers: dict[str, OIDCIssuerConfig] = field(default_factory=dict)
    identities_by_subject: dict[tuple[str, str], Identity] = field(default_factory=dict)
    secret_controls: SecretControls = field(default_factory=SecretControls)
    team_policies: dict[str, Policy] = field(default_factory=dict)
    actor_policies: dict[str, Policy] = field(default_factory=dict)
    max_request_bytes: int = 25 * 1024 * 1024
    upstream_timeout_seconds: int = 600
    usage_storage: UsageStorageConfig = field(default_factory=UsageStorageConfig)
    policy_control: PolicyControlConfig = field(default_factory=PolicyControlConfig)

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
        _reject_deprecated_context_configuration(raw)

        env = os.environ if environ is None else environ
        listen_raw = _object(raw.get("listen", {}), "listen")
        host = _string(listen_raw.get("host", "127.0.0.1"), "listen.host")
        port = _integer(listen_raw.get("port", 8787), "listen.port", minimum=1, maximum=65535)

        database_value = _string(raw.get("database", "./hormuz.sqlite3"), "database")
        database_path = Path(database_value).expanduser()
        if not database_path.is_absolute():
            database_path = (source_path.parent / database_path).resolve()

        usage_storage_raw = _object(raw.get("usage_storage", {}), "usage_storage")
        unsupported_storage_fields = set(usage_storage_raw).difference(
            {
                "backend",
                "postgres_dsn_env",
                "postgres_migration_dsn_env",
                "postgres_schema",
                "postgres_runtime_role",
            }
        )
        if unsupported_storage_fields:
            raise ConfigError(
                "usage_storage contains unsupported fields: "
                + ", ".join(sorted(str(field) for field in unsupported_storage_fields))
            )
        usage_backend = _string(usage_storage_raw.get("backend", "sqlite"), "usage_storage.backend")
        if usage_backend not in {"sqlite", "postgresql"}:
            raise ConfigError("usage_storage.backend must be sqlite or postgresql")
        postgres_dsn_env = _environment_name(
            usage_storage_raw.get("postgres_dsn_env", "HORMUZ_POSTGRES_DSN"),
            "usage_storage.postgres_dsn_env",
        )
        postgres_migration_dsn_env = _environment_name(
            usage_storage_raw.get("postgres_migration_dsn_env", "HORMUZ_POSTGRES_MIGRATION_DSN"),
            "usage_storage.postgres_migration_dsn_env",
        )
        postgres_schema = _postgres_identifier(
            usage_storage_raw.get("postgres_schema", "hormuz"),
            "usage_storage.postgres_schema",
        )
        postgres_runtime_role = _postgres_identifier(
            usage_storage_raw.get("postgres_runtime_role", "hormuz_runtime"),
            "usage_storage.postgres_runtime_role",
        )
        if usage_backend == "postgresql" and postgres_dsn_env == postgres_migration_dsn_env:
            raise ConfigError(
                "usage_storage.postgres_dsn_env and usage_storage.postgres_migration_dsn_env "
                "must name separate credentials"
            )

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
            if usage_backend != "postgresql":
                raise ConfigError("policy_control.mode postgresql requires usage_storage.backend postgresql")
            if policy_control_dsn_env in {postgres_dsn_env, postgres_migration_dsn_env}:
                raise ConfigError(
                    "policy_control.postgres_control_dsn_env must name a credential distinct from "
                    "the runtime and migration credentials"
                )
            if policy_control_role == postgres_runtime_role:
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

        identities_raw = raw.get("identities", [])
        if not isinstance(identities_raw, list):
            raise ConfigError("identities must be an array")
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
                organization_id=_string(item.get("organization_id", "organization"), f"{prefix}.organization_id"),
                clearance=_classification(item.get("clearance", "internal"), f"{prefix}.clearance"),
                identity_type=_identity_type(item.get("identity_type", "human"), f"{prefix}.identity_type"),
            )
            identities_by_token[token] = identity

        authentication_raw = _object(raw.get("authentication", {}), "authentication")
        oidc_raw = _object(authentication_raw.get("oidc", {}), "authentication.oidc")
        oidc_issuers_raw = oidc_raw.get("issuers", [])
        if not isinstance(oidc_issuers_raw, list):
            raise ConfigError("authentication.oidc.issuers must be an array")
        oidc_issuers: dict[str, OIDCIssuerConfig] = {}
        identities_by_subject: dict[tuple[str, str], Identity] = {}
        supported_algorithms = {
            "RS256",
            "RS384",
            "RS512",
            "PS256",
            "PS384",
            "PS512",
            "ES256",
            "ES384",
            "ES512",
        }
        for issuer_index, value in enumerate(oidc_issuers_raw):
            prefix = f"authentication.oidc.issuers[{issuer_index}]"
            item = _object(value, prefix)
            issuer = _url(item.get("issuer"), f"{prefix}.issuer")
            if issuer in oidc_issuers:
                raise ConfigError(f"OIDC issuer must be unique: {issuer}")
            audiences = _string_tuple(item.get("audiences"), f"{prefix}.audiences")
            if not audiences:
                raise ConfigError(f"{prefix}.audiences must contain at least one audience")
            algorithms = _string_tuple(item.get("algorithms", ["RS256"]), f"{prefix}.algorithms")
            if not algorithms or any(algorithm not in supported_algorithms for algorithm in algorithms):
                raise ConfigError(
                    f"{prefix}.algorithms must contain only asymmetric JWT algorithms: "
                    + ", ".join(sorted(supported_algorithms))
                )
            jwks_uri_value = item.get("jwks_uri")
            jwks_uri = _url(jwks_uri_value, f"{prefix}.jwks_uri") if jwks_uri_value is not None else None
            allow_insecure_http = _boolean(
                item.get("allow_insecure_http", False),
                f"{prefix}.allow_insecure_http",
            )
            _validate_oidc_transport(
                issuer=issuer,
                jwks_uri=jwks_uri,
                allow_insecure_http=allow_insecure_http,
                path=prefix,
            )
            issuer_config = OIDCIssuerConfig(
                issuer=issuer,
                audiences=audiences,
                jwks_uri=jwks_uri,
                algorithms=algorithms,
                clock_skew_seconds=_integer(
                    item.get("clock_skew_seconds", 60),
                    f"{prefix}.clock_skew_seconds",
                    minimum=0,
                    maximum=300,
                ),
                discovery_cache_seconds=_integer(
                    item.get("discovery_cache_seconds", 3600),
                    f"{prefix}.discovery_cache_seconds",
                    minimum=60,
                    maximum=86400,
                ),
                allow_insecure_http=allow_insecure_http,
            )
            oidc_issuers[issuer] = issuer_config
            subjects_raw = item.get("subjects", [])
            if not isinstance(subjects_raw, list):
                raise ConfigError(f"{prefix}.subjects must be an array")
            if not subjects_raw:
                raise ConfigError(f"{prefix}.subjects must contain at least one subject mapping")
            for subject_index, subject_value in enumerate(subjects_raw):
                subject_prefix = f"{prefix}.subjects[{subject_index}]"
                subject_item = _object(subject_value, subject_prefix)
                subject = _string(subject_item.get("subject"), f"{subject_prefix}.subject")
                key = (issuer, subject)
                if key in identities_by_subject:
                    raise ConfigError(f"OIDC subject must be unique for issuer {issuer}: {subject}")
                identities_by_subject[key] = Identity(
                    token_env="",
                    token="",
                    actor_id=_string(subject_item.get("actor_id"), f"{subject_prefix}.actor_id"),
                    actor_name=_string(subject_item.get("actor_name"), f"{subject_prefix}.actor_name"),
                    team_id=_string(subject_item.get("team_id"), f"{subject_prefix}.team_id"),
                    team_name=_string(subject_item.get("team_name"), f"{subject_prefix}.team_name"),
                    allowed_clients=_string_tuple(
                        subject_item.get("allowed_clients", []),
                        f"{subject_prefix}.allowed_clients",
                    ),
                    organization_id=_string(
                        subject_item.get("organization_id", "organization"),
                        f"{subject_prefix}.organization_id",
                    ),
                    clearance=_classification(
                        subject_item.get("clearance", "internal"),
                        f"{subject_prefix}.clearance",
                    ),
                    identity_type=_identity_type(
                        subject_item.get("identity_type", "human"),
                        f"{subject_prefix}.identity_type",
                    ),
                    authentication_source=f"oidc:{issuer}",
                )
        if not identities_by_token and not identities_by_subject:
            raise ConfigError("At least one static identity or OIDC subject mapping is required")
        _validate_identity_consistency((*identities_by_token.values(), *identities_by_subject.values()))
        bootstrap_administrators = _bootstrap_administrators(
            bootstrap_administrators_raw,
            identities_by_token=identities_by_token,
            oidc_issuers=oidc_issuers,
        )

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
        if policy_control_mode == "postgresql":
            # In managed mode the active policy is loaded only from the shared
            # immutable control plane. Keep a harmless empty local projection
            # so legacy helpers can still construct the configuration object.
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

        config = cls(
            source_path=source_path,
            listen=ListenConfig(host=host, port=port),
            database_path=database_path,
            upstreams=upstreams,
            identities_by_token=identities_by_token,
            model_routes=model_routes,
            organization_policy=organization_policy,
            oidc_issuers=oidc_issuers,
            identities_by_subject=identities_by_subject,
            secret_controls=secret_controls,
            team_policies=team_policies,
            actor_policies=actor_policies,
            max_request_bytes=_integer(raw.get("max_request_bytes", 25 * 1024 * 1024), "max_request_bytes", minimum=1024),
            upstream_timeout_seconds=_integer(raw.get("upstream_timeout_seconds", 600), "upstream_timeout_seconds", minimum=1),
            usage_storage=UsageStorageConfig(
                backend=usage_backend,
                postgres_dsn_env=postgres_dsn_env,
                postgres_migration_dsn_env=postgres_migration_dsn_env,
                postgres_schema=postgres_schema,
                postgres_runtime_role=postgres_runtime_role,
            ),
            policy_control=PolicyControlConfig(
                mode=policy_control_mode,
                bootstrap_administrators=bootstrap_administrators,
                postgres_control_dsn_env=policy_control_dsn_env,
                postgres_control_role=policy_control_role,
                break_glass=break_glass,
            ),
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
            for identity in self.identities_by_actor.values():
                if self.resolved_policy(identity).max_output_tokens is None:
                    raise ConfigError(
                        f"Identity {identity.actor_id} needs an effective max_output_tokens policy "
                        "when monthly token or budget limits are configured"
                    )

    def identity_for_token(self, token: str) -> Identity | None:
        return self.identities_by_token.get(token)

    @property
    def identities_by_actor(self) -> dict[str, Identity]:
        result: dict[str, Identity] = {}
        for identity in (*self.identities_by_token.values(), *self.identities_by_subject.values()):
            result.setdefault(identity.actor_id, identity)
        return result

    @property
    def organization_ids(self) -> tuple[str, ...]:
        """Return the distinct configured organization IDs in stable order."""

        return tuple(
            sorted(
                {
                    identity.organization_id
                    for identity in (*self.identities_by_token.values(), *self.identities_by_subject.values())
                }
            )
        )

    def identity_for_subject(self, issuer: str, subject: str) -> Identity | None:
        return self.identities_by_subject.get((issuer, subject))

    def resolved_policy(self, identity: Identity) -> Policy:
        return (
            self.organization_policy
            .overlaid(self.team_policies.get(identity.team_id))
            .overlaid(self.actor_policies.get(identity.actor_id))
        )

    @property
    def policy_version(self) -> str:
        """Return a content-free fingerprint only for local policy mode.

        Managed PostgreSQL mode must resolve an active immutable version through
        ``PolicyRuntime``. Returning a configuration fingerprint there would
        silently defeat the shared control-plane source of truth.
        """

        if self.policy_control.mode != "local":
            raise ConfigError("managed policy versions must be resolved through the policy control plane")

        payload = {
            "identities": {
                "static": [
                    _identity_policy_payload(identity)
                    for identity in sorted(self.identities_by_token.values(), key=lambda item: item.actor_id)
                ],
                "oidc": [
                    {
                        "issuer": issuer,
                        "subject": subject,
                        **_identity_policy_payload(identity),
                    }
                    for (issuer, subject), identity in sorted(self.identities_by_subject.items())
                ],
            },
            "model_routes": {
                alias: {
                    "protocol": route.protocol,
                    "upstream_model": route.upstream_model,
                    "input_cost_per_million": route.input_cost_per_million,
                    "cache_read_cost_per_million": route.cache_read_cost_per_million,
                    "cache_write_cost_per_million": route.cache_write_cost_per_million,
                    "output_cost_per_million": route.output_cost_per_million,
                }
                for alias, route in sorted(self.model_routes.items())
            },
            "policies": {
                "organization": _policy_payload(self.organization_policy),
                "teams": {name: _policy_payload(policy) for name, policy in sorted(self.team_policies.items())},
                "actors": {name: _policy_payload(policy) for name, policy in sorted(self.actor_policies.items())},
            },
            "upstream_controls": {
                protocol: {
                    "allow_response_storage": upstream.allow_response_storage,
                    "allow_background": upstream.allow_background,
                }
                for protocol, upstream in sorted(self.upstreams.items())
            },
            "secret_controls": {
                "mode": self.secret_controls.mode,
                "builtins": self.secret_controls.builtins,
                "custom_secret_envs": sorted(self.secret_controls.custom_secret_envs),
            },
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return f"local-config-{hashlib.sha256(canonical).hexdigest()[:16]}"


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


def _policy_payload(policy: Policy) -> dict[str, object]:
    return {
        "allowed_clients": list(policy.allowed_clients) if policy.allowed_clients is not None else None,
        "allowed_models": list(policy.allowed_models) if policy.allowed_models is not None else None,
        "fallback_model": policy.fallback_model,
        "fallback_models": dict(sorted((policy.fallback_models or {}).items())),
        "max_output_tokens": policy.max_output_tokens,
        "monthly_token_limit": policy.monthly_token_limit,
        "monthly_budget_usd": policy.monthly_budget_usd,
        "per_actor_monthly_budget_usd": policy.per_actor_monthly_budget_usd,
    }


def _identity_policy_payload(identity: Identity) -> dict[str, object]:
    return {
        "actor_id": identity.actor_id,
        "team_id": identity.team_id,
        "organization_id": identity.organization_id,
        "identity_type": identity.identity_type,
        "allowed_clients": list(identity.allowed_clients),
        "authentication_source": identity.authentication_source,
    }


def _reject_deprecated_context_configuration(raw: dict[str, Any]) -> None:
    """Fail closed rather than silently ignoring retired context settings."""

    pending: list[Any] = [raw]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            for key, nested in value.items():
                if key in _DEPRECATED_CONTEXT_CONFIGURATION_KEYS:
                    raise ConfigError(_CONTEXT_EXPERIMENT_MOVED_MESSAGE)
                pending.append(nested)
        elif isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, str) and value in _DEPRECATED_CONTEXT_CAPABILITIES:
            raise ConfigError(_CONTEXT_EXPERIMENT_MOVED_MESSAGE)


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


def _bootstrap_administrators(
    value: list[Any],
    *,
    identities_by_token: dict[str, Identity],
    oidc_issuers: dict[str, OIDCIssuerConfig],
) -> tuple[BootstrapAdministrator, ...]:
    """Validate tenant-qualified, one-time policy-admin bootstrap identities.

    The bootstrap format intentionally has no e-mail, username, team, or
    group-name field. Static credentials remain useful for a local bootstrap
    credential, while OIDC records use the stable issuer/subject pair that the
    governed control plane later persists.
    """

    administrators: list[BootstrapAdministrator] = []
    seen: set[tuple[str, str, str, str]] = set()
    static_identities = tuple(identities_by_token.values())
    for index, raw_value in enumerate(value):
        path = f"policy_control.bootstrap_administrators[{index}]"
        item = _object(raw_value, path)
        keys = set(item)
        organization_id = _string(item.get("organization_id"), f"{path}.organization_id")
        if keys == {"organization_id", "actor_id"}:
            actor_id = _string(item.get("actor_id"), f"{path}.actor_id")
            if not any(
                identity.actor_id == actor_id and identity.organization_id == organization_id
                for identity in static_identities
            ):
                raise ConfigError(
                    f"{path} must reference a configured static identity in the same organization"
                )
            administrator = BootstrapAdministrator(
                organization_id=organization_id,
                authentication_kind="static",
                actor_id=actor_id,
            )
            key = (organization_id, "static", actor_id, "")
        elif keys == {"organization_id", "issuer", "subject"}:
            issuer = _url(item.get("issuer"), f"{path}.issuer")
            subject = _string(item.get("subject"), f"{path}.subject")
            if issuer not in oidc_issuers:
                raise ConfigError(f"{path}.issuer must be a configured OIDC issuer")
            administrator = BootstrapAdministrator(
                organization_id=organization_id,
                authentication_kind="oidc",
                issuer=issuer,
                subject=subject,
            )
            key = (organization_id, "oidc", issuer, subject)
        else:
            raise ConfigError(
                f"{path} must contain organization_id plus actor_id, or organization_id plus issuer and subject"
            )
        if key in seen:
            raise ConfigError(f"{path} duplicates a bootstrap administrator")
        seen.add(key)
        administrators.append(administrator)
    return tuple(administrators)


def _object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{path} must be an object")
    return value


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{path} must be a non-empty string")
    return value.strip()


def _url(value: Any, path: str) -> str:
    result = _string(value, path)
    parsed = urlparse(result)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise ConfigError(f"{path} must be an HTTP(S) URL without a query or fragment")
    return result


def _classification(value: Any, path: str) -> str:
    result = _string(value, path)
    if result not in {"public", "internal", "confidential", "restricted"}:
        raise ConfigError(f"{path} must be public, internal, confidential, or restricted")
    return result


def _identity_type(value: Any, path: str) -> str:
    result = _string(value, path)
    if result not in {"human", "service_account", "ci", "connector"}:
        raise ConfigError(f"{path} must be human, service_account, ci, or connector")
    return result


def _validate_oidc_transport(
    *,
    issuer: str,
    jwks_uri: str | None,
    allow_insecure_http: bool,
    path: str,
) -> None:
    urls = [issuer, *(value for value in (jwks_uri,) if value is not None)]
    insecure = [value for value in urls if urlparse(value).scheme != "https"]
    if not insecure:
        return
    if not allow_insecure_http:
        raise ConfigError(f"{path} requires HTTPS; allow_insecure_http is only for loopback development")
    for value in insecure:
        hostname = urlparse(value).hostname
        if hostname not in {"127.0.0.1", "::1", "localhost"}:
            raise ConfigError(f"{path}.allow_insecure_http permits only loopback HTTP URLs")


def _validate_identity_consistency(identities: tuple[Identity, ...]) -> None:
    by_actor: dict[str, Identity] = {}
    for identity in identities:
        existing = by_actor.get(identity.actor_id)
        if existing is None:
            by_actor[identity.actor_id] = identity
            continue
        fields = (
            "actor_name",
            "team_id",
            "team_name",
            "allowed_clients",
            "organization_id",
            "clearance",
            "identity_type",
        )
        if any(getattr(existing, name) != getattr(identity, name) for name in fields):
            raise ConfigError(
                f"Identity metadata for actor {identity.actor_id} must match across authentication sources"
            )


def _boolean(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{path} must be a boolean")
    return value


def _environment_name(value: Any, path: str) -> str:
    result = _string(value, path)
    if _ENVIRONMENT_NAME_PATTERN.fullmatch(result) is None:
        raise ConfigError(f"{path} must be a safe environment variable name")
    return result


def _postgres_identifier(value: Any, path: str) -> str:
    result = _string(value, path)
    if _POSTGRES_IDENTIFIER_PATTERN.fullmatch(result) is None:
        raise ConfigError(f"{path} must be a safe PostgreSQL identifier")
    return result


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
    try:
        result = float(value)
    except OverflowError as error:
        raise ConfigError(f"{path} must be a non-negative number") from error
    if not math.isfinite(result):
        raise ConfigError(f"{path} must be a non-negative number")
    return result


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
