#!/usr/bin/env python3
"""Validate the versioned Hormuz threat model and emit content-free evidence."""

from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any


MODEL_SCHEMA = "hormuz.threat-model.v1"
EVIDENCE_SCHEMA = "hormuz.threat-model-evidence.v1"
MAX_MODEL_BYTES = 512 * 1024
STRIDE_CATEGORIES = {
    "spoofing",
    "tampering",
    "repudiation",
    "information_disclosure",
    "denial_of_service",
    "elevation_of_privilege",
}
REQUIRED_INCIDENT_SCENARIOS = {
    "provider_outage",
    "idp_outage",
    "credential_compromise",
    "tenant_isolation_incident",
    "stale_context_incident",
    "cost_spike",
    "data_deletion_request",
}
THREAT_STATUSES = {"mitigated", "partially_mitigated", "open"}
CONTROL_KINDS = {"code", "test", "documentation", "workflow", "configuration"}
IDENTIFIERS = {
    "asset": re.compile(r"^AST-[0-9]{3}$"),
    "boundary": re.compile(r"^TB-[0-9]{3}$"),
    "threat": re.compile(r"^TM-[0-9]{3}$"),
}
REFERENCE = re.compile(r"^[A-Za-z0-9_.\-/]+(?:#[A-Za-z0-9_.:\-]+)?$")


class ThreatModelContractError(RuntimeError):
    """Raised when the threat model cannot support fail-closed evidence."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ThreatModelContractError("threat model JSON contains a duplicate member")
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise ThreatModelContractError("threat model JSON contains a non-standard number")


def _read_model(path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise ThreatModelContractError("threat model is unavailable") from error
    if not raw or len(raw) > MAX_MODEL_BYTES:
        raise ThreatModelContractError("threat model size is invalid")
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ThreatModelContractError("threat model is not strict JSON") from error
    if not isinstance(value, dict):
        raise ThreatModelContractError("threat model must be an object")
    return value, raw


def _exact_fields(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ThreatModelContractError(f"threat model {label} has invalid fields")
    return value


def _required_string(value: Any, label: str, *, maximum: int = 4096) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > maximum
        or any(character in value for character in ("\x00", "\r"))
    ):
        raise ThreatModelContractError(f"threat model {label} is invalid")
    return value


def _string_list(value: Any, label: str, *, minimum: int = 1) -> list[str]:
    if not isinstance(value, list) or len(value) < minimum:
        raise ThreatModelContractError(f"threat model {label} is invalid")
    result = [_required_string(item, label, maximum=1024) for item in value]
    if len(set(result)) != len(result):
        raise ThreatModelContractError(f"threat model {label} contains duplicates")
    return result


def _identifier(value: Any, kind: str) -> str:
    result = _required_string(value, f"{kind} identifier", maximum=16)
    if IDENTIFIERS[kind].fullmatch(result) is None:
        raise ThreatModelContractError(f"threat model {kind} identifier is invalid")
    return result


def _validate_reference(reference: Any, project_root: Path) -> str:
    value = _required_string(reference, "evidence reference", maximum=512)
    if REFERENCE.fullmatch(value) is None:
        raise ThreatModelContractError("threat model evidence reference is invalid")
    raw_path, separator, symbol = value.partition("#")
    candidate = (project_root / raw_path).resolve()
    try:
        candidate.relative_to(project_root)
    except ValueError as error:
        raise ThreatModelContractError("threat model evidence reference escapes the repository") from error
    if not candidate.is_file():
        raise ThreatModelContractError("threat model evidence reference does not exist")
    if separator:
        try:
            source = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise ThreatModelContractError("threat model evidence reference is unreadable") from error
        if symbol not in source:
            raise ThreatModelContractError("threat model evidence reference symbol does not exist")
    return value


def _validate_controls(value: Any, project_root: Path) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise ThreatModelContractError("threat model controls must be a list")
    controls: list[dict[str, str]] = []
    for raw in value:
        control = _exact_fields(raw, {"kind", "ref", "claim"}, "control")
        kind = _required_string(control["kind"], "control kind", maximum=32)
        if kind not in CONTROL_KINDS:
            raise ThreatModelContractError("threat model control kind is invalid")
        controls.append(
            {
                "kind": kind,
                "ref": _validate_reference(control["ref"], project_root),
                "claim": _required_string(control["claim"], "control claim"),
            }
        )
    return controls


def validate_threat_model(path: Path, *, project_root: Path) -> dict[str, Any]:
    root = project_root.resolve()
    model, raw = _read_model(path)
    _exact_fields(
        model,
        {
            "schema",
            "version",
            "product",
            "scope",
            "assets",
            "trust_boundaries",
            "incident_scenarios",
            "threats",
            "independent_review",
        },
        "top-level fields",
    )
    if model["schema"] != MODEL_SCHEMA:
        raise ThreatModelContractError("threat model schema is unsupported")
    version = _required_string(model["version"], "version", maximum=32)
    try:
        date.fromisoformat(version)
    except ValueError as error:
        raise ThreatModelContractError("threat model version is not an ISO date") from error
    if model["product"] != "Hormuz":
        raise ThreatModelContractError("threat model product is invalid")

    scope = _exact_fields(
        model["scope"], {"statement", "in_scope", "out_of_scope"}, "scope"
    )
    _required_string(scope["statement"], "scope statement")
    _string_list(scope["in_scope"], "in-scope list")
    _string_list(scope["out_of_scope"], "out-of-scope list")

    if not isinstance(model["assets"], list) or not model["assets"]:
        raise ThreatModelContractError("threat model assets are invalid")
    asset_ids: set[str] = set()
    for raw_asset in model["assets"]:
        asset = _exact_fields(raw_asset, {"id", "name", "security_properties"}, "asset")
        asset_id = _identifier(asset["id"], "asset")
        if asset_id in asset_ids:
            raise ThreatModelContractError("threat model asset identifiers are duplicated")
        asset_ids.add(asset_id)
        _required_string(asset["name"], "asset name", maximum=256)
        properties = set(_string_list(asset["security_properties"], "asset properties"))
        if not properties <= {"confidentiality", "integrity", "availability"}:
            raise ThreatModelContractError("threat model asset properties are invalid")

    if not isinstance(model["trust_boundaries"], list) or not model["trust_boundaries"]:
        raise ThreatModelContractError("threat model trust boundaries are invalid")
    boundary_ids: set[str] = set()
    for raw_boundary in model["trust_boundaries"]:
        boundary = _exact_fields(
            raw_boundary, {"id", "name", "from", "to", "data"}, "trust boundary"
        )
        boundary_id = _identifier(boundary["id"], "boundary")
        if boundary_id in boundary_ids:
            raise ThreatModelContractError("threat model boundary identifiers are duplicated")
        boundary_ids.add(boundary_id)
        for field in ("name", "from", "to", "data"):
            _required_string(boundary[field], f"trust boundary {field}")

    if not isinstance(model["threats"], list) or not model["threats"]:
        raise ThreatModelContractError("threat model threats are invalid")
    threat_ids: set[str] = set()
    categories: set[str] = set()
    status_counts = {status: 0 for status in THREAT_STATUSES}
    for raw_threat in model["threats"]:
        threat = _exact_fields(
            raw_threat,
            {
                "id",
                "title",
                "category",
                "boundary_ids",
                "asset_ids",
                "status",
                "scenario",
                "controls",
                "residual_risk",
                "release_gate",
            },
            "threat",
        )
        threat_id = _identifier(threat["id"], "threat")
        if threat_id in threat_ids:
            raise ThreatModelContractError("threat model threat identifiers are duplicated")
        threat_ids.add(threat_id)
        _required_string(threat["title"], "threat title", maximum=256)
        category = _required_string(threat["category"], "threat category", maximum=64)
        if category not in STRIDE_CATEGORIES:
            raise ThreatModelContractError("threat model threat category is not STRIDE")
        categories.add(category)
        referenced_boundaries = set(_string_list(threat["boundary_ids"], "threat boundaries"))
        referenced_assets = set(_string_list(threat["asset_ids"], "threat assets"))
        if not referenced_boundaries <= boundary_ids or not referenced_assets <= asset_ids:
            raise ThreatModelContractError("threat model threat references are invalid")
        status = _required_string(threat["status"], "threat status", maximum=32)
        if status not in THREAT_STATUSES:
            raise ThreatModelContractError("threat model threat status is invalid")
        status_counts[status] += 1
        _required_string(threat["scenario"], "threat scenario")
        controls = _validate_controls(threat["controls"], root)
        if status == "mitigated" and not controls:
            raise ThreatModelContractError("mitigated threat has no control evidence")
        _required_string(threat["residual_risk"], "threat residual risk")
        _required_string(threat["release_gate"], "threat release gate", maximum=512)
    if categories != STRIDE_CATEGORIES:
        raise ThreatModelContractError("threat model does not cover every STRIDE category")

    if not isinstance(model["incident_scenarios"], list):
        raise ThreatModelContractError("threat model incident scenarios are invalid")
    incident_ids: set[str] = set()
    for raw_incident in model["incident_scenarios"]:
        incident = _exact_fields(raw_incident, {"id", "name", "threat_ids"}, "incident scenario")
        incident_id = _required_string(incident["id"], "incident scenario identifier", maximum=64)
        if incident_id in incident_ids:
            raise ThreatModelContractError("threat model incident scenario identifiers are duplicated")
        incident_ids.add(incident_id)
        _required_string(incident["name"], "incident scenario name", maximum=256)
        if not set(_string_list(incident["threat_ids"], "incident threat references")) <= threat_ids:
            raise ThreatModelContractError("threat model incident threat reference is invalid")
    if incident_ids != REQUIRED_INCIDENT_SCENARIOS:
        raise ThreatModelContractError("threat model does not cover required incident scenarios")

    review = _exact_fields(
        model["independent_review"],
        {"status", "reviewer", "completed_at", "findings_status", "evidence_refs", "owner_risk_acceptance"},
        "independent review",
    )
    review_status = _required_string(review["status"], "independent review status", maximum=16)
    if review_status not in {"pending", "completed"}:
        raise ThreatModelContractError("independent review status is invalid")
    evidence_refs = review["evidence_refs"]
    if not isinstance(evidence_refs, list):
        raise ThreatModelContractError("independent review evidence is invalid")
    if review_status == "pending":
        if (
            review["reviewer"] is not None
            or review["completed_at"] is not None
            or review["findings_status"] != "not_started"
            or evidence_refs
            or review["owner_risk_acceptance"] is not False
        ):
            raise ThreatModelContractError("pending independent review has completion evidence")
    else:
        if not evidence_refs:
            raise ThreatModelContractError("completed independent review evidence is missing")
        _required_string(review["reviewer"], "independent reviewer", maximum=256)
        completed = _required_string(review["completed_at"], "independent review completion", maximum=64)
        try:
            date.fromisoformat(completed)
        except ValueError as error:
            raise ThreatModelContractError("independent review completion date is invalid") from error
        if review["findings_status"] not in {"resolved", "risk_accepted"}:
            raise ThreatModelContractError("independent review findings are unresolved")
        if review["findings_status"] == "risk_accepted" and review["owner_risk_acceptance"] is not True:
            raise ThreatModelContractError("independent review risk acceptance is missing")
        for reference in evidence_refs:
            _validate_reference(reference, root)

    enterprise_release_ready = (
        review_status == "completed"
        and status_counts["partially_mitigated"] == 0
        and status_counts["open"] == 0
    )
    return {
        "schema": EVIDENCE_SCHEMA,
        "model_schema": MODEL_SCHEMA,
        "model_version": version,
        "model_sha256": hashlib.sha256(raw).hexdigest(),
        "asset_count": len(asset_ids),
        "trust_boundary_count": len(boundary_ids),
        "threat_count": len(threat_ids),
        "mitigated_count": status_counts["mitigated"],
        "partially_mitigated_count": status_counts["partially_mitigated"],
        "open_count": status_counts["open"],
        "stride_categories_covered": len(categories),
        "incident_scenarios_covered": len(incident_ids),
        "independent_review_status": review_status,
        "enterprise_release_ready": enterprise_release_ready,
    }


def _write_evidence(path: Path, value: dict[str, Any]) -> None:
    payload = json.dumps(value, indent=2, sort_keys=True) + "\n"
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as error:
        raise ThreatModelContractError("cannot write threat model evidence") from error
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            descriptor = -1
            stream.write(payload)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the Hormuz threat model contract")
    parser.add_argument(
        "--model",
        type=Path,
        default=Path("security/threat-model.json"),
    )
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    try:
        evidence = validate_threat_model(
            arguments.model,
            project_root=arguments.project_root,
        )
        if arguments.output is not None:
            _write_evidence(arguments.output, evidence)
        print(json.dumps(evidence, sort_keys=True, separators=(",", ":")))
    except ThreatModelContractError as error:
        print(f"threat model validation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
