from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from pathlib import Path
import os
import sqlite3
import tempfile
import threading
import unittest
from types import SimpleNamespace

from hormuz.config import Identity, ModelRoute
from hormuz.work_runtime import WorkRuntime, WorkRuntimeError


class WorkRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "work.sqlite3"
        self.now = 1_791_465_600.0
        self.runtime = WorkRuntime(self.path, clock=lambda: self.now, minimum_samples=3)
        self.alice = Identity("TOKEN", "secret", "alice", "Alice", "engineering", "Engineering", organization_id="company")
        self.bob = replace(self.alice, actor_id="bob", actor_name="Bob")
        self.other = replace(self.alice, organization_id="other-company")
        self.fast = ModelRoute("fast", "openai", "fast-v1", input_cost_per_million=2)
        self.cheap = ModelRoute("cheap", "openai", "cheap-v1", input_cost_per_million=1)

    def tearDown(self):
        self.runtime.close()
        self.directory.cleanup()

    def work(self, owner=None, repository="org/repo", **fields):
        return self.runtime.create_work(owner or self.alice, repository, context_revision="commit-1", **fields)["work_id"]

    def error(self, reason, callback):
        with self.assertRaises(WorkRuntimeError) as caught:
            callback()
        self.assertEqual(reason, caught.exception.reason)

    def test_owner_and_tenant_isolation_with_admin_tenant_report(self):
        alice_work = self.work()
        bob_work = self.work(self.bob)
        other_work = self.work(self.other)
        for owner in (self.bob, self.other):
            self.error("work_not_found", lambda: self.runtime.get_work(owner, alice_work))
            self.error("work_not_found", lambda: self.runtime.observe(owner, alice_work, "completed"))
            self.error("work_not_found", lambda: self.runtime.reserve(owner, alice_work, "attempt", self.fast, "openai", 10))
        self.assertEqual([alice_work], [work["work_id"] for work in self.runtime.list_work(self.alice)])
        admin = self.runtime.report(self.alice, administrator=True)
        self.assertEqual({alice_work, bob_work}, {work["work_id"] for work in admin["works"]})
        self.assertNotIn(other_work, {work["work_id"] for work in admin["works"]})

    def test_concurrent_instances_cannot_spend_same_remaining_budget(self):
        self.runtime.set_plan("company", "workspace", "company", 100, "cost")
        work = self.work()
        bob_work = self.work(self.bob)
        second = WorkRuntime(self.path, clock=lambda: self.now)
        def attempt(values):
            runtime, owner, work_id, request_id = values
            try:
                return runtime.reserve(owner, work_id, request_id, self.fast, "openai", 70)["state"]
            except WorkRuntimeError as error:
                return error.reason
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(attempt, ((self.runtime, self.alice, work, "a"), (second, self.bob, bob_work, "b"))))
        self.assertCountEqual(["pending", "budget_exhausted"], outcomes)
        plan = self.runtime.report(self.alice, administrator=True)["plans"][0]
        self.assertEqual(70, plan["pending_microusd"])
        self.assertEqual(30, plan["remaining_microusd"])

    def test_unknown_hold_survives_restart_and_no_zero_cost_is_invented(self):
        self.runtime.set_plan("company", "repository", "org/repo", 100, "speed")
        work = self.work()
        self.runtime.reserve(self.alice, work, "a", self.fast, "openai", 90)
        self.runtime.settle(self.alice, "a", status="succeeded", cost_microusd=None, latency_ms=42)
        restored = WorkRuntime(self.path, clock=lambda: self.now)
        state = restored.get_work(self.alice, work)
        self.assertEqual(90, state["costs"]["uncertain_microusd"])
        self.assertEqual(0, state["costs"]["committed_microusd"])
        self.assertIsNone(state["attempts"][0]["cost_microusd"])
        self.error("budget_exhausted", lambda: restored.reserve(self.alice, work, "b", self.fast, "openai", 20))
        restored.settle(self.alice, "a", cost_microusd=60, status="failed", latency_ms=42)
        self.assertEqual(0, restored.get_work(self.alice, work)["costs"]["uncertain_microusd"])
        self.assertEqual(60, restored.get_work(self.alice, work)["costs"]["committed_microusd"])

    def test_startup_reclassifies_previous_pending_hold_without_releasing_it(self):
        work = self.work()
        self.runtime.set_plan("company", "job", work, 100, "cost")
        self.runtime.reserve(self.alice, work, "pending", self.fast, "openai", 90)
        restarted = WorkRuntime(self.path, clock=lambda: self.now)
        view = restarted.get_work(self.alice, work)
        self.assertEqual(0, view["costs"]["pending_microusd"])
        self.assertEqual(90, view["costs"]["uncertain_microusd"])
        self.assertEqual(10, view["plans"][0]["remaining_microusd"])
        self.assertIsNone(view["attempts"][0]["cost_microusd"])

    def test_upstream_origin_change_invalidates_model_profile(self):
        self.runtime.config = SimpleNamespace(upstreams={"openai": SimpleNamespace(base_url="https://provider-a.example/v1")})
        for _ in range(3):
            self.episode(self.fast, 80, 10)
            self.episode(self.cheap, 20, 100)
        work = self.work(task_type="diagnosis")
        self.runtime.refresh_benchmark(self.alice, work, "openai")
        self.assertEqual("cheap", self.runtime.choose_route(self.alice, work, [self.fast, self.cheap], self.fast, {})["alias"])
        self.runtime.config.upstreams["openai"].base_url = "https://provider-b.example/v1"
        self.assertEqual("fast", self.runtime.choose_route(self.alice, work, [self.fast, self.cheap], self.fast, {})["alias"])

    def test_parent_budget_still_controls_job_continuation_and_version_conflicts(self):
        self.runtime.set_plan("company", "workspace", "company", 50, "cost")
        work = self.work()
        self.runtime.set_plan("company", "job", work, 10, "speed", expected_version=0)
        self.error("plan_conflict", lambda: self.runtime.set_plan("company", "job", work, 100, "cost", expected_version=0))
        self.error("budget_exhausted", lambda: self.runtime.reserve(self.alice, work, "a", self.fast, "openai", 20))
        self.runtime.act(self.alice, work, "approve_budget", budget_microusd=100, expected_version=1)
        self.error("budget_exhausted", lambda: self.runtime.reserve(self.alice, work, "b", self.fast, "openai", 60))
        self.assertEqual("paused", self.runtime.get_work(self.alice, work)["state"])

    def test_idempotency_and_settlement_cannot_reprice_history(self):
        work = self.work()
        first = self.runtime.reserve(self.alice, work, "a", self.fast, "openai", 50)
        repeated = self.runtime.reserve(self.alice, work, "a", self.fast, "openai", 50)
        self.assertFalse(first["idempotent_replay"])
        self.assertTrue(repeated["idempotent_replay"])
        self.error("request_conflict", lambda: self.runtime.reserve(self.alice, work, "a", self.fast, "openai", 51))
        self.runtime.settle(self.alice, "a", 30, latency_ms=20)
        self.runtime.settle(self.alice, "a", 30, latency_ms=20)
        self.error("settlement_conflict", lambda: self.runtime.settle(self.alice, "a", 31, latency_ms=20))

    def test_corrections_and_exact_recurrence_are_not_completion(self):
        work = self.work()
        pattern = self.runtime.pattern_for_request({"model": "fast", "input": "diagnose"})
        changed_model = self.runtime.pattern_for_request({"model": "cheap", "input": "diagnose"})
        self.assertEqual(pattern, changed_model)
        self.runtime.reserve(self.alice, work, "a", self.fast, "openai", 10, request_pattern=pattern)
        self.runtime.settle(self.alice, "a", 5, latency_ms=20)
        repeated = self.runtime.reserve(self.alice, work, "b", self.fast, "openai", 0, request_pattern=pattern, cache_source="exact_answer_cache")
        self.runtime.settle(self.alice, "b", status="cache_hit", latency_ms=1)
        self.assertEqual("possible_exact_recurrence_not_quality", repeated["repeat_signal"])
        retry = self.runtime.reserve(self.alice, work, "retry", self.fast, "openai", 10, request_pattern=pattern, retry_of="a")
        self.assertEqual("none", retry["repeat_signal"])
        self.assertEqual("unknown", self.runtime.get_work(self.alice, work)["outcome_evidence"])
        self.runtime.observe(self.alice, work, "completed", source="agent", reference="event-1")
        self.runtime.observe(self.alice, work, "completed", source="agent", reference="event-1")
        self.assertEqual(1, len(self.runtime.get_work(self.alice, work)["observations"]))
        self.runtime.observe(self.alice, work, "corrected", source="agent", reference="event-2")
        corrected = self.runtime.get_work(self.alice, work)
        self.assertEqual("active", corrected["state"])
        self.assertIsNone(corrected["completed_at"])
        self.assertIsNone(corrected["pinned_model"])

    def episode(self, route, cost, latency, correction=False, completed=True):
        work = self.work(task_type="diagnosis")
        self.runtime.reserve(self.alice, work, "request-" + work, route, "openai", cost)
        self.runtime.settle(self.alice, "request-" + work, cost, latency_ms=latency)
        if correction:
            self.runtime.observe(self.alice, work, "corrected")
        if completed:
            self.runtime.observe(self.alice, work, "completed", source="agent")
        return work

    def test_local_cost_speed_preferences_and_route_drift(self):
        for _ in range(3):
            self.episode(self.fast, 80, 10)
            self.episode(self.cheap, 20, 100)
        work = self.work(task_type="diagnosis")
        self.runtime.refresh_benchmark(self.alice, work, "openai")
        selection = self.runtime.choose_route(self.alice, work, [self.fast, self.cheap], self.fast, {"input": "new"})
        self.assertEqual("cheap", selection["alias"])
        self.assertEqual("observational", selection["sample_confidence"])
        self.runtime.set_plan("company", "job", work, 1000, "speed")
        self.assertEqual("fast", self.runtime.choose_route(self.alice, work, [self.fast, self.cheap], self.fast, {"input": "new"})["alias"])
        changed = replace(self.cheap, upstream_model="cheap-v2")
        self.runtime.set_plan("company", "job", work, 1000, "cost")
        self.assertEqual("fast", self.runtime.choose_route(self.alice, work, [self.fast, changed], self.fast, {"input": "new"})["alias"])

    def test_unknown_and_cached_only_episodes_do_not_invent_model_success(self):
        for index in range(3):
            self.episode(self.fast, 80, 10)
            self.episode(self.cheap, 1, 1, completed=False)
            cached = self.work(task_type="diagnosis")
            self.runtime.reserve(self.alice, cached, "cached-" + str(index), self.cheap, "openai", 0, cache_source="exact_answer_cache")
            self.runtime.settle(self.alice, "cached-" + str(index), status="cache_hit", latency_ms=1)
            self.runtime.observe(self.alice, cached, "completed")
        work = self.work(task_type="diagnosis")
        self.runtime.refresh_benchmark(self.alice, work, "openai")
        self.assertEqual("fast", self.runtime.choose_route(self.alice, work, [self.fast, self.cheap], self.fast, {"input": "new"})["alias"])

    def test_cost_guardrail_and_quality_objective_observe_correction(self):
        for _ in range(3):
            self.episode(self.fast, 80, 20, correction=True)
            self.episode(self.cheap, 90, 50)
        work = self.work(task_type="diagnosis")
        self.runtime.refresh_benchmark(self.alice, work, "openai")
        self.runtime.set_plan("company", "job", work, 1000, "quality")
        self.assertEqual("cheap", self.runtime.choose_route(self.alice, work, [self.fast, self.cheap], self.fast, {})["alias"])

    def test_session_and_tool_compatibility_preserve_approved_route(self):
        work = self.work()
        self.runtime.reserve(self.alice, work, "a", self.cheap, "openai", 10)
        session = self.runtime.choose_route(self.alice, work, [self.fast, self.cheap], self.fast, {"previous_response_id": "provider-state"})
        self.assertEqual(("cheap", "session_affinity"), (session["alias"], session["reason"]))
        self.error("session_route_not_eligible", lambda: self.runtime.choose_route(self.alice, work, [self.fast], self.fast, {"previous_response_id": "provider-state"}))
        tools = self.runtime.choose_route(self.alice, work, [self.fast, self.cheap], self.fast, {"tools": [{"type": "function"}]})
        self.assertEqual("tool_compatibility_baseline", tools["reason"])

    def test_initial_tool_routing_requires_declared_capabilities_and_matching_schema(self):
        tools = {"tools": [{"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}}]}
        shape = self.runtime.shape_for_request(tools)
        self.runtime.config = SimpleNamespace(ai_work=SimpleNamespace(tool_capable_aliases=("fast", "cheap")))
        for route, cost in ((self.fast, 80), (self.cheap, 20)):
            for index in range(3):
                job = self.work(task_type="tool-work")
                attempt = "request-" + job
                self.runtime.reserve(self.alice, job, attempt, route, "openai", cost, request_shape=shape)
                self.runtime.settle(self.alice, attempt, cost, latency_ms=20)
                self.runtime.observe(self.alice, job, "completed", source="agent")
        job = self.work(task_type="tool-work")
        self.runtime.refresh_benchmark(self.alice, job, "openai", tools)
        selection = self.runtime.choose_route(self.alice, job, [self.fast, self.cheap], self.fast, tools)
        self.assertEqual("cheap", selection["alias"])
        changed_tools = {"tools": [{"type": "function", "function": {"name": "write"}}]}
        self.assertEqual("fast", self.runtime.choose_route(self.alice, job, [self.fast, self.cheap], self.fast, changed_tools)["alias"])
        self.runtime.config.ai_work.tool_capable_aliases = ("fast",)
        self.assertEqual("fast", self.runtime.choose_route(self.alice, job, [self.fast, self.cheap], self.fast, tools)["alias"])

    def test_request_path_uses_snapshot_instead_of_building_profiles(self):
        work = self.work(task_type="diagnosis")
        self.runtime.refresh_benchmark(self.alice, work, "openai")
        original = self.runtime._profiles
        self.runtime._profiles = lambda *args: self.fail("profile build on request path")
        try:
            result = self.runtime.choose_route(self.alice, work, [self.fast, self.cheap], self.fast, {})
            self.assertEqual("fast", result["alias"])
        finally:
            self.runtime._profiles = original

    def test_monthly_scope_rolls_over_but_job_hold_remains(self):
        work = self.work()
        self.runtime.set_plan("company", "workspace", "company", 100, "cost")
        self.runtime.set_plan("company", "job", work, 100, "cost")
        self.runtime.reserve(self.alice, work, "a", self.fast, "openai", 90)
        self.now += 32 * 86400
        plans = {plan["scope_type"]: plan for plan in self.runtime.get_work(self.alice, work)["plans"]}
        self.assertEqual(100, plans["workspace"]["remaining_microusd"])
        self.assertEqual(10, plans["job"]["remaining_microusd"])

    def test_input_validation_and_ledger_retains_no_prompts(self):
        self.error("invalid_amount", lambda: self.runtime.set_plan("company", "workspace", "company", True, "cost"))
        self.error("invalid_objective", lambda: self.runtime.set_plan("company", "workspace", "company", 1, {}))
        work = self.work()
        secret = "unique prompt not persisted"
        pattern = self.runtime.pattern_for_request({"input": secret})
        self.runtime.reserve(self.alice, work, "a", self.fast, "openai", 10, request_pattern=pattern)
        with closing(sqlite3.connect(self.path)) as connection:
            dump = "\n".join(connection.iterdump())
        self.assertNotIn(secret, dump)
        self.assertNotIn(pattern, str(self.runtime.get_work(self.alice, work)))

    def test_database_hardlinks_rejected_without_mutating_source_file(self):
        source = Path(self.directory.name) / "important-file"
        source.write_bytes(b"preserve exactly")
        target = Path(self.directory.name) / "linked.sqlite3"
        os.link(source, target)
        self.error("unsafe_database_path", lambda: WorkRuntime(target))
        self.assertEqual(b"preserve exactly", source.read_bytes())

    def test_model_protocol_must_match_attempt_protocol(self):
        work = self.work()
        self.error("model_protocol_mismatch", lambda: self.runtime.reserve(
            self.alice, work, "wrong-protocol", self.fast, "anthropic", 10))
        self.assertEqual([], self.runtime.get_work(self.alice, work)["attempts"])

    def test_readiness_and_operations_do_not_recreate_missing_database(self):
        self.assertEqual({"status": "available", "schema_version": 1}, self.runtime.verify_ready())
        moved = self.path.with_name("moved.sqlite3")
        self.path.rename(moved)
        self.error("storage_unavailable", self.runtime.verify_ready)
        self.error("storage_unavailable", lambda: self.runtime.report(self.alice))
        self.assertFalse(self.path.exists())
        moved.rename(self.path)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("DROP TABLE ai_work_observations")
        self.error("storage_unavailable", self.runtime.verify_ready)

    def test_real_feedback_refreshes_fresh_snapshot_without_manual_replay(self):
        work = self.work(task_type="diagnosis")
        self.runtime.refresh_benchmark(self.alice, work, "openai")
        self.assertEqual("fast", self.runtime.choose_route(self.alice, work, [self.fast, self.cheap], self.fast, {})["alias"])
        ready = threading.Event()
        original = self.runtime._save_profiles
        def save(key, profiles, version):
            stored = original(key, profiles, version)
            if stored and all(profiles.get((route.alias, self.runtime._route_fingerprint(route)), {}).get("samples", 0) >= 3
                              for route in (self.fast, self.cheap)):
                ready.set()
            return stored
        self.runtime._save_profiles = save
        for _ in range(3):
            self.episode(self.fast, 80, 10)
            self.episode(self.cheap, 20, 100)
        self.assertTrue(ready.wait(timeout=2), "committed workflow evidence was not refreshed")
        self.assertEqual("cheap", self.runtime.choose_route(self.alice, work, [self.fast, self.cheap], self.fast, {})["alias"])

    def test_correction_retires_snapshot_and_prevents_stale_worker_restore(self):
        original_queue = self.runtime._queue_profile_refresh
        self.runtime._queue_profile_refresh = lambda *args: None
        try:
            cheap_episode = None
            for _ in range(3):
                self.episode(self.fast, 80, 10)
                cheap_episode = self.episode(self.cheap, 20, 100)
            work = self.work(task_type="diagnosis")
            self.runtime.refresh_benchmark(self.alice, work, "openai")
            key = ("company", "org/repo", "diagnosis", "openai", "plain")
            old_profiles = self.runtime._profile_cache[key]["profiles"]
            old_version = self.runtime._profile_versions[key]
            self.assertEqual("cheap", self.runtime.choose_route(self.alice, work, [self.fast, self.cheap], self.fast, {})["alias"])
            self.runtime.observe(self.alice, cheap_episode, "corrected", source="workflow")
            self.assertFalse(self.runtime._save_profiles(key, old_profiles, old_version))
            self.assertEqual("fast", self.runtime.choose_route(self.alice, work, [self.fast, self.cheap], self.fast, {})["alias"])
            self.runtime.refresh_benchmark(self.alice, work, "openai")
            self.assertEqual("fast", self.runtime.choose_route(self.alice, work, [self.fast, self.cheap], self.fast, {})["alias"])
        finally:
            self.runtime._queue_profile_refresh = original_queue

    def test_report_bounds_display_without_truncating_scope_totals_or_owner_detail(self):
        old_work = self.work()
        self.runtime.set_plan("company", "workspace", "company", 1000, "cost")
        for index in range(8):
            self.runtime.reserve(self.alice, old_work, "attempt-" + str(index), self.fast, "openai", 2)
            self.runtime.settle(self.alice, "attempt-" + str(index), 1)
        for index in range(8):
            self.runtime.observe(self.alice, old_work, "completed", reference="completion-" + str(index))
        for _ in range(105):
            self.now += 1
            self.work()
        report = self.runtime.report(self.alice)
        self.assertEqual(100, len(report["works"]))
        self.assertEqual(106, report["totals"]["works"])
        self.assertEqual(1, report["totals"]["completed"])
        self.assertEqual(8, report["totals"]["committed_microusd"])
        self.assertTrue(report["display"]["jobs_truncated"])
        self.assertNotIn(old_work, {job["work_id"] for job in report["works"]})
        detail = self.runtime.get_work(self.alice, old_work)
        self.assertEqual(8, len(detail["attempts"]))
        self.assertEqual(8, len(detail["observations"]))
        self.now += 1
        newest = self.work()
        for index in range(8):
            self.runtime.reserve(self.alice, newest, "new-" + str(index), self.fast, "openai", 2)
            self.runtime.settle(self.alice, "new-" + str(index), 1)
        summary = self.runtime.report(self.alice)["works"][0]
        self.assertEqual(5, len(summary["attempts"]))
        self.assertEqual(5, summary["attempts_limit"])
        self.assertTrue(summary["attempts_truncated"])

    def test_endpoint_capability_shapes_partition_local_evidence(self):
        self.assertNotEqual(self.runtime.shape_for_request({"_hormuz_endpoint": "/v1/responses"}),
                            self.runtime.shape_for_request({"_hormuz_endpoint": "/v1/chat/completions"}))

    def test_passive_observations_cannot_resume_customer_paused_or_stopped_work(self):
        work = self.work()
        self.runtime.act(self.alice, work, "pause")
        paused = self.runtime.observe(self.alice, work, "corrected", source="workflow")
        self.assertEqual("paused", paused["state"])
        self.assertEqual("customer_paused", paused["pause_reason"])
        self.error("work_not_active", lambda: self.runtime.reserve(self.alice, work, "a", self.fast, "openai", 1))
        self.runtime.act(self.alice, work, "stop")
        self.runtime.observe(self.alice, work, "reopened", source="workflow")
        stopped = self.runtime.observe(self.alice, work, "unknown", source="workflow")
        self.assertEqual("canceled", stopped["state"])
        self.error("work_not_active", lambda: self.runtime.reserve(self.alice, work, "b", self.fast, "openai", 1))
        self.runtime.act(self.alice, work, "resume")
        self.assertEqual("pending", self.runtime.reserve(self.alice, work, "c", self.fast, "openai", 1)["state"])

    def test_retained_previous_month_history_has_no_lifetime_admission_ceiling(self):
        previous_month = self.now - 32 * 86400
        self.runtime.set_plan("company", "workspace", "company", 100, "cost")
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.executemany(
                "INSERT INTO ai_work_jobs(work_id,organization_id,actor_id,repository,task_type,state,created_at,updated_at,completed_at) "
                "VALUES(?,'company','alice','history/repository','history','completed',?,?,?)",
                (("history-" + str(index), previous_month, previous_month, previous_month) for index in range(10_000)))
            connection.executemany(
                "INSERT INTO ai_work_plans(organization_id,scope_type,scope_id,version,budget_microusd,objective,updated_at) VALUES('company','job',?,1,100,'cost',?)",
                (("history-" + str(index), previous_month) for index in range(10_000)))
            connection.executemany(
                "INSERT INTO ai_work_attempts(organization_id,actor_id,request_id,work_id,model,protocol,reason,state,reserved_microusd,cost_microusd,created_at,settled_at) "
                "VALUES('company','alice',?,'history-0','fast','openai','history','succeeded',1,1,?,?)",
                (("history-attempt-" + str(index), previous_month, previous_month) for index in range(100_000)))
            connection.executemany(
                "INSERT INTO ai_work_observations(observation_id,organization_id,actor_id,work_id,status,source,reference,observed_at) VALUES(?,'company','alice','history-0','completed','history',NULL,?)",
                (("history-observation-" + str(index), previous_month) for index in range(100_000)))
        work = self.work()
        self.runtime.set_plan("company", "repository", "org/repo", 100, "speed")
        self.runtime.set_plan("company", "job", work, 40, "cost")
        self.runtime.reserve(self.alice, work, "this-month", self.fast, "openai", 30)
        self.runtime.settle(self.alice, "this-month", 20, latency_ms=1)
        self.runtime.observe(self.alice, work, "completed", source="workflow")
        approval_work = self.work()
        self.runtime.act(self.alice, approval_work, "approve_budget", budget_microusd=50)
        self.runtime.reserve(self.alice, approval_work, "still-capped", self.fast, "openai", 50)
        self.runtime.settle(self.alice, "still-capped", 50, latency_ms=1)
        self.error("budget_exhausted", lambda: self.runtime.reserve(
            self.alice, approval_work, "over-parent", self.fast, "openai", 31))
        report = self.runtime.report(self.alice)
        self.assertEqual(10_002, report["totals"]["works"])
        self.assertEqual(100_070, report["totals"]["committed_microusd"])
        workspace = next(plan for plan in report["plans"] if plan["scope_type"] == "workspace")
        self.assertEqual(30, workspace["remaining_microusd"])
        self.assertEqual(100, len(report["works"]))
        self.assertTrue(report["display"]["jobs_truncated"])
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(100_001, connection.execute("SELECT COUNT(*) FROM ai_work_observations").fetchone()[0])
            self.assertEqual(100_000, connection.execute("SELECT COUNT(*) FROM ai_work_attempts WHERE created_at=?", (previous_month,)).fetchone()[0])


class WorkCacheTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "work.sqlite3"
        self.now = 1_791_465_600.0
        self.runtime = WorkRuntime(self.path, cache_enabled=True, clock=lambda: self.now, cache_max_entries=2)
        self.owner = Identity("TOKEN", "secret", "alice", "Alice", "team", "Team", organization_id="company")
        self.work = self.runtime.create_work(self.owner, "repo", context_revision="commit-1")["work_id"]
        self.route = ModelRoute("fast", "openai", "fast-v1")
        self.request = {"model": "fast", "input": "count three", "temperature": 0, "stream": False}

    def tearDown(self):
        self.directory.cleanup()

    def put(self, request=None):
        return self.runtime.cache_put(self.owner, self.work, request or self.request, self.route, "policy-1", b'{"answer":"three"}',
            ttl_seconds=30, expected_generation=self.runtime.cache_generation(self.owner, self.work))

    def get(self, request=None, route=None, policy="policy-1"):
        return self.runtime.cache_get(self.owner, self.work, request or self.request, route or self.route, policy)

    def test_exact_hit_context_model_policy_and_tenant_boundaries(self):
        self.assertTrue(self.put())
        self.assertEqual(b'{"answer":"three"}', self.get()["body"])
        self.assertIsNone(self.get({**self.request, "input": "count four"}))
        self.assertIsNone(self.get(policy="policy-2"))
        self.assertIsNone(self.get(route=replace(self.route, upstream_model="fast-v2")))
        other_work = self.runtime.create_work(self.owner, "repo", context_revision="commit-2")["work_id"]
        self.assertIsNone(self.runtime.cache_get(self.owner, other_work, self.request, self.route, "policy-1"))
        with self.assertRaises(WorkRuntimeError):
            self.runtime.cache_get(replace(self.owner, organization_id="other"), self.work, self.request, self.route, "policy-1")

    def test_correction_cross_instance_and_ttl_invalidation(self):
        self.put()
        other = WorkRuntime(self.path, clock=lambda: self.now)
        other.observe(self.owner, self.work, "corrected")
        self.assertIsNone(self.get())
        self.put()
        self.now += 31
        self.assertIsNone(self.get())

    def test_content_is_memory_only_bounded_and_default_off(self):
        for index in range(3):
            self.put({**self.request, "input": str(index)})
        self.assertIsNone(self.get({**self.request, "input": "0"}))
        self.assertIsNotNone(self.get({**self.request, "input": "2"}))
        restarted = WorkRuntime(self.path, cache_enabled=True)
        self.assertIsNone(restarted.cache_get(self.owner, self.work, self.request, self.route, "policy-1"))
        off = WorkRuntime(self.path)
        self.assertFalse(off.cache_put(self.owner, self.work, self.request, self.route, "policy-1", b"answer"))
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertNotIn('"answer":"three"', "\n".join(connection.iterdump()))

    def test_stream_tool_state_and_non_deterministic_requests_never_hit(self):
        for extra in ({"stream": True}, {"tools": [{"type": "function"}]}, {"temperature": 1},
                      {"previous_response_id": "state"}, {"messages": [{"role": "tool", "content": "result"}]},
                      {"store": True}, {"background": True}):
            self.assertFalse(self.put({**self.request, **extra}))

    def test_inflight_answer_cannot_reseed_cache_after_correction_or_reopen(self):
        for status in ("corrected", "reopened"):
            generation = self.runtime.cache_generation(self.owner, self.work)
            request_id = "inflight-" + status
            self.runtime.reserve(self.owner, self.work, request_id, self.route, "openai", 10)
            self.runtime.observe(self.owner, self.work, status, source="workflow")
            self.runtime.settle(self.owner, request_id, 5, latency_ms=10)
            self.assertFalse(self.runtime.cache_put(self.owner, self.work, self.request, self.route,
                "policy-1", b"old answer", expected_generation=generation))
            self.assertIsNone(self.get())
        current = self.runtime.cache_generation(self.owner, self.work)
        self.assertTrue(self.runtime.cache_put(self.owner, self.work, self.request, self.route,
            "policy-1", b"fresh answer", expected_generation=current))
        self.assertEqual(b"fresh answer", self.get()["body"])

    def test_insertion_requires_an_explicit_dispatch_generation(self):
        self.assertFalse(self.runtime.cache_put(self.owner, self.work, self.request, self.route, "policy-1", b"old answer"))
        self.assertIsNone(self.get())

    def test_cache_replay_admission_atomically_checks_the_lookup_generation(self):
        self.put()
        entry = self.get()
        self.runtime.observe(self.owner, self.work, "corrected", source="workflow")
        with self.assertRaises(WorkRuntimeError) as caught:
            self.runtime.reserve(self.owner, self.work, "replay", self.route, "openai", 0,
                cache_source="exact_answer_cache", expected_cache_generation=entry["cache_generation"])
        self.assertEqual("cache_generation_changed", caught.exception.reason)
        self.assertEqual(0, self.runtime.get_work(self.owner, self.work)["costs"]["attempts"])

    def test_correction_during_cache_key_calculation_is_rechecked_at_insertion(self):
        original = self.runtime._cache_identity
        def racing_identity(*args, **kwargs):
            result = original(*args, **kwargs)
            self.runtime.observe(self.owner, self.work, "corrected", source="workflow")
            return result
        self.runtime._cache_identity = racing_identity
        self.assertFalse(self.put())
        self.runtime._cache_identity = original
        self.assertIsNone(self.get())


if __name__ == "__main__":
    unittest.main()
