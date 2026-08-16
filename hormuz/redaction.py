from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from typing import Any

from .config import DLPControls, DLPRuleConfig, SecretControls


REPLACEMENT = "[REDACTED:HORMUZ_SECRET]"
DLP_REPLACEMENT = "[REDACTED:HORMUZ_DLP]"
MAX_JSON_DEPTH = 100
MAX_ENCODED_TEXT_BYTES = 1024 * 1024
MAX_ENCODED_TEXT_DEPTH = 3


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


@dataclass(frozen=True)
class _EncodedText:
    decoded: str
    prefix: str
    urlsafe: bool
    padded: bool

    def render(self, value: str) -> str:
        raw = value.encode("utf-8")
        if self.urlsafe:
            encoded = base64.urlsafe_b64encode(raw).decode("ascii")
        else:
            encoded = base64.b64encode(raw).decode("ascii")
        if not self.padded:
            encoded = encoded.rstrip("=")
        return self.prefix + encoded


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
_OPAQUE_MEDIA_RULE_ID = "opaque_media"


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
        _validate_json_depth(value, depth=0)
        opaque_media_count, opaque_object_ids = _opaque_media_scan(
            value,
            protocol=protocol,
        )
        if opaque_media_count:
            opaque_rule = next(
                (
                    rule
                    for rule in self.dlp_controls.rules
                    if rule.rule_id == _OPAQUE_MEDIA_RULE_ID
                    and rule.applies_to(protocol=protocol, model=model)
                ),
                None,
            )
            if opaque_rule is not None and opaque_rule.action != "off":
                finding = DLPFinding(
                    rule_id=opaque_rule.rule_id,
                    category=opaque_rule.category,
                    confidence=opaque_rule.confidence,
                    action=opaque_rule.action,
                    count=opaque_media_count,
                )
                return RedactionResult(
                    value=value,
                    count=opaque_media_count,
                    rules=(opaque_rule.rule_id,),
                    findings=(finding,),
                    action=opaque_rule.action,
                    policy_version=self.dlp_controls.policy_version,
                )
        transformed, findings, redaction_count = self._transform(
            value,
            depth=0,
            protocol=protocol,
            model=model,
            opaque_object_ids=opaque_object_ids,
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
        opaque_object_ids: frozenset[int],
    ) -> tuple[Any, dict[tuple[str, str, str, str, str], int], int]:
        if depth > MAX_JSON_DEPTH:
            raise RedactionError(f"Request JSON exceeds the maximum nesting depth of {MAX_JSON_DEPTH}")
        if isinstance(value, dict) and id(value) in opaque_object_ids:
            return value, {}, 0
        if isinstance(value, str):
            return self._transform_string(
                value,
                protocol=protocol,
                model=model,
                encoded_depth=0,
            )
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
                    opaque_object_ids=opaque_object_ids,
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
                if isinstance(key, str):
                    _, key_findings, _ = self._transform_string(
                        key,
                        protocol=protocol,
                        model=model,
                        encoded_depth=0,
                    )
                    _merge_findings(
                        findings,
                        _fail_closed_key_redactions(key_findings),
                    )
                transformed, item_findings, item_redactions = self._transform(
                    item,
                    depth=depth + 1,
                    protocol=protocol,
                    model=model,
                    opaque_object_ids=opaque_object_ids,
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
        encoded_depth: int,
    ) -> tuple[str, dict[tuple[str, str, str, str, str], int], int]:
        plain_result = self._transform_plain_string(
            value,
            protocol=protocol,
            model=model,
        )
        if plain_result[1]:
            return plain_result

        encoded = _decode_encoded_text(value)
        if encoded is not None:
            if encoded_depth >= MAX_ENCODED_TEXT_DEPTH:
                raise RedactionError(
                    "Encoded text exceeds the maximum nesting depth of "
                    f"{MAX_ENCODED_TEXT_DEPTH}"
                )
            transformed, findings, redaction_count = self._transform_string(
                encoded.decoded,
                protocol=protocol,
                model=model,
                encoded_depth=encoded_depth + 1,
            )
            if redaction_count:
                transformed = encoded.render(transformed)
            else:
                transformed = value
            return transformed, findings, redaction_count

        return plain_result

    def _transform_plain_string(
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


def _decode_encoded_text(value: str) -> _EncodedText | None:
    prefix = ""
    payload = value
    data_uri = _text_data_uri(value)
    if data_uri is not None:
        prefix, payload = data_uri
    elif not _looks_like_base64(payload):
        return None

    if len(payload) < 16 or len(payload) % 4 == 1:
        return None
    if len(payload) > ((MAX_ENCODED_TEXT_BYTES + 2) // 3) * 4:
        raise RedactionError(
            "Encoded text exceeds the maximum decoded size of "
            f"{MAX_ENCODED_TEXT_BYTES} bytes"
        )

    urlsafe = "-" in payload or "_" in payload
    padded = payload.endswith("=")
    padding = "=" * (-len(payload) % 4)
    try:
        decoded_bytes = base64.b64decode(
            (payload + padding).encode("ascii"),
            altchars=b"-_" if urlsafe else None,
            validate=True,
        )
    except (UnicodeEncodeError, binascii.Error, ValueError):
        return None
    if len(decoded_bytes) > MAX_ENCODED_TEXT_BYTES:
        raise RedactionError(
            "Encoded text exceeds the maximum decoded size of "
            f"{MAX_ENCODED_TEXT_BYTES} bytes"
        )
    try:
        decoded = decoded_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if not _is_textual(decoded):
        return None
    return _EncodedText(
        decoded=decoded,
        prefix=prefix,
        urlsafe=urlsafe,
        padded=padded,
    )


def _text_data_uri(value: str) -> tuple[str, str] | None:
    if not value[:5].lower() == "data:":
        return None
    metadata, separator, payload = value[5:].partition(",")
    if not separator:
        return None
    parts = metadata.split(";")
    if not parts or parts[-1].lower() != "base64":
        return None
    media_type = parts[0].lower() or "text/plain"
    if not _is_text_media_type(media_type):
        return None
    return value[: len(value) - len(payload)], payload


def _is_text_media_type(media_type: str) -> bool:
    return (
        media_type.startswith("text/")
        or media_type in {
            "application/json",
            "application/javascript",
            "application/xml",
            "application/x-ndjson",
        }
        or media_type.endswith("+json")
        or media_type.endswith("+xml")
    )


def _looks_like_base64(value: str) -> bool:
    if len(value) < 16 or any(character.isspace() for character in value):
        return False
    return re.fullmatch(r"[A-Za-z0-9+/_-]*={0,2}", value) is not None


def _is_textual(value: str) -> bool:
    if not value or "\x00" in value:
        return False
    printable = sum(character.isprintable() or character.isspace() for character in value)
    return printable / len(value) >= 0.9


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


def _validate_json_depth(value: Any, *, depth: int) -> None:
    if depth > MAX_JSON_DEPTH:
        raise RedactionError(f"Request JSON exceeds the maximum nesting depth of {MAX_JSON_DEPTH}")
    if isinstance(value, list):
        for item in value:
            _validate_json_depth(item, depth=depth + 1)
    elif isinstance(value, dict):
        for item in value.values():
            _validate_json_depth(item, depth=depth + 1)


def _opaque_media_scan(
    value: dict[str, Any],
    *,
    protocol: str,
) -> tuple[int, frozenset[int]]:
    opaque_object_ids: set[int] = set()
    if protocol == "openai":
        count = _openai_input_count(value.get("input"), opaque_object_ids)
    elif protocol == "anthropic":
        count = _anthropic_content_count(value.get("system"), opaque_object_ids)
        messages = value.get("messages")
        if isinstance(messages, list):
            for message in messages:
                if isinstance(message, dict):
                    count += _anthropic_content_count(
                        message.get("content"),
                        opaque_object_ids,
                    )
    else:
        count = 0
    return count, frozenset(opaque_object_ids)


def _openai_input_count(value: Any, opaque_object_ids: set[int]) -> int:
    if isinstance(value, list):
        return sum(
            _openai_input_item_count(item, opaque_object_ids)
            for item in value
        )
    return _openai_input_item_count(value, opaque_object_ids)


def _openai_input_item_count(value: Any, opaque_object_ids: set[int]) -> int:
    if not isinstance(value, dict):
        return 0
    item_type = value.get("type")
    if item_type in {"input_image", "input_file", "computer_screenshot"}:
        opaque_object_ids.add(id(value))
        return 1
    if item_type == "computer_call_output":
        output = value.get("output")
        if isinstance(output, dict) and output.get("type") == "computer_screenshot":
            opaque_object_ids.add(id(output))
            return 1
        return 0
    if item_type in {"function_call_output", "custom_tool_call_output"}:
        return _openai_content_count(value.get("output"), opaque_object_ids)
    if item_type == "message" or "role" in value:
        return _openai_content_count(value.get("content"), opaque_object_ids)
    return 0


def _openai_content_count(value: Any, opaque_object_ids: set[int]) -> int:
    if isinstance(value, list):
        return sum(
            _openai_input_item_count(item, opaque_object_ids)
            for item in value
        )
    return _openai_input_item_count(value, opaque_object_ids)


def _anthropic_content_count(value: Any, opaque_object_ids: set[int]) -> int:
    if isinstance(value, list):
        return sum(
            _anthropic_block_count(item, opaque_object_ids)
            for item in value
        )
    return _anthropic_block_count(value, opaque_object_ids)


def _anthropic_block_count(value: Any, opaque_object_ids: set[int]) -> int:
    if not isinstance(value, dict):
        return 0
    block_type = value.get("type")
    if block_type == "image":
        opaque_object_ids.add(id(value))
        return 1
    if block_type == "document":
        source = value.get("source")
        if not isinstance(source, dict):
            opaque_object_ids.add(id(value))
            return 1
        source_type = source.get("type")
        if source_type == "text":
            return 0
        if source_type == "content":
            return _anthropic_content_count(
                source.get("content"),
                opaque_object_ids,
            )
        opaque_object_ids.add(id(value))
        return 1
    if block_type in {"file", "container_upload"}:
        opaque_object_ids.add(id(value))
        return 1
    if block_type in {"tool_result", "search_result", "web_search_tool_result"}:
        return _anthropic_content_count(value.get("content"), opaque_object_ids)
    return 0


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


def _fail_closed_key_redactions(
    findings: dict[tuple[str, str, str, str, str], int],
) -> dict[tuple[str, str, str, str, str], int]:
    result: dict[tuple[str, str, str, str, str], int] = {}
    for (rule_id, category, confidence, action, origin), count in findings.items():
        effective_action = "deny" if action == "redact" else action
        key = (rule_id, category, confidence, effective_action, origin)
        result[key] = result.get(key, 0) + count
    return result
