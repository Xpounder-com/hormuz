"""#8 provider collection runtime candidate contract tests."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from tools import verify_finance_collection_runtime as verifier
from tools.verify_finance_collection_runtime import FinanceCollectionRuntimeError


ROOT = Path(__file__).resolve().parents[1]


class FinanceCollectionRuntimePlanTests(unittest.TestCase):
    def plan(self):
        return json.loads((ROOT / verifier.PLAN_PATH).read_bytes())

    def test_candidate_is_verified_but_runtime_and_postgres_access_remain_gated(self):
        result = verifier.verify_finance_collection_runtime(ROOT)
        self.assertEqual(result["status"], "finance_collection_runtime_candidate_verified")
        self.assertEqual(result["current_sqlite_schema_version"], 12)
        self.assertEqual(result["current_postgresql_schema_version"], 16)
        self.assertTrue(result["collection_preflight_accepted"])
        self.assertFalse(result["provider_collection_runtime_accepted"])
        self.assertFalse(result["postgresql_collection_runtime_accepted"])
        self.assertFalse(result["reconciliation_implemented"])
        self.assertEqual(result["postgresql_acl_current"][0], 185)
        self.assertEqual(result["postgresql_acl_injected"][0], 186)

    def test_plan_digest_is_frozen(self):
        value = self.plan()
        verifier.validate_finance_collection_runtime_plan(value)
        changed = copy.deepcopy(value)
        changed["gates"]["provider_collection_runtime_accepted"] = True
        with self.assertRaisesRegex(
            FinanceCollectionRuntimeError,
            "finance_collection_runtime_contract_changed",
        ):
            verifier.validate_finance_collection_runtime_plan(changed)

    def test_current_acl_boundary_cannot_be_replaced_or_computed(self):
        value = self.plan()
        for key, replacement in (
            ("expected_current_count", 199),
            ("expected_current_sha256", "0" * 64),
            ("injected_permission_count", 187),
            ("accepts_multiple_fingerprints", True),
            ("computes_expected_from_database", True),
        ):
            with self.subTest(key=key):
                changed = copy.deepcopy(value)
                changed["postgresql_acl_gate"][key] = replacement
                with self.assertRaises(FinanceCollectionRuntimeError):
                    verifier.validate_finance_collection_runtime_plan(changed)

    def test_runtime_role_grants_are_a_fixed_gate(self):
        migration = ROOT / "hormuz/migrations/postgresql/0016_finance_collection.sql"
        original = migration.read_text(encoding="utf-8")
        for grant in (
            "GRANT SELECT, INSERT ON {schema}.portfolio_finance_snapshots TO {runtime_role};",
            "GRANT ALL\nON {schema}.portfolio_finance_snapshots\nTO {runtime_role};",
        ):
            with self.subTest(grant=grant), self.assertRaisesRegex(
                FinanceCollectionRuntimeError,
                "finance_collection_postgres_grants_not_gated",
            ):
                verifier._validate_postgres_runtime_grants(
                    original + "\n" + grant,
                    ["portfolio_finance_snapshots"],
                )


if __name__ == "__main__":
    unittest.main()
