"""Static and generic-OIDC identity configuration construction ownership."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlparse

from ._config_values import (
    _boolean,
    _environment_name,
    _integer,
    _object,
    _string,
    _string_tuple,
    _url,
)
from ._config_session import build_oidc_login
from .config import BootstrapAdministrator, ConfigError, Identity, OIDCIssuerConfig


@dataclass(frozen=True)
class IdentityConstruction:
    static_identities: tuple[Identity, ...]
    oidc_issuers: dict[str, OIDCIssuerConfig]
    identities_by_subject: dict[tuple[str, str], Identity]


def build_identity_domain(raw: dict[str, Any]) -> IdentityConstruction:
    broker = raw.get("authentication", {}).get("session_broker", {})
    managed_login = broker.get("enabled") is True and broker.get("onboarding_enabled") is True
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
            login=build_oidc_login(item.get("login"), prefix=f"{prefix}.login"),
        )
        oidc_issuers[issuer] = issuer_config
        subjects_raw = item.get("subjects", [])
        if not isinstance(subjects_raw, list):
            raise ConfigError(f"{prefix}.subjects must be an array")
        if not subjects_raw and not (managed_login and issuer_config.login is not None):
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
    if not static_identities and not identities_by_subject and not (
        managed_login and any(issuer.login is not None for issuer in oidc_issuers.values())
    ):
        raise ConfigError("At least one static identity or OIDC subject mapping is required")
    _validate_identity_consistency((*static_identities, *identities_by_subject.values()))
    return IdentityConstruction(
        static_identities=tuple(static_identities),
        oidc_issuers=oidc_issuers,
        identities_by_subject=identities_by_subject,
    )


def build_bootstrap_administrators(
    value: list[Any],
    *,
    static_identities: tuple[Identity, ...],
    oidc_issuers: dict[str, OIDCIssuerConfig],
    path_prefix: str = "policy_control.bootstrap_administrators",
) -> tuple[BootstrapAdministrator, ...]:
    """Validate tenant-qualified, one-time control-plane bootstrap identities."""

    administrators: list[BootstrapAdministrator] = []
    seen: set[tuple[str, str, str, str]] = set()
    for index, raw_value in enumerate(value):
        path = f"{path_prefix}[{index}]"
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


def resolve_static_identity_tokens(
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
