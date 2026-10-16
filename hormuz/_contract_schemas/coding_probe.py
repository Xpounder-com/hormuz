"""Explicit experimental permission, separate from coding outcome evidence."""
import re

from .common import ContractValidationError, _exact_keys
from .local_coding import SUBJECT_FIELDS, _digest, validate_identifier

PLAN_FIELDS = {"schema_id", "schema_version", "campaign_id", "plan_sha256", "case_id", "task_sha256",
               "checks_sha256", "model", "first_input_sha256", "max_requests", "max_elapsed_seconds", "subject"}
STATUS_FIELDS = {"schema_id", "schema_version", "state", "probe_id", "plan_sha256", "case_id", "model",
                 "destination_generation", "submitted_requests", "max_requests", "remaining_ms", "automatic_admission_effect"}
ERRORS = {"probe_invalid_plan", "probe_subject_mismatch", "probe_already_active", "probe_requires_local_only",
          "probe_requires_work_binding", "probe_requires_uncached_execution"}


def validate_plan(value):
    _exact_keys(value, PLAN_FIELDS)
    if value["schema_id"] != "hormuz.coding-probe-plan" or type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ContractValidationError("unsupported coding probe plan")
    for key in ("campaign_id", "case_id", "model"):
        validate_identifier(value[key])
    for key in ("plan_sha256", "task_sha256", "checks_sha256", "first_input_sha256"):
        _digest(value[key])
    for key, maximum in (("max_requests", 32), ("max_elapsed_seconds", 90)):
        if type(value[key]) is not int or not 1 <= value[key] <= maximum:
            raise ContractValidationError("invalid coding probe budget")
    subject = value["subject"]
    if type(subject) is not dict:
        raise ContractValidationError("invalid coding probe subject")
    _exact_keys(subject, SUBJECT_FIELDS)
    for key in ("client_version", "system", "machine", "runtime_profile", "model_revision", "upstream_model"):
        validate_identifier(subject[key])
    for key in ("runtime_sha256", "request_profile_sha256"):
        _digest(subject[key])
    if (subject["protocol"] != "openai" or subject["runtime_profile"] != "managed-llama.v1"
            or re.fullmatch(r"sha256:[a-f0-9]{64}", subject["model_revision"]) is None):
        raise ContractValidationError("unsupported coding probe subject")
    caps = subject["capabilities"]
    if (type(caps) is not list or any(type(cap) is not str or cap not in {
            "responses", "text", "streaming", "tools", "codex_http", "codex_http_0160", "structured_output"} for cap in caps)
            or not {"responses", "text"}.issubset(caps) or len(caps) != len(set(caps))):
        raise ContractValidationError("invalid coding probe capabilities")
    for key, maximum in (("max_request_bytes", 65536), ("max_output_tokens", 512), ("upstream_timeout_seconds", 90)):
        if type(subject[key]) is not int or not 1 <= subject[key] <= maximum:
            raise ContractValidationError("invalid coding probe subject limit")


def validate_status(value):
    _exact_keys(value, STATUS_FIELDS)
    if value["schema_id"] != "hormuz.coding-probe-status" or type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ContractValidationError("unsupported coding probe status")
    state = value["state"]
    if type(state) is not str or state not in {"inactive", "armed", "running", "exhausted", "expired", "revoked"}:
        raise ContractValidationError("invalid coding probe state")
    if value["automatic_admission_effect"] != "none":
        raise ContractValidationError("a probe cannot qualify coding")
    for key, maximum in (("submitted_requests", 32), ("max_requests", 32), ("remaining_ms", 90000)):
        if type(value[key]) is not int or not 0 <= value[key] <= maximum:
            raise ContractValidationError("invalid coding probe status budget")
    identities = ("probe_id", "plan_sha256", "case_id", "model", "destination_generation")
    if state == "inactive":
        if any(value[key] is not None for key in identities) or any(value[key] for key in ("submitted_requests", "max_requests", "remaining_ms")):
            raise ContractValidationError("invalid inactive coding probe")
    else:
        if type(value["probe_id"]) is not str or re.fullmatch(r"[a-f0-9]{32}", value["probe_id"]) is None:
            raise ContractValidationError("invalid coding probe identity")
        _digest(value["plan_sha256"])
        for key in ("case_id", "model", "destination_generation"):
            validate_identifier(value[key])
        if not 1 <= value["max_requests"] <= 32 or value["submitted_requests"] > value["max_requests"]:
            raise ContractValidationError("contradictory coding probe budget")
        if (state == "armed" and value["submitted_requests"] != 0
                or state == "running" and not 0 < value["submitted_requests"] < value["max_requests"]
                or state == "exhausted" and value["submitted_requests"] != value["max_requests"]
                or state in {"revoked", "expired"} and value["remaining_ms"] != 0):
            raise ContractValidationError("contradictory coding probe state")


def validate_error(value):
    _exact_keys(value, {"schema_id", "schema_version", "error"})
    if value["schema_id"] != "hormuz.coding-probe-error" or type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ContractValidationError("unsupported coding probe error")
    if type(value["error"]) is not dict or set(value["error"]) != {"code"} or value["error"]["code"] not in ERRORS:
        raise ContractValidationError("invalid coding probe error")
