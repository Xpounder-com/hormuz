from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .config import is_model_identifier


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    billable_tokens: int = 0
    actual_model: str | None = None
    usage_reported: bool = False
    provider_usage: dict[str, Any] = field(default_factory=dict, repr=False)


_OPENAI_USAGE_SPEC: dict[str, object] = {
    "input_tokens": "integer",
    "output_tokens": "integer",
    "total_tokens": "integer",
    "input_tokens_details": {
        "cached_tokens": "integer",
        "cache_write_tokens": "integer",
        "audio_tokens": "integer",
    },
    "output_tokens_details": {
        "reasoning_tokens": "integer",
        "audio_tokens": "integer",
        "accepted_prediction_tokens": "integer",
        "rejected_prediction_tokens": "integer",
    },
}
_ANTHROPIC_USAGE_SPEC: dict[str, object] = {
    "input_tokens": "integer",
    "output_tokens": "integer",
    "cache_read_input_tokens": "integer",
    "cache_creation_input_tokens": "integer",
    "cache_creation": {
        "ephemeral_5m_input_tokens": "integer",
        "ephemeral_1h_input_tokens": "integer",
    },
    "server_tool_use": {
        "web_search_requests": "integer",
        "web_fetch_requests": "integer",
    },
    "service_tier": "string",
    "inference_geo": "string",
}
_PROVIDER_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+=-]{0,255}\Z")
MAX_PROVIDER_USAGE_SSE_LINE_BYTES = 1024 * 1024
MAX_PROVIDER_USAGE_JSON_BYTES = 10 * 1024 * 1024


class ResponseUsageParser:
    """Extracts provider usage metadata without retaining response content."""

    def __init__(self, protocol: str, *, is_event_stream: bool):
        self.protocol = protocol
        self.is_event_stream = is_event_stream
        self.usage = Usage()
        self._sse_line_buffer = bytearray()
        self._discarding_sse_line = False
        self._json_buffer = bytearray()
        self._json_oversized = False
        self._input_usage_reported = False
        self._output_usage_reported = False
        self._terminal_usage_reported = not is_event_stream

    def feed(self, data: bytes) -> None:
        if self.is_event_stream:
            self._feed_sse(data)
        elif not self._json_oversized:
            remaining = MAX_PROVIDER_USAGE_JSON_BYTES - len(self._json_buffer)
            if len(data) > remaining:
                self._json_buffer.clear()
                self._json_oversized = True
            else:
                self._json_buffer.extend(data)

    def finish(self) -> Usage:
        if self.is_event_stream:
            if not self._discarding_sse_line and self._sse_line_buffer:
                self._parse_sse_line_bytes(self._sse_line_buffer)
            self._sse_line_buffer.clear()
            self._discarding_sse_line = False
        elif self._json_buffer:
            try:
                self._parse_object(json.loads(self._json_buffer))
            except (ValueError, RecursionError):
                pass
            finally:
                self._json_buffer.clear()
        self._json_oversized = False
        return self.usage

    def _feed_sse(self, data: bytes) -> None:
        offset = 0
        while offset < len(data):
            if self._discarding_sse_line:
                newline = data.find(b"\n", offset)
                if newline < 0:
                    return
                self._discarding_sse_line = False
                offset = newline + 1
                continue

            newline = data.find(b"\n", offset)
            segment_end = len(data) if newline < 0 else newline
            remaining = MAX_PROVIDER_USAGE_SSE_LINE_BYTES - len(
                self._sse_line_buffer
            )
            if segment_end - offset > remaining:
                self._sse_line_buffer.clear()
                if newline < 0:
                    self._discarding_sse_line = True
                    return
                offset = newline + 1
                continue

            self._sse_line_buffer.extend(data[offset:segment_end])
            if newline < 0:
                return
            self._parse_sse_line_bytes(self._sse_line_buffer)
            self._sse_line_buffer.clear()
            offset = newline + 1

    def _parse_sse_line_bytes(self, line: bytearray) -> None:
        self._parse_sse_line(
            bytes(line).rstrip(b"\r").decode("utf-8", errors="replace")
        )

    def _parse_sse_line(self, line: str) -> None:
        if not line.startswith("data:"):
            return
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            return
        try:
            value = json.loads(payload)
        except (ValueError, RecursionError):
            return
        self._parse_object(value)

    def _parse_object(self, value: Any) -> None:
        if not isinstance(value, dict):
            return
        if self.protocol == "openai":
            completed = value.get("type") == "response.completed"
            response = value.get("response") if completed else value
            if isinstance(response, dict):
                self.usage.actual_model = _bounded_model_identifier(
                    response.get("model"),
                    self.usage.actual_model,
                )
                self._apply_openai_usage(response.get("usage"))
                if completed:
                    self._terminal_usage_reported = True
                    self._update_usage_reported()
        elif self.protocol == "anthropic":
            if value.get("type") == "message_start" and isinstance(value.get("message"), dict):
                message = value["message"]
                self.usage.actual_model = _bounded_model_identifier(
                    message.get("model"),
                    self.usage.actual_model,
                )
                self._apply_anthropic_usage(message.get("usage"))
            else:
                self.usage.actual_model = _bounded_model_identifier(
                    value.get("model"),
                    self.usage.actual_model,
                )
                self._apply_anthropic_usage(value.get("usage"))
                if (
                    value.get("type") == "message_delta"
                    and isinstance(value.get("usage"), dict)
                    and _is_nonnegative_int(value["usage"].get("output_tokens"))
                ):
                    self._terminal_usage_reported = True
                    self._update_usage_reported()

    def _apply_openai_usage(self, value: Any) -> None:
        if not isinstance(value, dict):
            return
        self._merge_provider_usage(sanitize_provider_usage("openai", value))
        if _is_nonnegative_int(value.get("input_tokens")):
            self._input_usage_reported = True
        if _is_nonnegative_int(value.get("output_tokens")):
            self._output_usage_reported = True
        self.usage.input_tokens = _nonnegative_int(value.get("input_tokens"), self.usage.input_tokens)
        self.usage.output_tokens = _nonnegative_int(value.get("output_tokens"), self.usage.output_tokens)
        input_details = value.get("input_tokens_details")
        if isinstance(input_details, dict):
            self.usage.cache_read_tokens = _nonnegative_int(
                input_details.get("cached_tokens"), self.usage.cache_read_tokens
            )
            self.usage.cache_write_tokens = _nonnegative_int(
                input_details.get("cache_write_tokens"),
                self.usage.cache_write_tokens,
            )
        output_details = value.get("output_tokens_details")
        if isinstance(output_details, dict):
            self.usage.reasoning_tokens = _nonnegative_int(
                output_details.get("reasoning_tokens"), self.usage.reasoning_tokens
            )
        self.usage.billable_tokens = _bounded_sum(
            self.usage.input_tokens,
            self.usage.output_tokens,
        )
        self._update_usage_reported()

    def _apply_anthropic_usage(self, value: Any) -> None:
        if not isinstance(value, dict):
            return
        self._merge_provider_usage(sanitize_provider_usage("anthropic", value))
        if _is_nonnegative_int(value.get("input_tokens")):
            self._input_usage_reported = True
        if _is_nonnegative_int(value.get("output_tokens")):
            self._output_usage_reported = True
        self.usage.input_tokens = _nonnegative_int(value.get("input_tokens"), self.usage.input_tokens)
        self.usage.output_tokens = _nonnegative_int(value.get("output_tokens"), self.usage.output_tokens)
        self.usage.cache_read_tokens = _nonnegative_int(
            value.get("cache_read_input_tokens"), self.usage.cache_read_tokens
        )
        self.usage.cache_write_tokens = _nonnegative_int(
            value.get("cache_creation_input_tokens"), self.usage.cache_write_tokens
        )
        self.usage.billable_tokens = _bounded_sum(
            self.usage.input_tokens,
            self.usage.output_tokens,
            self.usage.cache_read_tokens,
            self.usage.cache_write_tokens,
        )
        self._update_usage_reported()

    def _update_usage_reported(self) -> None:
        self.usage.usage_reported = (
            self._input_usage_reported and self._output_usage_reported
            and self._terminal_usage_reported
        )

    def _merge_provider_usage(self, value: dict[str, Any]) -> None:
        for key, item in value.items():
            existing = self.usage.provider_usage.get(key)
            if isinstance(existing, dict) and isinstance(item, dict):
                existing.update(item)
            else:
                self.usage.provider_usage[key] = item


def sanitize_provider_usage(protocol: str, value: Any) -> dict[str, Any]:
    """Return the documented metadata-only subset of a provider usage object."""
    if not isinstance(value, dict):
        return {}
    spec = {
        "openai": _OPENAI_USAGE_SPEC,
        "anthropic": _ANTHROPIC_USAGE_SPEC,
    }.get(protocol)
    if spec is None:
        return {}
    return _sanitize_usage_object(value, spec)


def sanitize_provider_model_id(value: object) -> str | None:
    """Return a bounded provider model identifier safe for control metadata."""

    if (
        not is_model_identifier(value)
        or not isinstance(value, str)
        or len(value.encode("ascii")) > 256
    ):
        return None
    return value


def sanitize_provider_request_id(value: object) -> str | None:
    """Return an opaque bounded provider request ID or omit unsafe metadata."""

    if not isinstance(value, str) or _PROVIDER_REQUEST_ID.fullmatch(value) is None:
        return None
    return value


def _sanitize_usage_object(value: dict[str, Any], spec: dict[str, object]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, kind in spec.items():
        item = value.get(key)
        if kind == "integer":
            if (
                isinstance(item, int)
                and not isinstance(item, bool)
                and 0 <= item <= 2**63 - 1
            ):
                result[key] = item
        elif kind == "string":
            if (
                isinstance(item, str)
                and 0 < len(item.encode("utf-8")) <= 128
                and all(character.isprintable() for character in item)
            ):
                result[key] = item
        elif isinstance(kind, dict) and isinstance(item, dict):
            nested = _sanitize_usage_object(item, kind)
            if nested:
                result[key] = nested
    return result


def _nonnegative_int(value: Any, fallback: int) -> int:
    if not _is_nonnegative_int(value):
        return fallback
    return value


def _is_nonnegative_int(value: Any) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= 2**63 - 1
    )


def _bounded_model_identifier(value: Any, fallback: str | None) -> str | None:
    return sanitize_provider_model_id(value) or fallback


def _bounded_sum(*values: int) -> int:
    return min(sum(values), 2**63 - 1)
