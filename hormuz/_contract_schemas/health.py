"""Gateway health, readiness, identity, and error validators."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .common import (
    ContractValidationError,
    _exact_keys,
    _nullable_string,
    _sha256_digest,
    _validate_identity_values,
    _value_mapping,
    _value_integer,
    _value_string,
    _value_string_list,
)


def _validate_health(value: Mapping[str, Any]) -> None:
    _exact_keys(value, {"schema_id", "schema_version", "status", "service", "protocols"})
    _value_string(value, "status")
    _value_string(value, "service")
    _value_string_list(value, "protocols")


def _validate_readiness(value: Mapping[str, Any]) -> None:
    _exact_keys(value, {"schema_id", "schema_version", "status", "service", "reason"})
    status = _value_string(value, "status")
    _value_string(value, "service")
    reason = _nullable_string(value, "reason")
    if status == "ready" and reason is None:
        return
    if status == "not_ready" and reason in {"dependency_unavailable", "draining"}:
        return
    raise ContractValidationError("readiness status and reason are invalid")


def _validate_identity(value: Mapping[str, Any]) -> None:
    _exact_keys(
        value,
        {
            "schema_id",
            "schema_version",
            "actor_id",
            "actor_name",
            "team_id",
            "team_name",
            "organization_id",
            "identity_type",
            "allowed_clients",
            "authentication_source",
        },
    )
    _validate_identity_values(value)
    _value_string_list(value, "allowed_clients")


def _validate_error(value: Mapping[str, Any], allowed_codes: frozenset[str]) -> None:
    _exact_keys(value, {"schema_id", "schema_version", "error"})
    error = _value_mapping(value, "error")
    _exact_keys(error, {"code", "message"}, path="error")
    code = _value_string(error, "code", path="error")
    if code not in allowed_codes:
        raise ContractValidationError(f"unsupported public error code: {code}")
    _value_string(error, "message", path="error")


def _validate_route_admission(value: Mapping[str, Any]) -> None:
    _exact_keys(value, {"schema_id", "schema_version", "mode", "task_classifier", "local_capacity", "routes"})
    if _value_string(value, "mode") not in {"configured", "auto", "local_only"} or _value_string(value, "task_classifier") != "bounded_local_heuristic":
        raise ContractValidationError("route admission mode or classifier is invalid")
    capacity = _value_mapping(value, "local_capacity")
    _exact_keys(capacity, {"limit", "active"})
    if _value_integer(capacity, "limit") != 1 or _value_integer(capacity, "active") not in (0, 1):
        raise ContractValidationError("local capacity is invalid")
    routes = value.get("routes")
    if type(routes) is not list or len(routes) > 8:
        raise ContractValidationError("local qualification routes are invalid")
    for route in routes:
        if not isinstance(route, Mapping):
            raise ContractValidationError("local qualification route is invalid")
        fields = {"model", "destination", "generation", "model_revision", "qualification_id", "evidence_basis", "capabilities", "max_request_bytes", "max_output_tokens", "task_types", "readiness"}
        if value["schema_version"] == 2:
            fields |= {"coding_readiness", "coding_evidence", "automatic_task_types"}
        _exact_keys(route, fields)
        for field in ("capabilities", "task_types"):
            _value_string_list(route, field)
        if value["schema_version"] == 2:
            coding_readiness = _value_string(route, "coding_readiness")
            if coding_readiness not in {"unknown", "incomplete", "failed", "subject_mismatch", "qualified"}:
                raise ContractValidationError("invalid local coding readiness")
            evidence = route["coding_evidence"]
            if evidence is not None:
                if not isinstance(evidence, Mapping):
                    raise ContractValidationError("invalid local coding evidence")
                _exact_keys(evidence, {"evidence_id", "plan_sha256", "receipt_sha256", "basis", "criteria_version", "planned", "evaluated", "passed"})
                _value_string(evidence, "evidence_id")
                for field in ("plan_sha256", "receipt_sha256"):
                    _sha256_digest(_value_string(evidence, field), f"coding_evidence.{field}")
                if (evidence["basis"] != "operator_attested_outcomes" or evidence["criteria_version"] != "local-repair.v1"
                        or not 0 <= _value_integer(evidence, "passed") <= _value_integer(evidence, "evaluated") <= _value_integer(evidence, "planned") <= 64):
                    raise ContractValidationError("invalid local coding evidence")
            if (coding_readiness == "unknown") != (evidence is None):
                raise ContractValidationError("invalid local coding evidence readiness")
            if coding_readiness == "qualified" and (evidence["planned"] < 16
                    or not evidence["passed"] == evidence["evaluated"] == evidence["planned"]):
                raise ContractValidationError("invalid qualified coding outcomes")
            automatic = _value_string_list(route, "automatic_task_types")
            if any(kind not in route["task_types"] or kind == "code_review" or kind == "code_change" and coding_readiness != "qualified" for kind in automatic):
                raise ContractValidationError("invalid automatic task types")
        for field in ("model", "destination", "generation", "model_revision", "qualification_id"):
            _value_string(route, field)
        for field in ("max_request_bytes", "max_output_tokens"):
            if _value_integer(route, field) <= 0:
                raise ContractValidationError("local qualification bound is invalid")
        if _value_string(route, "evidence_basis") != "operator_declared" or _value_string(route, "readiness") not in {"unchecked", "stale", "ready", "unavailable"}:
            raise ContractValidationError("local qualification evidence or readiness is invalid")
