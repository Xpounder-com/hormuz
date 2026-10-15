"""Read-only configuration and connection diagnosis, without provider probes."""
from pathlib import Path

from .config import GatewayConfig
from .connected_client import ConnectedClientError
from .client_versions import SUPPORTED_CODEX_VERSION


def diagnose(lifetime, path: Path, executable: str | None, project: str | None = None, launch_arguments: list[str] | None = None):
    config = GatewayConfig.load(path)
    status, binding = lifetime.diagnosis_context(path)
    compatibility = "unobserved"
    if executable:
        from .connected_client import _qualify_client
        import shutil
        program = shutil.which(executable)
        if program:
            try:
                _qualify_client(program)
                compatibility = "supported"
            except ConnectedClientError:
                compatibility = "unsupported"
        else:
            compatibility = "unavailable"
    action = "none"
    if status["phase"] == "disconnected":
        action = "preview_connection"
    elif status["phase"] != "connected":
        action = "inspect_recovery"
    elif status["configuration"] != "installed":
        action = "inspect_configuration_changes"
    elif status["router"] != "ready":
        action = "inspect_owned_runtime"
    if compatibility in {"unsupported", "unavailable"}:
        action = "select_supported_client"
    if binding == "different":
        action = "select_owned_configuration"
    from .client_overrides import ROUTING_KEYS, OverrideInspectionError, inspect_overrides
    try:
        overrides = inspect_overrides(lifetime.home, Path(project or Path.cwd()).expanduser().absolute(), launch_arguments)
        if overrides["conflicts"] and action in {"none", "preview_connection"}:
            source = overrides["conflicts"][-1]["source"]
            action = "inspect_" + source + "_overrides"
        if overrides["launch_state"] == "remote" and action in {"none", "preview_connection", "inspect_launch_overrides", "inspect_profile_overrides", "inspect_project_overrides"}:
            action = "inspect_remote_connection"
    except (ValueError, OSError, RuntimeError, ConnectedClientError) as error:
        source = error.source if isinstance(error, OverrideInspectionError) else "project"
        overrides = {"schema_id": "hormuz.client-overrides", "schema_version": 1,
                     "supported_client_version": SUPPORTED_CODEX_VERSION,
                     "scope": "selected_project_and_supplied_launch", "project_state": "invalid",
                     "layers": [], "conflicts": [], "launch_state": "unobserved",
                     "effective_routing": {key: "unobserved" for key in ROUTING_KEYS},
                     "running_session": "unobserved", "error": {"code": "override_inspection_unavailable"}}
        if action in {"none", "preview_connection"}:
            action = "inspect_" + source + "_configuration"
    return {"schema_id": "hormuz.client-diagnosis", "schema_version": 1,
            "connection": status, "client_compatibility": compatibility,
            "supported_client_version": SUPPORTED_CODEX_VERSION,
            "configuration_binding": binding,
            "routing_mode": config.route_admission.mode if binding != "different" else "unobserved",
            "qualified_local_routes": len(config.route_admission.local_routes) if binding != "different" else None,
            "model_readiness": "unobserved", "task_completion": "unobserved",
            "scope": "codex_user_defaults", "project_overrides": overrides["project_state"],
            "override_diagnosis": overrides,
            "action": action}
