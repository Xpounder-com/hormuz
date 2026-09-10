"""Client/tool mappings for context optimization.

Mappings are deliberately narrow.  They select a representation from a
verified call contract; tool output is never treated as executable guidance.
"""

from __future__ import annotations

import json
import shlex
from collections.abc import Mapping
from importlib.resources import files
from typing import cast

from .compaction import Format, Protocol, Selection
from .compaction_formats import TRANSFORM_VERSION, strict_json_loads


SUPPORTED_CLIENTS = frozenset({"codex", "claude-code"})
_FORMATS = frozenset({"json_table", "line_runs", "search_lines", "path_list"})


def _load_mappings() -> dict[str, dict[str, dict[str, str]]]:
    try:
        data = files("hormuz").joinpath("context-tool-mappings-v1.json").read_text(encoding="utf-8")
        value = strict_json_loads(data)
    except Exception as error:
        raise RuntimeError("context_mapping_manifest_invalid") from error
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "transform_version", "clients"}
        or type(value.get("schema_version")) is not int
        or value["schema_version"] != 1
        or value.get("transform_version") != TRANSFORM_VERSION
        or not isinstance(value.get("clients"), dict)
        or set(value["clients"]) != SUPPORTED_CLIENTS
    ):
        raise RuntimeError("context_mapping_manifest_invalid")
    clients = value["clients"]
    assert isinstance(clients, dict)
    for client, protocols in clients.items():
        if not isinstance(client, str) or not isinstance(protocols, dict):
            raise RuntimeError("context_mapping_manifest_invalid")
        count = 0
        for protocol, mappings in protocols.items():
            if protocol not in {"chat", "responses", "anthropic"} or not isinstance(mappings, dict):
                raise RuntimeError("context_mapping_manifest_invalid")
            count += len(mappings)
            for name, selector in mappings.items():
                if (
                    not isinstance(name, str)
                    or not name
                    or selector not in _FORMATS | {"structural_rg"}
                ):
                    raise RuntimeError("context_mapping_manifest_invalid")
        if count > 64:
            raise RuntimeError("context_mapping_manifest_invalid")
    return cast(dict[str, dict[str, dict[str, str]]], clients)


_MAPPINGS = _load_mappings()


def derive_selections(
    payload: Mapping[str, object], protocol: Protocol, *, client: str
) -> tuple[Selection, ...]:
    if client not in SUPPORTED_CLIENTS:
        return ()
    calls = _calls(payload, protocol)
    selections: list[Selection] = []
    seen: set[str] = set()
    for result_id, name, arguments in calls:
        if result_id in seen:
            # optimize_request also rejects ambiguous call/result matches.  Do
            # not emit duplicate selections because those are config errors.
            continue
        selector = _MAPPINGS.get(client, {}).get(protocol, {}).get(name)
        format: Format | None = None
        if selector in _FORMATS:
            format = cast(Format, selector)
        elif selector == "structural_rg":
            format = _command_format(name, arguments, client=client)
        if format is not None:
            selections.append(Selection(result_id, format))
            seen.add(result_id)
    return tuple(selections)


def _calls(
    payload: Mapping[str, object], protocol: Protocol
) -> list[tuple[str, str, object]]:
    result: list[tuple[str, str, object]] = []
    key = "input" if protocol == "responses" else "messages"
    items = payload.get(key)
    if not isinstance(items, list):
        return result
    for item in items:
        if not isinstance(item, dict):
            return []
        if protocol == "responses" and item.get("type") == "function_call":
            _append(result, item.get("call_id"), item.get("name"), item.get("arguments"))
        elif protocol == "chat" and item.get("role") == "assistant":
            calls = item.get("tool_calls", [])
            if not isinstance(calls, list):
                return []
            for call in calls:
                if not isinstance(call, dict):
                    return []
                function = call.get("function")
                if isinstance(function, dict):
                    _append(result, call.get("id"), function.get("name"), function.get("arguments"))
        elif protocol == "anthropic" and item.get("role") == "assistant":
            blocks = item.get("content")
            if not isinstance(blocks, list):
                continue
            for block in blocks:
                if not isinstance(block, dict):
                    return []
                if block.get("type") == "tool_use":
                    _append(result, block.get("id"), block.get("name"), block.get("input"))
    return result


def _append(
    calls: list[tuple[str, str, object]], result_id: object, name: object, arguments: object
) -> None:
    if isinstance(result_id, str) and isinstance(name, str):
        calls.append((result_id, name, arguments))


def _command_format(name: str, arguments: object, *, client: str) -> Format | None:
    command: object = None
    if client == "codex" and name == "exec_command" and isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except (json.JSONDecodeError, UnicodeDecodeError, RecursionError):
            return None
        if isinstance(parsed, dict) and set(parsed) <= {
            "cmd", "workdir", "yield_time_ms", "max_output_tokens", "tty", "login"
        }:
            command = parsed.get("cmd")
    elif client == "claude-code" and name == "Bash" and isinstance(arguments, dict):
        if set(arguments) <= {"command", "description", "timeout", "run_in_background"}:
            command = arguments.get("command")
    if not isinstance(command, str) or not command or len(command.encode("utf-8")) > 4096:
        return None
    if any(value in command for value in ("\n", "\r", ";", "|", "&", "<", ">", "`", "$(")):
        return None
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    if not words or words[0] != "rg":
        return None
    if "--files" in words:
        return "path_list"
    if "-n" in words or "--line-number" in words:
        return "search_lines"
    return None
