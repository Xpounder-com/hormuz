"""Offline Linear scenario candidates; no provider normalizer or source authentication."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import unittest

from hormuz.outcome_wire import observation_from_mapping
from hormuz.portfolio_config import PortfolioConnectorBinding
from hormuz.portfolio_wire import PortfolioError
from tools.verify_portfolio_extensions import ExtensionContractError, validate_extension_payload


ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "tests/fixtures/connectors/linear/cases.json"
CONTEXT_BUNDLE = ROOT / "docs/linear-context-wire-v1.json"
POSITIVE_IDS = {
    "initiative-create", "project-create", "cycle-create", "issue-create-in-project",
    "issue-project-cycle-change", "project-remove",
}
NEGATIVE_IDS = {"reject-malformed-opaque-id", "reject-content-bearing-normalized-field"}


class LinearConnectorFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pack = json.loads(PACK.read_bytes())
        cls.bundle = json.loads(CONTEXT_BUNDLE.read_bytes())
        cls.cases = {case["case_id"]: case for case in cls.pack["cases"]}
        cls.binding = PortfolioConnectorBinding(**cls.pack["synthetic_binding"])

    def test_index_provenance_and_mapping_boundaries(self):
        self.assertEqual(self.pack["fixture_kind"], "synthetic_linear_lifecycle_proposals")
        self.assertEqual(self.pack["schema_version"], 1)
        self.assertEqual(set(self.cases), POSITIVE_IDS | NEGATIVE_IDS)
        self.assertEqual(len(self.cases), len(self.pack["cases"]))
        self.assertEqual({self.cases[name]["entity_kind"] for name in POSITIVE_IDS},
                         {"initiative", "project", "cycle", "issue"})
        self.assertEqual(self.binding.provider, "linear")
        self.assertEqual(len(self.binding.external_object_ids), 1)

        for case in self.pack["cases"]:
            with self.subTest(case=case["case_id"]):
                self.assertTrue(case["source_documentation"])
                for reference in case["source_documentation"]:
                    self.assertTrue(reference["url"].startswith("https://linear.app/"))
                    self.assertEqual(reference["reviewed_on"], "2026-09-20")
                    self.assertTrue(reference["section"])
                self.assertTrue(case["mapping_notes"])
                self.assertEqual(case["source_input"]["body"]["type"].lower(), case["entity_kind"])
                self.assertEqual(case["source_input"]["body"]["organizationId"], self.binding.workspace_id)
                self.assertEqual(case["source_input"]["body"]["createdAt"], "2026-09-20T12:00:00Z")
                self.assertLessEqual(set(case["source_input"]["body"]["data"]),
                                     {"id", "projectId", "cycleId"})
                self.assertNotIn("actor", case["source_input"]["body"])
                if case["source_mapping"] == "mapping_pending":
                    self.assertTrue(case["open_question"])
                else:
                    self.assertIn(case["source_mapping"], {"proposed", "reject"})
                    self.assertIsNone(case["open_question"])

    def test_positive_candidates_use_existing_contracts_without_making_source_claims(self):
        for case_id in POSITIVE_IDS:
            case = self.cases[case_id]
            with self.subTest(case=case_id):
                context = case["candidate_targets"]["context_event"]
                validate_extension_payload(self.bundle, "hormuz.linear-context-event", context)
                self.assertEqual(context["object"], case["typed_identity"])
                self.assertEqual(context["relationships"], case["typed_relationships"])
                self.assertEqual(context["source_delivery_id"],
                                 case["source_input"]["headers"]["Linear-Delivery"])
                self.assertEqual(context["reader_role"], "portfolio_admin")
                self.assertEqual(context["evidence_level"], "descriptive")
                self.assertEqual(context["association_eligibility"], "inconclusive")
                self.assertEqual(context["scope_state"], "unmatched")
                self.assertIsNone(context["binding"])
                self.assertIsNone(context["source_team_ids"])
                self.assertIsNone(context["event_at"])
                self.assertEqual(context["revision"], {"kind": "unknown", "value": None})
                self.assertEqual(context["ordering_state"], "unknown")
                self.assertIsNone(context["supersedes_context_event_id"])
                self.assertIn(context["relationship_coverage"], {"unknown", "partial"})
                if context["relationship_coverage"] == "unknown":
                    self.assertEqual(context["relationships"], [])
                if case["entity_kind"] != "issue":
                    self.assertNotIn("source_observation", case["candidate_targets"])

    def test_issue_outcome_candidate_is_issue_only_and_project_scoped(self):
        case = self.cases["issue-create-in-project"]
        candidate = case["candidate_targets"]["source_observation"]
        self.assertEqual(asdict(observation_from_mapping(candidate, self.binding)), candidate)
        self.assertEqual(candidate["object_type"], "issue")
        self.assertEqual(candidate["external_object_id"], case["typed_identity"]["id"])
        self.assertEqual(candidate["container_id"], self.binding.external_object_ids[0])
        self.assertIsNone(candidate["source_revision"])
        self.assertIsNone(candidate["ordering_domain"])
        self.assertIsNone(candidate["revision_order"])
        self.assertIsNone(candidate["event_at"])
        self.assertEqual(case["source_mapping"], "mapping_pending")

    def test_move_is_partial_and_does_not_enroll_new_parents(self):
        moved = self.cases["issue-project-cycle-change"]
        context = moved["candidate_targets"]["context_event"]
        parents = {(relation["kind"], relation["parent"]["id"])
                   for relation in context["relationships"]}
        self.assertEqual({kind for kind, _ in parents}, {"project_issue", "cycle_issue"})
        self.assertEqual(context["relationship_coverage"], "partial")
        self.assertIsNone(context["supersedes_context_event_id"])
        self.assertTrue(all(parent not in self.binding.external_object_ids for _, parent in parents))
        old = moved["source_input"]["body"]["updatedFrom"]
        self.assertNotEqual(old["projectId"], moved["source_input"]["body"]["data"]["projectId"])
        self.assertNotEqual(old["cycleId"], moved["source_input"]["body"]["data"]["cycleId"])
        issue_candidate = dict(self.cases["issue-create-in-project"]["candidate_targets"]["source_observation"])
        issue_candidate["container_id"] = moved["source_input"]["body"]["data"]["projectId"]
        with self.assertRaises(PortfolioError) as caught:
            observation_from_mapping(issue_candidate, self.binding)
        self.assertEqual(caught.exception.code, "forbidden")

    def test_pending_remove_does_not_claim_archive_or_supersession(self):
        case = self.cases["project-remove"]
        context = case["candidate_targets"]["context_event"]
        self.assertEqual(case["source_mapping"], "mapping_pending")
        self.assertEqual(case["source_input"]["body"]["action"], "remove")
        self.assertEqual(context["lifecycle"], "deleted")
        self.assertEqual(context["normalized_state"], "unknown")
        self.assertEqual(context["relationship_coverage"], "unknown")
        self.assertIsNone(context["supersedes_context_event_id"])

    def test_invalid_normalized_variants_fail_with_content_free_codes(self):
        malformed = self.cases["reject-malformed-opaque-id"]
        invalid_id = malformed["candidate_targets"]["context_event"]["object"]["id"]
        with self.assertRaises(ExtensionContractError) as caught:
            validate_extension_payload(self.bundle, "hormuz.linear-context-event",
                                       malformed["candidate_targets"]["context_event"])
        self.assertEqual(str(caught.exception), "wire_payload_string_pattern")
        self.assertNotIn(invalid_id, str(caught.exception))

        content = self.cases["reject-content-bearing-normalized-field"]
        candidate = content["candidate_targets"]["source_observation"]
        with self.assertRaises(PortfolioError) as caught:
            observation_from_mapping(candidate, self.binding)
        self.assertEqual(caught.exception.code, "invalid_request")
        self.assertNotIn(candidate["title"], str(caught.exception))


if __name__ == "__main__":
    unittest.main()
