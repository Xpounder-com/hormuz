"""The Linear runtime manifest fixes code and preserves every release gate."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from tools import verify_linear_runtime_plan as verifier
from tools import verify_linear_reconciliation_plan as reconciliation_verifier


class LinearRuntimePlanTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        plan = json.loads((verifier.ROOT / verifier.PLAN_PATH).read_text())
        paths = set(verifier.REQUIRED_FILES) | set(plan["source_sha256"])
        reconciliation = json.loads(
            (reconciliation_verifier.ROOT / reconciliation_verifier.PLAN_PATH).read_text()
        )
        paths.update(reconciliation_verifier.REQUIRED_FILES)
        paths.update(reconciliation["source_sha256"])
        for relative in paths:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(verifier.ROOT / relative, target)

    def plan(self):
        return json.loads((self.root / verifier.PLAN_PATH).read_text())

    def write_plan(self, value):
        (self.root / verifier.PLAN_PATH).write_text(
            json.dumps(value, indent=2) + "\n",
            encoding="utf-8",
        )

    def test_fixed_candidate_preserves_postgresql_live_and_release_gates(self):
        result = verifier.verify(self.root)
        self.assertEqual(result["status"], "linear_runtime_successor_verified")
        self.assertEqual(
            (result["sqlite_schema_version"], result["postgresql_schema_version"]),
            (15, 20),
        )
        self.assertEqual(
            result["postgresql_acl"],
            list(reconciliation_verifier.EXPECTED_ACL),
        )
        self.assertEqual(
            (
                result["table_count"],
                result["enforcement_table_count"],
                result["audit_source_count"],
            ),
            (4, 1, 4),
        )
        self.assertTrue(result["runtime_implemented"])
        self.assertFalse(result["live_workspace_authorized"])
        self.assertFalse(result["released"])

    def test_plan_gate_overclaim_is_rejected_even_when_repinned(self):
        plan = self.plan()
        plan["gates"]["live_delivery_verified"] = True
        self.write_plan(plan)
        with mock.patch.object(verifier, "PLAN_SHA256", verifier.canonical_digest(plan)), mock.patch.object(
            verifier, "SQLITE_SCHEMA_VERSION", 14,
        ), mock.patch.object(
            verifier, "POSTGRES_SCHEMA_VERSION", 19,
        ):
            with self.assertRaisesRegex(
                verifier.LinearRuntimePlanError,
                "linear_runtime_gate_overclaim",
            ):
                verifier.verify(self.root)

    def test_changed_or_missing_runtime_source_is_rejected(self):
        relative = "hormuz/linear_repository.py"
        path = self.root / relative
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(
            verifier.LinearRuntimePlanError,
            "linear_runtime_source_changed",
        ):
            verifier.verify(self.root)
        path.unlink()
        with self.assertRaisesRegex(
            verifier.LinearRuntimePlanError,
            "linear_runtime_source_kit_incomplete",
        ):
            verifier.verify(self.root)

    def test_schema_or_acl_boundary_change_is_rejected(self):
        for sqlite_version, postgres_version, acl in (
            (13, 19, verifier.EXPECTED_ACL),
            (14, 18, verifier.EXPECTED_ACL),
            (14, 19, (234, "0" * 64)),
        ):
            with self.subTest(
                sqlite=sqlite_version,
                postgres=postgres_version,
            ), mock.patch.object(
                verifier, "SQLITE_SCHEMA_VERSION", sqlite_version,
            ), mock.patch.object(
                verifier, "POSTGRES_SCHEMA_VERSION", postgres_version,
            ), mock.patch.dict(
                verifier._POSTGRES_EXPECTED_ACL_BOUNDARY_BY_VERSION,
                {19: acl},
            ):
                with self.assertRaisesRegex(
                    verifier.LinearRuntimePlanError,
                    "linear_runtime_schema_boundary_changed",
                ):
                    verifier.verify(self.root)

    def test_migration_cannot_gain_mutation_privileges(self):
        relative = "hormuz/migrations/postgresql/0019_linear_connector.sql"
        path = self.root / relative
        path.write_text(
            path.read_text(encoding="utf-8")
            + "\nGRANT UPDATE ON {schema}.portfolio_linear_context_events TO {runtime_role};\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            verifier.LinearRuntimePlanError,
            "linear_runtime_source_changed",
        ):
            verifier.verify(self.root)

    def test_predecessor_plan_is_still_immutable(self):
        path = self.root / verifier.PREDECESSOR_PATH
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(
            verifier.LinearRuntimePlanError,
            "linear_runtime_predecessor_changed",
        ):
            verifier.verify(self.root)


if __name__ == "__main__":
    unittest.main()
