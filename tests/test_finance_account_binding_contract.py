"""Protect internal preflight decisions without claiming runtime proof."""

import copy
import json
from pathlib import Path
import tempfile
import unittest

from tools import verify_finance_account_binding_contract as verifier

ROOT = Path(__file__).resolve().parents[1]


class FinanceAccountBindingContractTests(unittest.TestCase):
    def setUp(self):
        self.contract = json.loads((ROOT / verifier.CONTRACT_PATH).read_bytes())

    def test_owner_approval_does_not_accept_runtime_or_feature_preflight(self):
        result = verifier.verify(ROOT)
        self.assertTrue(result["gates"]["owner_scope_approved"])
        self.assertTrue(all(state is False for name, state in result["gates"].items()
                            if name != "owner_scope_approved"))

    def test_each_premature_gate_promotion_is_rejected(self):
        for name in self.contract["gates"]:
            with self.subTest(name=name):
                changed = copy.deepcopy(self.contract)
                changed["gates"][name] = not changed["gates"][name]
                with self.assertRaisesRegex(ValueError, "finance_account_binding_contract_changed"):
                    verifier.validate_contract(changed)

    def test_no_new_inference_denial_secret_hash_or_historical_backfill(self):
        for name in ("missing_binding_denies_inference", "invalid_binding_denies_inference",
                     "historical_backfill", "raw_content_or_credential_values",
                     "credential_value_hashes", "provider_io", "new_role"):
            with self.subTest(name=name):
                self.assertIs(self.contract["boundary"][name], False)
                changed = copy.deepcopy(self.contract)
                changed["boundary"][name] = True
                with self.assertRaises(ValueError):
                    verifier.validate_contract(changed)

    def test_required_identity_coordinates_cannot_be_dropped(self):
        for name in self.contract["binding_identity"]["required_coordinates"]:
            with self.subTest(name=name):
                changed = copy.deepcopy(self.contract)
                changed["binding_identity"]["required_coordinates"].remove(name)
                with self.assertRaises(ValueError):
                    verifier.validate_contract(changed)

    def test_failure_and_race_cases_cannot_be_dropped(self):
        for case in self.contract["required_reference_cases"]:
            with self.subTest(case=case):
                changed = copy.deepcopy(self.contract)
                changed["required_reference_cases"].remove(case)
                with self.assertRaises(ValueError):
                    verifier.validate_contract(changed)

    def test_unsupported_or_invalid_binding_cannot_silently_become_bound(self):
        for name in self.contract["attempt_evidence"]["reasons"]:
            with self.subTest(name=name):
                changed = copy.deepcopy(self.contract)
                changed["attempt_evidence"]["reasons"].remove(name)
                with self.assertRaises(ValueError):
                    verifier.validate_contract(changed)

    def test_unapproved_migration_or_storage_activation_is_rejected(self):
        for name in ("implementation", "successor_versions_reserved", "update_delete_or_grant_option"):
            changed = copy.deepcopy(self.contract)
            changed["candidate_storage"][name] = True
            with self.subTest(name=name), self.assertRaises(ValueError):
                verifier.validate_contract(changed)

    def test_manifest_requires_new_contract_and_tool(self):
        manifest = (ROOT / "MANIFEST.in").read_text()
        self.assertIn("include " + verifier.CONTRACT_PATH, manifest.splitlines())
        self.assertIn("include tools/verify_finance_account_binding_contract.py", manifest.splitlines())

    def test_each_missing_source_kit_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for omitted in verifier.REQUIRED_FILES:
                with self.subTest(omitted=omitted):
                    for name in verifier.REQUIRED_FILES:
                        target = root / name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        if name == omitted:
                            target.unlink(missing_ok=True)
                        else:
                            target.write_bytes((ROOT / name).read_bytes())
                    with self.assertRaisesRegex(ValueError, "finance_account_binding_source_kit_incomplete"):
                        verifier.verify(root)


if __name__ == "__main__":
    unittest.main()
