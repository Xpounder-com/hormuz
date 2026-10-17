"""Strict public contracts for the persistent coding-client lifecycle."""
from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any
from ..client_versions import CODEX_VERSION_PATTERN

from .common import (
    ContractValidationError, _exact_keys, _value_mapping, _value_string, _value_string_list,
)
from .constants import CONNECTED_CLIENT_ERROR_ACTIONS


def _validate_status(value: Mapping[str, Any]) -> None:
    _exact_keys(value, {"schema_id", "schema_version", "phase", "router", "scope",
                        "configuration", "retained_settings"},
                {"generation", "client_version", "owned_settings"})
    if _value_string(value, "scope") != "codex_user_defaults":
        raise ContractValidationError("unsupported connected-client scope")
    phase = _value_string(value, "phase")
    if phase not in {"prepared", "connected", "disconnecting", "restore_required", "disconnected"}:
        raise ContractValidationError("unsupported connected-client phase")
    router = _value_string(value, "router")
    if router not in {"ready", "stopping", "unavailable", "stopped"}:
        raise ContractValidationError("unsupported connected-client router state")
    configuration = _value_string(value, "configuration")
    if configuration not in {"installed", "changed", "unavailable", "not_owned"}:
        raise ContractValidationError("unsupported connected-client configuration state")
    retained = _value_string_list(value, "retained_settings")
    owner_fields = {"generation", "client_version", "owned_settings"}
    if owner_fields.intersection(value):
        if not owner_fields.issubset(value):
            raise ContractValidationError("incomplete connected-client owner")
        generation = _value_string(value, "generation")
        if re.fullmatch(r"[a-f0-9]{32}", generation) is None:
            raise ContractValidationError("invalid connected-client generation")
        if re.fullmatch(CODEX_VERSION_PATTERN, _value_string(value, "client_version")) is None:
            raise ContractValidationError("invalid connected-client version")
        owned = _value_string_list(value, "owned_settings")
        fields = {"model", "model_provider", "web_search"}
        if len(owned) != 3 or set(owned) != fields:
            raise ContractValidationError("invalid connected-client owned settings")
        if len(retained) != len(set(retained)) or set(retained) - fields - {"model_providers.hormuz_" + generation}:
            raise ContractValidationError("invalid connected-client retained settings")
    elif phase != "disconnected" or retained:
        raise ContractValidationError("connected-client status requires an owner")
    if (phase in {"disconnected", "restore_required"}) != (router == "stopped"):
        raise ContractValidationError("invalid connected-client router ownership")
    if (phase == "disconnected") != (configuration == "not_owned"):
        raise ContractValidationError("invalid connected-client configuration ownership")


def _validate_preview(value: Mapping[str, Any]) -> None:
    _exact_keys(value, {"schema_id", "schema_version", "phase", "router", "scope",
                        "client_version", "settings", "changes"})
    if _value_string(value, "scope") != "codex_user_defaults" or _value_string(value, "phase") != "preview":
        raise ContractValidationError("invalid connected-client preview scope or phase")
    if re.fullmatch(CODEX_VERSION_PATTERN, _value_string(value, "client_version")) is None:
        raise ContractValidationError("invalid connected-client version")
    settings = _value_mapping(value, "settings")
    _exact_keys(settings, {"model", "model_provider", "web_search"})
    if (len(_value_string(settings, "model")) > 2048
            or re.fullmatch(r"hormuz_[a-f0-9]{32}", _value_string(settings, "model_provider")) is None
            or _value_string(settings, "web_search") != "disabled"):
        raise ContractValidationError("invalid connected-client proposed settings")
    changes = _value_mapping(value, "changes")
    router = _value_string(value, "router")
    if router == "ready":
        _exact_keys(changes, set())
    elif router == "not_started":
        _exact_keys(changes, {"model", "model_provider", "web_search", "provider_table"})
        if _value_string(changes, "provider_table") != "add" or any(
                _value_string(changes, key) not in {"add", "replace"} for key in settings):
            raise ContractValidationError("invalid connected-client proposed changes")
    else:
        raise ContractValidationError("invalid connected-client preview router state")


def _validate_error(value: Mapping[str, Any]) -> None:
    _exact_keys(value, {"schema_id", "schema_version", "error"})
    error = _value_mapping(value, "error")
    _exact_keys(error, {"code", "action"})
    code = _value_string(error, "code")
    if code not in CONNECTED_CLIENT_ERROR_ACTIONS or _value_string(error, "action") != CONNECTED_CLIENT_ERROR_ACTIONS[code]:
        raise ContractValidationError("invalid connected-client error or recovery action")


def _validate_status_v2(value: Mapping[str, Any]) -> None:
    runtime = _value_mapping(value, "local_runtime")
    _exact_keys(runtime, {"ownership", "state"}, path="local_runtime")
    if _value_string(runtime, "ownership") != "owned":
        raise ContractValidationError("invalid connected-client runtime ownership")
    state = _value_string(runtime, "state")
    if state not in {"ready", "stopping", "unavailable", "stopped"} or state != value.get("router"):
        raise ContractValidationError("invalid connected-client runtime state")
    _validate_status({key: item for key, item in value.items() if key != "local_runtime"})


def _validate_preview_v2(value: Mapping[str, Any]) -> None:
    runtime = _value_mapping(value, "local_runtime")
    _exact_keys(runtime, {"ownership", "state"}, path="local_runtime")
    pair = (_value_string(runtime, "ownership"), _value_string(runtime, "state"))
    if pair not in {("planned", "not_started"), ("owned", "ready")} or pair[1] != value.get("router"):
        raise ContractValidationError("invalid connected-client proposed runtime")
    _validate_preview({key: item for key, item in value.items() if key != "local_runtime"})
