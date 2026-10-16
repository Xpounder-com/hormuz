"""Native inference at the approved connect/request/result/disconnect seam."""
import json
import time
import os
import threading
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
import unittest

from tests import test_execution_destination_workflow as workflow
from tests.test_native_codex_catalog_workflow import native_backend
from hormuz.contracts import validate_contract, validate_contract_manifest, ContractValidationError


NATIVE_HEADERS = {"X-Gateway-Key": "fixture-coding-client-credential",
                  "Authorization": "Bearer NATIVE_CHATGPT_SENTINEL",
                  "ChatGPT-Account-ID": "fixture-workspace"}
NATIVE_PATH = "/backend-api/codex/responses"


def completed(response_id="native-response", *, output=None):
    return {"type": "response.completed", "response": {
        "id": response_id, "model": "fixture-cloud-model", "status": "completed",
        "output": output or [{"id": "native-message", "type": "message", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": "NATIVE_RESULT", "annotations": []}]}],
        "usage": {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6}}}


def stream_reply(handler, _value, _number):
    data = ("event: response.completed\ndata: " + json.dumps(completed()) + "\n\n").encode()
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)
    handler.wfile.flush()


class NativeCodexResponsesWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.fixture = workflow.ExecutionDestinationWorkflowTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def configuration(self, local, cloud):
        value = self.fixture.configuration(local, cloud)
        value["native_codex_responses"] = True
        value["upstreams"]["native-codex"] = {"protocol": "openai", "locality": "cloud",
            "authentication": "codex_chatgpt", "base_url": "https://chatgpt.com/backend-api/codex"}
        value["model_routes"]["native-code"] = {"protocol": "openai", "destination": "native-codex",
                                               "upstream_model": "fixture-cloud-model"}
        value["policies"]["organization"]["allowed_models"].append("native-code")
        return value

    @staticmethod
    def native(submit, **options):
        return submit(path=NATIVE_PATH, absolute_path=True,
            headers={**NATIVE_HEADERS, **options.pop("headers", {})},
            request={"model": "fixture-cloud-model", "stream": True, "store": False,
                     **options.pop("request", {})}, **options)

    def test_native_response_keeps_credentials_and_plan_execution_on_the_fixed_backend(self):
        with workflow.provider("LOCAL_UNUSED") as (local, local_requests), \
             workflow.provider("PAID_API_UNUSED") as (cloud, cloud_requests), \
             native_backend({}, response_handler=stream_reply) as native_requests:
            with self.fixture.connected_client(self.configuration(local, cloud)) as submit:
                result = self.native(submit, headers={"X-Api-Key": "UNRELATED_API_SENTINEL",
                    "Cookie": "unrelated=COOKIE_SENTINEL"})
                self.assertEqual(result["status"], 200, result)
                self.assertIn("NATIVE_RESULT", result["body"])
                self.assertIn("response.completed", result["body"])
                self.assertEqual(result["headers"]["x-hormuz-contract"], "hormuz.native-codex-responses;v=1")
                self.assertEqual(result["headers"]["x-hormuz-billing"], "chatgpt_plan")
                self.assertEqual(result["headers"]["x-hormuz-cost-status"], "unknown")
                self.assertEqual(result["headers"]["x-hormuz-destination"], "native-codex")
                self.assertNotIn("SENTINEL", json.dumps(result))
            self.assertEqual((len(local_requests), len(cloud_requests), len(native_requests)), (0, 0, 1))
            request = native_requests[0]
            self.assertEqual(request["path"], NATIVE_PATH)
            self.assertEqual(request["body"]["model"], "fixture-cloud-model")
            self.assertIs(request["body"]["store"], False)
            self.assertEqual(request["headers"]["authorization"], NATIVE_HEADERS["Authorization"])
            self.assertEqual(request["headers"]["chatgpt-account-id"], "fixture-workspace")
            self.assertEqual(request["headers"]["host"], "chatgpt.com")
            self.assertFalse({"x-gateway-key", "x-api-key", "cookie"} & request["headers"].keys())

    def test_exact_native_alias_wins_over_slugs_and_other_destination_aliases_stay_blocked(self):
        with workflow.provider("LOCAL_UNUSED") as (local, local_requests), \
             workflow.provider("PAID_API_UNUSED") as (cloud, cloud_requests), \
             native_backend({}, response_handler=stream_reply) as native_requests:
            for case in ("exact_alias", "ambiguous_slug", "api_alias"):
                with self.subTest(case=case):
                    value = self.configuration(local, cloud)
                    alias = "other-native" if case == "ambiguous_slug" else "fixture-cloud-model"
                    value["model_routes"][alias] = {"protocol": "openai",
                        "destination": "openai" if case == "api_alias" else "native-codex",
                        "upstream_model": "fixture-cloud-model"}
                    value["policies"]["organization"]["allowed_models"].append(alias)
                    if case == "exact_alias":
                        value["policies"]["organization"]["allowed_models"].remove("native-code")
                    before = len(native_requests)
                    with self.fixture.connected_client(value) as submit:
                        result = self.native(submit)
                        self.assertEqual(result["status"], 200 if case == "exact_alias" else 403, result)
                    self.assertEqual(len(native_requests), before + (case == "exact_alias"))
            self.assertEqual((local_requests, cloud_requests), ([], []))

    def test_client_receives_an_incremental_event_before_native_completion_is_released(self):
        signal = self.fixture.root / "first-event"
        observed = []
        def reply(handler, _value, _number):
            handler.send_response(200)
            handler.send_header("Content-Type", "text/event-stream")
            handler.end_headers()
            handler.wfile.write(b'event: response.created\ndata: {"type":"response.created"}\n\n')
            handler.wfile.flush()
            deadline = time.monotonic() + 2
            while not signal.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            observed.append(signal.exists())
            handler.wfile.write(("data: " + json.dumps(completed()) + "\n\n").encode())
            handler.wfile.flush()
        with workflow.provider("LOCAL_UNUSED") as (local, local_requests), \
             workflow.provider("PAID_API_UNUSED") as (cloud, cloud_requests), \
             native_backend({}, response_handler=reply) as native_requests:
            with self.fixture.connected_client(self.configuration(local, cloud)) as submit:
                result = self.native(submit, first_event_signal=str(signal))
                self.assertEqual(result["status"], 200, result)
                self.assertEqual(observed, [True], "native completion was released before the client received an event")
                self.assertIn("NATIVE_RESULT", result["body"])
            self.assertEqual((len(local_requests), len(cloud_requests), len(native_requests)), (0, 0, 1))

    def test_native_execution_respects_every_permission_ceiling_before_egress(self):
        with workflow.provider("LOCAL_UNUSED") as (local, local_requests), \
             workflow.provider("PAID_API_UNUSED") as (cloud, cloud_requests), \
             native_backend({}, response_handler=stream_reply) as native_requests:
            for case in ("default_off", "local_only", "request_local_only", "identity_client",
                         "actor_client", "team_client", "organization_model", "actor_model", "team_model",
                         "organization_budget", "team_budget", "actor_budget", "token_budget"):
                with self.subTest(case=case):
                    value = self.configuration(local, cloud)
                    headers = {}
                    if case == "default_off":
                        value.pop("native_codex_responses")
                    elif case == "local_only":
                        value["route_admission"] = {"mode": "local_only"}
                    elif case == "request_local_only":
                        headers["X-Hormuz-Routing-Mode"] = "local_only"
                    elif case == "identity_client":
                        value["identities"][0]["allowed_clients"] = ["claude-code"]
                    elif case in {"actor_client", "team_client", "actor_model", "team_model"}:
                        group, owner = ("actors", "developer") if case.startswith("actor") else ("teams", "local")
                        value["policies"][group] = {owner: {"allowed_clients": ["claude-code"]}
                            if case.endswith("client") else {"allowed_models": ["local-code"]}}
                    elif case == "organization_model":
                        value["policies"]["organization"].update(allowed_models=["local-code"], fallback_model="local-code")
                    elif case in {"team_budget", "actor_budget"}:
                        group, owner = ("actors", "developer") if case.startswith("actor") else ("teams", "local")
                        value["policies"][group] = {owner: {"monthly_budget_usd": 10}}
                    else:
                        value["policies"]["organization"]["monthly_token_limit" if case == "token_budget" else "monthly_budget_usd"] = 100
                    with self.fixture.connected_client(value) as submit:
                        result = self.native(submit, headers=headers)
                        self.assertEqual(result["status"], 404 if case == "default_off" else 403, result)
                        self.assertEqual((local_requests, cloud_requests, native_requests), ([], [], []))
                        self.assertNotIn("SENTINEL", json.dumps(result))

    def test_native_egress_applies_the_shared_output_cap_and_secret_redaction_or_denial(self):
        secrets = "fixture-coding-client-credential and fixture-cloud-destination-credential and NATIVE_CHATGPT_SENTINEL"
        with workflow.provider("LOCAL_UNUSED") as (local, local_requests), \
             workflow.provider("PAID_API_UNUSED") as (cloud, cloud_requests), \
             native_backend({}, response_handler=stream_reply) as native_requests:
            value = self.configuration(local, cloud)
            with self.fixture.connected_client(value) as submit:
                result = self.native(submit, request={"max_output_tokens": 80, "input": secrets})
                self.assertEqual(result["status"], 200, result)
                self.assertEqual(native_requests[0]["body"]["max_output_tokens"], 8)
                self.assertEqual(native_requests[0]["body"]["input"],
                    "[REDACTED:HORMUZ_SECRET] and [REDACTED:HORMUZ_SECRET] and [REDACTED:HORMUZ_SECRET]")
                self.assertEqual(result["headers"]["x-hormuz-redactions"], "3")
            value["egress_controls"] = {"secrets": {"mode": "deny"}}
            with self.fixture.connected_client(value) as submit:
                result = self.native(submit, request={"input": secrets})
                self.assertEqual((result["status"], result["body"]["error"]["code"]), (403, "hormuz_secret_detected"))
            self.assertEqual((len(local_requests), len(cloud_requests), len(native_requests)), (0, 0, 1))

    def test_native_redaction_cannot_expand_a_request_beyond_its_egress_byte_limit(self):
        with workflow.provider("LOCAL_UNUSED") as (local, local_requests), \
             workflow.provider("PAID_API_UNUSED") as (cloud, cloud_requests), \
             native_backend({}, response_handler=stream_reply) as native_requests:
            value = self.configuration(local, cloud)
            value["max_request_bytes"] = 1024
            with self.fixture.connected_client(value) as submit:
                oversized = self.native(submit, headers={"Authorization": "Bearer q"},
                    request={"input": "q" * 100})
                self.assertEqual(oversized["status"], 413, oversized)
                self.assertEqual(native_requests, [], "expanded secret material reached the native provider")
                small = self.native(submit, headers={"Authorization": "Bearer q"}, request={"input": "q"})
                self.assertEqual(small["status"], 200, small)
                self.assertEqual(native_requests[0]["body"]["input"], "[REDACTED:HORMUZ_SECRET]")
                self.assertLessEqual(len(json.dumps(native_requests[0]["body"], separators=(",", ":")).encode()), 1024)
            self.assertEqual((len(local_requests), len(cloud_requests), len(native_requests)), (0, 0, 1))

    def test_native_status_counts_completed_execution_and_observed_usage_with_unknown_cost(self):
        with workflow.provider("LOCAL_UNUSED") as (local, local_requests), \
             workflow.provider("PAID_API_UNUSED") as (cloud, cloud_requests), \
             native_backend({}, response_handler=stream_reply) as native_requests:
            with self.fixture.connected_client(self.configuration(local, cloud)) as submit:
                self.assertEqual(self.native(submit)["status"], 200)
                status = submit(method="GET", path="/v1/router/native-codex", absolute_path=True,
                                headers={"X-Gateway-Key": NATIVE_HEADERS["X-Gateway-Key"]})
                self.assertEqual(status["status"], 200, status)
                view = status["body"]
                self.assertEqual((view["schema_id"], view["schema_version"]), ("hormuz.native-codex-status", 1))
                self.assertEqual((view["requests"], view["active"], view["succeeded"], view["failed"], view["outcome_unknown"]),
                                 (1, 0, 1, 0, 0))
                self.assertEqual((view["observed_input_tokens"], view["observed_output_tokens"],
                                  view["observed_usage_requests"], view["usage_unknown_requests"]), (4, 2, 1, 0))
                self.assertEqual((view["billing"], view["cost_status"], view["cost_usd"]), ("chatgpt_plan", "unknown", None))
                self.assertNotIn("SENTINEL", json.dumps(view))
                generic = submit(method="GET", path="/gateway/usage")
                self.assertEqual(generic["body"]["requests"], 0)
            self.assertEqual((len(local_requests), len(cloud_requests), len(native_requests)), (0, 0, 1))

    def test_only_a_valid_native_terminal_can_establish_a_known_execution_outcome(self):
        wrong_model = completed()
        wrong_model["response"]["model"] = "wrong-model"
        wrong_state = completed()
        wrong_state["response"]["status"] = "failed"
        missing_usage = completed()
        missing_usage["response"].pop("usage")
        failed = {"type": "response.failed", "response": {"id": "failed-response",
            "model": "fixture-cloud-model", "status": "failed", "error": {"code": "fixture_failure"}}}
        incomplete = {"type": "response.incomplete", "response": {"id": "incomplete-response",
            "model": "fixture-cloud-model", "status": "incomplete"}}
        cases = [
            ("event_name_only", b"event: response.completed\n\n", "outcome_unknown", 1),
            ("done_only", b"data: [DONE]\n\n", "outcome_unknown", 1),
            ("truncated_event", ("data: " + json.dumps(completed())).encode(), "outcome_unknown", 1),
            ("malformed_event", b"data: {broken}\n\n", "outcome_unknown", 1),
            ("wrong_model", ("data: " + json.dumps(wrong_model) + "\n\n").encode(), "outcome_unknown", 1),
            ("wrong_state", ("data: " + json.dumps(wrong_state) + "\n\n").encode(), "outcome_unknown", 1),
            ("duplicate_field", b'data: {"type":"response.completed","type":"response.created"}\n\n', "outcome_unknown", 1),
            ("missing_usage", ("data: " + json.dumps(missing_usage) + "\n\n").encode(), "succeeded", 1),
            ("failed", ("data: " + json.dumps(failed) + "\n\n").encode(), "failed", 1),
            ("incomplete", ("data: " + json.dumps(incomplete) + "\n\n").encode(), "failed", 1),
        ]
        with workflow.provider("LOCAL_UNUSED") as (local, local_requests), \
             workflow.provider("PAID_API_UNUSED") as (cloud, cloud_requests):
            for name, data, outcome, unknown_usage in cases:
                with self.subTest(case=name):
                    def reply(handler, _value, _number):
                        handler.send_response(200)
                        handler.send_header("Content-Type", "text/event-stream")
                        handler.send_header("Content-Length", str(len(data)))
                        handler.end_headers()
                        handler.wfile.write(data)
                    with native_backend({}, response_handler=reply) as native_requests:
                        with self.fixture.connected_client(self.configuration(local, cloud)) as submit:
                            result = self.native(submit)
                            self.assertEqual(result["status"], 200, result)
                            status = submit(method="GET", path="/v1/router/native-codex", absolute_path=True,
                                            headers={"X-Gateway-Key": NATIVE_HEADERS["X-Gateway-Key"]})["body"]
                            self.assertEqual((status["succeeded"], status["failed"], status["outcome_unknown"]),
                                (1, 0, 0) if outcome == "succeeded" else (0, 1, 0) if outcome == "failed" else (0, 0, 1), status)
                            self.assertEqual(status["usage_unknown_requests"], unknown_usage)
                            if name in {"failed", "incomplete"}:
                                self.assertEqual(result["body"], data.decode())
                        self.assertEqual(len(native_requests), 1)
            self.assertEqual((local_requests, cloud_requests), ([], []))

    def test_tool_and_opaque_replay_preserves_native_affinity_and_cannot_cross_owners(self):
        output = [
            {"id": "native-reasoning", "type": "reasoning", "summary": [], "encrypted_content": "OPAQUE_NATIVE_BYTES"},
            {"id": "native-tool", "type": "function_call", "call_id": "native-call", "name": "fixture_tool", "arguments": '{"value":"original"}'},
        ]
        session = {"thread_id": "native-thread", "session_id": "native-session"}
        replay = [{"role": "user", "content": "Use the fixture tool."}, *output,
                  {"type": "function_call_output", "call_id": "native-call", "output": "TOOL_RESULT"}]
        def reply(handler, _value, number):
            event = completed("tool-turn", output=output) if number == 1 else completed("final-turn")
            data = ("data: " + json.dumps(event) + "\n\n").encode()
            handler.send_response(200)
            handler.send_header("Content-Type", "text/event-stream")
            handler.send_header("Content-Length", str(len(data)))
            handler.end_headers()
            handler.wfile.write(data)
        with workflow.provider("LOCAL_UNUSED") as (local, local_requests), \
             workflow.provider("PAID_API_UNUSED") as (cloud, cloud_requests), \
             native_backend({}, response_handler=reply) as native_requests:
            value = self.configuration(local, cloud)
            self.fixture.environment["OTHER_GATEWAY_TOKEN"] = "fixture-other-gateway-credential"
            value["identities"].append({**value["identities"][0], "actor_id": "other", "token_env": "OTHER_GATEWAY_TOKEN"})
            value["model_routes"]["other-native-code"] = {"protocol": "openai", "destination": "native-codex", "upstream_model": "other-native-model"}
            value["policies"]["organization"]["allowed_models"].append("other-native-code")
            with self.fixture.connected_client(value) as submit:
                first = self.native(submit, request={"client_metadata": session, "include": ["reasoning.encrypted_content"]})
                self.assertIn('"call_id": "native-call"', first["body"])
                for case in ("credential", "account", "identity", "model", "session", "session_header", "unknown_call", "changed_arguments", "changed_opaque"):
                    with self.subTest(case=case):
                        headers, request = {}, {"input": json.loads(json.dumps(replay)), "client_metadata": dict(session)}
                        if case == "credential":
                            headers["Authorization"] = "Bearer OTHER_NATIVE_SENTINEL"
                        elif case == "account":
                            headers["ChatGPT-Account-ID"] = "other-workspace"
                        elif case == "identity":
                            headers["X-Gateway-Key"] = "fixture-other-gateway-credential"
                        elif case == "model":
                            request["model"] = "other-native-code"
                        elif case == "session":
                            request["client_metadata"]["thread_id"] = "other-thread"
                        elif case == "session_header":
                            headers["session-id"] = "other-session"
                        elif case == "unknown_call":
                            request["input"][-1]["call_id"] = "unknown-call"
                        elif case == "changed_arguments":
                            request["input"][2]["arguments"] = '{"value":"changed"}'
                        else:
                            request["input"][1]["encrypted_content"] = "CHANGED_OPAQUE_BYTES"
                        denied = self.native(submit, headers=headers, request=request)
                        self.assertEqual(denied["status"], 403, denied)
                        self.assertEqual(len(native_requests), 1)
                final = self.native(submit, request={"input": replay, "client_metadata": session})
                self.assertEqual(final["status"], 200, final)
                self.assertIn("NATIVE_RESULT", final["body"])
            self.assertEqual((len(local_requests), len(cloud_requests), len(native_requests)), (0, 0, 2))
            self.assertEqual(native_requests[1]["body"]["input"], replay)

    def test_stream_identity_must_match_before_success_or_tool_affinity_is_granted(self):
        call = {"id": "bound-tool", "type": "function_call", "call_id": "bound-call",
                "name": "fixture_tool", "arguments": "{}"}
        with workflow.provider("LOCAL_UNUSED") as (local, local_requests), \
             workflow.provider("PAID_API_UNUSED") as (cloud, cloud_requests):
            for case in ("matching", "created_id", "created_model", "delta_id"):
                with self.subTest(case=case):
                    created = {"type": "response.created", "response": {
                        "id": "different-response" if case == "created_id" else "bound-response",
                        "model": "different-model" if case == "created_model" else "fixture-cloud-model",
                        "status": "in_progress", "output": []}}
                    delta = {"type": "response.output_text.delta", "response_id":
                             "different-response" if case == "delta_id" else "bound-response", "delta": "part"}
                    events = [created, delta, completed("bound-response", output=[call])]
                    def reply(handler, value, number):
                        if number != 1:
                            return stream_reply(handler, value, number)
                        data = b"".join(("data: " + json.dumps(event) + "\n\n").encode() for event in events)
                        handler.send_response(200)
                        handler.send_header("Content-Type", "text/event-stream")
                        handler.send_header("Content-Length", str(len(data)))
                        handler.end_headers()
                        handler.wfile.write(data)
                    with native_backend({}, response_handler=reply) as native_requests:
                        with self.fixture.connected_client(self.configuration(local, cloud)) as submit:
                            self.native(submit)
                            status = submit(method="GET", path="/v1/router/native-codex", absolute_path=True,
                                headers={"X-Gateway-Key": NATIVE_HEADERS["X-Gateway-Key"]})["body"]
                            expected = (1, 0) if case == "matching" else (0, 1)
                            self.assertEqual((status["succeeded"], status["outcome_unknown"]), expected, status)
                            replay = self.native(submit, request={"input": [call,
                                {"type": "function_call_output", "call_id": "bound-call", "output": "result"}]})
                            self.assertEqual(replay["status"], 200 if case == "matching" else 403, replay)
                        self.assertEqual(len(native_requests), 2 if case == "matching" else 1)
            self.assertEqual((local_requests, cloud_requests), ([], []))

    def test_malformed_terminal_output_cannot_grant_success_or_tool_affinity(self):
        call = {"id": "untrusted-tool", "type": "function_call", "call_id": "untrusted-call",
                "name": "fixture_tool", "arguments": "{}"}
        invalid_items = [17, {}, {"type": "message", "role": "user", "content": []},
            {"type": "message", "role": "assistant", "content": 17},
            {"type": "message", "role": "assistant", "content": [17]},
            {"type": "message", "role": "assistant", "content": [{"type": []}]},
            {"type": "message", "role": "assistant", "content": [{"type": "output_text"}]}]
        with workflow.provider("LOCAL_UNUSED") as (local, local_requests), \
             workflow.provider("PAID_API_UNUSED") as (cloud, cloud_requests):
            for item in invalid_items:
                with self.subTest(item=item):
                    data = ("data: " + json.dumps(completed(output=[call, item])) + "\n\n").encode()
                    def reply(handler, _value, _number):
                        handler.send_response(200)
                        handler.send_header("Content-Type", "text/event-stream")
                        handler.send_header("Content-Length", str(len(data)))
                        handler.end_headers()
                        handler.wfile.write(data)
                    with native_backend({}, response_handler=reply) as native_requests:
                        with self.fixture.connected_client(self.configuration(local, cloud)) as submit:
                            self.native(submit)
                            status = submit(method="GET", path="/v1/router/native-codex", absolute_path=True,
                                headers={"X-Gateway-Key": NATIVE_HEADERS["X-Gateway-Key"]})["body"]
                            self.assertEqual((status["succeeded"], status["outcome_unknown"]), (0, 1), status)
                            replay = self.native(submit, request={"input": [call,
                                {"type": "function_call_output", "call_id": "untrusted-call", "output": "result"}]})
                            self.assertEqual(replay["status"], 403, replay)
                        self.assertEqual(len(native_requests), 1)
            self.assertEqual((local_requests, cloud_requests), ([], []))

    def test_terminal_output_variants_require_their_native_fields_before_affinity(self):
        call = {"type": "function_call", "call_id": "required-call", "name": "fixture_tool", "arguments": "{}"}
        reasoning = {"id": "required-reasoning", "type": "reasoning", "summary": [], "encrypted_content": "OPAQUE"}
        message = {"id": "required-message", "type": "message", "role": "assistant", "status": "completed",
                   "content": [{"type": "output_text", "text": "valid", "annotations": []}]}
        compaction = {"id": "required-compaction", "type": "compaction", "encrypted_content": "OPAQUE"}
        cases = [("reasoning_id", {k: v for k, v in reasoning.items() if k != "id"}, False),
            ("reasoning_summary", {k: v for k, v in reasoning.items() if k != "summary"}, False),
            ("summary_shape", {**reasoning, "summary": [17]}, False),
            ("summary_text", {**reasoning, "summary": [{"type": "summary_text"}]}, False),
            ("reasoning_content", {**reasoning, "content": [{"type": "reasoning_text", "text": 17}]}, False),
            ("message_id", {k: v for k, v in message.items() if k != "id"}, False),
            ("message_status", {k: v for k, v in message.items() if k != "status"}, False),
            ("text_annotations", {**message, "content": [{"type": "output_text", "text": "invalid"}]}, False),
            ("compaction_id", {k: v for k, v in compaction.items() if k != "id"}, False),
            ("compaction_content", {k: v for k, v in compaction.items() if k != "encrypted_content"}, False),
            ("unknown_variant", {"id": "unknown-item", "type": "unsupported_native_variant"}, False),
            ("valid_reasoning", {**reasoning, "summary": [{"type": "summary_text", "text": "summary"}]}, True),
            ("valid_nullable_reasoning", {**reasoning, "encrypted_content": None}, True),
            ("valid_message", message, True), ("valid_compaction", compaction, True),
            ("valid_custom_tool", {"type": "custom_tool_call", "call_id": "custom-call", "name": "custom", "input": "data"}, True)]
        with workflow.provider("LOCAL_UNUSED") as (local, local_requests), \
             workflow.provider("PAID_API_UNUSED") as (cloud, cloud_requests):
            for name, item, valid in cases:
                with self.subTest(case=name):
                    data = ("data: " + json.dumps(completed(output=[call, item])) + "\n\n").encode()
                    def reply(handler, value, number):
                        if number != 1:
                            return stream_reply(handler, value, number)
                        handler.send_response(200)
                        handler.send_header("Content-Type", "text/event-stream")
                        handler.send_header("Content-Length", str(len(data)))
                        handler.end_headers()
                        handler.wfile.write(data)
                    with native_backend({}, response_handler=reply) as native_requests:
                        with self.fixture.connected_client(self.configuration(local, cloud)) as submit:
                            self.native(submit)
                            status = submit(method="GET", path="/v1/router/native-codex", absolute_path=True,
                                headers={"X-Gateway-Key": NATIVE_HEADERS["X-Gateway-Key"]})["body"]
                            self.assertEqual((status["succeeded"], status["outcome_unknown"]),
                                             (1, 0) if valid else (0, 1), status)
                            replay = self.native(submit, request={"input": [call,
                                {"type": "function_call_output", "call_id": "required-call", "output": "result"}]})
                            self.assertEqual(replay["status"], 200 if valid else 403, replay)
                        self.assertEqual(len(native_requests), 2 if valid else 1)
            self.assertEqual((local_requests, cloud_requests), ([], []))

    def test_native_annotations_require_known_variants_and_fields_before_affinity(self):
        call = {"type": "function_call", "call_id": "annotation-call", "name": "fixture_tool", "arguments": "{}"}
        url = {"type": "url_citation", "url": "https://fixture.invalid/source", "title": "fixture", "start_index": 0, "end_index": 5}
        file = {"type": "file_citation", "file_id": "fixture-file", "filename": "fixture.txt", "index": 0}
        container = {"type": "container_file_citation", "container_id": "fixture-container",
                     "file_id": "fixture-file", "filename": "fixture.txt", "start_index": 0, "end_index": 5}
        cases = [(annotation, False) for annotation in ({}, {"type": "url_citation"},
            {**url, "start_index": True}, {**url, "end_index": -1}, {**url, "start_index": 6},
            {**file, "index": "0"}, {k: v for k, v in file.items() if k != "filename"},
            {k: v for k, v in container.items() if k != "container_id"}, {"type": "unknown_annotation"})]
        cases += [(annotation, True) for annotation in (url, file, container,
                  {"type": "file_path", "file_id": "fixture-file", "index": 0})]
        with workflow.provider("LOCAL_UNUSED") as (local, local_requests), \
             workflow.provider("PAID_API_UNUSED") as (cloud, cloud_requests):
            for annotation, valid in cases:
                with self.subTest(annotation=annotation):
                    message = completed()["response"]["output"][0]
                    message["content"][0]["annotations"] = [annotation]
                    data = ("data: " + json.dumps(completed(output=[call, message])) + "\n\n").encode()
                    def reply(handler, value, number):
                        if number != 1:
                            return stream_reply(handler, value, number)
                        handler.send_response(200)
                        handler.send_header("Content-Type", "text/event-stream")
                        handler.send_header("Content-Length", str(len(data)))
                        handler.end_headers()
                        handler.wfile.write(data)
                    with native_backend({}, response_handler=reply) as native_requests:
                        with self.fixture.connected_client(self.configuration(local, cloud)) as submit:
                            result = self.native(submit)
                            status = submit(method="GET", path="/v1/router/native-codex", absolute_path=True,
                                headers={"X-Gateway-Key": NATIVE_HEADERS["X-Gateway-Key"]})["body"]
                            self.assertEqual((status["succeeded"], status["outcome_unknown"]),
                                             (1, 0) if valid else (0, 1), status)
                            if valid:
                                self.assertEqual(result["body"], data.decode())
                            replay = self.native(submit, request={"input": [call,
                                {"type": "function_call_output", "call_id": "annotation-call", "output": "result"}]})
                            self.assertEqual(replay["status"], 200 if valid else 403, replay)
                        self.assertEqual(len(native_requests), 2 if valid else 1)
            self.assertEqual((local_requests, cloud_requests), ([], []))

    def test_nullable_native_tool_ids_preserve_call_binding_and_continuation(self):
        with workflow.provider("LOCAL_UNUSED") as (local, local_requests), \
             workflow.provider("PAID_API_UNUSED") as (cloud, cloud_requests):
            for kind in ("function_call", "custom_tool_call"):
                with self.subTest(kind=kind):
                    field = "arguments" if kind == "function_call" else "input"
                    original = '{"value":"original"}' if kind == "function_call" else "original"
                    changed = '{"value":"changed"}' if kind == "function_call" else "changed"
                    call = {"id": None, "type": kind, "call_id": "nullable-call", "name": "fixture_tool", field: original}
                    data = ("data: " + json.dumps(completed(output=[call])) + "\n\n").encode()
                    def reply(handler, value, number):
                        if number != 1:
                            return stream_reply(handler, value, number)
                        handler.send_response(200)
                        handler.send_header("Content-Type", "text/event-stream")
                        handler.send_header("Content-Length", str(len(data)))
                        handler.end_headers()
                        handler.wfile.write(data)
                    with native_backend({}, response_handler=reply) as native_requests:
                        with self.fixture.connected_client(self.configuration(local, cloud)) as submit:
                            first = self.native(submit)
                            self.assertEqual(first["body"], data.decode())
                            status = submit(method="GET", path="/v1/router/native-codex", absolute_path=True,
                                headers={"X-Gateway-Key": NATIVE_HEADERS["X-Gateway-Key"]})["body"]
                            self.assertEqual((status["succeeded"], status["outcome_unknown"]), (1, 0), status)
                            result = {"type": kind + "_output", "call_id": "nullable-call", "output": "result"}
                            denied = self.native(submit, request={"input": [{**call, field: changed}, result]})
                            self.assertEqual(denied["status"], 403, denied)
                            self.assertEqual(len(native_requests), 1)
                            replay = self.native(submit, request={"input": [call, result]})
                            self.assertEqual(replay["status"], 200, replay)
                        self.assertEqual(len(native_requests), 2)
            self.assertEqual((local_requests, cloud_requests), ([], []))

    def test_native_tool_status_cannot_be_changed_or_removed_during_full_replay(self):
        with workflow.provider("LOCAL_UNUSED") as (local, local_requests), \
             workflow.provider("PAID_API_UNUSED") as (cloud, cloud_requests):
            for kind in ("function_call", "custom_tool_call"):
                for issued_status in ("completed", None):
                    with self.subTest(kind=kind, issued_status=issued_status):
                        field = "arguments" if kind == "function_call" else "input"
                        call = {"type": kind, "call_id": "status-call", "name": "fixture_tool",
                                field: "{}", "status": issued_status}
                        data = ("data: " + json.dumps(completed(output=[call])) + "\n\n").encode()
                        def reply(handler, value, number):
                            if number != 1:
                                return stream_reply(handler, value, number)
                            handler.send_response(200)
                            handler.send_header("Content-Type", "text/event-stream")
                            handler.send_header("Content-Length", str(len(data)))
                            handler.end_headers()
                            handler.wfile.write(data)
                        with native_backend({}, response_handler=reply) as native_requests:
                            with self.fixture.connected_client(self.configuration(local, cloud)) as submit:
                                self.assertEqual(self.native(submit)["body"], data.decode())
                                result = {"type": kind + "_output", "call_id": "status-call", "output": "result"}
                                for status in ("in_progress", "incomplete", 7, "unsupported", "missing", None):
                                    if issued_status is None and status in {"missing", None}:
                                        continue
                                    with self.subTest(changed_status=status):
                                        changed = {**call, "status": status}
                                        if status == "missing":
                                            changed.pop("status")
                                        denied = self.native(submit, request={"input": [changed, result]})
                                        self.assertEqual(denied["status"], 403, denied)
                                        self.assertEqual(len(native_requests), 1)
                                for replay in (call, {k: v for k, v in call.items() if v is not None}):
                                    accepted = self.native(submit, request={"input": [replay, result]})
                                    self.assertEqual(accepted["status"], 200, accepted)
                            self.assertEqual(len(native_requests), 3)
            self.assertEqual((local_requests, cloud_requests), ([], []))

    def test_native_tool_execution_metadata_is_validated_and_bound_to_the_issued_call(self):
        metadata = {"namespace": "functions", "caller": {"type": "program", "caller_id": "issued-program"}, "async": True}
        malformed = [{"namespace": 17}, {"caller": {}}, {"caller": {"type": "program"}},
            {"caller": {"type": "program", "caller_id": []}}, {"caller": {"type": "unknown"}}, {"async": "true"}]
        with workflow.provider("LOCAL_UNUSED") as (local, local_requests), \
             workflow.provider("PAID_API_UNUSED") as (cloud, cloud_requests):
            for kind in ("function_call", "custom_tool_call"):
                field = "arguments" if kind == "function_call" else "input"
                call = {"type": kind, "call_id": "metadata-call", "name": "fixture_tool", field: "{}"}
                for options in ({}, metadata, *malformed):
                    with self.subTest(kind=kind, options=options):
                        issued = {**call, **options}
                        valid = options in ({}, metadata)
                        data = ("data: " + json.dumps(completed(output=[issued])) + "\n\n").encode()
                        def reply(handler, value, number):
                            if number != 1:
                                return stream_reply(handler, value, number)
                            handler.send_response(200)
                            handler.send_header("Content-Type", "text/event-stream")
                            handler.send_header("Content-Length", str(len(data)))
                            handler.end_headers()
                            handler.wfile.write(data)
                        with native_backend({}, response_handler=reply) as native_requests:
                            with self.fixture.connected_client(self.configuration(local, cloud)) as submit:
                                self.native(submit)
                                status = submit(method="GET", path="/v1/router/native-codex", absolute_path=True,
                                    headers={"X-Gateway-Key": NATIVE_HEADERS["X-Gateway-Key"]})["body"]
                                self.assertEqual((status["succeeded"], status["outcome_unknown"]),
                                                 (1, 0) if valid else (0, 1), status)
                                result = {"type": kind + "_output", "call_id": "metadata-call", "output": "result"}
                                if valid:
                                    for changed in ({"namespace": "other"}, {"caller": {"type": "direct"}},
                                            {"caller": {"type": "program", "caller_id": "other-program"}}, {"async": False}):
                                        with self.subTest(changed=changed):
                                            denied = self.native(submit, request={"input": [{**issued, **changed}, result]})
                                            self.assertEqual(denied["status"], 403, denied)
                                    replayed = issued if options else {**call, "namespace": None, "caller": None, "async": None}
                                    replay = self.native(submit, request={"input": [replayed, result]})
                                    self.assertEqual(replay["status"], 200, replay)
                                else:
                                    replay = self.native(submit, request={"input": [call, result]})
                                    self.assertEqual(replay["status"], 403, replay)
                            self.assertEqual(len(native_requests), 2 if valid else 1)
            self.assertEqual((local_requests, cloud_requests), ([], []))

    def test_unqualified_or_disguised_native_input_variants_are_blocked_before_egress(self):
        items = [{"type": "computer_call_output", "call_id": "foreign-call", "output": {}},
            {"type": "mcp_approval_response", "approval_request_id": "foreign-approval", "approve": True},
            {"type": "tool_search_output", "call_id": "foreign-call", "tools": []},
            {"type": "unknown_native_variant"}, {"call_id": "foreign-call", "output": "result"},
            {"type": None, "role": "user", "content": "hello"},
            {"type": "message", "role": "tool", "content": "result"},
            {"type": "reasoning", "summary": []}, {"type": "compaction"}]
        with workflow.provider("LOCAL_UNUSED") as (local, local_requests), \
             workflow.provider("PAID_API_UNUSED") as (cloud, cloud_requests), \
             native_backend({}, response_handler=stream_reply) as native_requests:
            with self.fixture.connected_client(self.configuration(local, cloud)) as submit:
                for item in items:
                    with self.subTest(item=item):
                        result = self.native(submit, request={"input": [item]})
                        self.assertEqual(result["status"], 403, result)
                        self.assertEqual((local_requests, cloud_requests, native_requests), ([], [], []))
                for role in ("user", "assistant"):
                    fresh = self.native(submit, request={"input": [{"role": role, "content": "fresh plain text"}]})
                    self.assertEqual(fresh["status"], 200, fresh)
            self.assertEqual((len(local_requests), len(cloud_requests), len(native_requests)), (0, 0, 2))

    def test_native_results_must_match_the_issued_tool_kind_and_execution_context(self):
        with workflow.provider("LOCAL_UNUSED") as (local, local_requests), \
             workflow.provider("PAID_API_UNUSED") as (cloud, cloud_requests):
            for kind in ("function_call", "custom_tool_call"):
                with self.subTest(kind=kind):
                    field = "arguments" if kind == "function_call" else "input"
                    call = {"id": "context-tool", "type": kind, "call_id": "context-call", "name": "fixture_tool",
                            "namespace": "functions", "caller": {"type": "direct"}, field: "{}"}
                    data = ("data: " + json.dumps(completed(output=[call])) + "\n\n").encode()
                    def reply(handler, value, number):
                        if number != 1:
                            return stream_reply(handler, value, number)
                        handler.send_response(200)
                        handler.send_header("Content-Type", "text/event-stream")
                        handler.send_header("Content-Length", str(len(data)))
                        handler.end_headers()
                        handler.wfile.write(data)
                    with native_backend({}, response_handler=reply) as native_requests:
                        with self.fixture.connected_client(self.configuration(local, cloud)) as submit:
                            self.assertEqual(self.native(submit)["body"], data.decode())
                            result = {"type": kind + "_output", "call_id": "context-call", "output": "result"}
                            other = "custom_tool_call_output" if kind == "function_call" else "function_call_output"
                            invalid = [{**result, "type": other}, {**result, "name": "other_tool"},
                                {**result, "namespace": "other"}, {**result, "caller": {"type": "program", "caller_id": "foreign-program"}},
                                {k: v for k, v in result.items() if k != "output"}, {**result, "output": 17},
                                {"type": "reasoning", "id": "context-tool", "summary": []}]
                            for item in invalid:
                                with self.subTest(item=item):
                                    denied = self.native(submit, request={"input": [item]})
                                    self.assertEqual(denied["status"], 403, denied)
                                    self.assertEqual(len(native_requests), 1)
                            for item in (result, {**result, "name": "fixture_tool", "namespace": "functions", "caller": {"type": "direct"}}):
                                replay = self.native(submit, request={"input": [item]})
                                self.assertEqual(replay["status"], 200, replay)
                        self.assertEqual(len(native_requests), 3)
            self.assertEqual((local_requests, cloud_requests), ([], []))

    def test_opaque_values_and_tool_item_ids_cannot_be_recombined_or_stripped(self):
        reasoning = {"id": "reasoning-a", "type": "reasoning", "summary": [], "encrypted_content": "OPAQUE_A"}
        compaction = {"id": "compaction-b", "type": "compaction", "encrypted_content": "OPAQUE_B"}
        nullable = {"id": "reasoning-null", "type": "reasoning", "summary": [], "encrypted_content": None}
        call_a = {"id": "item-a", "type": "function_call", "call_id": "call-a", "name": "tool", "arguments": '{"value":"a"}'}
        call_b = {"id": "item-b", "type": "function_call", "call_id": "call-b", "name": "tool", "arguments": '{"value":"b"}'}
        call_null = {**call_a, "id": None, "call_id": "call-null"}
        output = [reasoning, compaction, nullable, call_a, call_b, call_null]
        data = ("data: " + json.dumps(completed(output=output)) + "\n\n").encode()
        def reply(handler, value, number):
            if number != 1:
                return stream_reply(handler, value, number)
            handler.send_response(200)
            handler.send_header("Content-Type", "text/event-stream")
            handler.send_header("Content-Length", str(len(data)))
            handler.end_headers()
            handler.wfile.write(data)
        with workflow.provider("LOCAL_UNUSED") as (local, local_requests), \
             workflow.provider("PAID_API_UNUSED") as (cloud, cloud_requests), \
             native_backend({}, response_handler=reply) as native_requests:
            with self.fixture.connected_client(self.configuration(local, cloud)) as submit:
                self.assertEqual(self.native(submit)["body"], data.decode())
                invalid = [{**reasoning, "encrypted_content": "OPAQUE_B"}, {**compaction, "encrypted_content": "OPAQUE_A"},
                    {**call_a, "id": "item-b"}, {k: v for k, v in call_a.items() if k != "id"},
                    {**reasoning, "encrypted_content": None}, {k: v for k, v in reasoning.items() if k != "encrypted_content"}]
                for item in invalid:
                    with self.subTest(item=item):
                        denied = self.native(submit, request={"input": [item]})
                        self.assertEqual(denied["status"], 403, denied)
                        self.assertEqual(len(native_requests), 1)
                results = [{"type": "function_call_output", "call_id": call["call_id"], "output": "result"}
                           for call in (call_a, call_b, call_null)]
                normalized = [reasoning, compaction, {k: v for k, v in nullable.items() if k != "encrypted_content"},
                              call_a, call_b, {k: v for k, v in call_null.items() if k != "id"}]
                for replay in (output, normalized):
                    result = self.native(submit, request={"input": replay + results})
                    self.assertEqual(result["status"], 200, result)
            self.assertEqual((len(local_requests), len(cloud_requests), len(native_requests)), (0, 0, 3))

    def test_unsupported_or_ambiguous_native_request_envelopes_never_reach_a_provider(self):
        cases = [
            {"request": {"stream": False}}, {"request": {"store": True}}, {"request": {"background": True}},
            {"request": {"previous_response_id": "foreign-response"}}, {"request": {"conversation": "foreign-conversation"}},
            {"request": {"max_output_tokens": None}}, {"request": {"max_output_tokens": True}},
            {"request": {"max_output_tokens": -1}}, {"request": {"max_output_tokens": 1.5}},
            {"request": {"client_metadata": None}}, {"request": {"input": 17}},
            {"request": {"unrecognized_control": "unused"}},
            {"headers": {"Transfer-Encoding": "chunked"}}, {"headers": {"Upgrade": "websocket"}},
            {"headers": {"X-Hormuz-Routing-Mode": "automatic"}},
            {"path": NATIVE_PATH + "?unexpected=1"},
            {"raw_body": '{"model":"native-code","model":"native-code","stream":true,"input":"hello"}'},
        ]
        with workflow.provider("LOCAL_UNUSED") as (local, local_requests), \
             workflow.provider("PAID_API_UNUSED") as (cloud, cloud_requests), \
             native_backend({}, response_handler=stream_reply) as native_requests:
            with self.fixture.connected_client(self.configuration(local, cloud)) as submit:
                for options in cases:
                    with self.subTest(options=options):
                        options = dict(options)
                        result = submit(path=options.pop("path", NATIVE_PATH), absolute_path=True,
                            headers={**NATIVE_HEADERS, **options.pop("headers", {})},
                            request={"model": "native-code", "stream": True, "store": False,
                                     **options.pop("request", {})}, **options)
                        self.assertIn(result["status"], {400, 403}, result)
                        self.assertEqual((local_requests, cloud_requests, native_requests), ([], [], []))

    def test_native_errors_preserve_auth_and_rate_status_without_reflection_redirect_or_fallback(self):
        cases = [(401, "3", 401), (403, None, 403), (429, "7", 429),
                 (429, "unsafe retry hint", 429), (307, None, 502), (500, None, 502), (200, None, 502)]
        with workflow.provider("LOCAL_UNUSED") as (local, local_requests), \
             workflow.provider("PAID_API_UNUSED") as (cloud, cloud_requests):
            for upstream_status, retry, expected in cases:
                with self.subTest(status=upstream_status, retry=retry):
                    def reply(handler, _value, _number):
                        data = b'{"error":"PRIVATE_PROVIDER_SENTINEL"}'
                        handler.send_response(upstream_status)
                        handler.send_header("Content-Type", "application/json")
                        handler.send_header("Content-Length", str(len(data)))
                        handler.send_header("Location", cloud + "/responses")
                        handler.send_header("Set-Cookie", "PRIVATE_COOKIE_SENTINEL")
                        if retry is not None:
                            handler.send_header("Retry-After", retry)
                        handler.end_headers()
                        try:
                            handler.wfile.write(data)
                        except (BrokenPipeError, ConnectionResetError):
                            pass
                    with native_backend({}, response_handler=reply) as native_requests:
                        with self.fixture.connected_client(self.configuration(local, cloud)) as submit:
                            result = self.native(submit)
                            self.assertEqual(result["status"], expected, result)
                            self.assertEqual(result["body"]["error"]["code"], "gateway_upstream_error")
                            self.assertNotIn("SENTINEL", json.dumps(result))
                            self.assertNotIn("location", result["headers"])
                            self.assertNotIn("set-cookie", result["headers"])
                            self.assertEqual(result["headers"].get("retry-after"), "7" if retry == "7" else None)
                        self.assertEqual(len(native_requests), 1)
            self.assertEqual((local_requests, cloud_requests), ([], []))

    def test_native_cancellation_closes_the_owned_transport_before_headers_and_during_idle_streaming(self):
        with workflow.provider("LOCAL_UNUSED") as (local, local_requests), \
             workflow.provider("PAID_API_UNUSED") as (cloud, cloud_requests):
            for phase in ("headers", "stream"):
                with self.subTest(phase=phase):
                    signal = self.fixture.root / ("cancel-" + phase)
                    closed = threading.Event()
                    def reply(handler, _value, _number):
                        signal.write_text("native request arrived")
                        if phase == "stream":
                            handler.send_response(200)
                            handler.send_header("Content-Type", "text/event-stream")
                            handler.end_headers()
                            handler.wfile.write(b'event: response.created\ndata: {"type":"response.created"}\n\n')
                            handler.wfile.flush()
                        handler.connection.settimeout(2)
                        try:
                            if handler.rfile.read(1) == b"":
                                closed.set()
                        except OSError:
                            pass
                    workers = []
                    with native_backend({}, response_handler=reply, workers=workers) as native_requests:
                        with self.fixture.connected_client(self.configuration(local, cloud)) as submit:
                            result = self.native(submit, **({"cancel_before_headers": True, "cancel_signal": str(signal)}
                                if phase == "headers" else {"cancel_after_event": True}))
                            self.assertTrue(closed.wait(1), "cancelled native transport remained open")
                            status = submit(method="GET", path="/v1/router/native-codex", absolute_path=True,
                                            headers={"X-Gateway-Key": NATIVE_HEADERS["X-Gateway-Key"]})["body"]
                            self.assertEqual((status["active"], status["succeeded"], status["outcome_unknown"]), (0, 0, 1))
                            for pid in workers:
                                with self.assertRaises(ProcessLookupError):
                                    os.kill(pid, 0)
                        self.assertEqual(len(native_requests), 1)
            self.assertEqual((local_requests, cloud_requests), ([], []))

    def test_a_native_conversation_rejects_concurrent_dispatch_while_other_sessions_can_proceed(self):
        entered, release = threading.Event(), threading.Event()
        def reply(handler, value, number):
            if number == 1:
                entered.set()
                release.wait(3)
            stream_reply(handler, value, number)
        with workflow.provider("LOCAL_UNUSED") as (local, local_requests), \
             workflow.provider("PAID_API_UNUSED") as (cloud, cloud_requests), \
             native_backend({}, response_handler=reply) as native_requests:
            with self.fixture.connected_client(self.configuration(local, cloud)) as submit, ThreadPoolExecutor(max_workers=1) as clients:
                first = clients.submit(self.native, submit, request={"client_metadata": {"thread_id": "same-thread"}})
                try:
                    self.assertTrue(entered.wait(2), "native request did not arrive")
                    blocked = self.native(submit, request={"client_metadata": {"thread_id": "same-thread"}})
                    self.assertEqual(blocked["status"], 429, blocked)
                    self.assertEqual(len(native_requests), 1)
                    independent = self.native(submit, request={"client_metadata": {"thread_id": "independent-thread"}})
                    self.assertEqual(independent["status"], 200, independent)
                finally:
                    release.set()
                self.assertEqual(first.result(timeout=5)["status"], 200)
            self.assertEqual((len(local_requests), len(cloud_requests), len(native_requests)), (0, 0, 2))

    def test_fresh_native_requests_without_session_identifiers_can_proceed_independently(self):
        entered, release = threading.Event(), threading.Event()
        def reply(handler, value, number):
            if number == 1:
                entered.set()
                release.wait(3)
            stream_reply(handler, value, number)
        with workflow.provider("LOCAL_UNUSED") as (local, local_requests), \
             workflow.provider("PAID_API_UNUSED") as (cloud, cloud_requests), \
             native_backend({}, response_handler=reply) as native_requests:
            with self.fixture.connected_client(self.configuration(local, cloud)) as submit, ThreadPoolExecutor(max_workers=1) as clients:
                first = clients.submit(self.native, submit, request={"input": "first independent task"})
                try:
                    self.assertTrue(entered.wait(2), "first native request did not arrive")
                    second = self.native(submit, request={"input": "second independent task"})
                    self.assertEqual(second["status"], 200, second)
                    self.assertEqual(len(native_requests), 2)
                finally:
                    release.set()
                self.assertEqual(first.result(timeout=5)["status"], 200)
                status = submit(method="GET", path="/v1/router/native-codex", absolute_path=True,
                    headers={"X-Gateway-Key": NATIVE_HEADERS["X-Gateway-Key"]})["body"]
                self.assertEqual((status["requests"], status["active"], status["succeeded"]), (2, 0, 2))
            self.assertEqual((len(local_requests), len(cloud_requests), len(native_requests)), (0, 0, 2))

    def test_native_contracts_are_public_versioned_and_reject_cost_or_scope_invention(self):
        with workflow.provider("LOCAL_UNUSED") as (local, local_requests), \
             workflow.provider("PAID_API_UNUSED") as (cloud, cloud_requests):
            with self.fixture.connected_client(self.configuration(local, cloud)) as submit:
                status = submit(method="GET", path="/v1/router/native-codex", absolute_path=True,
                                headers={"X-Gateway-Key": NATIVE_HEADERS["X-Gateway-Key"]})
                view = status["body"]
                validate_contract(view)
                self.assertEqual(status["headers"]["x-hormuz-contract"], "hormuz.native-codex-status;v=1")
                for change in ({"scope": "account_qualified"}, {"cost_status": "known"}, {"cost_usd": 0},
                               {"requests": 1}, {"active": True}, {"failed": -1}, {"generation": "unbound"},
                               {"authorization": "PRIVATE_SENTINEL"}):
                    with self.subTest(change=change), self.assertRaises(ContractValidationError):
                        validate_contract({**view, **change})
            result = subprocess.run([sys.executable, "-m", "hormuz", "contract", "manifest"],
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = json.loads(result.stdout)
            validate_contract_manifest(manifest)
            schemas = {(entry["schema_id"], entry["schema_version"]): entry for entry in manifest["schemas"]}
            self.assertEqual(schemas[("hormuz.native-codex-responses", 1)]["delivery"], "http-headers")
            self.assertEqual(schemas[("hormuz.native-codex-status", 1)]["delivery"], "response")
            fixture = json.loads((Path(__file__).parent / "fixtures/native-codex-responses-v1.json").read_text())
            self.assertEqual(fixture["response_header"], "hormuz.native-codex-responses;v=1")
            self.assertEqual(set(view), set(fixture["status_fields"]))
            self.assertEqual((local_requests, cloud_requests), ([], []))

    def test_a_valid_terminal_ends_native_streaming_without_waiting_for_provider_eof(self):
        closed = threading.Event()
        def reply(handler, _value, _number):
            handler.send_response(200)
            handler.send_header("Content-Type", "text/event-stream")
            handler.end_headers()
            handler.wfile.write(("data: " + json.dumps(completed()) + "\n\n").encode())
            handler.wfile.flush()
            handler.connection.settimeout(2)
            try:
                if handler.rfile.read(1) == b"":
                    closed.set()
            except OSError:
                pass
        with workflow.provider("LOCAL_UNUSED") as (local, local_requests), \
             workflow.provider("PAID_API_UNUSED") as (cloud, cloud_requests), \
             native_backend({}, response_handler=reply) as native_requests:
            with self.fixture.connected_client(self.configuration(local, cloud)) as submit:
                result = self.native(submit)
                self.assertEqual(result["status"], 200, result)
                self.assertTrue(closed.wait(0.5), "the provider socket remained open after terminal delivery")
                status = submit(method="GET", path="/v1/router/native-codex", absolute_path=True,
                                headers={"X-Gateway-Key": NATIVE_HEADERS["X-Gateway-Key"]})["body"]
                self.assertEqual((status["succeeded"], status["outcome_unknown"]), (1, 0))
            self.assertEqual((len(local_requests), len(cloud_requests), len(native_requests)), (0, 0, 1))

    def test_native_provider_state_cannot_be_disguised_as_a_user_message(self):
        with workflow.provider("LOCAL_UNUSED") as (local, local_requests), \
             workflow.provider("PAID_API_UNUSED") as (cloud, cloud_requests), \
             native_backend({}, response_handler=stream_reply) as native_requests:
            with self.fixture.connected_client(self.configuration(local, cloud)) as submit:
                for item in ({"type": "item_reference", "id": "foreign-item", "role": "user"},
                             {"type": "reasoning", "id": "foreign-reasoning", "role": "user"},
                             {"type": [], "id": "foreign-item"},
                             {"type": "message", "role": [], "id": "foreign-item"},
                             {"type": "function_call", "call_id": ["broken"], "name": "fixture_tool", "arguments": "{}"}):
                    with self.subTest(item=item):
                        result = self.native(submit, request={"input": [item]})
                        self.assertIn(result["status"], {400, 403}, result)
                        self.assertEqual((local_requests, cloud_requests, native_requests), ([], [], []))

    def test_native_authentication_and_http_header_ambiguity_never_reach_a_provider(self):
        with workflow.provider("LOCAL_UNUSED") as (local, local_requests), \
             workflow.provider("PAID_API_UNUSED") as (cloud, cloud_requests), \
             native_backend({}, response_handler=stream_reply) as native_requests:
            with self.fixture.connected_client(self.configuration(local, cloud)) as submit:
                cases = [({"X-Gateway-Key": ""}, 401),
                    ({"X-Gateway-Key": "wrong", "X-Api-Key": NATIVE_HEADERS["X-Gateway-Key"]}, 401),
                    ({"x-gateway-key": "duplicate"}, 401), ({"authorization": "Bearer DUPLICATE_SENTINEL"}, 401),
                    ({"chatgpt-account-id": "duplicate"}, 401), ({"Authorization": "Bearer first,second"}, 401),
                    ({"Authorization": "Bearer fixture-cloud-destination-credential"}, 401),
                    ({"Authorization": "Bearer fixture-coding-client-credential"}, 401),
                    ({"Content-Length": "100", "content-length": "100"}, 400),
                    ({"X-Hormuz-Routing-Mode": "local_only", "x-hormuz-routing-mode": "local_only"}, 400)]
                for headers, expected in cases:
                    with self.subTest(headers=headers):
                        result = self.native(submit, headers=headers)
                        self.assertEqual(result["status"], expected, result)
                        self.assertNotIn("SENTINEL", json.dumps(result))
                        self.assertEqual((local_requests, cloud_requests, native_requests), ([], [], []))

    def test_native_requests_reject_unsupported_work_accounting_intent(self):
        with workflow.provider("LOCAL_UNUSED") as (local, local_requests), \
             workflow.provider("PAID_API_UNUSED") as (cloud, cloud_requests), \
             native_backend({}, response_handler=stream_reply) as native_requests:
            with self.fixture.connected_client(self.configuration(local, cloud)) as submit:
                for headers in ({"X-Hormuz-Work-ID": "fixture-work"},
                                {"X-Hormuz-Work-Scope": "v1;work_scope_id=fixture;version=1"}):
                    with self.subTest(headers=headers):
                        result = self.native(submit, headers=headers)
                        self.assertEqual(result["status"], 403, result)
                        self.assertEqual((local_requests, cloud_requests, native_requests), ([], [], []))

    def test_inconsistent_native_usage_stays_unknown_after_successful_execution(self):
        def reply(handler, _value, _number):
            event = completed()
            event["response"]["usage"]["total_tokens"] = 99
            data = ("data: " + json.dumps(event) + "\n\n").encode()
            handler.send_response(200)
            handler.send_header("Content-Type", "text/event-stream")
            handler.send_header("Content-Length", str(len(data)))
            handler.end_headers()
            handler.wfile.write(data)
        with workflow.provider("LOCAL_UNUSED") as (local, local_requests), \
             workflow.provider("PAID_API_UNUSED") as (cloud, cloud_requests), \
             native_backend({}, response_handler=reply) as native_requests:
            with self.fixture.connected_client(self.configuration(local, cloud)) as submit:
                self.assertEqual(self.native(submit)["status"], 200)
                status = submit(method="GET", path="/v1/router/native-codex", absolute_path=True,
                                headers={"X-Gateway-Key": NATIVE_HEADERS["X-Gateway-Key"]})["body"]
                self.assertEqual(status["succeeded"], 1)
                self.assertEqual((status["observed_usage_requests"], status["usage_unknown_requests"],
                                  status["observed_input_tokens"], status["observed_output_tokens"]), (0, 1, 0, 0))
            self.assertEqual((len(local_requests), len(cloud_requests), len(native_requests)), (0, 0, 1))

    def test_verified_native_tls_allows_provider_header_latency_beyond_the_dns_deadline(self):
        def reply(handler, value, number):
            time.sleep(3.3)
            try:
                stream_reply(handler, value, number)
            except OSError:
                pass
        with workflow.provider("LOCAL_UNUSED") as (local, local_requests), \
             workflow.provider("PAID_API_UNUSED") as (cloud, cloud_requests), \
             native_backend({}, response_handler=reply) as native_requests:
            value = self.configuration(local, cloud)
            value["upstream_timeout_seconds"] = 6
            with self.fixture.connected_client(value) as submit:
                result = self.native(submit)
                self.assertEqual(result["status"], 200, result)
                self.assertIn("NATIVE_RESULT", result["body"])
            self.assertEqual((len(local_requests), len(cloud_requests), len(native_requests)), (0, 0, 1))

    def test_native_dns_tls_and_whole_stream_deadlines_are_bounded_and_reap_owned_workers(self):
        stopped = threading.Event()
        frames = []
        def slow_reply(handler, _value, _number):
            handler.send_response(200)
            handler.send_header("Content-Type", "text/event-stream")
            handler.end_headers()
            try:
                for _ in range(12):
                    handler.wfile.write(b'data: {"type":"response.in_progress"}\n\n')
                    handler.wfile.flush()
                    frames.append(True)
                    if stopped.wait(0.15):
                        break
            except OSError:
                pass
        with workflow.provider("LOCAL_UNUSED") as (local, local_requests), \
             workflow.provider("PAID_API_UNUSED") as (cloud, cloud_requests):
            for case in ("dns", "tls", "stream"):
                stopped.clear()
                frames.clear()
                workers = []
                with self.subTest(case=case), native_backend({}, response_handler=slow_reply if case == "stream" else stream_reply,
                        dns_delay=10 if case == "dns" else 0, workers=workers,
                        certificate_host="unrelated.example.invalid" if case == "tls" else "chatgpt.com") as native_requests:
                    value = self.configuration(local, cloud)
                    value["upstream_timeout_seconds"] = 1 if case == "stream" else 3
                    with self.fixture.connected_client(value) as submit:
                        started = time.monotonic()
                        result = self.native(submit)
                        self.assertLess(time.monotonic() - started, 4.5)
                        self.assertEqual(result["status"], 200 if case == "stream" else 502, result)
                        if case == "stream":
                            self.assertNotIn("response.completed", result["body"])
                            self.assertGreaterEqual(time.monotonic() - started, 0.8)
                            self.assertGreaterEqual(len(frames), 4)
                        self.assertEqual(len(native_requests), 1 if case == "stream" else 0)
                        for pid in workers:
                            with self.assertRaises(ProcessLookupError):
                                os.kill(pid, 0)
                    stopped.set()
            self.assertEqual((local_requests, cloud_requests), ([], []))

    def test_native_affinity_expires_on_restart_and_timeout_and_failed_turns_cannot_grant_it(self):
        call = {"id": "issued-tool", "type": "function_call", "call_id": "issued-call", "name": "fixture_tool", "arguments": "{}"}
        def reply(handler, _value, number):
            event = completed(output=[call])
            if number == 2:
                event["type"], event["response"]["status"] = "response.failed", "failed"
                event["response"]["output"] = [{**call, "call_id": "failed-call", "id": "failed-tool"}]
            data = ("data: " + json.dumps(event) + "\n\n").encode()
            handler.send_response(200)
            handler.send_header("Content-Type", "text/event-stream")
            handler.send_header("Content-Length", str(len(data)))
            handler.end_headers()
            handler.wfile.write(data)
        with workflow.provider("LOCAL_UNUSED") as (local, local_requests), \
             workflow.provider("PAID_API_UNUSED") as (cloud, cloud_requests), \
             native_backend({}, response_handler=reply) as native_requests:
            value = self.configuration(local, cloud)
            replay = {"input": [call, {"type": "function_call_output", "call_id": "issued-call", "output": "result"}]}
            with self.fixture.connected_client(value) as submit:
                self.assertEqual(self.native(submit)["status"], 200)
                self.assertIn("response.failed", self.native(submit)["body"])
                failed_replay = {"input": [{"type": "function_call_output", "call_id": "failed-call", "output": "result"}]}
                self.assertEqual(self.native(submit, request=failed_replay)["status"], 403)
                monotonic = time.monotonic
                with patch("time.monotonic", side_effect=lambda: monotonic() + 1000):
                    self.assertEqual(self.native(submit, request=replay)["status"], 403)
            with self.fixture.connected_client(value) as submit:
                self.assertEqual(self.native(submit, request=replay)["status"], 403)
            self.assertEqual((len(local_requests), len(cloud_requests), len(native_requests)), (0, 0, 2))

    def test_a_stalled_native_reader_cannot_delay_disconnect_or_retain_its_worker(self):
        signal = self.fixture.root / "stalled-headers"
        frame = b'data: {"type":"response.output_text.delta","delta":"' + b"x" * 16384 + b'"}\n\n'
        def reply(handler, _value, _number):
            handler.send_response(200)
            handler.send_header("Content-Type", "text/event-stream")
            handler.end_headers()
            try:
                handler.wfile.write(frame * 512)
            except OSError:
                pass
        workers = []
        with workflow.provider("LOCAL_UNUSED") as (local, local_requests), \
             workflow.provider("PAID_API_UNUSED") as (cloud, cloud_requests), \
             native_backend({}, response_handler=reply, workers=workers) as native_requests, \
             ThreadPoolExecutor(max_workers=1) as clients:
            with self.fixture.connected_client(self.configuration(local, cloud)) as submit:
                stalled = clients.submit(self.native, submit, stall_after_headers=True,
                    stall_seconds=8, headers_signal=str(signal))
                deadline = time.monotonic() + 3
                while not signal.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(signal.exists(), "stalled client did not receive native headers")
                shutdown_started = time.monotonic()
            self.assertLess(time.monotonic() - shutdown_started, 5)
            self.assertFalse(stalled.done(), "disconnect waited for the stalled client to finish")
            for pid in workers:
                with self.assertRaises(ProcessLookupError):
                    os.kill(pid, 0)
            self.assertEqual(stalled.result(timeout=10)["status"], 200)
            self.assertEqual((len(local_requests), len(cloud_requests), len(native_requests)), (0, 0, 1))


if __name__ == "__main__":
    unittest.main()
