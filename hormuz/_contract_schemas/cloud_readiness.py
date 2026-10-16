"""Content-free, configuration-only cloud readiness; never an authority grant."""
import re

from .common import ContractValidationError, _exact_keys, _value_mapping, _value_string


SCHEMA_ID = "hormuz.cloud-readiness"
FIELDS = {"schema_id", "schema_version", "scope", "client", "configuration", "paths", "effects"}
PATHS = {
    "api_key": ("api_usage", "https://learn.chatgpt.com/docs/enterprise/gateway-compatibility"),
    "native_codex_gateway": ("chatgpt_plan", "https://learn.chatgpt.com/docs/enterprise/sign-in-with-chatgpt-through-a-gateway"),
    "chatgpt_app": ("chatgpt_plan", "https://developers.openai.com/siwc/token-sharing-open-source/"),
}
ACTIONS = {
    "configuration_unavailable": "Supply a readable router configuration; no settings were changed.",
    "configuration_invalid": "Repair the router configuration before attempting connection.",
    "actor_unavailable": "Choose an actor declared in the router configuration.",
    "client_not_allowed": "Review the selected identity and effective Codex policy.",
    "cloud_route_missing": "Declare an explicit cloud OpenAI route only if API usage is intended.",
    "cloud_models_not_allowed": "Review effective model policy before selecting a cloud route.",
    "local_only_forbids_cloud": "Keep local-only for offline work; changing cloud permissions requires a separate decision.",
    "client_unavailable": "Install or select the pinned Codex CLI executable.",
    "client_version_unsupported": "Use the pinned CLI version; it does not qualify subscription forwarding.",
    "desktop_unqualified": "Qualify the actual desktop app and its bundled engine separately.",
    "managed_configuration": "Use the workspace owner's managed setup; user defaults cannot override it.",
    "managed_configuration_unknown": "Inspect managed configuration availability before connection.",
    "credentials_unverified": "Verify the intended credential through a separately authorized connection.",
    "provider_unqualified": "Qualify the selected provider, model, streaming, tools and continuation separately.",
    "native_gateway_not_implemented": "Implement the native Codex gateway adapter before activation.",
    "managed_workspace_setup_required": "Verify the supported build and workspace-managed provider requirements.",
    "account_consent_eligibility_unverified": "Complete the chosen native consent flow and establish account eligibility.",
    "app_authorization_not_implemented": "Implement app-specific authorization and its plan request profile.",
    "registration_and_host_id_required": "Register the app and persist a stable host ID in the authorized flow.",
}


def validate_readiness(value):
    _exact_keys(value, FIELDS)
    if _value_string(value, "scope") != "configuration_only":
        raise ContractValidationError("invalid cloud readiness scope")
    client = _value_mapping(value, "client")
    _exact_keys(client, {"surface", "version_check", "pinned_cli_version"})
    surface = _value_string(client, "surface")
    version = _value_string(client, "version_check")
    if (surface not in {"cli", "desktop"}
            or version not in ({"not_performed"} if surface == "desktop" else {"pinned", "unavailable", "unsupported"})
            or re.fullmatch(r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}", _value_string(client, "pinned_cli_version")) is None):
        raise ContractValidationError("invalid cloud readiness client")
    configuration = _value_mapping(value, "configuration")
    _exact_keys(configuration, {"status", "routing_mode", "codex_allowed", "cloud_routes", "allowed_cloud_routes"})
    status = _value_string(configuration, "status")
    mode = _value_string(configuration, "routing_mode")
    if status not in {"available", "unavailable", "invalid"} or mode not in {"configured", "auto", "local_only", "unknown"}:
        raise ContractValidationError("invalid cloud readiness configuration")
    if configuration["codex_allowed"] is not None and type(configuration["codex_allowed"]) is not bool:
        raise ContractValidationError("invalid cloud readiness permission")
    for key in ("cloud_routes", "allowed_cloud_routes"):
        if type(configuration[key]) is not int or configuration[key] < 0:
            raise ContractValidationError("invalid cloud readiness route count")
    if (configuration["allowed_cloud_routes"] > configuration["cloud_routes"]
            or configuration["codex_allowed"] is not True and configuration["allowed_cloud_routes"] != 0
            or (status == "available") != (mode != "unknown")
            or status != "available" and (configuration["codex_allowed"] is not None or configuration["cloud_routes"] != 0)):
        raise ContractValidationError("inconsistent cloud readiness configuration")
    paths = value["paths"]
    if type(paths) is not list or len(paths) != len(PATHS):
        raise ContractValidationError("invalid cloud readiness paths")
    for path, (identifier, (billing, documentation)) in zip(paths, PATHS.items()):
        _exact_keys(path, {"id", "billing", "documentation", "status", "blockers"})
        if (path["id"], path["billing"], path["documentation"]) != (identifier, billing, documentation):
            raise ContractValidationError("invalid cloud authorization path")
        path_status = _value_string(path, "status")
        if path_status not in ({"blocked", "unverified"} if identifier == "api_key" else {"not_implemented"}):
            raise ContractValidationError("cloud readiness cannot grant authorization")
        blockers = path["blockers"]
        if type(blockers) is not list or not blockers or len(blockers) > len(ACTIONS):
            raise ContractValidationError("invalid cloud readiness blockers")
        seen = set()
        for blocker in blockers:
            _exact_keys(blocker, {"code", "action"})
            code = _value_string(blocker, "code")
            if code not in ACTIONS or blocker["action"] != ACTIONS[code] or code in seen:
                raise ContractValidationError("invalid cloud readiness action")
            seen.add(code)
        required = {"provider_unqualified"}
        if mode == "local_only":
            required.add("local_only_forbids_cloud")
        if status != "available":
            required.add("configuration_" + status)
        elif configuration["codex_allowed"] is None:
            required.add("actor_unavailable")
        elif configuration["codex_allowed"] is False:
            required.add("client_not_allowed")
        if surface == "desktop":
            required.add("desktop_unqualified")
        if identifier == "api_key":
            required.add("credentials_unverified")
            if surface == "cli" and version != "pinned":
                required.add("client_unavailable" if version == "unavailable" else "client_version_unsupported")
            if status == "available" and configuration["codex_allowed"] is not None:
                if configuration["cloud_routes"] == 0:
                    required.add("cloud_route_missing")
                elif configuration["codex_allowed"] and configuration["allowed_cloud_routes"] == 0:
                    required.add("cloud_models_not_allowed")
            if (path_status == "unverified") != (seen == {"credentials_unverified", "provider_unqualified"}):
                raise ContractValidationError("inconsistent API readiness blockers")
            if path_status == "unverified" and (surface != "cli" or version != "pinned"
                    or status != "available" or configuration["codex_allowed"] is not True
                    or mode == "local_only" or configuration["allowed_cloud_routes"] == 0):
                raise ContractValidationError("API declaration is blocked")
        else:
            required.update({"account_consent_eligibility_unverified"})
            required.update({"native_gateway_not_implemented", "managed_workspace_setup_required"}
                            if identifier == "native_codex_gateway" else
                            {"app_authorization_not_implemented", "registration_and_host_id_required"})
            if seen.intersection({"credentials_unverified", "cloud_route_missing", "cloud_models_not_allowed"}):
                raise ContractValidationError("plan authorization cannot require paid API setup")
        managed = seen.intersection({"managed_configuration", "managed_configuration_unknown"})
        optional = managed if identifier == "api_key" else set()
        if len(optional) > 1 or seen - optional != required:
            raise ContractValidationError("inconsistent cloud readiness blockers")
    effects = _value_mapping(value, "effects")
    _exact_keys(effects, {"settings_changed", "credentials_read", "provider_requests"})
    if (effects["settings_changed"] is not False or effects["credentials_read"] is not False
            or type(effects["provider_requests"]) is not int or effects["provider_requests"] != 0):
        raise ContractValidationError("cloud preflight must remain read-only")
