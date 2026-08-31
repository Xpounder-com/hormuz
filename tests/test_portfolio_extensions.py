"""Offline design/fixture proof, not runtime authorization or live-source proof."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tools import verify_portfolio_extensions as verifier
from tools._portfolio_wire_contract import PortfolioWireSchemaError, validate_wire_payload


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path("tests/fixtures/portfolio_intelligence/extension-v1-examples.json")


class PortfolioExtensionContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.examples = json.loads((ROOT / FIXTURES).read_bytes())["cases"]
        cls.bundles = {
            key: json.loads((ROOT / entry["path"]).read_bytes())
            for key, entry in verifier.WIRE_FILES.items()
        }

    def example(self, schema_id, variant="populated"):
        return copy.deepcopy(next(case["value"] for case in self.examples
                                  if case["name"] == f"{schema_id}:{variant}"))

    def validate(self, value):
        name = "budget" if value["schema_id"].startswith("hormuz.work-budget-") else "linear"
        verifier.validate_extension_payload(self.bundles[name], value["schema_id"], value)

    def rejected(self, value, code=None):
        with self.assertRaises(verifier.ExtensionContractError) as caught:
            self.validate(value)
        self.assertNotIn("SYNTHETIC_DO_NOT_ECHO", str(caught.exception))
        if code:
            self.assertEqual(str(caught.exception), code)

    def copy_kit(self, destination):
        for name in verifier.REQUIRED_FILES:
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, target)

    def test_approved_extension_contracts_pass_without_implementation_claims(self):
        result = verifier.validate_extension_contracts(ROOT)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["wire_schema_count"], 5)
        self.assertEqual(result["fixture_count"], 10)
        self.assertEqual(result["breaking_change_count"], 0)
        for name in ("runtime_implemented", "feature_preflight_accepted",
                     "live_integration_verified", "final_candidate_accepted"):
            self.assertIs(result[name], False)

    def test_minimal_and_populated_examples_cover_every_schema(self):
        names = set().union(*(set(bundle["x-hormuz-schema-ids"]) for bundle in self.bundles.values()))
        self.assertEqual(len(names), 5)
        self.assertEqual({case["name"] for case in self.examples},
                         {f"{name}:{variant}" for name in names for variant in ("minimal", "populated")})
        for case in self.examples:
            with self.subTest(case=case["name"]):
                self.validate(case["value"])

    def test_all_required_fields_are_required_and_unknown_content_is_rejected(self):
        for bundle in self.bundles.values():
            for name in bundle["x-hormuz-schema-ids"]:
                original = self.example(name)
                for field in bundle["$defs"][name]["required"]:
                    value = copy.deepcopy(original)
                    del value[field]
                    with self.subTest(schema=name, missing=field):
                        with self.assertRaises(verifier.ExtensionContractError):
                            verifier.validate_extension_payload(bundle, name, value)
                value = {**original, "raw_payload": "SYNTHETIC_DO_NOT_ECHO"}
                self.rejected(value)

    def test_nested_content_and_employee_fields_are_rejected(self):
        report = self.example("hormuz.work-budget-report")
        report["financial_observations"][0]["employee_rank"] = 1
        self.rejected(report)
        context = self.example("hormuz.linear-context-event")
        for field in ("title", "description", "url", "name", "credential", "prompt"):
            value = copy.deepcopy(context)
            value["object"][field] = "SYNTHETIC_DO_NOT_ECHO"
            self.rejected(value)
        context["relationships"][0]["parent"]["title"] = "SYNTHETIC_DO_NOT_ECHO"
        self.rejected(context)

    def test_schema_versions_and_counts_are_integers_not_boolean_or_float(self):
        for replacement in (True, 1.0, "1", 2):
            value = self.example("hormuz.work-budget-preview")
            value["schema_version"] = replacement
            self.rejected(value)
        for replacement in (True, 1.0, -1, 9007199254740992):
            value = self.example("hormuz.work-budget-report")
            value["enforcement"]["over_cap_attempts"] = replacement
            self.rejected(value)

    def test_decimal_and_currency_grammar_is_closed(self):
        for replacement in (0.1, "1e3", "01", " 1", "-1", "1" * 19, "0." + "1" * 19, "1\n"):
            value = self.example("hormuz.work-budget-report")
            value["plan_amount"] = replacement
            self.rejected(value)
        for replacement in ("usd", "US", "USDX", "USD\n", None):
            value = self.example("hormuz.work-budget-report")
            value["currency"] = replacement
            self.rejected(value)

    def test_preview_cannot_activate_or_claim_to_reserve_provider_work(self):
        for field in ("dry_run", "activation_permitted", "provider_egress_permitted"):
            value = self.example("hormuz.work-budget-preview")
            value[field] = not value[field]
            self.rejected(value)
        value = self.example("hormuz.work-budget-preview")
        value["reader_role"] = "finance_viewer"
        self.rejected(value)

    def test_preview_pins_all_existing_ceiling_classes_and_bounded_expiry(self):
        for field in ("ceiling_classes_evaluated", "restriction_reasons"):
            value = self.example("hormuz.work-budget-preview")
            value[field].append(value[field][0])
            self.rejected(value)
        value = self.example("hormuz.work-budget-preview")
        value["ceiling_classes_evaluated"].remove("actor")
        self.rejected(value)
        for replacement in ("2026-08-31T11:59:59Z", "2026-08-31T12:15:01Z"):
            value = self.example("hormuz.work-budget-preview")
            value["expires_at"] = replacement
            self.rejected(value, "extension_preview_expiry_invalid")

    def test_preview_counts_and_inconclusive_evidence_cannot_claim_success(self):
        value = self.example("hormuz.work-budget-preview")
        value["simulation"]["allowed_attempts"] = 999
        self.rejected(value, "extension_preview_counts_invalid")
        value = self.example("hormuz.work-budget-preview", "minimal")
        value["result"] = "compatible"
        self.rejected(value, "extension_preview_evidence_missing")

    def test_preview_result_and_reasons_match_known_simulation_outcomes(self):
        value = self.example("hormuz.work-budget-preview")
        value.update(result="compatible", restriction_reasons=[])
        self.rejected(value, "extension_preview_result_invalid")
        value["simulation"].update(allowed_attempts=20, denied_attempts=0)
        self.validate(value)
        value["restriction_reasons"] = ["budget_ceiling"]
        self.rejected(value, "extension_preview_result_invalid")
        value["result"] = "would_restrict"
        self.rejected(value, "extension_preview_result_invalid")
        value.update(result="compatible", restriction_reasons=[])
        value["simulation"].update(evaluated_attempts=0, allowed_attempts=0)
        self.rejected(value, "extension_preview_result_invalid")
        value["result"] = "inconclusive"
        self.validate(value)

    def test_remaining_budget_preserves_unknown_and_signed_overspend(self):
        value = self.example("hormuz.work-budget-report")
        value["enforcement"]["remaining_amount"] = "99"
        self.rejected(value, "extension_remaining_amount_invalid")
        value = self.example("hormuz.work-budget-report", "minimal")
        value["enforcement"]["remaining_amount"] = "0"
        self.rejected(value, "extension_remaining_evidence_missing")
        value = self.example("hormuz.work-budget-report")
        value["plan_amount"] = "1"
        value["enforcement"]["remaining_amount"] = "-11"
        self.validate(value)

    def test_remaining_amount_covers_the_full_exact_subtraction_range(self):
        for amount, remaining in (
                ("999999999999999999", "-2999999999999999997"),
                ("999999999999999999.999999999999999999",
                 "-2999999999999999999.999999999999999997")):
            with self.subTest(amount=amount):
                value = self.example("hormuz.work-budget-report", "minimal")
                value["plan_amount"] = "0"
                value["enforcement"].update(committed_amount=amount, pending_reservation_amount=amount,
                                             uncertain_reservation_amount=amount,
                                             remaining_amount=remaining, over_cap_attempts=0,
                                             reason_code="known")
                self.validate(value)
                value["enforcement"]["remaining_amount"] = "-1"
                self.rejected(value, "extension_remaining_amount_invalid")

    def test_report_requires_a_positive_actual_activation_generation(self):
        value = self.example("hormuz.work-budget-report")
        value["activation_generation"] = 0
        self.rejected(value)

    def test_broader_totals_cannot_be_disclosed_as_work_scope_cost(self):
        value = self.example("hormuz.work-budget-report")
        item = value["financial_observations"][0]
        item["scope_status"] = "broader_than_work_scope"
        self.rejected(value, "extension_finance_scope_invalid")
        item.update(amount=None, currency=None, reason_code="scope_mismatch",
                    finalization="not_applicable")
        self.validate(value)

    def test_unavailable_cost_and_estimate_provenance_are_explicit(self):
        for field, replacement in (("amount", "0"), ("currency", "USD")):
            value = self.example("hormuz.work-budget-report", "minimal")
            value["financial_observations"][0][field] = replacement
            self.rejected(value, "extension_finance_unavailable_invalid")
        value = self.example("hormuz.work-budget-report")
        value["financial_observations"][0]["rate_card"] = None
        self.rejected(value, "extension_rate_card_missing")

    def test_exact_scope_unknown_amount_preserves_known_provider_provenance(self):
        for reason in ("missing_evidence", "unsupported_currency", "not_available"):
            with self.subTest(reason=reason):
                value = self.example("hormuz.work-budget-report", "minimal")
                value["financial_observations"][0].update(
                    basis="provider_aggregate", source_kind="provider_api", finalization="unconfirmed",
                    scope_status="matches_work_scope", reason_code=reason)
                original = copy.deepcopy(value)
                self.validate(value)
                self.assertEqual(value, original)
                value["financial_observations"][0]["amount"] = "0"
                self.rejected(value, "extension_finance_currency_or_amount_invalid")
        value = self.example("hormuz.work-budget-report", "minimal")
        value["financial_observations"][0].update(
            basis="provider_aggregate", source_kind="provider_api", finalization="unconfirmed",
            scope_status="matches_work_scope", reason_code="known")
        self.rejected(value, "extension_finance_currency_or_amount_invalid")

    def test_provider_final_requires_authoritative_finalized_scope(self):
        for change in ({"finalization": "unconfirmed"}, {"source_kind": "configured_rates"},
                       {"scope_status": "unattributed"}):
            value = self.example("hormuz.work-budget-report")
            item = value["financial_observations"][0]
            item.update(basis="provider_final", rate_card=None,
                        source_kind="final_invoice", finalization="finalized")
            item.update(change)
            self.rejected(value)
        value = self.example("hormuz.work-budget-report")
        value["financial_observations"][0].update(
            basis="provider_final", rate_card=None, source_kind="final_invoice", finalization="finalized")
        self.validate(value)

    def test_forecast_missing_inputs_are_not_fabricated_zero(self):
        value = self.example("hormuz.work-budget-report", "minimal")
        value["forecast"]["projected_amount"] = "0"
        self.rejected(value, "extension_forecast_unavailable_invalid")
        value = self.example("hormuz.work-budget-report")
        value["forecast"]["method"] = "guaranteed_savings"
        self.rejected(value)
        value = self.example("hormuz.work-budget-report")
        value["forecast"]["currency"] = "EUR"
        self.rejected(value, "extension_forecast_currency_mismatch")

    def test_cost_basis_cannot_relabel_configured_rates_as_provider_evidence(self):
        for basis in ("provider_aggregate", "provider_final", "credit_or_discount"):
            value = self.example("hormuz.work-budget-report")
            value["financial_observations"][0].update(basis=basis, finalization="unconfirmed")
            self.rejected(value)
        value = self.example("hormuz.work-budget-report", "minimal")
        value["financial_observations"][0]["finalization"] = "finalized"
        self.rejected(value)

    def test_allocated_estimate_requires_its_own_versioned_rule_without_approving_one(self):
        value = self.example("hormuz.work-budget-report")
        item = value["financial_observations"][0]
        item.update(basis="allocated_estimate", source_kind="derived_allocation", rate_card=None)
        self.rejected(value)
        item["allocation_rule"] = {"id": "synthetic-not-approved", "version": 1, "content_digest": "a" * 64}
        self.validate(value)  # Shape only; the source contract still forbids unapproved allocation.

    def test_incomplete_known_preview_counts_cannot_exceed_the_population(self):
        value = self.example("hormuz.work-budget-preview")
        value["result"] = "inconclusive"
        value["simulation"].update(allowed_attempts=21, denied_attempts=None, inconclusive_attempts=None)
        self.rejected(value)

    def test_exact_projection_rejects_wrong_formula_time_basis_and_incomplete_coverage(self):
        for field, replacement in (("projected_amount", "20.000000000000000001"),
                                   ("elapsed_seconds", 0), ("elapsed_seconds", 100),
                                   ("basis_amount", "9"), ("period_seconds", 1339200)):
            value = self.example("hormuz.work-budget-report")
            value["forecast"][field] = replacement
            self.rejected(value)
        value = self.example("hormuz.work-budget-report")
        value["coverage"].update(priced_attempts=19, reason_code="incomplete_coverage")
        self.rejected(value)

    def test_report_exact_arithmetic_does_not_mutate_ambient_decimal_context(self):
        from decimal import Inexact, Rounded, localcontext
        with localcontext() as context:
            context.prec = 2
            context.traps[Inexact] = True
            context.traps[Rounded] = True
            before = dict(context.flags)
            self.validate(self.example("hormuz.work-budget-report"))
            self.assertEqual(context.flags, before)

    def test_coverage_cannot_exceed_declared_population_or_drop_unknown(self):
        value = self.example("hormuz.work-budget-report")
        value["coverage"]["included_attempts"] = 21
        self.rejected(value, "extension_coverage_counts_invalid")
        value = self.example("hormuz.work-budget-report", "minimal")
        value["coverage"]["reason_code"] = "known"
        self.rejected(value, "extension_coverage_unknown_invalid")

    def test_partial_coverage_respects_known_population_and_ancestor_bounds(self):
        cases = (
            {"population_attempts": 20, "unattributed_attempts": 21},
            {"population_attempts": 20, "unsupported_attempts": 21},
            {"population_attempts": 20, "included_attempts": 12, "unattributed_attempts": 9},
            {"population_attempts": 20, "pricing_eligible_attempts": 21},
            {"population_attempts": 20, "priced_attempts": 21},
            {"included_attempts": 10, "priced_attempts": 11},
            {"population_attempts": 20, "pricing_eligible_attempts": 10, "unattributed_attempts": 11},
            {"population_attempts": 20, "priced_attempts": 10, "unattributed_attempts": 11},
        )
        for known_counts in cases:
            with self.subTest(known_counts=known_counts):
                value = self.example("hormuz.work-budget-report", "minimal")
                value["coverage"].update(known_counts)
                self.rejected(value, "extension_coverage_counts_invalid")

    def test_consistent_partial_coverage_preserves_unknown_counts(self):
        for population in (None, 20):
            with self.subTest(population=population):
                value = self.example("hormuz.work-budget-report", "minimal")
                value["coverage"].update(population_attempts=population,
                                         unattributed_attempts=5, priced_attempts=10)
                before = copy.deepcopy(value)
                self.validate(value)
                self.assertEqual(value, before)
                self.assertIsNone(value["coverage"]["included_attempts"])
                self.assertIsNone(value["coverage"]["pricing_eligible_attempts"])

    def test_windows_and_timestamps_are_valid_utc_and_ordered(self):
        for field, replacement in (("start_at", "2026-02-31T00:00:00Z"),
                                   ("start_at", "2026-09-01T00:00:00Z"),
                                   ("end_at", "2026-08-01T00:00:00+00:00"),
                                   ("end_at", "2026-08-01T00:00:00Z")):
            value = self.example("hormuz.work-budget-report")
            value["window"][field] = replacement
            self.rejected(value)

    def test_linear_all_entity_types_are_distinct_from_frozen_issue_outcomes(self):
        for kind in ("initiative", "project", "cycle", "issue"):
            value = self.example("hormuz.linear-context-event", "minimal")
            value["object"]["kind"] = kind
            self.validate(value)
        old = json.loads((ROOT / "docs/portfolio-intelligence-wire-v1.json").read_bytes())
        original = json.loads((ROOT / "tests/fixtures/portfolio_intelligence/wire-v1-examples.json").read_bytes())
        event = next(case["value"] for case in original["cases"]
                     if case["name"] == "hormuz.work-outcome-event:minimal")
        for kind in ("initiative", "project", "cycle"):
            value = {**event, "object_type": kind}
            with self.assertRaises(PortfolioWireSchemaError):
                validate_wire_payload(old, "hormuz.work-outcome-event", value)

    def test_linear_relationships_are_typed_non_self_and_not_forced_to_one_parent(self):
        value = self.example("hormuz.linear-context-event")
        second = copy.deepcopy(value["relationships"][0])
        second["parent"]["id"] = "33333333-3333-4333-8333-333333333333"
        value["relationships"].append(second)
        self.validate(value)
        value["relationships"][0]["parent"] = dict(value["object"])
        self.rejected(value, "extension_relationship_invalid")
        value = self.example("hormuz.linear-context-event")
        value["relationships"][0]["kind"] = "cycle_issue"
        self.rejected(value, "extension_relationship_invalid")

    def test_linear_unknown_or_partial_relationships_never_mean_an_empty_complete_set(self):
        value = self.example("hormuz.linear-context-event", "minimal")
        value["relationships"] = self.example("hormuz.linear-context-event")["relationships"]
        self.rejected(value, "extension_relationship_coverage_invalid")
        value = self.example("hormuz.linear-context-event")
        value["relationship_coverage"] = "partial"
        self.validate(value)

    def test_linear_opaque_ids_cannot_encode_names_or_urls(self):
        for replacement in ("SYNTHETIC_DO_NOT_ECHO", "https://linear.app/issues/private", "alice@example.invalid"):
            value = self.example("hormuz.linear-context-event")
            value["object"]["id"] = replacement
            self.rejected(value)

    def test_linear_raw_context_is_admin_only_and_never_associated_or_controlled(self):
        for field, replacement in (("reader_role", "finance_viewer"), ("evidence_level", "controlled"),
                                   ("association_eligibility", "eligible")):
            value = self.example("hormuz.linear-context-event")
            value[field] = replacement
            self.rejected(value)

    def test_linear_missing_source_time_cannot_match_a_historical_work_binding(self):
        value = self.example("hormuz.linear-context-event")
        value["event_at"] = None
        self.rejected(value, "extension_context_binding_invalid")
        value = self.example("hormuz.linear-context-event", "minimal")
        self.assertIsNone(value["event_at"])
        self.assertEqual(value["scope_state"], "unmatched")
        self.validate(value)

    def test_linear_revision_order_does_not_use_delivery_or_uuid_order(self):
        value = self.example("hormuz.linear-context-event")
        value["revision"]["kind"] = "delivery_uuid"
        self.rejected(value)
        value = self.example("hormuz.linear-context-event", "minimal")
        value["revision"]["value"] = "1"
        self.rejected(value, "extension_revision_invalid")
        value = self.example("hormuz.linear-context-event")
        value["revision"]["value"] = "2026-08-99T00:00:00Z"
        self.rejected(value, "extension_revision_invalid")

    def test_linear_supersession_requires_current_known_complete_revision(self):
        target = "44444444-4444-4444-8444-444444444444"
        for state in ("late", "incomparable", "unknown"):
            with self.subTest(ordering_state=state):
                value = self.example("hormuz.linear-context-event")
                value.update(ordering_state=state, supersedes_context_event_id=target)
                self.rejected(value, "extension_context_supersession_invalid")
        value = self.example("hormuz.linear-context-event", "minimal")
        value["supersedes_context_event_id"] = target
        self.rejected(value, "extension_context_supersession_invalid")
        for coverage in ("partial", "unknown", "not_applicable"):
            with self.subTest(relationship_coverage=coverage):
                value = self.example("hormuz.linear-context-event")
                value.update(relationship_coverage=coverage, relationships=[],
                             supersedes_context_event_id=target)
                self.rejected(value, "extension_context_supersession_invalid")
        value = self.example("hormuz.linear-context-event")
        value["supersedes_context_event_id"] = target
        self.validate(value)

    def test_linear_ingestion_clock_is_distinct_and_page_cannot_leak_other_tenants(self):
        value = self.example("hormuz.linear-context-event")
        value["ingested_at"] = "2026-08-31T11:59:00Z"
        self.validate(value)  # Different component clocks are not causal proof.
        page = self.example("hormuz.linear-context-page")
        page["items"][0]["organization_id"] = "other-tenant"
        self.rejected(page, "extension_page_scope_invalid")
        page = self.example("hormuz.linear-context-page")
        page["items"][0]["ingested_at"] = "2026-08-31T12:01:00Z"
        self.validate(page)  # A database sequence, not a cross-clock comparison, defines membership.

    def test_linear_commit_sequence_is_required_and_positive(self):
        for replacement in (None, 0, True, 1.0):
            with self.subTest(sequence=replacement):
                value = self.example("hormuz.linear-context-event")
                if replacement is None:
                    value.pop("commit_sequence", None)
                else:
                    value["commit_sequence"] = replacement
                self.rejected(value)

    def test_linear_page_uses_commit_sequence_even_when_timestamp_looks_older(self):
        page = self.example("hormuz.linear-context-page")
        page["items"][0].update(commit_sequence=2, ingested_at="2026-08-31T11:58:00Z")
        self.rejected(page, "extension_page_snapshot_invalid")

    def test_linear_page_rejects_duplicate_commit_sequences(self):
        page = self.example("hormuz.linear-context-page")
        page["items"][0]["commit_sequence"] = 1
        duplicate = copy.deepcopy(page["items"][0])
        duplicate["context_event_id"] = "00000000-0000-4000-8000-000000000000"
        page["items"].append(duplicate)
        self.rejected(page, "extension_page_sequence_duplicate")

    def large_context_page(self, relationship_count):
        page = self.example("hormuz.linear-context-page", "minimal")
        page["snapshot_sequence"] = 100
        for index in range(100):
            item = self.example("hormuz.linear-context-event")
            item["context_event_id"] = f"50000000-0000-4000-8000-{index:012x}"
            item["source_delivery_id"] = f"60000000-0000-4000-8000-{index:012x}"
            item["object"]["id"] = f"40000000-0000-4000-8000-{index:012x}"
            item["binding"].update(binding_event_id=f"binding-test-{index}", registry_sequence=index + 1)
            if "commit_sequence" in item:
                item["commit_sequence"] = index + 1
            item["source_team_ids"] = [f"20000000-0000-4000-8000-{team:012x}" for team in range(100)]
            item["relationships"] = [
                {"kind": "initiative_project", "parent": {
                    "kind": "initiative", "id": f"30000000-0000-4000-8000-{parent:012x}"}}
                for parent in range(relationship_count)]
            page["items"].append(item)
        page["items"].reverse()
        return page

    def test_linear_page_can_use_the_advertised_array_bounds_below_one_mib(self):
        page = self.large_context_page(20)
        self.assertGreaterEqual(verifier.MAX_PAYLOAD_MEMBERS,
                                self.bundles["linear"]["x-hormuz-transport"]["response_maximum_bytes"])
        self.assertLess(len(json.dumps(page, separators=(",", ":")).encode("utf-8")), 1048576)
        self.validate(page)

    def test_linear_page_above_one_mib_is_rejected_without_truncation(self):
        page = self.large_context_page(100)
        self.assertGreater(len(json.dumps(page, separators=(",", ":")).encode("utf-8")), 1048576)
        self.rejected(page, "wire_payload_bytes_exceeded")

    def test_linear_cursor_pair_and_snapshot_bounds_are_consistent(self):
        for has_more, cursor in ((True, None), (False, "opaque")):
            value = self.example("hormuz.linear-context-page", "minimal")
            value.update(has_more=has_more, next_cursor=cursor)
            self.rejected(value, "extension_page_cursor_invalid")
        value = self.example("hormuz.linear-context-page")
        value["items"] *= 101
        self.rejected(value)
        value = self.example("hormuz.linear-context-page")
        value["items"].append(copy.deepcopy(value["items"][0]))
        self.rejected(value, "extension_page_identity_duplicate")

    def test_retention_is_separate_not_a_fabricated_provider_event(self):
        value = self.example("hormuz.linear-context-retention")
        self.validate(value)
        for field, replacement in (("reason_code", "source_deleted"), ("source_payload", {})):
            changed = {**value, field: replacement}
            self.rejected(changed)

    def test_frozen_wire_and_plan_files_cannot_be_changed_or_digest_repointed(self):
        for name in verifier.FROZEN_FILES:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self.copy_kit(root)
                (root / name).write_bytes((root / name).read_bytes() + b"\n")
                with self.assertRaisesRegex(verifier.ExtensionContractError, "extension_frozen_digest_mismatch"):
                    verifier.validate_extension_contracts(root)
        for name in ("budget", "linear"):
            with self.subTest(bundle=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self.copy_kit(root)
                path = root / verifier.WIRE_FILES[name]["path"]
                value = json.loads(path.read_bytes())
                value["$defs"][value["x-hormuz-schema-ids"][0]]["additionalProperties"] = True
                path.write_text(json.dumps(value))
                with self.assertRaisesRegex(verifier.ExtensionContractError, "extension_wire_digest_mismatch"):
                    verifier.validate_extension_contracts(root)

    def test_manifest_unknown_field_or_live_claim_fails_closed(self):
        for field, replacement in (("unreviewed", True), ("runtime_implemented", True),
                                   ("feature_preflight_accepted", True), ("new_http_routes", ["/bad"])):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self.copy_kit(root)
                path = root / verifier.CONTRACT_PATH
                value = json.loads(path.read_bytes())
                value[field] = replacement
                path.write_text(json.dumps(value))
                with self.assertRaisesRegex(verifier.ExtensionContractError, "extension_manifest_changed"):
                    verifier.validate_extension_contracts(root)

    def test_duplicate_json_and_oversized_contract_are_rejected_safely(self):
        for raw in (b'{"schema_id":"SYNTHETIC_DO_NOT_ECHO","schema_id":"x"}',
                    b" " * (verifier.MAX_FILE_BYTES + 1), b'{"value":NaN}'):
            with self.subTest(size=len(raw)), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self.copy_kit(root)
                (root / verifier.CONTRACT_PATH).write_bytes(raw)
                with self.assertRaises(verifier.ExtensionContractError) as caught:
                    verifier.validate_extension_contracts(root)
                self.assertNotIn("SYNTHETIC_DO_NOT_ECHO", str(caught.exception))

    def test_verifier_reads_each_frozen_file_once_and_hashes_the_parsed_buffer(self):
        with mock.patch.object(verifier, "_read_bytes", wraps=verifier._read_bytes) as reads:
            verifier.validate_extension_contracts(ROOT)
        for name in (*verifier.FROZEN_FILES, *(v["path"] for v in verifier.WIRE_FILES.values())):
            self.assertEqual(sum(call.args[0] == ROOT / name for call in reads.call_args_list), 1)

    def test_standalone_verifier_runs_isolated_without_install_or_environment(self):
        script = ROOT / "tools/verify_portfolio_extensions.py"
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run([sys.executable, "-I", str(script)], cwd=temporary,
                                    capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["wire_schema_count"], 5)


if __name__ == "__main__":
    unittest.main()
