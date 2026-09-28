"""The #222 manifest fixes provider-free scorecard evidence and open release gates."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from tools import verify_core_wheel as packaging
from tools import verify_recommendation_runtime as recommendation_verifier
from tools import verify_role_view_runtime as successor_verifier
from tools import verify_scorecard_runtime_plan as verifier


class ScorecardRuntimePlanTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        plan = json.loads((verifier.ROOT / verifier.PLAN_PATH).read_text())
        successor_plan = json.loads(
            (successor_verifier.ROOT / successor_verifier.PLAN_PATH).read_text()
        )
        recommendation_plan = json.loads(
            (
                recommendation_verifier.ROOT
                / recommendation_verifier.PLAN_PATH
            ).read_text()
        )
        paths = (
            set(verifier.REQUIRED_FILES)
            | set(plan["source_sha256"])
            | set(successor_verifier.REQUIRED_FILES)
            | set(successor_plan["source_sha256"])
            | set(recommendation_verifier.REQUIRED_FILES)
            | set(recommendation_plan["source_sha256"])
        )
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

    def repin(self, plan, relative):
        plan["source_sha256"][relative] = hashlib.sha256(
            (self.root / relative).read_bytes()
        ).hexdigest()
        self.write_plan(plan)
        return mock.patch.object(verifier, "PLAN_SHA256", verifier.canonical_digest(plan))

    def test_fixed_runtime_preserves_public_view_and_release_gates(self):
        result = verifier.verify(self.root)
        self.assertEqual(result["status"], "scorecard_runtime_successor_verified")
        self.assertEqual(
            (result["sqlite_schema_version"], result["postgresql_schema_version"]),
            (19, 24),
        )
        self.assertEqual(
            result["postgresql_acl"], list(recommendation_verifier.EXPECTED_ACL)
        )
        self.assertEqual(result["table_count"], 2)
        self.assertTrue(result["kernel_implemented"])
        self.assertTrue(result["runtime_implemented"])
        self.assertTrue(result["role_scoped_decision_views_implemented"])
        self.assertFalse(result["public_scorecard_route"])
        self.assertFalse(result["live_connectors_authorized"])
        self.assertFalse(result["released"])

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
            verifier.ScorecardRuntimePlanError,
            "scorecard_runtime_plan_invalid",
        ):
            verifier.verify(self.root)

    def test_gate_overclaim_is_rejected_even_when_repinned(self):
        plan = self.plan()
        plan["gates"]["exact_main_ci_verified"] = True
        self.write_plan(plan)
        with mock.patch.object(
            verifier, "PLAN_SHA256", verifier.canonical_digest(plan)
        ), mock.patch.object(
            verifier, "SQLITE_SCHEMA_VERSION", 17
        ), mock.patch.object(
            verifier, "POSTGRES_SCHEMA_VERSION", 22
        ):
            with self.assertRaisesRegex(
                verifier.ScorecardRuntimePlanError,
                "scorecard_runtime_gate_overclaim",
            ):
                verifier.verify(self.root)

    def test_changed_or_missing_runtime_source_is_rejected(self):
        relative = "hormuz/scorecard_kernel.py"
        path = self.root / relative
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(
            verifier.ScorecardRuntimePlanError,
            "scorecard_runtime_source_changed",
        ):
            verifier.verify(self.root)
        path.unlink()
        with self.assertRaisesRegex(
            verifier.ScorecardRuntimePlanError,
            "scorecard_runtime_source_kit_incomplete",
        ):
            verifier.verify(self.root)

    def test_schema_or_acl_boundary_change_is_rejected(self):
        for sqlite_version, postgres_version, acl in (
            (18, 24, recommendation_verifier.EXPECTED_ACL),
            (19, 23, recommendation_verifier.EXPECTED_ACL),
            (19, 24, (249, "0" * 64)),
        ):
            with self.subTest(
                sqlite=sqlite_version, postgres=postgres_version
            ), mock.patch.object(
                verifier, "SQLITE_SCHEMA_VERSION", sqlite_version
            ), mock.patch.object(
                verifier, "POSTGRES_SCHEMA_VERSION", postgres_version
            ), mock.patch.dict(
                verifier._POSTGRES_EXPECTED_ACL_BOUNDARY_BY_VERSION,
                {24: acl},
            ):
                with self.assertRaisesRegex(
                    verifier.ScorecardRuntimePlanError,
                    "scorecard_runtime_schema_boundary_changed",
                ):
                    verifier.verify(self.root)

    def test_migration_cannot_gain_mutation_privileges(self):
        relative = "hormuz/migrations/postgresql/0022_model_scorecards.sql"
        path = self.root / relative
        path.write_text(
            path.read_text(encoding="utf-8")
            + "\nGRANT UPDATE ON {schema}.portfolio_model_scorecard_snapshots TO {runtime_role};\n",
            encoding="utf-8",
        )
        plan = self.plan()
        with self.repin(plan, relative):
            with self.assertRaisesRegex(
                verifier.ScorecardRuntimePlanError,
                "scorecard_runtime_migration_invalid",
            ):
                verifier._validate_successor_predecessor(self.root)

    def test_frozen_scorecard_vector_cannot_be_rewritten(self):
        relative = "tests/fixtures/scorecard/runtime-v1.json"
        path = self.root / relative
        fixture = json.loads(path.read_text(encoding="utf-8"))
        fixture["expected"]["scorecard"]["pareto_cohort_ids"] = []
        path.write_text(json.dumps(fixture, indent=2) + "\n", encoding="utf-8")
        plan = self.plan()
        with self.repin(plan, relative):
            with self.assertRaisesRegex(
                verifier.ScorecardRuntimePlanError,
                "scorecard_runtime_fixture_invalid",
            ):
                verifier._validate_successor_predecessor(self.root)

    def test_forbidden_person_or_content_field_cannot_be_repinned(self):
        relative = "tests/fixtures/scorecard/runtime-v1.json"
        path = self.root / relative
        fixture = json.loads(path.read_text(encoding="utf-8"))
        fixture["input"]["cohorts"][0]["strata"][0]["work_items"][0]["employee_id"] = "person"
        path.write_text(json.dumps(fixture, indent=2) + "\n", encoding="utf-8")
        plan = self.plan()
        with self.repin(plan, relative):
            with self.assertRaisesRegex(
                verifier.ScorecardRuntimePlanError,
                "scorecard_runtime_fixture_invalid",
            ):
                verifier._validate_successor_predecessor(self.root)

    def test_association_runtime_predecessor_is_immutable(self):
        path = self.root / verifier.PREDECESSOR_PATH
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(
            verifier.ScorecardRuntimePlanError,
            "scorecard_runtime_predecessor_changed",
        ):
            verifier.verify(self.root)

    def test_distribution_requires_complete_runtime_kit(self):
        for required, label in (
            (packaging.REQUIRED_SCORECARD_RUNTIME_WHEEL_PATHS, "Scorecard runtime wheel"),
            (packaging.REQUIRED_SCORECARD_RUNTIME_SDIST_PATHS, "Scorecard runtime source kit"),
        ):
            archive_members = ["hormuz-1.2.0/" + path for path in required]
            packaging._assert_required_archive_paths(
                Path("test.whl"), mock.Mock(return_value=archive_members), required, label
            )
            for missing in required:
                incomplete = [item for item in archive_members if not item.endswith("/" + missing)]
                with self.subTest(label=label, missing=missing):
                    with self.assertRaisesRegex(RuntimeError, label):
                        packaging._assert_required_archive_paths(
                            Path("test.whl"), mock.Mock(return_value=incomplete), required, label
                        )


if __name__ == "__main__":
    unittest.main()
