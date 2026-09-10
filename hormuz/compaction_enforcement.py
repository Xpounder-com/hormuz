"""Gateway-side validation needed to preserve governance for compact inputs.

The gateway never selects or optimizes content.  It only reconstructs recognized
client encodings in bounded memory so existing secret controls see the same
plaintext they would have seen before compaction.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any, Literal

from .compaction_contract import (
    CONTEXT_FORMAT_HEADER,
    CONTEXT_FORMAT_VERSION,
    CONTEXT_FORMATS_HEADER,
)
from .compaction_formats import MAX_REQUEST_BYTES, CompactionFormatError, decode_text
from .redaction import RedactionResult, SecretRedactor


class CompactionEnforcementError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class CompactionInspection:
    redaction: RedactionResult
    recognized_blocks: int
    expanded_for_egress: bool


def inspect_request(
    payload: dict[str, Any],
    *,
    protocol: Literal["openai", "anthropic"],
    redactor: SecretRedactor,
    mode: str,
    declared_version: str | None,
) -> CompactionInspection:
    if declared_version not in {None, CONTEXT_FORMAT_VERSION}:
        raise CompactionEnforcementError("unsupported_compaction_version")
    reconstructed = copy.deepcopy(payload)
    try:
        recognized = _decode_eligible_fields(
            reconstructed,
            protocol=protocol,
        )
    except CompactionFormatError as error:
        raise CompactionEnforcementError(error.code) from error
    if declared_version is not None and recognized == 0:
        raise CompactionEnforcementError("declared_compaction_missing")
    if recognized == 0:
        return CompactionInspection(redactor.inspect(payload, mode=mode), 0, False)
    try:
        expanded_bytes = len(
            json.dumps(reconstructed, separators=(",", ":"), allow_nan=False).encode("utf-8")
        )
    except (TypeError, ValueError, OverflowError, RecursionError) as error:
        raise CompactionEnforcementError("invalid_compaction_request") from error
    if expanded_bytes > MAX_REQUEST_BYTES:
        raise CompactionEnforcementError("expanded_request_too_large")

    expanded_result = redactor.inspect(reconstructed, mode=mode)
    if expanded_result.count:
        # In redact mode this is the exact reconstructed, redacted payload.  In
        # deny mode the caller blocks egress and the value is never forwarded.
        return CompactionInspection(expanded_result, recognized, True)

    raw_result = redactor.inspect(payload, mode=mode)
    if raw_result.count:
        # A representation-only match should not corrupt the envelope.  The
        # already inspected expanded view is safe to send in redact mode.
        return CompactionInspection(
            RedactionResult(
                value=expanded_result.value,
                count=raw_result.count,
                rules=raw_result.rules,
            ),
            recognized,
            True,
        )
    return CompactionInspection(RedactionResult(value=payload), recognized, False)


def _decode_eligible_fields(
    payload: dict[str, Any],
    *,
    protocol: Literal["openai", "anthropic"],
) -> int:
    # The request-wide header promises at least one valid compact block. Other,
    # unselected tool results may legitimately contain marker-like text; keep
    # malformed candidates opaque and let the caller enforce the positive count.
    count = 0
    if protocol == "openai":
        items = payload.get("input")
        if not isinstance(items, list):
            return 0
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "function_call_output" or not isinstance(item.get("output"), str):
                continue
            try:
                decoded = decode_text(item["output"])
            except CompactionFormatError:
                continue
            if decoded.recognized:
                item["output"] = decoded.text
                count += 1
        return count

    messages = payload.get("messages")
    if not isinstance(messages, list):
        return 0
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        blocks = message.get("content")
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            if (
                not isinstance(block, dict)
                or block.get("type") != "tool_result"
                or not isinstance(block.get("content"), str)
            ):
                continue
            try:
                decoded = decode_text(block["content"])
            except CompactionFormatError:
                continue
            if decoded.recognized:
                block["content"] = decoded.text
                count += 1
    return count
