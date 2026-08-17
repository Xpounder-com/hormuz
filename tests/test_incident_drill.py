import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest

from scripts.incident_drill import (
    IncidentDrillContractError,
    _write_evidence,
    run_incident_drills,
    validate_incident_drills,
)


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "operations" / "incident-drills.json"


class IncidentDrillContractTests(unittest.TestCase):
    def _catalog(self) -> dict[str, object]:
        return json.loads(CATALOG.read_text(encoding="utf-8"))

    def _validate_mutation(self, mutation) -> dict[str, object]:
        value = copy.deepcopy(self._catalog())
        mutation(value)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "incident-drills.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            return validate_incident_drills(path, project_root=ROOT)

    def test_repository_catalog_binds_all_scenarios_and_stays_honest(self) -> None:
        evidence = validate_incident_drills(CATALOG, project_root=ROOT)

        self.assertEqual(evidence["schema"], "hormuz.incident-drill-evidence.v1")
        self.assertEqual(evidence["catalog_schema"], "hormuz.incident-drills.v1")
        self.assertEqual(evidence["scenario_count"], 7)
        self.assertEqual(evidence["test_count"], 7)
        self.assertEqual(evidence["executed_count"], 0)
        self.assertEqual(evidence["passed_count"], 0)
        self.assertFalse(evidence["production_exercise_complete"])
        self.assertFalse(evidence["owner_assignments_complete"])
        self.assertFalse(evidence["external_communications_exercised"])
        self.assertFalse(evidence["enterprise_release_ready"])

    def test_duplicate_json_members_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "incident-drills.json"
            path.write_text(
                '{"schema":"hormuz.incident-drills.v1",'
                '"schema":"hormuz.incident-drills.v1"}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(IncidentDrillContractError, "duplicate"):
                validate_incident_drills(path, project_root=ROOT)

    def test_unknown_fields_and_nonstandard_numbers_fail_closed(self) -> None:
        def add_unknown(value: dict[str, object]) -> None:
            value["unreviewed"] = True

        with self.assertRaisesRegex(IncidentDrillContractError, "top-level fields"):
            self._validate_mutation(add_unknown)

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "incident-drills.json"
            path.write_text(
                '{"schema":"hormuz.incident-drills.v1","version":NaN}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(IncidentDrillContractError, "non-standard"):
                validate_incident_drills(path, project_root=ROOT)

    def test_scenario_coverage_and_test_bindings_cannot_be_weakened(self) -> None:
        def remove_scenario(value: dict[str, object]) -> None:
            value["scenarios"].pop()

        with self.assertRaisesRegex(IncidentDrillContractError, "required scenarios"):
            self._validate_mutation(remove_scenario)

        def broaden_test_id(value: dict[str, object]) -> None:
            value["scenarios"][0]["test_ids"] = ["tests.test_gateway"]

        with self.assertRaisesRegex(IncidentDrillContractError, "test identifier"):
            self._validate_mutation(broaden_test_id)

        def missing_test(value: dict[str, object]) -> None:
            value["scenarios"][0]["test_ids"] = [
                "tests.test_gateway.GatewayIntegrationTests.test_missing_drill"
            ]

        with self.assertRaisesRegex(IncidentDrillContractError, "test binding"):
            self._validate_mutation(missing_test)

    def test_runbook_references_must_resolve_inside_the_repository(self) -> None:
        def escape_reference(value: dict[str, object]) -> None:
            value["scenarios"][0]["runbook"] = "../outside.md#provider-outage"

        with self.assertRaisesRegex(IncidentDrillContractError, "runbook reference"):
            self._validate_mutation(escape_reference)

        def missing_anchor(value: dict[str, object]) -> None:
            value["scenarios"][0]["runbook"] = (
                "docs/INCIDENT_RESPONSE.md#missing-incident-heading"
            )

        with self.assertRaisesRegex(IncidentDrillContractError, "runbook anchor"):
            self._validate_mutation(missing_anchor)

    def test_runner_executes_exact_regressions_and_emits_content_free_evidence(self) -> None:
        evidence = run_incident_drills(
            CATALOG,
            project_root=ROOT,
            stream=io.StringIO(),
        )

        self.assertEqual(evidence["executed_count"], 7)
        self.assertEqual(evidence["passed_count"], 7)
        self.assertEqual(evidence["failed_count"], 0)
        serialized = json.dumps(evidence, sort_keys=True)
        catalog = self._catalog()
        for scenario in catalog["scenarios"]:
            self.assertNotIn(scenario["name"], serialized)
            self.assertNotIn(scenario["objective"], serialized)
            self.assertNotIn(scenario["production_gap"], serialized)
            for test_id in scenario["test_ids"]:
                self.assertNotIn(test_id, serialized)

    def test_evidence_output_is_private_and_never_overwrites(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "evidence.json"
            _write_evidence(path, {"schema": "test"})
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            original = path.read_bytes()
            with self.assertRaisesRegex(IncidentDrillContractError, "cannot write"):
                _write_evidence(path, {"schema": "replacement"})
            self.assertEqual(path.read_bytes(), original)

    def test_ci_release_and_distribution_preserve_drill_contract(self) -> None:
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        release = (ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )
        for workflow in (ci, release):
            self.assertIn("python scripts/incident_drill.py", workflow)
            self.assertIn("hormuz-incident-drill-evidence.json", workflow)
        self.assertIn("name: hormuz-incident-drills", ci)

        manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
        self.assertIn("include scripts/incident_drill.py", manifest)
        self.assertIn("include operations/incident-drills.json", manifest)


if __name__ == "__main__":
    unittest.main()
