from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .config import DLPControls, DLPRuleConfig, SecretControls


REPLACEMENT = "[REDACTED:HORMUZ_SECRET]"
DLP_REPLACEMENT = "[REDACTED:HORMUZ_DLP]"
MAX_JSON_DEPTH = 100


class RedactionError(ValueError):
    pass


@dataclass(frozen=True)
class DLPFinding:
    rule_id: str
    category: str
    confidence: str
    action: str
    count: int
    origin: str = "dlp"

    def to_dict(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "category": self.category,
            "confidence": self.confidence,
            "action": self.action,
            "count": self.count,
        }


@dataclass(frozen=True)
class RedactionResult:
    value: dict[str, Any]
    count: int = 0
    rules: tuple[str, ...] = ()
    findings: tuple[DLPFinding, ...] = ()
    action: str = "allow"
    redaction_count: int = 0
    policy_version: str = "legacy-secret-v1"


_BUILTIN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
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
    ("hormuz_session_credential", re.compile(r"\bhox_[ar]_[A-Za-z0-9_-]{32,}\b")),
)

_US_SSN = re.compile(
    r"(?<![0-9])(?!000|666|9[0-9][0-9])([0-9]{3})-"
    r"(?!00)([0-9]{2})-(?!0000)([0-9]{4})(?![0-9])"
)
_PAYMENT_CARD_CANDIDATE = re.compile(
    r"(?<![0-9])(?:[0-9][ -]?){12,18}[0-9](?![0-9])"
)
_EMAIL_ADDRESS = re.compile(
    r"(?<![A-Za-z0-9.!#$%&'*+/=?^_`{|}~-])"
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+"
    r"(?![A-Za-z0-9-])"
)
_ACTION_PRECEDENCE = {
    "allow": 0,
    "detect": 1,
    "redact": 2,
    "require_approval": 3,
    "deny": 4,
}


class SecretRedactor:
    def __init__(
        self,
        controls: SecretControls,
        protected_values: tuple[tuple[str, str], ...] = (),
        dlp_controls: DLPControls | None = None,
    ):
        self.controls = controls
        self.protected_values = controls.custom_secret_values + protected_values
        self.dlp_controls = dlp_controls or DLPControls()

    def inspect(
        self,
        value: dict[str, Any],
        *,
        protocol: str = "",
        model: str = "",
    ) -> RedactionResult:
        if self.controls.mode == "off" and not self.dlp_controls.rules:
            return RedactionResult(
                value=value,
                policy_version=self.dlp_controls.policy_version,
            )
        transformed, findings, redaction_count = self._transform(
            value,
            depth=0,
            protocol=protocol,
            model=model,
        )
        assert isinstance(transformed, dict)
        result_findings = tuple(
            DLPFinding(
                rule_id=key[0],
                category=key[1],
                confidence=key[2],
                action=key[3],
                count=count,
                origin=key[4],
            )
            for key, count in sorted(findings.items())
        )
        action = max(
            (finding.action for finding in result_findings),
            key=lambda item: _ACTION_PRECEDENCE[item],
            default="allow",
        )
        return RedactionResult(
            value=transformed,
            count=sum(finding.count for finding in result_findings),
            rules=tuple(sorted({finding.rule_id for finding in result_findings})),
            findings=result_findings,
            action=action,
            redaction_count=redaction_count,
            policy_version=self.dlp_controls.policy_version,
        )

    def _transform(
        self,
        value: Any,
        *,
        depth: int,
        protocol: str,
        model: str,
    ) -> tuple[Any, dict[tuple[str, str, str, str, str], int], int]:
        if depth > MAX_JSON_DEPTH:
            raise RedactionError(f"Request JSON exceeds the maximum nesting depth of {MAX_JSON_DEPTH}")
        if isinstance(value, str):
            return self._transform_string(value, protocol=protocol, model=model)
        if isinstance(value, list):
            transformed_items: list[Any] = []
            findings: dict[tuple[str, str, str, str, str], int] = {}
            redaction_count = 0
            for item in value:
                transformed, item_findings, item_redactions = self._transform(
                    item,
                    depth=depth + 1,
                    protocol=protocol,
                    model=model,
                )
                transformed_items.append(transformed)
                _merge_findings(findings, item_findings)
                redaction_count += item_redactions
            return transformed_items, findings, redaction_count
        if isinstance(value, dict):
            transformed_object: dict[str, Any] = {}
            findings: dict[tuple[str, str, str, str, str], int] = {}
            redaction_count = 0
            for key, item in value.items():
                transformed, item_findings, item_redactions = self._transform(
                    item,
                    depth=depth + 1,
                    protocol=protocol,
                    model=model,
                )
                transformed_object[key] = transformed
                _merge_findings(findings, item_findings)
                redaction_count += item_redactions
            return transformed_object, findings, redaction_count
        return value, {}, 0

    def _transform_string(
        self,
        value: str,
        *,
        protocol: str,
        model: str,
    ) -> tuple[str, dict[tuple[str, str, str, str, str], int], int]:
        transformed = value
        findings: dict[tuple[str, str, str, str, str], int] = {}
        redaction_count = 0
        if self.controls.mode != "off":
            secret_action = self.controls.mode
            for rule_name, secret in self.protected_values:
                count = transformed.count(secret)
                if count:
                    transformed = transformed.replace(secret, REPLACEMENT)
                    redaction_count += count
                    _add_finding(
                        findings,
                        rule_name,
                        "credential",
                        "high",
                        secret_action,
                        count,
                        origin="secret",
                    )
            if self.controls.builtins:
                for name, pattern in _BUILTIN_PATTERNS:
                    transformed, count = pattern.subn(REPLACEMENT, transformed)
                    if count:
                        redaction_count += count
                        _add_finding(
                            findings,
                            name,
                            "credential",
                            "high",
                            secret_action,
                            count,
                            origin="secret",
                        )

        for rule in self.dlp_controls.rules:
            if protocol and not rule.applies_to(protocol=protocol, model=model):
                continue
            count = _rule_match_count(rule, value)
            if not count:
                continue
            _add_finding(
                findings,
                rule.rule_id,
                rule.category,
                rule.confidence,
                rule.action,
                count,
                origin="dlp",
            )
            if rule.action == "redact":
                transformed, applied = _redact_rule(rule, transformed)
                redaction_count += applied
        return transformed, findings, redaction_count


def _rule_match_count(rule: DLPRuleConfig, value: str) -> int:
    if rule.rule_id == "us_ssn":
        return sum(1 for _ in _US_SSN.finditer(value))
    if rule.rule_id == "payment_card":
        return sum(1 for match in _PAYMENT_CARD_CANDIDATE.finditer(value) if _valid_pan(match.group(0)))
    if rule.rule_id == "email_address":
        return sum(1 for _ in _EMAIL_ADDRESS.finditer(value))
    return sum(value.count(item) for item in rule.exact_values)


def _redact_rule(rule: DLPRuleConfig, value: str) -> tuple[str, int]:
    if rule.rule_id == "us_ssn":
        return _US_SSN.subn(DLP_REPLACEMENT, value)
    if rule.rule_id == "payment_card":
        return _replace_valid_cards(value)
    if rule.rule_id == "email_address":
        return _EMAIL_ADDRESS.subn(DLP_REPLACEMENT, value)
    transformed = value
    total = 0
    for item in rule.exact_values:
        count = transformed.count(item)
        if count:
            transformed = transformed.replace(item, DLP_REPLACEMENT)
            total += count
    return transformed, total


def _replace_valid_cards(value: str) -> tuple[str, int]:
    parts: list[str] = []
    cursor = 0
    count = 0
    for match in _PAYMENT_CARD_CANDIDATE.finditer(value):
        if not _valid_pan(match.group(0)):
            continue
        parts.append(value[cursor:match.start()])
        parts.append(DLP_REPLACEMENT)
        cursor = match.end()
        count += 1
    if not count:
        return value, 0
    parts.append(value[cursor:])
    return "".join(parts), count


def _valid_pan(value: str) -> bool:
    digits = "".join(character for character in value if "0" <= character <= "9")
    if not 13 <= len(digits) <= 19 or len(set(digits)) == 1:
        return False
    total = 0
    parity = len(digits) % 2
    for index, character in enumerate(digits):
        number = int(character)
        if index % 2 == parity:
            number *= 2
            if number > 9:
                number -= 9
        total += number
    return total % 10 == 0


def _add_finding(
    target: dict[tuple[str, str, str, str, str], int],
    rule_id: str,
    category: str,
    confidence: str,
    action: str,
    count: int,
    *,
    origin: str,
) -> None:
    key = (rule_id, category, confidence, action, origin)
    target[key] = target.get(key, 0) + count


def _merge_findings(
    target: dict[tuple[str, str, str, str, str], int],
    source: dict[tuple[str, str, str, str, str], int],
) -> None:
    for key, count in source.items():
        target[key] = target.get(key, 0) + count
