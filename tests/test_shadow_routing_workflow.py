"""Shadow recommendations observed through the approved connected workflow."""

import json
import unittest
import threading
import subprocess
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from tests import test_execution_destination_workflow as workflow
from hormuz.contracts import validate_contract

provider = workflow.provider


class ShadowRoutingWorkflowTests(unittest.TestCase):
    setUp = workflow.ExecutionDestinationWorkflowTests.setUp
    configuration = workflow.ExecutionDestinationWorkflowTests.configuration
    admission_configuration = workflow.ExecutionDestinationWorkflowTests.admission_configuration
    connected_client = workflow.ExecutionDestinationWorkflowTests.connected_client
    settled_attempt_history = workflow.ExecutionDestinationWorkflowTests.settled_attempt_history

    def shadow_configuration(self, local, cloud, classifier, *, mode="local_only"):
        value = self.admission_configuration(local, cloud, mode=mode)
        value["ai_work"] = {"enabled": True, "database": str(self.root / "work.sqlite3")}
        value["upstreams"]["local-classifier"] = {
            "protocol": "openai", "base_url": classifier,
            "locality": "local", "authentication": "none"}
        value["model_routes"]["local-router"] = {
            "protocol": "openai", "destination": "local-classifier",
            "upstream_model": "fixture-router", "model_revision": "fixture-router-v1",
            "capabilities": ["responses", "text"]}
        value["policies"]["organization"]["allowed_models"].append("local-router")
        value["shadow_router"] = {"schema_version": 1, "mode": "shadow", "model": "local-router",
                                  "local_model": "local-code"}
        return value

    def test_shadow_cloud_advice_cannot_change_a_local_only_request(self):
        with provider("LOCAL_DESTINATION") as (local, local_requests), \
             provider("CLOUD_DESTINATION") as (cloud, cloud_requests), \
             provider("CLOUD") as (classifier, classifier_requests):
            with self.connected_client(self.shadow_configuration(local, cloud, classifier)) as submit:
                result = submit(request={"input": "Summarize this short fixture."})
                self.assertEqual((result["status"], result["body"]["output"][0]["content"][0]["text"]),
                                 (200, "LOCAL_DESTINATION"))
                work = result["headers"]["x-hormuz-work-id"]
                history = submit(method="GET", path=f"/work/jobs/{work}/attempts")
                self.assertEqual(history["status"], 200)
                advice = history["body"]["attempts"][0]["shadow_routing"]
                self.assertEqual((advice["mode"], advice["status"], advice["recommendation"],
                                  advice["routing_effect"], advice["classifier_model"],
                                  advice["classifier_revision"], advice["actual_model"]),
                                 ("shadow", "classified", "cloud", "none", "local-router",
                                  "fixture-router-v1", "local-code"))
                self.assertEqual(history["body"]["schema_version"], 2)
                receipt = submit(method="GET", path=f"/work/jobs/{work}")["body"]
                self.assertEqual(receipt["outcome_evidence"], "unknown")
            self.assertEqual((len(local_requests), len(classifier_requests), len(cloud_requests)), (1, 1, 0))
            self.assertNotIn("authorization", classifier_requests[0]["headers"])
            self.assertNotIn("fixture-coding-client-credential", json.dumps(classifier_requests))

    def test_cancellation_during_shadow_classification_cannot_start_the_coding_request(self):
        entered, release = threading.Event(), threading.Event()
        with provider("LOCAL_DESTINATION") as (local, local_requests), \
             provider("CLOUD_DESTINATION") as (cloud, cloud_requests), \
             provider("LOCAL", hold_first=(entered, release)) as (classifier, classifier_requests):
            with self.connected_client(self.shadow_configuration(local, cloud, classifier)) as submit:
                try:
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        signal = self.root / "cancel-after-classifier-entry"
                        response = pool.submit(submit, cancel_before_headers=True, cancel_signal=str(signal),
                            request={"input": "Summarize this fixture."})
                        try:
                            self.assertTrue(entered.wait(timeout=3))
                            signal.touch()
                            self.assertEqual(response.result(timeout=4)["status"], "cancelled")
                        finally:
                            signal.touch()
                            release.set()
                    state = submit(method="GET", path="/work/state")["body"]
                    work = state["works"][0]["work_id"]
                    history = self.settled_attempt_history(submit, work)["body"]
                    self.assertEqual(history["attempts"][0]["shadow_routing"]["status"], "client_canceled")
                    self.assertEqual(history["attempts"][0]["state"], "failed")
                finally:
                    release.set()
            self.assertEqual((len(classifier_requests), len(local_requests), len(cloud_requests)), (1, 0, 0))

    def test_shadow_deadline_is_reported_without_changing_the_local_result_or_retrying(self):
        entered, release = threading.Event(), threading.Event()
        with provider("LOCAL_DESTINATION") as (local, local_requests), \
             provider("CLOUD_DESTINATION") as (cloud, cloud_requests), \
             provider("LOCAL", hold_first=(entered, release)) as (classifier, classifier_requests):
            value = self.shadow_configuration(local, cloud, classifier)
            value["shadow_router"]["timeout_seconds"] = 0.05
            with self.connected_client(value) as submit:
                try:
                    result = submit(request={"input": "Summarize this fixture."})
                    self.assertEqual((result["status"], result["body"]["output"][0]["content"][0]["text"]),
                                     (200, "LOCAL_DESTINATION"))
                    work = result["headers"]["x-hormuz-work-id"]
                    history = submit(method="GET", path=f"/work/jobs/{work}/attempts")["body"]
                    advice = history["attempts"][0]["shadow_routing"]
                    self.assertEqual((advice["status"], advice["recommendation"]), ("timeout", "unknown"))
                    self.assertLess(advice["elapsed_ms"], 500)
                finally:
                    release.set()
            self.assertEqual((len(classifier_requests), len(local_requests), len(cloud_requests)), (1, 1, 0))

    def test_shadow_evidence_has_a_discoverable_versioned_contract(self):
        with provider("LOCAL_DESTINATION") as (local, _), \
             provider("CLOUD_DESTINATION") as (cloud, _), \
             provider("LOCAL") as (classifier, _):
            with self.connected_client(self.shadow_configuration(local, cloud, classifier)) as submit:
                response = submit(request={"input": "Summarize this fixture."})
                work = response["headers"]["x-hormuz-work-id"]
                page = submit(method="GET", path=f"/work/jobs/{work}/attempts")["body"]
                validate_contract(page["attempts"][0]["shadow_routing"])
                validate_contract(page)
                frozen = json.loads((Path(__file__).parent / "fixtures/shadow-routing-v1.json").read_text())
                validate_contract(frozen)
                manifest = subprocess.run([sys.executable, "-m", "hormuz", "contract", "manifest"],
                                          capture_output=True, text=True, check=True, timeout=10)
                entries = json.loads(manifest.stdout)["schemas"]
                self.assertIn(("hormuz.shadow-routing", 1),
                              [(item["schema_id"], item["schema_version"]) for item in entries])

    def test_shadow_classifies_fresh_codex_work_once_and_preserves_tool_affinity(self):
        fixture = json.loads((Path(__file__).parent / "fixtures/codex-conversation-request-v1.json").read_text())
        with provider("LOCAL_DESTINATION", event_stream=True) as (local, local_requests), \
             provider("CLOUD_DESTINATION") as (cloud, cloud_requests), \
             provider("CLOUD") as (classifier, classifier_requests):
            value = self.shadow_configuration(local, cloud, classifier)
            value["model_routes"]["local-code"]["capabilities"].extend(["streaming", "tools", "codex_http"])
            value["ai_work"]["tool_capable_aliases"] = ["local-code"]
            with self.connected_client(value) as submit:
                first = submit(control=True, headers=fixture["headers"], request=fixture["request"])
                self.assertEqual(first["status"], 200)
                second = submit(headers=fixture["headers"], request={"stream": True,
                    "client_metadata": fixture["request"]["client_metadata"], "input": [
                        {"type": "function_call_output", "call_id": "fixture-call", "output": "Fixture tool result"}]})
                self.assertEqual(second["status"], 200)
                self.assertEqual(second["headers"]["x-hormuz-work-id"], first["headers"]["x-hormuz-work-id"])
                self.assertEqual(second["headers"]["x-hormuz-destination-generation"],
                                 first["headers"]["x-hormuz-destination-generation"])
                history = submit(method="GET", path=f'/work/jobs/{first["headers"]["x-hormuz-work-id"]}/attempts')["body"]
                self.assertEqual([row["shadow_routing"]["status"] for row in history["attempts"]],
                                 ["affinity_preserved", "classified"])
                self.assertEqual(second["headers"]["x-hormuz-routing-reason"], "session_affinity")
            self.assertEqual((len(classifier_requests), len(local_requests), len(cloud_requests)), (1, 2, 0))

    def test_local_advice_cannot_retarget_an_automatic_cloud_baseline(self):
        with provider("LOCAL_DESTINATION") as (local, local_requests), \
             provider("CLOUD_DESTINATION") as (cloud, cloud_requests), \
             provider("LOCAL") as (classifier, classifier_requests):
            with self.connected_client(self.shadow_configuration(local, cloud, classifier, mode="auto")) as submit:
                result = submit(request={"model": "cloud-code", "input": "Consider this fixture."})
                self.assertEqual((result["status"], result["body"]["output"][0]["content"][0]["text"]),
                                 (200, "CLOUD_DESTINATION"))
                work = result["headers"]["x-hormuz-work-id"]
                advice = submit(method="GET", path=f"/work/jobs/{work}/attempts")["body"]["attempts"][0]["shadow_routing"]
                self.assertEqual((advice["recommendation"], advice["actual_locality"], advice["routing_effect"]),
                                 ("local", "cloud", "none"))
            self.assertEqual((len(classifier_requests), len(local_requests), len(cloud_requests)), (1, 0, 1))

    def test_local_shadow_advice_cannot_qualify_declared_only_coding_or_relax_local_only(self):
        with provider("LOCAL_DESTINATION") as (local, local_requests), \
             provider("CLOUD_DESTINATION") as (cloud, cloud_requests), \
             provider("LOCAL") as (classifier, classifier_requests):
            value = self.shadow_configuration(local, cloud, classifier, mode="auto")
            value["route_admission"]["local_routes"]["local-code"]["task_types"] = ["code_change"]
            with self.connected_client(value) as submit:
                request = {"model": "cloud-code", "input": "Fix the arithmetic function in this Python code."}
                result = submit(request=request)
                self.assertEqual((result["status"], result["headers"].get("x-hormuz-destination-locality")), (200, "cloud"))
                history = self.settled_attempt_history(submit, result["headers"]["x-hormuz-work-id"])
                advice = history["body"]["attempts"][0]["shadow_routing"]
                self.assertEqual((advice["recommendation"], advice["actual_locality"], advice["routing_effect"]),
                                 ("local", "cloud", "none"))
                denied = submit(request=request, headers={"X-Hormuz-Routing-Mode": "local_only"})
                self.assertEqual(denied["status"], 503, denied)
            self.assertEqual((len(local_requests), len(cloud_requests), len(classifier_requests)), (0, 1, 1))

    def test_local_only_denial_precedes_shadow_classification(self):
        with provider("LOCAL_DESTINATION", health_status=503) as (local, local_requests), \
             provider("CLOUD_DESTINATION") as (cloud, cloud_requests), \
             provider("CLOUD") as (classifier, classifier_requests):
            with self.connected_client(self.shadow_configuration(local, cloud, classifier)) as submit:
                result = submit(request={"input": "Summarize this fixture."})
                self.assertEqual((result["status"], result["body"]["error"]["code"]),
                                 (503, "hormuz_route_local_route_unavailable"))
                self.assertEqual(submit(method="GET", path="/work/state")["body"]["works"], [])
            self.assertEqual((classifier_requests, local_requests, cloud_requests), ([], [], []))

    def test_invalid_model_advice_stays_content_free_and_sees_only_redacted_input(self):
        private_answer = "LOCAL followed by PRIVATE_CLASSIFIER_REPLY"
        with provider("LOCAL_DESTINATION") as (local, local_requests), \
             provider("CLOUD_DESTINATION") as (cloud, cloud_requests), \
             provider(private_answer) as (classifier, classifier_requests):
            with self.connected_client(self.shadow_configuration(local, cloud, classifier)) as submit:
                response = submit(request={"input": "Summarize fixture-coding-client-credential."})
                self.assertEqual(response["status"], 200)
                work = response["headers"]["x-hormuz-work-id"]
                public = submit(method="GET", path=f"/work/jobs/{work}")["body"]
                advice = public["attempts"][0]["shadow_routing"]
                self.assertEqual((advice["status"], advice["recommendation"]), ("invalid_response", "unknown"))
                self.assertNotIn("PRIVATE_CLASSIFIER_REPLY", json.dumps(public))
                task = json.loads(classifier_requests[0]["body"]["input"])["task"]
                self.assertEqual(task, "Summarize [REDACTED:HORMUZ_SECRET].")
            self.assertEqual((len(classifier_requests), len(local_requests), len(cloud_requests)), (1, 1, 0))

    def test_connect_rejects_cloud_classifiers_active_mode_and_unbounded_settings(self):
        with provider("LOCAL_DESTINATION") as (local, local_requests), \
             provider("CLOUD_DESTINATION") as (cloud, cloud_requests), \
             provider("LOCAL") as (classifier, classifier_requests):
            value = self.shadow_configuration(local, cloud, classifier)
            for change in ({"model": "cloud-code"}, {"mode": "active"}, {"timeout_seconds": 6},
                           {"max_input_bytes": 1_000_000}):
                with self.subTest(change=change):
                    value["shadow_router"] = {"schema_version": 1, "mode": "shadow", "model": "local-router",
                                              "local_model": "local-code", **change}
                    path = self.root / "invalid.json"
                    path.write_text(json.dumps(value))
                    result = subprocess.run([sys.executable, "-m", "hormuz", "--config", str(path),
                                             "client", "config", "codex"],
                                            env=self.environment, capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 2)
                    self.assertNotIn("Traceback", result.stderr)
            self.assertEqual((classifier_requests, local_requests, cloud_requests), ([], [], []))

    def test_incomplete_or_malformed_classifier_results_remain_unknown(self):
        for response_fields in ({"status": "incomplete"}, {"usage": None}, {"error": {"code": "fixture_error"}}):
            with self.subTest(response_fields=response_fields), \
                 provider("LOCAL_DESTINATION") as (local, _), \
                 provider("CLOUD_DESTINATION") as (cloud, cloud_requests), \
                 provider("LOCAL", response_fields=response_fields) as (classifier, _):
                with self.connected_client(self.shadow_configuration(local, cloud, classifier)) as submit:
                    response = submit(request={"input": "Summarize this fixture."})
                    self.assertEqual(response["status"], 200)
                    work = response["headers"]["x-hormuz-work-id"]
                    advice = submit(method="GET", path=f"/work/jobs/{work}/attempts")["body"]["attempts"][0]["shadow_routing"]
                    self.assertEqual((advice["status"], advice["recommendation"]), ("invalid_response", "unknown"))
                    validate_contract(advice)
                self.assertEqual(cloud_requests, [])

if __name__ == "__main__":
    unittest.main()
