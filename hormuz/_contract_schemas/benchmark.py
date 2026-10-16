"""Version-one public benchmark receipts; observed summaries are revalidated."""
from .common import ContractValidationError

FIELDS = {
    "hormuz.personal-model-scorecard": ["schema_id", "schema_version", "suite", "actor_id", "organization_id", "machine",
                              "resources", "recorded_at", "expires_at", "targets", "models"],
    "hormuz.benchmark-suite": ["schema_id", "schema_version", "name", "targets", "cases"],
    "hormuz.benchmark-routing-configuration": ["schema_id", "schema_version", "scorecard_sha256", "output",
                                              "dry_run", "mode", "activation", "changes"],
    "hormuz.benchmark-error": ["schema_id", "schema_version", "error"],
    "hormuz.benchmark-runtime": ["schema_id", "schema_version", "actor_id", "organization_id", "shadow_router_enabled", "policy_content_sha256", "routes"],
}


def validate_benchmark(value):
    from ..model_benchmark import hash_label, label, validate_scorecard, validate_suite, validate_runtime
    try:
        schema = value["schema_id"]
        if set(value) != set(FIELDS[schema]) or type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise ValueError()
        if schema == "hormuz.personal-model-scorecard":
            validate_scorecard(value)
        elif schema == "hormuz.benchmark-suite":
            validate_suite(value)
        elif schema == "hormuz.benchmark-runtime":
            validate_runtime(value)
        elif schema == "hormuz.benchmark-error":
            if type(value["error"]) is not dict or set(value["error"]) != {"code"} or not label(value["error"]["code"]):
                raise ValueError()
        elif (not hash_label(value["scorecard_sha256"]) or type(value["output"]) is not str
              or not value["output"] or type(value["dry_run"]) is not bool
              or value["mode"] not in ("auto", "local_only") or value["activation"] != "requires_reconnect"
              or type(value["changes"]) is not dict or set(value["changes"]) != {"route_admission.scorecard"}
              or type(value["changes"]["route_admission.scorecard"]) is not str):
            raise ValueError()
    except (ValueError, TypeError, KeyError, RecursionError):
        raise ContractValidationError("invalid benchmark contract") from None
