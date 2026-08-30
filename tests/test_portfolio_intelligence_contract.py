from __future__ import annotations

import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tools.verify_portfolio_intelligence_contract import (
    BASELINE_MANIFEST_PATH,
    CONTRACT_PATH,
    REQUIRED_DOCUMENTATION,
    PortfolioIntelligenceContractError,
    validate_portfolio_intelligence_contract,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class PortfolioIntelligenceContractTests(unittest.TestCase):
    def _copy_contract(self, target: Path) -> Path:
        required = [CONTRACT_PATH, BASELINE_MANIFEST_PATH]
        required.extend(Path(item) for item in REQUIRED_DOCUMENTATION)
        for relative in required:
            source = REPOSITORY_ROOT / relative
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        return target / CONTRACT_PATH

    @staticmethod
    def _read(path: Path) -> dict[str, object]:
        value = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(value, dict)
        return value

    @staticmethod
    def _write(path: Path, value: dict[str, object]) -> None:
        path.write_text(json.dumps(value), encoding="utf-8")

    def _baseline_manifest(self) -> dict[str, object]:
        return self._read(REPOSITORY_ROOT / BASELINE_MANIFEST_PATH)

    def test_accepted_v1_1_contract_passes(self) -> None:
        result = validate_portfolio_intelligence_contract(REPOSITORY_ROOT)
        self.assertEqual(result["schema_id"], "hormuz.portfolio-intelligence-contract")
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["target_release"], "1.1.0")
        self.assertEqual(result["baseline_release"], "1.0.0")
        self.assertEqual(result["entity_count"], 8)
        self.assertEqual(result["new_route_count"], 21)
        self.assertEqual(result["primary_kpi_count"], 3)
        self.assertEqual(result["child_gate_count"], 14)
        self.assertEqual(result["breaking_change_count"], 0)
        self.assertFalse(result["automatic_policy_application"])
        self.assertFalse(result["employee_ranking"])
        self.assertTrue(result["content_free_release_evidence"])

    def test_unknown_contract_field_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self._copy_contract(root)
            contract = self._read(path)
            contract["unreviewed_claim"] = True
            self._write(path, contract)
            with self.assertRaisesRegex(
                PortfolioIntelligenceContractError, "contract_fields_invalid"
            ):
                validate_portfolio_intelligence_contract(root)

    def test_boolean_contract_schema_version_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self._copy_contract(root)
            contract = self._read(path)
            contract["schema_version"] = True
            self._write(path, contract)
            with self.assertRaisesRegex(
                PortfolioIntelligenceContractError, "contract_identity_changed"
            ):
                validate_portfolio_intelligence_contract(root)

    def test_duplicate_json_member_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self._copy_contract(root)
            raw = path.read_text(encoding="utf-8")
            path.write_text(
                raw.replace(
                    '"schema_version": 1,',
                    '"schema_version": 1,\n  "schema_version": 1,',
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                PortfolioIntelligenceContractError, "duplicate_json_member"
            ):
                validate_portfolio_intelligence_contract(root)

    def test_breaking_change_declaration_fails_minor_release_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self._copy_contract(root)
            contract = self._read(path)
            compatibility = contract["compatibility"]
            assert isinstance(compatibility, dict)
            compatibility["breaking_changes"] = ["require work scope on v1 requests"]
            self._write(path, contract)
            with self.assertRaisesRegex(
                PortfolioIntelligenceContractError,
                "compatibility_contract_changed",
            ):
                validate_portfolio_intelligence_contract(root)

    def test_existing_auth_or_request_change_fails_minor_release_gate(self) -> None:
        for field, value in (
            ("existing_authentication", "new_role_required"),
            ("existing_request_fields", "work_scope_required"),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                path = self._copy_contract(root)
                contract = self._read(path)
                compatibility = contract["compatibility"]
                assert isinstance(compatibility, dict)
                compatibility[field] = value
                self._write(path, contract)
                with self.assertRaisesRegex(
                    PortfolioIntelligenceContractError,
                    "compatibility_contract_changed",
                ):
                    validate_portfolio_intelligence_contract(root)

    def test_existing_collection_or_retry_behavior_cannot_change(self) -> None:
        for field in (
            "existing_ordering",
            "existing_pagination",
            "existing_idempotency",
            "existing_retry_behavior",
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                path = self._copy_contract(root)
                contract = self._read(path)
                compatibility = contract["compatibility"]
                assert isinstance(compatibility, dict)
                compatibility[field] = "changed"
                self._write(path, contract)
                with self.assertRaisesRegex(
                    PortfolioIntelligenceContractError,
                    "compatibility_contract_changed",
                ):
                    validate_portfolio_intelligence_contract(root)

    def test_existing_v1_schema_cannot_change_or_disappear(self) -> None:
        baseline = self._baseline_manifest()
        schemas = baseline["schemas"]
        assert isinstance(schemas, list)
        for current, error in (
            ({**baseline, "schemas": schemas[1:]}, "v1_schema_changed"),
            (
                {
                    **baseline,
                    "schemas": [
                        {
                            **schemas[0],
                            "fields": [*schemas[0]["fields"], "new_required_field"],
                        },
                        *schemas[1:],
                    ],
                },
                "v1_schema_changed",
            ),
        ):
            with self.subTest(error=error), self.assertRaisesRegex(
                PortfolioIntelligenceContractError, error
            ):
                validate_portfolio_intelligence_contract(
                    REPOSITORY_ROOT, current_manifest=current
                )

    def test_additive_schema_and_error_code_are_compatible(self) -> None:
        current = copy.deepcopy(self._baseline_manifest())
        schemas = current["schemas"]
        errors = current["error_codes"]
        assert isinstance(schemas, list)
        assert isinstance(errors, list)
        schemas.append(
            {
                "schema_id": "hormuz.work-scope-version",
                "schema_version": 1,
                "delivery": "durable-evidence",
                "ownership": "hormuz",
                "legacy": False,
                "fields": ["organization_id", "work_scope_id", "version"],
            }
        )
        errors.append("hormuz_work_scope_denied")
        result = validate_portfolio_intelligence_contract(
            REPOSITORY_ROOT, current_manifest=current
        )
        self.assertEqual(result["status"], "passed")

    def test_existing_v1_error_code_cannot_disappear(self) -> None:
        current = self._baseline_manifest()
        errors = current["error_codes"]
        assert isinstance(errors, list)
        current["error_codes"] = errors[1:]
        with self.assertRaisesRegex(
            PortfolioIntelligenceContractError, "v1_error_code_removed"
        ):
            validate_portfolio_intelligence_contract(
                REPOSITORY_ROOT, current_manifest=current
            )

    def test_primary_attribution_cannot_become_multi_use_case(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self._copy_contract(root)
            contract = self._read(path)
            attribution = contract["attribution"]
            assert isinstance(attribution, dict)
            attribution["active_primary_use_cases_per_attempt"] = 2
            self._write(path, contract)
            with self.assertRaisesRegex(
                PortfolioIntelligenceContractError,
                "attribution_contract_changed",
            ):
                validate_portfolio_intelligence_contract(root)

    def test_identity_and_temporal_rules_cannot_drift(self) -> None:
        for section, field, error in (
            (
                "identity_and_lifecycle",
                "stable_id_reassignment",
                "identity_and_lifecycle_changed",
            ),
            (
                "temporal_and_lifecycle",
                "idempotency_conflict",
                "temporal_and_lifecycle_changed",
            ),
        ):
            with self.subTest(section=section), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                path = self._copy_contract(root)
                contract = self._read(path)
                value = contract[section]
                assert isinstance(value, dict)
                value[field] = "allowed"
                self._write(path, contract)
                with self.assertRaisesRegex(
                    PortfolioIntelligenceContractError, error
                ):
                    validate_portfolio_intelligence_contract(root)

    def test_evidence_thresholds_cannot_be_silently_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self._copy_contract(root)
            contract = self._read(path)
            evidence = contract["evidence"]
            assert isinstance(evidence, dict)
            evidence["minimum_sample_rule"] = "none"
            self._write(path, contract)
            with self.assertRaisesRegex(
                PortfolioIntelligenceContractError,
                "evidence_contract_changed",
            ):
                validate_portfolio_intelligence_contract(root)

    def test_self_scope_cannot_expand_to_peer_or_outcome_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self._copy_contract(root)
            contract = self._read(path)
            authorization = contract["authorization"]
            assert isinstance(authorization, dict)
            authorization["self_scope"] = "all_team_members"
            self._write(path, contract)
            with self.assertRaisesRegex(
                PortfolioIntelligenceContractError,
                "authorization_boundary_changed",
            ):
                validate_portfolio_intelligence_contract(root)

    def test_route_to_schema_binding_cannot_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self._copy_contract(root)
            contract = self._read(path)
            api = contract["api"]
            assert isinstance(api, dict)
            routes = api["route_contracts"]
            assert isinstance(routes, dict)
            route = routes["GET /v1/admin/portfolio/scorecards"]
            assert isinstance(route, dict)
            route["response"] = "hormuz.unversioned-database-row"
            self._write(path, contract)
            with self.assertRaisesRegex(
                PortfolioIntelligenceContractError,
                "api_route_inventory_changed",
            ):
                validate_portfolio_intelligence_contract(root)

    def test_boolean_entity_schema_version_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self._copy_contract(root)
            contract = self._read(path)
            entities = contract["entities"]
            assert isinstance(entities, list)
            entity = entities[0]
            assert isinstance(entity, dict)
            entity["schema_version"] = True
            self._write(path, contract)
            with self.assertRaisesRegex(
                PortfolioIntelligenceContractError,
                "entity_schema_version_changed",
            ):
                validate_portfolio_intelligence_contract(root)

    def test_automatic_policy_application_cannot_be_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self._copy_contract(root)
            contract = self._read(path)
            recommendations = contract["recommendations"]
            assert isinstance(recommendations, dict)
            recommendations["automatic_application"] = True
            self._write(path, contract)
            with self.assertRaisesRegex(
                PortfolioIntelligenceContractError,
                "recommendation_boundary_changed",
            ):
                validate_portfolio_intelligence_contract(root)

    def test_content_exclusion_cannot_be_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self._copy_contract(root)
            contract = self._read(path)
            content = contract["content_boundary"]
            assert isinstance(content, dict)
            excluded = content["excluded"]
            assert isinstance(excluded, list)
            excluded.remove("prompts_or_responses")
            self._write(path, contract)
            with self.assertRaisesRegex(
                PortfolioIntelligenceContractError,
                "content_exclusion_set_changed",
            ):
                validate_portfolio_intelligence_contract(root)

    def test_baseline_manifest_digest_is_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            baseline = root / BASELINE_MANIFEST_PATH
            baseline.write_text(
                baseline.read_text(encoding="utf-8") + " ", encoding="utf-8"
            )
            with self.assertRaisesRegex(
                PortfolioIntelligenceContractError,
                "baseline_manifest_digest_mismatch",
            ):
                validate_portfolio_intelligence_contract(root)

    def test_decision_document_must_retain_no_auto_apply_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            decision = root / REQUIRED_DOCUMENTATION[-1]
            decision.write_text(
                decision.read_text(encoding="utf-8").replace(
                    "never automatically changes", "may automatically change", 1
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                PortfolioIntelligenceContractError,
                "accepted_decision_document_drift",
            ):
                validate_portfolio_intelligence_contract(root)

    def test_source_distribution_carries_contract_verifier_and_fixture(self) -> None:
        manifest = (REPOSITORY_ROOT / "MANIFEST.in").read_text(encoding="utf-8")
        self.assertIn("include docs/portfolio-intelligence-contract-v1.json\n", manifest)
        self.assertIn(
            "include tools/verify_portfolio_intelligence_contract.py\n", manifest
        )
        self.assertIn("recursive-include tests *.py *.json\n", manifest)


if __name__ == "__main__":
    unittest.main()
