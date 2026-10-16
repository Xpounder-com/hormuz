"""Typed construction for already validated Hormuz configuration input."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

from ._config_input import ConfigurationInputError, load_configuration_input, parse_configuration_input
from ._config_identity import (
    build_identity_domain,
    resolve_static_identity_tokens,
)
from ._config_ingress import build_ingress_domain
from ._config_persistence import build_persistence_domain
from ._config_policy import (
    build_policy_domain,
    resolve_secret_controls,
)
from ._config_routing import build_model_route_domain, build_upstream_domain
from ._config_work import build_ai_work
from .route_admission import build_route_admission
from .shadow_routing import build_shadow_router
from ._config_values import _boolean, _integer
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


def build_preflight_config(config_type: type[GatewayConfig], path: str | Path, text: str) -> GatewayConfig:
    """Build one metadata snapshot without resolving any credential source."""
    return _build_gateway_config(config_type, path, environ=None, resolve_credentials=False,
                                 configuration_text=text)


def _build_gateway_config(
    config_type: type[GatewayConfig],
    path: str | Path,
    *,
    environ: dict[str, str] | None,
    resolve_credentials: bool,
    configuration_text: str | None = None,
) -> GatewayConfig:
    """Construct and validate configuration, optionally resolving credential values."""

    from .managed_llama import build_managed_llama

    source_path = Path(path).expanduser().resolve()
    try:
        raw = (load_configuration_input(source_path) if configuration_text is None
               else parse_configuration_input(configuration_text.encode("utf-8")))
    except ConfigurationInputError as error:
        raise ConfigError(error.code) from None
    ingress = build_ingress_domain(raw)
    if ingress.ingress.mode != "local":
        raise ConfigError("router_scope_retired_configuration")
    persistence = build_persistence_domain(raw, source_path=source_path)
    upstreams = build_upstream_domain(raw, source_path=source_path, key_custody=None)
    identities = build_identity_domain(raw).static_identities
    routes = build_model_route_domain(raw, upstreams=upstreams)
    admission = build_route_admission(raw, routes, upstreams)
    policy = build_policy_domain(raw, policy_control_mode="local")
    config = config_type(
        source_path=source_path, listen=ingress.listen, ingress=ingress.ingress,
        database_path=persistence.database_path, usage_storage=persistence.usage_storage,
        upstreams=upstreams, model_routes=routes,
        identities_by_token={f"pending-static-{index}": identity for index, identity in enumerate(identities)},
        organization_policy=policy.organization_policy, secret_controls=policy.secret_controls,
        team_policies=policy.team_policies, actor_policies=policy.actor_policies,
        max_request_bytes=_integer(raw.get("max_request_bytes", 25 * 1024 * 1024), "max_request_bytes", minimum=1024),
        upstream_timeout_seconds=_integer(raw.get("upstream_timeout_seconds", 600), "upstream_timeout_seconds", minimum=1),
        ai_work=build_ai_work(raw, source_path=source_path),
        route_admission=admission,
        managed_llama=build_managed_llama(raw, routes, upstreams, admission),
        shadow_router=build_shadow_router(raw, routes, upstreams, admission),
        native_codex_catalog=_boolean(raw.get("native_codex_catalog", False), "native_codex_catalog"),
        native_codex_responses=_boolean(raw.get("native_codex_responses", False), "native_codex_responses"),
    )
    config.validate_references()
    if not resolve_credentials:
        return config
    env = os.environ if environ is None else environ
    return replace(config, identities_by_token=resolve_static_identity_tokens(identities, env),
                   secret_controls=resolve_secret_controls(config.secret_controls, env))
