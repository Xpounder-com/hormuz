"""Local asset declarations and shared setup receipts; no secret values."""
import re

from .common import ContractValidationError, _exact_keys, _value_mapping, _value_string


PROFILE_FIELDS = {"schema_id", "schema_version", "qualification_id", "client_version", "system", "machine",
                  "runtime_sha256", "model_sha256", "model", "capabilities", "max_request_bytes",
                  "max_output_tokens", "task_types", "upstream_timeout_seconds", "startup_timeout_seconds"}


def _tag(value, key):
    text = _value_string(value, key)
    if not 1 <= len(text) <= 128 or any(ord(c) < 33 or ord(c) > 126 for c in text):
        raise ContractValidationError("invalid local setup identifier")


def validate_profile(value):
    _exact_keys(value, PROFILE_FIELDS)
    for key in ("qualification_id", "client_version", "system", "machine", "model"):
        _tag(value, key)
    for key in ("runtime_sha256", "model_sha256"):
        if re.fullmatch(r"[a-f0-9]{64}", _value_string(value, key)) is None:
            raise ContractValidationError("invalid local setup digest")
    for key, maximum in (("max_request_bytes", 65536), ("max_output_tokens", 512),
                         ("upstream_timeout_seconds", 90), ("startup_timeout_seconds", 60)):
        if type(value[key]) is not int or not 1 <= value[key] <= maximum:
            raise ContractValidationError("invalid local setup limit")
    choices = {
        "capabilities": {"responses", "text", "streaming", "tools", "codex_http", "structured_output"},
        "task_types": {"summarization", "translation", "structured_extraction", "code_change", "code_review"},
    }
    for key, allowed in choices.items():
        items = value[key]
        if (type(items) is not list or not items or len(items) > len(allowed)
                or any(type(item) is not str or item not in allowed for item in items)
                or len(set(items)) != len(items)):
            raise ContractValidationError("invalid local setup capabilities")
    if not {"responses", "text"} <= set(value["capabilities"]):
        raise ContractValidationError("local setup requires text Responses")


def validate_setup(value):
    from ..contracts import validate_contract
    _exact_keys(value, {"schema_id", "schema_version", "configuration", "connection"})
    configuration = _value_mapping(value, "configuration")
    _exact_keys(configuration, {"operation", "mode", "qualification_id", "credential_env", "model", "listen_port", "runtime_port"})
    if configuration["operation"] not in {"create", "reuse"} or configuration["mode"] != "local_only":
        raise ContractValidationError("invalid local setup operation")
    for key in ("qualification_id", "model"):
        _tag(configuration, key)
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", _value_string(configuration, "credential_env")) is None:
        raise ContractValidationError("invalid local setup credential environment")
    for key in ("listen_port", "runtime_port"):
        if type(configuration[key]) is not int or not 1 <= configuration[key] <= 65535:
            raise ContractValidationError("invalid local setup port")
    if configuration["listen_port"] == configuration["runtime_port"]:
        raise ContractValidationError("local setup ports must differ")
    connection = _value_mapping(value, "connection")
    if (connection.get("schema_id") not in {"hormuz.connected-client-preview", "hormuz.connected-client-status"}
            or connection.get("schema_version") != 2):
        raise ContractValidationError("invalid local setup connection")
    validate_contract(connection)


def validate_setup_v2(value):
    configuration = _value_mapping(value, "configuration")
    _exact_keys(configuration, {"operation", "mode", "qualification_id", "authentication", "model", "listen_port", "runtime_port"})
    if configuration["authentication"] != "managed_local":
        raise ContractValidationError("invalid managed local authentication")
    legacy = {key: item for key, item in configuration.items() if key != "authentication"}
    validate_setup({**value, "configuration": {**legacy, "credential_env": "MANAGED_LOCAL"}})


def validate_setup_v3(value):
    configuration = dict(_value_mapping(value, "configuration"))
    if configuration.pop("shadow_router", None) != "shadow":
        raise ContractValidationError("invalid shadow routing setup")
    validator = validate_setup_v2 if "authentication" in configuration else validate_setup
    validator({**value, "configuration": configuration})
