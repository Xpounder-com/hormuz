"""Real loopback transport witnesses for sanitized classification and timing."""
import http.client
import json
import sqlite3
from contextlib import closing
import unittest

from hormuz.config import ConfigError, GatewayConfig
from tests import test_work_gateway as fixtures


class WorkTransportProgressTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.WorkGatewayTests("test_exact_cache_is_zero_provider_work_and_correction_bypasses_it")
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)

    def send(self, work_id, *, kind=None, content="Summarize this engineering note"):
        request = {"model": "engineering-fast", "messages": [{"role": "user", "content": content}], "temperature": 0, "max_tokens": 20}
        headers = {"Authorization": "Bearer " + fixtures.GATEWAY_TOKEN, "Content-Type": "application/json", "X-Hormuz-Work-ID": work_id}
        if kind is not None:
            headers["X-Hormuz-Request-Kind"] = kind
        connection = http.client.HTTPConnection("127.0.0.1", self.fixture.gateway.server_port, timeout=5)
        connection.request("POST", "/v1/chat/completions", json.dumps(request), headers)
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def test_only_sanitized_request_characteristics_and_actual_timing_are_retained(self):
        runtime = self.fixture.gateway.work_runtime
        work = runtime.create_work(self.fixture.identity, "customer/repo", context_revision="commit-1")
        status, _, body = self.send(work["work_id"], content="Summarize this engineering note containing " + fixtures.OPENAI_KEY)
        self.assertEqual(200, status, body)
        state = runtime.get_work(self.fixture.identity, work["work_id"])
        self.assertEqual(("summarization", "inferred_characteristics"), (state["task_type"], state["task_origin"]))
        attempt = state["attempts"][0]
        self.assertGreaterEqual(attempt["gateway_wall_ms"], attempt["latency_ms"])
        self.assertGreaterEqual(attempt["gateway_overhead_ms"], 0)
        self.assertEqual("provider_transport_blocking_time", attempt["timing_basis"])
        with closing(sqlite3.connect(runtime.path)) as connection:
            self.assertNotIn(fixtures.OPENAI_KEY, "\n".join(connection.iterdump()))
        self.assertNotIn(fixtures.OPENAI_KEY, str(fixtures.WorkProvider.requests[0]["body"]))

    def test_repeated_cache_hits_trigger_one_fresh_provider_call_without_inferred_failure(self):
        work_id = self.fixture.job()
        self.assertEqual(200, self.send(work_id)[0])
        _, headers, _ = self.send(work_id)
        self.assertEqual("exact-answer-hit", headers.get("X-Hormuz-Cache"))
        self.assertEqual(1, len(fixtures.WorkProvider.requests))
        self.assertEqual(200, self.send(work_id)[0])
        self.assertEqual(2, len(fixtures.WorkProvider.requests))
        state = self.fixture.gateway.work_runtime.get_work(self.fixture.identity, work_id)
        self.assertIn("possible_repeat_not_quality", [attempt["cache_bypass_reason"] for attempt in state["attempts"]])
        self.assertEqual("unknown", state["outcome_evidence"])
        self.assertEqual([], state["observations"])

    def test_machine_repeats_keep_safe_cache_and_invalid_kind_refuses_dispatch(self):
        work_id = self.fixture.job()
        self.send(work_id)
        self.send(work_id, kind="polling")
        self.send(work_id, kind="polling")
        self.assertEqual(1, len(fixtures.WorkProvider.requests))
        status, _, _ = self.send(work_id, kind="invented")
        self.assertEqual(400, status)
        self.assertEqual(1, len(fixtures.WorkProvider.requests))

    def test_second_gateway_owner_cannot_reinterpret_live_pending_work(self):
        work_id = self.fixture.job()
        route = self.fixture.config.model_routes["engineering-fast"]
        self.fixture.gateway.work_runtime.reserve(self.fixture.identity, work_id, "pending-one", route, "openai", 100)
        with self.assertRaisesRegex(Exception, "hosted_work_owner_active"):
            fixtures.GatewayServer(self.fixture.config)
        self.assertEqual("pending", self.fixture.gateway.work_runtime.get_work(self.fixture.identity, work_id)["attempts"][0]["state"])

    def test_exploration_configuration_requires_explicit_aliases_and_monthly_allowance(self):
        for values in ({"exploration_enabled": True}, {"exploration_aliases": [{}]}, {"exploration_rate_percent": 21}, {"exploration_max_cost_microusd": -1}):
            self.fixture.config_value["ai_work"].update(values)
            self.fixture.path.write_text(json.dumps(self.fixture.config_value))
            with self.assertRaises(ConfigError):
                GatewayConfig.load(self.fixture.path)
            for key in values:
                self.fixture.config_value["ai_work"].pop(key)
