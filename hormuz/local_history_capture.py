"""Bounded text-only observations for explicitly consented local history.

Never changes provider bytes, accounting, or routing. Headers, opaque provider
state, tool arguments/results and binary attachments are outside this contract.
"""
from __future__ import annotations

from dataclasses import replace
import json
import logging
import time
import uuid

from .config import SecretControls
from .redaction import SecretRedactor


LOGGER = logging.getLogger("hormuz")
MAX_TEXT_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 256 * 1024


def history_redactor(controls: SecretControls, protected_values) -> SecretRedactor:
    return SecretRedactor(replace(controls, mode="redact", builtins=True), tuple(protected_values))


def history_consent(history, actor_id):
    """Snapshot once at authenticated request ingress, including opted-out None."""
    try:
        return history.begin(actor_id)
    except (OSError, ValueError):
        LOGGER.warning("local_history_capture_unavailable")
        return None


def _texts(content):
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    return [block["text"] for block in content if isinstance(block, dict)
            and block.get("type") in ("text", "input_text", "output_text")
            and isinstance(block.get("text"), str)]


def _request_text(value):
    result = []
    for name in ("instructions", "system"):
        for text in _texts(value.get(name)):
            result.append({"role": "system", "text": text})
    messages = value.get("messages", value.get("input"))
    if isinstance(messages, str):
        result.append({"role": "user", "text": messages})
    elif isinstance(messages, list):
        for message in messages:
            if not isinstance(message, dict):
                continue
            if message.get("type") not in (None, "message"):
                continue
            if message.get("role") not in ("user", "assistant", "system", "developer"):
                continue
            for text in _texts(message.get("content")):
                result.append({"role": message["role"], "text": text})
    return result


def _response_text(value):
    if not isinstance(value, dict):
        return ""
    if isinstance(value.get("output"), list):
        return "".join(text for message in value["output"] if isinstance(message, dict)
                       and message.get("type") == "message"
                       and message.get("role") == "assistant"
                       for text in _texts(message.get("content")))
    if value.get("role") == "assistant":
        return "".join(_texts(value.get("content")))
    choices = value.get("choices")
    if isinstance(choices, list):
        return "".join(text for choice in choices if isinstance(choice, dict)
                       and isinstance(choice.get("message"), dict)
                       for text in _texts(choice["message"].get("content")))
    return ""


def _standard_pricing(protocol, value):
    if not isinstance(value, dict):
        return True
    tiers = (None, "default") if protocol == "openai" else (None, "standard", "standard_only")
    return (value.get("service_tier") in tiers and value.get("speed") in (None, "standard")
            and value.get("inference_geo") in (None, "global")
            and all(_standard_pricing(protocol, value.get(name))
                    for name in ("response", "message", "usage")))


def _provider_tool_usage(value):
    if not isinstance(value, dict):
        return False
    tools = value.get("server_tool_use")
    return (isinstance(tools, dict) and any(count != 0 for count in tools.values())
            or any(_provider_tool_usage(value.get(name)) for name in ("response", "message", "usage")))


class HistoryCapture:
    """One provider attempt, discarded when consent changes before completion."""

    @classmethod
    def begin(cls, history, *, identity, client, protocol, decision, destination,
              request_value, redactor, generation):
        try:
            if generation is None:
                return None
            return cls(history, generation, identity, client, protocol, decision,
                       destination, request_value, redactor)
        except (OSError, ValueError, TypeError, AttributeError, RecursionError):
            LOGGER.warning("local_history_capture_unavailable")
            return None

    def __init__(self, history, generation, identity, client, protocol, decision,
                 destination, request_value, redactor):
        self.history, self.generation, self.redactor = history, generation, redactor
        self.route = decision.route
        self.started = time.monotonic()
        self.sent = False
        self.data = bytearray()
        self.overflow = False
        self.is_event_stream = False
        self.pricing_known = _standard_pricing(protocol, request_value)
        tools = request_value.get("tools")
        self.provider_tools = isinstance(tools, list) and any(
            isinstance(tool, dict) and tool.get("type") not in (
                None, "function", "custom", "namespace",
            ) for tool in tools)
        self.entry = {
            "id": uuid.uuid4().hex, "recorded_at": time.time(),
            "actor_id": identity.actor_id, "client": client, "protocol": protocol,
            "requested_model": decision.requested_model, "routed_model": self.route.upstream_model,
            "destination": destination.name, "locality": destination.locality,
            "outcome": "outcome_unknown", "http_status": None,
            "input_tokens": None, "output_tokens": None, "estimated_cost_microusd": None,
            "cost_reason": "provider_outcome_unknown", "latency_ms": 0,
            "request_text": [], "response_text": "",
            "request_content_status": "unsupported", "response_content_status": "unsupported",
        }
        text = _request_text(request_value)
        if len(text) > 256 or len(json.dumps(text, ensure_ascii=False).encode()) > MAX_TEXT_BYTES:
            self.entry["request_content_status"] = "omitted_limit"
        elif text:
            filtered = self.redactor.inspect({"text": text}, mode="redact").value["text"]
            if len(json.dumps(filtered, ensure_ascii=False).encode()) > MAX_TEXT_BYTES:
                self.entry["request_content_status"] = "omitted_limit"
            else:
                self.entry["request_text"] = filtered
                self.entry["request_content_status"] = "captured"

    def response(self, status, content_type):
        self.entry["http_status"] = status
        self.is_event_stream = "text/event-stream" in content_type.lower()

    def feed(self, chunk):
        if self.overflow:
            return
        if len(self.data) + len(chunk) > MAX_RESPONSE_BYTES:
            self.overflow = True
            self.data.clear()
        else:
            self.data.extend(chunk)

    def observed(self, parser, parsed, *, downstream_ok):
        usage = parsed.usage
        status = self.entry["http_status"]
        known = parser.provider_completed or parsed.provider_terminal_state in {"failed", "incomplete"}
        if status == 429:
            self.entry["outcome"] = "rate_limited"
        elif status is not None and not 200 <= status < 300 or parsed.provider_terminal_state in {"failed", "incomplete"}:
            self.entry["outcome"] = "failed"
        elif known or downstream_ok and not self.is_event_stream:
            self.entry["outcome"] = "succeeded"
        if usage.evidence_complete:
            self.entry.update(input_tokens=usage.input_tokens, output_tokens=usage.output_tokens)
        self._decode_response()
        reason = "provider_usage_unknown"
        fields = self.route.explicit_price_fields
        required = {"input_cost_per_million", "output_cost_per_million"}
        if usage.cache_read_tokens:
            required.add("cache_read_cost_per_million")
        if usage.cache_write_tokens:
            required.add("cache_write_cost_per_million")
        if self.entry["locality"] == "local":
            reason = "local_compute_unpriced"
        elif not required <= fields:
            reason = "rates_not_configured"
        elif not usage.provider_reported_model:
            reason = "provider_model_unknown"
        elif usage.provider_reported_model != self.route.upstream_model:
            reason = "provider_model_mismatch"
        elif self.provider_tools or parsed.finance.server_tool_request_count:
            reason = "additional_provider_charges_unknown"
        elif not self.pricing_known or self.overflow or not getattr(parser, "work_pricing_known", True):
            reason = "pricing_mode_unknown"
        elif self.entry["outcome"] == "outcome_unknown":
            reason = "provider_outcome_unknown"
        elif usage.evidence_complete and parsed.finance.state == "complete":
            self.entry["estimated_cost_microusd"] = self.route.estimate_cost_microusd(
                input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                cache_read_tokens=usage.cache_read_tokens, cache_write_tokens=usage.cache_write_tokens)
            reason = "configured_rate_card_estimate"
        self.entry["cost_reason"] = reason

    def _decode_response(self):
        if self.overflow:
            self.entry["response_content_status"] = "omitted_limit"
            return
        try:
            if not self.is_event_stream:
                value = json.loads(self.data)
                self.pricing_known &= _standard_pricing(self.entry["protocol"], value)
                self.provider_tools |= _provider_tool_usage(value)
                text = _response_text(value)
            else:
                terminal, deltas = None, []
                for frame in bytes(self.data).decode("utf-8").replace("\r\n", "\n").split("\n\n"):
                    lines = [line[5:].lstrip(" ") for line in frame.split("\n") if line.startswith("data:")]
                    if not lines or lines == ["[DONE]"]:
                        continue
                    value = json.loads("\n".join(lines))
                    if not isinstance(value, dict):
                        continue
                    self.pricing_known &= _standard_pricing(self.entry["protocol"], value)
                    self.provider_tools |= _provider_tool_usage(value)
                    if value.get("type") in ("response.completed", "response.failed", "response.incomplete"):
                        terminal = _response_text(value.get("response"))
                    elif value.get("type") == "response.output_text.delta" and isinstance(value.get("delta"), str):
                        deltas.append(value["delta"])
                    elif value.get("type") == "content_block_start":
                        deltas.extend(_texts([value.get("content_block")]))
                    elif value.get("type") == "content_block_delta":
                        delta = value.get("delta")
                        if isinstance(delta, dict) and delta.get("type") == "text_delta" and isinstance(delta.get("text"), str):
                            deltas.append(delta["text"])
                    elif isinstance(value.get("choices"), list):
                        for choice in value["choices"]:
                            delta = choice.get("delta") if isinstance(choice, dict) else None
                            if isinstance(delta, dict) and isinstance(delta.get("content"), str):
                                deltas.append(delta["content"])
                text = terminal if terminal is not None else "".join(deltas)
            if len(text.encode()) > MAX_TEXT_BYTES:
                self.entry["response_content_status"] = "omitted_limit"
            elif text:
                filtered = self.redactor.inspect({"text": text}, mode="redact").value["text"]
                if len(filtered.encode()) > MAX_TEXT_BYTES:
                    self.entry["response_content_status"] = "omitted_limit"
                else:
                    self.entry["response_text"] = filtered
                    self.entry["response_content_status"] = "partial" if self.entry["outcome"] == "outcome_unknown" else "captured"
        except (ValueError, UnicodeError, TypeError, AttributeError, RecursionError):
            self.pricing_known = False
            self.entry["response_content_status"] = "unsupported"

    def finish(self):
        try:
            if self.sent:
                if self.entry["http_status"] == 429:
                    self.entry["outcome"] = "rate_limited"
                self.entry["latency_ms"] = int((time.monotonic() - self.started) * 1000)
                self.history.append(self.generation, self.entry)
        except (OSError, ValueError):
            LOGGER.warning("local_history_write_unavailable")
        finally:
            self.data.clear()
