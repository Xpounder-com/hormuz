from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from tools._portfolio_wire_contract import (
    PortfolioWireSchemaError,
    validate_wire_bundle,
    validate_wire_payload,
)
from tools.verify_portfolio_intelligence_contract import (
    API_REQUEST_SCHEMAS,
    API_RESPONSE_SCHEMAS,
    ENTITY_SCHEMAS,
    WIRE_SCHEMA_PATH,
)


ROOT = Path(__file__).resolve().parents[1]


class PortfolioWireContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle = json.loads((ROOT / WIRE_SCHEMA_PATH).read_text(encoding="utf-8"))
        cls.examples = json.loads(
            (ROOT / "tests/fixtures/portfolio_intelligence/wire-v1-examples.json").read_text(
                encoding="utf-8"
            )
        )["cases"]
        cls.expected_ids = (
            set(API_REQUEST_SCHEMAS) | set(API_RESPONSE_SCHEMAS) | set(ENTITY_SCHEMAS.values())
        )

    def _example(self, schema_id: str, variant: str = "minimal") -> dict:
        return copy.deepcopy(next(
            case["value"] for case in self.examples
            if case["name"] == f"{schema_id}:{variant}"
        ))

    def test_every_owned_wire_schema_has_closed_bounded_field_definitions(self) -> None:
        validate_wire_bundle(self.bundle, self.expected_ids)
        self.assertEqual(len(self.expected_ids), 26)
        self.assertEqual(set(self.bundle["x-hormuz-schema-ids"]), self.expected_ids)
        for schema_id in self.expected_ids:
            schema = self.bundle["$defs"][schema_id]
            self.assertIs(schema["additionalProperties"], False)
            self.assertGreater(len(schema["properties"]), 2)

    def test_minimal_and_populated_examples_cover_every_schema_and_pass(self) -> None:
        self.assertEqual(len(self.examples), 52)
        self.assertEqual(
            {case["name"] for case in self.examples},
            {f"{schema}:{variant}" for schema in self.expected_ids
             for variant in ("minimal", "populated")},
        )
        for case in self.examples:
            with self.subTest(case=case["name"]):
                validate_wire_payload(self.bundle, case["schema_id"], case["value"])

    def test_every_wire_schema_rejects_unknown_fields_without_echoing_values(self) -> None:
        for schema_id in self.expected_ids:
            with self.subTest(schema_id=schema_id):
                value = self._example(schema_id)
                value["raw_connector_payloads"] = "SYNTHETIC_DO_NOT_ECHO"
                with self.assertRaisesRegex(
                    PortfolioWireSchemaError, "^wire_payload_unknown_field$"
                ) as caught:
                    validate_wire_payload(self.bundle, schema_id, value)
                self.assertNotIn("SYNTHETIC_DO_NOT_ECHO", str(caught.exception))

    def test_every_required_wire_field_is_actually_required(self) -> None:
        for schema_id in self.expected_ids:
            for field in self.bundle["$defs"][schema_id]["required"]:
                with self.subTest(schema_id=schema_id, field=field):
                    value = self._example(schema_id)
                    del value[field]
                    with self.assertRaisesRegex(
                        PortfolioWireSchemaError, "wire_payload_required_field_missing"
                    ):
                        validate_wire_payload(self.bundle, schema_id, value)

    def test_request_types_enums_bounds_and_nullability_are_enforced(self) -> None:
        cases = [
            ("hormuz.work-scope-create-request", "schema_version", True),
            ("hormuz.work-scope-create-request", "schema_id", "hormuz.unknown"),
            ("hormuz.work-scope-create-request", "kind", "employee"),
            ("hormuz.work-scope-create-request", "kind", None),
            ("hormuz.work-scope-create-request", "display_name", ""),
            ("hormuz.work-scope-create-request", "display_name", "x" * 121),
            ("hormuz.work-scope-create-request", "display_name", "example\n"),
            ("hormuz.work-scope-create-request", "owner_team_id", "team/path"),
            ("hormuz.work-budget-plan-request", "amount", 0.1),
            ("hormuz.work-budget-plan-request", "amount", "1e3"),
            ("hormuz.work-budget-plan-request", "amount", "1" * 19),
            ("hormuz.work-budget-plan-request", "amount", "-1"),
            ("hormuz.work-budget-plan-request", "currency", "usd"),
            ("hormuz.work-budget-plan-request", "output_token_cap", 10 ** 400),
            ("hormuz.portfolio-query", "limit", 0),
            ("hormuz.portfolio-query", "limit", 101),
            ("hormuz.portfolio-query", "limit", "50"),
            ("hormuz.portfolio-query", "cursor", "x" * 2049),
            ("hormuz.portfolio-query", "cursor", "example\n"),
            ("hormuz.portfolio-query", "organization_id", "other-tenant"),
            ("hormuz.model-scorecard", "employee_rank", 1),
        ]
        for schema_id, field, replacement in cases:
            with self.subTest(schema_id=schema_id, field=field, replacement_type=type(replacement)):
                value = self._example(schema_id)
                value[field] = replacement
                with self.assertRaises(PortfolioWireSchemaError):
                    validate_wire_payload(self.bundle, schema_id, value)

    def test_timestamp_validity_and_paired_query_window_are_enforced(self) -> None:
        for start, end in (
            ("2026-02-31T00:00:00Z", "2026-03-01T00:00:00Z"),
            ("2026-08-01T00:00:00+00:00", "2026-08-02T00:00:00Z"),
            ("2026-08-01T00:00:00Z", None),
        ):
            value = {"start_at": start}
            if end is not None:
                value["end_at"] = end
            with self.subTest(start=start, end=end), self.assertRaises(PortfolioWireSchemaError):
                validate_wire_payload(self.bundle, "hormuz.portfolio-query", value)

    def test_nested_record_content_cannot_escape_closed_schemas(self) -> None:
        value = self._example("hormuz.model-scorecard", "populated")
        value["cohorts"][0]["actual_model"]["prompt"] = "SYNTHETIC_DO_NOT_ECHO"
        with self.assertRaisesRegex(PortfolioWireSchemaError, "wire_payload_union_mismatch"):
            validate_wire_payload(self.bundle, "hormuz.model-scorecard", value)

    def test_collection_and_numeric_bounds_are_enforced(self) -> None:
        value = self._example("hormuz.work-scope-page", "populated")
        value["items"] *= 101
        with self.assertRaisesRegex(PortfolioWireSchemaError, "wire_payload_array_bounds"):
            validate_wire_payload(self.bundle, "hormuz.work-scope-page", value)
        value = self._example("hormuz.model-scorecard", "populated")
        value["cohorts"][0]["eligibility"]["observed_coverage"] = float("nan")
        with self.assertRaisesRegex(PortfolioWireSchemaError, "wire_payload_json_invalid"):
            validate_wire_payload(self.bundle, "hormuz.model-scorecard", value)

    def test_unknown_evidence_stays_null_instead_of_fabricated_zero(self) -> None:
        value = self._example("hormuz.model-scorecard", "populated")
        value["state"] = "inconclusive"
        value["pareto_cohort_ids"] = []
        cohort = value["cohorts"][0]
        cohort["eligibility"]["status"] = "inconclusive"
        cohort["eligibility"]["sample_count"] = None
        cohort["eligibility"]["observed_coverage"] = None
        cohort["drivers"]["retries"] = None
        value["coverage"]["eligible_governed_spend"] = {
            "numerator": None, "denominator": None, "ratio": None,
            "reason_code": "missing_evidence",
        }
        validate_wire_payload(self.bundle, "hormuz.model-scorecard", value)

    def test_missing_definition_or_envelope_identity_cannot_pass(self) -> None:
        bundle = copy.deepcopy(self.bundle)
        del bundle["$defs"]["hormuz.work-scope-create-request"]
        with self.assertRaisesRegex(PortfolioWireSchemaError, "wire_schema_inventory_changed"):
            validate_wire_bundle(bundle, self.expected_ids)
        bundle = copy.deepcopy(self.bundle)
        bundle["$defs"]["hormuz.work-scope-create-request"]["properties"]["schema_id"]["const"] = "hormuz.wrong"
        with self.assertRaisesRegex(PortfolioWireSchemaError, "wire_schema_envelope_identity_changed"):
            validate_wire_bundle(bundle, self.expected_ids)

    def test_explicit_mixed_schema_version_inventory_is_closed_and_enforced(self) -> None:
        bundle = copy.deepcopy(self.bundle)
        schema_id = "hormuz.work-scope-create-request"
        bundle["x-hormuz-schema-versions"] = {name: 1 for name in self.expected_ids}
        bundle["x-hormuz-schema-versions"][schema_id] = 2
        bundle["$defs"][schema_id]["properties"]["schema_version"]["const"] = 2
        validate_wire_bundle(bundle, self.expected_ids)

        query_bundle = copy.deepcopy(self.bundle)
        query_bundle["x-hormuz-schema-versions"] = {name: 1 for name in self.expected_ids}
        query_bundle["x-hormuz-schema-versions"]["hormuz.portfolio-query"] = 2
        query_bundle["$defs"]["hormuz.portfolio-query"]["x-hormuz-schema-version"] = 2
        validate_wire_bundle(query_bundle, self.expected_ids)
        query_bundle["x-hormuz-schema-versions"]["hormuz.portfolio-query"] = 1
        with self.assertRaisesRegex(PortfolioWireSchemaError, "wire_schema_query_identity_changed"):
            validate_wire_bundle(query_bundle, self.expected_ids)

        for mutation in ("missing", "extra", "boolean", "mismatch"):
            changed = copy.deepcopy(bundle)
            if mutation == "missing":
                del changed["x-hormuz-schema-versions"][schema_id]
            elif mutation == "extra":
                changed["x-hormuz-schema-versions"]["hormuz.unknown"] = 1
            elif mutation == "boolean":
                changed["x-hormuz-schema-versions"][schema_id] = True
            else:
                changed["$defs"][schema_id]["properties"]["schema_version"]["const"] = 1
            with self.subTest(mutation=mutation), self.assertRaises(PortfolioWireSchemaError):
                validate_wire_bundle(changed, self.expected_ids)

    def test_remote_dangling_or_cyclic_references_fail_closed(self) -> None:
        for target in (
            "https://untrusted.invalid/schema.json", "#/$defs/missing", "#/$defs/work_scope_ref"
        ):
            bundle = copy.deepcopy(self.bundle)
            bundle["$defs"]["work_scope_ref"]["properties"]["work_scope_id"] = {"$ref": target}
            with self.subTest(target=target), self.assertRaisesRegex(
                PortfolioWireSchemaError, "wire_schema_.*reference"
            ):
                validate_wire_bundle(bundle, self.expected_ids)

    def test_unbounded_or_unsupported_schema_assertions_fail_closed(self) -> None:
        for name, field in (("display_name", "maxLength"), ("count", "maximum")):
            bundle = copy.deepcopy(self.bundle)
            del bundle["$defs"][name][field]
            with self.subTest(name=name), self.assertRaisesRegex(
                PortfolioWireSchemaError, "wire_schema_.*unbounded"
            ):
                validate_wire_bundle(bundle, self.expected_ids)
        bundle = copy.deepcopy(self.bundle)
        bundle["$defs"]["opaque_id"]["notImplementedAssertion"] = True
        with self.assertRaisesRegex(PortfolioWireSchemaError, "wire_schema_unsupported_keyword"):
            validate_wire_bundle(bundle, self.expected_ids)


if __name__ == "__main__":
    unittest.main()
