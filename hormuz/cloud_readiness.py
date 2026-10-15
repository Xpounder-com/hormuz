"""Read-only authorization preflight shared by CLI and coding-client callers."""
from pathlib import Path
import shutil
import subprocess

from ._contract_schemas.cloud_readiness import ACTIONS, PATHS, SCHEMA_ID
from .client_versions import SUPPORTED_CODEX_VERSION
from .config import ConfigError, GatewayConfig
from .connected_client import ConnectedClientError, _qualify_client, _read, _require_unmanaged
from .contracts import contract_envelope
from .policy_runtime import PolicyRuntime


def _configuration(path, actor_id):
    facts = {"status": "unavailable", "routing_mode": "unknown", "codex_allowed": None,
             "cloud_routes": 0, "allowed_cloud_routes": 0}
    try:
        text = _read(Path(path).expanduser().absolute())
        if text is None:
            return facts, ["configuration_unavailable"]
        config = GatewayConfig.parse_preflight(path, text)
        facts.update(status="available", routing_mode=config.route_admission.mode)
        blockers = ["local_only_forbids_cloud"] if config.route_admission.mode == "local_only" else []
        identity = (config.identities_by_actor.get(actor_id) if actor_id is not None
                    else next(iter(config.identities_by_actor.values())))
        cloud = {alias for alias, route in config.model_routes.items()
                 if route.protocol == "openai" and config.upstreams[route.destination_id].locality == "cloud"}
        facts["cloud_routes"] = len(cloud)
        if identity is None:
            return facts, [*blockers, "actor_unavailable"]
        policy = PolicyRuntime(config).snapshot_for(identity).effective_policy
        allowed = cloud if policy.allowed_models is None else cloud.intersection(policy.allowed_models)
        codex_allowed = ((not identity.allowed_clients or "codex" in identity.allowed_clients)
                         and (policy.allowed_clients is None or "codex" in policy.allowed_clients))
        facts.update(codex_allowed=codex_allowed, allowed_cloud_routes=len(allowed) if codex_allowed else 0)
        if not codex_allowed:
            blockers.append("client_not_allowed")
        if not cloud:
            blockers.append("cloud_route_missing")
        elif not allowed:
            blockers.append("cloud_models_not_allowed")
        return facts, blockers
    except (ConnectedClientError, ConfigError, OSError, ValueError, RuntimeError):
        # Older Python versions raise RuntimeError for path-resolution loops.
        return {**facts, "status": "invalid", "routing_mode": "unknown", "codex_allowed": None,
                "cloud_routes": 0, "allowed_cloud_routes": 0}, ["configuration_invalid"]


def inspect_cloud_readiness(home, config_path, *, executable, actor_id, surface):
    if surface not in {"cli", "desktop"}:
        raise ConnectedClientError("configuration_shape_unsupported")
    client = {"surface": surface, "version_check": "not_performed", "pinned_cli_version": SUPPORTED_CODEX_VERSION}
    blockers = []
    if surface == "desktop":
        blockers.append("desktop_unqualified")
    else:
        program = shutil.which(executable)
        if program is None:
            client["version_check"] = "unavailable"
            blockers.append("client_unavailable")
        else:
            try:
                _qualify_client(program)
                client["version_check"] = "pinned"
            except (ConnectedClientError, OSError, subprocess.SubprocessError):
                client["version_check"] = "unsupported"
                blockers.append("client_version_unsupported")
    try:
        _require_unmanaged(home)
    except ConnectedClientError as error:
        blockers.append("managed_configuration" if error.code == "connected_client_managed_configuration"
                        else "managed_configuration_unknown")
    except OSError:
        blockers.append("managed_configuration_unknown")
    configuration, configuration_blockers = _configuration(config_path, actor_id)
    blockers.extend(configuration_blockers)
    # A missing API route or incompatible user-default setup is not a reason
    # to prescribe paid API access for either separately authorized plan path.
    plan_blockers = [code for code in configuration_blockers
                     if code not in {"cloud_route_missing", "cloud_models_not_allowed"}]
    if surface == "desktop":
        plan_blockers.append("desktop_unqualified")
    path_blockers = {
        "api_key": [*blockers, "credentials_unverified", "provider_unqualified"],
        "native_codex_gateway": [*plan_blockers, "native_gateway_not_implemented", "managed_workspace_setup_required",
                                 "account_consent_eligibility_unverified", "provider_unqualified"],
        "chatgpt_app": [*plan_blockers, "app_authorization_not_implemented", "registration_and_host_id_required",
                        "account_consent_eligibility_unverified", "provider_unqualified"],
    }
    return contract_envelope(SCHEMA_ID, {
        "scope": "configuration_only", "client": client, "configuration": configuration,
        "paths": [{"id": identifier, "billing": billing, "documentation": documentation,
                   "status": ("blocked" if blockers else "unverified") if identifier == "api_key" else "not_implemented",
                   "blockers": [{"code": code, "action": ACTIONS[code]} for code in path_blockers[identifier]]}
                  for identifier, (billing, documentation) in PATHS.items()],
        "effects": {"settings_changed": False, "credentials_read": False, "provider_requests": 0},
    })
