from __future__ import annotations

import json
import hashlib
import ipaddress
import math
import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .custody import KEY_PURPOSES, KEY_PURPOSE_DATA_ENCRYPTION, KEY_PURPOSE_PROVIDER_CREDENTIAL


class ConfigError(ValueError):
    pass


_ENVIRONMENT_NAME_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_POSTGRES_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_AWS_REGION_PATTERN = re.compile(r"[a-z]{2}(?:-gov)?-[a-z0-9-]+-\d+\Z")
_S3_BUCKET_PATTERN = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]\Z")


# Configuration is a deployment control input. Bound and validate its raw
# syntax before looking up any environment-backed identity or secret value.
MAX_CONFIGURATION_BYTES = 1 * 1024 * 1024
MAX_CONFIGURATION_DEPTH = 64
MAX_CONFIGURATION_NODES = 100_000
MAX_TRUSTED_PROXY_CIDRS = 64

_CONFIGURATION_UNAVAILABLE = "configuration_unavailable"
_CONFIGURATION_TOO_LARGE = "configuration_too_large"
_CONFIGURATION_INVALID_ENCODING = "configuration_invalid_encoding"
_CONFIGURATION_INVALID_JSON = "configuration_invalid_json"
_CONFIGURATION_DUPLICATE_MEMBER = "configuration_duplicate_member"
_CONFIGURATION_NONFINITE_NUMBER = "configuration_nonfinite_number"
_CONFIGURATION_STRUCTURE_LIMIT = "configuration_structure_limit"
_CONFIGURATION_SCHEMA_INVALID = "configuration_schema_invalid"
_CONFIGURATION_UNSUPPORTED_FIELDS = "configuration_unsupported_fields"


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
class IngressConfig:
    """The gateway-side boundary for customer-controlled TLS termination.

    ``local`` is intentionally the simple development default.  The external
    proxy mode never makes Hormuz a TLS terminator: it only accepts an already
    network-restricted and authenticated proxy hop.
    """

    mode: str = "local"
    trusted_proxy_cidrs: tuple[str, ...] = ()
    trusted_proxy_networks: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = field(
        default=(),
        repr=False,
    )
    credential_env: str | None = None
    credential: str = field(default="", repr=False)


@dataclass(frozen=True)
class UpstreamConfig:
    base_url: str
    api_key_env: str | None = None
    api_key_envelope_path: Path | None = None
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
    postgres_pool: "PostgresPoolConfig" = field(default_factory=lambda: PostgresPoolConfig())


@dataclass(frozen=True)
class PostgresPoolConfig:
    """Bounded runtime-pool settings for the optional PostgreSQL adapter.

    These settings apply only to long-running gateway runtime connections. The
    migration credential intentionally remains a one-shot operator connection,
    and the policy-control credential remains a distinct service boundary.
    """

    min_connections: int = 1
    max_connections: int = 8
    acquire_timeout_seconds: int = 5
    max_waiting: int = 16
    max_lifetime_seconds: int = 3600
    max_idle_seconds: int = 300


@dataclass(frozen=True)
class KeyCustodyConfig:
    """A configured external envelope-key service with purpose-separated keys.

    Key references are identifiers only.  AWS workload credentials come from
    the ambient SDK chain and must never be written into Hormuz JSON.
    """

    backend: str
    region: str
    key_references: dict[str, str]

    def key_reference_for(self, purpose: str) -> str:
        try:
            return self.key_references[purpose]
        except KeyError:
            raise ConfigError(f"key_custody.key_references lacks required purpose: {purpose}") from None


@dataclass(frozen=True)
class AuditAnchorConfig:
    """An explicit external immutable-retention target for audit snapshots."""

    backend: str
    region: str
    bucket: str
    prefix: str
    retention_days: int
    legal_hold: bool


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
    ingress: IngressConfig = field(default_factory=IngressConfig)
    oidc_issuers: dict[str, OIDCIssuerConfig] = field(default_factory=dict)
    identities_by_subject: dict[tuple[str, str], Identity] = field(default_factory=dict)
    secret_controls: SecretControls = field(default_factory=SecretControls)
    team_policies: dict[str, Policy] = field(default_factory=dict)
    actor_policies: dict[str, Policy] = field(default_factory=dict)
    max_request_bytes: int = 25 * 1024 * 1024
    upstream_timeout_seconds: int = 600
    usage_storage: UsageStorageConfig = field(default_factory=UsageStorageConfig)
    policy_control: PolicyControlConfig = field(default_factory=PolicyControlConfig)
    key_custody: KeyCustodyConfig | None = None
    audit_anchor: AuditAnchorConfig | None = None

    @classmethod
    def load(cls, path: str | Path, *, environ: dict[str, str] | None = None) -> "GatewayConfig":
        source_path = Path(path).expanduser().resolve()
        raw = _load_configuration_json(source_path)
        _reject_deprecated_context_configuration(raw)
        _validate_configuration_schema(raw)

        listen_raw = _object(raw.get("listen", {}), "listen")
        host = _string(listen_raw.get("host", "127.0.0.1"), "listen.host")
        port = _integer(listen_raw.get("port", 8787), "listen.port", minimum=1, maximum=65535)
        ingress = _ingress_config(raw.get("ingress", {}), listen_host=host)

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
                "postgres_pool",
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
        postgres_pool = _postgres_pool_config(usage_storage_raw.get("postgres_pool", {}))
        if usage_backend == "postgresql" and postgres_dsn_env == postgres_migration_dsn_env:
            raise ConfigError(
                "usage_storage.postgres_dsn_env and usage_storage.postgres_migration_dsn_env "
                "must name separate credentials"
            )
        if usage_backend != "postgresql" and "postgres_pool" in usage_storage_raw:
            raise ConfigError("usage_storage.postgres_pool requires usage_storage.backend postgresql")

        key_custody = _key_custody(raw.get("key_custody"))
        audit_anchor = _audit_anchor(raw.get("audit_anchor"), key_custody=key_custody)

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
            unsupported_upstream_fields = set(item).difference(
                {
                    "base_url",
                    "api_key_env",
                    "api_key_envelope",
                    "allow_response_storage",
                    "allow_background",
                }
            )
            if unsupported_upstream_fields:
                raise ConfigError(
                    f"upstreams.{protocol} contains unsupported fields: "
                    + ", ".join(sorted(str(field) for field in unsupported_upstream_fields))
                )
            base_url = _string(item.get("base_url"), f"upstreams.{protocol}.base_url").rstrip("/")
            parsed = urlparse(base_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ConfigError(f"upstreams.{protocol}.base_url must be an HTTP(S) URL")
            api_key_env_value = item.get("api_key_env")
            api_key_envelope_value = item.get("api_key_envelope")
            if (api_key_env_value is None) == (api_key_envelope_value is None):
                raise ConfigError(
                    f"upstreams.{protocol} must configure exactly one of api_key_env or api_key_envelope"
                )
            api_key_env: str | None = None
            api_key_envelope_path: Path | None = None
            if api_key_env_value is not None:
                api_key_env = _environment_name(api_key_env_value, f"upstreams.{protocol}.api_key_env")
            else:
                if key_custody is None:
                    raise ConfigError(f"upstreams.{protocol}.api_key_envelope requires key_custody")
                key_custody.key_reference_for(KEY_PURPOSE_PROVIDER_CREDENTIAL)
                api_key_envelope_path = _configured_path(
                    api_key_envelope_value,
                    f"upstreams.{protocol}.api_key_envelope",
                    source_path.parent,
                )
            upstreams[protocol] = UpstreamConfig(
                base_url=base_url,
                api_key_env=api_key_env,
                api_key_envelope_path=api_key_envelope_path,
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
        static_identities: list[Identity] = []
        for index, value in enumerate(identities_raw):
            item = _object(value, f"identities[{index}]")
            prefix = f"identities[{index}]"
            token_env = _environment_name(item.get("token_env"), f"{prefix}.token_env")
            identity = Identity(
                token_env=token_env,
                token="",
                actor_id=_string(item.get("actor_id"), f"{prefix}.actor_id"),
                actor_name=_string(item.get("actor_name"), f"{prefix}.actor_name"),
                team_id=_string(item.get("team_id"), f"{prefix}.team_id"),
                team_name=_string(item.get("team_name"), f"{prefix}.team_name"),
                allowed_clients=_string_tuple(item.get("allowed_clients", []), f"{prefix}.allowed_clients"),
                organization_id=_string(item.get("organization_id", "organization"), f"{prefix}.organization_id"),
                clearance=_classification(item.get("clearance", "internal"), f"{prefix}.clearance"),
                identity_type=_identity_type(item.get("identity_type", "human"), f"{prefix}.identity_type"),
            )
            static_identities.append(identity)

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
        if not static_identities and not identities_by_subject:
            raise ConfigError("At least one static identity or OIDC subject mapping is required")
        _validate_identity_consistency((*static_identities, *identities_by_subject.values()))
        bootstrap_administrators = _bootstrap_administrators(
            bootstrap_administrators_raw,
            static_identities=tuple(static_identities),
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
        secret_controls = _secret_controls(egress_raw.get("secrets", {}))
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
            ingress=ingress,
            database_path=database_path,
            upstreams=upstreams,
            identities_by_token={
                f"pending-static-{index}": identity for index, identity in enumerate(static_identities)
            },
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
                postgres_pool=postgres_pool,
            ),
            policy_control=PolicyControlConfig(
                mode=policy_control_mode,
                bootstrap_administrators=bootstrap_administrators,
                postgres_control_dsn_env=policy_control_dsn_env,
                postgres_control_role=policy_control_role,
                break_glass=break_glass,
            ),
            key_custody=key_custody,
            audit_anchor=audit_anchor,
        )
        config.validate_references()
        _validate_dedicated_ingress_credential_env(config)
        env = os.environ if environ is None else environ
        identities_by_token = _resolve_static_identity_tokens(tuple(static_identities), env)
        resolved_ingress = _resolve_ingress_credential(config.ingress, env)
        if resolved_ingress.credential and resolved_ingress.credential in identities_by_token:
            raise ConfigError("ingress credential must not equal a static identity token")
        return replace(
            config,
            ingress=resolved_ingress,
            identities_by_token=identities_by_token,
            secret_controls=_resolve_secret_controls(config.secret_controls, env),
        )

    def validate_references(self) -> None:
        if any(upstream.api_key_envelope_path is not None for upstream in self.upstreams.values()):
            if len(self.organization_ids) != 1:
                raise ConfigError(
                    "encrypted upstream credentials require exactly one configured organization; "
                    "use a tenant-scoped gateway configuration"
                )
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


_ROOT_CONFIGURATION_FIELDS = frozenset(
    {
        "listen",
        "ingress",
        "database",
        "upstreams",
        "identities",
        "authentication",
        "model_routes",
        "egress_controls",
        "policies",
        "max_request_bytes",
        "upstream_timeout_seconds",
        "usage_storage",
        "policy_control",
        "key_custody",
        "audit_anchor",
    }
)
_LISTEN_FIELDS = frozenset({"host", "port"})
_INGRESS_FIELDS = frozenset({"mode", "trusted_proxy_cidrs", "credential_env"})
_UPSTREAM_FIELDS = frozenset(
    {"base_url", "api_key_env", "api_key_envelope", "allow_response_storage", "allow_background"}
)
_IDENTITY_FIELDS = frozenset(
    {
        "token_env",
        "actor_id",
        "actor_name",
        "team_id",
        "team_name",
        "allowed_clients",
        "organization_id",
        "clearance",
        "identity_type",
    }
)
_OIDC_ISSUER_FIELDS = frozenset(
    {
        "issuer",
        "audiences",
        "jwks_uri",
        "algorithms",
        "clock_skew_seconds",
        "discovery_cache_seconds",
        "allow_insecure_http",
        "subjects",
    }
)
_OIDC_SUBJECT_FIELDS = _IDENTITY_FIELDS.difference({"token_env"}).union({"subject"})
_MODEL_ROUTE_FIELDS = frozenset(
    {
        "protocol",
        "upstream_model",
        "input_cost_per_million",
        "cache_read_cost_per_million",
        "cache_write_cost_per_million",
        "output_cost_per_million",
    }
)
_EGRESS_CONTROL_FIELDS = frozenset({"secrets"})
_SECRET_CONTROL_FIELDS = frozenset({"mode", "builtins", "custom_secret_envs"})
_POLICIES_FIELDS = frozenset({"organization", "teams", "actors"})
_POLICY_FIELDS = frozenset(
    {
        "allowed_clients",
        "allowed_models",
        "fallback_model",
        "fallback_models",
        "max_output_tokens",
        "monthly_token_limit",
        "monthly_budget_usd",
        "per_actor_monthly_budget_usd",
    }
)
_FALLBACK_MODEL_FIELDS = frozenset({"openai", "anthropic"})
_USAGE_STORAGE_FIELDS = frozenset(
    {
        "backend",
        "postgres_dsn_env",
        "postgres_migration_dsn_env",
        "postgres_schema",
        "postgres_runtime_role",
        "postgres_pool",
    }
)
_POSTGRES_POOL_FIELDS = frozenset(
    {
        "min_connections",
        "max_connections",
        "acquire_timeout_seconds",
        "max_waiting",
        "max_lifetime_seconds",
        "max_idle_seconds",
    }
)
_POLICY_CONTROL_FIELDS = frozenset(
    {
        "mode",
        "bootstrap_administrators",
        "postgres_control_dsn_env",
        "postgres_control_role",
        "break_glass",
    }
)
_BREAK_GLASS_FIELDS = frozenset({"enabled", "token_env"})
_BOOTSTRAP_ADMINISTRATOR_FIELDS = frozenset({"organization_id", "actor_id", "issuer", "subject"})
_KEY_CUSTODY_FIELDS = frozenset({"backend", "region", "key_references"})
_AUDIT_ANCHOR_FIELDS = frozenset({"backend", "region", "bucket", "prefix", "retention_days", "legal_hold"})


class _ConfigurationInputError(ValueError):
    """Internal JSON decoder failure with a fixed, content-free code."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _load_configuration_json(source_path: Path) -> dict[str, Any]:
    """Read one bounded, unambiguous JSON object before any secret lookup."""

    try:
        with source_path.open("rb") as source:
            encoded = source.read(MAX_CONFIGURATION_BYTES + 1)
    except OSError:
        raise ConfigError(_CONFIGURATION_UNAVAILABLE) from None
    if len(encoded) > MAX_CONFIGURATION_BYTES:
        raise ConfigError(_CONFIGURATION_TOO_LARGE)
    try:
        decoded = encoded.decode("utf-8")
    except UnicodeDecodeError:
        raise ConfigError(_CONFIGURATION_INVALID_ENCODING) from None
    try:
        raw = json.loads(
            decoded,
            object_pairs_hook=_reject_duplicate_json_members,
            parse_constant=_reject_nonfinite_json_number,
        )
    except _ConfigurationInputError as error:
        raise ConfigError(error.code) from None
    except (json.JSONDecodeError, RecursionError, ValueError):
        raise ConfigError(_CONFIGURATION_INVALID_JSON) from None
    if not isinstance(raw, dict):
        raise ConfigError(_CONFIGURATION_SCHEMA_INVALID)
    _validate_configuration_structure(raw)
    return raw


def _reject_duplicate_json_members(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _ConfigurationInputError(_CONFIGURATION_DUPLICATE_MEMBER)
        result[key] = value
    return result


def _reject_nonfinite_json_number(_value: str) -> None:
    raise _ConfigurationInputError(_CONFIGURATION_NONFINITE_NUMBER)


def _validate_configuration_structure(raw: object) -> None:
    """Reject deeply nested or enormous JSON without recursive traversal."""

    pending: list[tuple[object, int]] = [(raw, 1)]
    nodes = 0
    while pending:
        value, depth = pending.pop()
        nodes += 1
        if nodes > MAX_CONFIGURATION_NODES or depth > MAX_CONFIGURATION_DEPTH:
            raise ConfigError(_CONFIGURATION_STRUCTURE_LIMIT)
        if isinstance(value, float) and not math.isfinite(value):
            raise ConfigError(_CONFIGURATION_NONFINITE_NUMBER)
        if isinstance(value, dict):
            pending.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            pending.extend((item, depth + 1) for item in value)


def _validate_configuration_schema(raw: dict[str, Any]) -> None:
    """Reject unknown fields across the raw schema before environment lookup."""

    _schema_object(raw, _ROOT_CONFIGURATION_FIELDS)

    _schema_optional_object(raw, "listen", _LISTEN_FIELDS)
    _schema_optional_object(raw, "ingress", _INGRESS_FIELDS)

    upstreams = _schema_required_object(raw, "upstreams", frozenset({"openai", "anthropic"}))
    for value in upstreams.values():
        _schema_object(value, _UPSTREAM_FIELDS)

    identities = _schema_optional_array(raw, "identities")
    for value in identities:
        _schema_object(value, _IDENTITY_FIELDS)

    authentication = _schema_optional_object(raw, "authentication", frozenset({"oidc"}))
    if authentication is not None:
        oidc = _schema_optional_object(authentication, "oidc", frozenset({"issuers"}))
        if oidc is not None:
            issuers = _schema_optional_array(oidc, "issuers")
            for issuer in issuers:
                issuer_object = _schema_object(issuer, _OIDC_ISSUER_FIELDS)
                subjects = _schema_optional_array(issuer_object, "subjects")
                for subject in subjects:
                    _schema_object(subject, _OIDC_SUBJECT_FIELDS)

    model_routes = _schema_required_mapping(raw, "model_routes")
    for value in model_routes.values():
        _schema_object(value, _MODEL_ROUTE_FIELDS)

    egress = _schema_optional_object(raw, "egress_controls", _EGRESS_CONTROL_FIELDS)
    if egress is not None:
        _schema_optional_object(egress, "secrets", _SECRET_CONTROL_FIELDS)

    if "policies" in raw:
        policies = _schema_object(raw["policies"], _POLICIES_FIELDS)
        if "organization" in policies:
            _validate_policy_schema(policies["organization"])
        for scope in ("teams", "actors"):
            if scope not in policies:
                continue
            for policy in _schema_mapping(policies[scope]).values():
                _validate_policy_schema(policy)

    usage_storage = _schema_optional_object(raw, "usage_storage", _USAGE_STORAGE_FIELDS)
    if usage_storage is not None:
        _schema_optional_object(usage_storage, "postgres_pool", _POSTGRES_POOL_FIELDS)

    policy_control = _schema_optional_object(raw, "policy_control", _POLICY_CONTROL_FIELDS)
    if policy_control is not None:
        _schema_optional_object(policy_control, "break_glass", _BREAK_GLASS_FIELDS)
        for administrator in _schema_optional_array(policy_control, "bootstrap_administrators"):
            _schema_object(administrator, _BOOTSTRAP_ADMINISTRATOR_FIELDS)

    if "key_custody" in raw and raw["key_custody"] is not None:
        key_custody = _schema_object(raw["key_custody"], _KEY_CUSTODY_FIELDS)
        if "key_references" in key_custody:
            _schema_object(key_custody["key_references"], frozenset(KEY_PURPOSES))

    if "audit_anchor" in raw and raw["audit_anchor"] is not None:
        _schema_object(raw["audit_anchor"], _AUDIT_ANCHOR_FIELDS)


def _validate_policy_schema(value: object) -> None:
    policy = _schema_object(value, _POLICY_FIELDS)
    if "fallback_models" in policy and policy["fallback_models"] is not None:
        _schema_object(policy["fallback_models"], _FALLBACK_MODEL_FIELDS)


def _schema_required_object(
    parent: dict[str, Any],
    key: str,
    allowed_fields: frozenset[str],
) -> dict[str, Any]:
    return _schema_object(parent.get(key), allowed_fields)


def _schema_optional_object(
    parent: dict[str, Any],
    key: str,
    allowed_fields: frozenset[str],
) -> dict[str, Any] | None:
    if key not in parent:
        return None
    return _schema_object(parent[key], allowed_fields)


def _schema_required_mapping(parent: dict[str, Any], key: str) -> dict[str, Any]:
    return _schema_mapping(parent.get(key))


def _schema_optional_array(parent: dict[str, Any], key: str) -> list[Any]:
    if key not in parent:
        return []
    value = parent[key]
    if not isinstance(value, list):
        raise ConfigError(_CONFIGURATION_SCHEMA_INVALID)
    return value


def _schema_mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(_CONFIGURATION_SCHEMA_INVALID)
    return value


def _schema_object(value: object, allowed_fields: frozenset[str]) -> dict[str, Any]:
    result = _schema_mapping(value)
    if set(result).difference(allowed_fields):
        raise ConfigError(_CONFIGURATION_UNSUPPORTED_FIELDS)
    return result


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


def _ingress_config(value: Any, *, listen_host: str) -> IngressConfig:
    """Parse the private proxy hop without resolving its credential yet."""

    item = _object(value, "ingress")
    mode = _string(item.get("mode", "local"), "ingress.mode")
    if mode not in {"local", "external_tls_proxy"}:
        raise ConfigError("ingress.mode must be local or external_tls_proxy")

    if mode == "local":
        if "trusted_proxy_cidrs" in item or "credential_env" in item:
            raise ConfigError("ingress.trusted_proxy_cidrs and ingress.credential_env require external_tls_proxy mode")
        if not _is_loopback_listener(listen_host):
            raise ConfigError("a non-loopback listen.host requires ingress.mode external_tls_proxy")
        return IngressConfig()

    trusted_proxy_cidrs = _string_tuple(
        item.get("trusted_proxy_cidrs", []),
        "ingress.trusted_proxy_cidrs",
    )
    if not trusted_proxy_cidrs:
        raise ConfigError("ingress.trusted_proxy_cidrs must contain at least one network")
    if len(trusted_proxy_cidrs) > MAX_TRUSTED_PROXY_CIDRS:
        raise ConfigError(f"ingress.trusted_proxy_cidrs must contain at most {MAX_TRUSTED_PROXY_CIDRS} networks")

    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for index, cidr in enumerate(trusted_proxy_cidrs):
        try:
            networks.append(ipaddress.ip_network(cidr, strict=True))
        except ValueError:
            raise ConfigError(f"ingress.trusted_proxy_cidrs[{index}] must be a canonical CIDR") from None
    if len(networks) != len(set(networks)):
        raise ConfigError("ingress.trusted_proxy_cidrs cannot contain duplicate networks")
    ipv4_networks = [network for network in networks if network.version == 4]
    ipv6_networks = [network for network in networks if network.version == 6]
    collapsed_networks = (
        *tuple(ipaddress.collapse_addresses(ipv4_networks)),
        *tuple(ipaddress.collapse_addresses(ipv6_networks)),
    )
    if any(network.prefixlen == 0 for network in collapsed_networks):
        raise ConfigError("ingress.trusted_proxy_cidrs must not admit every address")

    return IngressConfig(
        mode=mode,
        trusted_proxy_cidrs=trusted_proxy_cidrs,
        trusted_proxy_networks=collapsed_networks,
        credential_env=_environment_name(item.get("credential_env"), "ingress.credential_env"),
    )


def _is_loopback_listener(host: str) -> bool:
    normalized = host.lower().rstrip(".")
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _resolve_ingress_credential(ingress: IngressConfig, env: dict[str, str]) -> IngressConfig:
    """Resolve the proxy credential only after every semantic check succeeds."""

    if ingress.mode == "local":
        return ingress
    assert ingress.credential_env is not None
    credential = env.get(ingress.credential_env, "")
    if not credential:
        raise ConfigError(f"Required ingress credential environment variable is not set: {ingress.credential_env}")
    if len(credential) < 16:
        raise ConfigError(f"Ingress credential from {ingress.credential_env} must be at least 16 characters")
    return replace(ingress, credential=credential)


def _validate_dedicated_ingress_credential_env(config: GatewayConfig) -> None:
    """Keep the proxy-hop secret separate from every other configured secret.

    The ingress credential passes through customer-controlled infrastructure,
    so reusing a provider key, employee token, database DSN, break-glass
    credential, or redaction secret would accidentally widen access to that
    other secret.  This is a configuration-time invariant; it does not inspect
    or expose any environment values.
    """

    ingress = config.ingress
    if ingress.mode == "local":
        return
    assert ingress.credential_env is not None

    credential_envs = {
        identity.token_env
        for identity in config.identities_by_token.values()
        if identity.token_env
    }
    credential_envs.update(
        upstream.api_key_env
        for upstream in config.upstreams.values()
        if upstream.api_key_env is not None
    )
    credential_envs.update(config.secret_controls.custom_secret_envs)
    if config.usage_storage.backend == "postgresql":
        credential_envs.update(
            {
                config.usage_storage.postgres_dsn_env,
                config.usage_storage.postgres_migration_dsn_env,
            }
        )
    if config.policy_control.mode == "postgresql":
        credential_envs.add(config.policy_control.postgres_control_dsn_env)
        if config.policy_control.break_glass.enabled:
            credential_envs.add(config.policy_control.break_glass.token_env)

    if ingress.credential_env in credential_envs:
        raise ConfigError("ingress.credential_env must name a credential distinct from all other Hormuz secrets")


def _secret_controls(value: Any) -> SecretControls:
    """Parse the non-secret custom-detector configuration only."""

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


def _resolve_secret_controls(controls: SecretControls, env: dict[str, str]) -> SecretControls:
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


def _resolve_static_identity_tokens(
    identities: tuple[Identity, ...],
    env: dict[str, str],
) -> dict[str, Identity]:
    """Resolve static tokens after every non-secret config invariant is valid."""

    resolved: dict[str, Identity] = {}
    for identity in identities:
        token = env.get(identity.token_env, "")
        if not token:
            raise ConfigError(f"Required identity token environment variable is not set: {identity.token_env}")
        if len(token) < 16:
            raise ConfigError(f"Identity token from {identity.token_env} must be at least 16 characters")
        if token in resolved:
            raise ConfigError(f"Identity tokens must be unique; duplicate value from {identity.token_env}")
        resolved[token] = replace(identity, token=token)
    return resolved


def _key_custody(value: Any) -> KeyCustodyConfig | None:
    """Parse an opt-in external key-custody profile without credentials."""

    if value is None:
        return None
    item = _object(value, "key_custody")
    unsupported = set(item).difference({"backend", "region", "key_references"})
    if unsupported:
        raise ConfigError("key_custody contains unsupported fields: " + ", ".join(sorted(unsupported)))
    backend = _string(item.get("backend"), "key_custody.backend")
    if backend != "aws-kms":
        raise ConfigError("key_custody.backend must be aws-kms")
    region = _aws_region(item.get("region"), "key_custody.region")
    raw_references = _object(item.get("key_references"), "key_custody.key_references")
    if not raw_references:
        raise ConfigError("key_custody.key_references must contain at least one purpose")
    key_references: dict[str, str] = {}
    for purpose, raw_reference in raw_references.items():
        if not isinstance(purpose, str) or purpose not in KEY_PURPOSES:
            raise ConfigError(
                "key_custody.key_references keys must be one of: " + ", ".join(sorted(KEY_PURPOSES))
            )
        reference = _string(raw_reference, f"key_custody.key_references.{purpose}")
        if len(reference) > 2048 or any(character in reference for character in "\x00\r\n"):
            raise ConfigError(f"key_custody.key_references.{purpose} must be a safe KMS key reference")
        key_references[purpose] = reference
    if len(key_references) != len(set(key_references.values())):
        raise ConfigError("key_custody.key_references must use distinct keys for distinct purposes")
    return KeyCustodyConfig(backend=backend, region=region, key_references=key_references)


def _audit_anchor(value: Any, *, key_custody: KeyCustodyConfig | None) -> AuditAnchorConfig | None:
    """Parse an explicit S3 Object Lock target with no accidental default."""

    if value is None:
        return None
    item = _object(value, "audit_anchor")
    unsupported = set(item).difference({"backend", "region", "bucket", "prefix", "retention_days", "legal_hold"})
    if unsupported:
        raise ConfigError("audit_anchor contains unsupported fields: " + ", ".join(sorted(unsupported)))
    backend = _string(item.get("backend"), "audit_anchor.backend")
    if backend != "aws-s3-object-lock":
        raise ConfigError("audit_anchor.backend must be aws-s3-object-lock")
    if key_custody is None:
        raise ConfigError("audit_anchor requires key_custody for SSE-KMS encryption")
    key_custody.key_reference_for(KEY_PURPOSE_DATA_ENCRYPTION)
    region = _aws_region(item.get("region"), "audit_anchor.region")
    if region != key_custody.region:
        raise ConfigError("audit_anchor.region must equal key_custody.region for SSE-KMS")
    bucket = _string(item.get("bucket"), "audit_anchor.bucket")
    if _S3_BUCKET_PATTERN.fullmatch(bucket) is None or ".." in bucket or ".-" in bucket or "-." in bucket:
        raise ConfigError("audit_anchor.bucket must be a valid lower-case S3 bucket name")
    prefix = _string(item.get("prefix", "hormuz/audit"), "audit_anchor.prefix").strip("/")
    if (
        not prefix
        or len(prefix) > 512
        or any(character in prefix for character in "\x00\r\n")
        or any(part in {"", ".", ".."} for part in prefix.split("/"))
    ):
        raise ConfigError("audit_anchor.prefix must be a safe non-empty object-key prefix")
    retention_days = _integer(item.get("retention_days"), "audit_anchor.retention_days", minimum=1, maximum=36500)
    legal_hold = _boolean(item.get("legal_hold", False), "audit_anchor.legal_hold")
    return AuditAnchorConfig(
        backend=backend,
        region=region,
        bucket=bucket,
        prefix=prefix,
        retention_days=retention_days,
        legal_hold=legal_hold,
    )


def _bootstrap_administrators(
    value: list[Any],
    *,
    static_identities: tuple[Identity, ...],
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


def _aws_region(value: Any, path: str) -> str:
    result = _string(value, path)
    if _AWS_REGION_PATTERN.fullmatch(result) is None:
        raise ConfigError(f"{path} must be a valid AWS region identifier")
    return result


def _configured_path(value: Any, path: str, base_directory: Path) -> Path:
    result = _string(value, path)
    if any(character in result for character in "\x00\r\n"):
        raise ConfigError(f"{path} must be a safe file path")
    configured = Path(result).expanduser()
    return configured.resolve() if configured.is_absolute() else (base_directory / configured).resolve()


def _postgres_identifier(value: Any, path: str) -> str:
    result = _string(value, path)
    if _POSTGRES_IDENTIFIER_PATTERN.fullmatch(result) is None:
        raise ConfigError(f"{path} must be a safe PostgreSQL identifier")
    return result


def _postgres_pool_config(value: Any) -> PostgresPoolConfig:
    raw = _object(value, "usage_storage.postgres_pool")
    allowed = {
        "min_connections",
        "max_connections",
        "acquire_timeout_seconds",
        "max_waiting",
        "max_lifetime_seconds",
        "max_idle_seconds",
    }
    unsupported = set(raw).difference(allowed)
    if unsupported:
        raise ConfigError(
            "usage_storage.postgres_pool contains unsupported fields: "
            + ", ".join(sorted(str(field) for field in unsupported))
        )
    min_connections = _integer(
        raw.get("min_connections", 1),
        "usage_storage.postgres_pool.min_connections",
        minimum=1,
        maximum=100,
    )
    max_connections = _integer(
        raw.get("max_connections", 8),
        "usage_storage.postgres_pool.max_connections",
        minimum=1,
        maximum=1000,
    )
    if min_connections > max_connections:
        raise ConfigError(
            "usage_storage.postgres_pool.min_connections must not exceed max_connections"
        )
    acquire_timeout_seconds = _integer(
        raw.get("acquire_timeout_seconds", 5),
        "usage_storage.postgres_pool.acquire_timeout_seconds",
        minimum=1,
        maximum=120,
    )
    max_waiting = _integer(
        raw.get("max_waiting", 16),
        "usage_storage.postgres_pool.max_waiting",
        minimum=1,
        maximum=10000,
    )
    max_lifetime_seconds = _integer(
        raw.get("max_lifetime_seconds", 3600),
        "usage_storage.postgres_pool.max_lifetime_seconds",
        minimum=60,
        maximum=7 * 24 * 60 * 60,
    )
    max_idle_seconds = _integer(
        raw.get("max_idle_seconds", 300),
        "usage_storage.postgres_pool.max_idle_seconds",
        minimum=1,
        maximum=max_lifetime_seconds,
    )
    return PostgresPoolConfig(
        min_connections=min_connections,
        max_connections=max_connections,
        acquire_timeout_seconds=acquire_timeout_seconds,
        max_waiting=max_waiting,
        max_lifetime_seconds=max_lifetime_seconds,
        max_idle_seconds=max_idle_seconds,
    )


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
