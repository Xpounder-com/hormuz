from __future__ import annotations

import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import tarfile
import unittest
from unittest import mock

from tools.verify_attribution_transition_plan import AttributionTransitionError
from tools.verify_outcome_transition_plan import (
    ROOT, OutcomeTransitionError, main, validate_outcome_transition_plan,
    verify_attribution_archive, verify_outcome_transition_plan,
)


class OutcomeTransitionPlanTests(unittest.TestCase):
    def plan(self):
        return json.loads((ROOT / "docs/outcome-transition-plan-v1.json").read_bytes())

    def test_checkpoint_never_claims_feature_candidate_or_connector_activation(self):
        result = verify_outcome_transition_plan()
        self.assertEqual(result["feature_issue"], 218)
        self.assertEqual(result["outcome_route_count"], 1)
        self.assertEqual(result["connector_routes_activated"], 0)
        self.assertFalse(result["outcome_implemented"])
        self.assertFalse(result["final_candidate_accepted"])
        self.assertEqual(result["status"], "outcome_preflight_plan_verified")

    def test_every_frozen_leaf_cannot_change(self):
        def leaves(value, path=()):
            if isinstance(value, dict):
                for key, child in value.items():
                    yield from leaves(child, path + (key,))
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    yield from leaves(child, path + (index,))
            else:
                yield path, value

        original = self.plan()
        for path, old in leaves(original):
            with self.subTest(path=path):
                changed = copy.deepcopy(original)
                parent = changed
                for key in path[:-1]:
                    parent = parent[key]
                parent[path[-1]] = not old if type(old) is bool else old + 1 if type(old) is int else str(old) + "_changed"
                with self.assertRaisesRegex(OutcomeTransitionError, "contract_changed"):
                    validate_outcome_transition_plan(changed)

    def test_numeric_aliases_unknown_fields_and_missing_cases_fail_closed(self):
        for mutate in (lambda x: x.update(schema_version=True), lambda x: x.update(schema_version=1.0),
                       lambda x: x.update(prompt="SYNTHETIC_EXCLUDED_CONTENT"),
                       lambda x: x["required_preflight_cases"].pop(),
                       lambda x: x["required_implementation_cases"].pop(),
                       lambda x: x["feature_surfaces"]["connector_routes_activated"].append("unapproved")):
            changed = self.plan()
            mutate(changed)
            with self.assertRaises(OutcomeTransitionError) as caught:
                validate_outcome_transition_plan(changed)
            self.assertNotIn("SYNTHETIC_EXCLUDED_CONTENT", str(caught.exception))

    def test_duplicate_members_fail_before_baseline_access(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "docs").mkdir()
            (root / "docs/outcome-transition-plan-v1.json").write_text('{"schema_version":1,"schema_version":1}')
            with mock.patch("tools.verify_outcome_transition_plan.verify_attribution_transition_plan", side_effect=AssertionError("unexpected baseline access")):
                with self.assertRaisesRegex(OutcomeTransitionError, "duplicate_member"):
                    verify_outcome_transition_plan(root)

    def test_oversized_plan_fails_before_parse(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "docs").mkdir()
            (root / "docs/outcome-transition-plan-v1.json").write_bytes(b" " * 32769)
            with self.assertRaisesRegex(OutcomeTransitionError, "too_large"):
                verify_outcome_transition_plan(root)

    def test_invalid_json_unicode_and_missing_plan_have_safe_errors(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "docs").mkdir()
            path = root / "docs/outcome-transition-plan-v1.json"
            for payload in (None, b"{", b"\xff"):
                if payload is not None:
                    path.write_bytes(payload)
                with self.assertRaisesRegex(OutcomeTransitionError, "unreadable"):
                    verify_outcome_transition_plan(root)

    def test_deep_valid_json_is_not_an_accepted_plan(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "docs").mkdir()
            (root / "docs/outcome-transition-plan-v1.json").write_bytes(b"[" * 5000 + b"]" * 5000)
            with self.assertRaises(OutcomeTransitionError) as caught:
                verify_outcome_transition_plan(root)
            # Python versions may reject nesting during decoding or encoding.
            # A parser that accepts this valid JSON must still reject its shape.
            self.assertIn(str(caught.exception), {"outcome_plan_unreadable", "outcome_plan_invalid", "outcome_preflight_contract_changed"})

    def test_canonical_key_order_and_whitespace_do_not_change_contract(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "docs").mkdir()
            (root / "docs/outcome-transition-plan-v1.json").write_text(json.dumps(self.plan(), sort_keys=True))
            with mock.patch("tools.verify_outcome_transition_plan.verify_attribution_transition_plan", return_value={"baseline_archive_sha256": "verified"}):
                self.assertEqual(verify_outcome_transition_plan(root)["baseline_archive_sha256"], "verified")

    def test_legacy_registry_and_attribution_verification_are_required(self):
        with mock.patch("tools.verify_outcome_transition_plan.verify_attribution_transition_plan", side_effect=AttributionTransitionError("synthetic")):
            with self.assertRaisesRegex(OutcomeTransitionError, "baseline_contract_invalid"):
                verify_outcome_transition_plan()

    def test_non_json_and_non_finite_values_fail_safely(self):
        for value in (object(), float("nan"), float("inf"), {"x": "\ud800"}):
            with self.assertRaises(OutcomeTransitionError):
                validate_outcome_transition_plan(value)

    def test_predecessor_fixture_and_plan_bind_same_exact_archive(self):
        from tests._outcome_predecessor_fixture import ARCHIVE_SHA256, SOURCE_COMMIT

        plan = self.plan()
        self.assertEqual(plan["predecessor"]["archive_sha256"], ARCHIVE_SHA256)
        self.assertEqual(plan["predecessor"]["source_commit"], SOURCE_COMMIT)

    def test_installed_predecessor_runtime_bytes_and_inventory_cannot_drift(self):
        from tests._outcome_predecessor_fixture import verify_installed_runtime

        payload = b"SYNTHETIC_SOURCE_ONLY\n"
        for mutation in ("none", "changed", "missing", "extra"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                if mutation != "missing":
                    (root / "example.py").write_bytes(payload if mutation != "changed" else b"SYNTHETIC_CHANGED\n")
                if mutation == "extra":
                    (root / "unexpected.py").write_bytes(payload)
                stream = io.BytesIO()
                with tarfile.open(fileobj=stream, mode="w") as archive:
                    member = tarfile.TarInfo("hormuz-attribution-baseline/hormuz/example.py")
                    member.size = len(payload)
                    archive.addfile(member, io.BytesIO(payload))
                stream.seek(0)
                with tarfile.open(fileobj=stream, mode="r:") as archive:
                    if mutation == "none":
                        self.assertEqual(verify_installed_runtime(archive, root), 1)
                    else:
                        with self.assertRaisesRegex(RuntimeError, "attribution_predecessor_runtime_mismatch"):
                            verify_installed_runtime(archive, root)

    def test_missing_wrong_or_oversized_predecessor_archive_fails_safely(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "predecessor.tar"
            for payload in (None, b"SYNTHETIC_EXCLUDED_CONTENT", b"0" * (32 * 1024 * 1024 + 1)):
                if payload is not None:
                    archive.write_bytes(payload)
                with self.assertRaisesRegex(OutcomeTransitionError, "outcome_attribution_archive_invalid"):
                    verify_attribution_archive(archive)

    def test_cli_requires_released_baseline_pair(self):
        for option in ("--baseline-archive", "--baseline-manifest"):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main([option, "/unused"]), 1)
            self.assertEqual(output.getvalue().strip(), "outcome_baseline_pair_required")

    def test_cli_bad_predecessor_never_reports_success(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["--attribution-archive", "/unused/missing.tar"]), 1)
        self.assertEqual(output.getvalue().strip(), "outcome_attribution_archive_invalid")

    def test_source_archive_requires_every_outcome_preflight_asset(self):
        from tools import verify_core_wheel as packaging

        required = packaging.REQUIRED_OUTCOME_PREFLIGHT_SDIST_PATHS
        members = [f"hormuz-1.0.0/{path}" for path in required]
        with mock.patch.object(packaging, "_sdist_members", return_value=members):
            packaging._assert_outcome_preflight_sdist_boundary(Path("test.tar.gz"))
        for missing in members:
            with self.subTest(missing=missing):
                with mock.patch.object(packaging, "_sdist_members", return_value=[item for item in members if item != missing]):
                    with self.assertRaisesRegex(RuntimeError, "Outcome preflight incomplete"):
                        packaging._assert_outcome_preflight_sdist_boundary(Path("test.tar.gz"))
