"""The owner-approved successor is fixed, additive, and not acceptance."""

import copy
import json
from pathlib import Path
import unittest
from unittest import mock

from tools import verify_finance_collection_postgres_runtime as verifier
from tools.verify_finance_collection_runtime import FinanceCollectionRuntimeError

ROOT = Path(__file__).resolve().parents[1]


class FinanceCollectionPostgresRuntimePlanTests(unittest.TestCase):
    def test_fixed_candidate_preserves_open_acceptance_gates(self):
        result = verifier.verify(ROOT)
        self.assertEqual(result["current_postgresql_schema_version"], 17)
        self.assertEqual(result["postgresql_acl_current"][0], 199)
        self.assertEqual(result["postgresql_acl_injected_rejected"][0], 200)
        self.assertTrue(result["gates"]["owner_approved_acl_implementation"])
        for gate, state in result["gates"].items():
            if gate not in {"owner_approved_acl_implementation", "postgresql_collection_runtime_implemented"}:
                self.assertIs(state, False, gate)

    def test_plan_rejects_changed_acl_dynamic_expectation_or_early_acceptance(self):
        original = json.loads((ROOT / verifier.PLAN_PATH).read_bytes())
        for section, key, replacement in (
            ("postgresql_acl_gate", "schema17", [185, "0" * 64]),
            ("postgresql_acl_gate", "accepts_multiple_fingerprints", True),
            ("postgresql_acl_gate", "computes_expected_from_database", True),
            ("gates", "postgresql_collection_runtime_accepted", True),
            ("migration", "privileges", ["SELECT", "INSERT", "UPDATE"]),
        ):
            changed = copy.deepcopy(original)
            changed[section][key] = replacement
            with self.subTest(key=key), self.assertRaises(FinanceCollectionRuntimeError):
                verifier.validate_plan(changed)

    def test_actual_runtime_map_cannot_accept_alternate_or_dynamic_fingerprint(self):
        import hormuz.postgres as postgres
        for replacement in ((185, "0" * 64), (200, verifier.INJECTED_ACL[1]), [verifier.EXPECTED_ACL, verifier.INJECTED_ACL]):
            with mock.patch.dict(postgres._POSTGRES_EXPECTED_ACL_BOUNDARY_BY_VERSION, {17: replacement}):
                with self.assertRaisesRegex(FinanceCollectionRuntimeError, "finance_collection_postgres_runtime_boundary_changed"):
                    verifier.verify(ROOT)
