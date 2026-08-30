from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tools.verify_registry_transition_plan import (
    RegistryTransitionError,
    rollback_disposition,
    validate_registry_transition_plan,
    verify_registry_transition_plan,
    verify_released_baseline,
)


ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = ROOT / "docs" / "registry-transition-plan-v2.json"


class RegistryTransitionPlanTests(unittest.TestCase):
    def plan(self):
        return json.loads(PLAN_PATH.read_text(encoding="utf-8"))

    def test_plan_is_registry_implementation_not_candidate_acceptance(self) -> None:
        result = verify_registry_transition_plan(ROOT)
        self.assertEqual(result["status"], "registry_implementation_plan_verified")
        self.assertEqual(result["feature_issue"], 215)
        self.assertEqual(result["registry_route_count"], 6)
        self.assertTrue(result["registry_implemented"])
        self.assertFalse(result["final_candidate_accepted"])

    def test_released_baseline_identity_cannot_be_replaced_by_current_main(self) -> None:
        for field, value in (("source_commit", "a" * 40), ("archive_sha256", "b" * 64)):
            with self.subTest(field=field):
                plan = self.plan()
                plan["baseline"][field] = value
                with self.assertRaisesRegex(RegistryTransitionError, "baseline_binding_changed"):
                    validate_registry_transition_plan(plan)

    def test_preflight_cannot_claim_registry_or_final_candidate_success(self) -> None:
        for field in ("registry_implemented", "final_candidate_accepted"):
            with self.subTest(field=field):
                plan = json.loads((ROOT / "docs/registry-transition-plan-v1.json").read_text(encoding="utf-8"))
                plan[field] = True
                with self.assertRaisesRegex(RegistryTransitionError, "preflight_scope_changed"):
                    validate_registry_transition_plan(plan)

    def test_implementation_plan_cannot_claim_final_candidate_acceptance(self) -> None:
        plan = self.plan()
        plan["final_candidate_accepted"] = True
        with self.assertRaisesRegex(RegistryTransitionError, "preflight_scope_changed"):
            validate_registry_transition_plan(plan)

    def test_release_and_schema_versions_remain_distinct_and_explicit(self) -> None:
        for backend, current, target in (("sqlite", 4, 5), ("postgresql", 8, 9)):
            with self.subTest(backend=backend):
                plan = self.plan()
                self.assertEqual(plan["transitions"][backend]["from"], current)
                self.assertEqual(plan["transitions"][backend]["to"], target)
                plan["transitions"][backend]["to"] = current
                with self.assertRaisesRegex(RegistryTransitionError, "schema_transition_changed"):
                    validate_registry_transition_plan(plan)

    def test_every_frozen_test_case_is_required_once(self) -> None:
        for mutation in ("remove", "duplicate", "rename"):
            with self.subTest(mutation=mutation):
                plan = self.plan()
                cases = plan["required_cases"]
                if mutation == "remove":
                    cases.pop()
                elif mutation == "duplicate":
                    cases.append(cases[0])
                else:
                    cases[0] = "unreviewed_case"
                with self.assertRaisesRegex(RegistryTransitionError, "transition_cases_changed"):
                    validate_registry_transition_plan(plan)

    def test_unsafe_rollback_or_legacy_manifest_correction_is_rejected(self) -> None:
        for section, field, value in (
            ("rollback", "after_writes", "restore_old_backup"),
            ("rollback", "in_place_downgrade", True),
            ("compatibility", "legacy_manifest", "change_release_line_in_place"),
            ("compatibility", "existing_v1_behavior", "change_auth"),
            ("compatibility", "provider_replay", "automatic_retry"),
        ):
            with self.subTest(field=field):
                plan = self.plan()
                plan[section][field] = value
                with self.assertRaisesRegex(RegistryTransitionError, "transition_policy_changed"):
                    validate_registry_transition_plan(plan)

    def test_unknown_fields_and_boolean_schema_versions_are_rejected(self) -> None:
        mutations = (
            lambda plan: plan.update(prompt="excluded content"),
            lambda plan: plan.update(schema_version=True),
            lambda plan: plan["baseline"].update(credential="excluded content"),
        )
        for mutate in mutations:
            plan = copy.deepcopy(self.plan())
            mutate(plan)
            with self.assertRaises(RegistryTransitionError) as raised:
                validate_registry_transition_plan(plan)
            self.assertNotIn("excluded content", str(raised.exception))

    def test_duplicate_plan_members_are_not_silently_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "docs").mkdir()
            (root / "docs/registry-transition-plan-v2.json").write_text(
                '{"schema_version":1,"schema_version":1}', encoding="utf-8",
            )
            with self.assertRaisesRegex(RegistryTransitionError, "transition_plan_duplicate_member"):
                verify_registry_transition_plan(root)

    def test_historical_preflight_cannot_be_substituted_for_implementation_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "docs").mkdir()
            (root / "docs/registry-transition-plan-v2.json").write_bytes(
                (ROOT / "docs/registry-transition-plan-v1.json").read_bytes()
            )
            with self.assertRaisesRegex(RegistryTransitionError, "implementation_plan_required"):
                verify_registry_transition_plan(root)

    def test_invalid_or_substituted_released_archive_cannot_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "hormuz-1.0.0.tar.gz"
            manifest = root / "manifest.json"
            archive.write_bytes(b"not an archive")
            manifest.write_bytes(b"{}")
            with self.assertRaisesRegex(RegistryTransitionError, "released_baseline_archive_invalid"):
                verify_released_baseline(archive, manifest)
            with mock.patch("tools.v1_candidate.inspect_archive", return_value={"digest": "sha256:" + "a" * 64}):
                with self.assertRaisesRegex(RegistryTransitionError, "released_baseline_digest_mismatch"):
                    verify_released_baseline(archive, manifest)

    def test_source_archive_requires_every_preflight_asset(self) -> None:
        from tools import verify_core_wheel as packaging

        required = packaging.REQUIRED_REGISTRY_PREFLIGHT_SDIST_PATHS
        members = [f"hormuz-1.0.0/{path}" for path in required]
        with mock.patch.object(packaging, "_sdist_members", return_value=members):
            packaging._assert_registry_preflight_sdist_boundary(Path("test.tar.gz"))
        for missing in members:
            with self.subTest(missing=missing):
                with mock.patch.object(packaging, "_sdist_members", return_value=[item for item in members if item != missing]):
                    with self.assertRaisesRegex(RuntimeError, "Registry preflight incomplete"):
                        packaging._assert_registry_preflight_sdist_boundary(Path("test.tar.gz"))

    def test_only_verified_quiesced_no_write_checkpoints_allow_pair_restore(self) -> None:
        checkpoint = {
            "quiesced": True,
            "backup_verified": True,
            "candidate_snapshot_retained": True,
            "post_checkpoint_writes": 0,
        }
        self.assertEqual(rollback_disposition(checkpoint), "restore_verified_pair_before_resuming_writes")
        for field in ("quiesced", "backup_verified", "candidate_snapshot_retained"):
            with self.subTest(field=field):
                self.assertEqual(rollback_disposition({**checkpoint, field: False}), "refuse_restore")
        for count in (1, 17, None):
            self.assertEqual(
                rollback_disposition({**checkpoint, "post_checkpoint_writes": count}),
                "preserve_candidate_and_recover_forward",
            )
        for count in (True, -1, "0"):
            with self.assertRaisesRegex(RegistryTransitionError, "rollback_checkpoint_invalid"):
                rollback_disposition({**checkpoint, "post_checkpoint_writes": count})


if __name__ == "__main__":
    unittest.main()
