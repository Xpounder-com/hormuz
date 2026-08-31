#!/usr/bin/env python3
"""Offline verification of owner-approved additive design contracts.

This is a synthetic fixture linter, not runtime request validation, source
authentication, authorization, persistence, or a feature-preflight acceptance.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from decimal import Context, Decimal, localcontext
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools._portfolio_wire_contract import (
    PortfolioWireSchemaError, validate_wire_bundle, validate_wire_payload,
)

CONTRACT_PATH = "docs/portfolio-extension-contract-v1.json"
MANIFEST_SHA256 = "2d83530ee7ea10ec5dcaf7249a7ed7b3ece1e5b9a7822c0e36ef21a9cac70171"
MAX_FILE_BYTES = 256 * 1024
FROZEN_FILES = {
    "docs/portfolio-intelligence-contract-v1.json": "40c27f7555631d2ecaa004f6481eef07d565ccebcb5e206106c5820ee3586c49",
    "docs/portfolio-intelligence-wire-v1.json": "26ef36b12d475d5b8354e45abfc92c76187ca6ff5aafb4b0ebce6fab439feb80",
    "hormuz/portfolio-registry-wire-v1.json": "20a5253990169e80961671a8d9129bd88afcac065ec071a03766f259ef4ea092",
    "hormuz/portfolio-attribution-wire-v1.json": "999c62ac8d3dde96b4fd961632cba0e2b196e2a9bce62d9693757c256ed8e3f4",
    "hormuz/portfolio-outcome-wire-v1.json": "0716c5483b6be72736d357f4ff318c89eeac10fa3f3ae86d3f3bdb9eb99f53a2",
    "docs/finance-transition-plan-v1.json": "452d9a533b8fd56118a892840fc48a7c6cf99b1be54efce34325a27c7ef0cf8d",
    "docs/finance-source-contract-v1.json": "8926e2dc1913c0130421b5f2384e2c82fb2d738bcf85495ee1368b2bb2a8f01b",
    "tests/fixtures/portfolio_intelligence/v1.0.0-contract-manifest.json": "6e7f264de74f8f998842b49001a61b3640a05927d74bb31e4eec5ff8ab89504f"
}
WIRE_FILES = {
    "budget": {
        "path": "docs/work-budget-reports-wire-v1.json",
        "sha256": "fedc765980a6b40e05dde9d08fd92b724f390d6d59feb217c70374c07c19ddf4",
        "schema_ids": [
            "hormuz.work-budget-preview",
            "hormuz.work-budget-report"
        ]
    },
    "linear": {
        "path": "docs/linear-context-wire-v1.json",
        "sha256": "80acbb06480e3b156d8bbb81fe2aad8bfb287b135c43f1770ec124ca8927d19e",
        "schema_ids": [
            "hormuz.linear-context-event",
            "hormuz.linear-context-page",
            "hormuz.linear-context-retention"
        ]
    }
}
FIXTURE_PATH = "tests/fixtures/portfolio_intelligence/extension-v1-examples.json"
FIXTURE_SHA256 = "44d92038776d2fcc53c4144d4e918b4b198070b6fab8f2790e82d072fd36e9da"
DOCUMENTATION = (
    "docs/PORTFOLIO_EXTENSIONS.md",
    "docs/decisions/0011-additive-budget-reports-and-linear-context.md",
)
REQUIRED_FILES = (
    CONTRACT_PATH, *FROZEN_FILES, *(item["path"] for item in WIRE_FILES.values()),
    FIXTURE_PATH, *DOCUMENTATION, "tools/verify_portfolio_extensions.py",
    "tools/_portfolio_wire_contract.py", "tests/test_portfolio_extensions.py",
    "tests/test_portfolio_extension_packaging.py",
)
CEILING_CLASSES = frozenset({
    "organization", "team", "actor", "application", "policy", "portfolio", "initiative", "use_case",
})
_TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z\Z")


class ExtensionContractError(ValueError):
    """Fixed content-free design-verification failure."""


def _fail(code):
    raise ExtensionContractError(code)


def _read_bytes(path):
    try:
        with path.open("rb") as source:
            raw = source.read(MAX_FILE_BYTES + 1)
    except OSError:
        _fail("extension_file_unavailable")
    if not 1 <= len(raw) <= MAX_FILE_BYTES:
        _fail("extension_file_bounds")
    return raw


def _json(raw):
    def pairs(items):
        result = {}
        for name, value in items:
            if name in result:
                _fail("extension_json_duplicate")
            result[name] = value
        return result

    def nonfinite(_value):
        _fail("extension_json_nonfinite")

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=nonfinite)
    except ExtensionContractError:
        raise
    except (ValueError, UnicodeError, RecursionError):
        _fail("extension_json_invalid")


def _time(value):
    if not isinstance(value, str) or not _TIMESTAMP.fullmatch(value):
        raise ValueError("invalid_time")
    return datetime.fromisoformat(value)


def _integer_payload(value):
    # These records have decimal strings and integer counters, never JSON floats.
    # Iterative depth/member bounds also guard cyclic Python test inputs.
    pending, members = [(value, 0)], 0
    while pending:
        item, depth = pending.pop()
        if depth > 16 or members > 16384:
            _fail("extension_payload_bounds")
        if isinstance(item, float):
            _fail("extension_payload_integer_required")
        if isinstance(item, dict):
            members += len(item)
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            members += len(item)
            pending.extend((child, depth + 1) for child in item)


def _preview(value):
    if set(value["ceiling_classes_evaluated"]) != CEILING_CLASSES:
        _fail("extension_preview_ceiling_coverage_invalid")
    expiry = _time(value["expires_at"]) - _time(value["as_of"])
    if not 0 < expiry.total_seconds() <= 900:
        _fail("extension_preview_expiry_invalid")
    if (value["expected_active_version"] is None) != (value["expected_activation_generation"] == 0):
        _fail("extension_preview_activation_binding_invalid")
    counts = value["simulation"]
    fields = ("evaluated_attempts", "allowed_attempts", "denied_attempts", "inconclusive_attempts")
    if counts["evaluated_attempts"] is not None:
        known = [counts[field] for field in fields[1:] if counts[field] is not None]
        if sum(known) > counts["evaluated_attempts"]:
            _fail("extension_preview_counts_invalid")
    if any(counts[field] is None for field in fields):
        if value["result"] != "inconclusive":
            _fail("extension_preview_evidence_missing")
    elif counts["evaluated_attempts"] != sum(counts[field] for field in fields[1:]):
        _fail("extension_preview_counts_invalid")
    elif counts["inconclusive_attempts"] and value["result"] != "inconclusive":
        _fail("extension_preview_evidence_missing")
    if value["result"] == "would_restrict" and not value["restriction_reasons"]:
        _fail("extension_preview_restriction_missing")


def _financial_observation(item, currency):
    if item["basis"] == "not_available":
        if (any(item[field] is not None for field in ("amount", "currency", "rate_card", "allocation_rule"))
                or item["reason_code"] == "known" or item["source_kind"] != "not_available"
                or item["finalization"] != "not_applicable" or item["scope_status"] != "not_available"):
            _fail("extension_finance_unavailable_invalid")
        return
    if item["basis"] == "configured_rate_card_estimate":
        if item["rate_card"] is None:
            _fail("extension_rate_card_missing")
        if item["source_kind"] != "configured_rates" or item["allocation_rule"] is not None or item["finalization"] != "not_applicable":
            _fail("extension_finance_basis_invalid")
    elif item["basis"] == "allocated_estimate":
        if (item["source_kind"] != "derived_allocation" or item["allocation_rule"] is None
                or item["rate_card"] is not None or item["finalization"] != "not_applicable"):
            _fail("extension_allocation_rule_invalid")
    else:
        if (item["source_kind"] not in ("provider_api", "customer_import", "final_invoice")
                or item["rate_card"] is not None or item["allocation_rule"] is not None):
            _fail("extension_finance_basis_invalid")
        if item["basis"] == "provider_final" and item["finalization"] != "finalized":
            _fail("extension_finance_final_authority_invalid")
        if item["basis"] == "provider_aggregate" and item["finalization"] != "unconfirmed":
            _fail("extension_finance_basis_invalid")
        if item["basis"] == "credit_or_discount" and item["finalization"] == "not_applicable":
            _fail("extension_finance_basis_invalid")
    if item["scope_status"] != "matches_work_scope":
        if item["amount"] is not None or item["currency"] is not None or item["reason_code"] != "scope_mismatch":
            _fail("extension_finance_scope_invalid")
        return
    if item["amount"] is None or item["currency"] != currency or item["reason_code"] != "known":
        _fail("extension_finance_currency_or_amount_invalid")
    if Decimal(item["amount"]) < 0 and item["basis"] != "credit_or_discount":
        _fail("extension_finance_signed_amount_invalid")


def _coverage(value):
    fields = ("population_attempts", "included_attempts", "unattributed_attempts",
              "unsupported_attempts", "pricing_eligible_attempts", "priced_attempts")
    if any(value[name] is None for name in fields):
        if value["reason_code"] == "known":
            _fail("extension_coverage_unknown_invalid")
    population = value["population_attempts"]
    partition = [value[name] for name in fields[1:4]]
    if population is not None and all(item is not None for item in partition):
        if sum(partition) != population:
            _fail("extension_coverage_counts_invalid")
    for smaller, larger in (("included_attempts", "population_attempts"),
                            ("pricing_eligible_attempts", "included_attempts"),
                            ("priced_attempts", "pricing_eligible_attempts")):
        if value[smaller] is not None and value[larger] is not None and value[smaller] > value[larger]:
            _fail("extension_coverage_counts_invalid")


def _forecast(value):
    forecast, coverage = value["forecast"], value["coverage"]
    fields = ("rule", "projected_amount", "currency", "basis_amount", "elapsed_seconds", "period_seconds")
    if forecast["method"] == "not_available":
        if any(forecast[name] is not None for name in fields) or forecast["cost_basis"] != "not_available" or forecast["reason_code"] == "known":
            _fail("extension_forecast_unavailable_invalid")
        return
    if any(forecast[name] is None for name in fields):
        _fail("extension_forecast_input_missing")
    if forecast["currency"] != value["currency"]:
        _fail("extension_forecast_currency_mismatch")
    if forecast["cost_basis"] != "configured_rate_card_estimate" or forecast["reason_code"] != "known":
        _fail("extension_forecast_basis_invalid")
    if (coverage["reason_code"] != "known"
            or any(coverage[name] != coverage["population_attempts"] for name in
                   ("included_attempts", "pricing_eligible_attempts", "priced_attempts"))):
        _fail("extension_forecast_coverage_incomplete")
    elapsed, period = forecast["elapsed_seconds"], forecast["period_seconds"]
    start, end, as_of = (_time(value["window"]["start_at"]), _time(value["window"]["end_at"]), _time(value["as_of"]))
    if not 0 < elapsed < period or as_of - start != timedelta(seconds=elapsed) or end - start != timedelta(seconds=period):
        _fail("extension_forecast_time_basis_invalid")
    if value["enforcement"]["committed_amount"] is None or Decimal(forecast["basis_amount"]) != Decimal(value["enforcement"]["committed_amount"]):
        _fail("extension_forecast_basis_invalid")
    # Cross multiplication tests exact equality without division/rounding.
    with localcontext(Context(prec=96)):
        if Decimal(forecast["projected_amount"]) * elapsed != Decimal(forecast["basis_amount"]) * period:
            _fail("extension_forecast_amount_invalid")


def _report(value):
    if _time(value["window"]["start_at"]) >= _time(value["window"]["end_at"]):
        _fail("extension_window_invalid")
    if _time(value["generated_at"]) < _time(value["as_of"]):
        _fail("extension_report_snapshot_invalid")
    enforcement = value["enforcement"]
    charges = [enforcement[name] for name in ("committed_amount", "pending_reservation_amount", "uncertain_reservation_amount")]
    if any(item is None for item in charges):
        if enforcement["remaining_amount"] is not None or enforcement["reason_code"] == "known":
            _fail("extension_remaining_evidence_missing")
    else:
        with localcontext(Context(prec=96)):
            expected = Decimal(value["plan_amount"]) - sum((Decimal(item) for item in charges), Decimal(0))
        if enforcement["remaining_amount"] is None or Decimal(enforcement["remaining_amount"]) != expected:
            _fail("extension_remaining_amount_invalid")
    for item in value["financial_observations"]:
        _financial_observation(item, value["currency"])
    _coverage(value["coverage"])
    _forecast(value)


def _context(value):
    source = value["object"]
    if value["relationship_coverage"] in ("unknown", "not_applicable") and value["relationships"]:
        _fail("extension_relationship_coverage_invalid")
    pairs = {"initiative_project": ("project", "initiative"), "project_issue": ("issue", "project"),
             "cycle_issue": ("issue", "cycle"), "initiative_parent": ("initiative", "initiative")}
    for item in value["relationships"]:
        if (source["kind"], item["parent"]["kind"]) != pairs[item["kind"]] or source["id"] == item["parent"]["id"]:
            _fail("extension_relationship_invalid")
    for kind in ("project_issue", "cycle_issue"):
        if sum(item["kind"] == kind for item in value["relationships"]) > 1:
            _fail("extension_relationship_invalid")
    revision = value["revision"]
    if revision["kind"] == "unknown":
        if revision["value"] is not None or value["ordering_state"] not in ("unknown", "incomparable"):
            _fail("extension_revision_invalid")
    else:
        try:
            if revision["kind"] == "source_updated_at_v1":
                _time(revision["value"])
            elif (not isinstance(revision["value"], str)
                  or not re.fullmatch(r"(?:0|[1-9][0-9]{0,18})", revision["value"])
                  or int(revision["value"]) > 9223372036854775807):
                raise ValueError("invalid_counter")
        except (ValueError, TypeError):
            _fail("extension_revision_invalid")
    if value["scope_state"] == "matched":
        if value["binding"] is None or value["event_at"] is None or _time(value["event_at"]) > _time(value["observed_at"]):
            _fail("extension_context_binding_invalid")
    elif value["binding"] is not None:
        _fail("extension_context_binding_invalid")
    if (value["capture_kind"] == "webhook") != (value["source_delivery_id"] is not None):
        _fail("extension_context_delivery_invalid")
    if value["supersedes_context_event_id"] == value["context_event_id"]:
        _fail("extension_context_self_supersession")
    # Source/observation/DB clocks may differ. Do not falsify or order them as
    # causal proof. The source clock only restricts eligibility, not retention.


def _page(value):
    if value["has_more"] != (value["next_cursor"] is not None) or (value["has_more"] and not value["items"]):
        _fail("extension_page_cursor_invalid")
    identities, ordering = set(), []
    for item in value["items"]:
        _context(item)
        if item["organization_id"] != value["organization_id"]:
            _fail("extension_page_scope_invalid")
        if _time(item["ingested_at"]) > _time(value["as_of"]) or value["snapshot_sequence"] == 0:
            _fail("extension_page_snapshot_invalid")
        identity = item["connector_id"], item["context_event_id"]
        if identity in identities:
            _fail("extension_page_identity_duplicate")
        identities.add(identity)
        ordering.append((_time(item["event_at"] or item["observed_at"]), *identity))
    if ordering != sorted(ordering, reverse=True):
        _fail("extension_page_order_invalid")


def validate_extension_payload(bundle, schema_id, value):
    """Lint synthetic fixtures; this never authorizes a real request or source."""
    try:
        _integer_payload(value)
        validate_wire_payload(bundle, schema_id, value)
        if schema_id == "hormuz.work-budget-preview":
            _preview(value)
        elif schema_id == "hormuz.work-budget-report":
            _report(value)
        elif schema_id == "hormuz.linear-context-event":
            _context(value)
        elif schema_id == "hormuz.linear-context-page":
            _page(value)
        elif schema_id != "hormuz.linear-context-retention":
            _fail("extension_schema_unknown")
    except ExtensionContractError:
        raise
    except PortfolioWireSchemaError as exc:
        raise ExtensionContractError(str(exc)) from None
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise ExtensionContractError("extension_payload_invalid") from None


def validate_extension_contracts(root):
    root = Path(root)
    raw = _read_bytes(root / CONTRACT_PATH)
    manifest = _json(raw)
    if hashlib.sha256(raw).hexdigest() != MANIFEST_SHA256:
        _fail("extension_manifest_changed")
    for name in REQUIRED_FILES:
        if not (root / name).is_file():
            _fail("extension_source_kit_incomplete")
    for name, digest in FROZEN_FILES.items():
        raw = _read_bytes(root / name)
        if hashlib.sha256(raw).hexdigest() != digest:
            _fail("extension_frozen_digest_mismatch")
        _json(raw)
    bundles = {}
    for name, entry in WIRE_FILES.items():
        raw = _read_bytes(root / entry["path"])
        if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            _fail("extension_wire_digest_mismatch")
        bundle = _json(raw)
        try:
            validate_wire_bundle(bundle, set(entry["schema_ids"]))
        except PortfolioWireSchemaError:
            _fail("extension_wire_schema_invalid")
        bundles[name] = bundle
    raw = _read_bytes(root / FIXTURE_PATH)
    if hashlib.sha256(raw).hexdigest() != FIXTURE_SHA256:
        _fail("extension_fixture_digest_mismatch")
    examples = _json(raw)
    schemas = {name for entry in WIRE_FILES.values() for name in entry["schema_ids"]}
    expected_names = {f"{name}:{variant}" for name in schemas for variant in ("minimal", "populated")}
    if (set(examples) != {"schema_id", "schema_version", "synthetic", "live_integration_verified", "cases"}
            or examples["schema_id"] != "hormuz.portfolio-extension-examples"
            or type(examples["schema_version"]) is not int or examples["schema_version"] != 1
            or examples["synthetic"] is not True or examples["live_integration_verified"] is not False
            or len(examples["cases"]) != len(expected_names)
            or {case["name"] for case in examples["cases"]} != expected_names):
        _fail("extension_fixture_inventory_invalid")
    for case in examples["cases"]:
        bundle = bundles["budget" if case["schema_id"].startswith("hormuz.work-budget-") else "linear"]
        validate_extension_payload(bundle, case["schema_id"], case["value"])
    return {
        "schema_id": manifest["schema_id"], "schema_version": 1, "target_release": "1.1.0",
        "status": "passed", "wire_schema_count": len(schemas), "fixture_count": len(examples["cases"]),
        "breaking_change_count": 0, "runtime_implemented": False, "feature_preflight_accepted": False,
        "live_integration_verified": False, "final_candidate_accepted": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(validate_extension_contracts(args.repo_root), sort_keys=True))
    except ExtensionContractError as exc:
        print(json.dumps({"status": "failed", "reason_code": str(exc)}, sort_keys=True))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
