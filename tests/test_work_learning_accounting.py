from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from hormuz.config import Identity, ModelRoute
from hormuz.work_runtime import WorkRuntime, WorkRuntimeError
from hormuz.work_provider_costs import account_cost_view


class WorkLearningAccountingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.now = 1_791_465_600.0
        self.path = Path(self.directory.name) / "work.sqlite3"
        self.settings = SimpleNamespace(exploration_enabled=False, exploration_aliases=("cheap",),
            exploration_rate_percent=20, exploration_max_cost_microusd=100, tool_capable_aliases=("fast", "cheap"))
        self.runtime = WorkRuntime(self.path, SimpleNamespace(ai_work=self.settings),
            clock=lambda: self.now, minimum_samples=3, cache_enabled=True)
        self.owner = Identity("TOKEN", "secret", "alice", "Alice", "team", "Team", organization_id="company")
        self.fast = ModelRoute("fast", "openai", "fast-v1", input_cost_per_million=2)
        self.cheap = ModelRoute("cheap", "openai", "cheap-v1", input_cost_per_million=1)
        self.request = {"model": "fast", "input": "Summarize this sanitized engineering note", "temperature": 0}

    def tearDown(self):
        self.runtime.close()
        self.directory.cleanup()

    def work(self, **values):
        return self.runtime.create_work(self.owner, values.pop("repository", "org/repo"), context_revision="commit-1", **values)["work_id"]

    def error(self, reason, fn):
        with self.assertRaises(WorkRuntimeError) as caught:
            fn()
        self.assertEqual(reason, caught.exception.reason)

    def seed(self, route, *, request=None, condition="workflow.completed.v1", count=3):
        for index in range(count):
            work = self.work(task_type="diagnosis", completion_condition=condition)
            if request is not None:
                self.runtime.bind_request_context(self.owner, work, request)
            attempt = "attempt-" + work
            self.runtime.reserve(self.owner, work, attempt, route, "openai", 30,
                request_shape=self.runtime.shape_for_request(request or {}))
            self.runtime.settle(self.owner, attempt, 20, latency_ms=30)
            self.runtime.observe(self.owner, work, "completed", completion_condition=condition)

    def test_characteristic_classification_retains_unknown_and_never_persists_input(self):
        work = self.work()
        secret = "Summarize unique sanitized customer note 96395"
        bound = self.runtime.bind_request_context(self.owner, work, {"input": secret})
        self.assertEqual(("summarization", "inferred_characteristics"), (bound["task_type"], bound["task_origin"]))
        unknown = self.work()
        bound = self.runtime.bind_request_context(self.owner, unknown, {"input": "What should I do next?"})
        self.assertEqual(("general", "unknown"), (bound["task_type"], bound["task_origin"]))
        self.assertEqual("unclassified_baseline", self.runtime.choose_route(self.owner, unknown, [self.fast, self.cheap], self.fast, {"input": "What should I do next?"})["reason"])
        declared = self.work(task_type="security-review")
        self.assertEqual("security-review", self.runtime.bind_request_context(self.owner, declared, self.request)["task_type"])
        declared_general = self.work(task_type="general")
        self.assertEqual(("general", "declared"), tuple(self.runtime.bind_request_context(self.owner, declared_general, self.request)[field] for field in ("task_type", "task_origin")))
        with closing(sqlite3.connect(self.path)) as connection:
            dump = "\n".join(connection.iterdump())
        self.assertNotIn(secret, dump)
        unattributed = self.work(repository="unattributed")
        self.runtime.bind_request_context(self.owner, unattributed, self.request)
        self.assertEqual("unattributed_baseline", self.runtime.choose_route(self.owner, unattributed, [self.fast, self.cheap], self.fast, self.request)["reason"])

    def test_context_and_completion_versions_partition_real_local_evidence(self):
        self.seed(self.fast, request=self.request)
        self.seed(self.cheap, request=self.request)
        work = self.work(task_type="diagnosis")
        self.runtime.bind_request_context(self.owner, work, self.request)
        ready = self.runtime.refresh_benchmark(self.owner, work, "openai", self.request)
        self.assertEqual(6, ready["sample_count"])
        different = {**self.request, "input": "Summarize " + "x" * 9000}
        self.runtime.bind_request_context(self.owner, work, different)
        self.assertEqual(0, self.runtime.refresh_benchmark(self.owner, work, "openai", different)["sample_count"])
        versioned = self.work(task_type="diagnosis", completion_condition="checks.pytest.v2")
        self.runtime.bind_request_context(self.owner, versioned, self.request)
        self.assertEqual(0, self.runtime.refresh_benchmark(self.owner, versioned, "openai", self.request)["sample_count"])
        self.error("completion_condition_conflict", lambda: self.runtime.observe(self.owner, versioned, "completed", completion_condition="checks.pytest.v1"))

    def test_concurrent_same_job_request_contexts_do_not_conflict_or_cross_cache(self):
        work = self.work(task_type="diagnosis")
        small = self.request
        large = {**self.request, "input": "Summarize " + "x" * 9000}
        contexts = [self.runtime.bind_request_context(self.owner, work, value)["context_signature"] for value in (small, large)]
        self.assertNotEqual(*contexts)
        def reserve(values):
            index, context = values
            return self.runtime.reserve(self.owner, work, "parallel-" + str(index), self.fast, "openai", 20,
                request_context=context)["context_signature"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(contexts, list(pool.map(reserve, enumerate(contexts))))
        generation = self.runtime.cache_generation(self.owner, work)
        for request, answer in ((small, b"small"), (large, b"large")):
            self.assertTrue(self.runtime.cache_put(self.owner, work, request, self.fast, "policy", answer, expected_generation=generation))
        self.assertEqual(b"small", self.runtime.cache_get(self.owner, work, small, self.fast, "policy")["body"])
        self.assertEqual(b"large", self.runtime.cache_get(self.owner, work, large, self.fast, "policy")["body"])

    def test_declared_signed_completion_rejects_manual_and_wrong_connector_sources(self):
        conditions = {"github.pull_request.merged.v1": "verified_github",
                      "github.check.passed.v1": "verified_github",
                      "linear.issue.completed.v1": "verified_linear"}
        for condition, required_source in conditions.items():
            with self.subTest(condition=condition):
                work = self.work(task_type="diagnosis", completion_condition=condition)
                for source in ("workflow", "agent", "operator", "verified_linear" if required_source == "verified_github" else "verified_github"):
                    self.error("completion_verified_source_required", lambda: self.runtime.observe(self.owner, work, "completed", source=source))
                self.assertEqual("active", self.runtime.get_work(self.owner, work)["state"])
                self.assertEqual([], self.runtime.get_work(self.owner, work)["observations"])
                corrected = self.runtime.observe(self.owner, work, "corrected", source="agent")
                self.assertEqual("agent", corrected["observations"][-1]["source"])
                self.assertEqual("completed", self.runtime.observe(self.owner, work, "completed", source=required_source)["state"])

    def test_legacy_manual_completion_cannot_train_signed_cohort_until_verified_event(self):
        condition = "github.pull_request.merged.v1"
        work = self.work(task_type="diagnosis", completion_condition=condition)
        self.runtime.bind_request_context(self.owner, work, self.request)
        self.runtime.reserve(self.owner, work, "signed-episode", self.cheap, "openai", 30,
                             request_shape=self.runtime.shape_for_request(self.request))
        self.runtime.settle(self.owner, "signed-episode", 20, latency_ms=10)
        # Reproduce an unsourced completion retained from the previous runtime.
        with self.runtime._transaction(write=True) as connection:
            connection.execute("INSERT INTO ai_work_observations(observation_id,organization_id,actor_id,work_id,status,source,observed_at,completion_condition) VALUES(?,?,?,?,?,?,?,?)",
                ("legacy-manual", "company", "alice", work, "completed", "workflow", self.now, condition))
            connection.execute("UPDATE ai_work_jobs SET state='completed',completed_at=? WHERE work_id=?", (self.now, work))
        self.assertEqual(0, self.runtime.refresh_benchmark(self.owner, work, "openai", self.request)["sample_count"])
        with self.runtime._transaction(write=True) as connection:
            connection.execute("INSERT INTO ai_work_observations(observation_id,organization_id,actor_id,work_id,status,source,observed_at,completion_condition) VALUES(?,?,?,?,?,?,?,?)",
                ("legacy-verified-rework", "company", "alice", work, "corrected", "verified_github", self.now, condition))
        self.assertEqual(1, self.runtime.refresh_benchmark(self.owner, work, "openai", self.request)["sample_count"])
        legacy_selection = self.runtime.choose_route(self.owner, work, [self.fast, self.cheap], self.fast, self.request)
        legacy_candidate = next(item for item in legacy_selection["candidates"] if item["model"] == "cheap")
        self.assertEqual((0, None), (legacy_candidate["completed"], legacy_candidate["mean_completion_ms"]))
        self.runtime.observe(self.owner, work, "completed", source="verified_github", reference="signed-source-receipt")
        self.assertEqual(1, self.runtime.refresh_benchmark(self.owner, work, "openai", self.request)["sample_count"])
        self.now += 1
        self.runtime.observe(self.owner, work, "corrected", source="agent", reference="reported-rework")
        self.assertEqual(1, self.runtime.refresh_benchmark(self.owner, work, "openai", self.request)["sample_count"])
        selection = self.runtime.choose_route(self.owner, work, [self.fast, self.cheap], self.fast, self.request)
        candidate = next(item for item in selection["candidates"] if item["model"] == "cheap")
        self.assertEqual((1, 0, 1), (candidate["samples"], candidate["completed"], candidate["rework"]))
        self.assertEqual("agent", self.runtime.get_work(self.owner, work)["observations"][-1]["source"])

    def test_signed_legacy_condition_does_not_reuse_manual_completion_snapshot(self):
        self.seed(self.fast)
        self.seed(self.cheap)
        with self.runtime._transaction(write=True) as connection:
            connection.execute("UPDATE ai_work_attempts SET cost_microusd=5 WHERE model='cheap'")
        manual = self.work(task_type="diagnosis")
        self.assertEqual(6, self.runtime.refresh_benchmark(self.owner, manual, "openai")["sample_count"])
        self.assertEqual("cheap", self.runtime.choose_route(self.owner, manual, [self.fast, self.cheap], self.fast, {})["alias"])
        signed = self.work(task_type="diagnosis", completion_condition="github.pull_request.merged.v1")
        chosen = self.runtime.choose_route(self.owner, signed, [self.fast, self.cheap], self.fast, {})
        self.assertEqual(("fast", 0), (chosen["alias"], chosen["sample_count"]))
        self.assertEqual(0, self.runtime.refresh_benchmark(self.owner, signed, "openai")["sample_count"])

    def test_finite_budget_reads_match_reported_spend_without_reporting_aggregation(self):
        work = self.work(task_type="diagnosis")
        for scope, identifier, cap in (("workspace", "company", 100), ("repository", "org/repo", 80), ("job", work, 50)):
            self.runtime.set_plan("company", scope, identifier, cap, "cost")
        for request_id, reserved, cost, status in (("success", 10, 3, "succeeded"), ("failure", 10, 5, "failed"), ("unknown", 7, None, "unknown"), ("pending", 9, None, None)):
            self.runtime.reserve(self.owner, work, request_id, self.fast, "openai", reserved)
            if status is not None:
                self.runtime.settle(self.owner, request_id, cost, status=status)
        same_repo = self.runtime.create_work(replace(self.owner, actor_id="bob"), "org/repo")["work_id"]
        self.runtime.reserve(replace(self.owner, actor_id="bob"), same_repo, "bob-spend", self.fast, "openai", 10)
        self.runtime.settle(replace(self.owner, actor_id="bob"), "bob-spend", 2)
        other_repo = self.work(repository="org/other")
        self.runtime.reserve(self.owner, other_repo, "other-repo-spend", self.fast, "openai", 15)
        self.runtime.settle(self.owner, "other-repo-spend", 11)
        other_owner = replace(self.owner, organization_id="other-company")
        other_work = self.runtime.create_work(other_owner, "org/repo")["work_id"]
        self.runtime.reserve(other_owner, other_work, "other-tenant-spend", self.fast, "openai", 50)
        self.runtime.settle(other_owner, "other-tenant-spend", 50)
        def remaining():
            with self.runtime._transaction() as connection:
                row = self.runtime._work(connection, ("company", "alice"), work)
                fast = self.runtime._budget_plans_for_work(connection, row, self.now)
                report = self.runtime._plans_for_work(connection, row, self.now, include_forecast=False)
                self.assertEqual([item["remaining_microusd"] for item in report], [item["remaining_microusd"] for item in fast])
                return [item["remaining_microusd"] for item in fast]
        self.assertEqual([63, 54, 26], remaining())
        self.runtime.confirm_cost(self.owner, "unknown", 4, "receipt-final-cost", source="provider_receipt", verified=True)
        self.assertEqual([66, 57, 29], remaining())
        with patch.object(self.runtime, "_costs", side_effect=AssertionError("reporting aggregation in admission")):
            self.runtime.reserve(self.owner, work, "minimal-admission", self.fast, "openai", 29)
        self.error("budget_exhausted", lambda: self.runtime.reserve(self.owner, work, "over-parent-cap", self.fast, "openai", 1))

    def test_routing_and_uncapped_limits_skip_full_reporting_and_equal_scopes_reuse_spend(self):
        self.seed(self.fast)
        work = self.work(task_type="diagnosis")
        self.runtime.refresh_benchmark(self.owner, work, "openai")
        self.runtime.set_plan("company", "workspace", "company", None, "cost")
        with self.runtime._transaction() as connection:
            queries = []
            connection.set_trace_callback(queries.append)
            self.runtime._budget_plans_for_work(connection, self.runtime._work(connection, ("company", "alice"), work), self.now)
            self.assertFalse(any("SUM" in query and "ai_work_attempts" in query for query in queries))
        self.runtime.set_plan("company", "workspace", "company", 100, "cost")
        self.runtime.set_plan("company", "repository", "org/repo", 90, "cost")
        with self.runtime._transaction() as connection:
            queries = []
            connection.set_trace_callback(queries.append)
            self.runtime._budget_plans_for_work(connection, self.runtime._work(connection, ("company", "alice"), work), self.now)
            self.assertEqual(1, sum("SUM" in query and "ai_work_attempts" in query for query in queries))
        with patch.object(self.runtime, "_costs", side_effect=AssertionError("reporting aggregation in selection")):
            selected = self.runtime.choose_route(self.owner, work, [self.fast, self.cheap], self.fast, {}, max_costs={"fast": 20, "cheap": 10})
            self.assertEqual("fast", selected["alias"])

    def test_alternative_cold_start_uses_one_bounded_approved_incoming_request(self):
        self.seed(self.fast)
        self.settings.exploration_enabled = True
        work = self.work(task_type="diagnosis")
        self.runtime.refresh_benchmark(self.owner, work, "openai")
        chosen = self.runtime.choose_route(self.owner, work, [self.fast, self.cheap], self.fast, {}, max_costs={"fast": 30, "cheap": 25})
        self.assertEqual(("cheap", "bounded_local_exploration"), (chosen["alias"], chosen["reason"]))
        self.assertEqual(0, chosen["sample_count"])
        self.assertEqual([], self.runtime.get_work(self.owner, work)["attempts"])
        self.runtime.reserve(self.owner, work, "actual", self.cheap, "openai", 25, reason=chosen["reason"])
        self.runtime.settle(self.owner, "actual", 20, latency_ms=5)
        self.runtime.observe(self.owner, work, "completed")
        later = self.work(task_type="diagnosis")
        self.assertEqual(4, self.runtime.refresh_benchmark(self.owner, later, "openai")["sample_count"])
        selection = self.runtime.choose_route(self.owner, later, [self.fast, self.cheap], self.fast, {})
        cheap = next(candidate for candidate in selection["candidates"] if candidate["model"] == "cheap")
        self.assertEqual(1, cheap["samples"])
        self.assertFalse(cheap["eligible"])

    def test_exploration_explicit_approval_parent_budget_and_exclusions(self):
        self.seed(self.fast)
        self.settings.exploration_enabled = True
        for kind in ("polling", "retry", "scheduled", "continuation"):
            work = self.work(task_type="diagnosis")
            self.runtime.refresh_benchmark(self.owner, work, "openai")
            self.assertEqual("fast", self.runtime.choose_route(self.owner, work, [self.fast, self.cheap], self.fast, {}, max_costs={"cheap": 20}, request_kind=kind)["alias"])
        work = self.work(task_type="diagnosis")
        self.runtime.set_plan("company", "job", work, 10, "cost", exploration_enabled=True)
        self.runtime.refresh_benchmark(self.owner, work, "openai")
        self.assertEqual("fast", self.runtime.choose_route(self.owner, work, [self.fast, self.cheap], self.fast, {}, max_costs={"cheap": 20})["alias"])
        self.runtime.set_plan("company", "job", work, 100, "cost", exploration_enabled=False)
        self.assertEqual("fast", self.runtime.choose_route(self.owner, work, [self.fast, self.cheap], self.fast, {}, max_costs={"cheap": 20})["alias"])
        self.runtime.set_plan("company", "job", work, 100, "cost", exploration_enabled=True)
        self.assertEqual("fast", self.runtime.choose_route(self.owner, work, [self.fast, self.cheap], self.fast, {"tools": [{"type": "function"}]}, max_costs={"cheap": 20})["alias"])
        self.runtime.set_plan("company", "workspace", "company", 1000, "cost", exploration_enabled=False)
        self.assertEqual("fast", self.runtime.choose_route(self.owner, work, [self.fast, self.cheap], self.fast, {}, max_costs={"cheap": 20})["alias"])
        amended = self.runtime.set_plan("company", "workspace", "company", 2000, "speed")
        self.assertFalse(amended["exploration_enabled"])
        reset = self.runtime.set_plan("company", "workspace", "company", 2000, "speed", exploration_enabled=None)
        self.assertIsNone(reset["exploration_enabled"])

    def test_concurrent_exploration_allocations_respect_monthly_ceiling(self):
        self.seed(self.fast, count=30)
        self.settings.exploration_enabled = True
        works = [self.work(task_type="diagnosis") for _ in range(4)]
        for work in works:
            self.runtime.refresh_benchmark(self.owner, work, "openai")
        with ThreadPoolExecutor(max_workers=4) as pool:
            result = list(pool.map(lambda work: self.runtime.choose_route(self.owner, work, [self.fast, self.cheap], self.fast, {}, max_costs={"cheap": 60})["alias"], works))
        self.assertEqual(1, result.count("cheap"))
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(60, connection.execute("SELECT SUM(reserved_microusd) FROM ai_work_exploration").fetchone()[0])

    def test_speed_choice_stays_inside_all_parent_budgets_and_common_timing_basis(self):
        self.seed(self.fast)
        self.seed(self.cheap)
        work = self.work(task_type="diagnosis")
        self.runtime.set_plan("company", "job", work, 25, "speed")
        self.runtime.refresh_benchmark(self.owner, work, "openai")
        chosen = self.runtime.choose_route(self.owner, work, [self.fast, self.cheap], self.fast, {}, max_costs={"fast": 40, "cheap": 20})
        self.assertEqual("cheap", chosen["alias"])
        excluded = next(row for row in chosen["candidates"] if row["model"] == "fast")
        self.assertEqual("work_budget_or_unbounded_cost", excluded["exclusion_reason"])
        self.assertFalse(excluded["eligible"])
        self.assertEqual("sum_measured_provider_intervals", chosen["latency_basis"])

    def test_recurrence_allows_first_cache_reuse_then_bypasses_without_quality_claim(self):
        work = self.work()
        self.runtime.bind_request_context(self.owner, work, self.request)
        pattern = self.runtime.pattern_for_request(self.request)
        self.runtime.reserve(self.owner, work, "first", self.fast, "openai", 10, request_pattern=pattern)
        self.runtime.settle(self.owner, "first", 5, latency_ms=1)
        first_guard = self.runtime.recurrence_guard(self.owner, work, self.request)
        self.assertFalse(first_guard["bypass"])
        generation = self.runtime.cache_generation(self.owner, work)
        self.assertTrue(self.runtime.cache_put(self.owner, work, self.request, self.fast, "policy", b"answer", expected_generation=generation))
        self.runtime.reserve(self.owner, work, "replay", self.fast, "openai", 0, cache_source="exact_answer_cache", request_pattern=pattern)
        self.runtime.settle(self.owner, "replay", status="cache_hit", latency_ms=1)
        for kind in ("polling", "retry", "scheduled", "history", "legitimate_iteration"):
            self.assertFalse(self.runtime.recurrence_guard(self.owner, work, self.request, request_kind=kind)["bypass"])
        guard = self.runtime.recurrence_guard(self.owner, work, self.request)
        self.assertEqual((True, "possible_repeat_not_quality"), (guard["bypass"], guard["reason"]))
        self.assertIsNone(self.runtime.cache_get(self.owner, work, self.request, self.fast, "policy"))
        self.assertFalse(self.runtime.cache_put(self.owner, work, self.request, self.fast, "policy", b"stale", expected_generation=generation))
        cooling = self.runtime.recurrence_guard(self.owner, work, self.request)
        self.assertEqual(guard["cache_generation"], cooling["cache_generation"])
        self.assertFalse(cooling["bypass"])
        self.assertTrue(self.runtime.cache_put(self.owner, work, self.request, self.fast, "policy", b"fresh", expected_generation=cooling["cache_generation"]))
        self.assertEqual(b"fresh", self.runtime.cache_get(self.owner, work, self.request, self.fast, "policy")["body"])
        self.now += 61
        self.assertFalse(self.runtime.recurrence_guard(self.owner, work, self.request)["bypass"])
        view = self.runtime.get_work(self.owner, work)
        self.assertEqual("unknown", view["outcome_evidence"])
        self.assertEqual([], view["observations"])

    def test_unrelated_interactive_request_breaks_consecutive_repeat_guard(self):
        work = self.work(task_type="diagnosis")
        self.runtime.bind_request_context(self.owner, work, self.request)
        for attempt, request, cached in (("one", self.request, False), ("two", self.request, True),
                                         ("other", {**self.request, "input": "Summarize another unrelated note"}, False)):
            self.runtime.reserve(self.owner, work, attempt, self.fast, "openai", 0 if cached else 10,
                request_pattern=self.runtime.pattern_for_request(request), cache_source="exact_answer_cache" if cached else None)
            self.runtime.settle(self.owner, attempt, 0 if cached else 5, status="cache_hit" if cached else "succeeded")
        self.assertFalse(self.runtime.recurrence_guard(self.owner, work, self.request)["bypass"])

    def test_concurrent_repeat_guard_advances_once_and_refuses_stale_replay_admission(self):
        work = self.work(task_type="diagnosis")
        self.runtime.bind_request_context(self.owner, work, self.request)
        pattern = self.runtime.pattern_for_request(self.request)
        for attempt, cached in (("one", False), ("two", True)):
            self.runtime.reserve(self.owner, work, attempt, self.fast, "openai", 0 if cached else 10,
                request_pattern=pattern, cache_source="exact_answer_cache" if cached else None)
            self.runtime.settle(self.owner, attempt, 0 if cached else 5, status="cache_hit" if cached else "succeeded")
        generation = self.runtime.cache_generation(self.owner, work)
        with ThreadPoolExecutor(max_workers=4) as pool:
            guards = list(pool.map(lambda _: self.runtime.recurrence_guard(self.owner, work, self.request), range(4)))
        self.assertEqual(1, sum(guard["bypass"] for guard in guards))
        self.assertEqual({generation + 1}, {guard["cache_generation"] for guard in guards})
        self.error("cache_generation_changed", lambda: self.runtime.reserve(self.owner, work, "stale", self.fast, "openai", 0,
            cache_source="exact_answer_cache", expected_cache_generation=generation))
        self.assertEqual(2, self.runtime.get_work(self.owner, work)["costs"]["attempts"])

    def test_timing_is_measured_separately_and_unknown_overhead_stays_unknown(self):
        work = self.work()
        self.runtime.reserve(self.owner, work, "one", self.fast, "openai", 100)
        self.runtime.settle(self.owner, "one", 80, latency_ms=40)
        self.assertIsNone(self.runtime.get_work(self.owner, work)["costs"]["gateway_overhead_ms"])
        self.runtime.record_timing(self.owner, "one", provider_ms=40, total_ms=55, stages={"routing": 5, "redaction": 3})
        costs = self.runtime.get_work(self.owner, work)["costs"]
        self.assertEqual((40, 55, 15), (costs["summed_provider_ms"], costs["gateway_wall_ms"], costs["gateway_overhead_ms"]))
        self.error("inconsistent_timing", lambda: self.runtime.record_timing(self.owner, "one", provider_ms=60, total_ms=55))
        self.error("timing_conflict", lambda: self.runtime.record_timing(self.owner, "one", provider_ms=40, total_ms=56))

    def test_verified_invoice_preserves_estimate_and_updates_budget_with_provenance(self):
        work = self.work()
        self.runtime.set_plan("company", "job", work, 100, "cost")
        self.runtime.reserve(self.owner, work, "one", self.fast, "openai", 60)
        self.runtime.settle(self.owner, "one", 50, latency_ms=20)
        self.error("unverified_cost_confirmation", lambda: self.runtime.confirm_cost(self.owner, "one", 70, "invoice-line"))
        self.runtime.confirm_cost(self.owner, "one", 70, "invoice-line", verified=True)
        view = self.runtime.get_work(self.owner, work)
        self.assertEqual(50, view["attempts"][0]["cost_microusd"])
        self.assertEqual(70, view["attempts"][0]["confirmed_cost_microusd"])
        self.assertEqual(30, view["plans"][0]["remaining_microusd"])
        self.assertTrue(view["costs"]["invoice_finality"])
        self.error("budget_exhausted", lambda: self.runtime.reserve(self.owner, work, "two", self.fast, "openai", 31))
        self.error("confirmation_conflict", lambda: self.runtime.confirm_cost(self.owner, "one", 71, "invoice-line", verified=True))
        self.error("attempt_not_found", lambda: self.runtime.confirm_cost(replace(self.owner, organization_id="other"), "one", 70, "invoice-line", verified=True))

    def test_confirmed_unknown_cost_releases_hold_but_does_not_invent_outcome(self):
        work = self.work()
        self.runtime.reserve(self.owner, work, "unknown", self.fast, "openai", 100)
        self.runtime.settle(self.owner, "unknown")
        self.runtime.confirm_cost(self.owner, "unknown", 60, "receipt-line", source="provider_receipt", verified=True)
        view = self.runtime.get_work(self.owner, work)
        self.assertEqual((60, 0), (view["costs"]["committed_microusd"], view["costs"]["uncertain_microusd"]))
        self.assertFalse(view["costs"]["invoice_finality"])
        self.assertEqual("unknown", view["attempts"][0]["state"])
        self.assertEqual("unknown", view["outcome_evidence"])

    def test_forecast_exposes_scenarios_coverage_and_unresolved_costs(self):
        self.runtime.set_plan("company", "workspace", "company", 1000, "cost")
        for day, amount in enumerate((10, 20, 30)):
            work = self.work()
            self.runtime.reserve(self.owner, work, "day-" + str(day), self.fast, "openai", amount)
            self.runtime.settle(self.owner, "day-" + str(day), amount)
            self.now += 86400
        plan = self.runtime.report(self.owner)["plans"][0]
        self.assertEqual(3, plan["forecast"]["observed_days"])
        self.assertLess(plan["forecast"]["lower_microusd"], plan["forecast"]["upper_microusd"])
        self.assertIn("estimated_provider_prices", plan["forecast"]["uncertainty"])
        unknown = self.work()
        self.runtime.reserve(self.owner, unknown, "pending", self.fast, "openai", 90)
        self.runtime.settle(self.owner, "pending")
        plan = self.runtime.report(self.owner)["plans"][0]
        self.assertIsNone(plan["forecast"]["point_microusd"])
        self.assertIn("unresolved_provider_cost", plan["forecast"]["uncertainty"])

    def test_existing_bound_provider_costs_are_visible_without_allocating_or_claiming_finality(self):
        from . import test_finance_account_reconciliation_cli as fixtures
        fixture = fixtures.FinanceAccountReconciliationCLITests(methodName="test_exact_binding_calculates_signed_variance_without_provider_access")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.start, fixture.end = "2026-10-07T00:00:00Z", "2026-10-08T00:00:00Z"
        receipt = fixture._seed_cost()
        self.runtime.config = fixture.config
        owner = fixture.config.identities_by_token[fixtures.ADMIN_TOKEN]
        work = self.runtime.create_work(owner, "org/repo")["work_id"]
        report = self.runtime.report(owner, administrator=True)
        evidence = report["provider_account_costs"]
        self.assertEqual("observed", evidence["status"])
        account = evidence["accounts"][0]
        self.assertEqual("1.25", account["provider_reported_amount"])
        self.assertFalse(account["invoice_finality"])
        self.assertEqual("customer_file", account["selected_snapshot_provenance"][0]["evidence_origin"])
        self.assertEqual(receipt.snapshot_id, account["selected_snapshot_provenance"][0]["snapshot_id"])
        self.assertEqual("not_allocated_to_jobs_or_requests", account["allocation"])
        self.assertEqual(0, self.runtime.get_work(owner, work)["costs"]["committed_microusd"])
        self.assertEqual([], self.runtime.report(owner)["provider_account_costs"]["accounts"])
        other = replace(owner, organization_id="other-company")
        self.assertEqual("provider_evidence_authority_not_configured", account_cost_view(fixture.config, other, self.now)["reason"])

    def test_provider_aggregate_unavailability_preserves_unknown_without_network_collection(self):
        called = []
        missing = account_cost_view(None, self.owner, self.now,
            repository_factory=lambda *args: called.append(True))
        self.assertEqual("unavailable", missing["status"])
        self.assertEqual([], called)
        self.assertFalse(missing["invoice_finality"])

    def test_readiness_requires_existing_exploration_accounting_without_recreating_it(self):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("DROP TABLE ai_work_exploration")
        self.error("storage_unavailable", self.runtime.verify_ready)
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name='ai_work_exploration'").fetchone())


if __name__ == "__main__":
    unittest.main()
