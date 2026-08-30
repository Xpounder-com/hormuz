from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import verify_portfolio_intelligence_contract as contract_verifier
from tools.verify_portfolio_intelligence_contract import (
    BASELINE_MANIFEST_PATH,
    CONTRACT_PATH,
    REQUIRED_DOCUMENTATION,
    WIRE_SCHEMA_PATH,
    WIRE_SCHEMA_SHA256,
    PortfolioIntelligenceContractError,
    validate_portfolio_intelligence_contract,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class PortfolioIntelligenceContractTests(unittest.TestCase):
    def _copy_contract(self, target: Path) -> Path:
        required = [CONTRACT_PATH, BASELINE_MANIFEST_PATH, WIRE_SCHEMA_PATH]
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

    def _assert_contract_change_rejected(self, change, error: str) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self._copy_contract(root)
            contract = self._read(path)
            change(contract)
            self._write(path, contract)
            with self.assertRaisesRegex(PortfolioIntelligenceContractError, error):
                validate_portfolio_intelligence_contract(root)

    def test_current_manifest_compatibility_cannot_drift(self) -> None:
        for field in self._baseline_manifest()["compatibility"]:
            with self.subTest(field=field):
                current = self._baseline_manifest()
                current["compatibility"][field] = "unreviewed_change"
                with self.assertRaisesRegex(
                    PortfolioIntelligenceContractError, "v1_compatibility_changed"
                ):
                    validate_portfolio_intelligence_contract(
                        REPOSITORY_ROOT, current_manifest=current
                    )

    def test_malformed_added_manifest_schema_is_rejected(self) -> None:
        schema = {
            "schema_id": "hormuz.additive-example",
            "schema_version": 1,
            "delivery": "response",
            "ownership": "hormuz",
            "legacy": False,
            "fields": ["schema_id", "schema_version", "value"],
        }
        for missing in schema:
            with self.subTest(missing=missing):
                current = self._baseline_manifest()
                current["schemas"].append(
                    {key: value for key, value in schema.items() if key != missing}
                )
                with self.assertRaisesRegex(
                    PortfolioIntelligenceContractError,
                    "current_manifest_contract_invalid",
                ):
                    validate_portfolio_intelligence_contract(
                        REPOSITORY_ROOT, current_manifest=current
                    )

    def test_all_role_specific_scopes_are_frozen(self) -> None:
        for field in (
            "team_lead_scope", "finance_scope", "platform_scope", "portfolio_admin_scope"
        ):
            with self.subTest(field=field):
                self._assert_contract_change_rejected(
                    lambda value: value["authorization"].__setitem__(field, "all_data"),
                    "authorization_boundary_changed",
                )

    def test_every_kpi_list_is_frozen(self) -> None:
        for field in ("drivers", "guardrails", "required_dimensions"):
            for mutation in ("remove", "add", "replace"):
                with self.subTest(field=field, mutation=mutation):
                    def change(contract):
                        values = contract["kpis"][field]
                        if mutation == "remove":
                            values.pop()
                        elif mutation == "add":
                            values.append("unreviewed_dimension")
                        else:
                            values[0] = "unreviewed_dimension"
                    self._assert_contract_change_rejected(change, "kpis_.*_changed")

    def test_content_allowlist_is_closed_and_disjoint(self) -> None:
        for addition in ("raw_connector_payloads", "employee_names", None):
            with self.subTest(addition=addition):
                def change(contract):
                    values = contract["content_boundary"]["allowed"]
                    if addition is None:
                        values.pop()
                    else:
                        values.append(addition)
                self._assert_contract_change_rejected(
                    change, "content_allowlist_changed|content_allowlist_overlap"
                )

    def test_entity_mutability_and_content_rules_cannot_be_exchanged(self) -> None:
        for field, error in (
            ("mutability", "entity_mutability_invalid"),
            ("content_boundary", "entity_content_boundary_invalid"),
        ):
            with self.subTest(field=field):
                def change(contract):
                    first, second = contract["entities"][0], contract["entities"][6]
                    first[field], second[field] = second[field], first[field]
                self._assert_contract_change_rejected(change, error)

    def test_recommendation_type_allowlist_is_frozen(self) -> None:
        for values in (["employee_ranking"], [], ["budget_plan_change"]):
            with self.subTest(values=values):
                self._assert_contract_change_rejected(
                    lambda contract: contract["recommendations"].__setitem__(
                        "allowed_types", values
                    ),
                    "recommendation_types_changed",
                )

    def test_standalone_verifier_needs_no_install_or_pythonpath(self) -> None:
        script = REPOSITORY_ROOT / "tools/verify_portfolio_intelligence_contract.py"
        # Keep installed runtime dependencies, but prohibit an installed Hormuz
        # from rescuing a missing source-root bootstrap. -I also ignores all
        # caller PYTHONPATH and current-directory import conveniences.
        runner = """
import importlib.abc
import importlib.machinery
from pathlib import Path
import runpy
import sys

script = Path(sys.argv[1])
class SourceOnly(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "hormuz":
            spec = importlib.machinery.PathFinder.find_spec(fullname, path)
            expected = script.parent.parent / "hormuz" / "__init__.py"
            if spec is None or Path(spec.origin).resolve() != expected.resolve():
                raise ModuleNotFoundError("standalone_verifier_did_not_import_source")
            return spec
sys.meta_path.insert(0, SourceOnly())
sys.argv = [str(script)]
runpy.run_path(str(script), run_name="__main__")
"""
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run(
                [
                    sys.executable, "-I", "-c", runner, str(script),
                ],
                cwd=temporary,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "passed")

    def test_every_wire_schema_definition_is_digest_frozen(self) -> None:
        original = self._read(REPOSITORY_ROOT / WIRE_SCHEMA_PATH)
        for schema_id in original["x-hormuz-schema-ids"]:
            with self.subTest(schema_id=schema_id), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self._copy_contract(root)
                bundle = copy.deepcopy(original)
                del bundle["$defs"][schema_id]
                self._write(root / WIRE_SCHEMA_PATH, bundle)
                with self.assertRaisesRegex(
                    PortfolioIntelligenceContractError, "wire_schema_digest_mismatch"
                ):
                    validate_portfolio_intelligence_contract(root)

    def test_wire_field_type_requiredness_bounds_and_semantics_cannot_drift(self) -> None:
        for change in ("type", "required", "maxLength", "description", "additionalProperties"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self._copy_contract(root)
                bundle = self._read(root / WIRE_SCHEMA_PATH)
                scope = bundle["$defs"]["hormuz.work-scope-create-request"]
                if change == "type":
                    bundle["$defs"]["display_name"]["type"] = "number"
                elif change == "required":
                    scope["required"].remove("display_name")
                elif change == "maxLength":
                    bundle["$defs"]["display_name"]["maxLength"] = 10000
                elif change == "description":
                    scope["description"] = "Body tenant scope grants access"
                else:
                    scope["additionalProperties"] = True
                self._write(root / WIRE_SCHEMA_PATH, bundle)
                with self.assertRaisesRegex(
                    PortfolioIntelligenceContractError, "wire_schema_digest_mismatch"
                ):
                    validate_portfolio_intelligence_contract(root)

    def test_replacing_the_wire_digest_reference_does_not_authorize_changes(self) -> None:
        self._assert_contract_change_rejected(
            lambda contract: contract["api"]["wire_schema_bundle"].__setitem__(
                "sha256", "0" * 64
            ),
            "wire_schema_reference_changed",
        )

    def test_digest_verification_parses_the_same_bytes_without_rereading(self) -> None:
        with mock.patch.object(
            contract_verifier, "_read_bytes", wraps=contract_verifier._read_bytes
        ) as reads:
            validate_portfolio_intelligence_contract(REPOSITORY_ROOT)
        labels = [call.kwargs["label"] for call in reads.call_args_list]
        self.assertEqual(labels.count("baseline_manifest"), 1)
        self.assertEqual(labels.count("wire_schema"), 1)

    def test_unregistered_current_error_code_is_not_an_additive_contract(self) -> None:
        current = self._baseline_manifest()
        current["error_codes"].append("hormuz_unregistered")
        current["error_codes"].sort()
        with self.assertRaisesRegex(
            PortfolioIntelligenceContractError, "current_manifest_contract_invalid"
        ):
            validate_portfolio_intelligence_contract(
                REPOSITORY_ROOT, current_manifest=current
            )

    def test_accepted_v1_1_contract_passes(self) -> None:
        result = validate_portfolio_intelligence_contract(REPOSITORY_ROOT)
        self.assertEqual(result["schema_id"], "hormuz.portfolio-intelligence-contract")
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["target_release"], "1.1.0")
        self.assertEqual(result["baseline_release"], "1.0.0")
        self.assertEqual(result["entity_count"], 8)
        self.assertEqual(result["new_route_count"], 21)
        self.assertEqual(result["wire_schema_count"], 26)
        self.assertEqual(result["wire_schema_bundle_sha256"], WIRE_SCHEMA_SHA256)
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

    def test_contract_integer_fields_reject_boolean_and_float_aliases(self) -> None:
        paths = [
            (("schema_version",), 1, "contract_identity_changed"),
            (("api", "public_schema_version"), 1, "api_behavior_changed"),
            (("api", "error_schema_version"), 1, "api_behavior_changed"),
            (("api", "default_page_size"), 50, "api_behavior_changed"),
            (("api", "maximum_page_size"), 100, "api_behavior_changed"),
            (("attribution", "active_primary_use_cases_per_attempt"), 1,
             "attribution_contract_changed"),
        ]
        paths.extend(
            (("entities", index, "schema_version"), 1, "entity_schema_version_changed")
            for index in range(8)
        )
        for path, expected, error in paths:
            for replacement in (True, float(expected)):
                with self.subTest(path=path, replacement=replacement):
                    def change(contract):
                        target = contract
                        for component in path[:-1]:
                            target = target[component]
                        target[path[-1]] = replacement
                    self._assert_contract_change_rejected(change, error)

    def test_contract_boolean_fields_reject_numeric_aliases(self) -> None:
        for path, alias, error in (
            (("baseline", "current_main_matches_release_manifest"), 1,
             "baseline_changed"),
            (("evidence", "temporal_proximity_is_causality"), 0,
             "evidence_contract_changed"),
            (("authorization", "role_grants_provider_access"), 0,
             "authorization_boundary_changed"),
            (("recommendations", "automatic_application"), 0,
             "recommendation_boundary_changed"),
        ):
            for replacement in (alias, float(alias)):
                with self.subTest(path=path, replacement=replacement):
                    self._assert_contract_change_rejected(
                        lambda contract: contract[path[0]].__setitem__(path[1], replacement),
                        error,
                    )

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
        errors.sort()
        # Future additions must also be registered with the current manifest
        # validator. Merely inventing a code in the returned JSON is invalid.
        with mock.patch(
            "hormuz._contract_schemas.manifest.PUBLIC_ERROR_CODES", set(errors)
        ):
            result = validate_portfolio_intelligence_contract(
                REPOSITORY_ROOT, current_manifest=current
            )
        self.assertEqual(result["status"], "passed")

    def test_existing_v1_error_code_cannot_disappear(self) -> None:
        current = self._baseline_manifest()
        errors = current["error_codes"]
        assert isinstance(errors, list)
        current["error_codes"] = errors[1:]
        with mock.patch(
            "hormuz._contract_schemas.manifest.PUBLIC_ERROR_CODES", set(errors[1:])
        ), self.assertRaisesRegex(
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
        self.assertIn("include docs/portfolio-intelligence-wire-v1.json\n", manifest)
        self.assertIn("include tools/_portfolio_wire_contract.py\n", manifest)
        self.assertIn(
            "include tools/verify_portfolio_intelligence_contract.py\n", manifest
        )
        self.assertIn("recursive-include tests *.py *.json\n", manifest)


if __name__ == "__main__":
    unittest.main()
