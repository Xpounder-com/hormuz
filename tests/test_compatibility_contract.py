import copy
import json
from pathlib import Path
import tempfile
import unittest

from scripts.compatibility_contract import (
    CompatibilityContractError,
    _write_evidence,
    validate_compatibility_matrix,
)


ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "compatibility" / "compatibility-matrix.json"


class CompatibilityContractTests(unittest.TestCase):
    def _matrix(self) -> dict[str, object]:
        return json.loads(MATRIX.read_text(encoding="utf-8"))

    def _validate_mutation(self, mutation) -> dict[str, object]:
        value = copy.deepcopy(self._matrix())
        mutation(value)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "compatibility-matrix.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            return validate_compatibility_matrix(path, project_root=ROOT)

    def test_repository_matrix_matches_blocking_evidence_and_stays_honest(self) -> None:
        evidence = validate_compatibility_matrix(MATRIX, project_root=ROOT)

        self.assertEqual(evidence["schema"], "hormuz.compatibility-evidence.v1")
        self.assertEqual(evidence["matrix_schema"], "hormuz.compatibility-matrix.v1")
        self.assertEqual(evidence["product_stage"], "alpha")
        self.assertFalse(evidence["enterprise_release_ready"])
        self.assertEqual(evidence["python_versions"], ["3.11", "3.12", "3.13", "3.14"])
        self.assertEqual(
            evidence["clients"],
            {"claude-code": "2.1.233", "codex": "0.147.0"},
        )
        self.assertEqual(evidence["real_idp_profiles_verified"], 0)
        self.assertEqual(evidence["production_persistence_profiles_verified"], 0)
        self.assertEqual(evidence["production_deployment_profiles_verified"], 0)
        self.assertGreater(evidence["unsupported_or_pending_count"], 0)

    def test_entra_reference_is_named_but_not_certified(self) -> None:
        identity = {
            entry["id"]: entry
            for entry in self._matrix()["categories"]["identity"]
        }

        reference = identity["identity.entra-reference"]
        self.assertEqual(reference["support_level"], "unsupported")
        self.assertFalse(reference["production_supported"])
        self.assertEqual(reference["tested_environments"], [])
        self.assertIn(
            "docs/ENTRA_REFERENCE.md#Certification evidence required",
            reference["evidence"],
        )

    def test_duplicate_members_and_unknown_fields_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "compatibility-matrix.json"
            path.write_text(
                '{"schema":"hormuz.compatibility-matrix.v1",'
                '"schema":"hormuz.compatibility-matrix.v1"}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(CompatibilityContractError, "duplicate"):
                validate_compatibility_matrix(path, project_root=ROOT)

        def add_unknown(value: dict[str, object]) -> None:
            value["unreviewed"] = True

        with self.assertRaisesRegex(CompatibilityContractError, "top-level fields"):
            self._validate_mutation(add_unknown)

    def test_non_standard_json_numbers_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "compatibility-matrix.json"
            path.write_text(
                '{"schema":"hormuz.compatibility-matrix.v1","value":NaN}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(CompatibilityContractError, "non-standard number"):
                validate_compatibility_matrix(path, project_root=ROOT)

    def test_support_levels_and_alpha_production_claims_fail_closed(self) -> None:
        def invent_level(value: dict[str, object]) -> None:
            value["categories"]["employee_clients"][0]["support_level"] = "certified"

        with self.assertRaisesRegex(CompatibilityContractError, "support level"):
            self._validate_mutation(invent_level)

        def claim_production(value: dict[str, object]) -> None:
            value["categories"]["deployment"][0]["production_supported"] = True

        with self.assertRaisesRegex(CompatibilityContractError, "production support"):
            self._validate_mutation(claim_production)

    def test_release_tested_entries_require_scope_evidence_and_limitations(self) -> None:
        def remove_scope(value: dict[str, object]) -> None:
            value["categories"]["employee_clients"][0]["tested_environments"] = []

        with self.assertRaisesRegex(CompatibilityContractError, "tested environments"):
            self._validate_mutation(remove_scope)

        def remove_evidence(value: dict[str, object]) -> None:
            value["categories"]["employee_clients"][0]["evidence"] = []

        with self.assertRaisesRegex(CompatibilityContractError, "evidence"):
            self._validate_mutation(remove_evidence)

        def remove_limitations(value: dict[str, object]) -> None:
            value["categories"]["employee_clients"][0]["limitations"] = []

        with self.assertRaisesRegex(CompatibilityContractError, "limitations"):
            self._validate_mutation(remove_limitations)

    def test_evidence_references_are_repository_local_and_selectors_resolve(self) -> None:
        def escape(value: dict[str, object]) -> None:
            value["categories"]["employee_clients"][0]["evidence"][0] = "../outside.json"

        with self.assertRaisesRegex(CompatibilityContractError, "evidence reference"):
            self._validate_mutation(escape)

        def missing_selector(value: dict[str, object]) -> None:
            value["categories"]["employee_clients"][0]["evidence"][0] = (
                "tests/test_gateway.py#test_does_not_exist"
            )

        with self.assertRaisesRegex(CompatibilityContractError, "evidence selector"):
            self._validate_mutation(missing_selector)

    def test_client_versions_are_bound_to_the_integrity_locked_fixture(self) -> None:
        def change_codex(value: dict[str, object]) -> None:
            for entry in value["categories"]["employee_clients"]:
                if entry["id"] == "client.codex":
                    entry["version"] = "latest"

        with self.assertRaisesRegex(CompatibilityContractError, "client versions"):
            self._validate_mutation(change_codex)

    def test_python_versions_are_bound_to_project_and_ci_contracts(self) -> None:
        def remove_python(value: dict[str, object]) -> None:
            value["categories"]["python_runtimes"].pop()

        with self.assertRaisesRegex(CompatibilityContractError, "Python versions"):
            self._validate_mutation(remove_python)

    def test_provider_and_container_claims_are_bound_to_source(self) -> None:
        def invent_provider_route(value: dict[str, object]) -> None:
            value["categories"]["provider_protocols"][0]["interfaces"] = [
                "POST /v9/imaginary"
            ]

        with self.assertRaisesRegex(CompatibilityContractError, "provider interfaces"):
            self._validate_mutation(invent_provider_route)

        def change_container(value: dict[str, object]) -> None:
            for entry in value["categories"]["deployment"]:
                if entry["id"] == "deployment.oci":
                    entry["version"] = "unbounded"

        with self.assertRaisesRegex(CompatibilityContractError, "container base"):
            self._validate_mutation(change_container)

    def test_ci_release_package_and_docs_enforce_the_matrix(self) -> None:
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        release = (ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )
        manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for workflow in (ci, release):
            self.assertIn("python scripts/compatibility_contract.py", workflow)
            self.assertIn("hormuz-compatibility-evidence.json", workflow)
        self.assertIn("name: hormuz-compatibility", ci)
        self.assertIn("include scripts/compatibility_contract.py", manifest)
        self.assertIn("include compatibility/compatibility-matrix.json", manifest)
        self.assertIn("docs/COMPATIBILITY.md", readme)

    def test_evidence_output_is_private_and_never_overwrites(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "evidence.json"
            _write_evidence(path, {"schema": "test"})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            original = path.read_bytes()
            with self.assertRaisesRegex(CompatibilityContractError, "cannot write"):
                _write_evidence(path, {"schema": "replacement"})
            self.assertEqual(path.read_bytes(), original)

    def test_evidence_schema_is_content_free_and_exact(self) -> None:
        evidence = validate_compatibility_matrix(MATRIX, project_root=ROOT)

        self.assertEqual(
            set(evidence),
            {
                "schema",
                "matrix_schema",
                "matrix_version",
                "matrix_sha256",
                "product_stage",
                "enterprise_release_ready",
                "category_counts",
                "support_level_counts",
                "entry_count",
                "unsupported_or_pending_count",
                "clients",
                "python_versions",
                "real_idp_profiles_verified",
                "production_persistence_profiles_verified",
                "production_deployment_profiles_verified",
            },
        )
        serialized = json.dumps(evidence, sort_keys=True)
        for forbidden in (
            "limitations",
            "tested_environments",
            "interfaces",
            "evidence reference",
            "owner-selected enterprise IdP",
        ):
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()
