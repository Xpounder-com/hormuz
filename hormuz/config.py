from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import ipaddress
import json
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .context_lifecycle import LifecyclePolicy


class ConfigError(ValueError):
    pass


MAX_CONFIG_BYTES = 1_048_576
MAX_CONFIG_DEPTH = 64
MAX_CONFIG_NODES = 100_000
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


def _load_configuration_json(source_path: Path) -> tuple[Any, str]:
    try:
        encoded = source_path.read_bytes()
    except FileNotFoundError as error:
        raise ConfigError(f"Configuration file does not exist: {source_path}") from error
    except OSError as error:
        raise ConfigError(f"Configuration file could not be read: {source_path}") from error
    if len(encoded) > MAX_CONFIG_BYTES:
        raise ConfigError(f"Configuration file exceeds {MAX_CONFIG_BYTES} bytes")
    source_sha256 = hashlib.sha256(encoded).hexdigest()
    try:
        text = encoded.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ConfigError("Configuration file must be valid UTF-8 JSON") from error
    try:
        value = json.loads(
            text,
            object_pairs_hook=_configuration_object,
            parse_constant=_reject_configuration_constant,
        )
    except ConfigError:
        raise
    except RecursionError as error:
        raise ConfigError("Configuration JSON exceeds structural limits") from error
    except (json.JSONDecodeError, ValueError) as error:
        raise ConfigError("Configuration file must be valid UTF-8 JSON") from error
    _validate_configuration_structure(value)
    return value, source_sha256


def _configuration_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ConfigError("Configuration JSON contains duplicate object members")
        value[key] = item
    return value


def _reject_configuration_constant(_: str) -> None:
    raise ConfigError("Configuration JSON contains a non-standard number")


def _validate_configuration_structure(value: Any) -> None:
    pending: list[tuple[Any, int]] = [(value, 1)]
    nodes = 0
    while pending:
        current, depth = pending.pop()
        nodes += 1
        if depth > MAX_CONFIG_DEPTH or nodes > MAX_CONFIG_NODES:
            raise ConfigError("Configuration JSON exceeds structural limits")
        if isinstance(current, dict):
            children = current.values()
        elif isinstance(current, list):
            children = current
        else:
            continue
        if (
            (depth >= MAX_CONFIG_DEPTH and len(children) > 0)
            or nodes + len(pending) + len(children) > MAX_CONFIG_NODES
        ):
            raise ConfigError("Configuration JSON exceeds structural limits")
        pending.extend((item, depth + 1) for item in children)


@dataclass(frozen=True)
class ListenConfig:
    host: str = "127.0.0.1"
    port: int = 8787
    shutdown_grace_seconds: int = 30
    max_concurrent_requests: int = 128
    accept_backlog: int = 256
    max_connections: int = 256
    request_header_timeout_seconds: int = 15
    request_body_timeout_seconds: int = 30


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
class DLPRuleConfig:
    rule_id: str
    category: str
    confidence: str
    action: str
    providers: tuple[str, ...] = ("openai", "anthropic")
    models: tuple[str, ...] = ()
    values_env: str | None = None
    exact_values: tuple[str, ...] = field(default=(), repr=False)

    def applies_to(self, *, protocol: str, model: str) -> bool:
        return protocol in self.providers and (not self.models or model in self.models)


@dataclass(frozen=True)
class DLPApprovalConfig:
    enabled: bool = False
    fingerprint_key_env: str | None = None
    fingerprint_key: bytes = field(default=b"", repr=False)
    fingerprint_key_source: str = field(default="", repr=False)
    ttl_seconds: int = 900


@dataclass(frozen=True)
class DLPControls:
    policy_version: str = "local-dlp-v1"
    rules: tuple[DLPRuleConfig, ...] = ()
    approval: DLPApprovalConfig = field(default_factory=DLPApprovalConfig)


@dataclass(frozen=True)
class DLPPolicyOverlay:
    policy_version: str
    rules: tuple[DLPRuleConfig, ...] = ()


@dataclass(frozen=True)
class ContextLifecycleAutomationConfig:
    enabled: bool = False
    policy: LifecyclePolicy | None = None
    job_batch_size: int = 100
    lease_seconds: int = 30


@dataclass(frozen=True)
class ContextServiceConfig:
    policy_version: str = "local-v1"
    max_token_budget: int = 32_768
    max_items: int = 20
    requests_per_minute: int = 60
    allow_provisional: bool = False
    lifecycle: ContextLifecycleAutomationConfig = field(
        default_factory=ContextLifecycleAutomationConfig
    )


@dataclass(frozen=True)
class ContextInjectionPolicy:
    mode: str | None = None
    allowed_clients: tuple[str, ...] | None = None
    allowed_models: tuple[str, ...] | None = None
    allowed_repositories: tuple[str, ...] | None = None
    max_classification: str | None = None
    token_budget: int | None = None
    max_items: int | None = None

    def overlaid(
        self,
        other: "ContextInjectionPolicy | None",
    ) -> "ContextInjectionPolicy":
        if other is None:
            return self
        return ContextInjectionPolicy(
            mode=_context_injection_mode_overlay(self.mode, other.mode),
            allowed_clients=_intersection(
                self.allowed_clients,
                other.allowed_clients,
            ),
            allowed_models=_intersection(
                self.allowed_models,
                other.allowed_models,
            ),
            allowed_repositories=_intersection(
                self.allowed_repositories,
                other.allowed_repositories,
            ),
            max_classification=_context_classification_overlay(
                self.max_classification,
                other.max_classification,
            ),
            token_budget=_minimum(self.token_budget, other.token_budget),
            max_items=_minimum(self.max_items, other.max_items),
        )


@dataclass(frozen=True)
class Identity:
    token_env: str
    token: str = field(repr=False)
    actor_id: str
    actor_name: str
    team_id: str
    team_name: str
    allowed_clients: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    organization_id: str = "organization"
    clearance: str = "internal"
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
    login: "OIDCLoginConfig | None" = None


@dataclass(frozen=True)
class OIDCLoginConfig:
    client_id: str
    client_secret_env: str
    client_secret: str = field(repr=False)
    scopes: tuple[str, ...] = ("openid",)
    token_endpoint_auth_method: str = "client_secret_basic"


@dataclass(frozen=True)
class SessionBrokerConfig:
    enabled: bool = False
    database_path: Path | None = None
    public_base_url: str | None = None
    master_key_env: str | None = None
    master_key: bytes = field(default=b"", repr=False)
    master_key_source: str = field(default="", repr=False)
    access_ttl_seconds: int = 600
    absolute_ttl_seconds: int = 43_200
    enrollment_ttl_seconds: int = 300
    allow_insecure_http: bool = False


@dataclass(frozen=True)
class ModelRoute:
    alias: str
    protocol: str
    upstream_model: str
    rate_card_version: str = "unversioned"
    currency: str = "USD"
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
class ModelUsageLimit:
    """Monthly capacity limits for one governed model alias."""

    monthly_token_limit: int | None = None
    monthly_budget_usd: float | None = None
    per_actor_monthly_token_limit: int | None = None
    per_actor_monthly_budget_usd: float | None = None

    def overlaid(self, other: "ModelUsageLimit") -> "ModelUsageLimit":
        return ModelUsageLimit(
            monthly_token_limit=_minimum(
                self.monthly_token_limit,
                other.monthly_token_limit,
            ),
            monthly_budget_usd=_minimum(
                self.monthly_budget_usd,
                other.monthly_budget_usd,
            ),
            per_actor_monthly_token_limit=_minimum(
                self.per_actor_monthly_token_limit,
                other.per_actor_monthly_token_limit,
            ),
            per_actor_monthly_budget_usd=_minimum(
                self.per_actor_monthly_budget_usd,
                other.per_actor_monthly_budget_usd,
            ),
        )


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
    model_limits: dict[str, ModelUsageLimit] = field(default_factory=dict)
    context_injection: ContextInjectionPolicy = field(
        default_factory=ContextInjectionPolicy
    )

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
            model_limits=_overlay_model_limits(
                self.model_limits,
                other.model_limits,
            ),
            context_injection=self.context_injection.overlaid(
                other.context_injection
            ),
        )


@dataclass(frozen=True)
class GatewayConfig:
    source_path: Path
    listen: ListenConfig
    database_path: Path
    context_database_path: Path
    upstreams: dict[str, UpstreamConfig]
    identities_by_token: dict[str, Identity]
    model_routes: dict[str, ModelRoute]
    organization_policy: Policy
    source_sha256: str
    context_service: ContextServiceConfig = field(default_factory=ContextServiceConfig)
    session_broker: SessionBrokerConfig = field(default_factory=SessionBrokerConfig)
    oidc_issuers: dict[str, OIDCIssuerConfig] = field(default_factory=dict)
    identities_by_subject: dict[tuple[str, str], Identity] = field(default_factory=dict)
    secret_controls: SecretControls = field(default_factory=SecretControls)
    dlp_controls: DLPControls = field(default_factory=DLPControls)
    team_dlp_overlays: dict[str, DLPPolicyOverlay] = field(default_factory=dict)
    actor_dlp_overlays: dict[str, DLPPolicyOverlay] = field(default_factory=dict)
    team_policies: dict[str, Policy] = field(default_factory=dict)
    actor_policies: dict[str, Policy] = field(default_factory=dict)
    max_request_bytes: int = 25 * 1024 * 1024
    upstream_timeout_seconds: int = 600

    @classmethod
    def load(
        cls,
        path: str | Path,
        *,
        environ: dict[str, str] | None = None,
        expected_sha256: str | None = None,
    ) -> "GatewayConfig":
        source_path = Path(path).expanduser().resolve()
        if expected_sha256 is not None and _SHA256_PATTERN.fullmatch(expected_sha256) is None:
            raise ConfigError(
                "Expected configuration SHA-256 must be 64 lowercase hexadecimal characters"
            )
        raw, source_sha256 = _load_configuration_json(source_path)
        if expected_sha256 is not None and not hmac.compare_digest(
            source_sha256,
            expected_sha256,
        ):
            raise ConfigError("Configuration digest mismatch")
        if not isinstance(raw, dict):
            raise ConfigError("Gateway configuration must be a JSON object")

        _reject_unknown_fields(
            raw,
            {
                "listen",
                "database",
                "context_database",
                "context_service",
                "max_request_bytes",
                "upstream_timeout_seconds",
                "upstreams",
                "authentication",
                "identities",
                "model_routes",
                "egress_controls",
                "policies",
            },
            "gateway configuration",
        )

        env = os.environ if environ is None else environ
        listen_raw = _object(raw.get("listen", {}), "listen")
        _reject_unknown_fields(
            listen_raw,
            {
                "host",
                "port",
                "shutdown_grace_seconds",
                "max_concurrent_requests",
                "accept_backlog",
                "max_connections",
                "request_header_timeout_seconds",
                "request_body_timeout_seconds",
            },
            "listen",
        )
        host = _string(listen_raw.get("host", "127.0.0.1"), "listen.host")
        port = _integer(listen_raw.get("port", 8787), "listen.port", minimum=1, maximum=65535)
        shutdown_grace_seconds = _integer(
            listen_raw.get("shutdown_grace_seconds", 30),
            "listen.shutdown_grace_seconds",
            minimum=1,
            maximum=300,
        )
        max_concurrent_requests = _integer(
            listen_raw.get("max_concurrent_requests", 128),
            "listen.max_concurrent_requests",
            minimum=1,
            maximum=10_000,
        )
        accept_backlog = _integer(
            listen_raw.get("accept_backlog", 256),
            "listen.accept_backlog",
            minimum=1,
            maximum=65_535,
        )
        max_connections = _integer(
            listen_raw.get("max_connections", 256),
            "listen.max_connections",
            minimum=1,
            maximum=10_000,
        )
        request_header_timeout_seconds = _integer(
            listen_raw.get("request_header_timeout_seconds", 15),
            "listen.request_header_timeout_seconds",
            minimum=1,
            maximum=120,
        )
        request_body_timeout_seconds = _integer(
            listen_raw.get("request_body_timeout_seconds", 30),
            "listen.request_body_timeout_seconds",
            minimum=1,
            maximum=600,
        )

        database_value = _string(raw.get("database", "./hormuz.sqlite3"), "database")
        database_path = Path(database_value).expanduser()
        if not database_path.is_absolute():
            database_path = (source_path.parent / database_path).resolve()

        context_database_value = _string(
            raw.get("context_database", "./hormuz-context.sqlite3"),
            "context_database",
        )
        context_database_path = Path(context_database_value).expanduser()
        if not context_database_path.is_absolute():
            context_database_path = (source_path.parent / context_database_path).resolve()
        if context_database_path == database_path:
            raise ConfigError("context_database must be separate from the usage database")

        upstreams_raw = _object(raw.get("upstreams"), "upstreams")
        _reject_unknown_fields(upstreams_raw, {"openai", "anthropic"}, "upstreams")
        upstreams: dict[str, UpstreamConfig] = {}
        for protocol in ("openai", "anthropic"):
            item = _object(upstreams_raw.get(protocol), f"upstreams.{protocol}")
            _reject_unknown_fields(
                item,
                {
                    "base_url",
                    "api_key_env",
                    "allow_response_storage",
                    "allow_background",
                },
                f"upstreams.{protocol}",
            )
            upstream_path = f"upstreams.{protocol}.base_url"
            base_url = _url(item.get("base_url"), upstream_path).rstrip("/")
            _validate_upstream_transport(base_url, upstream_path)
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
            _reject_unknown_fields(
                item,
                {
                    "token_env",
                    "actor_id",
                    "actor_name",
                    "team_id",
                    "team_name",
                    "allowed_clients",
                    "capabilities",
                    "organization_id",
                    "clearance",
                },
                prefix,
            )
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
                capabilities=_identity_capabilities(
                    item.get("capabilities", []),
                    f"{prefix}.capabilities",
                ),
                organization_id=_string(item.get("organization_id", "organization"), f"{prefix}.organization_id"),
                clearance=_classification(item.get("clearance", "internal"), f"{prefix}.clearance"),
            )
            identities_by_token[token] = identity

        authentication_raw = _object(raw.get("authentication", {}), "authentication")
        _reject_unknown_fields(
            authentication_raw,
            {"oidc", "session_broker"},
            "authentication",
        )
        oidc_raw = _object(authentication_raw.get("oidc", {}), "authentication.oidc")
        _reject_unknown_fields(oidc_raw, {"issuers"}, "authentication.oidc")
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
            _reject_unknown_fields(
                item,
                {
                    "issuer",
                    "audiences",
                    "jwks_uri",
                    "algorithms",
                    "clock_skew_seconds",
                    "discovery_cache_seconds",
                    "allow_insecure_http",
                    "login",
                    "subjects",
                },
                prefix,
            )
            issuer = _url(item.get("issuer"), f"{prefix}.issuer")
            if issuer in oidc_issuers:
                raise ConfigError(f"OIDC issuer must be unique: {issuer}")
            login_config = _oidc_login_config(item.get("login"), env, prefix)
            audiences = _string_tuple(item.get("audiences", []), f"{prefix}.audiences")
            if not audiences and login_config is None:
                raise ConfigError(
                    f"{prefix} must configure at least one workload audience or a login client"
                )
            if login_config is not None and login_config.client_id in audiences:
                raise ConfigError(
                    f"{prefix}.audiences must not contain the OIDC login client_id; "
                    "ID tokens and workload access tokens need distinct audiences"
                )
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
                login=login_config,
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
                _reject_unknown_fields(
                    subject_item,
                    {
                        "subject",
                        "actor_id",
                        "actor_name",
                        "team_id",
                        "team_name",
                        "allowed_clients",
                        "capabilities",
                        "organization_id",
                        "clearance",
                    },
                    subject_prefix,
                )
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
                    capabilities=_identity_capabilities(
                        subject_item.get("capabilities", []),
                        f"{subject_prefix}.capabilities",
                    ),
                    organization_id=_string(
                        subject_item.get("organization_id", "organization"),
                        f"{subject_prefix}.organization_id",
                    ),
                    clearance=_classification(
                        subject_item.get("clearance", "internal"),
                        f"{subject_prefix}.clearance",
                    ),
                    authentication_source=f"oidc:{issuer}",
                )
        session_broker = _session_broker_config(
            authentication_raw.get("session_broker", {}),
            env,
            source_path=source_path,
        )
        if session_broker.enabled and not any(
            issuer.login is not None for issuer in oidc_issuers.values()
        ):
            raise ConfigError(
                "authentication.session_broker requires at least one OIDC issuer with login configuration"
            )
        if session_broker.database_path in {database_path, context_database_path}:
            raise ConfigError(
                "authentication.session_broker.database must be separate from usage and context databases"
            )
        if not identities_by_token and not identities_by_subject:
            raise ConfigError("At least one static identity or OIDC subject mapping is required")
        _validate_identity_consistency((*identities_by_token.values(), *identities_by_subject.values()))

        routes_raw = _object(raw.get("model_routes"), "model_routes")
        if not routes_raw:
            raise ConfigError("model_routes must contain at least one route")
        model_routes: dict[str, ModelRoute] = {}
        for alias, value in routes_raw.items():
            if not is_model_identifier(alias):
                raise ConfigError(
                    "model_routes keys must be safe model identifiers up to 512 characters"
                )
            item = _object(value, f"model_routes.{alias}")
            _reject_unknown_fields(
                item,
                {
                    "protocol",
                    "upstream_model",
                    "rate_card_version",
                    "currency",
                    "input_cost_per_million",
                    "cache_read_cost_per_million",
                    "cache_write_cost_per_million",
                    "output_cost_per_million",
                },
                f"model_routes.{alias}",
            )
            protocol = _string(item.get("protocol"), f"model_routes.{alias}.protocol")
            if protocol not in upstreams:
                raise ConfigError(f"model_routes.{alias}.protocol must be openai or anthropic")
            rate_card_version = _string(
                item.get("rate_card_version", "unversioned"),
                f"model_routes.{alias}.rate_card_version",
            )
            if len(rate_card_version.encode("utf-8")) > 128 or any(
                character in rate_card_version for character in ("\n", "\r", "\x00")
            ):
                raise ConfigError(
                    f"model_routes.{alias}.rate_card_version must be a bounded single-line string"
                )
            currency = _string(
                item.get("currency", "USD"),
                f"model_routes.{alias}.currency",
            ).upper()
            if currency != "USD":
                raise ConfigError(
                    f"model_routes.{alias}.currency must be USD while costs use micro-USD storage"
                )
            model_routes[alias] = ModelRoute(
                alias=alias,
                protocol=protocol,
                upstream_model=_model_identifier(
                    item.get("upstream_model"),
                    f"model_routes.{alias}.upstream_model",
                ),
                rate_card_version=rate_card_version,
                currency=currency,
                input_cost_per_million=_number(item.get("input_cost_per_million", 0), f"model_routes.{alias}.input_cost_per_million"),
                cache_read_cost_per_million=_number(item.get("cache_read_cost_per_million", 0), f"model_routes.{alias}.cache_read_cost_per_million"),
                cache_write_cost_per_million=_number(item.get("cache_write_cost_per_million", 0), f"model_routes.{alias}.cache_write_cost_per_million"),
                output_cost_per_million=_number(item.get("output_cost_per_million", 0), f"model_routes.{alias}.output_cost_per_million"),
            )

        egress_raw = _object(raw.get("egress_controls", {}), "egress_controls")
        _reject_unknown_fields(
            egress_raw,
            {"secrets", "dlp"},
            "egress_controls",
        )
        secret_controls = _secret_controls(egress_raw.get("secrets", {}), env)
        dlp_raw = egress_raw.get("dlp", {})
        dlp_controls = _dlp_controls(dlp_raw, env)
        team_dlp_overlays, actor_dlp_overlays = _dlp_overlays(
            dlp_raw,
            dlp_controls,
        )
        context_service_raw = _object(raw.get("context_service", {}), "context_service")
        _reject_unknown_fields(
            context_service_raw,
            {
                "policy_version",
                "max_token_budget",
                "max_items",
                "requests_per_minute",
                "allow_provisional",
                "lifecycle",
            },
            "context_service",
        )
        context_service = ContextServiceConfig(
            policy_version=_string(
                context_service_raw.get("policy_version", "local-v1"),
                "context_service.policy_version",
            ),
            max_token_budget=_integer(
                context_service_raw.get("max_token_budget", 32_768),
                "context_service.max_token_budget",
                minimum=1,
                maximum=1_000_000,
            ),
            max_items=_integer(
                context_service_raw.get("max_items", 20),
                "context_service.max_items",
                minimum=1,
                maximum=100,
            ),
            requests_per_minute=_integer(
                context_service_raw.get("requests_per_minute", 60),
                "context_service.requests_per_minute",
                minimum=1,
                maximum=10_000,
            ),
            allow_provisional=_boolean(
                context_service_raw.get("allow_provisional", False),
                "context_service.allow_provisional",
            ),
            lifecycle=_context_lifecycle_automation_config(
                context_service_raw.get("lifecycle", {})
            ),
        )
        policies_raw = _object(raw.get("policies"), "policies")
        _reject_unknown_fields(
            policies_raw,
            {"organization", "teams", "actors"},
            "policies",
        )
        organization_policy = _policy(
            policies_raw.get("organization"),
            "policies.organization",
            default_context_injection_mode="off",
            default_context_allowed_repositories=(),
        )
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
            listen=ListenConfig(
                host=host,
                port=port,
                shutdown_grace_seconds=shutdown_grace_seconds,
                max_concurrent_requests=max_concurrent_requests,
                accept_backlog=accept_backlog,
                max_connections=max_connections,
                request_header_timeout_seconds=request_header_timeout_seconds,
                request_body_timeout_seconds=request_body_timeout_seconds,
            ),
            database_path=database_path,
            context_database_path=context_database_path,
            upstreams=upstreams,
            identities_by_token=identities_by_token,
            model_routes=model_routes,
            organization_policy=organization_policy,
            source_sha256=source_sha256,
            context_service=context_service,
            session_broker=session_broker,
            oidc_issuers=oidc_issuers,
            identities_by_subject=identities_by_subject,
            secret_controls=secret_controls,
            dlp_controls=dlp_controls,
            team_dlp_overlays=team_dlp_overlays,
            actor_dlp_overlays=actor_dlp_overlays,
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
            for alias in policy.model_limits:
                if alias not in self.model_routes:
                    raise ConfigError(
                        f"Policy model_limits references unknown model alias: {alias}"
                    )
            if policy.fallback_model is not None and policy.fallback_model not in self.model_routes:
                raise ConfigError(f"Policy references unknown fallback model alias: {policy.fallback_model}")
            for protocol, alias in (policy.fallback_models or {}).items():
                route = self.model_routes.get(alias)
                if route is None:
                    raise ConfigError(f"Policy references unknown fallback model alias: {alias}")
                if route.protocol != protocol:
                    raise ConfigError(f"Policy fallback {alias} does not use protocol {protocol}")
            for alias in policy.context_injection.allowed_models or ():
                if alias not in self.model_routes:
                    raise ConfigError(
                        f"Context injection policy references unknown model alias: {alias}"
                    )
        limits_require_request_bound = any(
            policy.monthly_token_limit is not None
            or policy.monthly_budget_usd is not None
            or policy.per_actor_monthly_budget_usd is not None
            or bool(policy.model_limits)
            for policy in policies
        )
        if limits_require_request_bound:
            for identity in self.identities_by_actor.values():
                if self.resolved_policy(identity).max_output_tokens is None:
                    raise ConfigError(
                        f"Identity {identity.actor_id} needs an effective max_output_tokens policy "
                        "when monthly token or budget limits are configured"
                    )
        team_organizations: dict[str, set[str]] = {}
        for identity in self.identities_by_actor.values():
            team_organizations.setdefault(identity.team_id, set()).add(
                identity.organization_id
            )
        if set(self.team_policies) - set(team_organizations):
            raise ConfigError("Policies reference unknown teams")
        if any(
            len(team_organizations[team_id]) != 1
            for team_id in self.team_policies
        ):
            raise ConfigError(
                "Policy team IDs must identify exactly one organization"
            )
        if set(self.actor_policies) - set(self.identities_by_actor):
            raise ConfigError("Policies reference unknown actors")
        unknown_dlp_teams = sorted(
            set(self.team_dlp_overlays) - set(team_organizations)
        )
        if unknown_dlp_teams:
            raise ConfigError(
                "DLP overlays reference unknown teams: " + ", ".join(unknown_dlp_teams)
            )
        ambiguous_dlp_teams = sorted(
            team_id
            for team_id in self.team_dlp_overlays
            if len(team_organizations[team_id]) != 1
        )
        if ambiguous_dlp_teams:
            raise ConfigError(
                "DLP overlay team IDs must identify exactly one organization: "
                + ", ".join(ambiguous_dlp_teams)
            )
        unknown_dlp_actors = sorted(
            set(self.actor_dlp_overlays) - set(self.identities_by_actor)
        )
        if unknown_dlp_actors:
            raise ConfigError(
                "DLP overlays reference unknown actors: " + ", ".join(unknown_dlp_actors)
            )
        all_dlp_rules = (
            *self.dlp_controls.rules,
            *(rule for overlay in self.team_dlp_overlays.values() for rule in overlay.rules),
            *(rule for overlay in self.actor_dlp_overlays.values() for rule in overlay.rules),
        )
        for rule in all_dlp_rules:
            for model in rule.models:
                matching_routes = tuple(
                    route
                    for route in self.model_routes.values()
                    if route.upstream_model == model and route.protocol in rule.providers
                )
                if not matching_routes:
                    raise ConfigError(
                        f"DLP rule {rule.rule_id} model {model} must match a routed upstream "
                        "model for one of the rule's providers"
                    )
        if self.dlp_controls.approval.enabled and any(
            rule.action == "require_approval" for rule in all_dlp_rules
        ):
            organization_ids = {
                identity.organization_id for identity in self.identities_by_actor.values()
            }
            approver_organizations = {
                identity.organization_id
                for identity in self.identities_by_actor.values()
                if "dlp_approver" in identity.capabilities
            }
            missing = sorted(organization_ids - approver_organizations)
            if missing:
                raise ConfigError(
                    "DLP approval is enabled but these organizations have no dlp_approver: "
                    + ", ".join(missing)
                )
        if self.context_service.lifecycle.enabled:
            organization_ids = {
                identity.organization_id for identity in self.identities_by_actor.values()
            }
            promoter_organizations = {
                identity.organization_id
                for identity in self.identities_by_actor.values()
                if "context_promoter" in identity.capabilities
            }
            missing = sorted(organization_ids - promoter_organizations)
            if missing:
                raise ConfigError(
                    "Context lifecycle automation is enabled but these organizations have no "
                    "context_promoter: " + ", ".join(missing)
                )

    def identity_for_token(self, token: str) -> Identity | None:
        return self.identities_by_token.get(token)

    @property
    def identities_by_actor(self) -> dict[str, Identity]:
        result: dict[str, Identity] = {}
        for identity in (*self.identities_by_token.values(), *self.identities_by_subject.values()):
            result.setdefault(identity.actor_id, identity)
        return result

    def identity_for_subject(self, issuer: str, subject: str) -> Identity | None:
        return self.identities_by_subject.get((issuer, subject))

    def resolved_policy(self, identity: Identity) -> Policy:
        return (
            self.organization_policy
            .overlaid(self.team_policies.get(identity.team_id))
            .overlaid(self.actor_policies.get(identity.actor_id))
        )

    def resolved_dlp_controls(
        self,
        identity: Identity,
        *,
        protocol: str,
        model: str,
    ) -> DLPControls:
        overlay_items: list[tuple[str, str, DLPPolicyOverlay]] = []
        team_overlay = self.team_dlp_overlays.get(identity.team_id)
        if team_overlay is not None:
            overlay_items.append(("team", identity.team_id, team_overlay))
        actor_overlay = self.actor_dlp_overlays.get(identity.actor_id)
        if actor_overlay is not None:
            overlay_items.append(("actor", identity.actor_id, actor_overlay))
        overlays = tuple(overlay_items)
        if not overlays:
            return self.dlp_controls

        effective_rules: list[DLPRuleConfig] = []
        for organization_rule in self.dlp_controls.rules:
            if not organization_rule.applies_to(protocol=protocol, model=model):
                continue
            action = organization_rule.action
            for _, _, overlay in overlays:
                for override in overlay.rules:
                    if (
                        override.rule_id == organization_rule.rule_id
                        and override.applies_to(protocol=protocol, model=model)
                        and _DLP_ACTION_RANK[override.action] > _DLP_ACTION_RANK[action]
                    ):
                        action = override.action
            effective_rules.append(
                DLPRuleConfig(
                    rule_id=organization_rule.rule_id,
                    category=organization_rule.category,
                    confidence=organization_rule.confidence,
                    action=action,
                    providers=(protocol,),
                    models=(model,),
                    values_env=organization_rule.values_env,
                    exact_values=organization_rule.exact_values,
                )
            )

        return DLPControls(
            policy_version=_effective_dlp_policy_version(
                self.dlp_controls,
                overlays,
            ),
            rules=tuple(effective_rules),
            approval=self.dlp_controls.approval,
        )


def _policy(
    value: Any,
    path: str,
    *,
    default_context_injection_mode: str | None = None,
    default_context_allowed_repositories: tuple[str, ...] | None = None,
) -> Policy:
    item = _object(value, path)
    _reject_unknown_fields(
        item,
        {
            "allowed_clients",
            "allowed_models",
            "fallback_model",
            "fallback_models",
            "max_output_tokens",
            "monthly_token_limit",
            "monthly_budget_usd",
            "per_actor_monthly_budget_usd",
            "model_limits",
            "context_injection",
        },
        path,
    )
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
        model_limits=_model_usage_limits(
            item.get("model_limits", {}),
            f"{path}.model_limits",
        ),
        context_injection=_context_injection_policy(
            item.get("context_injection", {}),
            f"{path}.context_injection",
            default_mode=default_context_injection_mode,
            default_allowed_repositories=default_context_allowed_repositories,
        ),
    )


def _model_usage_limits(value: Any, path: str) -> dict[str, ModelUsageLimit]:
    items = _object(value, path)
    limits: dict[str, ModelUsageLimit] = {}
    allowed_fields = {
        "monthly_token_limit",
        "monthly_budget_usd",
        "per_actor_monthly_token_limit",
        "per_actor_monthly_budget_usd",
    }
    for alias, raw_limit in items.items():
        if not is_model_identifier(alias):
            raise ConfigError(
                f"{path} keys must be safe model identifiers up to 512 characters"
            )
        limit_path = f"{path}.{alias}"
        item = _object(raw_limit, limit_path)
        _reject_unknown_fields(item, allowed_fields, limit_path)
        limit = ModelUsageLimit(
            monthly_token_limit=_optional_integer(
                item.get("monthly_token_limit"),
                f"{limit_path}.monthly_token_limit",
                minimum=1,
            ),
            monthly_budget_usd=_optional_number(
                item.get("monthly_budget_usd"),
                f"{limit_path}.monthly_budget_usd",
            ),
            per_actor_monthly_token_limit=_optional_integer(
                item.get("per_actor_monthly_token_limit"),
                f"{limit_path}.per_actor_monthly_token_limit",
                minimum=1,
            ),
            per_actor_monthly_budget_usd=_optional_number(
                item.get("per_actor_monthly_budget_usd"),
                f"{limit_path}.per_actor_monthly_budget_usd",
            ),
        )
        if not any(
            value is not None
            for value in (
                limit.monthly_token_limit,
                limit.monthly_budget_usd,
                limit.per_actor_monthly_token_limit,
                limit.per_actor_monthly_budget_usd,
            )
        ):
            raise ConfigError(f"{limit_path} must configure at least one limit")
        limits[alias] = limit
    return limits


def _context_injection_policy(
    value: Any,
    path: str,
    *,
    default_mode: str | None = None,
    default_allowed_repositories: tuple[str, ...] | None = None,
) -> ContextInjectionPolicy:
    item = _object(value, path)
    _reject_unknown_fields(
        item,
        {
            "mode",
            "allowed_clients",
            "allowed_models",
            "allowed_repositories",
            "max_classification",
            "token_budget",
            "max_items",
        },
        path,
    )
    mode_value = item.get("mode", default_mode)
    mode = _optional_string(mode_value, f"{path}.mode")
    if mode not in {None, "off", "optional", "required"}:
        raise ConfigError(f"{path}.mode must be off, optional, or required")
    token_budget = item.get("token_budget")
    if token_budget is not None:
        token_budget = _integer(
            token_budget,
            f"{path}.token_budget",
            minimum=1,
            maximum=1_000_000,
        )
    max_items = item.get("max_items")
    if max_items is not None:
        max_items = _integer(
            max_items,
            f"{path}.max_items",
            minimum=1,
            maximum=100,
        )
    allowed_repositories = (
        _optional_string_tuple(
            item.get("allowed_repositories"),
            f"{path}.allowed_repositories",
        )
        if "allowed_repositories" in item
        else default_allowed_repositories
    )
    for repository_id in allowed_repositories or ():
        if not is_context_selector(repository_id):
            raise ConfigError(
                f"{path}.allowed_repositories values must be safe exact selectors up to 512 characters"
            )
    max_classification = item.get("max_classification")
    if max_classification is not None:
        max_classification = _classification(
            max_classification,
            f"{path}.max_classification",
        )
    return ContextInjectionPolicy(
        mode=mode,
        allowed_clients=_optional_string_tuple(
            item.get("allowed_clients"),
            f"{path}.allowed_clients",
        ),
        allowed_models=_optional_string_tuple(
            item.get("allowed_models"),
            f"{path}.allowed_models",
        ),
        allowed_repositories=allowed_repositories,
        max_classification=max_classification,
        token_budget=token_budget,
        max_items=max_items,
    )


def _oidc_login_config(
    value: Any,
    env: dict[str, str],
    issuer_path: str,
) -> OIDCLoginConfig | None:
    if value is None:
        return None
    path = f"{issuer_path}.login"
    item = _object(value, path)
    _reject_unknown_fields(
        item,
        {"client_id", "client_secret_env", "scopes", "token_endpoint_auth_method"},
        path,
    )
    client_secret_env = _string(item.get("client_secret_env"), f"{path}.client_secret_env")
    client_secret = env.get(client_secret_env, "")
    if not client_secret:
        raise ConfigError(
            f"Required OIDC login client secret environment variable is not set: {client_secret_env}"
        )
    if len(client_secret) < 16:
        raise ConfigError(f"OIDC login client secret from {client_secret_env} must be at least 16 characters")
    scopes = _string_tuple(item.get("scopes", ["openid"]), f"{path}.scopes")
    if "openid" not in scopes:
        raise ConfigError(f"{path}.scopes must include openid")
    auth_method = _string(
        item.get("token_endpoint_auth_method", "client_secret_basic"),
        f"{path}.token_endpoint_auth_method",
    )
    if auth_method not in {"client_secret_basic", "client_secret_post"}:
        raise ConfigError(
            f"{path}.token_endpoint_auth_method must be client_secret_basic or client_secret_post"
        )
    return OIDCLoginConfig(
        client_id=_string(item.get("client_id"), f"{path}.client_id"),
        client_secret_env=client_secret_env,
        client_secret=client_secret,
        scopes=scopes,
        token_endpoint_auth_method=auth_method,
    )


def _session_broker_config(
    value: Any,
    env: dict[str, str],
    *,
    source_path: Path,
) -> SessionBrokerConfig:
    path = "authentication.session_broker"
    item = _object(value, path)
    _reject_unknown_fields(
        item,
        {
            "enabled",
            "database",
            "public_base_url",
            "master_key_env",
            "access_ttl_seconds",
            "absolute_ttl_seconds",
            "enrollment_ttl_seconds",
            "allow_insecure_http",
        },
        path,
    )
    enabled = _boolean(item.get("enabled", False), f"{path}.enabled")
    if not enabled:
        return SessionBrokerConfig()

    database_value = _string(item.get("database"), f"{path}.database")
    database_path = Path(database_value).expanduser()
    if not database_path.is_absolute():
        database_path = (source_path.parent / database_path).resolve()
    public_base_url = _url(item.get("public_base_url"), f"{path}.public_base_url").rstrip("/")
    if urlparse(public_base_url).path not in {"", "/"}:
        raise ConfigError(f"{path}.public_base_url must not include a path")
    allow_insecure_http = _boolean(
        item.get("allow_insecure_http", False),
        f"{path}.allow_insecure_http",
    )
    _validate_oidc_transport(
        issuer=public_base_url,
        jwks_uri=None,
        allow_insecure_http=allow_insecure_http,
        path=path,
    )
    master_key_env = _string(item.get("master_key_env"), f"{path}.master_key_env")
    encoded_master_key = env.get(master_key_env, "")
    if not encoded_master_key:
        raise ConfigError(f"Required session broker master key environment variable is not set: {master_key_env}")
    try:
        padding = "=" * (-len(encoded_master_key) % 4)
        master_key = base64.b64decode(
            encoded_master_key + padding,
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, binascii.Error) as error:
        raise ConfigError(f"Session broker master key from {master_key_env} must be base64url") from error
    if len(master_key) != 32:
        raise ConfigError(f"Session broker master key from {master_key_env} must decode to exactly 32 bytes")

    access_ttl = _integer(
        item.get("access_ttl_seconds", 600),
        f"{path}.access_ttl_seconds",
        minimum=300,
        maximum=900,
    )
    absolute_ttl = _integer(
        item.get("absolute_ttl_seconds", 43_200),
        f"{path}.absolute_ttl_seconds",
        minimum=access_ttl,
        maximum=43_200,
    )
    enrollment_ttl = _integer(
        item.get("enrollment_ttl_seconds", 300),
        f"{path}.enrollment_ttl_seconds",
        minimum=60,
        maximum=600,
    )
    return SessionBrokerConfig(
        enabled=True,
        database_path=database_path,
        public_base_url=public_base_url,
        master_key_env=master_key_env,
        master_key=master_key,
        master_key_source=encoded_master_key,
        access_ttl_seconds=access_ttl,
        absolute_ttl_seconds=absolute_ttl,
        enrollment_ttl_seconds=enrollment_ttl,
        allow_insecure_http=allow_insecure_http,
    )


def _secret_controls(value: Any, env: dict[str, str]) -> SecretControls:
    item = _object(value, "egress_controls.secrets")
    _reject_unknown_fields(
        item,
        {"mode", "builtins", "custom_secret_envs"},
        "egress_controls.secrets",
    )
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


_DLP_ACTIONS = {"off", "detect", "redact", "deny", "require_approval"}
_DLP_ACTION_RANK = {
    "off": 0,
    "detect": 1,
    "redact": 2,
    "require_approval": 3,
    "deny": 4,
}
_DLP_BUILTINS = {
    "us_ssn": ("regulated_identifier", "high", "redact"),
    "payment_card": ("regulated_identifier", "high", "redact"),
    "email_address": ("pii", "low", "detect"),
    "opaque_media": ("unsupported_media", "high", "deny"),
}
_DLP_RULE_ID = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z")
_CONTEXT_SELECTOR = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/@:+-]{0,511}\Z")
_MODEL_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,511}\Z")


def _dlp_controls(value: Any, env: dict[str, str]) -> DLPControls:
    path = "egress_controls.dlp"
    item = _object(value, path)
    _reject_unknown_fields(
        item,
        {"policy_version", "rules", "dictionaries", "approval", "overlays"},
        path,
    )
    policy_version = _bounded_policy_version(
        item.get("policy_version", "local-dlp-v1"),
        f"{path}.policy_version",
    )
    configured_rules = _object(item.get("rules", {}), f"{path}.rules")
    unknown_rules = sorted(set(configured_rules) - set(_DLP_BUILTINS))
    if unknown_rules:
        raise ConfigError(f"Unknown {path}.rules")

    rules: list[DLPRuleConfig] = []
    for rule_id, (category, confidence, default_action) in _DLP_BUILTINS.items():
        rule_path = f"{path}.rules.{rule_id}"
        rule = _object(configured_rules.get(rule_id, {}), rule_path)
        action, providers, models = _dlp_rule_scope(
            rule,
            rule_path,
            default_action=default_action,
        )
        if rule_id == "opaque_media" and action not in {"off", "deny"}:
            raise ConfigError(
                f"{rule_path}.action must be off or deny because Hormuz cannot safely transform opaque media"
            )
        if action != "off":
            rules.append(
                DLPRuleConfig(
                    rule_id=rule_id,
                    category=category,
                    confidence=confidence,
                    action=action,
                    providers=providers,
                    models=models,
                )
            )

    dictionaries = item.get("dictionaries", [])
    if not isinstance(dictionaries, list):
        raise ConfigError(f"{path}.dictionaries must be an array")
    if len(dictionaries) > 100:
        raise ConfigError(f"{path}.dictionaries cannot contain more than 100 rules")
    known_ids = set(_DLP_BUILTINS)
    for index, value in enumerate(dictionaries):
        rule_path = f"{path}.dictionaries[{index}]"
        rule = _object(value, rule_path)
        _reject_unknown_fields(
            rule,
            {
                "rule_id",
                "category",
                "confidence",
                "action",
                "providers",
                "models",
                "values_env",
            },
            rule_path,
        )
        rule_id = _dlp_identifier(rule.get("rule_id"), f"{rule_path}.rule_id")
        if rule_id in known_ids:
            raise ConfigError(f"Duplicate DLP rule_id: {rule_id}")
        known_ids.add(rule_id)
        category = _dlp_identifier(
            rule.get("category", "company_dictionary"),
            f"{rule_path}.category",
        )
        confidence = _string(rule.get("confidence", "high"), f"{rule_path}.confidence")
        if confidence not in {"low", "medium", "high"}:
            raise ConfigError(f"{rule_path}.confidence must be low, medium, or high")
        action, providers, models = _dlp_rule_scope(
            rule,
            rule_path,
            default_action="detect",
            allowed_fields={
                "rule_id",
                "category",
                "confidence",
                "action",
                "providers",
                "models",
                "values_env",
            },
        )
        if action == "off":
            continue
        values_env = _string(rule.get("values_env"), f"{rule_path}.values_env")
        exact_values = _dlp_dictionary_values(env.get(values_env), values_env, rule_path)
        rules.append(
            DLPRuleConfig(
                rule_id=rule_id,
                category=category,
                confidence=confidence,
                action=action,
                providers=providers,
                models=models,
                values_env=values_env,
                exact_values=exact_values,
            )
        )
    approval = _dlp_approval_config(item.get("approval", {}), env)
    return DLPControls(
        policy_version=policy_version,
        rules=tuple(rules),
        approval=approval,
    )


def _dlp_overlays(
    value: Any,
    organization: DLPControls,
) -> tuple[dict[str, DLPPolicyOverlay], dict[str, DLPPolicyOverlay]]:
    path = "egress_controls.dlp.overlays"
    dlp_item = _object(value, "egress_controls.dlp")
    item = _object(dlp_item.get("overlays", {}), path)
    _reject_unknown_fields(item, {"teams", "actors"}, path)
    teams = _dlp_overlay_map(item.get("teams", {}), f"{path}.teams", organization)
    actors = _dlp_overlay_map(item.get("actors", {}), f"{path}.actors", organization)
    return teams, actors


def _dlp_overlay_map(
    value: Any,
    path: str,
    organization: DLPControls,
) -> dict[str, DLPPolicyOverlay]:
    item = _object(value, path)
    if len(item) > 10_000:
        raise ConfigError(f"{path} cannot contain more than 10000 scopes")
    overlays: dict[str, DLPPolicyOverlay] = {}
    for raw_scope_id, raw_overlay in item.items():
        scope_id = _bounded_scope_id(raw_scope_id, f"{path} scope")
        overlays[scope_id] = _dlp_overlay(
            raw_overlay,
            f"{path}.{scope_id}",
            organization,
        )
    return overlays


def _dlp_overlay(
    value: Any,
    path: str,
    organization: DLPControls,
) -> DLPPolicyOverlay:
    item = _object(value, path)
    _reject_unknown_fields(item, {"policy_version", "rules"}, path)
    policy_version = _bounded_policy_version(
        item.get("policy_version"),
        f"{path}.policy_version",
    )
    raw_rules = _object(item.get("rules"), f"{path}.rules")
    if not raw_rules:
        raise ConfigError(f"{path}.rules must contain at least one tightening rule")
    if len(raw_rules) > len(organization.rules):
        raise ConfigError(f"{path}.rules cannot contain more rules than organization DLP")
    organization_rules = {rule.rule_id: rule for rule in organization.rules}
    rules: list[DLPRuleConfig] = []
    for raw_rule_id, raw_rule in raw_rules.items():
        rule_id = _dlp_identifier(raw_rule_id, f"{path}.rules rule_id")
        base = organization_rules.get(rule_id)
        if base is None:
            raise ConfigError(
                f"{path}.rules.{rule_id} must reference an enabled organization DLP rule"
            )
        rule_path = f"{path}.rules.{rule_id}"
        rule = _object(raw_rule, rule_path)
        _reject_unknown_fields(rule, {"action", "providers", "models"}, rule_path)
        action = _string(rule.get("action"), f"{rule_path}.action")
        if action not in _DLP_ACTIONS - {"off"}:
            raise ConfigError(
                f"{rule_path}.action must be detect, redact, deny, or require_approval"
            )
        if _DLP_ACTION_RANK[action] <= _DLP_ACTION_RANK[base.action]:
            raise ConfigError(
                f"{rule_path}.action must be stricter than organization action {base.action}"
            )
        providers = base.providers
        if "providers" in rule:
            providers = _string_tuple(rule["providers"], f"{rule_path}.providers")
            if not providers or not set(providers).issubset(base.providers):
                raise ConfigError(
                    f"{rule_path}.providers must be a non-empty subset of organization providers"
                )
            providers = tuple(dict.fromkeys(providers))
        models = base.models
        if "models" in rule:
            models = _string_tuple(rule["models"], f"{rule_path}.models")
            if not models:
                raise ConfigError(
                    f"{rule_path}.models must be a non-empty model scope"
                )
            if base.models and not set(models).issubset(base.models):
                raise ConfigError(
                    f"{rule_path}.models must be a non-empty subset of organization models"
                )
            models = tuple(dict.fromkeys(models))
        rules.append(
            DLPRuleConfig(
                rule_id=base.rule_id,
                category=base.category,
                confidence=base.confidence,
                action=action,
                providers=providers,
                models=models,
                values_env=base.values_env,
                exact_values=base.exact_values,
            )
        )
    return DLPPolicyOverlay(policy_version=policy_version, rules=tuple(rules))


def _effective_dlp_policy_version(
    organization: DLPControls,
    overlays: tuple[tuple[str, str, DLPPolicyOverlay], ...],
) -> str:
    def rule_metadata(rule: DLPRuleConfig) -> dict[str, object]:
        return {
            "rule_id": rule.rule_id,
            "category": rule.category,
            "confidence": rule.confidence,
            "action": rule.action,
            "providers": rule.providers,
            "models": rule.models,
            "values_env": rule.values_env,
        }

    payload = {
        "schema": "hormuz.dlp-effective-policy.v1",
        "organization": {
            "policy_version": organization.policy_version,
            "approval": {
                "enabled": organization.approval.enabled,
                "fingerprint_key_env": organization.approval.fingerprint_key_env,
            },
            "rules": [
                rule_metadata(rule)
                for rule in sorted(organization.rules, key=lambda item: item.rule_id)
            ],
        },
        "overlays": [
            {
                "scope_type": scope_type,
                "scope_id": scope_id,
                "policy_version": overlay.policy_version,
                "rules": [
                    rule_metadata(rule)
                    for rule in sorted(overlay.rules, key=lambda item: item.rule_id)
                ],
            }
            for scope_type, scope_id, overlay in overlays
        ],
    }
    canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return "dlp-effective-v1:" + hashlib.sha256(canonical).hexdigest()[:32]


def _dlp_approval_config(value: Any, env: dict[str, str]) -> DLPApprovalConfig:
    path = "egress_controls.dlp.approval"
    item = _object(value, path)
    _reject_unknown_fields(item, {"enabled", "fingerprint_key_env"}, path)
    enabled = _boolean(item.get("enabled", False), f"{path}.enabled")
    if not enabled:
        return DLPApprovalConfig()
    fingerprint_key_env = _string(
        item.get("fingerprint_key_env"),
        f"{path}.fingerprint_key_env",
    )
    encoded_key = env.get(fingerprint_key_env, "")
    if not encoded_key:
        raise ConfigError(
            "Required DLP approval fingerprint key environment variable is not set: "
            + fingerprint_key_env
        )
    try:
        padding = "=" * (-len(encoded_key) % 4)
        fingerprint_key = base64.b64decode(
            encoded_key + padding,
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, binascii.Error) as error:
        raise ConfigError(
            f"DLP approval fingerprint key from {fingerprint_key_env} must be base64url"
        ) from error
    if len(fingerprint_key) != 32:
        raise ConfigError(
            f"DLP approval fingerprint key from {fingerprint_key_env} must decode to exactly 32 bytes"
        )
    return DLPApprovalConfig(
        enabled=True,
        fingerprint_key_env=fingerprint_key_env,
        fingerprint_key=fingerprint_key,
        fingerprint_key_source=encoded_key,
    )


def _dlp_rule_scope(
    value: dict[str, Any],
    path: str,
    *,
    default_action: str,
    allowed_fields: set[str] | None = None,
) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    allowed = {"action", "providers", "models"} if allowed_fields is None else allowed_fields
    _reject_unknown_fields(value, allowed, path)
    action = _string(value.get("action", default_action), f"{path}.action")
    if action not in _DLP_ACTIONS:
        raise ConfigError(
            f"{path}.action must be off, detect, redact, deny, or require_approval"
        )
    providers = _string_tuple(
        value.get("providers", ["openai", "anthropic"]),
        f"{path}.providers",
    )
    if not providers or any(provider not in {"openai", "anthropic"} for provider in providers):
        raise ConfigError(f"{path}.providers must contain openai and/or anthropic")
    models = _string_tuple(value.get("models", []), f"{path}.models")
    return action, tuple(dict.fromkeys(providers)), tuple(dict.fromkeys(models))


def _dlp_dictionary_values(raw: str | None, env_name: str, path: str) -> tuple[str, ...]:
    if not raw:
        raise ConfigError(f"Required DLP dictionary environment variable is not set: {env_name}")
    try:
        values = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ConfigError(f"DLP dictionary from {env_name} must be a JSON string array") from error
    if not isinstance(values, list) or not values or len(values) > 1000:
        raise ConfigError(f"DLP dictionary from {env_name} must contain 1 to 1000 strings")
    normalized: list[str] = []
    total_bytes = 0
    for index, value in enumerate(values):
        if not isinstance(value, str) or value != value.strip():
            raise ConfigError(f"{path} value {index} must be a trimmed string")
        byte_length = len(value.encode("utf-8"))
        if byte_length < 4 or byte_length > 512 or not all(character.isprintable() for character in value):
            raise ConfigError(f"{path} value {index} must be printable and 4 to 512 bytes")
        total_bytes += byte_length
        if total_bytes > 262_144:
            raise ConfigError(f"DLP dictionary from {env_name} exceeds 256 KiB")
        normalized.append(value)
    return tuple(dict.fromkeys(normalized))


def _dlp_identifier(value: Any, path: str) -> str:
    result = _string(value, path)
    if _DLP_RULE_ID.fullmatch(result) is None:
        raise ConfigError(f"{path} must be a lowercase safe identifier up to 64 characters")
    return result


def is_context_selector(value: object) -> bool:
    return isinstance(value, str) and _CONTEXT_SELECTOR.fullmatch(value) is not None


def is_model_identifier(value: object) -> bool:
    """Return whether a model alias is safe for policy, logs, and HTTP metadata."""

    return isinstance(value, str) and _MODEL_IDENTIFIER.fullmatch(value) is not None


def _context_lifecycle_automation_config(value: Any) -> ContextLifecycleAutomationConfig:
    path = "context_service.lifecycle"
    item = _object(value, path)
    _reject_unknown_fields(
        item,
        {
            "enabled",
            "policy_version",
            "promotion_paths",
            "job_batch_size",
            "lease_seconds",
        },
        path,
    )
    enabled = _boolean(item.get("enabled", False), f"{path}.enabled")
    promotion_paths = item.get("promotion_paths", [])
    if not isinstance(promotion_paths, list):
        raise ConfigError(f"{path}.promotion_paths must be an array")
    policy: LifecyclePolicy | None = None
    if promotion_paths:
        try:
            policy = LifecyclePolicy.from_dict(
                {
                    "policy_version": _bounded_policy_version(
                        item.get("policy_version", "engineering-lifecycle-v1"),
                        f"{path}.policy_version",
                    ),
                    "promotion_paths": promotion_paths,
                }
            )
        except ValueError as error:
            raise ConfigError(f"Invalid {path}: {error}") from error
    elif enabled:
        raise ConfigError(
            f"{path}.promotion_paths must contain at least one path when enabled"
        )
    return ContextLifecycleAutomationConfig(
        enabled=enabled,
        policy=policy,
        job_batch_size=_integer(
            item.get("job_batch_size", 100),
            f"{path}.job_batch_size",
            minimum=1,
            maximum=1_000,
        ),
        lease_seconds=_integer(
            item.get("lease_seconds", 30),
            f"{path}.lease_seconds",
            minimum=1,
            maximum=3_600,
        ),
    )


def _identity_capabilities(value: Any, path: str) -> tuple[str, ...]:
    capabilities = _string_tuple(value, path)
    supported = {"context_promoter", "dlp_approver", "session_admin", "usage_viewer"}
    unknown = sorted(set(capabilities) - supported)
    if unknown:
        raise ConfigError(f"Unknown {path}")
    return capabilities


def _bounded_policy_version(value: Any, path: str) -> str:
    result = _string(value, path)
    if len(result.encode("utf-8")) > 128 or not all(character.isprintable() for character in result):
        raise ConfigError(f"{path} must be a printable string up to 128 bytes")
    return result


def _bounded_scope_id(value: Any, path: str) -> str:
    result = _string(value, path)
    if len(result.encode("utf-8")) > 256 or not all(
        character.isprintable() for character in result
    ):
        raise ConfigError(f"{path} must be a printable string up to 256 bytes")
    return result


def _object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{path} must be an object")
    return value


def _reject_unknown_fields(
    value: dict[str, Any],
    allowed: set[str],
    path: str,
) -> None:
    """Reject misspelled configuration without reflecting attacker-chosen keys."""

    if set(value) - allowed:
        raise ConfigError(f"Unknown {path} fields")


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{path} must be a non-empty string")
    return value.strip()


def _model_identifier(value: Any, path: str) -> str:
    result = _string(value, path)
    if not is_model_identifier(result):
        raise ConfigError(f"{path} must be a safe model identifier up to 512 characters")
    return result


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


def _validate_upstream_transport(value: str, path: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme == "https":
        return
    hostname = parsed.hostname
    if hostname is None or not _is_loopback_hostname(hostname):
        raise ConfigError(
            f"{path} requires HTTPS; HTTP is permitted only for loopback development"
        )


def _is_loopback_hostname(value: str) -> bool:
    if value.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


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
            "capabilities",
            "organization_id",
            "clearance",
        )
        if any(getattr(existing, name) != getattr(identity, name) for name in fields):
            raise ConfigError(
                f"Identity metadata for actor {identity.actor_id} must match across authentication sources"
            )


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
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{path} must be a non-negative finite number")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ConfigError(f"{path} must be a non-negative finite number")
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


def _overlay_model_limits(
    parent: dict[str, ModelUsageLimit],
    child: dict[str, ModelUsageLimit],
) -> dict[str, ModelUsageLimit]:
    result = dict(parent)
    for alias, child_limit in child.items():
        parent_limit = result.get(alias)
        result[alias] = (
            child_limit
            if parent_limit is None
            else parent_limit.overlaid(child_limit)
        )
    return result


def _context_classification_overlay(
    parent: str | None,
    child: str | None,
) -> str | None:
    if parent is None:
        return child
    if child is None:
        return parent
    classifications = ("public", "internal", "confidential", "restricted")
    return classifications[min(classifications.index(parent), classifications.index(child))]


def _context_injection_mode_overlay(
    parent: str | None,
    child: str | None,
) -> str | None:
    if child is None:
        return parent
    if parent is None:
        return child
    # A narrower scope cannot enable injection after a higher scope disabled
    # it, or weaken a higher-scope required policy.
    if parent == "off":
        return "off"
    if parent == "required":
        return "required"
    return child
