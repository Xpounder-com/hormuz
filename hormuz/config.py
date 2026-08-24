from __future__ import annotations

import hashlib
import ipaddress
import json
from dataclasses import dataclass, field
from pathlib import Path

from ._config_input import (
    MAX_CONFIGURATION_BYTES,
    MAX_CONFIGURATION_DEPTH,
    MAX_CONFIGURATION_NODES,
)
from .custody_lifecycle import CustodyLifecycleConfig


class ConfigError(ValueError):
    pass


MAX_TRUSTED_PROXY_CIDRS = 64


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

    Key references are identifiers only. Backends resolve credentials only at
    runtime: AWS uses its ambient workload chain and OpenBao uses a named
    process-environment source. No credential value is allowed in Hormuz JSON.
    """

    backend: str
    region: str | None
    key_references: dict[str, str]
    endpoint_url: str | None = None
    token_env: str | None = None
    transit_mount: str | None = None

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
    endpoint_url: str | None = None
    access_key_env: str | None = None
    secret_key_env: str | None = None


@dataclass(frozen=True)
class AuditChainConfig:
    """Optional local readiness bound for externally anchored chain checkpoints."""

    maximum_anchor_age_seconds: int


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
class CustodyControlConfig:
    """Tenant-scoped custody authority and approval-store configuration.

    The control database credential authorizes only custody metadata. Customer
    KMS permissions belong to a separate machine executor and are deliberately
    absent from this configuration.
    """

    mode: str = "local"
    bootstrap_administrators: tuple[BootstrapAdministrator, ...] = ()
    postgres_control_dsn_env: str = "HORMUZ_CUSTODY_CONTROL_DSN"
    postgres_control_role: str = "hormuz_custody_control"
    authorization_ttl_seconds: int = 900


@dataclass(frozen=True)
class CustodyExecutorConfig:
    """Dedicated machine credential and recovery window for custody execution.

    This configuration is safe to distribute with the normal gateway config:
    it contains only the name of an executor-only secret source. The secret
    itself and the customer key-service credential belong only to the isolated
    executor process.
    """

    postgres_executor_dsn_env: str = "HORMUZ_CUSTODY_EXECUTOR_DSN"
    postgres_executor_role: str = "hormuz_custody_executor"
    pending_attempt_ttl_seconds: int = 900


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
    custody_control: CustodyControlConfig = field(default_factory=CustodyControlConfig)
    custody_executor: CustodyExecutorConfig = field(default_factory=CustodyExecutorConfig)
    custody_lifecycle: CustodyLifecycleConfig | None = None
    key_custody: KeyCustodyConfig | None = None
    audit_anchor: AuditAnchorConfig | None = None
    audit_chain: AuditChainConfig | None = None

    @classmethod
    def load(cls, path: str | Path, *, environ: dict[str, str] | None = None) -> "GatewayConfig":
        from ._config_builder import build_gateway_config

        return build_gateway_config(cls, path, environ=environ)

    def validate_references(self) -> None:
        if self.custody_lifecycle is not None:
            if self.custody_control.mode != "postgresql" or self.usage_storage.backend != "postgresql":
                raise ConfigError("custody_lifecycle requires managed PostgreSQL custody control")
            if len(self.organization_ids) != 1:
                raise ConfigError(
                    "custody_lifecycle requires exactly one configured organization; "
                    "use a tenant-scoped gateway configuration"
                )
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
