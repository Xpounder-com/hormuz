"""Coding evidence assessment behind the existing admission interface."""
from dataclasses import dataclass
import json
import platform

from ._contract_schemas.local_coding import FAMILIES, canonical_digest
from .client_versions import SUPPORTED_CODEX_VERSION


@dataclass(frozen=True)
class CodingQualification:
    readiness: str = "unknown"
    evidence_id: str | None = None
    plan_sha256: str | None = None
    receipt_sha256: str | None = None
    planned: int = 0
    evaluated: int = 0
    passed: int = 0
    request_profile_sha256: str | None = None
    client_version: str | None = None

    def describe(self):
        if self.evidence_id is None:
            return None
        return {"evidence_id": self.evidence_id, "plan_sha256": self.plan_sha256,
                "receipt_sha256": self.receipt_sha256, "basis": "operator_attested_outcomes",
                "criteria_version": "local-repair.v1", "planned": self.planned,
                "evaluated": self.evaluated, "passed": self.passed}


def request_profile_digest(value):
    # Task text and turn identifiers vary; instructions, tools and generation
    # controls do not inherit qualification from another experiment.
    fields = {"instructions", "tools", "tool_choice", "temperature", "top_p", "reasoning", "include",
              "text", "parallel_tool_calls", "store", "service_tier", "stream"}
    profile = {key: item for key, item in value.items() if key in fields}
    items = value.get("input", [])
    if not isinstance(items, list):
        items = [items]
    instructions = [{"position": index, "message": item} for index, item in enumerate(items)
                    if isinstance(item, dict) and item.get("role") in ("system", "developer")]
    if instructions:
        profile["input_instructions"] = instructions
    profile.setdefault("store", False)
    if profile.get("service_tier") is None:
        profile["service_tier"] = "default"
    return canonical_digest(profile)


def assess_coding_evidence(serialized, config, route, qualification):
    if serialized is None:
        return CodingQualification()
    evidence = json.loads(serialized)
    plan = evidence["plan"]
    subject = plan["subject"]
    outcomes = {row["case_id"]: row for row in evidence["outcomes"]}
    passed, failed = 0, False
    for case in plan["cases"]:
        outcome = outcomes.get(case["case_id"])
        if outcome is None:
            continue
        complete = (outcome["termination"] == "completed"
                    and all(outcome[key] is not None for key in outcome if key != "case_id"))
        success = (complete and outcome["baseline_exit_code"] == 1
                   and outcome["checks_sha256"] == case["checks_sha256"]
                   and outcome["implementation_changed"] is True
                   and outcome["check_exit_code"] == 0 and outcome["client_exit_code"] == 0
                   and outcome["elapsed_ms"] <= subject["upstream_timeout_seconds"] * 1000)
        passed += int(success)
        failed |= (outcome["termination"] in {"timeout", "canceled"}
                   or outcome["implementation_changed"] is False
                   or outcome["baseline_exit_code"] is not None and outcome["baseline_exit_code"] != 1
                   or any(outcome[key] is not None and outcome[key] != 0 for key in ("check_exit_code", "client_exit_code"))
                   or outcome["checks_sha256"] is not None and outcome["checks_sha256"] != case["checks_sha256"]
                   or outcome["elapsed_ms"] is not None and outcome["elapsed_ms"] > subject["upstream_timeout_seconds"] * 1000)
    covered = all(sum(case["partition"] == partition and case["family"] == family for case in plan["cases"]) >= count
                  for family in FAMILIES for partition, count in (("development", 1), ("held_out", 3)))
    managed = config.managed_llama
    expected = {"client_version": SUPPORTED_CODEX_VERSION, "system": platform.system(), "machine": platform.machine(),
                "runtime_profile": "managed-llama.v1", "runtime_sha256": managed.runtime_sha256 if managed else None,
                "model_revision": route.model_revision, "upstream_model": route.upstream_model, "protocol": route.protocol,
                "capabilities": sorted(route.capabilities), "max_request_bytes": qualification.max_request_bytes,
                "max_output_tokens": qualification.max_output_tokens, "upstream_timeout_seconds": config.upstream_timeout_seconds,
                "request_profile_sha256": subject["request_profile_sha256"]}
    declared = {**subject, "capabilities": sorted(subject["capabilities"])}
    matched = managed is not None and managed.route == route.alias and declared == expected
    readiness = ("subject_mismatch" if not matched else "failed" if failed else
                 "qualified" if covered and passed == len(plan["cases"]) else "incomplete")
    return CodingQualification(readiness, evidence["evidence_id"], evidence["plan_sha256"], canonical_digest(evidence),
                               len(plan["cases"]), len(outcomes), passed, subject["request_profile_sha256"], subject["client_version"])
