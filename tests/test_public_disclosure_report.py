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

    def test_repository_report_is_strict_content_free_and_explicitly_blocked(self) -> None:
        disclosure.validate_report(self.report)

        self.assertEqual(self.report["schema_id"], "hormuz.public-disclosure-report")
        self.assertEqual(self.report["schema_version"], 1)
        self.assertEqual(self.report["verdict"], "decision_required")
        self.assertFalse(self.report["publication"]["visibility_changed"])
        self.assertFalse(self.report["publication"]["raw_audit_material_committed"])
        self.assertEqual(len(self.report["findings"]), 14)
        self.assertEqual(len(self.report["blockers"]), 5)

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
        premature = copy.deepcopy(self.report)
        premature["blockers"] = []
        premature["verdict"] = "ready_for_public_transition"
        with self.assertRaisesRegex(disclosure.DisclosureReportError, "blocker_resolution"):
            disclosure.validate_report(premature)

        inconsistent = copy.deepcopy(self.report)
        inconsistent["verdict"] = "ready_for_public_transition"
        with self.assertRaisesRegex(disclosure.DisclosureReportError, "open_blockers"):
            disclosure.validate_report(inconsistent)


if __name__ == "__main__":
    unittest.main()
