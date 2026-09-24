"""The #221 runtime manifest fixes provider-free evidence and keeps release gates open."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from tools import verify_association_runtime_plan as verifier
from tools import verify_core_wheel as packaging


class AssociationRuntimePlanTests(unittest.TestCase):
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
        # The immutable predecessor plan pins its original source tree.  This
        # successor test fixture uses current source files to exercise the
        # historical validation branch, so give the synthetic copy its own
        # internally consistent source manifest without changing the checked-in
        # predecessor artifact.
        for relative in plan["source_sha256"]:
            plan["source_sha256"][relative] = hashlib.sha256(
                (self.root / relative).read_bytes()
            ).hexdigest()
        self.write_plan(plan)
        self.historical_plan_sha256 = verifier.canonical_digest(plan)

    def plan(self):
        return json.loads((self.root / verifier.PLAN_PATH).read_text())

    def write_plan(self, value):
        (self.root / verifier.PLAN_PATH).write_text(
            json.dumps(value, indent=2) + "\n", encoding="utf-8"
        )

    def repin(self, plan, relative):
        plan["source_sha256"][relative] = hashlib.sha256(
            (self.root / relative).read_bytes()
        ).hexdigest()
        self.write_plan(plan)
        return verifier.canonical_digest(plan)

    def verify_historical(self, plan_sha256=None):
        with (
            mock.patch.object(
                verifier,
                "PLAN_SHA256",
                plan_sha256 or self.historical_plan_sha256,
            ),
            mock.patch.object(verifier, "SQLITE_SCHEMA_VERSION", 16),
            mock.patch.object(verifier, "POSTGRES_SCHEMA_VERSION", 21),
            mock.patch.dict(
                verifier._POSTGRES_EXPECTED_ACL_BOUNDARY_BY_VERSION,
                {21: verifier.EXPECTED_ACL},
            ),
        ):
            return verifier.verify(self.root)

    def test_scorecard_successor_preserves_live_and_release_gates(self):
        result = verifier.verify(verifier.ROOT)
        self.assertEqual(result["status"], "association_runtime_successor_verified")
        self.assertEqual(
            (result["sqlite_schema_version"], result["postgresql_schema_version"]),
            (19, 24),
        )
        self.assertEqual(result["postgresql_acl"][0], 249)
        self.assertEqual((result["table_count"], result["audit_source_count"]), (5, 2))
        self.assertTrue(result["runtime_implemented"])
        self.assertTrue(result["metric_reference_implemented"])
        self.assertTrue(result["scorecard_runtime_implemented"])
        self.assertTrue(result["scorecard_kernel_implemented"])
        self.assertFalse(result["public_scorecard_route"])
        self.assertFalse(result["live_connectors_authorized"])
        self.assertFalse(result["released"])

    def test_scorecard_successor_rechecks_association_owned_sources(self):
        with mock.patch.object(
            verifier, "PLAN_SHA256", self.historical_plan_sha256
        ):
            verifier._validate_successor_predecessor(self.root)
            path = self.root / "hormuz/association_repository.py"
            path.write_bytes(path.read_bytes() + b"\n")
            with self.assertRaisesRegex(
                verifier.AssociationRuntimePlanError,
                "association_runtime_source_changed",
            ):
                verifier._validate_successor_predecessor(self.root)

    def test_duplicate_plan_member_is_rejected(self):
        path = self.root / verifier.PLAN_PATH
        payload = path.read_text(encoding="utf-8")
        path.write_text(
            payload.replace(
                '"schema_version": 1,',
                '"schema_version": 1,\n  "schema_version": 1,',
                1,
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            verifier.AssociationRuntimePlanError,
            "association_runtime_plan_invalid",
        ):
            self.verify_historical()

    def test_gate_overclaim_is_rejected_even_when_repinned(self):
        plan = self.plan()
        plan["gates"]["live_multi_source_evidence_verified"] = True
        self.write_plan(plan)
        with self.assertRaisesRegex(
            verifier.AssociationRuntimePlanError,
            "association_runtime_gate_overclaim",
        ):
            self.verify_historical(verifier.canonical_digest(plan))

    def test_changed_or_missing_runtime_source_is_rejected(self):
        relative = "hormuz/association_repository.py"
        path = self.root / relative
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(
            verifier.AssociationRuntimePlanError,
            "association_runtime_source_changed",
        ):
            self.verify_historical()
        path.unlink()
        with self.assertRaisesRegex(
            verifier.AssociationRuntimePlanError,
            "association_runtime_source_kit_incomplete",
        ):
            self.verify_historical()

    def test_schema_or_acl_boundary_change_is_rejected(self):
        for sqlite_version, postgres_version, acl in (
            (15, 21, verifier.EXPECTED_ACL),
            (16, 20, verifier.EXPECTED_ACL),
            (16, 21, (253, "0" * 64)),
        ):
            with self.subTest(
                sqlite=sqlite_version, postgres=postgres_version
            ), mock.patch.object(
                verifier, "SQLITE_SCHEMA_VERSION", sqlite_version
            ), mock.patch.object(
                verifier, "POSTGRES_SCHEMA_VERSION", postgres_version
            ), mock.patch.dict(
                verifier._POSTGRES_EXPECTED_ACL_BOUNDARY_BY_VERSION,
                {21: acl},
            ), mock.patch.object(
                verifier,
                "PLAN_SHA256",
                self.historical_plan_sha256,
            ):
                with self.assertRaisesRegex(
                    verifier.AssociationRuntimePlanError,
                    "association_runtime_schema_boundary_changed",
                ):
                    verifier.verify(self.root)

    def test_migration_cannot_gain_mutation_privileges(self):
        relative = "hormuz/migrations/postgresql/0021_run_outcome_association.sql"
        path = self.root / relative
        path.write_text(
            path.read_text(encoding="utf-8")
            + "\nGRANT UPDATE ON {schema}.portfolio_run_work_link_events TO {runtime_role};\n",
            encoding="utf-8",
        )
        plan = self.plan()
        plan_sha256 = self.repin(plan, relative)
        with self.assertRaisesRegex(
            verifier.AssociationRuntimePlanError,
            "association_runtime_migration_invalid",
        ):
            self.verify_historical(plan_sha256)

    def test_frozen_metric_vector_cannot_be_rewritten(self):
        relative = "tests/fixtures/association/runtime-multisource-v1.json"
        path = self.root / relative
        fixture = json.loads(path.read_text(encoding="utf-8"))
        fixture["expected"]["coverage"]["attributed_runs"]["ratio"] = "0"
        path.write_text(json.dumps(fixture, indent=2) + "\n", encoding="utf-8")
        plan = self.plan()
        plan_sha256 = self.repin(plan, relative)
        with self.assertRaisesRegex(
            verifier.AssociationRuntimePlanError,
            "association_runtime_fixture_invalid",
        ):
            self.verify_historical(plan_sha256)

    def test_transition_predecessor_is_immutable(self):
        path = self.root / verifier.PREDECESSOR_PATH
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(
            verifier.AssociationRuntimePlanError,
            "association_runtime_predecessor_changed",
        ):
            self.verify_historical()

    def test_distribution_requires_complete_runtime_kit(self):
        for required, label in (
            (
                packaging.REQUIRED_ASSOCIATION_RUNTIME_WHEEL_PATHS,
                "Association runtime wheel",
            ),
            (
                packaging.REQUIRED_ASSOCIATION_RUNTIME_SDIST_PATHS,
                "Association runtime source kit",
            ),
        ):
            archive_members = ["hormuz-1.2.0/" + path for path in required]
            packaging._assert_required_archive_paths(
                Path("test.whl"), mock.Mock(return_value=archive_members), required, label
            )
            for missing in required:
                incomplete = [
                    item for item in archive_members if not item.endswith("/" + missing)
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
