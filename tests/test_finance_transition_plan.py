"""The #8 checkpoint must preserve old contracts and cannot claim live finance."""

from __future__ import annotations

import contextlib
import copy
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest import mock

from tools.verify_finance_transition_plan import (
    ROOT, FinanceTransitionError, main, validate_finance_plan, validate_finance_sources,
    verify_finance_transition_plan, verify_outcome_archive,
)


class FinanceTransitionPlanTests(unittest.TestCase):
    def plan(self):
        return json.loads((ROOT / "docs/finance-transition-plan-v1.json").read_bytes())

    def sources(self):
        return json.loads((ROOT / "docs/finance-source-contract-v1.json").read_bytes())

    def test_checkpoint_is_not_implementation_live_access_or_release(self):
        result = verify_finance_transition_plan()
        self.assertEqual(result["feature_issue"], 8)
        self.assertEqual(result["status"], "finance_preflight_plan_verified")
        self.assertEqual(result["new_http_routes"], 0)
        self.assertFalse(result["finance_implemented"])
        self.assertFalse(result["live_finance_verified"])
        self.assertFalse(result["final_candidate_accepted"])
        self.assertEqual(result["provider_count"], 2)

    def test_each_plan_section_is_frozen_not_only_version_label(self):
        for key in self.plan():
            with self.subTest(key=key):
                changed = copy.deepcopy(self.plan())
                changed[key] = "SYNTHETIC_EXCLUDED"
                with self.assertRaisesRegex(FinanceTransitionError, "finance_preflight_contract_changed"):
                    validate_finance_plan(changed)

    def test_source_contract_cannot_silently_change_permissions_units_or_scope(self):
        for provider in ("openai", "anthropic"):
            for key in self.sources()["providers"][provider]:
                with self.subTest(provider=provider, key=key):
                    changed = copy.deepcopy(self.sources())
                    changed["providers"][provider][key] = "SYNTHETIC_EXCLUDED"
                    with self.assertRaisesRegex(FinanceTransitionError, "finance_source_contract_changed"):
                        validate_finance_sources(changed)

    def test_unknown_fields_and_wrong_primitive_versions_fail(self):
        for validate, value in ((validate_finance_plan, self.plan()), (validate_finance_sources, self.sources())):
            for changed in ({**value, "unexpected": True}, {**value, "schema_version": True}, {**value, "schema_version": 2}, []):
                with self.assertRaises(FinanceTransitionError):
                    validate(changed)

    def test_non_json_non_finite_and_invalid_unicode_are_fixed_errors(self):
        for validate in (validate_finance_plan, validate_finance_sources):
            for value in (object(), float("nan"), float("inf"), {"x": "\ud800"}):
                with self.assertRaises(FinanceTransitionError) as caught:
                    validate(value)
                self.assertNotIn("SYNTHETIC_EXCLUDED", str(caught.exception))

    def test_unreadable_duplicate_oversized_and_deep_json_fail_closed(self):
        for filename in ("finance-transition-plan-v1.json", "finance-source-contract-v1.json"):
            for raw in (b"{", b'{"schema_version":1,"schema_version":1}', b" " * 65537, b"[" * 5000 + b"]" * 5000):
                with self.subTest(filename=filename, length=len(raw)), tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    (root / "docs").mkdir()
                    (root / "docs/finance-transition-plan-v1.json").write_text(json.dumps(self.plan()))
                    (root / "docs/finance-source-contract-v1.json").write_text(json.dumps(self.sources()))
                    (root / "docs" / filename).write_bytes(raw)
                    with self.assertRaises(FinanceTransitionError):
                        verify_finance_transition_plan(root)

    def test_whitespace_and_order_are_not_contract_changes(self):
        validate_finance_plan(json.loads(json.dumps(self.plan(), sort_keys=True)))
        validate_finance_sources(json.loads(json.dumps(self.sources(), sort_keys=True)))

    def test_all_earlier_implemented_transition_plans_remain_required(self):
        from tools.verify_outcome_transition_plan import OutcomeTransitionError

        with mock.patch("tools.verify_finance_transition_plan.verify_outcome_implementation_plan", side_effect=OutcomeTransitionError("synthetic")):
            with self.assertRaisesRegex(FinanceTransitionError, "finance_baseline_contract_invalid"):
                verify_finance_transition_plan()

    def test_source_dimensions_and_accounting_limits_are_explicit(self):
        sources = self.sources()
        self.assertEqual(sources["providers"]["openai"]["cost_amount_unit"], "major_currency_decimal")
        self.assertEqual(sources["providers"]["anthropic"]["cost_amount_unit"], "USD_fractional_cents_divide_by_100_exactly")
        self.assertIn("api_key_id", sources["providers"]["openai"]["cost_group_by"])
        self.assertIn("priority_tier_costs", sources["providers"]["anthropic"]["unsupported_finance_sources"])
        self.assertFalse(sources["provider_reports_are_final_invoices"])
        self.assertFalse(self.plan()["accounting"]["automatic_person_or_team_allocation"])
        self.assertFalse(self.plan()["accounting"]["variance_proves_gateway_bypass"])

    def test_predecessor_fixture_and_plan_bind_same_archive(self):
        from tests._finance_predecessor_fixture import ARCHIVE_SHA256, SOURCE_COMMIT

        self.assertEqual(self.plan()["predecessor"]["archive_sha256"], ARCHIVE_SHA256)
        self.assertEqual(self.plan()["predecessor"]["source_commit"], SOURCE_COMMIT)

    def test_changed_missing_and_extra_installed_predecessor_files_refuse(self):
        from tests._finance_predecessor_fixture import verify_installed_runtime

        source = b"SYNTHETIC_SOURCE_ONLY\n"
        for mutation in ("none", "changed", "missing", "extra"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                if mutation != "missing":
                    (root / "example.py").write_bytes(source if mutation != "changed" else b"SYNTHETIC_CHANGED\n")
                if mutation == "extra":
                    (root / "unexpected.py").write_bytes(source)
                stream = io.BytesIO()
                with tarfile.open(fileobj=stream, mode="w") as archive:
                    member = tarfile.TarInfo("hormuz-outcome-baseline/hormuz/example.py")
                    member.size = len(source)
                    archive.addfile(member, io.BytesIO(source))
                stream.seek(0)
                with tarfile.open(fileobj=stream, mode="r:") as archive:
                    if mutation == "none":
                        self.assertEqual(verify_installed_runtime(archive, root), 1)
                    else:
                        with self.assertRaisesRegex(RuntimeError, "outcome_predecessor_runtime_mismatch"):
                            verify_installed_runtime(archive, root)

    def test_missing_wrong_and_oversized_predecessor_archive_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "predecessor.tar"
            for payload in (None, b"SYNTHETIC_EXCLUDED", b"0" * (32 * 1024 * 1024 + 1)):
                if payload is not None:
                    archive.write_bytes(payload)
                with self.assertRaisesRegex(FinanceTransitionError, "finance_outcome_archive_invalid"):
                    verify_outcome_archive(archive)

    def test_cli_requires_released_baseline_pair(self):
        for option in ("--baseline-archive", "--baseline-manifest"):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main([option, "/unused"]), 1)
            self.assertEqual(output.getvalue().strip(), "finance_baseline_pair_required")

    def test_cli_bad_predecessor_cannot_succeed(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["--outcome-archive", "/unused/missing.tar"]), 1)
        self.assertEqual(output.getvalue().strip(), "finance_outcome_archive_invalid")

    def test_source_archive_requires_every_finance_checkpoint_asset(self):
        from tools import verify_core_wheel as packaging

        members = ["hormuz-1.0.0/" + path for path in packaging.REQUIRED_FINANCE_PREFLIGHT_SDIST_PATHS]
        with mock.patch.object(packaging, "_sdist_members", return_value=members):
            packaging._assert_finance_preflight_sdist_boundary(Path("test.tar.gz"))
        for missing in members:
            with self.subTest(missing=missing), mock.patch.object(packaging, "_sdist_members", return_value=[item for item in members if item != missing]):
                with self.assertRaisesRegex(RuntimeError, "Finance preflight incomplete"):
                    packaging._assert_finance_preflight_sdist_boundary(Path("test.tar.gz"))


if __name__ == "__main__":
    unittest.main()
