from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .config import SecretControls


REPLACEMENT = "[REDACTED:HORMUZ_SECRET]"
MAX_JSON_DEPTH = 100


class RedactionError(ValueError):
    pass


@dataclass(frozen=True)
class RedactionResult:
    value: dict[str, Any]
    count: int = 0
    rules: tuple[str, ...] = ()


_BUILTIN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("hormuz_console_credential", re.compile(r"(?<![A-Za-z0-9_-])hox_c(?:s|f)?_[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])")),
    ("hormuz_invitation_credential", re.compile(r"(?<![A-Za-z0-9_-])hox_i_[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])")),
    ("hormuz_session_credential", re.compile(r"(?<![A-Za-z0-9_-])hox_[ar]_[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])")),
    (
        "private_key",
        re.compile(
            r"-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----.*?"
            r"-----END (?:[A-Z0-9]+ )?PRIVATE KEY-----",
            re.DOTALL,
        ),
    ),
    ("anthropic_api_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("openai_api_key", re.compile(r"\bsk-(?:proj-|svcacct-)[A-Za-z0-9_-]{20,}\b")),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")),
    ("aws_access_key_id", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("google_api_key", re.compile(r"\bAIza[A-Za-z0-9_-]{35}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
)


class SecretRedactor:
    def __init__(
        self,
        controls: SecretControls,
        protected_values: tuple[tuple[str, str], ...] = (),
    ):
        self.controls = controls
        self.protected_values = controls.custom_secret_values + protected_values

    def inspect(self, value: dict[str, Any], *, mode: str | None = None) -> RedactionResult:
        """Inspect a request with an optional immutable policy action override.

        Detector configuration and protected values remain process-local; the
        active managed policy can only select the allowlisted enforcement mode.
        """

        effective_mode = self.controls.mode if mode is None else mode
        if effective_mode not in {"off", "redact", "deny"}:
            raise RedactionError("Secret enforcement mode is unsupported")
        if effective_mode == "off":
            return RedactionResult(value=value)
        transformed, count, rules = self._transform(value, depth=0)
        assert isinstance(transformed, dict)
        return RedactionResult(value=transformed, count=count, rules=tuple(sorted(rules)))

    def _transform(self, value: Any, *, depth: int) -> tuple[Any, int, set[str]]:
        if depth > MAX_JSON_DEPTH:
            raise RedactionError(f"Request JSON exceeds the maximum nesting depth of {MAX_JSON_DEPTH}")
        if isinstance(value, str):
            return self._transform_string(value)
        if isinstance(value, list):
            transformed_items: list[Any] = []
            total = 0
            rules: set[str] = set()
            for item in value:
                transformed, count, item_rules = self._transform(item, depth=depth + 1)
                transformed_items.append(transformed)
                total += count
                rules.update(item_rules)
            return transformed_items, total, rules
        if isinstance(value, dict):
            transformed_object: dict[str, Any] = {}
            total = 0
            rules: set[str] = set()
            for key, item in value.items():
                transformed, count, item_rules = self._transform(item, depth=depth + 1)
                transformed_object[key] = transformed
                total += count
                rules.update(item_rules)
            return transformed_object, total, rules
        return value, 0, set()

    def _transform_string(self, value: str) -> tuple[str, int, set[str]]:
        transformed = value
        total = 0
        rules: set[str] = set()
        for rule_name, secret in self.protected_values:
            count = transformed.count(secret)
            if count:
                transformed = transformed.replace(secret, REPLACEMENT)
                total += count
                rules.add(rule_name)
        if self.controls.builtins:
            for name, pattern in _BUILTIN_PATTERNS:
                transformed, count = pattern.subn(REPLACEMENT, transformed)
                if count:
                    total += count
                    rules.add(name)
        return transformed, total, rules
