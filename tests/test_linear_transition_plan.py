from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from tools import verify_linear_transition_plan as verifier


class LinearTransitionPlanTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        plan = json.loads((verifier.ROOT / verifier.PLAN_PATH).read_text())
        paths = set(verifier.REQUIRED_FILES) | set(plan["frozen_file_sha256"])
        for relative in paths:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(verifier.ROOT / relative, target)

    def plan(self):
        return json.loads((self.root / verifier.PLAN_PATH).read_text())

    def write_plan(self, value):
        (self.root / verifier.PLAN_PATH).write_text(
            json.dumps(value, indent=2) + "\n", encoding="utf-8"
        )

    def test_verifies_assigned_versions_and_preserves_runtime_gates(self):
        result = verifier.verify(self.root)
        self.assertEqual(result["sqlite_transition"], [13, 14])
        self.assertEqual(result["postgresql_transition"], [18, 19])
        self.assertEqual(result["proposal_tables"], 4)
        self.assertFalse(result["runtime_implemented"])
        self.assertFalse(result["live_workspace_authorized"])

    def test_duplicate_plan_member_is_rejected(self):
        path = self.root / verifier.PLAN_PATH
        payload = path.read_text(encoding="utf-8")
        path.write_text(payload.replace(
            '"schema_version": 1,',
            '"schema_version": 1,\n  "schema_version": 1,',
            1,
        ), encoding="utf-8")
        with self.assertRaisesRegex(
            verifier.LinearTransitionPlanError,
            "linear_transition_plan_invalid",
        ):
            verifier.verify(self.root)

    def test_gate_overclaim_is_rejected_even_with_a_repinned_plan(self):
        plan = self.plan()
        plan["gates"]["runtime_implemented"] = True
        self.write_plan(plan)
        with mock.patch.object(verifier, "PLAN_SHA256", verifier.canonical_digest(plan)):
            with self.assertRaisesRegex(
                verifier.LinearTransitionPlanError,
                "linear_transition_gate_overclaim",
            ):
                verifier.verify(self.root)

    def test_frozen_contract_change_is_rejected(self):
        path = self.root / "docs/linear-context-wire-v1.json"
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(
            verifier.LinearTransitionPlanError,
            "linear_transition_frozen_file_changed",
        ):
            verifier.verify(self.root)

    def test_proposal_cannot_add_mutation_privileges(self):
        relative = verifier.POSTGRES_PROPOSAL
        path = self.root / relative
        path.write_text(
            path.read_text(encoding="utf-8")
            + "\nGRANT UPDATE ON {schema}.portfolio_linear_context_events TO {runtime_role};\n",
            encoding="utf-8",
        )
        plan = self.plan()
        plan["frozen_file_sha256"][relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.write_plan(plan)
        with mock.patch.object(verifier, "PLAN_SHA256", verifier.canonical_digest(plan)):
            with self.assertRaisesRegex(
                verifier.LinearTransitionPlanError,
                "linear_transition_proposal_invalid",
            ):
                verifier.verify(self.root)

    def test_runtime_schema_advance_requires_a_superseding_plan(self):
        with mock.patch.object(verifier, "SQLITE_SCHEMA_VERSION", 14):
            with self.assertRaisesRegex(
                verifier.LinearTransitionPlanError,
                "linear_transition_baseline_changed",
            ):
                verifier.verify(self.root)


if __name__ == "__main__":
    unittest.main()
