"""Bounded, content-free declarations of independently checked coding outcomes."""
import hashlib
import json
import math
import re

from .common import ContractValidationError, _exact_keys

EVIDENCE_FIELDS = {"schema_id", "schema_version", "evidence_id", "plan", "plan_sha256", "outcomes"}
SUBJECT_FIELDS = {"client_version", "system", "machine", "runtime_profile", "runtime_sha256", "model_revision",
                  "upstream_model", "protocol", "capabilities", "max_request_bytes", "max_output_tokens",
                  "upstream_timeout_seconds", "request_profile_sha256"}
FAMILIES = frozenset({"arithmetic", "text", "collections", "validation_state"})


def canonical_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _digest(value):
    if type(value) is not str or re.fullmatch(r"[a-f0-9]{64}", value) is None:
        raise ContractValidationError("invalid coding evidence digest")


def validate_identifier(value):
    if type(value) is not str or re.fullmatch(r"[A-Za-z0-9_.:+-]{1,128}", value) is None:
        raise ContractValidationError("invalid coding evidence identifier")


def validate_evidence(value):
    _exact_keys(value, EVIDENCE_FIELDS)
    if value["schema_id"] != "hormuz.local-coding-evidence" or type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ContractValidationError("invalid coding evidence version")
    validate_identifier(value["evidence_id"])
    plan = value["plan"]
    if type(plan) is not dict:
        raise ContractValidationError("invalid coding evidence plan")
    _exact_keys(plan, {"criteria_version", "subject", "cases"})
    if plan["criteria_version"] != "local-repair.v1":
        raise ContractValidationError("unsupported coding criteria")
    subject = plan["subject"]
    if type(subject) is not dict:
        raise ContractValidationError("invalid coding evidence subject")
    _exact_keys(subject, SUBJECT_FIELDS)
    for key in ("client_version", "system", "machine", "runtime_profile", "model_revision", "upstream_model"):
        validate_identifier(subject[key])
    for key in ("runtime_sha256", "request_profile_sha256"):
        _digest(subject[key])
    if subject["protocol"] != "openai" or subject["runtime_profile"] != "managed-llama.v1":
        raise ContractValidationError("unsupported coding execution profile")
    if re.fullmatch(r"sha256:[a-f0-9]{64}", subject["model_revision"]) is None:
        raise ContractValidationError("invalid coding model revision")
    caps = subject["capabilities"]
    if (type(caps) is not list
            or any(type(cap) is not str or cap not in {"responses", "text", "streaming", "tools", "codex_http", "codex_http_0160", "structured_output"} for cap in caps)
            or not {"responses", "text"}.issubset(caps) or len(caps) != len(set(caps))):
        raise ContractValidationError("invalid coding capabilities")
    for key, maximum in (("max_request_bytes", 65536), ("max_output_tokens", 512), ("upstream_timeout_seconds", 90)):
        if type(subject[key]) is not int or not 1 <= subject[key] <= maximum:
            raise ContractValidationError("invalid coding evidence limit")
    cases = plan["cases"]
    if type(cases) is not list or not 1 <= len(cases) <= 64:
        raise ContractValidationError("invalid coding case inventory")
    identifiers, tasks = set(), set()
    for case in cases:
        if type(case) is not dict:
            raise ContractValidationError("invalid coding case")
        _exact_keys(case, {"case_id", "partition", "family", "task_sha256", "checks_sha256"})
        validate_identifier(case["case_id"])
        _digest(case["task_sha256"])
        _digest(case["checks_sha256"])
        if (type(case["partition"]) is not str or case["partition"] not in {"development", "held_out"}
                or type(case["family"]) is not str or case["family"] not in FAMILIES
                or case["case_id"] in identifiers or case["task_sha256"] in tasks):
            raise ContractValidationError("invalid or reused coding case")
        identifiers.add(case["case_id"])
        tasks.add(case["task_sha256"])
    _digest(value["plan_sha256"])
    if value["plan_sha256"] != canonical_digest(plan):
        raise ContractValidationError("coding plan digest mismatch")
    outcomes = value["outcomes"]
    if type(outcomes) is not list or len(outcomes) > len(cases):
        raise ContractValidationError("invalid coding outcomes")
    observed = set()
    for outcome in outcomes:
        if type(outcome) is not dict:
            raise ContractValidationError("invalid coding outcome")
        _exact_keys(outcome, {"case_id", "baseline_exit_code", "checks_sha256", "implementation_changed", "patch_sha256",
                              "check_exit_code", "client_exit_code", "termination", "elapsed_ms"})
        validate_identifier(outcome["case_id"])
        if outcome["case_id"] not in identifiers or outcome["case_id"] in observed:
            raise ContractValidationError("invalid coding outcome identity")
        observed.add(outcome["case_id"])
        for key in ("checks_sha256", "patch_sha256"):
            if outcome[key] is not None:
                _digest(outcome[key])
        for key in ("baseline_exit_code", "check_exit_code", "client_exit_code"):
            if outcome[key] is not None and (type(outcome[key]) is not int or not -255 <= outcome[key] <= 255):
                raise ContractValidationError("invalid coding exit code")
        if outcome["implementation_changed"] is not None and type(outcome["implementation_changed"]) is not bool:
            raise ContractValidationError("invalid coding edit observation")
        if type(outcome["termination"]) is not str or outcome["termination"] not in {"completed", "timeout", "canceled", "unknown"}:
            raise ContractValidationError("invalid coding completion")
        elapsed = outcome["elapsed_ms"]
        if elapsed is not None and (type(elapsed) not in (int, float) or not 0 <= elapsed <= 86_400_000 or not math.isfinite(elapsed)):
            raise ContractValidationError("invalid coding elapsed time")
