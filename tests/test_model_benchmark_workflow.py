"""Personal benchmarks through the public CLI and connected router workflow."""
import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import threading
import unittest

from tests import test_execution_destination_workflow as workflow
from tests import test_connected_client_lifetime_workflow as lifetime_workflow
from hormuz.contracts import validate_contract


class ModelBenchmarkWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.fixture = workflow.ExecutionDestinationWorkflowTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root.resolve()
        self.config_path = self.root / "gateway.json"
        self.suite_path = self.root / "my-work.json"
        self.report_path = self.root / "scorecard.json"
        self.environment = {**os.environ, **self.fixture.environment,
                            "HORMUZ_HISTORY_HOME": str(self.root / "history")}

    def command(self, *arguments, expected=0, without_resource=False):
        prefix = [sys.executable, "-m", "hormuz"]
        if without_resource:
            prefix = [sys.executable, "-c", "import sys; sys.modules['resource']=None; from hormuz.cli import main; raise SystemExit(main(sys.argv[1:]))"]
        result = subprocess.run([*prefix, "--config", str(self.config_path),
                                 "benchmark", *arguments], env=self.environment,
                                capture_output=True, text=True, timeout=25)
        self.assertEqual(result.returncode, expected, result.stderr or result.stdout)
        return json.loads(result.stdout)

    def suite(self):
        return {"schema_id": "hormuz.benchmark-suite", "schema_version": 1, "name": "my-work",
                "targets": {"objective": "quality", "min_pass_rate": 1, "min_cases": 2},
                "cases": [
                    {"id": "seven", "task_type": "summarization", "partition": "held_out",
                     "request": {"input": "Summarize the count as a digit: seven PRIVATE_TASK boxes.",
                                 "max_output_tokens": 8},
                     "checks": [{"metric": "correctness", "kind": "text_equals", "value": "7"}]},
                    {"id": "nine", "task_type": "summarization", "partition": "held_out",
                     "request": {"input": "Summarize the count as a digit: nine PRIVATE_TASK boxes.",
                                 "max_output_tokens": 8},
                     "checks": [{"metric": "correctness", "kind": "text_equals", "value": "9"}]},
                ]}

    def run_benchmark(self, *models, expected=0, **options):
        self.suite_path.write_text(json.dumps(options.pop("suite", self.suite())))
        return self.command("run", "--suite", str(self.suite_path), "--models", *models,
                            "--output", str(self.report_path), expected=expected,
                            *options.pop("arguments", ()))

    def two_local_configuration(self, first, second):
        value = self.fixture.admission_configuration(first, first)
        value["upstreams"]["second-local"] = {"protocol": "openai", "base_url": second,
            "locality": "local", "authentication": "none"}
        value["model_routes"]["local-better"] = {**value["model_routes"]["local-code"],
            "destination": "second-local", "upstream_model": "fixture-second-model"}
        value["route_admission"]["local_routes"]["local-better"] = dict(value["route_admission"]["local_routes"]["local-code"])
        value["policies"]["organization"]["allowed_models"].append("local-better")
        return value

    def test_custom_benchmark_counts_wrong_answers_as_quality_failures(self):
        with workflow.provider("7") as (endpoint, _):
            value = self.fixture.admission_configuration(endpoint, endpoint)
            with self.fixture.connected_client(value):
                report = self.run_benchmark("local-code")
            metrics = report["models"][0]["metrics"]
            self.assertEqual((metrics["attempts"], metrics["completed"], metrics["passed"],
                              metrics["task_success_rate"]), (2, 2, 1, 0.5))

    def test_a_completed_reply_with_a_provider_error_cannot_meet_a_quality_target(self):
        suite = self.suite()
        suite["cases"] = suite["cases"][:1]
        suite["targets"]["min_cases"] = 1
        with workflow.provider("7", response_fields={"error": {"code": "provider_failure"}}) as (endpoint, _):
            with self.fixture.connected_client(self.fixture.admission_configuration(endpoint, endpoint)):
                report = self.run_benchmark("local-code", suite=suite)
        model = report["models"][0]
        self.assertEqual((model["metrics"]["completed"], model["metrics"]["passed"],
                          model["workloads"][0]["target_met"], model["observations"][0]["error"]),
                         (0, 0, False, "incomplete_response"))

    def test_provider_model_identity_must_match_before_scores_or_prices_are_certified(self):
        suite = self.suite()
        suite["cases"] = suite["cases"][:1]
        suite["targets"]["min_cases"] = 1
        for model_name in ("unexpected-model", None):
            with self.subTest(model=model_name):
                self.report_path = self.root / f"identity-{model_name}.json"
                with workflow.provider("7", response_fields={"model": model_name}) as (endpoint, _):
                    value = self.fixture.admission_configuration(endpoint, endpoint)
                    value["model_routes"]["local-code"].update(input_cost_per_million=1, output_cost_per_million=1)
                    with self.fixture.connected_client(value):
                        report = self.run_benchmark("local-code", suite=suite)
                model = report["models"][0]
                self.assertEqual((model["metrics"]["completed"], model["workloads"][0]["target_met"],
                                  model["observations"][0]["error"], model["metrics"]["mean_cost_microusd"]),
                                 (0, False, "execution_mismatch", None))

    def test_provider_output_over_the_forwarded_budget_is_failed_evidence(self):
        suite = self.suite()
        suite["cases"] = suite["cases"][:1]
        suite["targets"]["min_cases"] = 1
        with workflow.provider("7", response_fields={"usage": {"input_tokens": 4, "output_tokens": 9}}) as (endpoint, received):
            with self.fixture.connected_client(self.fixture.admission_configuration(endpoint, endpoint)):
                report = self.run_benchmark("local-code", suite=suite)
        model = report["models"][0]
        observation = model["observations"][0]
        self.assertEqual((received[0]["body"]["max_output_tokens"], observation["usage"]["output_tokens"],
                          model["metrics"]["completed"], model["workloads"][0]["target_met"], observation["error"]),
                         (8, 9, 0, False, "execution_mismatch"))
        self.assertEqual(self.command("show", "--scorecard", str(self.report_path)), report)

    def test_saved_scorecard_can_be_reviewed_offline_without_retaining_task_text(self):
        with workflow.provider("7") as (endpoint, _):
            with self.fixture.connected_client(self.fixture.admission_configuration(endpoint, endpoint)):
                report = self.run_benchmark("local-code")
        self.config_path.unlink()
        reviewed = self.command("show", "--scorecard", str(self.report_path))
        self.assertEqual(reviewed, report)
        self.assertNotIn("PRIVATE_TASK", self.report_path.read_text())
        self.assertEqual(self.report_path.stat().st_mode & 0o777, 0o600)

    def test_scorecard_reports_observed_metrics_and_keeps_unknown_cost_and_memory_visible(self):
        with workflow.provider("7") as (endpoint, _):
            with self.fixture.connected_client(self.fixture.admission_configuration(endpoint, endpoint)):
                report = self.run_benchmark("local-code")
        metrics = report["models"][0]["metrics"]
        self.assertEqual((metrics["input_tokens"], metrics["output_tokens"],
                          metrics["error_rate"], metrics["check_pass_rates"],
                          metrics["mean_cost_microusd"], metrics["ttft_ms"]["p50"],
                          report["resources"]["model_peak_rss_bytes"]),
                         (8, 4, 0, {"correctness": 0.5}, None, None, None))
        self.assertGreater(metrics["latency_ms"]["p50"], 0)
        self.assertGreaterEqual(metrics["latency_ms"]["p95"], metrics["latency_ms"]["p50"])
        self.assertGreater(metrics["output_tokens_per_second"], 0)
        self.assertGreater(report["resources"]["runner_peak_rss_bytes"], 0)

    def test_request_bounds_measure_the_bytes_delivered_to_the_model(self):
        suite = self.suite()
        suite["cases"] = suite["cases"][:1]
        suite["targets"]["min_cases"] = 1
        for accounting in (False, True):
            with self.subTest(accounting=accounting):
                self.report_path = self.root / f"wire-scorecard-{accounting}.json"
                with workflow.provider("7") as (endpoint, received):
                    value = self.fixture.admission_configuration(endpoint, endpoint)
                    if accounting:
                        value["ai_work"] = {"enabled": True, "database": str(self.root / "work.sqlite3")}
                    with self.fixture.connected_client(value):
                        report = self.run_benchmark("local-code", suite=suite)
                model = report["models"][0]
                self.assertEqual((model["observations"][0]["request_bytes"], model["workloads"][0]["request_bytes_max"]),
                                 (received[0]["wire_bytes"], received[0]["wire_bytes"]))

    def test_unavailable_model_is_reported_without_omitting_failures_or_retrying(self):
        with workflow.provider("7", status=503) as (endpoint, received):
            with self.fixture.connected_client(self.fixture.admission_configuration(endpoint, endpoint)):
                report = self.run_benchmark("local-code")
        model = report["models"][0]
        self.assertEqual((model["metrics"]["attempts"], model["metrics"]["completed"],
                          model["metrics"]["task_success_rate"], model["metrics"]["error_rate"],
                          [row["error"] for row in model["observations"]], len(received)),
                         (2, 0, 0, 1, ["http_503", "http_503"], 2))

    def test_capacity_rejection_stops_probes_even_without_a_valid_json_body(self):
        for status in (429, 529):
            for body in (b"", b"capacity unavailable"):
                with self.subTest(status=status, body=body):
                    self.report_path = self.root / f"capacity-{status}-{len(body)}.json"
                    with workflow.provider("7", status=status, response_body=body) as (endpoint, received):
                        value = self.fixture.admission_configuration(endpoint, endpoint)
                        with self.fixture.connected_client(value):
                            report = self.run_benchmark("local-code")
                    observations = report["models"][0]["observations"]
                    self.assertEqual(([row["error"] for row in observations], len(received),
                                      observations[1]["latency_ms"],
                                      report["models"][0]["metrics"]["latency_ms"]["samples"]),
                                     ([f"http_{status}", "capacity_rejection_not_run"], 1, None, 1))

    def test_custom_metrics_check_structured_answers_and_tool_arguments_against_references(self):
        answer = '{"count":7}'
        tool = {"type": "function_call", "call_id": "call-one", "name": "lookup_count",
                "arguments": '{"item":"boxes"}'}
        output = [{"type": "message", "content": [{"type": "output_text", "text": answer}]}, tool]
        suite = self.suite()
        suite["cases"][0]["checks"] = [
            {"metric": "domain_accuracy", "kind": "json_equals", "value": {"count": 7}},
            {"metric": "required_terms", "kind": "text_contains_all", "value": ["count", "7"]},
            {"metric": "tool_arguments", "kind": "tool_call", "value": {"name": "lookup_count", "arguments": {"item": "boxes"}}}]
        suite["cases"][1]["checks"] = [
            {"metric": "boolean_count", "kind": "json_equals", "value": {"count": True}},
            {"metric": "tool_accuracy", "kind": "tool_call", "value": {"name": "lookup_count", "arguments": {"item": "wrong"}}}]
        with workflow.provider(answer, response_fields={"output": output}) as (endpoint, _):
            with self.fixture.connected_client(self.fixture.admission_configuration(endpoint, endpoint)):
                report = self.run_benchmark("local-code", suite=suite)
        model = report["models"][0]
        self.assertEqual(model["metrics"]["check_pass_rates"],
                         {"domain_accuracy": 1, "required_terms": 1, "tool_arguments": 1,
                          "boolean_count": 0, "tool_accuracy": 0})
        self.assertEqual((model["metrics"]["completed"], model["metrics"]["passed"],
                          model["workloads"][0]["target_met"]), (2, 1, False))
        self.assertEqual([row["passed"] for row in model["observations"]], [True, False])

    def test_user_can_create_an_editable_suite_without_a_router_or_overwriting_work(self):
        suite = self.command("init", "--output", str(self.suite_path))
        self.assertEqual(json.loads(self.suite_path.read_text()), suite)
        self.assertEqual((suite["schema_id"], suite["schema_version"]), ("hormuz.benchmark-suite", 1))
        self.assertTrue(suite["cases"])
        failure = self.command("init", "--output", str(self.suite_path), expected=2)
        self.assertEqual(failure["error"]["code"], "benchmark_output_exists")
        self.assertEqual(json.loads(self.suite_path.read_text()), suite)

    def test_invalid_suite_is_rejected_before_execution_or_creating_a_scorecard(self):
        suite = self.suite()
        suite["cases"][0]["request"]["previous_response_id"] = "opaque-history"
        with workflow.provider("7") as (endpoint, received):
            with self.fixture.connected_client(self.fixture.admission_configuration(endpoint, endpoint)):
                result = self.run_benchmark("local-code", suite=suite, expected=2)
        self.assertEqual((result["error"]["code"], received, self.report_path.exists()),
                         ("benchmark_suite_invalid", [], False))

    def test_streaming_scorecard_measures_first_token_delivery_separately_from_total_latency(self):
        suite = self.suite()
        for case in suite["cases"]:
            case["request"]["stream"] = True
        with workflow.provider("7", event_stream=True, text_deltas=("7",)) as (endpoint, _):
            value = self.fixture.admission_configuration(endpoint, endpoint)
            value["model_routes"]["local-code"]["capabilities"].append("streaming")
            with self.fixture.connected_client(value):
                report = self.run_benchmark("local-code", suite=suite)
        metrics = report["models"][0]["metrics"]
        self.assertEqual((metrics["ttft_ms"]["samples"], metrics["completed"]), (2, 2))
        for observation in report["models"][0]["observations"]:
            self.assertLessEqual(observation["first_byte_ms"], observation["ttft_ms"])
            self.assertLessEqual(observation["ttft_ms"], observation["latency_ms"])

    def test_a_different_response_stream_mode_cannot_certify_the_requested_profile(self):
        for requested_stream, received_stream in ((True, False), (False, True)):
            with self.subTest(requested_stream=requested_stream):
                self.report_path = self.root / f"stream-mode-{requested_stream}.json"
                suite = self.suite()
                suite["cases"] = suite["cases"][:1]
                suite["cases"][0]["request"]["stream"] = requested_stream
                suite["targets"]["min_cases"] = 1
                with workflow.provider("7", event_stream=received_stream, text_deltas=("7",)) as (endpoint, received):
                    value = self.fixture.admission_configuration(endpoint, endpoint)
                    value["model_routes"]["local-code"]["capabilities"].append("streaming")
                    with self.fixture.connected_client(value):
                        report = self.run_benchmark("local-code", suite=suite)
                model = report["models"][0]
                self.assertEqual((model["metrics"]["completed"], model["workloads"][0]["target_met"],
                                  model["observations"][0]["error"], received[0]["body"]["stream"]),
                                 (0, False, "benchmark_response_mode_mismatch", requested_stream))

    def test_stream_failure_after_a_completed_event_cannot_qualify_the_model(self):
        suite = self.suite()
        suite["cases"] = suite["cases"][:1]
        suite["cases"][0]["request"]["stream"] = True
        suite["targets"]["min_cases"] = 1
        with workflow.provider("7", event_stream=True, text_deltas=("7",),
                               trailing_events=({"type": "response.failed"},)) as (endpoint, _):
            value = self.fixture.admission_configuration(endpoint, endpoint)
            value["model_routes"]["local-code"]["capabilities"].append("streaming")
            with self.fixture.connected_client(value):
                report = self.run_benchmark("local-code", suite=suite)
        model = report["models"][0]
        self.assertEqual((model["metrics"]["completed"], model["metrics"]["passed"],
                          model["workloads"][0]["target_met"], model["observations"][0]["error"]),
                         (0, 0, False, "benchmark_stream_invalid"))

    def test_repeats_do_not_inflate_held_out_coverage_or_hide_unmet_workload_targets(self):
        suite = self.suite()
        suite["cases"][0]["partition"] = "development"
        with workflow.provider("7") as (endpoint, _):
            with self.fixture.connected_client(self.fixture.admission_configuration(endpoint, endpoint)):
                report = self.run_benchmark("local-code", suite=suite, arguments=("--repeats", "3"))
        model = report["models"][0]
        workload = model["workloads"][0]
        self.assertEqual((model["metrics"]["attempts"], workload["held_out_case_count"],
                          workload["metrics"]["task_success_rate"], workload["target_met"],
                          workload["target_failures"]),
                         (6, 1, 0, False, ["insufficient_held_out_cases", "pass_rate_below_target"]))

    def test_opted_in_router_uses_measured_scorecard_to_select_a_model_meeting_the_target(self):
        suite = self.suite()
        suite["cases"] = suite["cases"][:1]
        suite["targets"]["min_cases"] = 1
        with workflow.provider("0") as (first, first_requests), workflow.provider("7") as (second, second_requests):
            value = self.two_local_configuration(first, second)
            value["route_admission"]["scorecard"] = str(self.report_path)
            with self.fixture.connected_client(value) as submit:
                self.run_benchmark("local-code", "local-better", suite=suite)
                result = submit(request={"input": "Summarize seven boxes as a digit."})
                self.assertEqual((result["status"], result["body"]["output"][0]["content"][0]["text"],
                                  result["headers"].get("x-hormuz-routing-reason"),
                                  result["headers"].get("x-hormuz-scorecard-status")),
                                 (200, "7", "benchmark_scorecard", "selected"))
            self.assertEqual((len(first_requests), len(second_requests)), (1, 2))

    def test_routing_matches_the_effective_policy_cap_not_the_uncapped_request(self):
        for measured_budget, expected_model, expected_status in (
                (8, "fixture-local-model", "no_target_match"),
                (4, "fixture-second-model", "selected")):
            with self.subTest(measured_budget=measured_budget):
                self.report_path = self.root / f"policy-cap-{measured_budget}.json"
                suite = self.suite()
                suite["cases"] = suite["cases"][:1]
                suite["cases"][0]["request"]["max_output_tokens"] = measured_budget
                suite["targets"]["min_cases"] = 1
                with workflow.provider("0") as (first, first_requests), workflow.provider("7") as (second, second_requests):
                    value = self.two_local_configuration(first, second)
                    value["route_admission"]["scorecard"] = str(self.report_path)
                    with self.fixture.connected_client(value):
                        self.run_benchmark("local-code", "local-better", suite=suite)
                    value["policies"]["organization"]["max_output_tokens"] = 4
                    with self.fixture.connected_client(value) as submit:
                        result = submit(request={"input": "Summarize seven boxes as a digit.",
                                                 "max_output_tokens": 8})
                delivered = (first_requests if expected_model == "fixture-local-model" else second_requests)[-1]
                self.assertEqual((result["status"], result["headers"].get("x-hormuz-routed-model"),
                                  result["headers"].get("x-hormuz-scorecard-status"),
                                  delivered["body"]["max_output_tokens"]),
                                 (200, expected_model, expected_status, 4))

    def test_a_changed_upstream_deadline_cannot_inherit_old_scores(self):
        suite = self.suite()
        suite["cases"] = suite["cases"][:1]
        suite["targets"]["min_cases"] = 1
        with workflow.provider("0") as (first, _), workflow.provider("7") as (second, _):
            value = self.two_local_configuration(first, second)
            value["route_admission"]["scorecard"] = str(self.report_path)
            with self.fixture.connected_client(value) as submit:
                self.run_benchmark("local-code", "local-better", suite=suite)
                before = submit(request={"input": "Summarize seven boxes as a digit."})
                self.assertEqual(before["headers"].get("x-hormuz-scorecard-status"), "selected")
            value["upstream_timeout_seconds"] = 1
            with self.fixture.connected_client(value) as submit:
                after = submit(request={"input": "Summarize seven boxes as a digit."})
        self.assertEqual((after["status"], after["headers"].get("x-hormuz-routed-model"),
                          after["headers"].get("x-hormuz-scorecard-status")),
                         (200, "fixture-local-model", "no_target_match"))

    def test_larger_prepared_requests_do_not_inherit_a_smaller_workloads_score(self):
        suite = self.suite()
        suite["cases"] = suite["cases"][:1]
        suite["targets"]["min_cases"] = 1
        with workflow.provider("0") as (first, _), workflow.provider("7") as (second, _):
            value = self.two_local_configuration(first, second)
            value["route_admission"]["scorecard"] = str(self.report_path)
            value["ai_work"] = {"enabled": True, "database": str(self.root / "work.sqlite3")}
            with self.fixture.connected_client(value) as submit:
                self.run_benchmark("local-code", "local-better", suite=suite)
                result = submit(request={"input": suite["cases"][0]["request"]["input"] + "!", "stream": False})
        self.assertEqual((result["status"], result["headers"].get("x-hormuz-routed-model"),
                          result["headers"].get("x-hormuz-scorecard-status")),
                         (200, "fixture-local-model", "no_target_match"))

    def test_candidate_storage_normalization_preserves_the_measured_wire_bound(self):
        suite = self.suite()
        suite["cases"] = suite["cases"][:1]
        suite["targets"]["min_cases"] = 1
        measured_input = suite["cases"][0]["request"]["input"]
        with workflow.provider("0") as (first, first_requests), workflow.provider("7") as (second, second_requests):
            value = self.two_local_configuration(first, second)
            value["upstreams"]["local-llama"]["allow_response_storage"] = True
            value["upstreams"]["openai"]["allow_response_storage"] = True
            value["route_admission"]["scorecard"] = str(self.report_path)
            with self.fixture.connected_client(value) as submit:
                report = self.run_benchmark("local-code", "local-better", suite=suite)
                matching = submit(request={"input": measured_input, "stream": False})
                larger = submit(request={"input": measured_input + "!", "stream": False})
        self.assertEqual((matching["status"], matching["headers"].get("x-hormuz-scorecard-status"),
                          larger["status"], larger["headers"].get("x-hormuz-routed-model"),
                          larger["headers"].get("x-hormuz-scorecard-status")),
                         (200, "selected", 200, "fixture-local-model", "no_target_match"))
        measured_bound = report["models"][1]["workloads"][0]["request_bytes_max"]
        self.assertEqual((len(first_requests), len(second_requests), second_requests[-1]["wire_bytes"]),
                         (2, 2, measured_bound))

    def test_compaction_requests_cannot_inherit_response_generation_scores(self):
        suite = self.suite()
        suite["cases"] = suite["cases"][:1]
        suite["targets"]["min_cases"] = 1
        with workflow.provider("0") as (first, _), workflow.provider("7") as (second, _):
            value = self.two_local_configuration(first, second)
            value["route_admission"]["scorecard"] = str(self.report_path)
            for route in value["model_routes"].values():
                route["capabilities"] = sorted(set(route.get("capabilities", [])) | {"compact"})
            with self.fixture.connected_client(value) as submit:
                self.run_benchmark("local-code", "local-better", suite=suite)
                result = submit(path="/responses/compact", request={"input": "Summarize seven boxes as a digit.", "stream": False})
        self.assertEqual((result["status"], result["headers"].get("x-hormuz-routed-model"),
                          result["headers"].get("x-hormuz-scorecard-status")),
                         (200, "fixture-local-model", "no_target_match"))

    def test_inconsistent_scorecard_totals_cannot_override_the_existing_route(self):
        suite = self.suite()
        suite["cases"] = suite["cases"][:1]
        suite["targets"]["min_cases"] = 1
        with workflow.provider("0") as (first, _), workflow.provider("7") as (second, _):
            value = self.two_local_configuration(first, second)
            value["route_admission"]["scorecard"] = str(self.report_path)
            with self.fixture.connected_client(value) as submit:
                report = self.run_benchmark("local-code", "local-better", suite=suite)
                workload = report["models"][0]["workloads"][0]
                workload["target_met"] = True
                workload["metrics"]["task_success_rate"] = 1
                workload["metrics"]["latency_ms"]["p95"] = 0
                self.report_path.write_text(json.dumps(report))
                result = submit(request={"input": "Summarize seven boxes as a digit."})
                self.assertEqual((result["status"], result["body"]["output"][0]["content"][0]["text"],
                                  result["headers"].get("x-hormuz-scorecard-status")), (200, "0", "invalid"))

    def test_absolute_run_deadline_reports_timeout_and_prevents_more_model_work(self):
        entered, release = threading.Event(), threading.Event()
        with workflow.provider("7", hold_first=(entered, release)) as (endpoint, received):
            try:
                with self.fixture.connected_client(self.fixture.admission_configuration(endpoint, endpoint)):
                    report = self.run_benchmark("local-code", arguments=("--timeout-seconds", "0.15", "--deadline-seconds", "0.15"))
            finally:
                release.set()
        self.assertEqual(([row["error"] for row in report["models"][0]["observations"]], len(received)),
                         (["timeout", "deadline_not_run"], 1))

    def test_scorecard_routing_can_be_staged_without_rewriting_the_active_configuration(self):
        with workflow.provider("7") as (endpoint, _):
            with self.fixture.connected_client(self.fixture.admission_configuration(endpoint, endpoint)):
                self.run_benchmark("local-code")
        original = self.config_path.read_bytes()
        output = self.root / "scorecard-routing.json"
        preview = self.command("configure", "--scorecard", str(self.report_path), "--output", str(output), "--dry-run")
        self.assertFalse(output.exists())
        applied = self.command("configure", "--scorecard", str(self.report_path), "--output", str(output))
        self.assertEqual((preview["activation"], applied["activation"],
                          json.loads(output.read_text())["route_admission"]["scorecard"]),
                         ("requires_reconnect", "requires_reconnect", str(self.report_path)))
        self.assertEqual(self.config_path.read_bytes(), original)
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)

    def test_valid_large_configurations_can_stage_scorecard_routing(self):
        with workflow.provider("7") as (endpoint, _):
            with self.fixture.connected_client(self.fixture.admission_configuration(endpoint, endpoint)):
                self.run_benchmark("local-code")
        self.config_path.write_bytes(self.config_path.read_bytes() + b" " * 300000)
        original = self.config_path.read_bytes()
        output = self.root / "large-scorecard-routing.json"
        result = self.command("configure", "--scorecard", str(self.report_path), "--output", str(output))
        self.assertEqual((result["activation"], json.loads(output.read_text())["route_admission"]["scorecard"]),
                         ("requires_reconnect", str(self.report_path)))
        self.assertEqual(self.config_path.read_bytes(), original)

    def test_scorecard_staging_does_not_need_the_running_services_credentials(self):
        with workflow.provider("7") as (endpoint, _):
            with self.fixture.connected_client(self.fixture.admission_configuration(endpoint, endpoint)):
                self.run_benchmark("local-code")
        for name in self.fixture.environment:
            self.environment.pop(name, None)
        original = self.config_path.read_bytes()
        output = self.root / "offline-scorecard-routing.json"
        result = self.command("configure", "--scorecard", str(self.report_path), "--output", str(output))
        self.assertEqual((result["activation"], json.loads(output.read_text())["route_admission"]["scorecard"]),
                         ("requires_reconnect", str(self.report_path)))
        self.assertEqual(self.config_path.read_bytes(), original)

    def test_benchmark_receipts_have_valid_discoverable_versioned_contracts(self):
        with workflow.provider("7") as (endpoint, _):
            with self.fixture.connected_client(self.fixture.admission_configuration(endpoint, endpoint)) as submit:
                report = self.run_benchmark("local-code")
                runtime = submit(method="GET", path="/router/benchmark-state")["body"]
        self.assertEqual(report["schema_id"], "hormuz.personal-model-scorecard")
        validate_contract(report)
        validate_contract(runtime)
        frozen = json.loads((Path(__file__).parent / "fixtures/model-benchmark-v1.json").read_text())
        self.assertEqual((report["schema_id"], report["schema_version"]),
                         (frozen["schema_id"], frozen["schema_version"]))
        self.assertEqual(sorted(report), sorted(frozen["scorecard_required_fields"]))
        self.assertEqual(sorted(report["machine"]), sorted(frozen["machine_required_fields"]))
        self.assertEqual(sorted(report["models"][0]), sorted(frozen["model_required_fields"]))
        self.assertEqual(sorted(report["models"][0]["observations"][0]), sorted(frozen["observation_required_fields"]))
        self.assertEqual(sorted(report["models"][0]["observations"][0]["usage"]), sorted(frozen["usage_required_fields"]))
        self.assertEqual(sorted(runtime), sorted(frozen["runtime_required_fields"]))
        self.assertEqual(sorted(runtime["routes"][0]), sorted(frozen["runtime_route_required_fields"]))
        manifest = subprocess.run([sys.executable, "-m", "hormuz", "contract", "manifest"],
                                  capture_output=True, text=True, timeout=10, check=True)
        schemas = {(row["schema_id"], row["schema_version"]) for row in json.loads(manifest.stdout)["schemas"]}
        relay = {row["schema_version"]: set(row["fields"]) for row in json.loads(manifest.stdout)["schemas"]
                 if row["schema_id"] == "hormuz.relay-metadata"}
        self.assertTrue(relay[3] <= relay[4])
        self.assertTrue({("hormuz.personal-model-scorecard", 1), ("hormuz.benchmark-suite", 1),
                         ("hormuz.benchmark-routing-configuration", 1), ("hormuz.benchmark-error", 1),
                         ("hormuz.relay-metadata", 4), ("hormuz.benchmark-runtime", 1)} <= schemas)

    def test_user_can_read_a_comparison_table_with_unknown_prices_labeled(self):
        with workflow.provider("7") as (endpoint, _):
            with self.fixture.connected_client(self.fixture.admission_configuration(endpoint, endpoint)):
                self.run_benchmark("local-code")
        view = subprocess.run([sys.executable, "-m", "hormuz", "benchmark", "show", "--scorecard",
                               str(self.report_path), "--format", "markdown"],
                              env=self.environment, capture_output=True, text=True, timeout=10)
        self.assertEqual(view.returncode, 0, view.stderr)
        self.assertIn("| local-code | 1/2 | 50.0% |", view.stdout)
        self.assertIn("unknown", view.stdout)
        self.assertNotIn("PRIVATE_TASK", view.stdout)

    def test_renaming_a_duplicate_task_does_not_create_more_held_out_evidence(self):
        suite = self.suite()
        suite["cases"][1] = {**suite["cases"][0], "id": "renamed-copy"}
        with workflow.provider("7") as (endpoint, _):
            with self.fixture.connected_client(self.fixture.admission_configuration(endpoint, endpoint)):
                report = self.run_benchmark("local-code", suite=suite)
        workload = report["models"][0]["workloads"][0]
        self.assertEqual((workload["held_out_case_count"], workload["target_met"]), (1, False))

    def test_unexecuted_or_failed_requests_cannot_meet_a_permissive_quality_target(self):
        suite = self.suite()
        suite["targets"]["min_pass_rate"] = 0
        with workflow.provider("7", status=503) as (endpoint, _):
            with self.fixture.connected_client(self.fixture.admission_configuration(endpoint, endpoint)):
                report = self.run_benchmark("local-code", suite=suite)
        workload = report["models"][0]["workloads"][0]
        self.assertEqual((workload["target_met"], workload["target_failures"]),
                         (False, ["execution_evidence_incomplete"]))

    def test_benchmark_does_not_measure_a_capacity_alternate_as_the_requested_model(self):
        suite = self.suite()
        suite["cases"] = suite["cases"][:1]
        with workflow.provider("7", status=lambda body: 429 if body["model"] == "fixture-local-model" else 200) as (endpoint, received):
            value = self.fixture.admission_configuration(endpoint, endpoint)
            value["ai_work"] = {"enabled": True, "database": str(self.root / "work.sqlite3")}
            value["model_routes"]["local-code"]["failover_alias"] = "local-alternate"
            value["model_routes"]["local-alternate"] = {**value["model_routes"]["local-code"], "upstream_model": "alternate-model"}
            value["model_routes"]["local-alternate"].pop("failover_alias")
            value["route_admission"]["local_routes"]["local-alternate"] = dict(value["route_admission"]["local_routes"]["local-code"])
            value["policies"]["organization"]["allowed_models"].append("local-alternate")
            with self.fixture.connected_client(value):
                report = self.run_benchmark("local-code", suite=suite)
        self.assertEqual(([row["error"] for row in report["models"][0]["observations"]],
                          [request["body"]["model"] for request in received]),
                         (["http_429"], ["fixture-local-model"]))

    def test_unready_benchmark_model_cannot_borrow_another_destinations_result(self):
        suite = self.suite()
        suite["cases"] = suite["cases"][:1]
        suite["targets"]["min_cases"] = 1
        with workflow.provider("7", health_status=503) as (first, first_requests), \
             workflow.provider("7") as (second, second_requests):
            value = self.two_local_configuration(first, second)
            value["model_routes"]["local-better"]["upstream_model"] = "fixture-local-model"
            with self.fixture.connected_client(value):
                report = self.run_benchmark("local-code", suite=suite)
        model = report["models"][0]
        self.assertEqual((model["metrics"]["completed"], model["workloads"][0]["target_met"],
                          [row["error"] for row in model["observations"]], first_requests, second_requests),
                         (0, False, ["http_503"], [], []))

    def test_changed_configuration_cannot_label_an_older_running_model_as_a_new_revision(self):
        with workflow.provider("7") as (endpoint, received):
            value = self.fixture.admission_configuration(endpoint, endpoint)
            with self.fixture.connected_client(value):
                changed = json.loads(self.config_path.read_text())
                changed["model_routes"]["local-code"]["model_revision"] = "new-weights-v2"
                changed["route_admission"]["local_routes"]["local-code"]["model_revision"] = "new-weights-v2"
                self.config_path.write_text(json.dumps(changed))
                result = self.run_benchmark("local-code", expected=2)
        self.assertEqual((result["error"]["code"], received, self.report_path.exists()),
                         ("benchmark_execution_mismatch", [], False))

    def test_redacted_requests_cannot_certify_the_original_control_profile(self):
        secret = "fixture-benchmark-protected-instruction"
        self.fixture.environment["BENCHMARK_POLICY_SECRET"] = secret
        self.environment["BENCHMARK_POLICY_SECRET"] = secret
        suite = self.suite()
        suite["cases"] = suite["cases"][:1]
        suite["cases"][0]["request"]["instructions"] = "Use this reference: " + secret
        suite["targets"]["min_cases"] = 1
        with workflow.provider("7") as (endpoint, received):
            value = self.fixture.admission_configuration(endpoint, endpoint)
            value["egress_controls"] = {"secrets": {"mode": "redact", "custom_secret_envs": ["BENCHMARK_POLICY_SECRET"]}}
            with self.fixture.connected_client(value):
                report = self.run_benchmark("local-code", suite=suite)
        model = report["models"][0]
        self.assertNotIn(secret, received[0]["body"]["instructions"])
        self.assertEqual((model["metrics"]["completed"], model["workloads"][0]["target_met"],
                          model["observations"][0]["error"]), (0, False, "execution_mismatch"))

    def test_live_policy_must_match_the_configuration_before_any_probes(self):
        secret = "fixture-benchmark-protected-instruction"
        self.fixture.environment["BENCHMARK_POLICY_SECRET"] = secret
        self.environment["BENCHMARK_POLICY_SECRET"] = secret
        suite = self.suite()
        suite["cases"] = suite["cases"][:1]
        suite["cases"][0]["request"]["instructions"] = "Use this reference: " + secret
        suite["targets"]["min_cases"] = 1
        with workflow.provider("7") as (endpoint, received):
            value = self.fixture.admission_configuration(endpoint, endpoint)
            value["egress_controls"] = {"secrets": {"mode": "redact", "custom_secret_envs": ["BENCHMARK_POLICY_SECRET"]}}
            with self.fixture.connected_client(value):
                changed = json.loads(self.config_path.read_text())
                changed["egress_controls"]["secrets"]["mode"] = "off"
                self.config_path.write_text(json.dumps(changed))
                result = self.run_benchmark("local-code", suite=suite, expected=2)
        self.assertEqual((result["error"]["code"], received, self.report_path.exists()),
                         ("benchmark_execution_mismatch", [], False))

    def test_runner_needs_only_the_selected_actor_credential_and_no_service_secrets(self):
        secret = "fixture-service-only-redaction-secret"
        self.fixture.environment["BENCHMARK_SERVICE_SECRET"] = secret
        self.fixture.environment["BENCHMARK_OTHER_ACTOR_TOKEN"] = "fixture-other-actor-credential"
        self.environment.pop("BENCHMARK_SERVICE_SECRET", None)
        self.environment.pop("BENCHMARK_OTHER_ACTOR_TOKEN", None)
        suite = self.suite()
        suite["cases"] = suite["cases"][:1]
        suite["targets"]["min_cases"] = 1
        for multiple_actors in (False, True):
            with self.subTest(multiple_actors=multiple_actors):
                self.report_path = self.root / f"service-only-{multiple_actors}.json"
                with workflow.provider("7") as (endpoint, received):
                    value = self.fixture.admission_configuration(endpoint, endpoint)
                    value["egress_controls"] = {"secrets": {"mode": "redact", "custom_secret_envs": ["BENCHMARK_SERVICE_SECRET"]}}
                    if multiple_actors:
                        value["identities"].append({**value["identities"][0], "actor_id": "other",
                            "actor_name": "Other", "token_env": "BENCHMARK_OTHER_ACTOR_TOKEN"})
                    with self.fixture.connected_client(value):
                        report = self.run_benchmark("local-code", suite=suite, arguments=("--actor", "developer"))
                self.assertEqual((report["actor_id"], report["models"][0]["metrics"]["completed"],
                                  report["models"][0]["workloads"][0]["target_met"], len(received)),
                                 ("developer", 1, True, 1))
                self.assertNotIn(secret, self.report_path.read_text())

    def test_actor_rotation_can_use_an_available_later_credential_source(self):
        self.fixture.environment["BENCHMARK_OLD_TOKEN"] = "fixture-old-actor-credential"
        self.environment.pop("BENCHMARK_OLD_TOKEN", None)
        suite = self.suite()
        suite["cases"] = suite["cases"][:1]
        suite["targets"]["min_cases"] = 1
        with workflow.provider("7") as (endpoint, received):
            value = self.fixture.admission_configuration(endpoint, endpoint)
            value["identities"].insert(0, {**value["identities"][0], "token_env": "BENCHMARK_OLD_TOKEN"})
            with self.fixture.connected_client(value):
                report = self.run_benchmark("local-code", suite=suite, arguments=("--actor", "developer"))
        self.assertEqual((report["actor_id"], report["models"][0]["metrics"]["completed"],
                          report["models"][0]["workloads"][0]["target_met"], len(received)),
                         ("developer", 1, True, 1))

    def test_runner_accepts_loopback_spellings_allowed_by_ingress(self):
        suite = self.suite()
        suite["cases"] = suite["cases"][:1]
        suite["targets"]["min_cases"] = 1
        hosts = ["LOCALHOST"] + (["127.0.0.2"] if sys.platform == "linux" else [])
        for host in hosts:
            with self.subTest(host=host):
                self.report_path = self.root / f"loopback-{host}.json"
                with workflow.provider("7") as (endpoint, received):
                    value = self.fixture.admission_configuration(endpoint, endpoint)
                    value["listen"]["host"] = host
                    with self.fixture.connected_client(value):
                        report = self.run_benchmark("local-code", suite=suite)
                self.assertEqual((report["models"][0]["metrics"]["completed"],
                                  report["models"][0]["workloads"][0]["target_met"], len(received)),
                                 (1, True, 1))

    def test_a_changed_endpoint_cannot_receive_an_older_endpoints_benchmark_evidence(self):
        suite = self.suite()
        suite["cases"] = suite["cases"][:1]
        suite["targets"]["min_cases"] = 1
        with workflow.provider("7") as (first, first_requests), workflow.provider("0") as (second, second_requests):
            value = self.fixture.admission_configuration(first, first)
            with self.fixture.connected_client(value):
                changed = json.loads(self.config_path.read_text())
                changed["upstreams"]["local-llama"]["base_url"] = second
                self.config_path.write_text(json.dumps(changed))
                result = self.run_benchmark("local-code", suite=suite, expected=2)
        self.assertEqual((result["error"]["code"], first_requests, second_requests, self.report_path.exists()),
                         ("benchmark_execution_mismatch", [], [], False))

    def test_an_unexpected_live_output_cap_cannot_certify_the_requested_budget(self):
        suite = self.suite()
        suite["cases"] = suite["cases"][:1]
        suite["cases"][0]["request"]["max_output_tokens"] = 16
        suite["targets"]["min_cases"] = 1
        with workflow.provider("7") as (endpoint, received):
            value = self.fixture.admission_configuration(endpoint, endpoint)
            with self.fixture.connected_client(value):
                changed = json.loads(self.config_path.read_text())
                changed["policies"]["organization"]["max_output_tokens"] = 16
                self.config_path.write_text(json.dumps(changed))
                result = self.run_benchmark("local-code", suite=suite, expected=2)
        self.assertEqual((result["error"]["code"], received, self.report_path.exists()),
                         ("benchmark_execution_mismatch", [], False))

    def test_stale_missing_and_mismatched_evidence_preserve_the_existing_route(self):
        suite = self.suite()
        suite["cases"] = suite["cases"][:1]
        suite["targets"]["min_cases"] = 1
        with workflow.provider("0") as (first, _), workflow.provider("7") as (second, _):
            value = self.two_local_configuration(first, second)
            value["route_admission"]["scorecard"] = str(self.report_path)
            with self.fixture.connected_client(value) as submit:
                original = self.run_benchmark("local-code", "local-better", suite=suite)
                for change, expected in (("expired", "expired"), ("owner", "owner_mismatch"),
                                         ("route", "no_target_match"), ("missing", "missing")):
                    with self.subTest(change=change):
                        report = json.loads(json.dumps(original))
                        if change == "expired":
                            report["recorded_at"] -= 8 * 86400
                            report["expires_at"] -= 8 * 86400
                        elif change == "owner":
                            report["actor_id"] = "another-user"
                        elif change == "route":
                            report["models"][1]["route_fingerprint"] = "destination-v1:" + "0" * 64
                        if change == "missing":
                            self.report_path.unlink()
                        else:
                            self.report_path.write_text(json.dumps(report))
                        result = submit(request={"input": "Summarize seven boxes as a digit."})
                        self.assertEqual((result["status"], result["body"]["output"][0]["content"][0]["text"],
                                          result["headers"].get("x-hormuz-scorecard-status")), (200, "0", expected))

    def test_scorecard_does_not_change_routing_without_opt_in_or_override_a_denied_model(self):
        suite = self.suite()
        suite["cases"] = suite["cases"][:1]
        suite["targets"]["min_cases"] = 1
        with workflow.provider("0") as (first, _), workflow.provider("7") as (second, _):
            value = self.two_local_configuration(first, second)
            with self.fixture.connected_client(value) as submit:
                self.run_benchmark("local-code", "local-better", suite=suite)
                result = submit(request={"input": "Summarize seven boxes as a digit."})
                self.assertEqual((result["body"]["output"][0]["content"][0]["text"],
                                  result["headers"].get("x-hormuz-scorecard-status")), ("0", None))
            value["route_admission"]["scorecard"] = str(self.report_path)
            value["policies"]["organization"]["allowed_models"] = ["local-code"]
            with self.fixture.connected_client(value) as submit:
                result = submit(request={"input": "Summarize seven boxes as a digit."})
                self.assertEqual((result["status"], result["body"]["output"][0]["content"][0]["text"]), (200, "0"))

    def test_tool_continuation_keeps_the_successful_model_after_scorecard_deletion(self):
        suite = self.suite()
        suite["cases"] = suite["cases"][:1]
        suite["targets"]["min_cases"] = 1
        tools = [{"type": "function", "name": "lookup_count", "parameters": {"type": "object", "properties": {}}}]
        suite["cases"][0]["request"]["tools"] = tools
        with workflow.provider("0") as (first, _), workflow.provider("7") as (second, _):
            value = self.two_local_configuration(first, second)
            value["route_admission"]["scorecard"] = str(self.report_path)
            value["ai_work"] = {"enabled": True, "database": str(self.root / "work.sqlite3"),
                                "tool_capable_aliases": ["local-code", "local-better"]}
            for route in value["model_routes"].values():
                route["capabilities"] = sorted(set(route.get("capabilities", [])) | {"tools"})
            with self.fixture.connected_client(value) as submit:
                self.run_benchmark("local-code", "local-better", suite=suite)
                first_result = submit(request={"input": "Summarize seven boxes as a digit.", "tools": tools})
                self.assertEqual(first_result["headers"].get("x-hormuz-scorecard-status"), "selected")
                self.report_path.unlink()
                continued = submit(headers={"X-Hormuz-Work-Id": first_result["headers"]["x-hormuz-work-id"]},
                    request={"input": [{"type": "function_call_output", "call_id": "fixture-call", "output": "7"}], "tools": tools})
                self.assertEqual((continued["status"], continued["body"]["output"][0]["content"][0]["text"],
                                  continued["headers"].get("x-hormuz-scorecard-status"),
                                  continued["headers"].get("x-hormuz-routing-reason")),
                                 (200, "7", "affinity_preserved", "session_affinity"))

    def test_cost_routing_requires_observed_prices_and_honors_the_cost_target(self):
        suite = self.suite()
        suite["cases"] = suite["cases"][:1]
        suite["targets"] = {"objective": "cost", "min_pass_rate": 1, "min_cases": 1, "max_mean_cost_microusd": 10}
        with workflow.provider("7") as (first, _), workflow.provider("7") as (second, _):
            value = self.two_local_configuration(first, second)
            value["route_admission"]["scorecard"] = str(self.report_path)
            for alias, rate in (("local-code", 10), ("local-better", 1)):
                value["model_routes"][alias].update(input_cost_per_million=rate, output_cost_per_million=rate)
            with self.fixture.connected_client(value) as submit:
                report = self.run_benchmark("local-code", "local-better", suite=suite)
                result = submit(request={"input": "Summarize seven boxes as a digit."})
                self.assertEqual(([model["metrics"]["mean_cost_microusd"] for model in report["models"]],
                                  result["headers"].get("x-hormuz-routed-model")), ([60, 6], "fixture-second-model"))

    def test_observed_cache_writes_require_prices_and_use_their_actual_cost(self):
        for label, writes, rate, expected_writes, expected_cost in (
                ("unpriced", 2, None, 2, None), ("priced", 2, 4, 2, 12),
                ("invalid-subset", 4, 4, None, None)):
            with self.subTest(case=label):
                self.report_path = self.root / f"cache-write-{label}.json"
                suite = self.suite()
                suite["cases"] = suite["cases"][:1]
                suite["targets"].update(objective="cost", min_cases=1, max_mean_cost_microusd=10)
                usage = {"input_tokens": 4, "output_tokens": 2,
                         "input_tokens_details": {"cached_tokens": 1, "cache_write_tokens": writes}}
                with workflow.provider("7", response_fields={"usage": usage}) as (endpoint, _):
                    value = self.fixture.admission_configuration(endpoint, endpoint)
                    route = value["model_routes"]["local-code"]
                    route.update(input_cost_per_million=1, output_cost_per_million=1, cache_read_cost_per_million=1)
                    if rate is not None:
                        route["cache_write_cost_per_million"] = rate
                    with self.fixture.connected_client(value):
                        report = self.run_benchmark("local-code", suite=suite)
                model = report["models"][0]
                self.assertEqual((model["observations"][0]["usage"].get("cache_write_tokens"),
                                  model["metrics"].get("cache_write_tokens"), model["metrics"]["mean_cost_microusd"],
                                  model["workloads"][0]["target_met"]),
                                 (expected_writes, expected_writes, expected_cost, False))

    def test_removing_a_zero_cache_write_price_invalidates_cost_selection(self):
        suite = self.suite()
        suite["cases"] = suite["cases"][:1]
        suite["targets"].update(objective="cost", min_cases=1, max_mean_cost_microusd=0)
        usage = {"input_tokens": 4, "output_tokens": 2, "input_tokens_details": {"cache_write_tokens": 1}}
        with workflow.provider("0", response_fields={"usage": usage}) as (first, _), \
             workflow.provider("7", response_fields={"usage": usage}) as (second, _):
            value = self.two_local_configuration(first, second)
            value["route_admission"]["scorecard"] = str(self.report_path)
            value["model_routes"]["local-code"].update(input_cost_per_million=1, output_cost_per_million=1,
                                                       cache_write_cost_per_million=1)
            value["model_routes"]["local-better"].update(input_cost_per_million=0, output_cost_per_million=0,
                                                         cache_write_cost_per_million=0)
            with self.fixture.connected_client(value):
                report = self.run_benchmark("local-code", "local-better", suite=suite)
            self.assertEqual(report["models"][1]["metrics"]["mean_cost_microusd"], 0)
            value["model_routes"]["local-better"].pop("cache_write_cost_per_million")
            with self.fixture.connected_client(value) as submit:
                result = submit(request={"input": "Summarize seven boxes as a digit."})
        self.assertEqual((result["status"], result["headers"].get("x-hormuz-routed-model"),
                          result["headers"].get("x-hormuz-scorecard-status")),
                         (200, "fixture-local-model", "no_target_match"))

    def test_impossible_cache_usage_cannot_satisfy_a_cost_target(self):
        suite = self.suite()
        suite["targets"] = {"objective": "cost", "min_pass_rate": 0, "min_cases": 2,
                            "max_mean_cost_microusd": 10}
        usage = {"input_tokens": 4, "output_tokens": 2, "input_tokens_details": {"cached_tokens": 5}}
        with workflow.provider("7", response_fields={"usage": usage}) as (endpoint, _):
            value = self.fixture.admission_configuration(endpoint, endpoint)
            value["model_routes"]["local-code"].update(input_cost_per_million=10,
                cache_read_cost_per_million=0, output_cost_per_million=1)
            with self.fixture.connected_client(value):
                report = self.run_benchmark("local-code", suite=suite)
        model = report["models"][0]
        self.assertEqual((model["metrics"]["cache_read_tokens"], model["metrics"]["mean_cost_microusd"],
                          model["workloads"][0]["target_met"]), (None, None, False))

    def test_removed_zero_prices_cannot_preserve_a_cost_routing_preference(self):
        suite = self.suite()
        suite["cases"] = suite["cases"][:1]
        suite["targets"] = {"objective": "cost", "min_pass_rate": 1, "min_cases": 1}
        with workflow.provider("0") as (first, _), workflow.provider("7") as (second, _):
            value = self.two_local_configuration(first, second)
            value["route_admission"]["scorecard"] = str(self.report_path)
            value["model_routes"]["local-better"].update(input_cost_per_million=0, output_cost_per_million=0)
            with self.fixture.connected_client(value):
                self.run_benchmark("local-better", suite=suite)
            value["model_routes"]["local-better"].pop("input_cost_per_million")
            value["model_routes"]["local-better"].pop("output_cost_per_million")
            with self.fixture.connected_client(value) as submit:
                result = submit(request={"input": "Summarize seven boxes as a digit."})
        self.assertEqual((result["headers"].get("x-hormuz-routed-model"),
                          result["headers"].get("x-hormuz-scorecard-status")),
                         ("fixture-local-model", "no_target_match"))

    def test_benchmark_rejects_shadow_helpers_before_starting_any_model_requests(self):
        with workflow.provider("7") as (endpoint, received):
            value = self.fixture.admission_configuration(endpoint, endpoint)
            value["ai_work"] = {"enabled": True, "database": str(self.root / "work.sqlite3")}
            value["shadow_router"] = {"schema_version": 1, "mode": "shadow", "model": "local-code", "local_model": "local-code"}
            with self.fixture.connected_client(value):
                result = self.run_benchmark("local-code", expected=2)
        self.assertEqual((result["error"]["code"], received), ("benchmark_shadow_router_unsupported", []))

    def test_live_shadow_helpers_cannot_add_calls_after_the_config_file_changes(self):
        suite = self.suite()
        suite["cases"] = suite["cases"][:1]
        suite["targets"]["min_cases"] = 1
        with workflow.provider("7") as (endpoint, received):
            value = self.fixture.admission_configuration(endpoint, endpoint)
            value["ai_work"] = {"enabled": True, "database": str(self.root / "work.sqlite3")}
            value["shadow_router"] = {"schema_version": 1, "mode": "shadow", "model": "local-code", "local_model": "local-code"}
            with self.fixture.connected_client(value):
                changed = json.loads(self.config_path.read_text())
                changed.pop("shadow_router")
                self.config_path.write_text(json.dumps(changed))
                result = self.run_benchmark("local-code", suite=suite, expected=2)
        self.assertEqual((result["error"]["code"], received), ("benchmark_shadow_router_unsupported", []))

    def test_different_generation_budget_does_not_inherit_a_benchmark_quality_score(self):
        suite = self.suite()
        suite["cases"] = suite["cases"][:1]
        suite["targets"]["min_cases"] = 1
        with workflow.provider("0") as (first, _), workflow.provider("7") as (second, _):
            value = self.two_local_configuration(first, second)
            value["route_admission"]["scorecard"] = str(self.report_path)
            with self.fixture.connected_client(value) as submit:
                self.run_benchmark("local-code", "local-better", suite=suite)
                result = submit(request={"input": "Summarize seven boxes as a digit.", "max_output_tokens": 4})
                self.assertEqual((result["status"], result["headers"].get("x-hormuz-routed-model"),
                                  result["headers"].get("x-hormuz-scorecard-status")),
                                 (200, "fixture-local-model", "no_target_match"))

    def test_a_changed_verified_managed_runtime_cannot_inherit_old_scores(self):
        managed = lifetime_workflow.ConnectedClientLifetimeWorkflowTests()
        managed.setUp()
        self.addCleanup(managed.doCleanups)
        self.config_path = managed.path
        self.environment = {**managed.environment, "HORMUZ_HISTORY_HOME": str(self.root / "history")}
        suite = self.suite()
        suite["cases"] = suite["cases"][:1]
        suite["cases"][0]["checks"][0]["value"] = "MANAGED_LOCAL"
        suite["targets"]["min_cases"] = 1
        with workflow.provider("UNUSED") as (cloud, cloud_requests):
            value, _, engine = managed.managed_configuration(cloud)
            members = tuple(engine.iterdir())
            value["route_admission"]["scorecard"] = str(self.report_path)
            managed.path.write_text(json.dumps(value))
            try:
                managed.command("connect")
                self.run_benchmark("local-code", suite=suite)
                original = managed.client()
                self.assertEqual(original["headers"].get("x-hormuz-scorecard-status"), "selected")
            finally:
                managed.command("disconnect", check=False)
            (engine / "libfixture.1.dylib").write_bytes(b"synthetic runtime dependency version two")
            archive = managed.root / "runtime-v2.tar.gz"
            with tarfile.open(archive, "w:gz") as bundle:
                for path in members:
                    bundle.add(path, arcname=path.name)
            value["managed_llama"].update(runtime_archive=str(archive),
                runtime_sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
            managed.path.write_text(json.dumps(value))
            try:
                managed.command("connect")
                changed = managed.client()
                self.assertEqual((changed["status"], changed["headers"].get("x-hormuz-scorecard-status")),
                                 (200, "no_target_match"))
            finally:
                managed.command("disconnect", check=False)
            self.assertEqual(cloud_requests, [])

    def test_scorecard_identifies_the_model_revision_and_local_hardware_context(self):
        with workflow.provider("7") as (endpoint, _):
            value = self.fixture.admission_configuration(endpoint, endpoint)
            alias = "local-" + "x" * 300
            value["model_routes"][alias] = value["model_routes"].pop("local-code")
            value["route_admission"]["local_routes"][alias] = value["route_admission"]["local_routes"].pop("local-code")
            value["policies"]["organization"]["allowed_models"] = [alias]
            with self.fixture.connected_client(value):
                self.run_benchmark(alias)
        report = self.command("show", "--scorecard", str(self.report_path))
        self.assertEqual(report["models"][0]["subject"], {"upstream_model": "fixture-local-model",
            "model_revision": "fixture-weights-v1", "destination": "local-llama", "protocol": "openai", "locality": "local"})
        self.assertGreater(report["machine"]["logical_cpu_count"], 0)
        memory = report["machine"]["physical_memory_bytes"]
        self.assertTrue(memory is None or memory > 0)

    def test_offline_review_accepts_a_bounded_report_larger_than_a_single_provider_reply(self):
        with workflow.provider("7") as (endpoint, _):
            with self.fixture.connected_client(self.fixture.admission_configuration(endpoint, endpoint)):
                report = self.run_benchmark("local-code")
        self.report_path.write_bytes(json.dumps(report).encode() + b" " * 300000)
        self.assertEqual(self.command("show", "--scorecard", str(self.report_path)), report)

    def test_optional_os_memory_statistics_do_not_disable_the_public_cli(self):
        suite = self.command("init", "--output", str(self.suite_path), without_resource=True)
        self.assertEqual(suite["schema_id"], "hormuz.benchmark-suite")


if __name__ == "__main__":
    unittest.main()
