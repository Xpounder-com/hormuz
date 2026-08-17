import copy
import json
from pathlib import Path
import tempfile
import unittest

from scripts.threat_model_contract import (
    ThreatModelContractError,
    _write_evidence,
    validate_threat_model,
)


ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "security" / "threat-model.json"


class ThreatModelContractTests(unittest.TestCase):
    def _model(self) -> dict[str, object]:
        return json.loads(MODEL.read_text(encoding="utf-8"))

    def _validate_mutation(self, mutation) -> dict[str, object]:
        value = copy.deepcopy(self._model())
        mutation(value)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "threat-model.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            return validate_threat_model(path, project_root=ROOT)

    def test_repository_threat_model_covers_release_incidents_and_stays_honest(self) -> None:
        evidence = validate_threat_model(MODEL, project_root=ROOT)

        self.assertEqual(evidence["schema"], "hormuz.threat-model-evidence.v1")
        self.assertEqual(evidence["model_schema"], "hormuz.threat-model.v1")
        self.assertEqual(evidence["stride_categories_covered"], 6)
        self.assertEqual(evidence["incident_scenarios_covered"], 7)
        self.assertGreaterEqual(evidence["threat_count"], 16)
        self.assertGreater(evidence["partially_mitigated_count"], 0)
        self.assertGreater(evidence["open_count"], 0)
        self.assertEqual(evidence["independent_review_status"], "pending")
        self.assertFalse(evidence["enterprise_release_ready"])

    def test_duplicate_json_members_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "threat-model.json"
            path.write_text(
                '{"schema":"hormuz.threat-model.v1",'
                '"schema":"hormuz.threat-model.v1"}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ThreatModelContractError, "duplicate"):
                validate_threat_model(path, project_root=ROOT)

    def test_unknown_fields_and_invalid_status_fail_closed(self) -> None:
        def add_unknown(value: dict[str, object]) -> None:
            value["unreviewed"] = True

        with self.assertRaisesRegex(ThreatModelContractError, "top-level fields"):
            self._validate_mutation(add_unknown)

        def change_status(value: dict[str, object]) -> None:
            value["threats"][0]["status"] = "accepted"

        with self.assertRaisesRegex(ThreatModelContractError, "threat status"):
            self._validate_mutation(change_status)

    def test_stride_and_incident_coverage_cannot_be_silently_removed(self) -> None:
        def remove_spoofing(value: dict[str, object]) -> None:
            for threat in value["threats"]:
                if threat["category"] == "spoofing":
                    threat["category"] = "tampering"

        with self.assertRaisesRegex(ThreatModelContractError, "STRIDE"):
            self._validate_mutation(remove_spoofing)

        def remove_incident(value: dict[str, object]) -> None:
            value["incident_scenarios"].pop()

        with self.assertRaisesRegex(ThreatModelContractError, "incident scenarios"):
            self._validate_mutation(remove_incident)

    def test_evidence_references_must_resolve_inside_the_repository(self) -> None:
        def missing_reference(value: dict[str, object]) -> None:
            value["threats"][0]["controls"][0]["ref"] = "tests/does-not-exist.py"

        with self.assertRaisesRegex(ThreatModelContractError, "evidence reference"):
            self._validate_mutation(missing_reference)

        def escaped_reference(value: dict[str, object]) -> None:
            value["threats"][0]["controls"][0]["ref"] = "../outside.md"

        with self.assertRaisesRegex(ThreatModelContractError, "evidence reference"):
            self._validate_mutation(escaped_reference)

    def test_independent_review_cannot_be_claimed_without_evidence(self) -> None:
        def claim_review(value: dict[str, object]) -> None:
            value["independent_review"]["status"] = "completed"

        with self.assertRaisesRegex(ThreatModelContractError, "independent review evidence"):
            self._validate_mutation(claim_review)

    def test_ci_and_release_preserve_content_free_threat_model_evidence(self) -> None:
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        for workflow in (ci, release):
            self.assertIn("python scripts/threat_model_contract.py", workflow)
            self.assertIn("hormuz-threat-model-evidence.json", workflow)
        self.assertIn("name: hormuz-threat-model", ci)

    def test_release_python_setup_has_one_version_member_per_job(self) -> None:
        release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertEqual(release.count("python-version: ${{ env.PYTHON_VERSION }}"), 3)

    def test_evidence_output_is_private_and_never_overwrites(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "evidence.json"
            _write_evidence(path, {"schema": "test"})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            original = path.read_bytes()
            with self.assertRaisesRegex(ThreatModelContractError, "cannot write"):
                _write_evidence(path, {"schema": "replacement"})
            self.assertEqual(path.read_bytes(), original)

    def test_source_distribution_contains_the_model_and_validator(self) -> None:
        manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
        self.assertIn("include scripts/threat_model_contract.py", manifest)
        self.assertIn("include security/threat-model.json", manifest)


if __name__ == "__main__":
    unittest.main()
