"""Bounded local request characteristics; never retains input or infers success."""
from __future__ import annotations

import hashlib
import json
import re

REQUEST_KINDS = frozenset({"interactive", "retry", "automatic_retry", "poll", "polling", "scheduled", "history", "continuation", "legitimate_iteration"})


def request_characteristics(value):
    """Conservative, deterministic hints from the latest sanitized user input.

    The caller owns redaction. Only fixed labels and length buckets are returned;
    an ambiguous instruction stays unknown. No embeddings or provider call occur.
    """
    pieces = []
    observed_size = 0
    def collect(item, depth=0):
        nonlocal observed_size
        if depth > 12 or len(pieces) >= 32:
            return
        if isinstance(item, str):
            observed_size += len(item)
            pieces.append(item[:8192])
        elif isinstance(item, list):
            for child in item[-32:]:
                collect(child, depth + 1)
        elif isinstance(item, dict):
            if item.get("type") in {"text", "input_text"}:
                collect(item.get("text"), depth + 1)
            elif item.get("role") == "user":
                collect(item.get("content"), depth + 1)
    if isinstance(value.get("messages"), list):
        users = [item for item in value["messages"][-64:] if isinstance(item, dict) and item.get("role") == "user"]
        if users:
            collect(users[-1])
    else:
        input_value = value.get("input")
        if isinstance(input_value, list):
            users = [item for item in input_value[-64:] if isinstance(item, dict) and item.get("role") == "user"]
            collect(users[-1] if users else input_value)
        else:
            collect(input_value)
    text = " ".join(pieces)[:8192].lstrip().lower()
    leading = text[:512]
    kind = "unknown"
    if re.match(r"(?:please\s+)?(?:summarize|summarise)\b", leading):
        kind = "summarization"
    elif re.match(r"(?:please\s+)?translate\b", leading) and re.search(r"\b(?:into|to|from)\b", leading):
        kind = "translation"
    elif re.match(r"(?:please\s+)?(?:extract|parse)\b", leading) and re.search(r"\b(?:json|fields|table|csv)\b", leading):
        kind = "structured_extraction"
    elif re.match(r"(?:please\s+)?(?:fix|implement|refactor|debug|add)\b", leading) and re.search(r"\b(?:code|function|class|test|tests|bug|repository|repo|python|typescript|javascript)\b", text):
        kind = "code_change"
    elif re.match(r"(?:please\s+)?review\b", leading) and re.search(r"\b(?:code|diff|pull request|function|security|bug)\b", text):
        kind = "code_review"
    total = observed_size
    messages = value.get("messages", [])
    messages = messages if isinstance(messages, list) else []
    formatting = value.get("text", {})
    return {"version": "characteristics.v1", "task_hint": kind,
            "confidence": "unknown" if kind == "unknown" else "heuristic",
            "input_size": "small" if total <= 1024 else "medium" if total <= 8192 else "large",
            "tools": bool(value.get("tools")),
            "structured_output": bool(value.get("response_format") or (formatting.get("format") if isinstance(formatting, dict) else None)),
            "history": any(isinstance(item, dict) and item.get("role") in {"assistant", "tool"}
                           for item in messages)}


def context_signature(characteristics, task_type, completion_condition):
    dimensions = ["local-context.v1", task_type, completion_condition, characteristics]
    return hashlib.sha256(json.dumps(dimensions, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
