"""Offline integrity and existing-contract checks for proposed GitHub examples."""

from __future__ import annotations

from dataclasses import asdict, fields
import json
from pathlib import Path
import unittest

from hormuz.outcome_wire import SourceObservation, observation_from_mapping
from hormuz.portfolio_config import PortfolioConnectorBinding
from hormuz.portfolio_wire import PortfolioError


FIXTURE = Path(__file__).resolve().parent / "fixtures/connectors/github/cases.json"
SOURCE_DOCUMENT = "https://docs.github.com/en/webhooks/webhook-events-and-payloads"
INVENTORY = {
    "pr-opened": ("pull_request", "opened", "candidate_observation"),
    "pr-closed-merged": ("pull_request", "closed", "candidate_observation"),
    "pr-closed-unmerged": ("pull_request", "closed", "mapping_pending"),
    "review-submitted": ("pull_request_review", "submitted", "mapping_pending"),
    "check-run-success": ("check_run", "completed", "mapping_pending"),
    "check-run-failure": ("check_run", "completed", "mapping_pending"),
    "pr-opened-missing-object-id": ("pull_request", "opened", "invalid_projection"),
    "pr-opened-invalid-object-id-type": ("pull_request", "opened", "invalid_projection"),
}


class GitHubConnectorFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pack = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.cases = {case["case_id"]: case for case in cls.pack["cases"]}
        cls.binding = PortfolioConnectorBinding(**{
            **cls.pack["synthetic_server_binding"],
            "external_object_ids": tuple(cls.pack["synthetic_server_binding"]["external_object_ids"]),
        })

    def test_inventory_source_notes_and_separate_inputs(self) -> None:
        self.assertEqual(self.pack["fixture_pack"], "github-outcome-proposals-v1")
        self.assertEqual(self.pack["reviewed_on"], "2026-09-20")
        self.assertEqual(self.pack["source_document"], SOURCE_DOCUMENT)
        self.assertEqual(len(self.pack["cases"]), len(INVENTORY))
        self.assertEqual(set(self.cases), set(INVENTORY))
        self.assertEqual(self.binding.provider, "github")
        self.assertEqual(self.binding.external_object_ids, ("456",))
        self.assertEqual(self.binding.installation_id, "123")
        contract_fields = {item.name for item in fields(SourceObservation)}
        deliveries = set()
        for case_id, case in self.cases.items():
            with self.subTest(case=case_id):
                event, action, kind = INVENTORY[case_id]
                self.assertEqual((case["provider_event"], case["provider_action"],
                                  case["expectation"]["kind"]), (event, action, kind))
                self.assertEqual(case["source_document"], f"{SOURCE_DOCUMENT}#{event}")
                self.assertEqual(case["reviewed_on"], self.pack["reviewed_on"])
                self.assertEqual(set(case["field_mapping"]), contract_fields)
                self.assertTrue(all(isinstance(note, str) and note.strip()
                                    for note in case["field_mapping"].values()))
                source = case["input"]
                self.assertEqual(set(source), {"headers", "body"})
                self.assertEqual(source["headers"]["X-GitHub-Event"], event)
                delivery = source["headers"]["X-GitHub-Delivery"]
                self.assertRegex(delivery, r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
                self.assertNotIn(delivery, deliveries)
                deliveries.add(delivery)
                self.assertEqual(source["body"]["action"], action)
                self.assertEqual(source["body"]["installation"]["id"], 123)
                self.assertEqual(source["body"]["repository"]["id"], 456)
                self.assertFalse({"organization_id", "connector_id", "work_scope_id"} & set(source["body"]))
                self.assertFalse({"observation", "invalid_observation"} & set(source))
                self.assertNotIn("http", json.dumps(source).lower())
                self.assertFalse({"title", "body", "full_name", "html_url", "diff_url", "patch_url"} &
                                 set(source["body"].get("pull_request", {})))

    def test_provider_identifiers_remain_distinct_and_synthetic(self) -> None:
        numeric_ids = {123, 456}
        revisions = set()
        for case_id, case in self.cases.items():
            with self.subTest(case=case_id):
                body = case["input"]["body"]
                obj = body.get("pull_request")
                if obj is not None:
                    object_id = obj.get("id")
                    if object_id is not None and type(object_id) is int:
                        self.assertNotIn(object_id, numeric_ids)
                        numeric_ids.add(object_id)
                    sha = obj["head"]["sha"]
                    if "merge_commit_sha" in obj:
                        self.assertNotEqual(sha, obj["merge_commit_sha"])
                        revisions.add(obj["merge_commit_sha"])
                else:
                    check = body["check_run"]
                    self.assertNotIn(check["id"], numeric_ids)
                    numeric_ids.add(check["id"])
                    sha = check["head_sha"]
                    for pr in check["pull_requests"]:
                        self.assertNotIn(pr["id"], numeric_ids)
                        numeric_ids.add(pr["id"])
                self.assertRegex(sha, r"^[0-9a-f]{40}$")
                revisions.add(sha)
                if "review" in body:
                    self.assertNotIn(body["review"]["id"], numeric_ids)
                    numeric_ids.add(body["review"]["id"])
        self.assertEqual(len(revisions), 9)
        self.assertTrue(all(str(value) not in revisions for value in numeric_ids))
        self.assertNotIn("id", self.cases["pr-opened-missing-object-id"]["input"]["body"]["pull_request"])
        self.assertIs(self.cases["pr-opened-invalid-object-id-type"]["input"]["body"]["pull_request"]["id"], True)

    def test_candidate_observations_use_only_existing_closed_contract(self) -> None:
        for case_id in ("pr-opened", "pr-closed-merged"):
            with self.subTest(case=case_id):
                case = self.cases[case_id]
                expectation = case["expectation"]
                self.assertIs(expectation["proposal_only"], True)
                self.assertTrue(expectation["decisions_needed"])
                projected = expectation["observation"]
                self.assertEqual(asdict(observation_from_mapping(projected, self.binding)), projected)
                self.assertEqual(projected["external_object_id"], str(case["input"]["body"]["pull_request"]["id"]))
                self.assertEqual(projected["container_id"], str(case["input"]["body"]["repository"]["id"]))
                self.assertNotEqual(projected["source_event_id"], case["input"]["headers"]["X-GitHub-Delivery"])
                self.assertIsNone(projected["source_revision"])
                self.assertIsNone(projected["ordering_domain"])
                self.assertIsNone(projected["revision_order"])
                self.assertEqual(projected["quality_state"], "unknown")
                self.assertFalse({"organization_id", "connector_id", "source_delivery_id", "evidence_level",
                                  "work_scope_id"} & set(projected))
                with self.assertRaises(PortfolioError) as caught:
                    observation_from_mapping({**projected, "organization_id": "source-claimed-org"}, self.binding)
                self.assertEqual(caught.exception.code, "invalid_request")
                with self.assertRaises(PortfolioError) as caught:
                    observation_from_mapping({**projected, "container_id": "999"}, self.binding)
                self.assertEqual(caught.exception.code, "forbidden")

    def test_pending_cases_have_no_projected_observation(self) -> None:
        for case_id in ("pr-closed-unmerged", "review-submitted", "check-run-success", "check-run-failure"):
            with self.subTest(case=case_id):
                expectation = self.cases[case_id]["expectation"]
                self.assertIsNone(expectation["observation"])
                self.assertTrue(expectation["decisions_needed"])
        self.assertIs(self.cases["pr-closed-unmerged"]["input"]["body"]["pull_request"]["merged"], False)
        self.assertEqual(self.cases["review-submitted"]["input"]["body"]["review"]["state"], "approved")
        self.assertEqual(self.cases["check-run-success"]["input"]["body"]["check_run"]["conclusion"], "success")
        self.assertEqual(self.cases["check-run-failure"]["input"]["body"]["check_run"]["conclusion"], "failure")
        self.assertEqual(self.cases["check-run-failure"]["input"]["body"]["check_run"]["pull_requests"], [])

    def test_malformed_projections_fail_existing_validator(self) -> None:
        for case_id, invalid in (("pr-opened-missing-object-id", None),
                                 ("pr-opened-invalid-object-id-type", True)):
            with self.subTest(case=case_id):
                expectation = self.cases[case_id]["expectation"]
                projected = expectation["invalid_observation"]
                self.assertIs(projected["external_object_id"], invalid)
                with self.assertRaises(PortfolioError) as caught:
                    observation_from_mapping(projected, self.binding)
                self.assertEqual(caught.exception.code, expectation["expected_error"])
                # Only the deliberately malformed ID keeps this closed projection invalid.
                repaired = {**projected, "external_object_id": "1006"}
                self.assertEqual(asdict(observation_from_mapping(repaired, self.binding)), repaired)


if __name__ == "__main__":
    unittest.main()
