"""Bounded, lossless context representation formats.

This module deliberately has no tokenizer, network, storage, or gateway
dependency.  The client uses it to verify candidates and the gateway uses the
same decoders to preserve secret inspection across compact representations.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


TRANSFORM_VERSION = "structural-v1"
MAX_BLOCK_BYTES = 64 * 1024
MAX_REQUEST_BYTES = 1024 * 1024
MAX_ROWS = 4_096
MAX_COLUMNS = 64
MAX_DEPTH = 32
MAX_NODES = 50_000

JSON_TABLE = "hormuz-json-table-v1"
LINE_RUNS = "hormuz-line-runs-v1"
SEARCH_LINES = "hormuz-search-lines-v1"
PATH_LIST = "hormuz-path-list-v1"
KNOWN_FORMATS = frozenset({JSON_TABLE, LINE_RUNS, SEARCH_LINES, PATH_LIST})
_DECLARED_MARKER = re.compile(
    r'^\s*\{.*"format"\s*:\s*"(?:'
    + "|".join(re.escape(value) for value in sorted(KNOWN_FORMATS))
    + r')"',
    re.DOTALL,
)


class CompactionFormatError(ValueError):
    """A recognized compact envelope is malformed or unsafe to expand."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class DecodedText:
    recognized: bool
    text: str
    format: str | None = None


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def strict_json_loads(text: str) -> object:
    try:
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError, ValueError) as error:
        raise CompactionFormatError("invalid_json") from error
    validate_tree(value)
    return value


def validate_tree(value: object, *, max_depth: int = MAX_DEPTH, max_nodes: int = MAX_NODES) -> None:
    nodes = 0
    pending: list[tuple[object, int]] = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if nodes > max_nodes:
            raise CompactionFormatError("node_limit_exceeded")
        if depth > max_depth:
            raise CompactionFormatError("depth_limit_exceeded")
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)


def decode_text(value: str, *, max_output_bytes: int = MAX_BLOCK_BYTES) -> DecodedText:
    """Decode a recognized canonical envelope; leave ordinary text untouched."""

    try:
        parsed = strict_json_loads(value)
    except CompactionFormatError as error:
        # Invalid JSON is ordinary tool output.  A JSON object declaring one of
        # our markers is handled below and cannot reach this branch.
        if _DECLARED_MARKER.search(value):
            raise CompactionFormatError("malformed_compaction_envelope") from error
        return DecodedText(False, value)
    if not isinstance(parsed, dict) or parsed.get("format") not in KNOWN_FORMATS:
        return DecodedText(False, value)
    if canonical_json(parsed) != value:
        raise CompactionFormatError("noncanonical_compaction_envelope")
    marker = parsed["format"]
    if marker == JSON_TABLE:
        result = _decode_json_table(parsed)
    elif marker == LINE_RUNS:
        result = _decode_line_runs(parsed, max_output_bytes=max_output_bytes)
    elif marker == SEARCH_LINES:
        result = _decode_search_lines(parsed)
    else:
        result = _decode_path_list(parsed)
    if len(result.encode("utf-8")) > max_output_bytes:
        raise CompactionFormatError("expanded_block_too_large")
    return DecodedText(True, result, marker)


def restore_text(value: str) -> str:
    return decode_text(value).text


def _decode_json_table(value: dict[str, Any]) -> str:
    _require_keys(value, {"format", "columns", "rows"})
    columns = value["columns"]
    rows = value["rows"]
    if (
        not isinstance(columns, list)
        or not 1 <= len(columns) <= MAX_COLUMNS
        or any(not isinstance(column, str) or not column for column in columns)
        or len(set(columns)) != len(columns)
        or not isinstance(rows, list)
        or not 2 <= len(rows) <= MAX_ROWS
    ):
        raise CompactionFormatError("invalid_json_table")
    restored: list[dict[str, object]] = []
    for row in rows:
        if not isinstance(row, list) or len(row) != len(columns):
            raise CompactionFormatError("invalid_json_table")
        restored.append(dict(zip(columns, row, strict=True)))
    try:
        return canonical_json(restored)
    except (TypeError, ValueError, OverflowError) as error:
        raise CompactionFormatError("invalid_json_table") from error


def _decode_line_runs(value: dict[str, Any], *, max_output_bytes: int) -> str:
    _require_keys(value, {"format", "runs"})
    runs = value["runs"]
    if not isinstance(runs, list) or not 1 <= len(runs) <= MAX_ROWS:
        raise CompactionFormatError("invalid_line_runs")
    lines: list[str] = []
    byte_upper_bound = 0
    for run in runs:
        if (
            not isinstance(run, list)
            or len(run) != 2
            or not isinstance(run[0], str)
            or isinstance(run[1], bool)
            or not isinstance(run[1], int)
            or run[1] <= 0
        ):
            raise CompactionFormatError("invalid_line_runs")
        count = run[1]
        if len(lines) + count > MAX_ROWS:
            raise CompactionFormatError("expanded_rows_exceeded")
        byte_upper_bound += len(run[0].encode("utf-8")) * count + count
        if byte_upper_bound > max_output_bytes + 1:
            raise CompactionFormatError("expanded_block_too_large")
        lines.extend([run[0]] * count)
    return "\n".join(lines)


def _decode_search_lines(value: dict[str, Any]) -> str:
    legacy = set(value) == {"format", "path", "matches", "trailing_newline"}
    framed = set(value) == {"format", "before", "path", "matches", "after"}
    if not legacy and not framed:
        raise CompactionFormatError("unknown_compaction_field")
    path = value["path"]
    matches = value["matches"]
    before = "" if legacy else value["before"]
    after = ("\n" if value["trailing_newline"] else "") if legacy else value["after"]
    if (
        not isinstance(path, str)
        or not path
        or any(character in path for character in ":\r\n")
        or not isinstance(matches, list)
        or not 2 <= len(matches) <= MAX_ROWS
        or not isinstance(before, str)
        or not isinstance(after, str)
        or (legacy and not isinstance(value["trailing_newline"], bool))
        or (framed and not before and not after)
    ):
        raise CompactionFormatError("invalid_search_lines")
    lines: list[str] = []
    for match in matches:
        if (
            not isinstance(match, list)
            or len(match) != 2
            or not isinstance(match[0], str)
            or not match[0].isdigit()
            or not isinstance(match[1], str)
            or "\r" in match[1]
            or "\n" in match[1]
        ):
            raise CompactionFormatError("invalid_search_lines")
        lines.append(f"{path}:{match[0]}:{match[1]}")
    return before + "\n".join(lines) + after


def _decode_path_list(value: dict[str, Any]) -> str:
    legacy = set(value) == {"format", "prefix", "suffixes", "trailing_newline"}
    framed = set(value) == {"format", "before", "prefix", "suffixes", "after"}
    if not legacy and not framed:
        raise CompactionFormatError("unknown_compaction_field")
    prefix = value["prefix"]
    suffixes = value["suffixes"]
    before = "" if legacy else value["before"]
    after = ("\n" if value["trailing_newline"] else "") if legacy else value["after"]
    if (
        not isinstance(prefix, str)
        or not prefix
        or not prefix.endswith("/")
        or any(character in prefix for character in "\r\n")
        or not isinstance(suffixes, list)
        or not 2 <= len(suffixes) <= MAX_ROWS
        or not isinstance(before, str)
        or not isinstance(after, str)
        or (legacy and not isinstance(value["trailing_newline"], bool))
        or (framed and not before and not after)
        or any(
            not isinstance(suffix, str)
            or not suffix
            or any(character in suffix for character in "/\r\n")
            for suffix in suffixes
        )
    ):
        raise CompactionFormatError("invalid_path_list")
    return before + "\n".join(prefix + suffix for suffix in suffixes) + after


def _require_keys(value: dict[str, Any], expected: set[str]) -> None:
    if set(value) != expected:
        raise CompactionFormatError("unknown_compaction_field")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise ValueError(f"nonfinite number: {value}")
