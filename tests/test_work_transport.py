"""Real HTTP admission and signed-payment boundaries, without paid egress."""
import hashlib
import hmac
import http.client
import json
import time
import unittest
from copy import deepcopy
from unittest.mock import patch

from hormuz.postgres import PostgresStorageError
from hormuz.server import _provider_input_tokens_bounded
from hormuz.work_billing import STRIPE_VERSION, WorkBilling
from hormuz.work_learning import request_characteristics
from hormuz.work_runtime import WorkRuntime
from tests import test_work_gateway as fixtures


def _inline_namespace():
    return {"type": "namespace", "name": "functions", "description": "Local tools", "tools": [
        {"type": "function", "name": "inspect", "description": "Inspect local text", "strict": False,
         "parameters": {"type": "object", "properties": {
             "type": {"type": ["string", "null"]},
             "sample": {"type": "object", "default": {
                 "type": "input_image", "image_url": "https://example.invalid/schema-literal"}},
             "audio_sample": {"type": "object", "default": {
                 "type": "input_audio", "input_audio": {"data": "AAAA", "format": "wav"}}},
             "assistant_sample": {"type": "object", "default": {
                 "messages": [{"role": "assistant", "audio": {"id": "schema-literal"}}]}}},
             "$defs": {"reference": {"const": {"type": "item_reference", "id": "schema-literal"}}}}},
        {"type": "custom", "name": "patch", "description": "Local patch grammar", "defer_loading": False,
         "format": {"type": "grammar", "syntax": "lark", "definition": 'start: "text"'}}]}


class ProviderInputTokenBoundsTests(unittest.TestCase):
    def test_validated_tools_are_text_and_original_definitions_are_preserved(self):
        namespace = _inline_namespace()
        for protocol, tools in (("openai", [namespace]), ("openai", namespace["tools"]),
                ("anthropic", [{"name": "inspect", "description": "Local text",
                    "input_schema": namespace["tools"][0]["parameters"]}])):
            request = {"tools": tools}
            before = deepcopy(request)
            with self.subTest(protocol=protocol, tools=tools):
                self.assertTrue(_provider_input_tokens_bounded(protocol, request, text_only=True))
                self.assertEqual(request, before)
        self.assertFalse(_provider_input_tokens_bounded("openai", {"tools": [namespace]}))

    def test_only_recognized_openai_output_schema_locations_are_text(self):
        schema = _inline_namespace()["tools"][0]["parameters"]
        formats = ({"text": {"format": {"type": "json_schema", "name": "result", "schema": schema}}},
            {"response_format": {"type": "json_schema", "json_schema": {"name": "result", "schema": schema}}})
        for fields in formats:
            before = deepcopy(fields)
            with self.subTest(fields=fields):
                self.assertTrue(_provider_input_tokens_bounded("openai", fields, text_only=True))
                self.assertEqual(fields, before)
                self.assertFalse(_provider_input_tokens_bounded("openai", fields))
                self.assertFalse(_provider_input_tokens_bounded("anthropic", fields, text_only=True))
                self.assertFalse(_provider_input_tokens_bounded("openai",
                    {**fields, "input": [{"type": "item_reference", "id": "ref"}]}, text_only=True))
                self.assertFalse(_provider_input_tokens_bounded("openai",
                    {**fields, "input": [{"type": "input_image", "image_url": "data:image/png;base64,AAAA"}]}, text_only=True))
                self.assertFalse(_provider_input_tokens_bounded("openai",
                    {**fields, "input": [{"type": "input_audio", "input_audio": {"data": "AAAA", "format": "wav"}}]}, text_only=True))
                self.assertFalse(_provider_input_tokens_bounded("openai",
                    {**fields, "messages": [{"role": "assistant", "audio": {"id": "previous-audio"}}]}, text_only=True))
        for fields in ({"text": {"format": {"type": "json_schema", "schema": []}}},
                {"response_format": {"type": "json_schema", "json_schema": {"schema": []}}},
                {"response_format": {"type": "json_schema", "json_schema": []}},
                {"text": {"format": {"type": "unknown", "schema": schema}}},
                {"input": [{"schema": schema}]}, {"schema": schema}):
            with self.subTest(malformed_or_unrecognized=fields):
                self.assertFalse(_provider_input_tokens_bounded("openai", fields, text_only=True))

    def test_namespace_shape_and_children_fail_closed(self):
        namespace = _inline_namespace()
        cases = []
        for field, value in (("name", ""), ("name", 1), ("description", []),
                             ("tools", None), ("tools", {}), ("tools", [])):
            cases.append({**namespace, field: value})
        cases.append({key: value for key, value in namespace.items() if key != "tools"})
        cases.append({**namespace, "unsupported": True})
        for child in (None, [], {}, {"type": []}, namespace,
                *({"type": kind} for kind in ("web_search", "tool_search", "image_generation", "file_search", "unknown")),
                {**namespace["tools"][0], "parameters": "not-a-schema"},
                {**namespace["tools"][0], "strict": "false"},
                {**namespace["tools"][0], "defer_loading": None},
                {**namespace["tools"][1], "format": {"type": "grammar"}}):
            cases.append({**namespace, "tools": [child]})
        for tool in cases:
            with self.subTest(tool=tool):
                self.assertFalse(_provider_input_tokens_bounded("openai", {"tools": [tool]}, text_only=True))

    def test_only_the_validated_top_level_tools_subtree_is_excluded(self):
        namespace = _inline_namespace()
        content = ({"type": "item_reference", "id": "ref"},
            {"type": "input_image", "image_url": "data:image/png;base64,AAAA"},
            {"type": "input_file", "file_data": "AAAA"},
            {"type": "image_url", "image_url": {"url": "https://example.invalid/image"}},
            {"type": "image", "source": {"type": "base64", "data": "AAAA"}},
            {"type": "document", "source": {"type": "base64", "data": "AAAA"}},
            {"type": "input_audio", "input_audio": {"data": "AAAA", "format": "mp3"}},
            {"type": "audio", "data": "AAAA"},
            {"parameters": {"type": "item_reference", "id": "ref"}},
            {"tools": [{"type": "input_image", "image_url": "data:image/png;base64,AAAA"}]})
        for protocol in ("openai", "anthropic"):
            tools = [namespace] if protocol == "openai" else [{"name": "inspect", "input_schema": {}}]
            for value in content:
                with self.subTest(protocol=protocol, content=value):
                    self.assertFalse(_provider_input_tokens_bounded(protocol,
                        {"tools": tools, "input": [value]}, text_only=True))
            for kind in ([], {}, True, None):
                for text_only in (False, True):
                    with self.subTest(protocol=protocol, kind=kind, text_only=text_only):
                        self.assertFalse(_provider_input_tokens_bounded(protocol,
                            {"input": [{"type": kind}]}, text_only=text_only))
                        self.assertFalse(_provider_input_tokens_bounded(protocol,
                            {"input": [{"type": "image", "source": {"type": kind}}]}, text_only=text_only))
        for field in ("conversation", "previous_response_id", "prompt"):
            self.assertFalse(_provider_input_tokens_bounded("openai",
                {"tools": [namespace], field: "provider-reference"}, text_only=True))
        for field in ("container", "mcp_servers"):
            self.assertFalse(_provider_input_tokens_bounded("anthropic",
                {"tools": [{"name": "inspect", "input_schema": {}}], field: "provider-reference"}, text_only=True))

    def test_legacy_inline_media_behavior_is_preserved(self):
        cases = (("openai", {"type": "input_image", "image_url": "data:image/png;base64,AAAA"}),
            ("openai", {"type": "input_file", "file_data": "AAAA"}),
            ("anthropic", {"type": "image", "source": {"type": "base64", "data": "AAAA"}}),
            ("openai", {"type": "input_audio", "input_audio": {"data": "AAAA", "format": "wav"}}),
            ("openai", {"type": "audio", "data": "AAAA"}))
        for protocol, value in cases:
            with self.subTest(protocol=protocol):
                self.assertTrue(_provider_input_tokens_bounded(protocol, {"input": [value]}))
                self.assertFalse(_provider_input_tokens_bounded(protocol, {"input": [value]}, text_only=True))
        audio_reference = {"messages": [{"role": "assistant", "audio": {"id": "previous-audio"}}]}
        self.assertTrue(_provider_input_tokens_bounded("openai", audio_reference))
        self.assertFalse(_provider_input_tokens_bounded("openai", audio_reference, text_only=True))
        self.assertTrue(_provider_input_tokens_bounded("openai", {
            "messages": [{"role": "assistant", "audio": None}]}, text_only=True))

    def test_runtime_non_string_tags_conservatively_bypass_context_inference_and_cache(self):
        schema = _inline_namespace()["tools"][0]["parameters"]
        request = {"temperature": 0, "text": {"format": {
            "type": "json_schema", "name": "result", "schema": schema}}}
        self.assertTrue(WorkRuntime._contains_provider_state(request))
        self.assertFalse(WorkRuntime._cache_safe(request))
        for field in ("role", "type"):
            for tag in ({"type": "string"}, ["string", "null"], None, True):
                with self.subTest(field=field, tag=tag):
                    request = {"temperature": 0, "input": [{field: tag}]}
                    self.assertTrue(WorkRuntime._contains_provider_state(request))
                    self.assertFalse(WorkRuntime._cache_safe(request))
        ordinary = {"temperature": 0, "messages": [{"role": "user", "content": "Text"}]}
        self.assertFalse(WorkRuntime._contains_provider_state(ordinary))
        self.assertTrue(WorkRuntime._cache_safe(ordinary))
        self.assertTrue(WorkRuntime._contains_provider_state({"input": [{"type": "function_call"}]}))
        self.assertFalse(WorkRuntime._cache_safe({**ordinary, "previous_response_id": "ref"}))

    def test_malformed_tags_produce_only_fixed_learning_characteristics(self):
        for tag in ({"type": "string"}, ["string", "null"], None, True):
            for request in ({"input": [{"type": tag, "text": "synthetic-private"}]},
                    {"messages": [{"role": tag, "content": "synthetic-private"}]}):
                with self.subTest(tag=tag, request=request):
                    characteristics = request_characteristics(request)
                    self.assertEqual("unknown", characteristics["task_hint"])
                    self.assertFalse(characteristics["history"])
                    self.assertNotIn("synthetic-private", json.dumps(characteristics))
        self.assertEqual("summarization", request_characteristics({
            "input": [{"type": "input_text", "text": "Summarize this note"}]})["task_hint"])
        self.assertTrue(request_characteristics({
            "messages": [{"role": "assistant", "content": "Prior answer"}]})["history"])


class WorkTransportTests(unittest.TestCase):
    setUp = fixtures.WorkGatewayTests.setUp
    start = fixtures.WorkGatewayTests.start
    tearDown = fixtures.WorkGatewayTests.tearDown
    job = fixtures.WorkGatewayTests.job
    post = fixtures.WorkGatewayTests.post

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.gateway.server_port, timeout=5)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        status, data = response.status, response.read()
        connection.close()
        return status, json.loads(data)

    def test_unbounded_chat_options_cannot_dispatch_or_spend(self):
        work = self.job()
        options = ({"n": 128}, {"n": True}, {"web_search_options": {}},
            {"modalities": ["text", "audio"]}, {"audio": {}},
            {"messages": [{"role": "user", "content": [{"type": "image_url",
                "image_url": {"url": "https://example.test/image.png"}}]}]})
        for fields in options:
            with self.subTest(fields=fields):
                status, _, body = self.post(work, **fields)
                self.assertEqual(422, status, body)
                self.assertIn(b"request_cost_unbounded", body)
        self.assertEqual([], fixtures.WorkProvider.requests)
        state = self.gateway.work_runtime.get_work(self.identity, work)
        self.assertEqual(0, state["costs"]["consumed_microusd"])
        self.assertEqual(200, self.post(work, n=1)[0])

    def test_inline_namespace_reaches_provider_unchanged_and_reserves_its_complete_bytes(self):
        work = self.job()
        tools = [_inline_namespace()]
        tools[0]["tools"][0]["description"] += "x" * 2048
        status, _, body = self.post(work, endpoint="/v1/responses", tools=tools)
        self.assertEqual(200, status, body)
        self.assertEqual(1, len(fixtures.WorkProvider.requests))
        sent = fixtures.WorkProvider.requests[0]
        self.assertEqual(tools, sent["body"]["tools"])
        self.assertFalse(any(name.lower() == "x-hormuz-work-id" for name in sent["headers"]))
        attempt = self.gateway.work_runtime.get_work(self.identity, work)["attempts"][0]
        self.assertEqual(("succeeded", True), (attempt["state"], attempt["response_succeeded"]))
        route = self.config.model_routes[attempt["model"]]
        complete_bytes = len(json.dumps(sent["body"], separators=(",", ":")).encode())
        self.assertEqual(route.estimate_reservation_cost_microusd(input_tokens=complete_bytes,
            output_tokens=sent["body"]["max_output_tokens"]), attempt["reserved_microusd"])

    def test_namespace_hosted_children_and_real_content_cannot_dispatch_or_spend(self):
        work = self.job()
        namespace = _inline_namespace()
        fields = [{"tools": [{**namespace, "tools": [{"type": kind}]}]}
                  for kind in ("web_search", "tool_search", "image_generation", "unknown", "namespace")]
        fields += [{"tools": [{**namespace, "tools": {}}]},
            {"tools": [namespace], "previous_response_id": "provider-reference"},
            {"tools": [namespace], "input": [{"type": "input_image", "image_url": "data:image/png;base64,AAAA"}]},
            {"tools": [namespace], "input": [{"parameters": {"type": "item_reference", "id": "ref"}}]},
            {"tools": [namespace], "input": [{"type": []}]}]
        for changes in fields:
            with self.subTest(fields=changes):
                status, _, body = self.post(work, endpoint="/v1/responses", **changes)
                self.assertEqual(422, status, body)
                self.assertIn(b"request_cost_unbounded", body)
        self.assertEqual([], fixtures.WorkProvider.requests)
        state = self.gateway.work_runtime.get_work(self.identity, work)
        self.assertEqual(0, state["costs"]["consumed_microusd"])
        self.assertEqual(0, state["costs"]["uncertain_microusd"])

    def test_embedded_chat_audio_cannot_dispatch_or_spend(self):
        work = self.job()
        cases = ([{"role": "user", "content": [{"type": "input_audio",
                  "input_audio": {"data": "AAAA", "format": "wav"}}]}],
                 [{"role": "assistant", "audio": {"id": "previous-audio"}}])
        for messages in cases:
            with self.subTest(messages=messages):
                status, _, body = self.post(work, messages=messages)
                self.assertEqual(422, status, body)
                self.assertIn(b"request_cost_unbounded", body)
        self.assertEqual([], fixtures.WorkProvider.requests)
        state = self.gateway.work_runtime.get_work(self.identity, work)
        self.assertEqual(0, state["costs"]["consumed_microusd"])
        self.assertEqual(0, state["costs"]["uncertain_microusd"])

    def test_recognized_output_schemas_preserve_wire_and_full_cost_reservation(self):
        schema = _inline_namespace()["tools"][0]["parameters"]
        schema["description"] = "x" * 2048
        for endpoint, fields in (("/v1/responses", {"text": {"format": {
                "type": "json_schema", "name": "result", "schema": schema}}}),
                ("/v1/chat/completions", {"response_format": {"type": "json_schema", "json_schema": {
                    "name": "result", "schema": schema}}})):
            with self.subTest(endpoint=endpoint):
                work = self.job()
                status, _, body = self.post(work, endpoint=endpoint, **fields)
                self.assertEqual(200, status, body)
                sent = fixtures.WorkProvider.requests[-1]["body"]
                for field, value in fields.items():
                    self.assertEqual(value, sent[field])
                attempt = self.gateway.work_runtime.get_work(self.identity, work)["attempts"][0]
                self.assertEqual(("succeeded", True), (attempt["state"], attempt["response_succeeded"]))
                route = self.config.model_routes[attempt["model"]]
                output_limit = sent["max_output_tokens" if endpoint == "/v1/responses" else "max_tokens"]
                self.assertEqual(route.estimate_reservation_cost_microusd(
                    input_tokens=len(json.dumps(sent, separators=(",", ":")).encode()),
                    output_tokens=output_limit), attempt["reserved_microusd"])
                provider_calls = len(fixtures.WorkProvider.requests)
                self.assertEqual(200, self.post(work, endpoint=endpoint, **fields)[0])
                self.assertEqual(provider_calls + 1, len(fixtures.WorkProvider.requests))
                self.assertEqual(0, self.gateway.work_runtime.get_work(self.identity, work)["costs"]["cache_hits"])

    def test_signed_billing_post_dispatches_without_bearer_and_get_cannot_mutate(self):
        now = int(time.time())
        secret = "whsec_transport_synthetic_secret"
        self.gateway.work_billing = WorkBilling(self.root / "transport-billing.sqlite3", "price_Approved",
            [(self.identity.organization_id, "cus_Approved", "sub_Approved")], secret)
        event = {"id": "evt_transport", "type": "customer.subscription.updated", "created": now,
            "api_version": STRIPE_VERSION, "livemode": True, "data": {"object": {
                "id": "sub_Approved", "customer": "cus_Approved", "status": "active",
                "items": {"data": [{"price": {"id": "price_Approved"}, "quantity": 1,
                    "current_period_start": now, "current_period_end": now + 3600}]}}}}
        body = json.dumps(event).encode()
        signature = hmac.new(secret.encode(), str(now).encode() + b"." + body, hashlib.sha256).hexdigest()
        headers = {"Content-Type": "application/json", "Stripe-Signature": f"t={now},v1={signature}"}
        self.assertEqual(405, self.request("GET", "/v1/work/billing/webhook", body, headers)[0])
        status, result = self.request("POST", "/v1/work/billing/webhook", body, headers)
        self.assertEqual(200, status, result)
        self.assertEqual("applied", result["status"])
        self.assertEqual("duplicate", self.request("POST", "/v1/work/billing/webhook", body, headers)[1]["status"])
        self.assertEqual(400, self.request("POST", "/v1/work/billing/webhook", b"{}",
            {"Content-Type": "application/json", "Stripe-Signature": f"t={now},v1={'0'*64}"})[0])

    def test_readiness_checks_work_and_billing_without_recreating_missing_stores(self):
        self.assertEqual(200, self.request("GET", "/ready")[0])
        path = self.gateway.work_runtime.path
        moved = path.with_suffix(".moved")
        path.rename(moved)
        try:
            self.assertEqual(503, self.request("GET", "/ready")[0])
            self.assertFalse(path.exists())
        finally:
            moved.rename(path)
        self.gateway.work_billing = WorkBilling(self.root / "readiness-billing.sqlite3", "price_Approved", [], "")
        self.assertEqual(200, self.request("GET", "/ready")[0])
        billing_path = self.gateway.work_billing.path
        billing_path.rename(billing_path.with_suffix(".moved"))
        self.assertEqual(503, self.request("GET", "/ready")[0])
        self.assertFalse(billing_path.exists())

    def test_model_catalog_authenticates_filters_aliases_and_handles_storage_failure(self):
        self.assertEqual(401, self.request("GET", "/v1/models")[0])
        headers = {"Authorization": "Bearer " + fixtures.GATEWAY_TOKEN}
        status, result = self.request("GET", "/v1/models", headers=headers)
        self.assertEqual(200, status)
        self.assertEqual("list", result["object"])
        self.assertEqual({"engineering-fast", "engineering-deep"}, {item["id"] for item in result["data"]})
        self.assertNotIn("gpt-test", json.dumps(result))
        claude_headers = {"Authorization": "Bearer " + fixtures.CLAUDE_ONLY_TOKEN}
        self.assertEqual([], self.request("GET", "/v1/models", headers=claude_headers)[1]["data"])
        with patch.object(self.gateway.policy_engine.policy_runtime, "snapshot_for", side_effect=PostgresStorageError("unavailable")):
            self.assertEqual(503, self.request("GET", "/v1/models", headers=headers)[0])

    def test_chat_requires_existing_explicit_openai_client_authorization(self):
        work = self.job()
        self.assertEqual(403, self.post(work, token=fixtures.CLAUDE_ONLY_TOKEN)[0])
        self.assertEqual([], fixtures.WorkProvider.requests)
