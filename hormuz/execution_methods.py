"""Bounded cycle-5 execution methods for explicit adapter yield points."""

from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from .compaction_formats import canonical_json, strict_json_loads, validate_tree


MAX_EXECUTION_INPUT_BYTES = 64 * 1024
MAX_OPTIONS = 32
JEV_ENDPOINT = "https://api.typesafe.ai/v1/systemone"


class ExecutionMethodError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class _RejectRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Never forward a credential-bearing Jev request to another URL."""

    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


@dataclass(frozen=True)
class MethodRequest:
    step_id: str
    tool_call_id: str
    operation: str
    payload: object
    permission: Literal["read", "write", "network"]
    side_effecting: bool
    streaming_required: bool
    expected_model_input_tokens: int | None = None


@dataclass(frozen=True)
class RoutedExecution:
    step_id: str
    tool_call_id: str
    method: Literal["deterministic", "jev", "model"]
    permission: Literal["read", "write", "network"]
    side_effecting: bool
    streaming_required: bool
    result: object | None
    reason: str
    model_step_avoided: bool
    usage: Mapping[str, int | None]
    latency_ms: float | None


@dataclass(frozen=True)
class JevQualification:
    fixture_passed: bool
    minimum_probability: float
    minimum_net_tokens: int
    maximum_latency_ms: float

    def __post_init__(self) -> None:
        if (
            not isinstance(self.fixture_passed, bool)
            or isinstance(self.minimum_probability, bool)
            or not isinstance(self.minimum_probability, (int, float))
            or not math.isfinite(self.minimum_probability)
            or not 0 <= self.minimum_probability <= 1
            or type(self.minimum_net_tokens) is not int
            or self.minimum_net_tokens < 0
            or isinstance(self.maximum_latency_ms, bool)
            or not isinstance(self.maximum_latency_ms, (int, float))
            or not math.isfinite(self.maximum_latency_ms)
            or self.maximum_latency_ms <= 0
        ):
            raise ExecutionMethodError("invalid_jev_qualification")


class DeterministicExecutor:
    """Conventional code for fully defined read-only JSON operations."""

    operations = frozenset({"json_count", "json_get", "json_validate_keys"})

    def execute(self, request: MethodRequest) -> RoutedExecution:
        if request.operation not in self.operations:
            raise ExecutionMethodError("unsupported_exact_operation")
        if request.permission != "read" or request.side_effecting:
            raise ExecutionMethodError("exact_operation_permission_rejected")
        _validate_request(request)
        started = time.perf_counter()
        if request.operation == "json_count":
            if not isinstance(request.payload, (list, dict)):
                raise ExecutionMethodError("json_collection_required")
            result: object = len(request.payload)
        elif request.operation == "json_get":
            result = _json_get(request.payload)
        else:
            result = _json_validate_keys(request.payload)
        return RoutedExecution(
            step_id=request.step_id,
            tool_call_id=request.tool_call_id,
            method="deterministic",
            permission=request.permission,
            side_effecting=request.side_effecting,
            streaming_required=request.streaming_required,
            result=result,
            reason="exact_contract_matched",
            model_step_avoided=True,
            usage={"input_tokens": 0, "output_tokens": 0},
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
        )


class JevChoiceAdapter:
    """Optional TypeSafe Jev choice call for one supplied-handler decision."""

    def __init__(
        self,
        *,
        credential: str,
        qualification: JevQualification,
        developer_selected_provider: bool,
        transport: Callable[[str, Mapping[str, str], bytes, float], bytes] | None = None,
        timeout_seconds: float = 10,
    ):
        if not developer_selected_provider:
            raise ExecutionMethodError("jev_provider_not_selected")
        if (
            not isinstance(credential, str)
            or not 8 <= len(credential.encode("utf-8")) <= 4096
            or credential != credential.strip()
            or any(character in credential for character in ("\r", "\n", "\x00"))
        ):
            raise ExecutionMethodError("jev_credential_invalid")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 60
        ):
            raise ExecutionMethodError("jev_timeout_invalid")
        self._credential = credential
        self.qualification = qualification
        self.transport = transport or _http_transport
        self.timeout_seconds = timeout_seconds

    def choose(
        self,
        request: MethodRequest,
        *,
        options: Mapping[str, str | None],
        instructions: str,
    ) -> RoutedExecution:
        _validate_request(request)
        if (
            request.operation != "handler_select"
            or request.permission != "read"
            or request.side_effecting
            or not 2 <= len(options) <= MAX_OPTIONS
            or any(not _safe_identifier(key) for key in options)
            or any(
                value is not None
                and (
                    not isinstance(value, str)
                    or len(value.encode("utf-8")) > 4096
                )
                for value in options.values()
            )
            or not isinstance(instructions, str)
            or not 1 <= len(instructions.encode("utf-8")) <= 4096
        ):
            return _model_fallback(request, "jev_contract_not_applicable")
        if not self.qualification.fixture_passed:
            return _model_fallback(request, "jev_fixture_qualification_failed")
        document = {
            "state": request.payload,
            "model": "jev-latest",
            "questions": {
                "handler": {
                    "type": "choice",
                    "instructions": instructions,
                    "criteria": dict(options),
                }
            },
        }
        try:
            body = canonical_json(document).encode("utf-8")
        except (TypeError, ValueError, RecursionError) as error:
            raise ExecutionMethodError("jev_state_invalid") from error
        if len(body) > MAX_EXECUTION_INPUT_BYTES:
            return _model_fallback(request, "jev_state_too_large")
        started = time.perf_counter()
        try:
            raw = self.transport(
                JEV_ENDPOINT,
                {
                    "Authorization": "Bearer " + self._credential,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                body,
                self.timeout_seconds,
            )
            latency_ms = (time.perf_counter() - started) * 1000
            response = strict_json_loads(raw.decode("utf-8"))
            choice, probability, usage = _parse_jev_choice(response, options)
        except (ExecutionMethodError, UnicodeDecodeError, ValueError, OSError):
            return _model_fallback(request, "jev_unavailable")
        expected = request.expected_model_input_tokens
        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        net_tokens = (
            None
            if expected is None or input_tokens is None or output_tokens is None
            else expected - input_tokens - output_tokens
        )
        if (
            latency_ms > self.qualification.maximum_latency_ms
            or probability < self.qualification.minimum_probability
            or net_tokens is None
            or net_tokens < self.qualification.minimum_net_tokens
        ):
            return _model_fallback(
                request,
                "jev_net_benefit_unqualified",
                usage=usage,
                latency_ms=latency_ms,
            )
        return RoutedExecution(
            step_id=request.step_id,
            tool_call_id=request.tool_call_id,
            method="jev",
            permission=request.permission,
            side_effecting=request.side_effecting,
            streaming_required=request.streaming_required,
            result={
                "choice": choice,
                "probability": probability,
                "state_disclosure": _state_disclosure(request.payload),
            },
            reason="jev_fixture_and_net_benefit_qualified",
            model_step_avoided=True,
            usage=usage,
            latency_ms=round(latency_ms, 3),
        )


class ExecutionRouter:
    """Select exact code, optional Jev, or the unchanged model path."""

    def __init__(
        self,
        *,
        deterministic: DeterministicExecutor | None = None,
        jev: JevChoiceAdapter | None = None,
    ):
        self.deterministic = deterministic or DeterministicExecutor()
        self.jev = jev

    def route(
        self,
        request: MethodRequest,
        *,
        options: Mapping[str, str | None] | None = None,
        instructions: str | None = None,
    ) -> RoutedExecution:
        if request.operation in self.deterministic.operations:
            try:
                return self.deterministic.execute(request)
            except ExecutionMethodError:
                return _model_fallback(request, "exact_contract_rejected")
        if (
            request.operation == "handler_select"
            and self.jev is not None
            and options is not None
            and instructions is not None
        ):
            return self.jev.choose(request, options=options, instructions=instructions)
        return _model_fallback(request, "ordinary_model_path")


def _json_get(payload: object) -> object:
    if not isinstance(payload, dict) or set(payload) != {"document", "path"}:
        raise ExecutionMethodError("json_get_contract_invalid")
    path = payload["path"]
    if not isinstance(path, list) or not 1 <= len(path) <= 32:
        raise ExecutionMethodError("json_get_contract_invalid")
    value = payload["document"]
    for component in path:
        if isinstance(value, dict) and isinstance(component, str) and component in value:
            value = value[component]
        elif (
            isinstance(value, list)
            and type(component) is int
            and 0 <= component < len(value)
        ):
            value = value[component]
        else:
            raise ExecutionMethodError("json_path_not_found")
    return value


def _json_validate_keys(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict) or set(payload) != {"document", "required_keys"}:
        raise ExecutionMethodError("json_validate_contract_invalid")
    document = payload["document"]
    required = payload["required_keys"]
    if (
        not isinstance(document, dict)
        or not isinstance(required, list)
        or not 1 <= len(required) <= 128
        or any(not isinstance(key, str) or not key for key in required)
        or len(set(required)) != len(required)
    ):
        raise ExecutionMethodError("json_validate_contract_invalid")
    missing = [key for key in required if key not in document]
    return {"valid": not missing, "missing": missing}


def _validate_request(request: MethodRequest) -> None:
    if (
        not _safe_identifier(request.step_id)
        or not _safe_identifier(request.tool_call_id)
        or not _safe_identifier(request.operation)
        or request.permission not in {"read", "write", "network"}
        or not isinstance(request.side_effecting, bool)
        or not isinstance(request.streaming_required, bool)
        or request.expected_model_input_tokens is not None
        and (
            type(request.expected_model_input_tokens) is not int
            or request.expected_model_input_tokens < 0
        )
    ):
        raise ExecutionMethodError("invalid_execution_request")
    try:
        validate_tree(request.payload)
        size = len(canonical_json(request.payload).encode("utf-8"))
    except (TypeError, ValueError, RecursionError) as error:
        raise ExecutionMethodError("invalid_execution_request") from error
    if size > MAX_EXECUTION_INPUT_BYTES:
        raise ExecutionMethodError("execution_input_too_large")


def _parse_jev_choice(
    value: object, options: Mapping[str, object]
) -> tuple[str, float, dict[str, int | None]]:
    if not isinstance(value, dict) or not isinstance(value.get("answers"), dict):
        raise ExecutionMethodError("jev_response_invalid")
    answer = value["answers"].get("handler")
    usage_value = value.get("usage")
    if (
        not isinstance(answer, dict)
        or answer.get("type") != "choice"
        or answer.get("choice") not in options
        or not isinstance(answer.get("probabilities"), dict)
    ):
        raise ExecutionMethodError("jev_response_invalid")
    choice = answer["choice"]
    probability = answer["probabilities"].get(choice)
    if (
        isinstance(probability, bool)
        or not isinstance(probability, (int, float))
        or not math.isfinite(probability)
        or not 0 <= probability <= 1
    ):
        raise ExecutionMethodError("jev_response_invalid")
    usage = {
        "input_tokens": None,
        "output_tokens": None,
        "actual_cost_microusd": None,
    }
    if isinstance(usage_value, dict):
        for name in ("input_tokens", "output_tokens"):
            observed = usage_value.get(name)
            usage[name] = observed if type(observed) is int and observed >= 0 else None
    explicit_cost = value.get("actual_cost_microusd")
    if type(explicit_cost) is int and explicit_cost >= 0:
        usage["actual_cost_microusd"] = explicit_cost
    return choice, float(probability), usage


def _state_disclosure(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return {"kind": "object", "top_level_fields": sorted(str(key) for key in value)}
    if isinstance(value, list):
        return {"kind": "array", "items": len(value)}
    return {"kind": "scalar", "type": type(value).__name__}


def _model_fallback(
    request: MethodRequest,
    reason: str,
    *,
    usage: Mapping[str, int | None] | None = None,
    latency_ms: float | None = None,
) -> RoutedExecution:
    return RoutedExecution(
        step_id=request.step_id,
        tool_call_id=request.tool_call_id,
        method="model",
        permission=request.permission,
        side_effecting=request.side_effecting,
        streaming_required=request.streaming_required,
        result=None,
        reason=reason,
        model_step_avoided=False,
        usage=usage
        or {
            "input_tokens": None,
            "output_tokens": None,
            "actual_cost_microusd": None,
        },
        latency_ms=None if latency_ms is None else round(latency_ms, 3),
    )


def _safe_identifier(value: object) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 128
        and all(character.isalnum() or character in {"-", "_", "."} for character in value)
    )


def _http_transport(
    endpoint: str, headers: Mapping[str, str], body: bytes, timeout: float
) -> bytes:
    if endpoint != JEV_ENDPOINT:
        raise ExecutionMethodError("jev_endpoint_invalid")
    request = urllib.request.Request(
        endpoint, data=body, method="POST", headers=dict(headers)
    )
    opener = urllib.request.build_opener(_RejectRedirectHandler())
    try:
        with opener.open(request, timeout=timeout) as response:
            if response.status != 200:
                raise ExecutionMethodError("jev_unavailable")
            value = response.read(MAX_EXECUTION_INPUT_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise ExecutionMethodError("jev_unavailable") from error
    if len(value) > MAX_EXECUTION_INPUT_BYTES:
        raise ExecutionMethodError("jev_response_too_large")
    return value


__all__ = [
    "DeterministicExecutor",
    "ExecutionMethodError",
    "ExecutionRouter",
    "JEV_ENDPOINT",
    "JevChoiceAdapter",
    "JevQualification",
    "MethodRequest",
    "RoutedExecution",
]
