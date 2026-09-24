"""The Linear reconciliation manifest fixes source while preserving live gates."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from tools import verify_core_wheel as packaging
from tools import verify_linear_reconciliation_plan as verifier


class LinearReconciliationPlanTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        plan = json.loads((verifier.ROOT / verifier.PLAN_PATH).read_text())
        paths = set(verifier.REQUIRED_FILES) | set(plan["source_sha256"])
        for relative in paths:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(verifier.ROOT / relative, target)
        plan = self.plan()
        for relative in plan["source_sha256"]:
            plan["source_sha256"][relative] = hashlib.sha256(
                (self.root / relative).read_bytes()
            ).hexdigest()
        self.write_plan(plan)
        self.baseline_plan_sha256 = verifier.canonical_digest(plan)

    def plan(self):
        return json.loads((self.root / verifier.PLAN_PATH).read_text())

    def write_plan(self, value):
        (self.root / verifier.PLAN_PATH).write_text(
            json.dumps(value, indent=2) + "\n",
            encoding="utf-8",
        )

    def verify_baseline(self):
        with mock.patch.object(
            verifier, "PLAN_SHA256", self.baseline_plan_sha256
        ), mock.patch.object(
            verifier, "SQLITE_SCHEMA_VERSION", 15
        ), mock.patch.object(
            verifier, "POSTGRES_SCHEMA_VERSION", 20
        ):
            return verifier.verify(self.root)

    def test_fixed_candidate_preserves_live_wheel_and_release_gates(self):
        result = self.verify_baseline()
        self.assertEqual(result["status"], "linear_reconciliation_candidate_verified")
        self.assertEqual(
            (result["sqlite_schema_version"], result["postgresql_schema_version"]),
            (15, 20),
        )
        self.assertEqual(result["postgresql_acl"], list(verifier.EXPECTED_ACL))
        self.assertEqual((result["table_count"], result["audit_source_count"]), (3, 2))
        self.assertTrue(result["reconciliation_implemented"])
        self.assertFalse(result["live_workspace_authorized"])
        self.assertFalse(result["live_reconciliation_verified"])
        self.assertFalse(result["released"])

    def test_current_association_successor_chain_is_verified(self):
        result = verifier.verify(verifier.ROOT)
        self.assertEqual(result["status"], "linear_reconciliation_successor_verified")
        self.assertEqual(
            (result["sqlite_schema_version"], result["postgresql_schema_version"]),
            (16, 21),
        )
        self.assertTrue(result["association_runtime_implemented"])

    def test_gate_overclaim_is_rejected_even_when_repinned(self):
        plan = self.plan()
        plan["gates"]["live_reconciliation_verified"] = True
        self.write_plan(plan)
        with mock.patch.object(verifier, "PLAN_SHA256", verifier.canonical_digest(plan)), mock.patch.object(
            verifier, "SQLITE_SCHEMA_VERSION", 15,
        ), mock.patch.object(
            verifier, "POSTGRES_SCHEMA_VERSION", 20,
        ):
            with self.assertRaisesRegex(
                verifier.LinearReconciliationPlanError,
                "linear_reconciliation_gate_overclaim",
            ):
                verifier.verify(self.root)

    def test_changed_or_missing_source_is_rejected(self):
        relative = "hormuz/linear_snapshot.py"
        path = self.root / relative
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(
            verifier.LinearReconciliationPlanError,
            "linear_reconciliation_source_changed",
        ):
            self.verify_baseline()
        path.unlink()
        with self.assertRaisesRegex(
            verifier.LinearReconciliationPlanError,
            "linear_reconciliation_source_kit_incomplete",
        ):
            self.verify_baseline()

    def test_schema_or_acl_boundary_change_is_rejected(self):
        for sqlite_version, postgres_version, acl in (
            (14, 20, verifier.EXPECTED_ACL),
            (15, 19, verifier.EXPECTED_ACL),
            (15, 20, (240, "0" * 64)),
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
                {20: acl},
            ):
                with self.assertRaisesRegex(
                    verifier.LinearReconciliationPlanError,
                    "linear_reconciliation_schema_boundary_changed",
                ), mock.patch.object(
                    verifier, "PLAN_SHA256", self.baseline_plan_sha256
                ):
                    verifier.verify(self.root)

    def test_cumulative_transition_suites_are_fixed_source(self):
        plan = self.plan()
        self.assertTrue(
            set(verifier.CUMULATIVE_TRANSITION_FILES).issubset(plan["source_sha256"])
        )
        plan["source_sha256"].pop(verifier.CUMULATIVE_TRANSITION_FILES[0])
        self.write_plan(plan)
        with mock.patch.object(verifier, "PLAN_SHA256", verifier.canonical_digest(plan)), mock.patch.object(
            verifier, "SQLITE_SCHEMA_VERSION", 15,
        ), mock.patch.object(
            verifier, "POSTGRES_SCHEMA_VERSION", 20,
        ):
            with self.assertRaisesRegex(
                verifier.LinearReconciliationPlanError,
                "linear_reconciliation_plan_invalid",
            ):
                verifier.verify(self.root)

    def test_migration_cannot_gain_mutation_privileges(self):
        relative = "hormuz/migrations/postgresql/0020_linear_snapshot.sql"
        path = self.root / relative
        path.write_text(
            path.read_text(encoding="utf-8")
            + "\nGRANT UPDATE ON {schema}.gateway_linear_snapshot_receipts TO {runtime_role};\n",
            encoding="utf-8",
        )
        plan = self.plan()
        plan["source_sha256"][relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.write_plan(plan)
        with mock.patch.object(verifier, "PLAN_SHA256", verifier.canonical_digest(plan)), mock.patch.object(
            verifier, "SQLITE_SCHEMA_VERSION", 15,
        ), mock.patch.object(
            verifier, "POSTGRES_SCHEMA_VERSION", 20,
        ):
            with self.assertRaisesRegex(
                verifier.LinearReconciliationPlanError,
                "linear_reconciliation_migration_invalid",
            ):
                verifier.verify(self.root)

    def test_postgres_snapshot_runtime_must_remain_in_ci(self):
        path = self.root / verifier.CI_PATH
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "-p test_postgres_linear_snapshot_runtime.py",
                "-p test_postgres_linear_snapshot_runtime_omitted.py",
            ),
            encoding="utf-8",
        )
        plan = self.plan()
        plan["source_sha256"][verifier.CI_PATH] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        self.write_plan(plan)
        with mock.patch.object(
            verifier, "PLAN_SHA256", verifier.canonical_digest(plan)
        ), mock.patch.object(
            verifier, "SQLITE_SCHEMA_VERSION", 15,
        ), mock.patch.object(
            verifier, "POSTGRES_SCHEMA_VERSION", 20,
        ):
            with self.assertRaisesRegex(
                verifier.LinearReconciliationPlanError,
                "linear_reconciliation_ci_invalid",
            ):
                verifier.verify(self.root)

    def test_runtime_predecessor_is_immutable(self):
        path = self.root / verifier.PREDECESSOR_PATH
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(
            verifier.LinearReconciliationPlanError,
            "linear_reconciliation_predecessor_changed",
        ):
            self.verify_baseline()

    def test_distribution_requires_complete_reconciliation_kit(self):
        for required, label in (
            (
                packaging.REQUIRED_LINEAR_RECONCILIATION_WHEEL_PATHS,
                "Linear reconciliation wheel",
            ),
            (
                packaging.REQUIRED_LINEAR_RECONCILIATION_SDIST_PATHS,
                "Linear reconciliation source kit",
            ),
        ):
            archive_members = ["hormuz-1.2.0/" + path for path in required]
            with self.subTest(label=label):
                packaging._assert_required_archive_paths(
                    Path("test.whl"),
                    mock.Mock(return_value=archive_members),
                    required,
                    label,
                )
            for missing in required:
                incomplete = [
                    item
                    for item in archive_members
                    if not item.endswith("/" + missing)
                ]
                with self.subTest(label=label, missing=missing):
                    with self.assertRaisesRegex(RuntimeError, label):
                        packaging._assert_required_archive_paths(
                            Path("test.whl"),
                            mock.Mock(return_value=incomplete),
                            required,
                            label,
                        )


if __name__ == "__main__":
    unittest.main()
