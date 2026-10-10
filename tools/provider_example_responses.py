"""Validate complete provider example replies without retaining their contents.

This checks transport/API completion, not answer correctness or completed work.
All failures use fixed identifiers suitable for a metadata-only receipt.
"""
from __future__ import annotations

import json


_APIS = {"/v1/responses", "/v1/chat/completions", "/v1/messages"}
_CHAT_SUCCESS = {"stop", "tool_calls", "function_call"}
_MESSAGE_SUCCESS = {"end_turn", "tool_use", "stop_sequence"}
_MAX_BYTES = 4 * 1024 * 1024


def _fail(reason):
    raise ValueError(reason)


def _object_pairs(pairs):
    value = {}
    for name, item in pairs:
        if name in value:
            _fail("invalid_provider_response")
        value[name] = item
    return value


def _json(raw):
    try:
        value = json.loads(raw, object_pairs_hook=_object_pairs,
                           parse_constant=lambda _value: _fail("invalid_provider_response"))
    except (ValueError, TypeError):
        _fail("invalid_provider_response")
    if not isinstance(value, dict):
        _fail("invalid_provider_response")
    return value


def _check_error(value, event=None):
    kind = value.get("type", event)
    if event == "error" or kind in {"error", "response.failed", "response.error"} or value.get("error") is not None:
        _fail("failed_provider_response")
    if kind == "response.incomplete" or value.get("status") == "incomplete":
        _fail("incomplete_provider_response")
    if value.get("status") in {"failed", "cancelled", "canceled"}:
        _fail("failed_provider_response")


def _content(items, *, responses=False):
    if not isinstance(items, list) or not items:
        _fail("invalid_provider_response")
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("type"), str):
            _fail("invalid_provider_response")
        _check_error(item)
        kind = item["type"]
        if kind in {"text", "output_text"} and not isinstance(item.get("text"), str):
            _fail("invalid_provider_response")
        if kind == "refusal" and not isinstance(item.get("refusal"), str):
            _fail("invalid_provider_response")
        if kind == "tool_use" and (not isinstance(item.get("name"), str) or not item["name"]
                or not isinstance(item.get("id"), str) or not item["id"] or not isinstance(item.get("input"), dict)):
            _fail("invalid_provider_response")
        if responses and kind == "message":
            if item.get("role") != "assistant":
                _fail("invalid_provider_response")
            _content(item.get("content"))
        if responses and kind == "function_call":
            if any(not isinstance(item.get(key), str) or not item[key] for key in ("name", "call_id", "arguments")):
                _fail("invalid_provider_response")
        if item.get("status") in {"in_progress", "incomplete"}:
            _fail("incomplete_provider_response")


def _responses(value):
    _check_error(value)
    if value.get("object") != "response":
        _fail("invalid_provider_response")
    if value.get("status") != "completed":
        _fail("incomplete_provider_response")
    _content(value.get("output"), responses=True)
    if not any(item["type"] in {"message", "function_call"} for item in value["output"]):
        _fail("invalid_provider_response")


def _finish_reason(value, successful):
    if value in {"length", "max_tokens", "pause_turn"} or value is None:
        _fail("incomplete_provider_response")
    if value not in successful:
        _fail("failed_provider_response")


def _chat(value):
    _check_error(value)
    if value.get("object") != "chat.completion":
        _fail("invalid_provider_response")
    choices = value.get("choices")
    if not isinstance(choices, list) or not choices:
        _fail("invalid_provider_response")
    seen = set()
    for choice in choices:
        if not isinstance(choice, dict) or type(choice.get("index")) is not int or choice["index"] < 0 or choice["index"] in seen:
            _fail("invalid_provider_response")
        seen.add(choice["index"])
        _finish_reason(choice.get("finish_reason"), _CHAT_SUCCESS)
        message = choice.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            _fail("invalid_provider_response")
        tools = message.get("tool_calls")
        if tools is not None:
            if not isinstance(tools, list) or not tools:
                _fail("invalid_provider_response")
            for tool in tools:
                if not isinstance(tool, dict) or tool.get("type") != "function" or not isinstance(tool.get("id"), str) or not tool["id"]:
                    _fail("invalid_provider_response")
                function = tool.get("function")
                if not isinstance(function, dict) or any(not isinstance(function.get(key), str) or not function[key] for key in ("name", "arguments")):
                    _fail("invalid_provider_response")
        function = message.get("function_call")
        if function is not None and (not isinstance(function, dict) or any(not isinstance(function.get(key), str) or not function[key] for key in ("name", "arguments"))):
            _fail("invalid_provider_response")
        if not isinstance(message.get("content"), str) and not message.get("tool_calls") and not message.get("function_call") and not isinstance(message.get("refusal"), str):
            _fail("invalid_provider_response")


def _messages(value):
    _check_error(value)
    if value.get("type") != "message" or value.get("role") != "assistant":
        _fail("invalid_provider_response")
    _finish_reason(value.get("stop_reason"), _MESSAGE_SUCCESS)
    _content(value.get("content"))
    if not any(item["type"] in {"text", "tool_use"} for item in value["content"]):
        _fail("invalid_provider_response")


def _events(raw):
    """Dispatch only delimited SSE events; an unfinished final event is partial."""
    event, data = None, []
    for line in raw.splitlines():
        if not line:
            if data:
                yield event, "\n".join(data)
            event, data = None, []
        elif not line.startswith(":"):
            name, separator, value = line.partition(":")
            if separator and value.startswith(" "):
                value = value[1:]
            if name == "event":
                event = value
            elif name == "data":
                data.append(value)
    if data:
        _fail("incomplete_provider_response")


def _stream(api, raw):
    complete = False
    choices, finished = set(), set()
    message_started = message_finished = False
    for event, data in _events(raw):
        if complete:
            _fail("invalid_provider_response")
        if data == "[DONE]":
            if api != "/v1/chat/completions":
                _fail("invalid_provider_response")
            if not choices or choices != finished:
                _fail("incomplete_provider_response")
            complete = True
            continue
        value = _json(data)
        _check_error(value, event)
        kind = value.get("type", event)
        if api == "/v1/responses":
            if event is not None and value.get("type") is not None and event != value["type"]:
                _fail("invalid_provider_response")
            if kind == "response.completed":
                _responses(value.get("response") if isinstance(value.get("response"), dict) else {})
                complete = True
        elif api == "/v1/chat/completions":
            if value.get("object") != "chat.completion.chunk" or not isinstance(value.get("choices"), list):
                _fail("invalid_provider_response")
            for choice in value["choices"]:
                if not isinstance(choice, dict) or type(choice.get("index")) is not int or choice["index"] < 0 or not isinstance(choice.get("delta"), dict):
                    _fail("invalid_provider_response")
                index = choice["index"]
                if index in finished:
                    _fail("invalid_provider_response")
                choices.add(index)
                if choice.get("finish_reason") is not None:
                    _finish_reason(choice["finish_reason"], _CHAT_SUCCESS)
                    finished.add(index)
        else:
            if event is not None and value.get("type") is not None and event != value["type"]:
                _fail("invalid_provider_response")
            if kind == "message_start":
                message = value.get("message")
                if message_started or not isinstance(message, dict) or message.get("type") != "message" or message.get("role") != "assistant" or not isinstance(message.get("content"), list):
                    _fail("invalid_provider_response")
                _check_error(message)
                message_started = True
            elif kind == "message_delta":
                delta = value.get("delta")
                if not message_started or not isinstance(delta, dict):
                    _fail("invalid_provider_response")
                _finish_reason(delta.get("stop_reason"), _MESSAGE_SUCCESS)
                message_finished = True
            elif kind == "message_stop":
                if not message_started or not message_finished:
                    _fail("incomplete_provider_response")
                complete = True
    if not complete:
        _fail("incomplete_provider_response")


def _validate_response(api, behavior, status, headers, raw):
    if api not in _APIS:
        _fail("unsupported_example_api")
    if status != 200:
        _fail("response_status_not_successful")
    if not isinstance(raw, (bytes, str)) or len(raw) > _MAX_BYTES:
        _fail("invalid_provider_response")
    try:
        raw = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    except UnicodeDecodeError:
        _fail("invalid_provider_response")
    if not isinstance(headers, dict):
        _fail("invalid_provider_response")
    content_type = next((str(value).split(";", 1)[0].strip().lower()
                         for name, value in headers.items() if str(name).lower() == "content-type"), "")
    if behavior == "stream":
        if content_type != "text/event-stream":
            _fail("invalid_provider_response")
        _stream(api, raw)
    else:
        if content_type != "application/json":
            _fail("invalid_provider_response")
        {"/v1/responses": _responses, "/v1/chat/completions": _chat, "/v1/messages": _messages}[api](_json(raw))
    return True


def validate_response(api, behavior, status, headers, raw):
    """Return True for a complete reply, otherwise raise a fixed ValueError code.

    ``raw`` is bytes (or UTF-8 text); ``headers`` is a case-insensitive mapping
    in practice and ``behavior == 'stream'`` selects SSE. Usage is optional:
    the gateway remains responsible for cost evidence and unknown charge holds.
    """
    try:
        return _validate_response(api, behavior, status, headers, raw)
    except (TypeError, KeyError, AttributeError, RecursionError):
        # Malformed nested structures must never escape with provider text.
        _fail("invalid_provider_response")
