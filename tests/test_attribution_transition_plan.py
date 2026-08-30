from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tools.verify_attribution_transition_plan import (
    AttributionTransitionError, EXPECTED, validate_attribution_transition_plan,
    verify_attribution_transition_plan,
)
from tools.verify_registry_transition_plan import RegistryTransitionError


class AttributionTransitionPlanTests(unittest.TestCase):
    def test_checkpoint_is_not_feature_or_candidate_acceptance(self):
        result = verify_attribution_transition_plan()
        self.assertEqual(result["feature_issue"], 216)
        self.assertEqual(result["attribution_route_count"], 2)
        self.assertFalse(result["attribution_implemented"])
        self.assertFalse(result["final_candidate_accepted"])

    def test_preflight_cannot_claim_feature_or_release_success(self):
        for field in ("attribution_implemented", "final_candidate_accepted"):
            value = copy.deepcopy(EXPECTED)
            value[field] = True
            with self.assertRaises(AttributionTransitionError):
                validate_attribution_transition_plan(value)

    def test_registry_baseline_and_new_migration_numbers_are_fixed(self):
        mutations = (
            lambda x: x.update(registry_source_commit="0" * 40),
            lambda x: x["transitions"]["sqlite"].update(to=5),
            lambda x: x["transitions"]["postgresql"].update(to=9),
        )
        for change in mutations:
            value = copy.deepcopy(EXPECTED)
            change(value)
            with self.assertRaises(AttributionTransitionError):
                validate_attribution_transition_plan(value)

    def test_single_primary_authorization_and_no_replay_cannot_be_weakened(self):
        for field, replacement in (("active_primary_use_cases_per_attempt", 2),
                                   ("authorization_time", "after_egress"),
                                   ("actual_model_source", "requested_alias"),
                                   ("provider_replay", "automatic")):
            value = copy.deepcopy(EXPECTED)
            value["compatibility"][field] = replacement
            with self.assertRaises(AttributionTransitionError):
                validate_attribution_transition_plan(value)

    def test_header_grammar_native_body_and_exclusion_are_frozen(self):
        for field, replacement in (("maximum_request_bytes", 1000000),
                                   ("provider_forwarding", "permitted"),
                                   ("provider_body", "inject_attribution"),
                                   ("reflected_input", "echo_header")):
            value = copy.deepcopy(EXPECTED)
            value["admission_headers"][field] = replacement
            with self.assertRaises(AttributionTransitionError):
                validate_attribution_transition_plan(value)

    def test_rollback_never_discards_post_checkpoint_writes(self):
        for field, replacement in (("in_place_downgrade", True),
                                   ("after_writes", "restore_old_backup"),
                                   ("unknown_write_count", "restore_old_backup"),
                                   ("retain_candidate_snapshot", False)):
            value = copy.deepcopy(EXPECTED)
            value["rollback"][field] = replacement
            with self.assertRaises(AttributionTransitionError):
                validate_attribution_transition_plan(value)

    def test_boolean_version_unknown_fields_and_missing_cases_fail_closed(self):
        for change in (lambda x: x.update(schema_version=True),
                       lambda x: x.update(prompt="SYNTHETIC_EXCLUDED"),
                       lambda x: x["required_implementation_cases"].pop()):
            value = copy.deepcopy(EXPECTED)
            change(value)
            with self.assertRaises(AttributionTransitionError) as caught:
                validate_attribution_transition_plan(value)
            self.assertNotIn("SYNTHETIC_EXCLUDED", str(caught.exception))

    def test_duplicate_json_is_rejected_before_baseline_access(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "docs").mkdir()
            (root / "docs/attribution-transition-plan-v1.json").write_text('{"schema_version":1,"schema_version":1}')
            with self.assertRaisesRegex(AttributionTransitionError, "duplicate_member"):
                verify_attribution_transition_plan(root)

    def test_oversized_plan_is_rejected_before_parse(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "docs").mkdir()
            (root / "docs/attribution-transition-plan-v1.json").write_bytes(b" " * 32769)
            with self.assertRaisesRegex(AttributionTransitionError, "too_large"):
                verify_attribution_transition_plan(root)

    def test_legacy_and_registry_contract_verification_is_mandatory(self):
        with mock.patch("tools.verify_attribution_transition_plan.verify_registry_transition_plan", side_effect=RegistryTransitionError("synthetic")):
            with self.assertRaisesRegex(AttributionTransitionError, "baseline_contract_invalid"):
                verify_attribution_transition_plan()

    def test_source_archive_requires_every_attribution_preflight_asset(self):
        from tools import verify_core_wheel as packaging

        required = packaging.REQUIRED_ATTRIBUTION_PREFLIGHT_SDIST_PATHS
        members = [f"hormuz-1.0.0/{path}" for path in required]
        with mock.patch.object(packaging, "_sdist_members", return_value=members):
            packaging._assert_attribution_preflight_sdist_boundary(Path("test.tar.gz"))
        for missing in members:
            with self.subTest(missing=missing):
                with mock.patch.object(packaging, "_sdist_members", return_value=[item for item in members if item != missing]):
                    with self.assertRaisesRegex(RuntimeError, "Attribution preflight incomplete"):
                        packaging._assert_attribution_preflight_sdist_boundary(Path("test.tar.gz"))
