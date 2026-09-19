"""Cross-language reference vectors exercised by the existing gateway validators."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from hormuz.contracts import ContractValidationError, validate_contract

FIXTURES = Path(__file__).parent / "fixtures" / "native_client" / "v1" / "gateway.json"


class NativeClientContractTests(unittest.TestCase):
    def test_shared_vectors_preserve_gateway_schema_expectations(self) -> None:
        fixtures = json.loads(FIXTURES.read_text(encoding="utf-8"))
        self.assertEqual(fixtures["schema_id"], "hormuz.native-client-fixtures")
        self.assertEqual(fixtures["schema_version"], 1)
        self.assertRegex(fixtures["source_revision"], r"^[0-9a-f]{40}$")
        self.assertEqual(len(fixtures["cases"]), 41)
        identifiers = [case["id"] for case in fixtures["cases"]]
        self.assertEqual(len(identifiers), len(set(identifiers)))
        for case in fixtures["cases"]:
            with self.subTest(case=case["id"]):
                self.assertIsInstance(case["gateway_valid"], bool)
                self.assertIsInstance(case["client_valid"], bool)
                if case["gateway_valid"]:
                    validate_contract(case["response"])
                else:
                    with self.assertRaises(ContractValidationError):
                        validate_contract(case["response"])
                if case["gateway_valid"] != case["client_valid"]:
                    self.assertTrue(case["note"], "Document client/server responsibility differences")
                self.assertEqual(case["expected"] is not None, case["client_valid"])

    def test_usage_baseline_is_the_existing_frozen_gateway_fixture(self) -> None:
        existing = json.loads(
            (Path(__file__).parent / "fixtures" / "contracts" / "valid-v1.json").read_text()
        )
        shared = json.loads(FIXTURES.read_text(encoding="utf-8"))
        usage = next(case for case in shared["cases"] if case["id"] == "usage_current")
        self.assertEqual(usage["response"], existing["usage_summary"])


if __name__ == "__main__":
    unittest.main()
