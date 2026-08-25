from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tools.verify_deployment_contract import (
    CONTRACT_PATH,
    REQUIRED_DOCUMENTATION,
    DeploymentContractError,
    validate_deployment_contract,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class DeploymentContractTests(unittest.TestCase):
    def _copy_contract(self, target: Path) -> Path:
        contract_source = REPOSITORY_ROOT / CONTRACT_PATH
        contract_target = target / CONTRACT_PATH
        contract_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(contract_source, contract_target)
        for relative in REQUIRED_DOCUMENTATION:
            source = REPOSITORY_ROOT / relative
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        return contract_target

    @staticmethod
    def _read(path: Path) -> dict[str, object]:
        value = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(value, dict)
        return value

    @staticmethod
    def _write(path: Path, value: dict[str, object]) -> None:
        path.write_text(json.dumps(value), encoding="utf-8")

    def test_accepted_v1_deployment_contract_passes(self) -> None:
        result = validate_deployment_contract(REPOSITORY_ROOT)
        self.assertEqual(result["schema_id"], "hormuz.v1-deployment-contract")
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["profile_count"], 2)
        self.assertEqual(result["component_owner_count"], 8)
        self.assertEqual(result["state_class_count"], 17)
        self.assertEqual(result["child_gate_count"], 8)
        self.assertEqual(result["rpo_seconds_max"], 300)
        self.assertEqual(result["internal_rto_seconds_max"], 3600)
        self.assertTrue(result["end_to_end_time_publication_required"])
        self.assertFalse(result["customer_sla"])

    def test_unknown_contract_field_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self._copy_contract(root)
            contract = self._read(path)
            contract["unreviewed_claim"] = True
            self._write(path, contract)
            with self.assertRaisesRegex(DeploymentContractError, "contract_fields_invalid"):
                validate_deployment_contract(root)

    def test_recovery_targets_cannot_drift(self) -> None:
        for field, value, error in (
            ("rpo", 301, "rpo_contract_changed"),
            ("internal_rto", 3601, "internal_rto_contract_changed"),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                path = self._copy_contract(root)
                contract = self._read(path)
                objectives = contract["recovery_objectives"]
                assert isinstance(objectives, dict)
                target = objectives[field]
                assert isinstance(target, dict)
                target["maximum_seconds"] = value
                self._write(path, contract)
                with self.assertRaisesRegex(DeploymentContractError, error):
                    validate_deployment_contract(root)

    def test_reference_target_cannot_become_customer_sla(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self._copy_contract(root)
            contract = self._read(path)
            objectives = contract["recovery_objectives"]
            assert isinstance(objectives, dict)
            objectives["customer_sla"] = True
            self._write(path, contract)
            with self.assertRaisesRegex(DeploymentContractError, "recovery_scope_changed"):
                validate_deployment_contract(root)

    def test_complete_end_to_end_time_must_be_published(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self._copy_contract(root)
            contract = self._read(path)
            objectives = contract["recovery_objectives"]
            assert isinstance(objectives, dict)
            end_to_end = objectives["end_to_end_recovery_time"]
            assert isinstance(end_to_end, dict)
            end_to_end["must_publish"] = False
            self._write(path, contract)
            with self.assertRaisesRegex(
                DeploymentContractError, "end_to_end_recovery_contract_changed"
            ):
                validate_deployment_contract(root)

    def test_state_inventory_cannot_silently_drop_a_class(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self._copy_contract(root)
            contract = self._read(path)
            states = contract["state_inventory"]
            assert isinstance(states, list)
            states.pop()
            self._write(path, contract)
            with self.assertRaisesRegex(
                DeploymentContractError, "state_inventory_set_changed"
            ):
                validate_deployment_contract(root)

    def test_compose_cannot_gain_a_recovery_claim(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self._copy_contract(root)
            contract = self._read(path)
            profiles = contract["profiles"]
            assert isinstance(profiles, list)
            compose = profiles[0]
            assert isinstance(compose, dict)
            compose["availability_claim"] = "enterprise_ha"
            self._write(path, contract)
            with self.assertRaisesRegex(DeploymentContractError, "compose_profile_changed"):
                validate_deployment_contract(root)

    def test_decision_document_must_retain_measurement_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            decision = root / REQUIRED_DOCUMENTATION[1]
            decision.write_text(
                decision.read_text(encoding="utf-8").replace(
                    "not a customer SLA", "not a universal objective", 1
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                DeploymentContractError, "accepted_decision_document_drift"
            ):
                validate_deployment_contract(root)

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
            with self.assertRaisesRegex(DeploymentContractError, "duplicate_json_member"):
                validate_deployment_contract(root)

    def test_source_distribution_carries_contract_and_verifier(self) -> None:
        manifest = (REPOSITORY_ROOT / "MANIFEST.in").read_text(encoding="utf-8")
        self.assertIn("include docs/deployment-contract-v1.json\n", manifest)
        self.assertIn("include tools/verify_deployment_contract.py\n", manifest)


if __name__ == "__main__":
    unittest.main()
