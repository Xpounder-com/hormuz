from __future__ import annotations

import json
import unittest
from pathlib import Path

from hormuz.contracts import (
    AUDIT_EVENT_SCHEMA_ID,
    AUDIT_EVENT_SCHEMA_VERSION,
    ContractValidationError,
    contract_envelope,
    contract_manifest,
    relay_contract_header,
    validate_audit_event,
    validate_contract,
    validate_contract_manifest,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "contracts"


class PolicyEvidenceContractTests(unittest.TestCase):
    def test_valid_compatibility_fixtures_cover_every_current_json_surface(self) -> None:
        fixtures = json.loads((FIXTURES / "valid-v1.json").read_text(encoding="utf-8"))

        for name in ("health", "identity", "usage_summary", "error", "policy_decision", "usage_report"):
            validate_contract(fixtures[name])
        for name in ("audit_usage_v1", "audit_security_v1", "audit_usage_v2", "audit_security_v2"):
            validate_audit_event(fixtures[name])
        self.assertEqual(fixtures["relay_contract_header"], relay_contract_header())

    def test_invalid_compatibility_fixtures_fail_closed(self) -> None:
        fixtures = json.loads((FIXTURES / "invalid-v1.json").read_text(encoding="utf-8"))

        with self.assertRaises(ContractValidationError):
            validate_contract(fixtures["policy_decision_unknown_field"])
        with self.assertRaises(ContractValidationError):
            validate_audit_event(fixtures["audit_usage_unknown_field"])

    def test_manifest_enumerates_current_contract_versions(self) -> None:
        manifest = contract_manifest()
        schemas = {
            (item["schema_id"], item["schema_version"])
            for item in manifest["schemas"]
        }
        self.assertIn((AUDIT_EVENT_SCHEMA_ID, AUDIT_EVENT_SCHEMA_VERSION), schemas)
        self.assertIn(("hormuz.policy-decision", 1), schemas)
        self.assertEqual(manifest["schema_id"], "hormuz.policy-evidence-manifest")
        self.assertEqual(manifest["schema_version"], 1)
        validate_contract_manifest(manifest)
        json.dumps(manifest, sort_keys=True)

    def test_manifest_rejects_an_undeclared_field(self) -> None:
        manifest = contract_manifest()
        manifest["undeclared"] = True
        with self.assertRaises(ContractValidationError):
            validate_contract_manifest(manifest)

    def test_contract_envelope_rejects_unknown_schema(self) -> None:
        with self.assertRaises(ContractValidationError):
            contract_envelope("hormuz.unknown", {})


if __name__ == "__main__":
    unittest.main()
