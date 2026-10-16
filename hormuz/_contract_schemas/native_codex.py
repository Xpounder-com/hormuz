"""Strict process-scoped native observation receipt; monetary cost is unknown."""
import re

from .common import ContractValidationError, _exact_keys


COUNTERS = ("requests", "active", "succeeded", "failed", "outcome_unknown",
            "observed_input_tokens", "observed_output_tokens", "observed_usage_requests", "usage_unknown_requests")
FIELDS = {"schema_id", "schema_version", "scope", "generation", "enabled", "billing", "cost_status", "cost_usd", *COUNTERS}


def validate_status(value):
    _exact_keys(value, FIELDS)
    if (value["scope"] != "identity_process_observation" or value["billing"] != "chatgpt_plan"
            or value["cost_status"] != "unknown" or value["cost_usd"] is not None
            or type(value["enabled"]) is not bool or type(value["generation"]) is not str
            or re.fullmatch(r"[0-9a-f]{64}", value["generation"]) is None):
        raise ContractValidationError("invalid native observation scope or cost")
    for key in COUNTERS:
        if type(value[key]) is not int or value[key] < 0:
            raise ContractValidationError("invalid native observation counter")
    settled = value["succeeded"] + value["failed"] + value["outcome_unknown"]
    if (value["requests"] != value["active"] + settled
            or value["observed_usage_requests"] + value["usage_unknown_requests"] != settled
            or not value["enabled"] and value["requests"] != 0
            or value["observed_usage_requests"] == 0 and (value["observed_input_tokens"] or value["observed_output_tokens"])):
        raise ContractValidationError("inconsistent native observation counters")
