#!/usr/bin/env python3
"""Verify #217's analytics-first preflight, never budget runtime acceptance."""

from __future__ import annotations

import argparse
import copy
from datetime import datetime
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools._portfolio_wire_contract import PortfolioWireSchemaError, validate_wire_bundle
from tools.verify_finance_transition_plan import (
    FinanceTransitionError,
    verify_finance_implementation_plan,
)
from tools.verify_portfolio_extensions import (
    ExtensionContractError,
    validate_extension_payload,
)


PLAN_PATH = "docs/budget-transition-plan-v1.json"
REPORT_V1_PATH = "docs/work-budget-reports-wire-v1.json"
REPORT_V2_PATH = "docs/work-budget-reports-wire-v2.json"
FIXTURE_PATH = "tests/fixtures/portfolio_intelligence/budget-report-v2-examples.json"
PLAN_CANONICAL_SHA256 = "a07f9bd1a42084ce361090e3c53f7c4540a1264b8cccce7264af81d633aefaa2"
REPORT_V1_SHA256 = "6a45a010de84273be45da85115d8d41267d5689addd29df592c47e8704a29cbf"
REPORT_V2_SHA256 = "1e09eb42bedc8d91dc5ec230adb1f21360832940b237e38c8a88b86285a2c6d2"
FIXTURE_SHA256 = "2a51eeadbe3d7cce38ee94ed3b23933672bbd5c29d40315010e5fecbb110e3d6"
FINANCE_SOURCE_COMMIT = "1dd6c9f561ee70880d6e68b7aa2d2ab17852d207"
FINANCE_ARCHIVE_SHA256 = "31622ab69ee74daa4be92b4f1a8d57808304d1c4916600a17f7afe499a721910"
MAX_JSON_BYTES = 256 * 1024
REQUIRED_FILES = (
    PLAN_PATH,
    REPORT_V1_PATH,
    REPORT_V2_PATH,
    FIXTURE_PATH,
    "docs/BUDGET_TRANSITION.md",
    "docs/decisions/0012-analytics-first-budget-management-output.md",
    "tools/verify_budget_transition_plan.py",
    "tests/_budget_predecessor_fixture.py",
    "tests/test_budget_transition_plan.py",
    "tests/test_sqlite_budget_transition.py",
    "tests/test_postgres_budget_transition.py",
    "tests/test_budget_preflight_packaging.py",
    "tests/test_portfolio_wire_contract.py",
)


class BudgetTransitionError(ValueError):
    """Fixed content-free budget checkpoint diagnostics."""


def _fail(code: str) -> None:
    raise BudgetTransitionError(code)


def _read(root: Path, path: str, *, expected_sha256: str | None = None) -> object:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                _fail("budget_json_duplicate_member")
            result[key] = value
        return result

    def nonfinite(_value):
        _fail("budget_json_nonfinite")

    try:
        with (root / path).open("rb") as source:
            raw = source.read(MAX_JSON_BYTES + 1)
        if not 1 <= len(raw) <= MAX_JSON_BYTES:
            _fail("budget_file_bounds")
        if expected_sha256 is not None and hashlib.sha256(raw).hexdigest() != expected_sha256:
            _fail("budget_file_digest_mismatch")
        return json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=nonfinite)
    except BudgetTransitionError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        _fail("budget_file_unreadable")


def _canonical_digest(value: object) -> str:
    try:
        payload = json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        _fail("budget_plan_invalid")
    return hashlib.sha256(payload).hexdigest()


def validate_budget_transition_plan(value: object) -> None:
    if _canonical_digest(value) != PLAN_CANONICAL_SHA256:
        _fail("budget_preflight_contract_changed")


def _canonical_decimal(value: Decimal) -> str:
    if value.is_zero():
        return "0"
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        _fail("budget_change_time_invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        _fail("budget_change_time_invalid")
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        _fail("budget_change_time_invalid")
    return parsed


def _expected_kind(delta: Decimal) -> str:
    return "increased" if delta > 0 else "decreased" if delta < 0 else "unchanged"


def _validate_change(report: dict[str, object]) -> None:
    change = report["plan_change"]
    if not isinstance(change, dict):
        _fail("budget_change_invalid")
    if _timestamp(change["changed_at"]) > _timestamp(report["as_of"]):
        _fail("budget_change_time_invalid")
    status = change["comparison_status"]
    previous_plan = change["previous_plan"]
    previous_amount = change["previous_amount"]
    previous_currency = change["previous_currency"]
    previous_work_scope = change["previous_work_scope"]
    previous_window = change["previous_window"]
    amount_delta = change["amount_delta"]
    percent_delta = change["percent_delta"]
    kind = change["kind"]
    reasons = change["comparison_reasons"]
    current_amount = Decimal(report["plan_amount"])
    previous_facts = (
        previous_plan, previous_amount, previous_currency,
        previous_work_scope, previous_window,
    )
    if report["activation_generation"] == 1 and status != "first_activation":
        _fail("budget_change_first_activation_invalid")

    if status == "first_activation":
        if (
            report["activation_generation"] != 1
            or kind != "established"
            or reasons != []
            or any(value is not None for value in (*previous_facts, amount_delta, percent_delta))
        ):
            _fail("budget_change_first_activation_invalid")
        return

    if status == "missing_evidence":
        if kind != "not_comparable" or reasons != [] or any(
            value is not None for value in (*previous_facts, amount_delta, percent_delta)
        ):
            _fail("budget_change_missing_evidence_invalid")
        return

    if not isinstance(previous_plan, dict) or previous_plan.get("id") != report["plan"]["id"]:
        _fail("budget_change_previous_plan_invalid")
    if any(value is None for value in (previous_amount, previous_currency, previous_work_scope, previous_window)):
        _fail("budget_change_previous_basis_invalid")
    prior = Decimal(previous_amount)

    expected_reasons = []
    if previous_currency != report["currency"]:
        expected_reasons.append("currency_changed")
    if previous_work_scope != report["work_scope"]:
        expected_reasons.append("work_scope_changed")
    if previous_window != report["window"]:
        expected_reasons.append("window_changed")
    if expected_reasons:
        if (
            status != "not_comparable"
            or reasons != expected_reasons
            or kind != "not_comparable"
            or amount_delta is not None
            or percent_delta is not None
        ):
            _fail("budget_change_noncomparable_invalid")
        return
    if reasons != []:
        _fail("budget_change_comparison_reasons_invalid")

    with localcontext(Context(prec=96)):
        expected_delta = current_amount - prior
    if amount_delta is None or Decimal(amount_delta) != expected_delta or kind != _expected_kind(expected_delta):
        _fail("budget_change_amount_invalid")

    if status == "previous_amount_zero":
        if not prior.is_zero() or percent_delta is not None:
            _fail("budget_change_zero_denominator_invalid")
        return

    if status != "known" or prior <= 0 or percent_delta is None:
        _fail("budget_change_status_invalid")
    with localcontext(Context(prec=96)):
        expected_percent = (expected_delta * Decimal(100) / prior).quantize(
            Decimal("0.000001"), rounding=ROUND_HALF_EVEN,
        )
    if percent_delta != _canonical_decimal(expected_percent):
        _fail("budget_change_percentage_invalid")


def validate_budget_report_v2(
    report_v1: object, report_v2: object, fixtures: object,
) -> None:
    if not isinstance(report_v1, dict) or not isinstance(report_v2, dict):
        _fail("budget_report_bundle_invalid")
    try:
        validate_wire_bundle(report_v1, {"hormuz.work-budget-preview", "hormuz.work-budget-report"})
        validate_wire_bundle(report_v2, {"hormuz.work-budget-preview", "hormuz.work-budget-report"})
    except PortfolioWireSchemaError:
        _fail("budget_report_bundle_invalid")
    if report_v1.get("x-hormuz-schema-versions") is not None:
        _fail("budget_report_v1_changed")
    if report_v2.get("x-hormuz-schema-versions") != {
        "hormuz.work-budget-preview": 1,
        "hormuz.work-budget-report": 2,
    }:
        _fail("budget_report_version_inventory_invalid")
    v1_definitions = report_v1.get("$defs")
    v2_definitions = report_v2.get("$defs")
    if (
        not isinstance(v1_definitions, dict)
        or not isinstance(v2_definitions, dict)
        or set(v2_definitions) != set(v1_definitions) | {"signed_percentage", "budget_change"}
    ):
        _fail("budget_report_v2_not_additive")
    for name, definition in v1_definitions.items():
        if name != "hormuz.work-budget-report" and v2_definitions.get(name) != definition:
            _fail("budget_report_v2_not_additive")
    successor = copy.deepcopy(v2_definitions["hormuz.work-budget-report"])
    try:
        del successor["properties"]["plan_change"]
        successor["required"].remove("plan_change")
        successor["properties"]["schema_version"]["const"] = 1
        successor["description"] = v1_definitions["hormuz.work-budget-report"]["description"]
    except (KeyError, TypeError, ValueError):
        _fail("budget_report_v2_not_additive")
    if successor != v1_definitions["hormuz.work-budget-report"]:
        _fail("budget_report_v2_not_additive")
    for field in (
        "oneOf", "x-hormuz-schema-ids", "x-hormuz-route-query-fields", "x-hormuz-transport",
    ):
        if report_v2.get(field) != report_v1.get(field):
            _fail("budget_report_v2_not_additive")
    v1_rules = report_v1.get("x-hormuz-domain-rules")
    v2_rules = report_v2.get("x-hormuz-domain-rules")
    if (
        not isinstance(v1_rules, dict)
        or not isinstance(v2_rules, dict)
        or set(v2_rules) != set(v1_rules) | {"plan_change"}
        or any(
            v2_rules.get(name) != rule
            for name, rule in v1_rules.items()
            if name != "compatibility"
        )
    ):
        _fail("budget_report_v2_not_additive")
    if (
        not isinstance(fixtures, dict)
        or set(fixtures) != {"schema_id", "schema_version", "synthetic", "cases"}
        or fixtures["schema_id"] != "hormuz.budget-report-v2-examples"
        or type(fixtures["schema_version"]) is not int
        or fixtures["schema_version"] != 1
        or fixtures["synthetic"] is not True
        or not isinstance(fixtures["cases"], list)
        or len(fixtures["cases"]) != 2
        or {case.get("name") for case in fixtures["cases"]} != {
            "hormuz.work-budget-report:first-activation",
            "hormuz.work-budget-report:increased",
        }
    ):
        _fail("budget_report_fixture_inventory_invalid")
    for case in fixtures["cases"]:
        if case.get("schema_id") != "hormuz.work-budget-report":
            _fail("budget_report_fixture_inventory_invalid")
        value = case.get("value")
        try:
            validate_extension_payload(report_v2, "hormuz.work-budget-report", value)
            base = copy.deepcopy(value)
            del base["plan_change"]
            base["schema_version"] = 1
            validate_extension_payload(report_v1, "hormuz.work-budget-report", base)
        except (ExtensionContractError, KeyError, TypeError):
            _fail("budget_report_fixture_invalid")
        try:
            _validate_change(value)
        except BudgetTransitionError:
            raise
        except (KeyError, TypeError, ValueError, ArithmeticError):
            _fail("budget_report_fixture_invalid")


def verify_finance_archive(path: Path) -> None:
    try:
        with path.open("rb") as source:
            payload = source.read(32 * 1024 * 1024 + 1)
        if len(payload) > 32 * 1024 * 1024 or hashlib.sha256(payload).hexdigest() != FINANCE_ARCHIVE_SHA256:
            _fail("budget_finance_archive_invalid")
    except OSError:
        _fail("budget_finance_archive_invalid")


def verify_budget_transition_plan(root: Path = ROOT) -> dict[str, object]:
    root = Path(root)
    for path in REQUIRED_FILES:
        if not (root / path).is_file():
            _fail("budget_source_kit_incomplete")
    plan = _read(root, PLAN_PATH)
    validate_budget_transition_plan(plan)
    try:
        predecessor = verify_finance_implementation_plan(root)
    except FinanceTransitionError:
        _fail("budget_finance_predecessor_invalid")
    report_v1 = _read(root, REPORT_V1_PATH, expected_sha256=REPORT_V1_SHA256)
    report_v2 = _read(root, REPORT_V2_PATH, expected_sha256=REPORT_V2_SHA256)
    fixtures = _read(root, FIXTURE_PATH, expected_sha256=FIXTURE_SHA256)
    validate_budget_report_v2(report_v1, report_v2, fixtures)
    return {
        "schema_id": "hormuz.budget-transition-plan",
        "schema_version": 1,
        "status": "budget_preflight_plan_verified",
        "target_release": "1.1.0",
        "feature_issue": 217,
        "finance_source_commit": FINANCE_SOURCE_COMMIT,
        "finance_archive_sha256": FINANCE_ARCHIVE_SHA256,
        "predecessor_sqlite_schema_version": predecessor["sqlite_schema_version"],
        "predecessor_postgresql_schema_version": predecessor["postgresql_schema_version"],
        "planned_sqlite_schema_version": 9,
        "planned_postgresql_schema_version": 13,
        "report_schema_version": 2,
        "new_http_routes": 0,
        "budget_implemented": False,
        "feature_preflight_accepted": False,
        "final_candidate_accepted": False,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--finance-archive", type=Path)
    args = parser.parse_args(argv)
    try:
        result = verify_budget_transition_plan(args.repo_root)
        if args.finance_archive is not None:
            verify_finance_archive(args.finance_archive)
        result["finance_archive_verified"] = args.finance_archive is not None
        print(json.dumps(result, sort_keys=True))
        return 0
    except BudgetTransitionError as error:
        print(str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
