"""Read-only configuration and connection diagnosis, without provider probes."""
from pathlib import Path

from .config import GatewayConfig
from .client_versions import SUPPORTED_CODEX_VERSION


def diagnose(lifetime, path: Path, executable: str | None):
    config = GatewayConfig.load(path)
    status = lifetime.status()
    compatibility = "unobserved"
    if executable:
        from .connected_client import _qualify_client
        import shutil
        program = shutil.which(executable)
        if program:
            from .connected_client import ConnectedClientError
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
    return {"schema_id": "hormuz.client-diagnosis", "schema_version": 1,
            "connection": status, "client_compatibility": compatibility,
            "supported_client_version": SUPPORTED_CODEX_VERSION,
            "routing_mode": config.route_admission.mode,
            "qualified_local_routes": len(config.route_admission.local_routes),
            "model_readiness": "unobserved", "task_completion": "unobserved",
            "scope": "codex_user_defaults", "project_overrides": "unobserved",
            "action": action}
