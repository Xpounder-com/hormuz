"""Deterministic client-side structural context optimization."""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, cast

from .compaction_formats import (
    JSON_TABLE,
    LINE_RUNS,
    MAX_BLOCK_BYTES,
    MAX_COLUMNS,
    MAX_DEPTH,
    MAX_NODES,
    MAX_ROWS,
    MAX_REQUEST_BYTES,
    PATH_LIST,
    SEARCH_LINES,
    TRANSFORM_VERSION,
    CompactionFormatError,
    canonical_json,
    decode_text,
    strict_json_loads,
    validate_tree,
)


Protocol = Literal["chat", "responses", "anthropic"]
Format = Literal["json_table", "line_runs", "search_lines", "path_list"]
MIN_BLOCK_BYTES = 256
MAX_SELECTIONS = 64
MIN_TOKEN_SAVINGS = 32
MIN_PERCENT_SAVINGS = 5
_SEARCH_LINE = re.compile(r"^([^:\r\n]+):([0-9]+):(.*)$")
_FORMATS = frozenset({"json_table", "line_runs", "search_lines", "path_list"})


class CompactionConfigError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Selection:
    result_id: str
    format: Format


@dataclass(frozen=True)
class CompactionResult:
    payload: dict[str, object]
    changed: bool
    reason: str
    changed_blocks: int
    before_bytes: int | None
    after_bytes: int | None
    before_tokens: dict[str, int]
    after_tokens: dict[str, int]
    transform_version: str = TRANSFORM_VERSION


@dataclass
class _Target:
    result_id: str
    owner: dict[str, object]
    field: str
    item: dict[str, object]


def compact_text(text: str, format: Format) -> str:
    if format not in _FORMATS:
        raise CompactionConfigError("unknown_format")
    try:
        if decode_text(text).recognized:
            return text
    except CompactionFormatError:
        # A malformed marker is content and is not nested in another format.
        return text
    try:
        candidate = {
            "json_table": _compact_json_table,
            "line_runs": _compact_line_runs,
            "search_lines": _compact_search_lines,
            "path_list": _compact_path_list,
        }[format](text)
        if candidate == text or len(candidate.encode("utf-8")) >= len(text.encode("utf-8")):
            return text
        if decode_text(candidate).text != text:
            return text
        return candidate
    except (CompactionFormatError, TypeError, ValueError, OverflowError, RecursionError):
        return text


def optimize_request(
    payload: Mapping[str, object],
    protocol: Protocol,
    selections: Sequence[Selection],
    counters: Mapping[str, Callable[[str], int]],
    *,
    enabled: bool = False,
) -> CompactionResult:
    if not isinstance(enabled, bool):
        raise CompactionConfigError("enabled_must_be_boolean")
    original = copy.deepcopy(dict(payload))
    if not enabled:
        return _unchanged(original, "disabled")
    _validate_selections(selections)
    if protocol not in {"chat", "responses", "anthropic"}:
        raise CompactionConfigError("unknown_protocol")
    if not counters:
        return _unchanged(original, "counter_unavailable")
    try:
        validate_tree(original, max_depth=MAX_DEPTH, max_nodes=MAX_NODES)
        before_serialized = _request_json(original)
    except (CompactionFormatError, TypeError, ValueError, OverflowError, RecursionError):
        return _unchanged(original, "limit_exceeded", measured=False)
    before_bytes = len(before_serialized.encode("utf-8"))
    if before_bytes > MAX_REQUEST_BYTES:
        return _unchanged(original, "limit_exceeded", measured=False)
    if protocol == "responses" and "previous_response_id" in original:
        return _unchanged(original, "unsupported_history", before_bytes=before_bytes)

    changed = copy.deepcopy(original)
    targets, calls, shape_supported = _targets_and_calls(changed, protocol)
    if not shape_supported:
        return _unchanged(original, "unsupported_shape", before_bytes=before_bytes)
    selected = {selection.result_id: selection.format for selection in selections}
    result_counts: dict[str, int] = {}
    for target in targets:
        result_counts[target.result_id] = result_counts.get(target.result_id, 0) + 1
    changed_blocks = 0
    eligible = False
    for target in targets:
        format = selected.get(target.result_id)
        if format is None or result_counts[target.result_id] != 1:
            continue
        call_names = calls.get(target.result_id, ())
        if len(call_names) != 1:
            continue
        text = target.owner.get(target.field)
        if not isinstance(text, str):
            continue
        eligible = True
        block_bytes = len(text.encode("utf-8"))
        if block_bytes < MIN_BLOCK_BYTES or block_bytes > MAX_BLOCK_BYTES:
            continue
        candidate = compact_text(text, format)
        if candidate == text:
            continue
        try:
            old_item = _request_json(target.item)
            target.owner[target.field] = candidate
            new_item = _request_json(target.item)
            before_counts = _count(old_item, counters)
            after_counts = _count(new_item, counters)
        except Exception:
            return _unchanged(original, "counter_unavailable", before_bytes=before_bytes)
        if not _candidate_saves(old_item, new_item, before_counts, after_counts):
            target.owner[target.field] = text
            continue
        changed_blocks += 1

    if not changed_blocks:
        return _unchanged(
            original,
            "no_savings" if eligible else "no_eligible_result",
            before_bytes=before_bytes,
            counters=counters,
            serialized=before_serialized,
        )
    try:
        after_serialized = _request_json(changed)
        before_tokens = _count(before_serialized, counters)
        after_tokens = _count(after_serialized, counters)
    except Exception:
        return _unchanged(original, "counter_unavailable", before_bytes=before_bytes)
    return CompactionResult(
        payload=changed,
        changed=True,
        reason="compacted",
        changed_blocks=changed_blocks,
        before_bytes=before_bytes,
        after_bytes=len(after_serialized.encode("utf-8")),
        before_tokens=before_tokens,
        after_tokens=after_tokens,
    )


def protocol_calls(payload: Mapping[str, object], protocol: Protocol) -> dict[str, tuple[str, ...]]:
    copy_value = copy.deepcopy(dict(payload))
    _targets, calls, _supported = _targets_and_calls(copy_value, protocol)
    return calls


def _compact_json_table(text: str) -> str:
    rows = strict_json_loads(text)
    if canonical_json(rows) != text or not isinstance(rows, list) or not 2 <= len(rows) <= MAX_ROWS:
        return text
    if not all(isinstance(row, dict) for row in rows):
        return text
    typed_rows = cast(list[dict[str, object]], rows)
    columns = list(typed_rows[0])
    if not 1 <= len(columns) <= MAX_COLUMNS or any(list(row) != columns for row in typed_rows):
        return text
    return canonical_json(
        {
            "format": JSON_TABLE,
            "columns": columns,
            "rows": [[row[key] for key in columns] for row in typed_rows],
        }
    )


def _compact_line_runs(text: str) -> str:
    lines = text.split("\n")
    if not 2 <= len(lines) <= MAX_ROWS:
        return text
    runs: list[list[object]] = []
    for line in lines:
        if runs and runs[-1][0] == line:
            runs[-1][1] = cast(int, runs[-1][1]) + 1
        else:
            runs.append([line, 1])
    if len(runs) == len(lines):
        return text
    return canonical_json({"format": LINE_RUNS, "runs": runs})


def _compact_search_lines(text: str) -> str:
    if "\r" in text:
        return text
    lines, trailing = _lines_and_trailing(text)
    if 2 <= len(lines) <= MAX_ROWS:
        parsed = [_SEARCH_LINE.fullmatch(line) for line in lines]
        if all(match is not None for match in parsed):
            matches = cast(list[re.Match[str]], parsed)
            path = matches[0].group(1)
            if all(match.group(1) == path for match in matches[1:]):
                return canonical_json(
                    {
                        "format": SEARCH_LINES,
                        "path": path,
                        "matches": [[match.group(2), match.group(3)] for match in matches],
                        "trailing_newline": trailing,
                    }
                )
    return _compact_framed_lines(text, format="search_lines")


def _compact_path_list(text: str) -> str:
    if "\r" in text:
        return text
    lines, trailing = _lines_and_trailing(text)
    parsed = [_path_parts(line) for line in lines]
    if 2 <= len(lines) <= MAX_ROWS and all(parts is not None for parts in parsed):
        parts = cast(list[tuple[str, str]], parsed)
        prefix = parts[0][0]
        if all(part[0] == prefix for part in parts[1:]):
            return canonical_json(
                {
                    "format": PATH_LIST,
                    "prefix": prefix,
                    "suffixes": [part[1] for part in parts],
                    "trailing_newline": trailing,
                }
            )
    return _compact_framed_lines(text, format="path_list")


def _compact_framed_lines(text: str, *, format: Literal["search_lines", "path_list"]) -> str:
    segments = text.split("\n")
    raw_lines = [line + "\n" for line in segments[:-1]]
    if segments[-1]:
        raw_lines.append(segments[-1])
    if not 2 <= len(raw_lines) <= MAX_ROWS:
        return text
    values: list[tuple[str, str | list[str]] | None] = []
    for raw in raw_lines:
        terminated = raw.endswith("\n")
        line = raw[:-1] if terminated else raw
        if "\r" in line:
            values.append(None)
            continue
        if format == "path_list":
            parts = _path_parts(line)
            values.append((parts[0], parts[1]) if parts is not None else None)
        else:
            match = _SEARCH_LINE.fullmatch(line)
            values.append(
                (match.group(1), [match.group(2), match.group(3)])
                if match is not None
                else None
            )

    best: tuple[int, int] | None = None
    best_score = -1
    start = 0
    while start < len(values):
        current = values[start]
        if current is None:
            start += 1
            continue
        end = start + 1
        while end < len(values):
            next_value = values[end]
            if next_value is None or next_value[0] != current[0]:
                break
            end += 1
        if end - start >= 2:
            score = len(current[0].encode("utf-8")) * (end - start - 1)
            if score > best_score:
                best = (start, end)
                best_score = score
        start = end
    if best is None:
        return text

    start, end = best
    before = "".join(raw_lines[:start])
    last_terminated = raw_lines[end - 1].endswith("\n")
    after = ("\n" if last_terminated else "") + "".join(raw_lines[end:])
    if not before and not after:
        return text
    selected = cast(list[tuple[str, str | list[str]]], values[start:end])
    if format == "path_list":
        envelope = {
            "format": PATH_LIST,
            "before": before,
            "prefix": selected[0][0],
            "suffixes": [part[1] for part in selected],
            "after": after,
        }
    else:
        envelope = {
            "format": SEARCH_LINES,
            "before": before,
            "path": selected[0][0],
            "matches": [part[1] for part in selected],
            "after": after,
        }
    return canonical_json(envelope)


def _path_parts(line: str) -> tuple[str, str] | None:
    if not line or "\r" in line or "\n" in line:
        return None
    parent, separator, suffix = line.rpartition("/")
    if not separator or not parent or not suffix:
        return None
    return parent + separator, suffix


def _lines_and_trailing(text: str) -> tuple[list[str], bool]:
    lines = text.split("\n")
    trailing = bool(lines and lines[-1] == "")
    if trailing:
        lines.pop()
    return lines, trailing


def _validate_selections(selections: Sequence[Selection]) -> None:
    if len(selections) > MAX_SELECTIONS:
        raise CompactionConfigError("too_many_selections")
    seen: set[str] = set()
    for selection in selections:
        if (
            not isinstance(selection, Selection)
            or not isinstance(selection.result_id, str)
            or not 1 <= len(selection.result_id) <= 128
            or selection.format not in _FORMATS
        ):
            raise CompactionConfigError("invalid_selection")
        if selection.result_id in seen:
            raise CompactionConfigError("duplicate_selection")
        seen.add(selection.result_id)


def _targets_and_calls(
    payload: dict[str, object], protocol: Protocol
) -> tuple[list[_Target], dict[str, tuple[str, ...]], bool]:
    targets: list[_Target] = []
    call_names: dict[str, list[str]] = {}
    key = "input" if protocol == "responses" else "messages"
    items = payload.get(key)
    if not isinstance(items, list):
        return targets, {}, False
    for item in items:
        if not isinstance(item, dict):
            return [], {}, False
        if protocol == "responses":
            if item.get("type") == "function_call":
                _append_call(call_names, item.get("call_id"), item.get("name"))
            elif item.get("type") == "function_call_output":
                _append_target(targets, item.get("call_id"), item, "output", item)
        elif protocol == "chat":
            if item.get("role") == "assistant" and "tool_calls" in item:
                calls = item["tool_calls"]
                if not isinstance(calls, list):
                    return [], {}, False
                for call in calls:
                    if not isinstance(call, dict):
                        return [], {}, False
                    function = call.get("function")
                    if isinstance(function, dict):
                        _append_call(call_names, call.get("id"), function.get("name"))
            elif item.get("role") == "tool":
                _append_target(targets, item.get("tool_call_id"), item, "content", item)
        else:
            blocks = item.get("content")
            if not isinstance(blocks, list):
                continue
            if item.get("role") == "assistant":
                for block in blocks:
                    if not isinstance(block, dict):
                        return [], {}, False
                    if block.get("type") == "tool_use":
                        _append_call(call_names, block.get("id"), block.get("name"))
            elif item.get("role") == "user":
                for block in blocks:
                    if not isinstance(block, dict):
                        return [], {}, False
                    if block.get("type") == "tool_result":
                        _append_target(targets, block.get("tool_use_id"), block, "content", block)
    return targets, {key: tuple(value) for key, value in call_names.items()}, True


def _append_call(calls: dict[str, list[str]], result_id: object, name: object) -> None:
    if isinstance(result_id, str) and isinstance(name, str):
        calls.setdefault(result_id, []).append(name)


def _append_target(
    targets: list[_Target], result_id: object, owner: dict[str, object], field: str, item: dict[str, object]
) -> None:
    if isinstance(result_id, str) and isinstance(owner.get(field), str):
        targets.append(_Target(result_id, owner, field, item))


def _candidate_saves(
    before: str, after: str, before_counts: Mapping[str, int], after_counts: Mapping[str, int]
) -> bool:
    if len(after.encode("utf-8")) > len(before.encode("utf-8")):
        return False
    for name, count in before_counts.items():
        saved = count - after_counts[name]
        if saved < MIN_TOKEN_SAVINGS or saved * 100 < count * MIN_PERCENT_SAVINGS:
            return False
    return True


def _count(value: str, counters: Mapping[str, Callable[[str], int]]) -> dict[str, int]:
    result: dict[str, int] = {}
    for name, counter in counters.items():
        count = counter(value)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("invalid counter result")
        result[name] = count
    return result


def _request_json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), allow_nan=False)


def _unchanged(
    payload: dict[str, object],
    reason: str,
    *,
    measured: bool = True,
    before_bytes: int | None = None,
    counters: Mapping[str, Callable[[str], int]] | None = None,
    serialized: str | None = None,
) -> CompactionResult:
    counts: dict[str, int] = {}
    if measured and serialized is None:
        try:
            serialized = _request_json(payload)
        except (TypeError, ValueError, OverflowError, RecursionError):
            measured = False
    if measured and serialized is not None:
        before_bytes = len(serialized.encode("utf-8")) if before_bytes is None else before_bytes
        if counters:
            try:
                counts = _count(serialized, counters)
            except Exception:
                counts = {}
    else:
        before_bytes = None
    return CompactionResult(
        payload=copy.deepcopy(payload),
        changed=False,
        reason=reason,
        changed_blocks=0,
        before_bytes=before_bytes,
        after_bytes=before_bytes,
        before_tokens=counts,
        after_tokens=dict(counts),
    )
