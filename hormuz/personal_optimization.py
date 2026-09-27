"""Experimental cycle-3/4 primitives kept outside the first-release path."""

from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
import time
from dataclasses import dataclass
from typing import Literal

from .compaction_formats import canonical_json


MAX_HISTORY_TURNS = 256
MAX_HISTORY_BYTES = 512 * 1024


class PersonalOptimizationError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ConversationTurn:
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    constraints: tuple[str, ...] = ()
    unresolved_question: bool = False
    tool_name: str | None = None
    tool_state: str | None = None


@dataclass(frozen=True)
class SemanticCompactionResult:
    history_type: str
    turns: tuple[ConversationTurn, ...]
    recovery_key: str | None
    omitted_turns: int
    before_bytes: int
    after_bytes: int
    overhead_us: int
    auxiliary_calls: int


class RecoveryBuffer:
    """Bounded, in-memory recovery only; content is never persisted."""

    def __init__(self, maximum_sessions: int = 8):
        if not 1 <= maximum_sessions <= 32:
            raise PersonalOptimizationError("invalid_recovery_limit")
        self.maximum_sessions = maximum_sessions
        self._values: dict[str, tuple[ConversationTurn, ...]] = {}

    def save(self, turns: tuple[ConversationTurn, ...]) -> str:
        key = secrets.token_urlsafe(18)
        self._values[key] = turns
        while len(self._values) > self.maximum_sessions:
            self._values.pop(next(iter(self._values)))
        return key

    def recover(self, key: str) -> tuple[ConversationTurn, ...]:
        try:
            return self._values.pop(key)
        except KeyError as error:
            raise PersonalOptimizationError("recovery_unavailable") from error

    def clear(self) -> None:
        self._values.clear()


class TypedToolHistoryCompactor:
    """Conservative extractive compaction for one typed tool-session history."""

    history_type = "typed-tool-session-v1"

    def __init__(self, recovery: RecoveryBuffer, *, recent_turns: int = 8):
        if not 4 <= recent_turns <= 32:
            raise PersonalOptimizationError("invalid_recent_turn_limit")
        self.recovery = recovery
        self.recent_turns = recent_turns

    def compact(self, turns: tuple[ConversationTurn, ...]) -> SemanticCompactionResult:
        started = time.perf_counter_ns()
        _validate_turns(turns)
        before_bytes = _history_bytes(turns)
        if len(turns) <= self.recent_turns:
            return SemanticCompactionResult(
                self.history_type,
                turns,
                None,
                0,
                before_bytes,
                before_bytes,
                max(0, (time.perf_counter_ns() - started) // 1_000),
                0,
            )
        recent_start = len(turns) - self.recent_turns
        required_indices = {
            index
            for index, turn in enumerate(turns)
            if turn.role == "system" or turn.constraints or turn.unresolved_question
        }
        required_indices.update(range(recent_start, len(turns)))
        latest_tool: dict[str, int] = {}
        for index, turn in enumerate(turns):
            if turn.tool_name is not None and turn.tool_state is not None:
                latest_tool[turn.tool_name] = index
        required_indices.update(latest_tool.values())
        omitted = [turn for index, turn in enumerate(turns) if index not in required_indices]
        if not omitted:
            return SemanticCompactionResult(
                self.history_type,
                turns,
                None,
                0,
                before_bytes,
                before_bytes,
                max(0, (time.perf_counter_ns() - started) // 1_000),
                0,
            )
        constraints = _unique(
            constraint
            for turn in turns
            for constraint in turn.constraints
        )
        unresolved = _unique(
            turn.content for turn in turns if turn.unresolved_question
        )
        states = _unique(
            f"{turn.tool_name}: {turn.tool_state}"
            for index, turn in enumerate(turns)
            if index in latest_tool.values() and turn.tool_name and turn.tool_state
        )
        summary = ConversationTurn(
            role="system",
            content=canonical_json(
                {
                    "format": "hormuz-semantic-history-v1",
                    "omitted_resolved_turns": len(omitted),
                    "constraints": constraints,
                    "unresolved_questions": unresolved,
                    "latest_tool_state": states,
                    "recovery": "available_in_bounded_local_session",
                }
            ),
            constraints=tuple(constraints),
            unresolved_question=bool(unresolved),
        )
        kept = tuple(turn for index, turn in enumerate(turns) if index in required_indices)
        candidate = (summary, *kept)
        after_bytes = _history_bytes(candidate)
        if after_bytes >= before_bytes:
            return SemanticCompactionResult(
                self.history_type,
                turns,
                None,
                0,
                before_bytes,
                before_bytes,
                max(0, (time.perf_counter_ns() - started) // 1_000),
                0,
            )
        recovery_key = self.recovery.save(turns)
        return SemanticCompactionResult(
            self.history_type,
            candidate,
            recovery_key,
            len(omitted),
            before_bytes,
            after_bytes,
            max(0, (time.perf_counter_ns() - started) // 1_000),
            0,
        )


@dataclass(frozen=True)
class BehaviorEvent:
    intent: str
    tool_name: str
    outcome: Literal["success", "failure"]
    error_kind: str | None
    permission: Literal["read", "write", "network"]
    side_effecting: bool
    retryable: bool
    retry_count: int = 0
    pagination: bool = False
    polling: bool = False
    automatic_retry: bool = False
    history_retransmission: bool = False
    legitimate_iteration: bool = False


@dataclass(frozen=True)
class FrictionSignal:
    possible_friction: bool
    signature: Literal["repeated_failed_tool_call"] | None
    repetitions: int
    coverage: Literal["observed_typed_events_only"]
    uncertainty: Literal["possible_friction_not_model_failure"]
    excluded_reason: str | None


class BehaviorTracker:
    """Derive semantic similarity on-device and retain only bounded memory."""

    def __init__(self, maximum_signatures: int = 128):
        if not 8 <= maximum_signatures <= 1024:
            raise PersonalOptimizationError("invalid_signature_limit")
        self.maximum_signatures = maximum_signatures
        self._counts: dict[str, int] = {}

    def observe(self, event: BehaviorEvent) -> FrictionSignal:
        _validate_behavior_event(event)
        excluded = _excluded_behavior(event)
        if excluded is not None or event.outcome != "failure":
            return FrictionSignal(
                False,
                None,
                0,
                "observed_typed_events_only",
                "possible_friction_not_model_failure",
                excluded,
            )
        normalized = re.sub(r"\s+", " ", event.intent.strip().lower())
        digest = hashlib.sha256(
            (event.tool_name + "\0" + (event.error_kind or "unknown") + "\0" + normalized).encode("utf-8")
        ).hexdigest()
        self._counts[digest] = self._counts.get(digest, 0) + 1
        while len(self._counts) > self.maximum_signatures:
            self._counts.pop(next(iter(self._counts)))
        repetitions = self._counts[digest]
        return FrictionSignal(
            repetitions >= 2,
            "repeated_failed_tool_call" if repetitions >= 2 else None,
            repetitions,
            "observed_typed_events_only",
            "possible_friction_not_model_failure",
            None,
        )


@dataclass(frozen=True)
class InterventionDecision:
    action: Literal["retry_once", "return_control"]
    reason: str
    maximum_attempts: int
    counted_savings: int


class BoundedReadRetry:
    """One permitted recovery for a typed, idempotent read operation."""

    def decide(self, event: BehaviorEvent, signal: FrictionSignal) -> InterventionDecision:
        _validate_behavior_event(event)
        if (
            signal.possible_friction
            and signal.signature == "repeated_failed_tool_call"
            and signal.repetitions >= 2
            and signal.excluded_reason is None
            and event.outcome == "failure"
            and event.permission == "read"
            and not event.side_effecting
            and event.retryable
            and event.retry_count == 0
            and not _excluded_behavior(event)
        ):
            return InterventionDecision("retry_once", "bounded_idempotent_read_recovery", 1, 0)
        return InterventionDecision("return_control", "uncertain_or_not_permitted", 0, 0)


@dataclass(frozen=True)
class InterventionComparison:
    baseline_cost_units: int | None
    intervention_cost_units: int | None
    baseline_delay_ms: float | None
    intervention_delay_ms: float | None
    subsequent_repetition_observed: bool | None

    def __post_init__(self) -> None:
        costs = (self.baseline_cost_units, self.intervention_cost_units)
        delays = (self.baseline_delay_ms, self.intervention_delay_ms)
        if (
            any(value is not None and (type(value) is not int or value < 0) for value in costs)
            or any(
                value is not None
                and (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    or value < 0
                )
                for value in delays
            )
            or self.subsequent_repetition_observed is not None
            and not isinstance(self.subsequent_repetition_observed, bool)
        ):
            raise PersonalOptimizationError("intervention_comparison_invalid")

    @property
    def supported_savings(self) -> int | None:
        if self.baseline_cost_units is None or self.intervention_cost_units is None:
            return None
        return self.baseline_cost_units - self.intervention_cost_units


def _validate_turns(turns: tuple[ConversationTurn, ...]) -> None:
    if not isinstance(turns, tuple) or not 1 <= len(turns) <= MAX_HISTORY_TURNS:
        raise PersonalOptimizationError("history_invalid")
    for turn in turns:
        if (
            not isinstance(turn, ConversationTurn)
            or turn.role not in {"system", "user", "assistant", "tool"}
            or not isinstance(turn.content, str)
            or len(turn.content.encode("utf-8")) > 64 * 1024
            or any(not isinstance(item, str) for item in turn.constraints)
            or not isinstance(turn.unresolved_question, bool)
            or turn.tool_name is not None and not isinstance(turn.tool_name, str)
            or turn.tool_state is not None and not isinstance(turn.tool_state, str)
        ):
            raise PersonalOptimizationError("history_invalid")
    if _history_bytes(turns) > MAX_HISTORY_BYTES:
        raise PersonalOptimizationError("history_too_large")


def _history_bytes(turns: tuple[ConversationTurn, ...]) -> int:
    return len(
        json.dumps(
            [
                {
                    "role": turn.role,
                    "content": turn.content,
                    "constraints": turn.constraints,
                    "unresolved_question": turn.unresolved_question,
                    "tool_name": turn.tool_name,
                    "tool_state": turn.tool_state,
                }
                for turn in turns
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _unique(values) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _validate_behavior_event(event: BehaviorEvent) -> None:
    if (
        not isinstance(event.intent, str)
        or not 1 <= len(event.intent.encode("utf-8")) <= 4096
        or not isinstance(event.tool_name, str)
        or not 1 <= len(event.tool_name.encode("utf-8")) <= 128
        or event.outcome not in {"success", "failure"}
        or event.error_kind is not None and not isinstance(event.error_kind, str)
        or isinstance(event.error_kind, str)
        and len(event.error_kind.encode("utf-8")) > 256
        or event.permission not in {"read", "write", "network"}
        or any(
            not isinstance(value, bool)
            for value in (
                event.side_effecting,
                event.retryable,
                event.pagination,
                event.polling,
                event.automatic_retry,
                event.history_retransmission,
                event.legitimate_iteration,
            )
        )
        or type(event.retry_count) is not int
        or not 0 <= event.retry_count <= 16
    ):
        raise PersonalOptimizationError("behavior_event_invalid")


def _excluded_behavior(event: BehaviorEvent) -> str | None:
    for enabled, reason in (
        (event.pagination, "pagination"),
        (event.polling, "polling"),
        (event.automatic_retry, "automatic_retry"),
        (event.history_retransmission, "history_retransmission"),
        (event.legitimate_iteration, "legitimate_iteration"),
    ):
        if enabled:
            return reason
    return None


__all__ = [
    "BehaviorEvent",
    "BehaviorTracker",
    "BoundedReadRetry",
    "ConversationTurn",
    "FrictionSignal",
    "InterventionComparison",
    "InterventionDecision",
    "RecoveryBuffer",
    "SemanticCompactionResult",
    "TypedToolHistoryCompactor",
]
