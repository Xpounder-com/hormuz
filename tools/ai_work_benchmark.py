#!/usr/bin/env python3
"""Reproduce bounded local work-ledger measurements without provider calls."""

from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
from time import perf_counter, time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hormuz.config import Identity, ModelRoute
from hormuz.work_runtime import WorkRuntime


def run() -> dict:
    identity = Identity("TOKEN", "local-test-only", "alice", "Alice", "team", "Team",
                        organization_id="benchmark-company")
    fast = ModelRoute("fast", "openai", "fast-v1", input_cost_per_million=2)
    cheap = ModelRoute("cheap", "openai", "cheap-v1", input_cost_per_million=1)
    now = time()
    with TemporaryDirectory(prefix="hormuz-work-benchmark-") as directory:
        runtime = WorkRuntime(Path(directory) / "benchmark.sqlite3", clock=lambda: now, minimum_samples=3)
        try:
            condition = "synthetic.completed.v1"
            request = {"messages": [{"role": "user", "content": "Summarize this synthetic benchmark record."}]}
            work = runtime.create_work(identity, "example/repository", task_type="benchmark",
                                       completion_condition=condition)["work_id"]
            context = runtime.bind_request_context(identity, work, request)
            shape = runtime._cohort_shape(context, runtime.shape_for_request(request))
            runtime.set_plan(identity.organization_id, "workspace", identity.organization_id, None, "cost")
            jobs, attempts, observations = [], [], []
            for index in range(9999):
                key, created = "synthetic-" + str(index), now - 10000 + index
                route = fast if index % 2 else cheap
                jobs.append((key, identity.organization_id, identity.actor_id, "example/repository",
                             "benchmark", "completed", created, created, created, "declared",
                             json.dumps(context["task_characteristics"]), context["context_signature"], condition))
                observations.append(("observation-" + key, identity.organization_id, identity.actor_id,
                                     key, "completed", "synthetic-benchmark", created, condition))
                for count in range(10):
                    attempts.append((identity.organization_id, identity.actor_id, key + "-" + str(count),
                                     key, route.alias, route.protocol, runtime._route_fingerprint(route), shape,
                                     "benchmark", "succeeded", 10, 8 if route == fast else 2, 10, created, created,
                                     context["context_signature"]))
            with closing(sqlite3.connect(runtime.path)) as connection, connection:
                connection.executemany(
                    "INSERT INTO ai_work_jobs(work_id,organization_id,actor_id,repository,task_type,state,created_at,updated_at,"
                    "completed_at,task_origin,task_characteristics,context_signature,completion_condition) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", jobs)
                connection.executemany(
                    "INSERT INTO ai_work_observations(observation_id,organization_id,actor_id,work_id,status,source,observed_at,completion_condition) "
                    "VALUES(?,?,?,?,?,?,?,?)", observations)
                connection.executemany(
                    "INSERT INTO ai_work_attempts(organization_id,actor_id,request_id,work_id,model,protocol,route_fingerprint,"
                    "request_shape,reason,state,reserved_microusd,cost_microusd,latency_ms,created_at,settled_at,context_signature) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", attempts)
            started = perf_counter()
            benchmark = runtime.refresh_benchmark(identity, work, "openai", request)
            build_ms = (perf_counter() - started) * 1000
            samples = []
            selected = set()
            for _ in range(500):
                started = perf_counter()
                result = runtime.choose_route(identity, work, [fast, cheap], fast, request)
                samples.append((perf_counter() - started) * 1000)
                selected.add(result["alias"])
            runtime.set_plan(identity.organization_id, "workspace", identity.organization_id, 2_000_000, "cost")
            runtime.set_plan(identity.organization_id, "repository", "example/repository", 1_000_000, "cost")
            runtime.set_plan(identity.organization_id, "job", work, 100, "cost")
            budget_samples, budget_selected = [], set()
            for _ in range(100):
                started = perf_counter()
                result = runtime.choose_route(identity, work, [fast, cheap], fast, request,
                                              max_costs={"fast": 20, "cheap": 10})
                budget_samples.append((perf_counter() - started) * 1000)
                budget_selected.add(result["alias"])
            started = perf_counter()
            report = runtime.report(identity)
            report_ms = (perf_counter() - started) * 1000
            ordered = sorted(samples)
            p95 = ordered[474]
            budget_ordered = sorted(budget_samples)
            return {
                "schema_id": "hormuz.ai-work-local-benchmark", "schema_version": 1,
                "measured_at": datetime.now(timezone.utc).isoformat(),
                "source_boundary": "working_tree_files_identified_by_sha256",
                "source_files": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                                 for name in ("hormuz/work_runtime.py", "hormuz/work_learning.py",
                                              "hormuz/work_accounting.py", "hormuz/work_provider_costs.py",
                                              "tools/ai_work_benchmark.py")},
                "fixture": "synthetic_local_sqlite_metadata", "provider_calls": 0,
                "cohort": {"task_origin": "declared", "task_type": "benchmark",
                           "completion_condition": condition, "request_shape": shape,
                           "context_signature": context["context_signature"]},
                "jobs": report["totals"]["works"], "attempts": report["totals"]["attempts"],
                "routing_samples": len(samples), "route_p50_ms": round(ordered[249], 6),
                "route_p95_ms": round(p95, 6), "route_max_ms": round(max(samples), 6),
                "routing_target_ms": 30, "routing_target_passed": p95 < 30,
                "selector_scope": "snapshot_selector_excludes_gateway_normalization_policy_egress_and_atomic_admission",
                "selected_models": sorted(selected), "expected_selection_passed": selected == {"cheap"},
                "budget_aware_selection": {"samples": len(budget_samples), "workspace_cap_microusd": 2_000_000,
                    "repository_cap_microusd": 1_000_000, "job_cap_microusd": 100,
                    "monthly_scope_relationship": "one_repository_workspace_and_repository_spend_are_identical",
                    "max_costs_microusd": {"fast": 20, "cheap": 10}, "route_p50_ms": round(budget_ordered[49], 6),
                    "route_p95_ms": round(budget_ordered[94], 6), "route_max_ms": round(max(budget_samples), 6),
                    "routing_target_ms": 30, "routing_target_passed": budget_ordered[94] < 30,
                    "selected_models": sorted(budget_selected), "expected_selection_passed": budget_selected == {"cheap"},
                    "scope": "snapshot_selector_plus_finite_work_budget_eligibility_excludes_gateway_normalization_policy_egress_and_atomic_admission"},
                "background_build_ms": round(build_ms, 6), "benchmark_episodes": benchmark["sample_count"],
                "report_ms": round(report_ms, 6), "report_json_bytes": len(json.dumps(report).encode()),
                "displayed_jobs": len(report["works"]), "total_jobs": report["totals"]["works"],
                "limitations": ["local_metadata_mechanics_only", "single_process_single_machine",
                                "not_production_load_or_provider_latency", "not_customer_savings_or_quality_evidence"],
            }
        finally:
            runtime.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Save the metadata-only measurement as JSON.")
    arguments = parser.parse_args()
    result = run()
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if result["routing_target_passed"] and result["expected_selection_passed"] and result["budget_aware_selection"]["routing_target_passed"] and result["budget_aware_selection"]["expected_selection_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
