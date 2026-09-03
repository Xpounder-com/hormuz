"""Strict configuration boundary for the opt-in single-node provider pilot.

The existing hosted profile remains provider-free.  This module accepts a
second, operator-owned gateway configuration only when it is bound to the same
login state and stays inside the deliberately small Render pilot envelope.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import fields
from pathlib import Path
from urllib.parse import urlsplit

from ._hosted_config import BACKEND_PORT, SECRET_NAMES, HostedError, load_profile
from .config import (
    ConfigError,
    CustodyControlConfig,
    CustodyExecutorConfig,
    GatewayConfig,
    PolicyControlConfig,
    PostgresPoolConfig,
    UsageStorageConfig,
)


PROVIDER_CONFIG_ENV = "HORMUZ_PROVIDER_CONFIG"
PROVIDER_KEY_ENVS = {
    "openai": "HORMUZ_OPENAI_PROVIDER_KEY",
    "anthropic": "HORMUZ_ANTHROPIC_PROVIDER_KEY",
}
PROVIDER_RUNTIME_DSN_ENV = "HORMUZ_POSTGRES_DSN"
PROVIDER_MIGRATION_DSN_ENV = "HORMUZ_POSTGRES_MIGRATION_DSN"
PROVIDER_FAILOVER_REHEARSAL_ENV = "HORMUZ_FAILOVER_REHEARSAL_KEY"
PROVIDER_DEPLOYMENT_METADATA_NAMES = (
    "RENDER",
    "RENDER_CPU_COUNT",
    "RENDER_EXTERNAL_HOSTNAME",
    "RENDER_EXTERNAL_URL",
    "RENDER_GIT_BRANCH",
    "RENDER_GIT_COMMIT",
    "RENDER_GIT_REPO_SLUG",
    "RENDER_INSTANCE_ID",
    "RENDER_SERVICE_ID",
    "RENDER_SERVICE_TYPE",
    "RENDER_WEB_CONCURRENCY",
)
PROVIDER_SECRET_NAMES = (
    *SECRET_NAMES,
    *PROVIDER_KEY_ENVS.values(),
    PROVIDER_RUNTIME_DSN_ENV,
    PROVIDER_FAILOVER_REHEARSAL_ENV,
)
PROVIDER_OPERATOR_SECRET_NAMES = (
    *PROVIDER_SECRET_NAMES,
    PROVIDER_MIGRATION_DSN_ENV,
)
PROVIDER_CHILD_ENV_NAMES = (
    *PROVIDER_SECRET_NAMES,
    *PROVIDER_DEPLOYMENT_METADATA_NAMES,
)
PROVIDER_BASE_URLS = {
    "openai": "https://api.openai.com",
    "anthropic": "https://api.anthropic.com",
}
PROVIDER_ROUTE_ALIASES = {
    "openai": ("openai-primary", "openai-secondary"),
    "anthropic": ("anthropic-primary", "anthropic-secondary"),
}
MAX_PROVIDER_CONFIG_BYTES = 64 * 1024
MAX_PROVIDER_REQUEST_BYTES = 2 * 1024 * 1024
MAX_PROVIDER_TIMEOUT_SECONDS = 600
MAX_PROVIDER_OUTPUT_TOKENS = 32_768
PROVIDER_USAGE_STORAGE = UsageStorageConfig(
    backend="postgresql",
    postgres_dsn_env=PROVIDER_RUNTIME_DSN_ENV,
    postgres_migration_dsn_env=PROVIDER_MIGRATION_DSN_ENV,
    postgres_schema="hormuz",
    postgres_runtime_role="hormuz_runtime",
    postgres_pool=PostgresPoolConfig(
        min_connections=1,
        max_connections=4,
        acquire_timeout_seconds=5,
        max_waiting=8,
        max_lifetime_seconds=1800,
        max_idle_seconds=300,
    ),
)


def deployment_metadata(values: dict[str, str]) -> dict[str, object]:
    """Validate Render-owned, non-secret provenance for a provider process."""

    if values.get("RENDER", "") != "true":
        return {
            "platform": "local",
            "source_commit": "unavailable",
            "source_branch": "unavailable",
            "repository": "unavailable",
            "cpu_count": "unavailable",
            "web_concurrency": "unavailable",
            "external_origin": "unavailable",
            "service_id": "unavailable",
            "instance_fingerprint": "unavailable",
        }
    commit = values.get("RENDER_GIT_COMMIT", "")
    external_hostname = values.get("RENDER_EXTERNAL_HOSTNAME", "")
    external_url = values.get("RENDER_EXTERNAL_URL", "")
    instance_id = values.get("RENDER_INSTANCE_ID", "")
    service_id = values.get("RENDER_SERVICE_ID", "")
    if (
        re.fullmatch(r"[0-9a-f]{40}", commit) is None
        or re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.onrender\.com", external_hostname) is None
        or external_url != f"https://{external_hostname}"
        or values.get("RENDER_GIT_BRANCH") != "main"
        or values.get("RENDER_GIT_REPO_SLUG") != "Xpounder-com/hormuz"
        or not 1 <= len(instance_id) <= 128
        or not instance_id.isascii()
        or any(character.isspace() or ord(character) < 33 or ord(character) == 127 for character in instance_id)
        or re.fullmatch(r"srv-[a-z0-9]{16,32}", service_id) is None
        or values.get("RENDER_SERVICE_TYPE") != "web"
        or values.get("RENDER_CPU_COUNT") not in {"0.5", "0.50"}
        or values.get("RENDER_WEB_CONCURRENCY") != "1"
    ):
        raise HostedError("hosted_provider_deployment_metadata_invalid")
    return {
        "platform": "render",
        "source_commit": commit,
        "source_branch": "main",
        "repository": "Xpounder-com/hormuz",
        "cpu_count": "0.5",
        "web_concurrency": "1",
        "external_origin": external_url,
        "service_id": service_id,
        "instance_fingerprint": hashlib.sha256(instance_id.encode("ascii")).hexdigest()[:16],
    }


def _validate_render_runtime(
    config: GatewayConfig,
    metadata: dict[str, object],
) -> None:
    if metadata["platform"] != "render":
        return
    state = Path("/var/lib/hormuz/private/state").resolve()
    issuer_hostnames = {
        urlsplit(issuer.issuer).hostname or "" for issuer in config.oidc_issuers.values()
    }
    if (
        config.database_path.resolve() != state / "usage.sqlite3"
        or config.session_broker.database_path is None
        or config.session_broker.database_path.resolve() != state / "sessions.sqlite3"
        or config.session_broker.trusted_parent_path is None
        or config.session_broker.trusted_parent_path.resolve() != Path("/var/lib/hormuz").resolve()
        or config.session_broker.public_base_url != metadata["external_origin"]
        or len(issuer_hostnames) != 1
        or not next(iter(issuer_hostnames)).endswith((".okta.com", ".oktapreview.com"))
    ):
        raise HostedError("hosted_provider_render_runtime_invalid")


def _safe_configuration_file(path: Path) -> None:
    try:
        details = path.lstat()
    except OSError:
        raise HostedError("hosted_provider_configuration_unavailable") from None
    if (
        not stat.S_ISREG(details.st_mode)
        or details.st_uid not in {0, os.getuid()}
        or details.st_mode & 0o022
        or details.st_nlink != 1
        or details.st_size > MAX_PROVIDER_CONFIG_BYTES
    ):
        raise HostedError("hosted_provider_configuration_file_unsafe")


def _validate_credentials(credentials: dict[str, str]) -> None:
    if (
        credentials.get("HORMUZ_HOSTED_MODE") == "provider-pilot"
        and credentials.get(PROVIDER_MIGRATION_DSN_ENV, "")
    ):
        # The supervisor and gateway child share one container UID. Keeping an
        # owner credential in the supervisor environment would therefore make
        # it recoverable through process inspection after inference starts.
        raise HostedError("hosted_provider_migration_credential_forbidden")
    values = [credentials.get(name, "") for name in PROVIDER_SECRET_NAMES]
    if len(set(values)) != len(values):
        raise HostedError("hosted_provider_credentials_must_be_distinct")
    for name in PROVIDER_KEY_ENVS.values():
        value = credentials.get(name, "")
        if not isinstance(value, str) or not 16 <= len(value) <= 512 or not value.isascii() or any(
            character.isspace() or ord(character) < 33 or ord(character) == 127
            for character in value
        ):
            raise HostedError("hosted_provider_credential_invalid")
    dsn = credentials.get(PROVIDER_RUNTIME_DSN_ENV, "")
    if (
        not isinstance(dsn, str)
        or not 16 <= len(dsn) <= 4096
        or not dsn.startswith(("postgresql://", "postgres://"))
        or any(character.isspace() or ord(character) < 33 or ord(character) == 127 for character in dsn)
    ):
        raise HostedError("hosted_provider_postgres_credential_invalid")
    rehearsal = credentials.get(PROVIDER_FAILOVER_REHEARSAL_ENV, "")
    if re.fullmatch(r"[A-Za-z0-9_-]{43,128}", rehearsal) is None:
        raise HostedError("hosted_provider_rehearsal_credential_invalid")


def _same_dataclass(left, right, *, ignore: tuple[str, ...] = ()) -> bool:
    return all(getattr(left, field.name) == getattr(right, field.name) for field in fields(left) if field.name not in ignore)


def _validate_state_binding(config: GatewayConfig, staging: GatewayConfig) -> None:
    if (
        config.listen != staging.listen
        or not _same_dataclass(config.ingress, staging.ingress)
        or config.database_path != staging.database_path
        or config.session_broker != staging.session_broker
        or config.oidc_issuers != staging.oidc_issuers
    ):
        raise HostedError("hosted_provider_state_binding_mismatch")


def _validate_provider_runtime(config: GatewayConfig) -> None:
    if (
        config.listen.host != "127.0.0.1"
        or config.listen.port != BACKEND_PORT
        or config.ingress.mode != "external_tls_proxy"
        or config.ingress.trusted_proxy_cidrs != ("127.0.0.1/32",)
        or config.ingress.credential_env != "HORMUZ_INGRESS_CREDENTIAL"
        or config.usage_storage != PROVIDER_USAGE_STORAGE
        or not config.session_broker.enabled
        or not config.session_broker.onboarding_enabled
        or not config.session_broker.console_enabled
        or config.session_broker.allow_insecure_http
        or config.identities_by_token
        or config.identities_by_subject
    ):
        raise HostedError("hosted_provider_configuration_unsafe")

    if set(config.upstreams) != set(PROVIDER_BASE_URLS):
        raise HostedError("hosted_provider_upstreams_invalid")
    for protocol, base_url in PROVIDER_BASE_URLS.items():
        upstream = config.upstreams[protocol]
        if (
            upstream.base_url != base_url
            or upstream.api_key_env != PROVIDER_KEY_ENVS[protocol]
            or upstream.api_key_envelope_path is not None
            or upstream.allow_response_storage
            or upstream.allow_background
        ):
            raise HostedError("hosted_provider_upstreams_invalid")

    expected_aliases = {alias for aliases in PROVIDER_ROUTE_ALIASES.values() for alias in aliases}
    if set(config.model_routes) != expected_aliases:
        raise HostedError("hosted_provider_routes_invalid")
    for protocol, (primary_alias, secondary_alias) in PROVIDER_ROUTE_ALIASES.items():
        primary = config.model_routes[primary_alias]
        secondary = config.model_routes[secondary_alias]
        if (
            primary.protocol != protocol
            or secondary.protocol != protocol
            or primary.failover_alias != secondary_alias
            or secondary.failover_alias is not None
            or primary.upstream_model == secondary.upstream_model
            or primary.upstream_model.startswith("replace-with-")
            or secondary.upstream_model.startswith("replace-with-")
        ):
            raise HostedError("hosted_provider_routes_invalid")
        for route in (primary, secondary):
            if (
                route.input_cost_per_million <= 0
                or route.cache_read_cost_per_million <= 0
                or route.cache_write_cost_per_million <= 0
                or route.output_cost_per_million <= 0
            ):
                raise HostedError("hosted_provider_routes_invalid")

    policy = config.organization_policy
    if (
        set(policy.allowed_clients or ()) != {"codex", "claude-code"}
        or set(policy.allowed_models or ()) != expected_aliases
        or policy.fallback_model is not None
        or policy.fallback_models != {
            protocol: aliases[0] for protocol, aliases in PROVIDER_ROUTE_ALIASES.items()
        }
        or policy.max_output_tokens is None
        or policy.max_output_tokens > MAX_PROVIDER_OUTPUT_TOKENS
        or policy.monthly_budget_usd is None
        or policy.monthly_budget_usd <= 0
        or policy.per_actor_monthly_budget_usd is None
        or policy.per_actor_monthly_budget_usd <= 0
        or config.team_policies
        or config.actor_policies
    ):
        raise HostedError("hosted_provider_policy_invalid")

    controls = config.secret_controls
    if (
        controls.mode not in {"redact", "deny"}
        or not controls.builtins
        or controls.custom_secret_envs
        or controls.custom_secret_values
    ):
        raise HostedError("hosted_provider_egress_controls_invalid")
    if not 1024 <= config.max_request_bytes <= MAX_PROVIDER_REQUEST_BYTES or not (
        30 <= config.upstream_timeout_seconds <= MAX_PROVIDER_TIMEOUT_SECONDS
    ):
        raise HostedError("hosted_provider_limits_invalid")
    if (
        config.policy_control != PolicyControlConfig()
        or config.custody_control != CustodyControlConfig()
        or config.custody_executor != CustodyExecutorConfig()
        or config.custody_retention is not None
        or config.custody_lifecycle is not None
        or config.key_custody is not None
        or config.audit_anchor is not None
        or config.audit_chain is not None
        or config.portfolio_control is not None
        or config.attribution_control is not None
    ):
        raise HostedError("hosted_provider_optional_control_unsupported")


def load_provider_profile(
    hosted_path: Path,
    provider_path: Path,
    credentials: dict[str, str],
) -> GatewayConfig:
    """Load one full gateway config only inside the fixed provider-pilot envelope."""

    staging = load_profile(hosted_path, credentials)
    _safe_configuration_file(provider_path)
    _validate_credentials(credentials)
    try:
        config = GatewayConfig.load(provider_path, environ=credentials)
    except (ConfigError, OSError, ValueError):
        raise HostedError("hosted_provider_configuration_invalid") from None
    _validate_state_binding(config, staging)
    _validate_provider_runtime(config)
    metadata = deployment_metadata(credentials)
    _validate_render_runtime(config, metadata)
    return config
