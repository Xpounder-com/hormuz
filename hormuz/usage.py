from __future__ import annotations

import codecs
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
    provider_usage: dict[str, Any] = field(default_factory=dict, repr=False)


_OPENAI_USAGE_SPEC: dict[str, object] = {
    "input_tokens": "integer",
    "output_tokens": "integer",
    "total_tokens": "integer",
    "input_tokens_details": {
        "cached_tokens": "integer",
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


class ResponseUsageParser:
    """Extracts provider usage metadata without retaining response content."""

    def __init__(self, protocol: str, *, is_event_stream: bool):
        self.protocol = protocol
        self.is_event_stream = is_event_stream
        self.usage = Usage()
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._line_buffer = ""
        self._json_buffer = bytearray()

    def feed(self, data: bytes) -> None:
        if self.is_event_stream:
            self._line_buffer += self._decoder.decode(data)
            while "\n" in self._line_buffer:
                line, self._line_buffer = self._line_buffer.split("\n", 1)
                self._parse_sse_line(line.rstrip("\r"))
        elif len(self._json_buffer) < 10 * 1024 * 1024:
            self._json_buffer.extend(data)

    def finish(self) -> Usage:
        if self.is_event_stream:
            self._line_buffer += self._decoder.decode(b"", final=True)
            if self._line_buffer:
                self._parse_sse_line(self._line_buffer.rstrip("\r"))
        elif self._json_buffer:
            try:
                self._parse_object(json.loads(self._json_buffer))
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass
        return self.usage

    def _parse_sse_line(self, line: str) -> None:
        if not line.startswith("data:"):
            return
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            return
        try:
            value = json.loads(payload)
        except json.JSONDecodeError:
            return
        self._parse_object(value)

    def _parse_object(self, value: Any) -> None:
        if not isinstance(value, dict):
            return
        if self.protocol == "openai":
            response = value.get("response") if value.get("type") == "response.completed" else value
            if isinstance(response, dict):
                self.usage.actual_model = _bounded_model_identifier(
                    response.get("model"),
                    self.usage.actual_model,
                )
                self._apply_openai_usage(response.get("usage"))
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

    def _apply_openai_usage(self, value: Any) -> None:
        if not isinstance(value, dict):
            return
        self._merge_provider_usage(sanitize_provider_usage("openai", value))
        self.usage.input_tokens = _nonnegative_int(value.get("input_tokens"), self.usage.input_tokens)
        self.usage.output_tokens = _nonnegative_int(value.get("output_tokens"), self.usage.output_tokens)
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
        self.usage.billable_tokens = _bounded_sum(
            self.usage.input_tokens,
            self.usage.output_tokens,
        )

    def _apply_anthropic_usage(self, value: Any) -> None:
        if not isinstance(value, dict):
            return
        self._merge_provider_usage(sanitize_provider_usage("anthropic", value))
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
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > 2**63 - 1
    ):
        return fallback
    return value


def _bounded_model_identifier(value: Any, fallback: str | None) -> str | None:
    return sanitize_provider_model_id(value) or fallback


def _bounded_sum(*values: int) -> int:
    return min(sum(values), 2**63 - 1)
