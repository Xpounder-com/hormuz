"""Typed construction for already validated Hormuz configuration input."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

from ._config_custody import (
    build_custody_control_domain,
    build_custody_lifecycle,
    build_external_custody_domain,
)
from ._config_input import ConfigurationInputError, load_configuration_input
from ._config_identity import (
    build_bootstrap_administrators,
    build_identity_domain,
    resolve_static_identity_tokens,
)
from ._config_ingress import (
    build_ingress_domain,
    resolve_ingress_credential,
    validate_dedicated_ingress_credential_env,
)
from ._config_persistence import build_persistence_domain
from ._config_policy import (
    build_policy_control_domain,
    build_policy_domain,
    resolve_secret_controls,
)
from ._config_routing import build_model_route_domain, build_upstream_domain
from ._config_values import _integer
from .portfolio_config import build_portfolio_config
from .attribution_config import build_attribution_config
from .config import (
    ConfigError,
    GatewayConfig,
    PolicyAnalysisContext,
    PolicyValidationContext,
)


def build_gateway_config(
    config_type: type[GatewayConfig],
    path: str | Path,
    *,
    environ: dict[str, str] | None = None,
) -> GatewayConfig:
    """Construct typed runtime configuration after strict input validation."""

    return _build_gateway_config(
        config_type,
        path,
        environ=environ,
        resolve_credentials=True,
    )


def build_policy_validation_context(
    config_type: type[GatewayConfig],
    path: str | Path,
) -> PolicyValidationContext:
    """Project strict configuration into a credential-free policy validation view."""

    config = _build_gateway_config(
        config_type,
        path,
        environ=None,
        resolve_credentials=False,
    )
    return PolicyValidationContext(
        organization_ids=config.organization_ids,
        identities_by_actor=dict(config.identities_by_actor),
        model_routes=dict(config.model_routes),
    )


def build_policy_analysis_context(
    config_type: type[GatewayConfig],
    path: str | Path,
) -> PolicyAnalysisContext:
    """Project strict configuration into credential-free local analysis facts."""

    config = _build_gateway_config(
        config_type,
        path,
        environ=None,
        resolve_credentials=False,
    )
    return PolicyAnalysisContext(
        organization_ids=config.organization_ids,
        identities_by_actor=dict(config.identities_by_actor),
        model_routes=dict(config.model_routes),
        database_path=config.database_path,
        usage_storage=config.usage_storage,
        audit_chain=config.audit_chain,
    )


def _build_gateway_config(
    config_type: type[GatewayConfig],
    path: str | Path,
    *,
    environ: dict[str, str] | None,
    resolve_credentials: bool,
) -> GatewayConfig:
    """Construct and validate configuration, optionally resolving credential values."""

    cls = config_type
    source_path = Path(path).expanduser().resolve()
    try:
        raw = load_configuration_input(source_path)
    except ConfigurationInputError as error:
        raise ConfigError(error.code) from None

    ingress_domain = build_ingress_domain(raw)
    persistence_domain = build_persistence_domain(raw, source_path=source_path)
    external_custody_domain = build_external_custody_domain(raw)
    key_custody = external_custody_domain.key_custody

    policy_control_domain = build_policy_control_domain(
        raw,
        usage_storage=persistence_domain.usage_storage,
    )
    policy_control_mode = policy_control_domain.config.mode
    custody_control_domain = build_custody_control_domain(
        raw,
        usage_storage=persistence_domain.usage_storage,
        policy_control=policy_control_domain.config,
        key_custody=key_custody,
    )
    custody_control_mode = custody_control_domain.control.mode

    upstreams = build_upstream_domain(
        raw,
        source_path=source_path,
        key_custody=key_custody,
    )

    identity_domain = build_identity_domain(raw)
    static_identities = identity_domain.static_identities
    oidc_issuers = identity_domain.oidc_issuers
    identities_by_subject = identity_domain.identities_by_subject
    bootstrap_administrators = build_bootstrap_administrators(
        policy_control_domain.bootstrap_administrators_raw,
        static_identities=tuple(static_identities),
        oidc_issuers=oidc_issuers,
    )
    custody_bootstrap_administrators = build_bootstrap_administrators(
        custody_control_domain.bootstrap_administrators_raw,
        static_identities=tuple(static_identities),
        oidc_issuers=oidc_issuers,
        path_prefix="custody_control.bootstrap_administrators",
    )
    configured_organization_ids = tuple(
        sorted({identity.organization_id for identity in (*static_identities, *identities_by_subject.values())})
    )
    custody_lifecycle = build_custody_lifecycle(
        raw.get("custody_lifecycle"),
        organization_ids=configured_organization_ids,
        upstreams=upstreams,
        key_custody=key_custody,
        base_directory=source_path.parent,
    )
    if custody_lifecycle is not None and custody_control_mode != "postgresql":
        raise ConfigError("custody_lifecycle requires custody_control.mode postgresql")

    model_routes = build_model_route_domain(raw, upstreams=upstreams)

    policy_domain = build_policy_domain(raw, policy_control_mode=policy_control_mode)

    config = cls(
        source_path=source_path,
        listen=ingress_domain.listen,
        ingress=ingress_domain.ingress,
        database_path=persistence_domain.database_path,
        upstreams=upstreams,
        identities_by_token={
            f"pending-static-{index}": identity for index, identity in enumerate(static_identities)
        },
        model_routes=model_routes,
        organization_policy=policy_domain.organization_policy,
        oidc_issuers=oidc_issuers,
        identities_by_subject=identities_by_subject,
        secret_controls=policy_domain.secret_controls,
        team_policies=policy_domain.team_policies,
        actor_policies=policy_domain.actor_policies,
        max_request_bytes=_integer(raw.get("max_request_bytes", 25 * 1024 * 1024), "max_request_bytes", minimum=1024),
        upstream_timeout_seconds=_integer(raw.get("upstream_timeout_seconds", 600), "upstream_timeout_seconds", minimum=1),
        usage_storage=persistence_domain.usage_storage,
        policy_control=replace(
            policy_control_domain.config,
            bootstrap_administrators=bootstrap_administrators,
        ),
        custody_control=replace(
            custody_control_domain.control,
            bootstrap_administrators=custody_bootstrap_administrators,
        ),
        custody_executor=custody_control_domain.executor,
        custody_retention=custody_control_domain.retention,
        custody_lifecycle=custody_lifecycle,
        portfolio_control=build_portfolio_config(
            raw.get("portfolio_control"),
            (*static_identities, *identities_by_subject.values()),
        ),
        attribution_control=build_attribution_config(
            raw.get("attribution_control"),
            (*static_identities, *identities_by_subject.values()),
        ),
        key_custody=key_custody,
        audit_anchor=external_custody_domain.audit_anchor,
        audit_chain=external_custody_domain.audit_chain,
    )
    config.validate_references()
    validate_dedicated_ingress_credential_env(config)
    if not resolve_credentials:
        return config
    env = os.environ if environ is None else environ
    identities_by_token = resolve_static_identity_tokens(tuple(static_identities), env)
    resolved_ingress = resolve_ingress_credential(config.ingress, env)
    if resolved_ingress.credential and resolved_ingress.credential in identities_by_token:
        raise ConfigError("ingress credential must not equal a static identity token")
    return replace(
        config,
        ingress=resolved_ingress,
        identities_by_token=identities_by_token,
        secret_controls=resolve_secret_controls(config.secret_controls, env),
    )
