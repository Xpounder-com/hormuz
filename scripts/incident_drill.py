#!/usr/bin/env python3
"""Run repository-local incident regressions and emit content-free evidence."""

from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, TextIO
import unittest


CATALOG_SCHEMA = "hormuz.incident-drills.v1"
EVIDENCE_SCHEMA = "hormuz.incident-drill-evidence.v1"
EXERCISE_SCOPE = "repository_local_simulation"
MAX_CATALOG_BYTES = 512 * 1024
MAX_RUNBOOK_BYTES = 512 * 1024
REQUIRED_INCIDENT_SCENARIOS = {
    "provider_outage",
    "idp_outage",
    "credential_compromise",
    "tenant_isolation_incident",
    "policy_rollout_incident",
    "cost_spike",
    "data_deletion_request",
}
TOP_LEVEL_FIELDS = {
    "schema",
    "version",
    "exercise_scope",
    "production_exercise_complete",
    "owner_assignments_complete",
    "external_communications_exercised",
    "enterprise_release_ready",
    "scenarios",
}
SCENARIO_FIELDS = {
    "id",
    "name",
    "lead_role",
    "coordination_role",
    "objective",
    "test_ids",
    "runbook",
    "production_gap",
}
IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
TEST_IDENTIFIER = re.compile(
    r"^tests\.test_[a-z0-9_]+\."
    r"[A-Za-z][A-Za-z0-9_]*\.test_[a-z0-9_]+$"
)
RUNBOOK_REFERENCE = re.compile(r"^([A-Za-z0-9_.\-/]+)#([a-z0-9-]+)$")


class IncidentDrillContractError(RuntimeError):
    """Raised when incident evidence cannot be produced safely."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise IncidentDrillContractError(
                "incident drill JSON contains a duplicate member"
            )
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise IncidentDrillContractError(
        "incident drill JSON contains a non-standard number"
    )


def _read_catalog(path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise IncidentDrillContractError("incident drill catalog is unavailable") from error
    if not raw or len(raw) > MAX_CATALOG_BYTES:
        raise IncidentDrillContractError("incident drill catalog size is invalid")
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise IncidentDrillContractError("incident drill catalog is not strict JSON") from error
    if not isinstance(value, dict):
        raise IncidentDrillContractError("incident drill catalog must be an object")
    return value, raw


def _exact_fields(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise IncidentDrillContractError(f"incident drill {label} has invalid fields")
    return value


def _required_string(value: Any, label: str, *, maximum: int = 4096) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > maximum
        or any(character in value for character in ("\x00", "\r"))
    ):
        raise IncidentDrillContractError(f"incident drill {label} is invalid")
    return value


def _markdown_anchors(path: Path) -> set[str]:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise IncidentDrillContractError("incident drill runbook reference is missing") from error
    if not raw or len(raw) > MAX_RUNBOOK_BYTES:
        raise IncidentDrillContractError("incident drill runbook size is invalid")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise IncidentDrillContractError("incident drill runbook is not UTF-8") from error
    anchors: set[str] = set()
    for line in text.splitlines():
        match = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
        if match is None:
            continue
        anchor = match.group(1).strip().lower()
        anchor = re.sub(r"[^a-z0-9 _-]", "", anchor)
        anchor = re.sub(r"[ _]+", "-", anchor)
        anchor = re.sub(r"-+", "-", anchor).strip("-")
        if anchor:
            anchors.add(anchor)
    return anchors


def _validate_runbook(reference: Any, root: Path) -> str:
    value = _required_string(reference, "runbook reference", maximum=512)
    match = RUNBOOK_REFERENCE.fullmatch(value)
    if match is None:
        raise IncidentDrillContractError("incident drill runbook reference is invalid")
    candidate = (root / match.group(1)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise IncidentDrillContractError("incident drill runbook reference escapes repository") from error
    if match.group(2) not in _markdown_anchors(candidate):
        raise IncidentDrillContractError("incident drill runbook anchor is missing")
    return value


def _load_exact_test(test_id: str, root: Path) -> unittest.TestSuite:
    added = str(root) not in sys.path
    if added:
        sys.path.insert(0, str(root))
    try:
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromName(test_id)
    finally:
        if added:
            sys.path.remove(str(root))
    if loader.errors or suite.countTestCases() != 1:
        raise IncidentDrillContractError("incident drill test binding does not resolve exactly")
    return suite


def _validated_catalog(
    catalog_path: Path,
    *,
    project_root: Path,
) -> tuple[dict[str, Any], bytes, list[dict[str, Any]], int]:
    catalog, raw = _read_catalog(catalog_path)
    catalog = _exact_fields(catalog, TOP_LEVEL_FIELDS, "top-level fields")
    if catalog["schema"] != CATALOG_SCHEMA:
        raise IncidentDrillContractError("incident drill schema is unsupported")
    version = _required_string(catalog["version"], "version", maximum=32)
    try:
        date.fromisoformat(version)
    except ValueError as error:
        raise IncidentDrillContractError("incident drill version is invalid") from error
    if catalog["exercise_scope"] != EXERCISE_SCOPE:
        raise IncidentDrillContractError("incident drill exercise scope is unsupported")
    for field in (
        "production_exercise_complete",
        "owner_assignments_complete",
        "external_communications_exercised",
        "enterprise_release_ready",
    ):
        if catalog[field] is not False:
            raise IncidentDrillContractError(
                "repository-local incident drills cannot claim production readiness"
            )

    scenarios_value = catalog["scenarios"]
    if not isinstance(scenarios_value, list):
        raise IncidentDrillContractError("incident drill scenarios are invalid")
    scenarios: list[dict[str, Any]] = []
    scenario_ids: set[str] = set()
    test_ids: set[str] = set()
    root = project_root.resolve()
    for raw_scenario in scenarios_value:
        scenario = _exact_fields(raw_scenario, SCENARIO_FIELDS, "scenario")
        scenario_id = _required_string(scenario["id"], "scenario identifier", maximum=64)
        if IDENTIFIER.fullmatch(scenario_id) is None or scenario_id in scenario_ids:
            raise IncidentDrillContractError("incident drill scenario identifier is invalid")
        scenario_ids.add(scenario_id)
        _required_string(scenario["name"], "scenario name", maximum=256)
        for role_field in ("lead_role", "coordination_role"):
            role = _required_string(scenario[role_field], role_field, maximum=64)
            if IDENTIFIER.fullmatch(role) is None:
                raise IncidentDrillContractError("incident drill role identifier is invalid")
        _required_string(scenario["objective"], "scenario objective", maximum=1024)
        _required_string(scenario["production_gap"], "production gap", maximum=1024)
        _validate_runbook(scenario["runbook"], root)

        raw_test_ids = scenario["test_ids"]
        if not isinstance(raw_test_ids, list) or not raw_test_ids:
            raise IncidentDrillContractError("incident drill test identifiers are invalid")
        for raw_test_id in raw_test_ids:
            test_id = _required_string(raw_test_id, "test identifier", maximum=256)
            if TEST_IDENTIFIER.fullmatch(test_id) is None:
                raise IncidentDrillContractError("incident drill test identifier is invalid")
            if test_id in test_ids:
                raise IncidentDrillContractError("incident drill test identifier is duplicated")
            _load_exact_test(test_id, root)
            test_ids.add(test_id)
        scenarios.append(scenario)

    if scenario_ids != REQUIRED_INCIDENT_SCENARIOS:
        raise IncidentDrillContractError(
            "incident drill catalog does not cover required scenarios"
        )
    return catalog, raw, scenarios, len(test_ids)


def _evidence(
    catalog: dict[str, Any],
    raw: bytes,
    *,
    scenario_count: int,
    test_count: int,
    executed_count: int,
    passed_count: int,
) -> dict[str, Any]:
    return {
        "schema": EVIDENCE_SCHEMA,
        "catalog_schema": CATALOG_SCHEMA,
        "catalog_version": catalog["version"],
        "catalog_sha256": hashlib.sha256(raw).hexdigest(),
        "exercise_scope": EXERCISE_SCOPE,
        "scenario_count": scenario_count,
        "test_count": test_count,
        "executed_count": executed_count,
        "passed_count": passed_count,
        "failed_count": executed_count - passed_count,
        "production_exercise_complete": False,
        "owner_assignments_complete": False,
        "external_communications_exercised": False,
        "enterprise_release_ready": False,
    }


def validate_incident_drills(
    catalog_path: Path,
    *,
    project_root: Path,
) -> dict[str, Any]:
    catalog, raw, scenarios, test_count = _validated_catalog(
        catalog_path,
        project_root=project_root,
    )
    return _evidence(
        catalog,
        raw,
        scenario_count=len(scenarios),
        test_count=test_count,
        executed_count=0,
        passed_count=0,
    )


def run_incident_drills(
    catalog_path: Path,
    *,
    project_root: Path,
    stream: TextIO | None = None,
) -> dict[str, Any]:
    catalog, raw, scenarios, test_count = _validated_catalog(
        catalog_path,
        project_root=project_root,
    )
    root = project_root.resolve()
    output = stream if stream is not None else sys.stderr
    executed_count = 0
    passed_count = 0
    for scenario in scenarios:
        for test_id in scenario["test_ids"]:
            suite = _load_exact_test(test_id, root)
            result = unittest.TextTestRunner(stream=output, verbosity=2).run(suite)
            executed_count += result.testsRun
            if (
                result.wasSuccessful()
                and result.testsRun == 1
                and not result.skipped
                and not result.expectedFailures
                and not result.unexpectedSuccesses
            ):
                passed_count += 1
            else:
                raise IncidentDrillContractError(
                    "incident drill regression did not pass exactly"
                )
    if executed_count != test_count or passed_count != test_count:
        raise IncidentDrillContractError("incident drill execution count is invalid")
    return _evidence(
        catalog,
        raw,
        scenario_count=len(scenarios),
        test_count=test_count,
        executed_count=executed_count,
        passed_count=passed_count,
    )


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
        raise IncidentDrillContractError("cannot write incident drill evidence") from error
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
            descriptor = -1
            file.write(payload)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run repository-local Hormuz incident regressions"
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=Path("operations/incident-drills.json"),
    )
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    try:
        evidence = run_incident_drills(
            arguments.catalog,
            project_root=arguments.project_root,
        )
        if arguments.output is not None:
            _write_evidence(arguments.output, evidence)
        print(json.dumps(evidence, sort_keys=True, separators=(",", ":")))
    except IncidentDrillContractError as error:
        print(f"incident drill failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
