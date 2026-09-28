"""The #224 manifest fixes recommendations while preserving release gates."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from tools import verify_core_wheel as packaging
from tools import verify_recommendation_runtime as verifier


class RecommendationRuntimePlanTests(unittest.TestCase):
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
        return mock.patch.object(
            verifier, "PLAN_SHA256", verifier.canonical_digest(plan)
        )

    def test_fixed_runtime_preserves_activation_external_and_release_gates(self):
        result = verifier.verify(self.root)
        self.assertEqual(
            result["status"], "recommendation_runtime_candidate_verified"
        )
        self.assertEqual(
            (result["sqlite_schema_version"], result["postgresql_schema_version"]),
            (19, 24),
        )
        self.assertEqual(result["postgresql_acl"], list(verifier.EXPECTED_ACL))
        self.assertEqual(result["table_count"], 4)
        self.assertEqual(result["route_count"], 3)
        self.assertEqual(result["required_role"], "portfolio_admin")
        self.assertTrue(result["recommendations_implemented"])
        self.assertFalse(result["automatic_application"])
        self.assertFalse(result["live_external_data_authorized"])
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
            verifier.RecommendationRuntimePlanError,
            "recommendation_runtime_plan_invalid",
        ):
            verifier.verify(self.root)

    def test_release_or_activation_overclaim_is_rejected_when_repinned(self):
        for gate in (
            "exact_main_ci_verified",
            "automatic_application_authorized",
            "final_candidate_accepted",
            "released",
        ):
            with self.subTest(gate=gate):
                plan = self.plan()
                plan["gates"][gate] = True
                self.write_plan(plan)
                with mock.patch.object(
                    verifier, "PLAN_SHA256", verifier.canonical_digest(plan)
                ):
                    with self.assertRaisesRegex(
                        verifier.RecommendationRuntimePlanError,
                        "recommendation_runtime_gate_overclaim",
                    ):
                        verifier.verify(self.root)
                self.write_plan(self.plan() | {"gates": verifier.EXPECTED_GATES})

    def test_changed_or_missing_runtime_source_is_rejected(self):
        relative = "hormuz/recommendation_repository.py"
        path = self.root / relative
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(
            verifier.RecommendationRuntimePlanError,
            "recommendation_runtime_source_changed",
        ):
            verifier.verify(self.root)
        path.unlink()
        with self.assertRaisesRegex(
            verifier.RecommendationRuntimePlanError,
            "recommendation_runtime_source_kit_incomplete",
        ):
            verifier.verify(self.root)

    def test_schema_or_acl_boundary_change_is_rejected(self):
        for sqlite_version, postgres_version, acl in (
            (18, 24, verifier.EXPECTED_ACL),
            (19, 23, verifier.EXPECTED_ACL),
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
                    verifier.RecommendationRuntimePlanError,
                    "recommendation_runtime_schema_boundary_changed",
                ):
                    verifier.verify(self.root)

    def test_migration_cannot_gain_mutation_privileges(self):
        relative = "hormuz/migrations/postgresql/0024_policy_recommendations.sql"
        path = self.root / relative
        path.write_text(
            path.read_text(encoding="utf-8")
            + "\nGRANT UPDATE ON {schema}.portfolio_policy_recommendation_cursors TO {runtime_role};\n",
            encoding="utf-8",
        )
        plan = self.plan()
        with self.repin(plan, relative):
            with self.assertRaisesRegex(
                verifier.RecommendationRuntimePlanError,
                "recommendation_runtime_migration_invalid",
            ):
                verifier.verify(self.root)

    def test_automatic_application_wire_cannot_be_repinned(self):
        paths = (
            "docs/portfolio-intelligence-wire-v1.json",
            "hormuz/portfolio-intelligence-wire-v1.json",
        )
        for relative in paths:
            path = self.root / relative
            value = json.loads(path.read_text(encoding="utf-8"))
            value["$defs"]["hormuz.policy-recommendation"]["properties"][
                "automatic_application"
            ]["const"] = True
            path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        plan = self.plan()
        with self.repin(
            plan, "hormuz/portfolio-intelligence-wire-v1.json"
        ), mock.patch.object(
            verifier.role_verifier, "_validate_successor_predecessor"
        ):
            with self.assertRaisesRegex(
                verifier.RecommendationRuntimePlanError,
                "recommendation_runtime_wire_invalid",
            ):
                verifier.verify(self.root)

    def test_public_generation_route_is_rejected(self):
        original = verifier.route

        def route_with_generation(method, path):
            if method == "POST" and path == verifier.RECOMMENDATIONS:
                return "generate_recommendation", None
            return original(method, path)

        with mock.patch.object(verifier, "route", side_effect=route_with_generation):
            with self.assertRaisesRegex(
                verifier.RecommendationRuntimePlanError,
                "recommendation_runtime_route_invalid",
            ):
                verifier.verify(self.root)

    def test_frozen_decision_fixture_cannot_be_repinned(self):
        relative = verifier.DECISION_FIXTURE_PATH
        path = self.root / relative
        fixture = json.loads(path.read_text(encoding="utf-8"))
        fixture["suppression_cases"]["guardrails"].remove("quality")
        path.write_text(json.dumps(fixture, indent=2) + "\n", encoding="utf-8")
        plan = self.plan()
        with self.repin(plan, relative):
            with self.assertRaisesRegex(
                verifier.RecommendationRuntimePlanError,
                "recommendation_runtime_decision_fixture_invalid",
            ):
                verifier.verify(self.root)

    def test_role_view_predecessor_is_immutable(self):
        path = self.root / verifier.PREDECESSOR_PATH
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(
            verifier.RecommendationRuntimePlanError,
            "recommendation_runtime_predecessor_changed",
        ):
            verifier.verify(self.root)

    def test_distribution_requires_complete_runtime_kit(self):
        for required, label in (
            (
                packaging.REQUIRED_RECOMMENDATION_RUNTIME_WHEEL_PATHS,
                "Policy recommendation runtime wheel",
            ),
            (
                packaging.REQUIRED_RECOMMENDATION_RUNTIME_SDIST_PATHS,
                "Policy recommendation runtime source kit",
            ),
        ):
            archive_members = ["hormuz-1.2.0/" + path for path in required]
            packaging._assert_required_archive_paths(
                Path("test.whl"),
                mock.Mock(return_value=archive_members),
                required,
                label,
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
