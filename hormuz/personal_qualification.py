"""Provider-free qualification for personal optimizer contracts and experiments."""

from __future__ import annotations

import hashlib
import json
from importlib.resources import files

from .adapters import adapter_for, conformance_report
from .compaction import optimize_request
from .compaction_formats import restore_text, strict_json_loads
from .compaction_protocols import derive_selections
from .execution_methods import ExecutionRouter, JevChoiceAdapter, JevQualification, MethodRequest
from .personal_optimization import (
    BehaviorEvent,
    BehaviorTracker,
    BoundedReadRetry,
    ConversationTurn,
    RecoveryBuffer,
    TypedToolHistoryCompactor,
)
from .personal_profiles import PERSONAL_RELEASE_VERSION


MAX_QUALIFICATION_FIXTURE_BYTES = 512 * 1024


class PersonalQualificationError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def run_product_qualification() -> dict[str, object]:
    raw = files("hormuz").joinpath("personal-qualification-v1.json").read_bytes()
    if len(raw) > MAX_QUALIFICATION_FIXTURE_BYTES:
        raise PersonalQualificationError("personal_qualification_fixture_invalid")
    try:
        fixture = strict_json_loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise PersonalQualificationError("personal_qualification_fixture_invalid") from error
    if (
        not isinstance(fixture, dict)
        or fixture.get("schema_id") != "hormuz.personal-qualification-fixture"
        or fixture.get("schema_version") != 1
    ):
        raise PersonalQualificationError("personal_qualification_fixture_invalid")

    checks: list[dict[str, object]] = []
    for key in _required_list(fixture, "adapters"):
        if not isinstance(key, str):
            raise PersonalQualificationError("personal_qualification_fixture_invalid")
        report = conformance_report(adapter_for(key))
        checks.append(
            {
                "name": f"adapter:{key}",
                "scope": "adapter_contract",
                "passed": report["passed"] is True,
            }
        )

    checks.append(
        {
            "name": "structural_compaction",
            "scope": "release_contract",
            "passed": _qualify_structural_compaction(fixture),
        }
    )
    checks.append(
        {
            "name": "semantic_history",
            "scope": "experimental_contract",
            "passed": _qualify_semantic(fixture),
        }
    )
    checks.extend(_qualify_execution(fixture))
    checks.extend(_qualify_jev(fixture))
    checks.append(
        {
            "name": "behavior_and_retry",
            "scope": "experimental_contract",
            "passed": _qualify_behavior(fixture),
        }
    )
    passed = all(check["passed"] is True for check in checks)
    return {
        "schema_id": "hormuz.personal-product-qualification",
        "schema_version": 1,
        "release_version": PERSONAL_RELEASE_VERSION,
        "qualification_kind": "provider_free_contract",
        "fixture_sha256": hashlib.sha256(raw).hexdigest(),
        "provider_calls": 0,
        "passed": passed,
        "checks": checks,
        "experiment_status": {
            "cycles_3_and_4": "reference_implementation_only",
            "cycle_5": "no_builtin_adapter_execution_yield",
            "live_jev": "not_run",
        },
        "nonclaims": [
            "live_provider_availability",
            "live_model_interpretation",
            "fresh_machine_agent_acceptance",
            "integrated_cycle_3_or_4_intervention",
            "integrated_model_step_replacement",
            "live_jev_qualification",
            "commercial_retention_or_willingness_to_pay",
        ],
    }


def _qualify_structural_compaction(fixture: dict[str, object]) -> bool:
    value = _required_dict(fixture, "structural_compaction")
    client = _required_string(value, "client")
    protocol = _required_string(value, "protocol")
    tool_name = _required_string(value, "tool_name")
    command = _required_string(value, "command")
    prefix = _required_string(value, "path_prefix")
    suffix = _required_string(value, "path_suffix")
    count = _required_int(value, "path_count")
    expected_format = _required_string(value, "expected_format")
    if protocol != "responses" or not 2 <= count <= 256:
        raise PersonalQualificationError("personal_qualification_fixture_invalid")
    output = "".join(f"{prefix}{index:03}{suffix}\n" for index in range(count))
    payload = {
        "model": "provider-free-fixture",
        "input": [
            {
                "type": "function_call",
                "call_id": "qualification-call",
                "name": tool_name,
                "arguments": json.dumps({"cmd": command}, separators=(",", ":")),
            },
            {
                "type": "function_call_output",
                "call_id": "qualification-call",
                "output": output,
            },
        ],
    }
    selections = derive_selections(payload, "responses", client=client)
    result = optimize_request(
        payload,
        "responses",
        selections,
        {"cl100k_base": len, "o200k_base": len},
        enabled=True,
    )
    changed_input = result.payload.get("input")
    if (
        not isinstance(changed_input, list)
        or len(changed_input) != 2
        or not isinstance(changed_input[1], dict)
    ):
        return False
    changed_output = changed_input[1].get("output")
    if not isinstance(changed_output, str):
        return False
    try:
        envelope = strict_json_loads(changed_output)
    except ValueError:
        return False
    return (
        len(selections) == 1
        and result.changed
        and result.reason == "compacted"
        and result.changed_blocks == 1
        and result.before_bytes is not None
        and result.after_bytes is not None
        and result.after_bytes < result.before_bytes
        and isinstance(envelope, dict)
        and envelope.get("format") == expected_format
        and restore_text(changed_output) == output
    )


def _qualify_semantic(fixture: dict[str, object]) -> bool:
    value = _required_dict(fixture, "semantic_history")
    turns = tuple(_conversation_turn(item) for item in _required_list(value, "turns"))
    recent_turns = value.get("recent_turns")
    if type(recent_turns) is not int:
        raise PersonalQualificationError("personal_qualification_fixture_invalid")
    recovery = RecoveryBuffer()
    result = TypedToolHistoryCompactor(recovery, recent_turns=recent_turns).compact(turns)
    expected = _required_dict(value, "expected")
    constraint = expected.get("constraint")
    question = expected.get("unresolved_question")
    tool_name = expected.get("tool_name")
    tool_state = expected.get("tool_state")
    if not all(isinstance(item, str) for item in (constraint, question, tool_name, tool_state)):
        raise PersonalQualificationError("personal_qualification_fixture_invalid")
    preserved_constraint = any(constraint in turn.constraints for turn in result.turns)
    preserved_question = any(
        turn.unresolved_question and turn.content == question for turn in result.turns
    )
    preserved_tool = any(
        turn.tool_name == tool_name and turn.tool_state == tool_state for turn in result.turns
    )
    recovered = (
        result.recovery_key is not None
        and recovery.recover(result.recovery_key) == turns
    )
    return all(
        (
            result.after_bytes < result.before_bytes,
            preserved_constraint,
            preserved_question,
            preserved_tool,
            recovered,
            result.auxiliary_calls == 0,
        )
    )


def _qualify_execution(fixture: dict[str, object]) -> list[dict[str, object]]:
    checks: list[dict[str, object]] = []
    router = ExecutionRouter()
    for item in _required_list(fixture, "execution_cases"):
        case = _as_dict(item)
        name = _required_string(case, "name")
        request = _method_request(_required_dict(case, "request"))
        result = router.route(request)
        passed = (
            result.method == case.get("expected_method")
            and result.result == case.get("expected_result")
            and result.model_step_avoided == case.get("model_step_avoided")
            and result.step_id == request.step_id
            and result.tool_call_id == request.tool_call_id
            and result.permission == request.permission
            and result.side_effecting == request.side_effecting
            and result.streaming_required == request.streaming_required
        )
        checks.append(
            {
                "name": f"execution:{name}",
                "scope": "experimental_contract",
                "passed": passed,
            }
        )
    return checks


def _qualify_jev(fixture: dict[str, object]) -> list[dict[str, object]]:
    checks: list[dict[str, object]] = []
    for item in _required_list(fixture, "jev_cases"):
        case = _as_dict(item)
        name = _required_string(case, "name")
        response = _required_dict(case, "response")

        def transport(_url, _headers, _body, _timeout, response=response):
            return json.dumps(response, separators=(",", ":")).encode("utf-8")

        adapter = JevChoiceAdapter(
            credential="provider-free-fixture-key",
            qualification=JevQualification(
                fixture_passed=True,
                minimum_probability=_required_number(case, "minimum_probability"),
                minimum_net_tokens=_required_int(case, "minimum_net_tokens"),
                maximum_latency_ms=_required_number(case, "maximum_latency_ms"),
            ),
            developer_selected_provider=True,
            transport=transport,
        )
        request = MethodRequest(
            step_id="step-" + name,
            tool_call_id="call-" + name,
            operation="handler_select",
            payload=case.get("state"),
            permission="read",
            side_effecting=False,
            streaming_required=False,
            expected_model_input_tokens=_required_int(case, "expected_model_input_tokens"),
        )
        options = _required_dict(case, "options")
        if any(not isinstance(value, (str, type(None))) for value in options.values()):
            raise PersonalQualificationError("personal_qualification_fixture_invalid")
        result = ExecutionRouter(jev=adapter).route(
            request,
            options=options,  # type: ignore[arg-type]
            instructions=_required_string(case, "instructions"),
        )
        choice = result.result.get("choice") if isinstance(result.result, dict) else None
        monetary_cost = result.usage.get("actual_cost_microusd")
        expected_cost = response.get("actual_cost_microusd")
        cost_passed = monetary_cost == expected_cost if result.method == "jev" else True
        checks.append(
            {
                "name": f"jev:{name}",
                "scope": "experimental_contract",
                "passed": (
                    result.method == case.get("expected_method")
                    and choice == case.get("expected_choice")
                    and result.latency_ms is not None
                    and cost_passed
                ),
            }
        )
    return checks


def _qualify_behavior(fixture: dict[str, object]) -> bool:
    value = _required_dict(fixture, "behavior")
    repeated = _behavior_event(_required_dict(value, "repeated_failure"))
    excluded = _behavior_event(_required_dict(value, "excluded_poll"))
    tracker = BehaviorTracker()
    first = tracker.observe(repeated)
    second = tracker.observe(repeated)
    decision = BoundedReadRetry().decide(repeated, second)
    poll = tracker.observe(excluded)
    return (
        not first.possible_friction
        and second.possible_friction
        and decision.action == "retry_once"
        and decision.maximum_attempts == 1
        and decision.counted_savings == 0
        and poll.excluded_reason == "polling"
        and not poll.possible_friction
    )


def _conversation_turn(value: object) -> ConversationTurn:
    item = _as_dict(value)
    constraints = item.get("constraints", [])
    if not isinstance(constraints, list) or any(not isinstance(value, str) for value in constraints):
        raise PersonalQualificationError("personal_qualification_fixture_invalid")
    try:
        return ConversationTurn(
            role=item.get("role"),  # type: ignore[arg-type]
            content=_required_string(item, "content"),
            constraints=tuple(constraints),
            unresolved_question=item.get("unresolved_question", False),  # type: ignore[arg-type]
            tool_name=item.get("tool_name"),  # type: ignore[arg-type]
            tool_state=item.get("tool_state"),  # type: ignore[arg-type]
        )
    except TypeError as error:
        raise PersonalQualificationError("personal_qualification_fixture_invalid") from error


def _method_request(value: dict[str, object]) -> MethodRequest:
    try:
        return MethodRequest(
            step_id=_required_string(value, "step_id"),
            tool_call_id=_required_string(value, "tool_call_id"),
            operation=_required_string(value, "operation"),
            payload=value.get("payload"),
            permission=value.get("permission"),  # type: ignore[arg-type]
            side_effecting=value.get("side_effecting"),  # type: ignore[arg-type]
            streaming_required=value.get("streaming_required"),  # type: ignore[arg-type]
            expected_model_input_tokens=value.get("expected_model_input_tokens"),  # type: ignore[arg-type]
        )
    except TypeError as error:
        raise PersonalQualificationError("personal_qualification_fixture_invalid") from error


def _behavior_event(value: dict[str, object]) -> BehaviorEvent:
    try:
        return BehaviorEvent(**value)  # type: ignore[arg-type]
    except TypeError as error:
        raise PersonalQualificationError("personal_qualification_fixture_invalid") from error


def _as_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise PersonalQualificationError("personal_qualification_fixture_invalid")
    return value


def _required_dict(value: dict[str, object], key: str) -> dict[str, object]:
    return _as_dict(value.get(key))


def _required_list(value: dict[str, object], key: str) -> list[object]:
    result = value.get(key)
    if not isinstance(result, list):
        raise PersonalQualificationError("personal_qualification_fixture_invalid")
    return result


def _required_string(value: dict[str, object], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise PersonalQualificationError("personal_qualification_fixture_invalid")
    return result


def _required_int(value: dict[str, object], key: str) -> int:
    result = value.get(key)
    if type(result) is not int:
        raise PersonalQualificationError("personal_qualification_fixture_invalid")
    return result


def _required_number(value: dict[str, object], key: str) -> float:
    result = value.get(key)
    if isinstance(result, bool) or not isinstance(result, (int, float)):
        raise PersonalQualificationError("personal_qualification_fixture_invalid")
    return float(result)


__all__ = ["PersonalQualificationError", "run_product_qualification"]
