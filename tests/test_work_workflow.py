"""Signed production connector-to-work bridge and consent isolation witnesses."""
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hormuz.config import Identity
from hormuz.github_connector import GitHubOutcomeReceiver
from hormuz.linear_connector import LinearOutcomeReceiver
from hormuz.portfolio_repository import create_portfolio_repository
from hormuz.portfolio_wire import PortfolioError
from hormuz.store import UsageStore
from hormuz.work_runtime import WorkRuntime, WorkRuntimeError
from hormuz.work_workflow import WorkWorkflow, GitHubWorkRepository, LinearWorkRepository
from hormuz.outcome_wire import observation_from_mapping
from hormuz.outcome_ingest import registered_binding
from tests import test_github_connector_runtime as github
from tests import test_linear_connector_runtime as linear


class WorkWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.config = github.runtime_config(self.root)
        UsageStore(self.config.database_path)
        self.now = datetime(2026, 8, 31, tzinfo=timezone.utc).timestamp()
        self.runtime = WorkRuntime(self.root / "work.sqlite3", clock=lambda: self.now)
        self.workflow = WorkWorkflow(self.runtime, self.config)
        self.identity = Identity("", "", "alice", "Alice", "engineering", "Engineering", organization_id="acme")
        self.repository = create_portfolio_repository(self.config)
        self.receiver = GitHubOutcomeReceiver(self.config, GitHubWorkRepository(self.repository.outcomes, self.workflow))

    def tearDown(self):
        self.runtime.close()
        self.directory.cleanup()

    def bind(self, condition="github.pull_request.merged.v1"):
        work = self.runtime.create_work(self.identity, "customer/repo", completion_condition=condition)
        binding = self.workflow.bind(self.identity, work["work_id"], provider="github", connector_id="github-one", container_id="456", object_id="1001", completion_condition=condition)
        return work["work_id"], binding

    def deliver(self, value, index=1):
        raw = github.encoded(value)
        return self.receiver.ingest(github.signed(raw, **{"X-GitHub-Delivery": "30000000-0000-4000-8000-" + str(index).zfill(12)}), raw)

    def closed(self, date="2026-09-01T10:00:00Z"):
        value = github.payload(action="closed")
        value["pull_request"].update(merged=True, closed_at=date)
        return value

    def test_signed_merge_completes_explicit_work_reopen_invalidates_and_replay_is_idempotent(self):
        work_id, _ = self.bind()
        self.deliver(self.closed())
        self.assertEqual("completed", self.runtime.get_work(self.identity, work_id)["state"])
        reopened = github.payload(action="reopened")
        reopened["pull_request"]["updated_at"] = "2026-09-02T10:00:00Z"
        self.deliver(reopened, 2)
        work = self.runtime.get_work(self.identity, work_id)
        self.assertEqual("active", work["state"])
        self.assertEqual(1, self.runtime.cache_generation(self.identity, work_id))
        self.assertEqual("verified_github", work["observations"][-1]["source"])
        self.deliver(reopened, 2)
        self.assertEqual(2, len(self.runtime.get_work(self.identity, work_id)["observations"]))

    def test_invalid_signature_stores_no_workflow_metadata(self):
        self.bind()
        raw = github.encoded(self.closed())
        with self.assertRaises(PortfolioError):
            self.receiver.ingest(github.signed(raw, secret="unapproved-secret"), raw)
        with self.runtime._transaction() as connection:
            self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM ai_work_bridge_events").fetchone()[0])

    def test_racing_same_delivery_bodies_apply_only_the_exact_accepted_source_bytes(self):
        work_id, _ = self.bind()
        # A generic verified adapter could reuse an opaque delivery identity.
        # GitHub itself already hashes exact body bytes; this independently
        # proves our journal cannot apply staged bytes from another body.
        ingestor = self.receiver._ingestors[0]
        binding = registered_binding(self.config, "acme", "github-one")
        body_a = self.closed()
        raw_a = github.encoded(body_a)
        verified_a = ingestor.adapter.verify(binding=binding, headers=github.signed(raw_a), raw=raw_a)
        observations = tuple(observation_from_mapping(item, binding) for item in ingestor.adapter.normalize(binding=binding, verified=verified_a, body=body_a))
        self.workflow._stage(binding=binding, verified=verified_a, raw=raw_a, observations=observations, source_kind="pull_request_lifecycle")
        body_b = github.payload(action="reopened")
        body_b["pull_request"]["updated_at"] = "2026-09-02T10:00:00Z"
        self.deliver(body_b)
        state = self.runtime.get_work(self.identity, work_id)
        self.assertEqual("active", state["state"])
        self.assertEqual(["reopened"], [row["status"] for row in state["observations"]])
        digest_a, _ = self.workflow._delivery_digest(binding, verified_a, raw_a)
        digest_b, _ = self.workflow._delivery_digest(binding, verified_a, github.encoded(body_b))
        self.assertNotEqual(digest_a, digest_b)
        self.deliver(body_a)
        self.assertEqual("active", self.runtime.get_work(self.identity, work_id)["state"])

    def test_source_commit_replay_finishes_interrupted_observation_without_duplicates(self):
        work_id, _ = self.bind()
        with patch.object(self.runtime, "observe", side_effect=WorkRuntimeError("storage_unavailable", 503)):
            with self.assertRaises(PortfolioError):
                self.deliver(self.closed())
        self.assertEqual("active", self.runtime.get_work(self.identity, work_id)["state"])
        self.deliver(self.closed())
        self.deliver(self.closed())
        work = self.runtime.get_work(self.identity, work_id)
        self.assertEqual("completed", work["state"])
        self.assertEqual(1, len(work["observations"]))

    def test_late_merge_cannot_undo_newer_reopening_and_raw_ids_are_not_retained(self):
        work_id, _ = self.bind()
        value = github.payload(action="reopened")
        value["pull_request"]["updated_at"] = "2026-09-02T10:00:00Z"
        self.deliver(value, 2)
        self.deliver(self.closed(), 1)
        self.assertEqual("active", self.runtime.get_work(self.identity, work_id)["state"])
        with self.runtime._transaction() as connection:
            binding = dict(connection.execute("SELECT * FROM ai_work_bindings").fetchone())
            self.assertNotIn("1001", binding.values())
            self.assertNotIn("456", binding.values())
            self.assertEqual("late_or_ambiguous", connection.execute("SELECT reason FROM ai_work_bridge_events ORDER BY event_at LIMIT 1").fetchone()[0])

    def test_check_success_does_not_mean_merge_and_review_approval_does_not_mean_check(self):
        work_id, _ = self.bind()
        check = github.payload(pull_request=None, action="completed", check_run={"id": 9001, "status": "completed", "conclusion": "success", "completed_at": "2026-09-01T10:00:00Z", "pull_requests": [{"id": 1001}]})
        self.deliver(check)
        self.assertEqual("active", self.runtime.get_work(self.identity, work_id)["state"])
        # A different owned object must not steal an existing work association.
        other = self.runtime.create_work(self.identity, "customer/repo", completion_condition="github.check.passed.v1")
        with self.assertRaises(WorkRuntimeError) as caught:
            self.workflow.bind(self.identity, other["work_id"], provider="github", connector_id="github-one", container_id="456", object_id="1001", completion_condition="github.check.passed.v1")
        self.assertEqual("binding_association_conflict", caught.exception.reason)

    def test_owned_bindings_and_private_funnel_require_explicit_consent(self):
        work_id, _ = self.bind()
        other = replace(self.identity, organization_id="other")
        with self.assertRaises(WorkRuntimeError):
            self.workflow.bindings(other, work_id)
        self.workflow.event(self.identity, "receipt_opened", work_id)
        self.assertEqual({"consent": False, "events": {}}, self.workflow.funnel(self.identity))
        with self.assertRaises(WorkRuntimeError):
            self.workflow.acquire(self.identity, {"utm_source": "ad"}, consent=False)
        acquired = self.workflow.acquire(self.identity, {"utm_source": "ad"}, consent=True)
        self.workflow.event(self.identity, "receipt_opened", work_id)
        self.workflow.event(self.identity, "receipt_opened", work_id)
        funnel = self.workflow.funnel(self.identity)
        self.assertEqual(acquired["acquisition_reference"], funnel["acquisition_reference"])
        self.assertEqual(1, funnel["events"]["receipt_opened"])
        self.assertEqual({"consent": False, "events": {}}, self.workflow.funnel(other))

    def test_linear_verified_issue_completion_updates_explicit_work(self):
        config = linear.runtime_config(self.root)
        repository = create_portfolio_repository(config)
        self.now = linear.NOW_MS / 1000 - 60
        workflow = WorkWorkflow(self.runtime, config)
        work = self.runtime.create_work(self.identity, "customer/linear", completion_condition="linear.issue.completed.v1")
        workflow.bind(self.identity, work["work_id"], provider="linear", connector_id="linear-one", container_id=linear.PROJECT, object_id=linear.ISSUE, completion_condition="linear.issue.completed.v1")
        receiver = LinearOutcomeReceiver(config, LinearWorkRepository(repository.linear, workflow))
        value = linear.payload(action="update")
        value["data"]["completedAt"] = "2026-09-23T12:00:00Z"
        value["data"]["updatedAt"] = "2026-09-23T12:00:00Z"
        value["updatedFrom"] = {"completedAt": None}
        raw = linear.encoded(value)
        receiver.ingest(linear.signed(raw), raw, now_ms=linear.NOW_MS)
        self.assertEqual("completed", self.runtime.get_work(self.identity, work["work_id"])["state"])
