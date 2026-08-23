"""Typed construction for already validated Hormuz configuration input."""

from __future__ import annotations

import ipaddress
import math
import os
import re
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ._config_input import ConfigurationInputError, load_configuration_input
from .config import (
    AuditAnchorConfig,
    AuditChainConfig,
    BootstrapAdministrator,
    BreakGlassConfig,
    ConfigError,
    GatewayConfig,
    Identity,
    IngressConfig,
    KeyCustodyConfig,
    ListenConfig,
    MAX_TRUSTED_PROXY_CIDRS,
    ModelRoute,
    OIDCIssuerConfig,
    Policy,
    PolicyControlConfig,
    PostgresPoolConfig,
    SecretControls,
    UpstreamConfig,
    UsageStorageConfig,
)
from .custody import KEY_PURPOSES, KEY_PURPOSE_DATA_ENCRYPTION, KEY_PURPOSE_PROVIDER_CREDENTIAL


_ENVIRONMENT_NAME_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_POSTGRES_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_AWS_REGION_PATTERN = re.compile(r"[a-z]{2}(?:-gov)?-[a-z0-9-]+-\d+\Z")
_S3_BUCKET_PATTERN = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]\Z")
_OPENBAO_PATH_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
def build_gateway_config(
    config_type: type[GatewayConfig],
    path: str | Path,
    *,
    environ: dict[str, str] | None = None,
) -> GatewayConfig:
    """Construct typed runtime configuration after strict input validation."""

    cls = config_type
    source_path = Path(path).expanduser().resolve()
    try:
        raw = load_configuration_input(source_path)
    except ConfigurationInputError as error:
        raise ConfigError(error.code) from None

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
    audit_chain = _audit_chain(raw.get("audit_chain"), audit_anchor=audit_anchor)

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
        audit_chain=audit_chain,
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
    backend = _string(item.get("backend"), "key_custody.backend")
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
    if backend == "aws-kms":
        unsupported = set(item).difference({"backend", "region", "key_references"})
        if unsupported:
            raise ConfigError("key_custody contains unsupported fields: " + ", ".join(sorted(unsupported)))
        return KeyCustodyConfig(
            backend=backend,
            region=_aws_region(item.get("region"), "key_custody.region"),
            key_references=key_references,
        )
    if backend == "openbao-transit":
        unsupported = set(item).difference({"backend", "endpoint_url", "token_env", "transit_mount", "key_references"})
        if unsupported:
            raise ConfigError("key_custody contains unsupported fields: " + ", ".join(sorted(unsupported)))
        for purpose, reference in key_references.items():
            if _OPENBAO_PATH_NAME_PATTERN.fullmatch(reference) is None:
                raise ConfigError(
                    f"key_custody.key_references.{purpose} must be a safe OpenBao Transit key name"
                )
        return KeyCustodyConfig(
            backend=backend,
            region=None,
            key_references=key_references,
            endpoint_url=_self_hosted_service_url(item.get("endpoint_url"), "key_custody.endpoint_url"),
            token_env=_environment_name(item.get("token_env"), "key_custody.token_env"),
            transit_mount=_openbao_path_name(item.get("transit_mount", "transit"), "key_custody.transit_mount"),
        )
    raise ConfigError("key_custody.backend must be aws-kms or openbao-transit")


def _audit_anchor(value: Any, *, key_custody: KeyCustodyConfig | None) -> AuditAnchorConfig | None:
    """Parse an explicit S3 Object Lock target with no accidental default."""

    if value is None:
        return None
    item = _object(value, "audit_anchor")
    backend = _string(item.get("backend"), "audit_anchor.backend")
    if key_custody is None:
        raise ConfigError("audit_anchor requires key_custody")
    key_custody.key_reference_for(KEY_PURPOSE_DATA_ENCRYPTION)
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
    if backend == "aws-s3-object-lock":
        unsupported = set(item).difference({"backend", "region", "bucket", "prefix", "retention_days", "legal_hold"})
        if unsupported:
            raise ConfigError("audit_anchor contains unsupported fields: " + ", ".join(sorted(unsupported)))
        if key_custody.backend != "aws-kms" or key_custody.region is None:
            raise ConfigError("audit_anchor.backend aws-s3-object-lock requires key_custody.backend aws-kms")
        region = _aws_region(item.get("region"), "audit_anchor.region")
        if region != key_custody.region:
            raise ConfigError("audit_anchor.region must equal key_custody.region for SSE-KMS")
        return AuditAnchorConfig(
            backend=backend,
            region=region,
            bucket=bucket,
            prefix=prefix,
            retention_days=retention_days,
            legal_hold=legal_hold,
        )
    if backend == "s3-compatible-object-lock":
        unsupported = set(item).difference(
            {
                "backend",
                "region",
                "bucket",
                "prefix",
                "retention_days",
                "legal_hold",
                "endpoint_url",
                "access_key_env",
                "secret_key_env",
            }
        )
        if unsupported:
            raise ConfigError("audit_anchor contains unsupported fields: " + ", ".join(sorted(unsupported)))
        if key_custody.backend != "openbao-transit":
            raise ConfigError("audit_anchor.backend s3-compatible-object-lock requires key_custody.backend openbao-transit")
        access_key_env = _environment_name(item.get("access_key_env"), "audit_anchor.access_key_env")
        secret_key_env = _environment_name(item.get("secret_key_env"), "audit_anchor.secret_key_env")
        if access_key_env == secret_key_env:
            raise ConfigError("audit_anchor access_key_env and secret_key_env must differ")
        return AuditAnchorConfig(
            backend=backend,
            region=_s3_compatible_region(item.get("region"), "audit_anchor.region"),
            bucket=bucket,
            prefix=prefix,
            retention_days=retention_days,
            legal_hold=legal_hold,
            endpoint_url=_self_hosted_service_url(item.get("endpoint_url"), "audit_anchor.endpoint_url"),
            access_key_env=access_key_env,
            secret_key_env=secret_key_env,
        )
    raise ConfigError("audit_anchor.backend must be aws-s3-object-lock or s3-compatible-object-lock")


def _audit_chain(value: Any, *, audit_anchor: AuditAnchorConfig | None) -> AuditChainConfig | None:
    """Parse opt-in readiness monitoring for asynchronous external checkpoints."""

    if value is None:
        return None
    item = _object(value, "audit_chain")
    unsupported = set(item).difference({"maximum_anchor_age_seconds"})
    if unsupported:
        raise ConfigError("audit_chain contains unsupported fields: " + ", ".join(sorted(unsupported)))
    if audit_anchor is None:
        raise ConfigError("audit_chain requires audit_anchor")
    return AuditChainConfig(
        maximum_anchor_age_seconds=_integer(
            item.get("maximum_anchor_age_seconds"),
            "audit_chain.maximum_anchor_age_seconds",
            minimum=60,
            maximum=31 * 24 * 60 * 60,
        )
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


def _self_hosted_service_url(value: Any, path: str) -> str:
    """Accept a private service origin and allow HTTP only for loopback dev."""

    result = _url(value, path)
    parsed = urlparse(result)
    if parsed.path not in {"", "/"}:
        raise ConfigError(f"{path} must be a service origin without a path")
    if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "::1", "localhost"}:
        raise ConfigError(f"{path} requires HTTPS outside loopback development")
    return result.rstrip("/")


def _openbao_path_name(value: Any, path: str) -> str:
    result = _string(value, path)
    if _OPENBAO_PATH_NAME_PATTERN.fullmatch(result) is None:
        raise ConfigError(f"{path} must be a safe OpenBao path name")
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


def _s3_compatible_region(value: Any, path: str) -> str:
    """Accept an S3-compatible location without weakening AWS validation.

    AWS profiles retain their normal AWS-region requirement. Ceph RGW's
    standard single-zone ``default`` location is a meaningful S3-compatible
    value and is used by Hormuz's documented self-hosted reference profile.
    """

    result = _string(value, path)
    if result == "default" or _AWS_REGION_PATTERN.fullmatch(result) is not None:
        return result
    raise ConfigError(f"{path} must be a valid S3-compatible region identifier")


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
