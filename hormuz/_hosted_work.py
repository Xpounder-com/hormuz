"""Explicit full-config, single-node hosted boundary; no implicit activation."""
from __future__ import annotations

import re
import os
import tempfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from ._config_builder import _build_gateway_config
from ._config_identity import resolve_static_identity_tokens
from ._config_ingress import resolve_ingress_credential
from ._config_policy import resolve_secret_controls
from ._config_session import resolve_session_credentials
from .outcome_connector_config import resolve_outcome_connector_credentials
from ._hosted_config import BACKEND_PORT, HostedError, _https_url
from ._hosted_provider import (
    MAX_PROVIDER_OUTPUT_TOKENS, MAX_PROVIDER_REQUEST_BYTES,
    MAX_PROVIDER_TIMEOUT_SECONDS, PROVIDER_DEPLOYMENT_METADATA_NAMES,
    _safe_configuration_file, deployment_metadata,
)
from .config import ConfigError, GatewayConfig
from .work_recovery import WORK_DATABASE, owned_databases, validate_store

MAX_SECRET_REFERENCES = 128
MAX_SELECTED_ENVIRONMENT_BYTES = 64 * 1024
CONTROL_NAMES = frozenset({
    "PATH", "HOME", "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONINSPECT",
    "LD_PRELOAD", "LD_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES", "DYLD_LIBRARY_PATH",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "SSL_CERT_FILE", "SSL_CERT_DIR",
    "PORT", "HORMUZ_CONFIG", "HORMUZ_HOSTED_MODE", "HORMUZ_PROVIDER_CONFIG",
    "HORMUZ_PROVIDER_PROFILE", "HORMUZ_POSTGRES_DSN", "HORMUZ_POSTGRES_MIGRATION_DSN",
    "HORMUZ_POLICY_CONTROL_DSN", "HORMUZ_CUSTODY_CONTROL_DSN", "HORMUZ_CUSTODY_EXECUTOR_DSN",
    "HORMUZ_FAILOVER_REHEARSAL_KEY", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
    *PROVIDER_DEPLOYMENT_METADATA_NAMES,
})


def _validate_profile(config):
    broker = config.session_broker
    directory = config.database_path.parent
    if (config.listen.host != "127.0.0.1" or config.listen.port != BACKEND_PORT
            or config.ingress.mode != "external_tls_proxy"
            or config.ingress.trusted_proxy_cidrs != ("127.0.0.1/32",)
            or not broker.enabled or not broker.onboarding_enabled or not broker.console_enabled
            or not broker.workspace_enabled or broker.allow_insecure_http
            or not config.ai_work.enabled or not config.upstreams or not config.model_routes):
        raise HostedError("hosted_work_profile_invalid")
    _https_url(broker.public_base_url, origin=True)
    for issuer in config.oidc_issuers.values():
        _https_url(issuer.issuer)
    if (config.usage_storage.backend != "sqlite"
            or config.database_path != directory / "usage.sqlite3"
            or broker.database_path != directory / "sessions.sqlite3"
            or config.ai_work.database_path != directory / WORK_DATABASE
            or directory != directory.resolve() or directory == directory.parent
            or broker.trusted_parent_path is None
            or broker.trusted_parent_path != broker.trusted_parent_path.resolve()
            or broker.trusted_parent_path == broker.trusted_parent_path.parent
            or not directory.is_relative_to(broker.trusted_parent_path)):
        raise HostedError("hosted_work_state_binding_invalid")
    if (config.policy_control.mode != "local" or config.policy_control.break_glass.enabled
            or config.custody_control.mode != "local" or config.custody_retention is not None
            or config.custody_lifecycle is not None or config.key_custody is not None
            or config.audit_anchor is not None or config.audit_chain is not None
            or any(upstream.api_key_envelope_path is not None for upstream in config.upstreams.values())):
        raise HostedError("hosted_work_external_control_unsupported")
    if (not 1024 <= config.max_request_bytes <= MAX_PROVIDER_REQUEST_BYTES
            or not 1 <= config.upstream_timeout_seconds <= MAX_PROVIDER_TIMEOUT_SECONDS
            or config.organization_policy.max_output_tokens is None
            or not 1 <= config.organization_policy.max_output_tokens <= MAX_PROVIDER_OUTPUT_TOKENS
            or config.secret_controls.mode not in {"redact", "deny"}
            or not config.secret_controls.builtins):
        raise HostedError("hosted_work_limits_invalid")
    for upstream in config.upstreams.values():
        _https_url(upstream.base_url, origin=True)
        if not upstream.api_key_env or upstream.allow_response_storage or upstream.allow_background:
            raise HostedError("hosted_work_upstream_invalid")


def credential_names(config):
    """Only validated declared references; never enumerate deployment values."""
    broker = config.session_broker
    names = {config.ingress.credential_env, broker.master_key_env}
    names.update(identity.token_env for identity in config.identities_by_token.values())
    names.update(upstream.api_key_env for upstream in config.upstreams.values())
    names.update(issuer.login.client_secret_env for issuer in config.oidc_issuers.values() if issuer.login)
    names.update(config.secret_controls.custom_secret_envs)
    if broker.workspace_domain_target:
        names.add(broker.workspace_domain_api_key_env)
    if config.ai_work.billing_price_id:
        names.update((config.ai_work.billing_api_key_env, config.ai_work.billing_webhook_secret_env))
    connectors = config.outcome_connectors
    if connectors:
        for channel in connectors.github:
            names.update(reference.environment_variable for reference in (*channel.webhook_secrets, *channel.identity_keys))
        for channel in connectors.linear:
            references = (*channel.identity_keys, channel.active_webhook_secret, channel.previous_webhook_secret,
                          channel.active_snapshot_secret, channel.previous_snapshot_secret)
            names.update(reference.environment_variable for reference in references if reference is not None)
    if (len(names) > MAX_SECRET_REFERENCES or any(
            not isinstance(name, str) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", name) is None
            or name.upper() in CONTROL_NAMES or name.upper().startswith(("PYTHON", "LD_", "DYLD_", "AWS_"))
            for name in names)):
        raise HostedError("hosted_work_credential_reference_invalid")
    return tuple(sorted(names))


def _deployment_settings(environ):
    return {name: environ.get(name, "") for name in PROVIDER_DEPLOYMENT_METADATA_NAMES}


def load_profile(path, environ):
    """Return full config plus its bounded child environment, before serving."""
    path = Path(path)
    _safe_configuration_file(path)
    try:
        unresolved = _build_gateway_config(GatewayConfig, path, environ={}, resolve_credentials=False)
        _validate_profile(unresolved)
        names = credential_names(unresolved)
    except (ConfigError, OSError, ValueError):
        raise HostedError("hosted_work_configuration_invalid") from None
    selected = {}
    for name in names:
        value = environ.get(name, "")
        if (not isinstance(value, str) or not value or "\x00" in value
                or len(value.encode("utf-8")) > 4096):
            raise HostedError("hosted_work_credential_missing_or_invalid")
        selected[name] = value
    metadata = _deployment_settings(environ)
    reported = deployment_metadata(metadata)
    if reported["platform"] == "render":
        directory = Path("/var/lib/hormuz/private/state")
        if (unresolved.database_path.parent != directory
                or unresolved.session_broker.trusted_parent_path != Path("/var/lib/hormuz")
                or unresolved.session_broker.public_base_url != reported["external_origin"]):
            raise HostedError("hosted_work_render_binding_invalid")
    selected.update(metadata)
    selected["HORMUZ_HOSTED_MODE"] = "ai-work"
    if sum(len(name) + len(value.encode("utf-8")) + 2 for name, value in selected.items()) > MAX_SELECTED_ENVIRONMENT_BYTES:
        raise HostedError("hosted_work_environment_too_large")
    if re.fullmatch(r"[A-Za-z0-9_-]{43,128}", selected[unresolved.ingress.credential_env]) is None:
        raise HostedError("hosted_work_ingress_credential_invalid")
    try:
        # Resolve the exact typed snapshot already checked above. Re-reading an
        # owner-edited file here could replace the validated transport boundary.
        tokens = resolve_static_identity_tokens(tuple(unresolved.identities_by_token.values()), selected)
        ingress = resolve_ingress_credential(unresolved.ingress, selected)
        if ingress.credential in tokens:
            raise ConfigError("ingress credential must not equal a static identity token")
        config = resolve_session_credentials(replace(
            unresolved, ingress=ingress, identities_by_token=tokens,
            secret_controls=resolve_secret_controls(unresolved.secret_controls, selected),
            outcome_connectors=resolve_outcome_connector_credentials(unresolved.outcome_connectors, selected),
        ), selected)
    except (ConfigError, OSError, ValueError):
        raise HostedError("hosted_work_credential_configuration_invalid") from None
    upstream_values = [selected[upstream.api_key_env] for upstream in config.upstreams.values()]
    if any(not value.isascii() or any(ord(char) < 33 or ord(char) == 127 for char in value)
           or len(value) < 16 for value in upstream_values):
        raise HostedError("hosted_work_provider_credential_invalid")
    if config.ai_work.billing_price_id:
        webhook = selected[config.ai_work.billing_webhook_secret_env]
        api_key = selected[config.ai_work.billing_api_key_env]
        if (re.fullmatch(r"whsec_[A-Za-z0-9_]{16,512}", webhook) is None
                or re.fullmatch(r"(?:sk|rk)_live_[A-Za-z0-9]{16,512}", api_key) is None
                or webhook == api_key or any(value in {webhook, api_key} for name, value in selected.items()
                    if name not in {config.ai_work.billing_webhook_secret_env, config.ai_work.billing_api_key_env})):
            raise HostedError("hosted_work_billing_credential_invalid")
    if any(selected[name] == config.ingress.credential for name in names if name != config.ingress.credential_env):
        raise HostedError("hosted_work_ingress_credential_not_dedicated")
    return config, selected


def initialize(config, environ):
    """Explicit owner operation creates all stores; serving never creates them."""
    from ._hosted_config import at_directory
    from ._hosted_state import _private, check_initialized, initialize as initialize_login, state_lock
    from . import work_gateway
    with state_lock(config):
        directory = config.database_path.parent
        # Reserve a fresh empty destination; never replace an existing or
        # partially initialized state. Build the complete unit in private
        # sibling storage before publishing the initialization marker.
        directory.mkdir(mode=0o700)
        reserved = _private(directory, directory=True)
        staging = None
        try:
            temporary = Path(tempfile.mkdtemp(prefix="." + directory.name + ".initialize-", dir=directory.parent))
            temporary_identity = _private(temporary, directory=True)
            staging = at_directory(config, temporary / "state")
            initialize_login(staging)
            owner = SimpleNamespace(config=staging)
            try:
                work_gateway.initialize(owner, environ=environ)
            finally:
                runtime = getattr(owner, "work_runtime", None)
                if runtime is not None:
                    runtime.close()
                work_gateway.close(owner)
            check_work_stores(staging)
            check_initialized(staging)
            staged_identity = _private(staging.database_path.parent, directory=True)
            current = _private(directory, directory=True)
            if (current.st_dev, current.st_ino) != (reserved.st_dev, reserved.st_ino) or any(directory.iterdir()):
                raise HostedError("hosted_work_initialization_destination_changed")
            os.rename(staging.database_path.parent, directory)
            descriptor = os.open(directory.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            published = _private(directory, directory=True)
            if (published.st_dev, published.st_ino) != (staged_identity.st_dev, staged_identity.st_ino):
                raise HostedError("hosted_work_initialization_destination_changed")
            check_initialized(config)
            check_work_stores(config)
        except BaseException:
            # Remove only this attempt's still-empty reservation. Any changed
            # or published state remains for explicit owner inspection.
            try:
                current = _private(directory, directory=True)
                if (current.st_dev, current.st_ino) == (reserved.st_dev, reserved.st_ino) and not any(directory.iterdir()):
                    directory.rmdir()
            except (OSError, HostedError):
                pass
            raise
        finally:
            if staging is not None:
                _cleanup_staging(staging, temporary_identity)


def _cleanup_staging(config, identity):
    """Only known private entries in this attempt's allocated directory."""
    from ._hosted_state import DATABASES, MARKER, _private
    directory = config.database_path.parent
    root = directory.parent
    try:
        current = _private(root, directory=True)
        if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino):
            return
        entries = list(root.iterdir())
        if any(path.name not in {"state", ".state.hosted.lock"} for path in entries):
            return
        files = [path for path in entries if path.name != "state"]
        if directory.exists():
            _private(directory, directory=True)
            names = {*DATABASES, MARKER, *owned_databases(config), WORK_DATABASE + ".owner.lock"}
            names.update(name + suffix for name in tuple(names) for suffix in ("-wal", "-shm"))
            state_files = list(directory.iterdir())
            if any(path.name not in names for path in state_files):
                return
            files.extend(state_files)
        for path in files:
            _private(path)
        for path in files:
            path.unlink()
        if directory.exists():
            directory.rmdir()
        root.rmdir()
    except (OSError, HostedError):
        # Unexpected state is left for explicit operator inspection.
        return


def check_work_stores(config):
    from ._hosted_state import _private
    identities = []
    for name in owned_databases(config):
        path = config.database_path.parent / name
        info = _private(path)
        validate_store(path, billing=name.endswith(".billing.sqlite3"))
        identities.append((info.st_dev, info.st_ino))
    return tuple(identities)
