"""Explicit qualification probes through the approved connected-client workflow."""
import hashlib
import json
import platform
import socket
import time
import unittest

from tests import test_connected_client_lifetime_workflow as lifetime
from hormuz.contracts import validate_contract


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


class CodingProbeWorkflowTests(unittest.TestCase):
    setUp = lifetime.ConnectedClientLifetimeWorkflowTests.setUp
    command = lifetime.ConnectedClientLifetimeWorkflowTests.command
    client = lifetime.ConnectedClientLifetimeWorkflowTests.client
    managed_configuration = lifetime.ConnectedClientLifetimeWorkflowTests.managed_configuration

    def prepare(self, cloud):
        value, _, engine = self.managed_configuration(cloud)
        value["upstream_timeout_seconds"] = 6
        value["ai_work"] = {"enabled": True, "database": str(self.root / "work.sqlite3"),
                            "tool_capable_aliases": ["local-code"]}
        route = value["model_routes"]["local-code"]
        route["capabilities"].extend(["tools", "codex_http", "codex_http_0160"])
        qualification = value["route_admission"]["local_routes"]["local-code"]
        qualification["task_types"] = ["code_change"]
        (self.root / "client-version.txt").write_text("0.160.0")
        self.path.write_text(json.dumps(value))
        request = {"model": "local-code", "max_output_tokens": 8,
                   "input": [{"role": "user", "content": "Fix the arithmetic function in this Python code."}],
                   "tools": [{"type": "function", "name": "fixture_tool", "parameters": {"type": "object"}}]}
        request_file = self.root / "request.json"
        request_file.write_text(json.dumps(request))
        profile = {"store": False, "service_tier": "default", "tools": request["tools"]}
        plan = {"schema_id": "hormuz.coding-probe-plan", "schema_version": 1,
                "campaign_id": "synthetic-probe-v1", "plan_sha256": "c" * 64,
                "case_id": "development-fixture", "task_sha256": "a" * 64,
                "checks_sha256": "b" * 64, "model": "local-code",
                "first_input_sha256": digest(request["input"]), "max_requests": 2,
                "max_elapsed_seconds": 6,
                "subject": {"client_version": "0.160.0", "system": platform.system(),
                    "machine": platform.machine(), "runtime_profile": "managed-llama.v1",
                    "runtime_sha256": value["managed_llama"]["runtime_sha256"],
                    "model_revision": route["model_revision"], "upstream_model": route["upstream_model"],
                    "protocol": "openai", "capabilities": route["capabilities"],
                    "max_request_bytes": qualification["max_request_bytes"],
                    "max_output_tokens": 8, "upstream_timeout_seconds": 6,
                    "request_profile_sha256": digest(profile)}}
        plan_file = self.root / "probe.json"
        plan_file.write_text(json.dumps(plan))
        return value, engine, request, request_file, plan, plan_file

    def test_explicit_probe_does_not_admit_ordinary_unqualified_coding(self):
        with lifetime.provider("CLOUD_MUST_STAY_UNUSED") as (cloud, cloud_requests):
            _, engine, _, request_file, _, plan_file = self.prepare(cloud)
            try:
                self.command("connect")
                options = ("request-file", str(request_file), "bound-conversation")
                self.assertEqual(self.client(*options)["status"], 503)
                armed = json.loads(self.command("qualify", "--plan", str(plan_file)).stdout)
                validate_contract(armed)
                self.assertEqual(armed["state"], "armed")
                result = self.client(*options, "qualification-probe", armed["probe_id"])
                self.assertEqual(result["status"], 200, result)
                self.assertEqual(result["headers"]["x-hormuz-routing-reason"], "qualification_probe")
                self.assertEqual(result["headers"]["x-hormuz-qualification-probe"], armed["probe_id"])
                self.assertEqual(self.client(*options)["status"], 503)
                probe = json.loads(self.command("qualify").stdout)
                validate_contract(probe)
                self.assertEqual((probe["submitted_requests"], probe["automatic_admission_effect"]), (1, "none"))
                routing = json.loads(self.command("status", "--routing").stdout)
                self.assertEqual((routing["routes"][0]["coding_readiness"],
                                  routing["routes"][0]["automatic_task_types"]), ("unknown", []))
                revoked = json.loads(self.command("qualify", "--revoke").stdout)
                self.assertEqual(revoked["state"], "revoked")
                self.assertNotEqual(self.client(*options, "qualification-probe", armed["probe_id"])["status"], 200)
                self.assertEqual(cloud_requests, [])
                self.assertEqual((engine / "inference-count").read_text().splitlines(), ["1"])
            finally:
                self.command("disconnect")

    def test_probe_deadline_bounds_inflight_generation_without_cloud_or_qualification(self):
        with lifetime.provider("CLOUD_MUST_STAY_UNUSED") as (cloud, cloud_requests):
            value, engine, _, request_file, plan, plan_file = self.prepare(cloud)
            executable = engine / "llama-server"
            executable.write_text(executable.read_text().replace(
                '        result={"id":"managed-fixture"',
                '        time.sleep(4)\n        result={"id":"managed-fixture"'))
            # The controlled external runtime changed, so update its verified
            # archive and the corresponding public subject before connecting.
            import tarfile
            value = json.loads(self.path.read_text())
            archive = self.root / "runtime.tar.gz"
            with tarfile.open(archive, "w:gz") as bundle:
                for path in engine.iterdir():
                    bundle.add(path, arcname=path.name)
            value["managed_llama"]["runtime_sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
            self.path.write_text(json.dumps(value))
            plan["subject"]["runtime_sha256"] = value["managed_llama"]["runtime_sha256"]
            plan["max_elapsed_seconds"] = 1
            plan_file.write_text(json.dumps(plan))
            try:
                self.command("connect")
                armed = json.loads(self.command("qualify", "--plan", str(plan_file)).stdout)
                started = time.monotonic()
                result = self.client("request-file", str(request_file), "bound-conversation",
                                     "qualification-probe", armed["probe_id"])
                self.assertIn(result["status"], (502, 504), result)
                self.assertLess(time.monotonic() - started, 3)
                self.assertEqual((engine / "inference-count").read_text().splitlines(), ["1"])
                status = json.loads(self.command("qualify").stdout)
                self.assertEqual((status["state"], status["remaining_ms"]), ("expired", 0))
                route = json.loads(self.command("status", "--routing").stdout)["routes"][0]
                self.assertEqual((route["coding_readiness"], route["automatic_task_types"]), ("unknown", []))
                self.assertEqual(cloud_requests, [])
            finally:
                disconnected = json.loads(self.command("disconnect").stdout)
            self.assertEqual(self.client("inspect")["model"], "original-model")
            self.assertEqual(disconnected["local_runtime"], {"ownership": "owned", "state": "stopped"})
            from urllib.parse import urlsplit
            endpoint = urlsplit(value["upstreams"]["local-llama"]["base_url"])
            with self.assertRaises(OSError):
                socket.create_connection((endpoint.hostname, endpoint.port), timeout=0.5)

    def test_probe_pins_one_tool_conversation_and_cannot_replay_or_change_task(self):
        with lifetime.provider("CLOUD_MUST_STAY_UNUSED") as (cloud, cloud_requests):
            _, engine, request, request_file, _, plan_file = self.prepare(cloud)
            try:
                self.command("connect")
                probe = json.loads(self.command("qualify", "--plan", str(plan_file)).stdout)
                options = ("request-file", str(request_file), "bound-conversation",
                           "qualification-probe", probe["probe_id"])
                first = self.client(*options)
                self.assertEqual(first["status"], 200, first)
                self.assertNotEqual(self.client(*options)["status"], 200)
                continuation = {**request, "input": request["input"] + first["body"]["output"] + [
                    {"type": "function_call_output", "call_id": "fixture-call", "output": "Fixture result"}]}
                changed_task = {**continuation, "input": continuation["input"] + [
                    {"role": "user", "content": "Fix a different Python implementation."}]}
                request_file.write_text(json.dumps(changed_task))
                self.assertNotEqual(self.client(*options)["status"], 200)
                request_file.write_text(json.dumps({**continuation, "instructions": "Unfrozen instructions."}))
                self.assertNotEqual(self.client(*options)["status"], 200)
                request_file.write_text(json.dumps(continuation))
                self.assertNotEqual(self.client("request-file", str(request_file),
                    "qualification-probe", probe["probe_id"])["status"], 200)
                self.assertNotEqual(self.client("request-file", str(request_file), "bound-conversation")["status"], 200)
                second = self.client(*options)
                self.assertEqual(second["status"], 200, second)
                self.assertEqual(second["headers"]["x-hormuz-routing-reason"], "session_affinity")
                for header in ("x-hormuz-work-id", "x-hormuz-destination-generation"):
                    self.assertEqual(first["headers"][header], second["headers"][header])
                self.assertEqual(second["body"]["output"][0]["content"][0]["text"], "MANAGED_LOCAL")
                self.assertNotEqual(self.client(*options)["status"], 200)
                exhausted = json.loads(self.command("qualify").stdout)
                self.assertEqual((exhausted["state"], exhausted["submitted_requests"]), ("exhausted", 2))
                self.assertEqual((engine / "inference-count").read_text().splitlines(), ["1", "1"])
                self.assertEqual(cloud_requests, [])
            finally:
                self.command("disconnect")

    def test_unfrozen_requests_and_mismatched_plans_cannot_reach_an_engine(self):
        with lifetime.provider("CLOUD_MUST_STAY_UNUSED") as (cloud, cloud_requests):
            _, engine, request, request_file, plan, plan_file = self.prepare(cloud)
            try:
                self.command("connect")
                for changed in ({**plan, "max_requests": 0},
                                {**plan, "subject": {**plan["subject"], "runtime_sha256": "d" * 64}}):
                    plan_file.write_text(json.dumps(changed))
                    denied = self.command("qualify", "--plan", str(plan_file), check=False)
                    self.assertEqual(denied.returncode, 2)
                    self.assertEqual(json.loads(self.command("qualify").stdout)["state"], "inactive")
                plan_file.write_text(json.dumps(plan))
                probe = json.loads(self.command("qualify", "--plan", str(plan_file)).stdout)
                repeated = self.command("qualify", "--plan", str(plan_file), check=False)
                self.assertEqual(json.loads(repeated.stderr)["error"]["code"], "probe_already_active")
                options = ("request-file", str(request_file), "bound-conversation",
                           "qualification-probe", probe["probe_id"])
                for changed in ({**request, "input": [{"role": "user", "content": "Fix a different Python function."}]},
                                {**request, "temperature": 0.1}):
                    request_file.write_text(json.dumps(changed))
                    self.assertNotEqual(self.client(*options)["status"], 200)
                request_file.write_text(json.dumps(request))
                self.assertNotEqual(self.client(*options, "missing-client-version")["status"], 200)
                self.assertNotEqual(self.client(*options, "client-agent", "codex_exec/0.147.0")["status"], 200)
                self.assertNotEqual(self.client("request-file", str(request_file), "bound-conversation",
                    "qualification-probe", "0" * 32)["status"], 200)
                self.assertEqual(json.loads(self.command("qualify").stdout)["submitted_requests"], 0)
                self.assertFalse((engine / "inference-count").exists())
                self.assertEqual(cloud_requests, [])
            finally:
                self.command("disconnect")

    def test_probe_cannot_repeat_a_successful_tool_submission_with_spare_budget(self):
        with lifetime.provider("CLOUD_MUST_STAY_UNUSED") as (cloud, cloud_requests):
            _, engine, request, request_file, plan, plan_file = self.prepare(cloud)
            plan["max_requests"] = 3
            plan_file.write_text(json.dumps(plan))
            try:
                self.command("connect")
                probe = json.loads(self.command("qualify", "--plan", str(plan_file)).stdout)
                options = ("request-file", str(request_file), "bound-conversation",
                           "qualification-probe", probe["probe_id"])
                first = self.client(*options)
                self.assertEqual(first["status"], 200, first)
                request_file.write_text(json.dumps({**request, "input": request["input"] + first["body"]["output"] + [
                    {"type": "function_call_output", "call_id": "fixture-call", "output": "Fixture result"}]}))
                second = self.client(*options)
                self.assertEqual(second["status"], 200, second)
                replayed = self.client(*options)
                self.assertEqual(replayed["status"], 403, replayed)
                self.assertEqual(json.loads(self.command("qualify").stdout)["submitted_requests"], 2)
                self.assertEqual((engine / "inference-count").read_text().splitlines(), ["1", "1"])
                self.assertEqual(cloud_requests, [])
            finally:
                self.command("disconnect")

    def test_probe_suppresses_extra_shadow_inference_without_changing_ordinary_advice(self):
        with lifetime.provider("CLOUD_MUST_STAY_UNUSED") as (cloud, cloud_requests), \
             lifetime.provider("CLOUD") as (classifier, classifier_requests):
            value, engine, _, request_file, _, plan_file = self.prepare(cloud)
            value["route_admission"]["local_routes"]["local-code"]["task_types"].append("summarization")
            value["upstreams"]["local-classifier"] = {"protocol": "openai", "base_url": classifier,
                                                       "locality": "local", "authentication": "none"}
            value["model_routes"]["local-router"] = {"protocol": "openai", "destination": "local-classifier",
                "upstream_model": "fixture-router", "model_revision": "fixture-router-v1", "capabilities": ["responses", "text"]}
            value["policies"]["organization"]["allowed_models"].append("local-router")
            value["shadow_router"] = {"schema_version": 1, "mode": "shadow", "model": "local-router", "local_model": "local-code"}
            self.path.write_text(json.dumps(value))
            try:
                self.command("connect")
                ordinary = self.client()
                self.assertEqual(ordinary["status"], 200)
                self.assertEqual(len(classifier_requests), 1)
                self.assertEqual(self.client("code-change")["status"], 503)
                probe = json.loads(self.command("qualify", "--plan", str(plan_file)).stdout)
                result = self.client("request-file", str(request_file), "bound-conversation",
                                     "qualification-probe", probe["probe_id"])
                self.assertEqual(result["status"], 200, result)
                self.assertEqual(result["headers"]["x-hormuz-contract"], "hormuz.coding-probe-relay;v=1")
                self.assertEqual(len(classifier_requests), 1)
                self.assertEqual((engine / "inference-count").read_text().splitlines(), ["1", "1"])
                route = json.loads(self.command("status", "--routing").stdout)["routes"][0]
                self.assertEqual((route["coding_readiness"], route["automatic_task_types"]), ("unknown", ["summarization"]))
                self.assertEqual(cloud_requests, [])
            finally:
                self.command("disconnect")
