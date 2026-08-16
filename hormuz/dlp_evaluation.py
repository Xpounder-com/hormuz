from __future__ import annotations

import json
import os
import platform
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .config import DLPControls, DLPRuleConfig, SecretControls
from .redaction import DLP_DETECTOR_VERSION, RedactionError, SecretRedactor


RESULT_SCHEMA = "hormuz.dlp-evaluation.v1"
CORPUS_FORMAT = "hormuz.dlp-evaluation-corpus-jsonl.v1"
MAX_CORPUS_BYTES = 25 * 1024 * 1024
MAX_CASES = 10_000
_CORPUS_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")


class DLPEvaluationError(ValueError):
    pass


class _DuplicateJSONMember(ValueError):
    pass


class _NonStandardJSONConstant(ValueError):
    pass


@dataclass(frozen=True)
class DLPEvaluationCase:
    expected_match: bool
    payload: dict[str, Any] = field(repr=False)


def load_evaluation_corpus(path: str | Path) -> tuple[DLPEvaluationCase, ...]:
    try:
        source = Path(path).expanduser().resolve()
        size = source.stat().st_size
    except OSError as error:
        raise DLPEvaluationError("cannot read DLP evaluation corpus") from error
    if size > MAX_CORPUS_BYTES:
        raise DLPEvaluationError(
            f"DLP evaluation corpus cannot exceed {MAX_CORPUS_BYTES} bytes"
        )
    try:
        encoded = source.read_bytes()
        if len(encoded) > MAX_CORPUS_BYTES:
            raise DLPEvaluationError(
                f"DLP evaluation corpus cannot exceed {MAX_CORPUS_BYTES} bytes"
            )
        text = encoded.decode("utf-8")
    except UnicodeDecodeError as error:
        raise DLPEvaluationError("DLP evaluation corpus must be valid UTF-8") from error
    except OSError as error:
        raise DLPEvaluationError("cannot read DLP evaluation corpus") from error

    cases: list[DLPEvaluationCase] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        value = _strict_json(line, line_number=line_number)
        if not isinstance(value, dict):
            raise DLPEvaluationError(
                f"DLP evaluation case on line {line_number} must be an object"
            )
        if set(value) != {"payload", "expected_match"}:
            raise DLPEvaluationError(
                f"DLP evaluation case on line {line_number} must contain exactly "
                "payload and expected_match"
            )
        payload = value["payload"]
        expected_match = value["expected_match"]
        if not isinstance(payload, dict):
            raise DLPEvaluationError(
                f"DLP evaluation case payload must be an object on line {line_number}"
            )
        if not isinstance(expected_match, bool):
            raise DLPEvaluationError(
                f"DLP evaluation case expected_match must be boolean on line {line_number}"
            )
        if len(cases) >= MAX_CASES:
            raise DLPEvaluationError(
                f"DLP evaluation corpus cannot contain more than {MAX_CASES} cases"
            )
        cases.append(
            DLPEvaluationCase(
                payload=payload,
                expected_match=expected_match,
            )
        )
    if not cases:
        raise DLPEvaluationError("DLP evaluation corpus must contain at least one case")
    return tuple(cases)


def evaluate_dlp_rule(
    cases: tuple[DLPEvaluationCase, ...],
    *,
    rule: DLPRuleConfig,
    policy_version: str,
    corpus_id: str,
    protocol: str,
    model: str,
) -> dict[str, Any]:
    if protocol not in {"openai", "anthropic"}:
        raise DLPEvaluationError("DLP evaluation protocol must be openai or anthropic")
    if (
        not isinstance(model, str)
        or not model
        or len(model.encode("utf-8")) > 256
        or any(character in model for character in ("\n", "\r", "\x00"))
    ):
        raise DLPEvaluationError("DLP evaluation model must be a bounded single-line value")
    if not rule.applies_to(protocol=protocol, model=model):
        raise DLPEvaluationError(
            f"DLP rule {rule.rule_id} is outside the configured scope for this protocol and model"
        )
    if not cases or len(cases) > MAX_CASES:
        raise DLPEvaluationError(
            f"DLP evaluation requires between 1 and {MAX_CASES} cases"
        )
    if not isinstance(corpus_id, str) or _CORPUS_ID.fullmatch(corpus_id) is None:
        raise DLPEvaluationError(
            "DLP evaluation corpus_id must be 1 to 128 safe identifier characters"
        )

    detector_rule = DLPRuleConfig(
        rule_id=rule.rule_id,
        category=rule.category,
        confidence=rule.confidence,
        action="detect",
        providers=(protocol,),
        models=(model,),
        values_env=rule.values_env,
        exact_values=rule.exact_values,
    )
    redactor = SecretRedactor(
        SecretControls(mode="off", builtins=False),
        dlp_controls=DLPControls(
            policy_version=policy_version,
            rules=(detector_rule,),
        ),
    )
    true_positive = 0
    true_negative = 0
    false_positive = 0
    false_negative = 0
    finding_count = 0
    positive_count = 0
    for index, case in enumerate(cases, start=1):
        positive_count += int(case.expected_match)
        try:
            inspected = redactor.inspect(
                case.payload,
                protocol=protocol,
                model=model,
            )
        except (RedactionError, RecursionError) as error:
            raise DLPEvaluationError(
                f"DLP detector rejected evaluation case {index} without producing a report"
            ) from error
        actual_match = rule.rule_id in inspected.rules
        finding_count += sum(
            finding.count
            for finding in inspected.findings
            if finding.rule_id == rule.rule_id
        )
        if case.expected_match and actual_match:
            true_positive += 1
        elif case.expected_match:
            false_negative += 1
        elif actual_match:
            false_positive += 1
        else:
            true_negative += 1

    negative_count = len(cases) - positive_count
    return {
        "schema_version": RESULT_SCHEMA,
        "status": "evaluated",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runner": {
            "hormuz_version": __version__,
            "python_version": platform.python_version(),
        },
        "detector": {
            "implementation": "hormuz.redaction.SecretRedactor",
            "version": DLP_DETECTOR_VERSION,
        },
        "policy_version": policy_version,
        "rule": {
            "rule_id": rule.rule_id,
            "category": rule.category,
            "confidence": rule.confidence,
            "configured_action": rule.action,
            "evaluation_action": "detect",
        },
        "scope": {
            "protocol": protocol,
            "model": model,
        },
        "corpus": {
            "corpus_id": corpus_id,
            "case_count": len(cases),
            "positive_count": positive_count,
            "negative_count": negative_count,
        },
        "confusion_matrix": {
            "true_positive": true_positive,
            "true_negative": true_negative,
            "false_positive": false_positive,
            "false_negative": false_negative,
        },
        "finding_count": finding_count,
        "metrics": {
            "precision": _ratio(true_positive, true_positive + false_positive),
            "recall": _ratio(true_positive, true_positive + false_negative),
            "specificity": _ratio(true_negative, true_negative + false_positive),
            "false_positive_rate": _ratio(
                false_positive,
                false_positive + true_negative,
            ),
            "false_negative_rate": _ratio(
                false_negative,
                false_negative + true_positive,
            ),
            "accuracy": _ratio(true_positive + true_negative, len(cases)),
        },
        "privacy": {
            "payloads_retained": False,
            "matched_values_retained": False,
            "case_identifiers_retained": False,
            "corpus_hash_retained": False,
        },
        "promotion": {
            "automatic": False,
            "decision": "manual_policy_decision_required",
        },
        "corpus_format": CORPUS_FORMAT,
    }


def write_evaluation_result(
    value: dict[str, Any],
    output: str,
    *,
    force: bool = False,
) -> None:
    try:
        serialized = json.dumps(
            value,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        ) + "\n"
    except (TypeError, ValueError, RecursionError) as error:
        raise DLPEvaluationError("DLP evaluation result is not valid strict JSON") from error
    if output == "-":
        sys.stdout.write(serialized)
        return
    path = Path(output).expanduser().absolute()
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | (os.O_TRUNC if force else os.O_EXCL)
    )
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as error:
        raise DLPEvaluationError(
            f"DLP evaluation output already exists: {path}"
        ) from error
    except OSError as error:
        raise DLPEvaluationError(f"cannot create DLP evaluation output: {path}") from error
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
        raise DLPEvaluationError(f"cannot write DLP evaluation output: {path}") from error


def _strict_json(line: str, *, line_number: int) -> Any:
    def reject_duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise _DuplicateJSONMember()
            result[key] = value
        return result

    def reject_constant(_value: str) -> None:
        raise _NonStandardJSONConstant()

    try:
        return json.loads(
            line,
            object_pairs_hook=reject_duplicate,
            parse_constant=reject_constant,
        )
    except _DuplicateJSONMember as error:
        raise DLPEvaluationError(
            f"DLP evaluation case has a duplicate JSON member on line {line_number}"
        ) from error
    except _NonStandardJSONConstant as error:
        raise DLPEvaluationError(
            f"DLP evaluation case has a non-standard JSON constant on line {line_number}"
        ) from error
    except (json.JSONDecodeError, RecursionError) as error:
        raise DLPEvaluationError(
            f"DLP evaluation case is invalid JSON on line {line_number}"
        ) from error


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 6)
