"""Content-free shadow advice, separate from dispatch and coding success."""

import math
import re

from .common import ContractValidationError, _exact_keys, _value_string


SHADOW_FIELDS = {"schema_id", "schema_version", "mode", "prompt_version", "status", "recommendation",
                 "routing_effect", "classifier_model", "classifier_revision", "classifier_generation",
                 "local_model", "local_model_revision", "actual_model", "actual_destination", "actual_locality",
                 "elapsed_ms", "input_tokens", "output_tokens", "cost_microusd"}


def validate_shadow(value):
    _exact_keys(value, SHADOW_FIELDS)
    if value["mode"] != "shadow" or value["routing_effect"] != "none" or value["prompt_version"] != "local-fit.v1":
        raise ContractValidationError("invalid shadow routing authority")
    if value["status"] not in {"unknown", "classified", "affinity_preserved", "budget_unqualified", "policy_denied",
                               "input_unqualified", "invalid_response", "capacity_busy", "timeout", "unavailable", "client_canceled"}:
        raise ContractValidationError("invalid shadow routing status")
    if (value["recommendation"] not in {"local", "cloud", "unknown"}
            or value["status"] != "classified" and value["recommendation"] != "unknown"):
        raise ContractValidationError("invalid shadow routing recommendation")
    for key in ("classifier_model", "classifier_revision", "local_model", "local_model_revision", "actual_model", "actual_destination"):
        _value_string(value, key)
    if re.fullmatch(r"[0-9a-f]{64}", _value_string(value, "classifier_generation")) is None:
        raise ContractValidationError("invalid shadow classifier generation")
    if value["actual_locality"] not in {"local", "cloud"}:
        raise ContractValidationError("invalid shadow actual locality")
    elapsed = value["elapsed_ms"]
    if type(elapsed) not in (float, int) or not math.isfinite(elapsed) or elapsed < 0:
        raise ContractValidationError("invalid shadow latency")
    for field in ("input_tokens", "output_tokens"):
        if value[field] is not None and (type(value[field]) is not int or not 0 <= value[field] <= 1_000_000):
            raise ContractValidationError("invalid shadow token observation")
    if value["cost_microusd"] is not None:
        raise ContractValidationError("shadow inference cost is unqualified")


def validate_attempt_history(value):
    from ..contracts import validate_contract
    _exact_keys(value, {"schema_id", "schema_version", "scope", "work_id", "attempts", "page_limit", "order", "next_before"})
    if value["scope"] != "owned_job" or value["order"] != "reverse_admission":
        raise ContractValidationError("invalid attempt history scope")
    _value_string(value, "work_id")
    if value["next_before"] is not None:
        _value_string(value, "next_before")
    if type(value["page_limit"]) is not int or not 1 <= value["page_limit"] <= 100:
        raise ContractValidationError("invalid attempt history bound")
    rows = value["attempts"]
    if type(rows) is not list or len(rows) > value["page_limit"]:
        raise ContractValidationError("invalid attempt history page")
    shadows = 0
    for row in rows:
        if type(row) is not dict:
            raise ContractValidationError("invalid attempt history row")
        if "shadow_routing" in row:
            validate_contract(row["shadow_routing"])
            shadows += 1
    if bool(shadows) != (value["schema_version"] == 2):
        raise ContractValidationError("invalid attempt history version")
