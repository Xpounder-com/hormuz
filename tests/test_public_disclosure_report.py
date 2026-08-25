from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from tools import verify_public_disclosure_report as disclosure


REPORT_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "evidence"
    / "public-disclosure-report-v1.json"
)


class PublicDisclosureReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = disclosure.load_report(REPORT_PATH)

    def test_repository_report_is_strict_content_free_and_publicly_verified(self) -> None:
        disclosure.validate_report(self.report)

        self.assertEqual(self.report["schema_id"], "hormuz.public-disclosure-report")
        self.assertEqual(self.report["schema_version"], 1)
        self.assertEqual(self.report["verdict"], "public_transition_verified")
        self.assertTrue(self.report["publication"]["visibility_changed"])
        self.assertEqual(self.report["publication"]["repository_visibility"], "public")
        self.assertFalse(self.report["publication"]["raw_audit_material_committed"])
        self.assertEqual(self.report["publication"]["owner_authorization"], "approved")
        self.assertEqual(self.report["publication"]["actions_cache_disposition"], "deleted")
        self.assertEqual(len(self.report["findings"]), 16)
        self.assertEqual(len(self.report["blockers"]), 0)

    def test_duplicate_keys_and_sensitive_values_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            duplicate = Path(directory) / "duplicate.json"
            duplicate.write_text('{"schema_id":"first","schema_id":"second"}\n')
            with self.assertRaisesRegex(disclosure.DisclosureReportError, "duplicate_key"):
                disclosure.load_report(duplicate)

        unsafe = copy.deepcopy(self.report)
        unsafe["scanner"]["git_version"] = "owner@example.com"
        with self.assertRaisesRegex(disclosure.DisclosureReportError, "forbidden_content"):
            disclosure.validate_report(unsafe)

    def test_incomplete_scans_or_unknown_finding_codes_fail_closed(self) -> None:
        missing_artifact = copy.deepcopy(self.report)
        missing_artifact["external_surfaces"]["actions"]["artifacts_downloaded"] -= 1
        with self.assertRaisesRegex(disclosure.DisclosureReportError, "not_downloaded"):
            disclosure.validate_report(missing_artifact)

        unknown = copy.deepcopy(self.report)
        unknown["findings"][0]["id"] = "unreviewed_category"
        with self.assertRaisesRegex(disclosure.DisclosureReportError, "finding_id"):
            disclosure.validate_report(unknown)

    def test_ready_verdict_requires_no_blockers_and_owner_authorization(self) -> None:
        unauthorized = copy.deepcopy(self.report)
        unauthorized["publication"]["owner_authorization"] = "pending"
        with self.assertRaisesRegex(disclosure.DisclosureReportError, "blocker_resolution"):
            disclosure.validate_report(unauthorized)

        incomplete = copy.deepcopy(self.report)
        incomplete["publication"]["final_candidate_delta_audit"] = "pending"
        incomplete["blockers"] = [{
            "id": "final_candidate_delta_audit",
            "status": "execution_required",
            "required_action": "rescan_final_candidate_and_new_github_surfaces",
        }]
        with self.assertRaisesRegex(disclosure.DisclosureReportError, "open_blockers"):
            disclosure.validate_report(incomplete)

    def test_visibility_and_verdict_lifecycle_are_consistent(self) -> None:
        ready = copy.deepcopy(self.report)
        ready["publication"]["repository_visibility"] = "private"
        ready["publication"]["visibility_changed"] = False
        ready["verdict"] = "ready_for_public_transition"
        disclosure.validate_report(ready)

        inconsistent = copy.deepcopy(self.report)
        inconsistent["publication"]["visibility_changed"] = False
        with self.assertRaisesRegex(disclosure.DisclosureReportError, "visibility_state"):
            disclosure.validate_report(inconsistent)

        premature = copy.deepcopy(self.report)
        premature["verdict"] = "ready_for_public_transition"
        with self.assertRaisesRegex(disclosure.DisclosureReportError, "ready_after_visibility"):
            disclosure.validate_report(premature)


if __name__ == "__main__":
    unittest.main()
