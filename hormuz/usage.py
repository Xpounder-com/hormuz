from __future__ import annotations

import codecs
import json
from dataclasses import dataclass
from typing import Any

from .finance_attempts import NativeUsageAccumulator, NativeUsageObservation


_OPENAI_TERMINAL_RESPONSE_EVENTS = frozenset({
    "response.completed",
    "response.failed",
    "response.incomplete",
})


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    provider_reported_model: str | None = None
    evidence_complete: bool = False


@dataclass(frozen=True)
class ParsedUsage:
    """The unchanged v1 projection and its provider-native finance evidence."""

    usage: Usage
    finance: NativeUsageObservation
    provider_terminal_state: str | None


class ResponseUsageParser:
    """Extracts provider usage metadata without retaining response content."""

    def __init__(self, protocol: str, *, is_event_stream: bool):
        self.protocol = protocol
        self.is_event_stream = is_event_stream
        self.usage = Usage()
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._line_buffer = ""
        self._json_buffer = bytearray()
        self._input_tokens_observed = False
        self._output_tokens_observed = False
        self._native = NativeUsageAccumulator(protocol)
        self._provider_terminal_state: str | None = None
        self._provider_completed = False
        self._finished: ParsedUsage | None = None

    @property
    def provider_completed(self) -> bool:
        """Whether a successful provider terminal marker was observed."""

        return self._provider_completed

    def feed(self, data: bytes) -> None:
        if self._finished is not None:
            raise RuntimeError("provider_usage_parser_finished")
        if self.is_event_stream:
            self._line_buffer += self._decoder.decode(data)
            while "\n" in self._line_buffer:
                line, self._line_buffer = self._line_buffer.split("\n", 1)
                self._parse_sse_line(line.rstrip("\r"))
        elif len(self._json_buffer) < 10 * 1024 * 1024:
            self._json_buffer.extend(data)

    def finish(self) -> Usage:
        return self.finish_with_finance().usage

    def finish_with_finance(self) -> ParsedUsage:
        if self._finished is not None:
            return self._finished
        if self.is_event_stream:
            self._line_buffer += self._decoder.decode(b"", final=True)
            if self._line_buffer:
                self._parse_sse_line(self._line_buffer.rstrip("\r"))
        elif self._json_buffer:
            try:
                self._parse_object(_strict_json_loads(self._json_buffer))
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
                self._native.note_parse_failure()
        self.usage.evidence_complete = (
            self._input_tokens_observed and self._output_tokens_observed
        )
        self._finished = ParsedUsage(
            self.usage,
            self._native.finish(),
            self._provider_terminal_state,
        )
        return self._finished

    def _parse_sse_line(self, line: str) -> None:
        if line.startswith("event:"):
            event = line[6:].strip()
            if self.protocol == "openai" and event in _OPENAI_TERMINAL_RESPONSE_EVENTS:
                self._observe_provider_terminal_state(
                    event.removeprefix("response."),
                )
            if (self.protocol == "openai" and event == "response.completed") or (
                self.protocol == "anthropic" and event == "message_stop"
            ):
                self._provider_completed = True
            return
        if not line.startswith("data:"):
            return
        payload = line[5:].strip()
        if not payload:
            return
        if payload == "[DONE]":
            self._provider_completed = True
            return
        try:
            value = _strict_json_loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            self._native.note_parse_failure()
            return
        self._parse_object(value)

    def _parse_object(self, value: Any) -> None:
        if not isinstance(value, dict):
            return
        if self.protocol == "openai":
            event_type = value.get("type")
            if isinstance(event_type, str) and event_type in _OPENAI_TERMINAL_RESPONSE_EVENTS:
                self._observe_provider_terminal_state(
                    event_type.removeprefix("response."),
                )
                if event_type == "response.completed":
                    self._provider_completed = True
            elif not self.is_event_stream:
                self._observe_provider_terminal_state(value.get("status"))
            response = (
                value.get("response")
                if isinstance(event_type, str)
                and event_type in _OPENAI_TERMINAL_RESPONSE_EVENTS
                else value
            )
            if isinstance(response, dict):
                self._apply_provider_model(response.get("model"))
                self._apply_openai_usage(response.get("usage"))
                self._native.observe_openai_response(response)
        elif self.protocol == "anthropic":
            if self.is_event_stream and value.get("type") == "message_stop":
                self._provider_completed = True
            if value.get("type") == "message_start" and isinstance(value.get("message"), dict):
                self._apply_provider_model(value["message"].get("model"))
                self._apply_anthropic_usage(
                    value["message"].get("usage"),
                    observe_output=not self.is_event_stream,
                )
                self._native.observe_anthropic_usage(
                    value["message"].get("usage"),
                    present="usage" in value["message"],
                    observe_input=True,
                    observe_output=not self.is_event_stream,
                )
            elif self.is_event_stream:
                self._apply_provider_model(value.get("model"))
                if value.get("type") == "message_delta":
                    # Anthropic's final cumulative output count is authoritative.
                    # A later malformed delta must not inherit earlier evidence.
                    self.usage.output_tokens = 0
                    self._output_tokens_observed = False
                    self._apply_anthropic_usage(
                        value.get("usage"), observe_input=False,
                    )
                    self._native.observe_anthropic_usage(
                        value.get("usage"),
                        present="usage" in value,
                        observe_input=False,
                        observe_output=True,
                        replace_output=True,
                    )
            else:
                self._apply_provider_model(value.get("model"))
                self._apply_anthropic_usage(value.get("usage"))
                self._native.observe_anthropic_usage(
                    value.get("usage"),
                    present="usage" in value,
                    observe_input=True,
                    observe_output=True,
                )

    def _observe_provider_terminal_state(self, value: Any) -> None:
        if value not in {"completed", "failed", "incomplete"}:
            return
        if self._provider_terminal_state is None:
            self._provider_terminal_state = value
        elif self._provider_terminal_state != value:
            # A provider stream cannot truthfully have two different terminal
            # results. Preserve the conservative non-success classification
            # and mark its native evidence partial rather than trusting either.
            self._provider_terminal_state = "failed"
            self._native.note_parse_failure()

    def _apply_provider_model(self, value: Any) -> None:
        if isinstance(value, str) and value and len(value) <= 256 and all(character not in value for character in "\r\n\x00"):
            self.usage.provider_reported_model = value

    def _apply_openai_usage(self, value: Any) -> None:
        if not isinstance(value, dict):
            return
        input_tokens = _observed_nonnegative_int(value.get("input_tokens"))
        if input_tokens is not None:
            self.usage.input_tokens = input_tokens
            self._input_tokens_observed = True
        output_tokens = _observed_nonnegative_int(value.get("output_tokens"))
        if output_tokens is not None:
            self.usage.output_tokens = output_tokens
            self._output_tokens_observed = True
        input_details = value.get("input_tokens_details")
        if isinstance(input_details, dict):
            self.usage.cache_read_tokens = _nonnegative_int(
                input_details.get("cached_tokens"), self.usage.cache_read_tokens
            )
        output_details = value.get("output_tokens_details")
        if isinstance(output_details, dict):
            self.usage.reasoning_tokens = _nonnegative_int(
                output_details.get("reasoning_tokens"), self.usage.reasoning_tokens
            )

    def _apply_anthropic_usage(
        self,
        value: Any,
        *,
        observe_input: bool = True,
        observe_output: bool = True,
    ) -> None:
        if not isinstance(value, dict):
            return
        if observe_input:
            input_tokens = _observed_nonnegative_int(value.get("input_tokens"))
            if input_tokens is not None:
                self.usage.input_tokens = input_tokens
                self._input_tokens_observed = True
            self.usage.cache_read_tokens = _nonnegative_int(
                value.get("cache_read_input_tokens"), self.usage.cache_read_tokens
            )
            self.usage.cache_write_tokens = _nonnegative_int(
                value.get("cache_creation_input_tokens"), self.usage.cache_write_tokens
            )
        if observe_output:
            output_tokens = _observed_nonnegative_int(value.get("output_tokens"))
            if output_tokens is not None:
                self.usage.output_tokens = output_tokens
                self._output_tokens_observed = True


def _nonnegative_int(value: Any, fallback: int) -> int:
    observed = _observed_nonnegative_int(value)
    return fallback if observed is None else observed


def _observed_nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _strict_json_loads(value: bytes | bytearray | str) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("duplicate JSON member")
            result[key] = item
        return result

    def nonfinite(_value: str) -> None:
        raise ValueError("non-finite JSON value")

    return json.loads(value, object_pairs_hook=unique, parse_constant=nonfinite)
