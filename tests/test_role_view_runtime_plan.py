"""The #223 manifest fixes role-scoped views and preserves open release gates."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from tools import verify_core_wheel as packaging
from tools import verify_recommendation_runtime as successor_verifier
from tools import verify_role_view_runtime as verifier


class RoleViewRuntimePlanTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        plan = json.loads((verifier.ROOT / verifier.PLAN_PATH).read_text())
        successor_plan = json.loads(
            (successor_verifier.ROOT / successor_verifier.PLAN_PATH).read_text()
        )
        paths = (
            set(verifier.REQUIRED_FILES)
            | set(plan["source_sha256"])
            | set(successor_verifier.REQUIRED_FILES)
            | set(successor_plan["source_sha256"])
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
        return mock.patch.object(
            verifier, "PLAN_SHA256", verifier.canonical_digest(plan)
        )

    def test_fixed_runtime_preserves_external_and_release_gates(self):
        result = verifier.verify(self.root)
        self.assertEqual(result["status"], "role_view_runtime_successor_verified")
        self.assertEqual(
            (result["sqlite_schema_version"], result["postgresql_schema_version"]),
            (19, 24),
        )
        self.assertEqual(
            result["postgresql_acl"], list(successor_verifier.EXPECTED_ACL)
        )
        self.assertEqual(result["table_count"], 2)
        self.assertEqual(result["route_count"], 4)
        self.assertEqual(
            result["roles"], ["finance_viewer", "platform_viewer", "team_lead"]
        )
        self.assertTrue(result["role_scoped_decision_views_implemented"])
        self.assertFalse(result["live_connectors_authorized"])
        self.assertTrue(result["recommendations_implemented"])
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
            verifier.RoleViewRuntimePlanError,
            "role_view_runtime_plan_invalid",
        ):
            verifier.verify(self.root)

    def test_gate_overclaim_is_rejected_even_when_repinned(self):
        plan = self.plan()
        for relative in plan["source_sha256"]:
            plan["source_sha256"][relative] = hashlib.sha256(
                (self.root / relative).read_bytes()
            ).hexdigest()
        plan["gates"]["exact_main_ci_verified"] = True
        self.write_plan(plan)
        with mock.patch.object(
            verifier, "PLAN_SHA256", verifier.canonical_digest(plan)
        ), mock.patch.object(
            verifier, "SQLITE_SCHEMA_VERSION", 18
        ), mock.patch.object(
            verifier, "POSTGRES_SCHEMA_VERSION", 23
        ), mock.patch.dict(
            verifier._POSTGRES_EXPECTED_ACL_BOUNDARY_BY_VERSION,
            {23: verifier.EXPECTED_ACL},
        ):
            with self.assertRaisesRegex(
                verifier.RoleViewRuntimePlanError,
                "role_view_runtime_gate_overclaim",
            ):
                verifier.verify(self.root)

    def test_changed_or_missing_runtime_source_is_rejected(self):
        relative = "hormuz/role_view_repository.py"
        path = self.root / relative
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(
            verifier.RoleViewRuntimePlanError,
            "role_view_runtime_source_changed",
        ):
            verifier.verify(self.root)
        path.unlink()
        with self.assertRaisesRegex(
            verifier.RoleViewRuntimePlanError,
            "role_view_runtime_source_kit_incomplete",
        ):
            verifier.verify(self.root)

    def test_schema_or_acl_boundary_change_is_rejected(self):
        for sqlite_version, postgres_version, acl in (
            (18, 24, successor_verifier.EXPECTED_ACL),
            (19, 23, successor_verifier.EXPECTED_ACL),
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
                    verifier.RoleViewRuntimePlanError,
                    "role_view_runtime_schema_boundary_changed",
                ):
                    verifier.verify(self.root)

    def test_migration_cannot_gain_mutation_privileges(self):
        relative = "hormuz/migrations/postgresql/0023_portfolio_role_views.sql"
        path = self.root / relative
        path.write_text(
            path.read_text(encoding="utf-8")
            + "\nGRANT UPDATE ON {schema}.portfolio_role_view_cursors TO {runtime_role};\n",
            encoding="utf-8",
        )
        plan = self.plan()
        with self.repin(plan, relative):
            with self.assertRaisesRegex(
                verifier.RoleViewRuntimePlanError,
                "role_view_runtime_migration_invalid",
            ):
                verifier.verify(self.root)

    def test_forbidden_person_or_content_field_cannot_be_repinned(self):
        relative = "tests/fixtures/portfolio_role_views/wire-v1-examples.json"
        path = self.root / relative
        fixture = json.loads(path.read_text(encoding="utf-8"))
        fixture["cases"][0]["value"]["employee_id"] = "person"
        path.write_text(json.dumps(fixture, indent=2) + "\n", encoding="utf-8")
        plan = self.plan()
        with self.repin(plan, relative):
            with self.assertRaisesRegex(
                verifier.RoleViewRuntimePlanError,
                "role_view_runtime_fixture_invalid",
            ):
                verifier.verify(self.root)

    def test_packaged_wire_must_equal_documented_wire(self):
        relative = "hormuz/portfolio-role-views-wire-v1.json"
        path = self.root / relative
        path.write_bytes(path.read_bytes() + b"\n")
        plan = self.plan()
        with self.repin(plan, relative):
            with self.assertRaisesRegex(
                verifier.RoleViewRuntimePlanError,
                "role_view_runtime_wire_invalid",
            ):
                verifier.verify(self.root)

    def test_budget_transport_cannot_return_to_planned_only_metadata(self):
        relatives = (
            "docs/work-budget-reports-wire-v2.json",
            "hormuz/work-budget-reports-wire-v2.json",
        )
        plan = self.plan()
        for relative in relatives:
            path = self.root / relative
            value = json.loads(path.read_text(encoding="utf-8"))
            value["x-hormuz-route-query-fields"] = {}
            value["x-hormuz-transport"] = {
                "response_maximum_bytes": 1048576,
                "runtime_enabled": False,
                "new_http_routes": [],
                "delivery": "separate_planned_cli_or_internal_records",
            }
            path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
            plan["source_sha256"][relative] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
        self.write_plan(plan)
        with mock.patch.object(
            verifier,
            "PLAN_SHA256",
            verifier.canonical_digest(plan),
        ):
            with self.assertRaisesRegex(
                verifier.RoleViewRuntimePlanError,
                "role_view_runtime_wire_invalid",
            ):
                verifier.verify(self.root)

    def test_resource_payload_pairing_cannot_be_removed(self):
        relatives = (
            "docs/portfolio-role-views-wire-v1.json",
            "hormuz/portfolio-role-views-wire-v1.json",
        )
        plan = self.plan()
        for relative in relatives:
            path = self.root / relative
            value = json.loads(path.read_text(encoding="utf-8"))
            del value["$defs"]["hormuz.portfolio-role-view-item"]["allOf"]
            path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
            plan["source_sha256"][relative] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
        self.write_plan(plan)
        with mock.patch.object(
            verifier,
            "PLAN_SHA256",
            verifier.canonical_digest(plan),
        ):
            with self.assertRaisesRegex(
                verifier.RoleViewRuntimePlanError,
                "role_view_runtime_wire_invalid",
            ):
                verifier.verify(self.root)

    def test_scorecard_runtime_predecessor_is_immutable(self):
        path = self.root / verifier.PREDECESSOR_PATH
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(
            verifier.RoleViewRuntimePlanError,
            "role_view_runtime_predecessor_changed",
        ):
            verifier.verify(self.root)

    def test_distribution_requires_complete_runtime_kit(self):
        for required, label in (
            (
                packaging.REQUIRED_ROLE_VIEW_RUNTIME_WHEEL_PATHS,
                "Portfolio role-view runtime wheel",
            ),
            (
                packaging.REQUIRED_ROLE_VIEW_RUNTIME_SDIST_PATHS,
                "Portfolio role-view runtime source kit",
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
