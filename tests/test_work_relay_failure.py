"""Independent ledgers and one HTTP response under work-store failures."""

from __future__ import annotations

from contextlib import closing
import json
import socket
import sqlite3
import unittest
import urllib.error
from unittest.mock import patch

from hormuz.work_runtime import WorkRuntimeError
from tests import test_work_gateway as fixtures


class WorkRelayFailureTests(unittest.TestCase):
    setUp = fixtures.WorkGatewayTests.setUp
    start = fixtures.WorkGatewayTests.start
    tearDown = fixtures.WorkGatewayTests.tearDown
    job = fixtures.WorkGatewayTests.job

    def raw_post(self, work_id, *, endpoint="/v1/chat/completions", **fields):
        payload = {"model": "engineering-fast", "messages": [{"role": "user", "content": "count three"}],
                   "temperature": 0, "max_tokens": 20, "stream": False}
        if endpoint == "/v1/responses":
            payload = {"model": "engineering-fast", "input": "count three", "temperature": 0,
                       "max_output_tokens": 20, "stream": False}
        payload.update(fields)
        body = json.dumps(payload).encode()
        headers = ["POST " + endpoint + " HTTP/1.1", "Host: 127.0.0.1",
                   "Authorization: Bearer " + fixtures.GATEWAY_TOKEN,
                   "Content-Type: application/json", "Content-Length: " + str(len(body)),
                   "Connection: close"]
        if work_id is not None:
            headers.append("X-Hormuz-Work-ID: " + work_id)
        with socket.create_connection(("127.0.0.1", self.gateway.server_port), timeout=5) as connection:
            connection.sendall(("\r\n".join(headers) + "\r\n\r\n").encode() + body)
            chunks = []
            while True:
                chunk = connection.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                self.assertLess(sum(map(len, chunks)), 2_000_000)
        wire = b"".join(chunks)
        self.assertEqual(1, wire.count(b"HTTP/1.1 "), wire)
        head, response_body = wire.split(b"\r\n\r\n", 1)
        status = int(head.split(b"\r\n", 1)[0].split()[1])
        # Parse all bytes through EOF, including bytes beyond Content-Length on
        # a cache hit. A second error response must never contaminate the body.
        return status, json.loads(response_body), head

    def terminal_events(self):
        with closing(sqlite3.connect(self.gateway.store.path)) as connection:
            return connection.execute(
                "SELECT state, reason_code FROM gateway_request_attempt_events WHERE sequence=2 ORDER BY rowid"
            ).fetchall()

    def restart(self):
        self.gateway.shutdown()
        self.gateway.server_close()
        self.config_value["listen"]["port"] = fixtures._free_port()
        self.start()

    def assert_retained_hold_after_restart(self, work):
        before = self.gateway.work_runtime.get_work(self.identity, work)
        self.assertEqual("pending", before["attempts"][0]["state"])
        maximum = before["costs"]["pending_microusd"]
        self.assertGreater(maximum, 0)
        self.assertIsNone(before["attempts"][0]["cost_microusd"])
        self.restart()
        after = self.gateway.work_runtime.get_work(self.identity, work)
        self.assertEqual("unknown", after["attempts"][0]["state"])
        self.assertEqual(maximum, after["costs"]["uncertain_microusd"])
        self.assertEqual(maximum, after["costs"]["consumed_microusd"])
        self.assertIsNone(after["attempts"][0]["cost_microusd"])

    def test_timing_failure_after_relay_preserves_response_and_provider_charge(self):
        work = self.job()
        with patch.object(self.gateway.work_runtime, "record_timing",
                          side_effect=WorkRuntimeError("storage_unavailable", 503)):
            status, body, _ = self.raw_post(work)
        self.assertEqual(200, status)
        self.assertEqual("three", body["choices"][0]["message"]["content"])
        self.assertEqual([("succeeded", None)], self.terminal_events())
        totals = self.gateway.store.monthly_totals(actor_id="alice")
        self.assertEqual((8, 3), (totals.input_tokens, totals.output_tokens))
        self.assertGreater(totals.cost_microusd, 0)
        self.assertEqual(0, self.gateway.store.active_budget_reservations())

    def test_answer_cache_write_failure_cannot_skip_provider_finalization(self):
        work = self.job()
        with patch.object(self.gateway.work_runtime, "cache_put",
                          side_effect=WorkRuntimeError("storage_unavailable", 503)):
            status, body, _ = self.raw_post(work)
        self.assertEqual(200, status)
        self.assertIn("choices", body)
        self.assertEqual([("succeeded", None)], self.terminal_events())
        self.assertGreater(self.gateway.store.monthly_totals(actor_id="alice").cost_microusd, 0)
        self.assertEqual("succeeded", self.gateway.work_runtime.get_work(self.identity, work)["attempts"][0]["state"])

    def test_cache_hit_timing_failure_has_no_second_http_bytes_or_provider_call(self):
        work = self.job()
        self.assertEqual(200, self.raw_post(work)[0])
        with patch.object(self.gateway.work_runtime, "record_timing",
                          side_effect=WorkRuntimeError("storage_unavailable", 503)):
            status, body, head = self.raw_post(work)
        self.assertEqual(200, status)
        self.assertIn(b"X-Hormuz-Cache: exact-answer-hit", head)
        self.assertIn("choices", body)
        self.assertEqual(1, len(fixtures.WorkProvider.requests))
        self.assertEqual([("succeeded", None)], self.terminal_events())

    def test_settlement_failure_commits_provider_charge_and_retains_work_hold(self):
        work = self.job()
        with patch.object(self.gateway.work_runtime, "settle",
                          side_effect=WorkRuntimeError("storage_unavailable", 503)):
            status, body, _ = self.raw_post(work)
        self.assertEqual(200, status)
        self.assertIn("choices", body)
        self.assertEqual([("succeeded", None)], self.terminal_events())
        self.assertGreater(self.gateway.store.monthly_totals(actor_id="alice").cost_microusd, 0)
        self.assertEqual(0, self.gateway.store.active_budget_reservations())
        self.assert_retained_hold_after_restart(work)

    def test_missing_usage_commits_unknown_provider_outcome_before_work_failure(self):
        work = self.job()
        with patch.object(self.gateway.work_runtime, "settle",
                          side_effect=WorkRuntimeError("storage_unavailable", 503)):
            status, body, _ = self.raw_post(work, simulate_missing_usage=True)
        self.assertEqual(200, status)
        self.assertNotIn("usage", body)
        self.assertEqual([("outcome_unknown", "provider_transport_ambiguous")], self.terminal_events())
        self.assertEqual(1, self.gateway.store.active_budget_reservations())
        self.assertEqual(0, self.gateway.store.monthly_totals(actor_id="alice").requests)
        self.assert_retained_hold_after_restart(work)

    def test_pre_response_transport_failure_records_unknown_before_work_failure(self):
        work = self.job()
        with patch("hormuz.server._open_upstream", side_effect=urllib.error.URLError("synthetic_unavailable")), \
                patch.object(self.gateway.work_runtime, "settle",
                             side_effect=WorkRuntimeError("storage_unavailable", 503)):
            status, body, _ = self.raw_post(work)
        self.assertEqual(503, status)
        self.assertEqual("hormuz_ai_work_storage_unavailable", body["error"]["code"])
        self.assertEqual([], fixtures.WorkProvider.requests)
        self.assertEqual([("outcome_unknown", "provider_transport_ambiguous")], self.terminal_events())
        self.assertEqual(1, self.gateway.store.active_budget_reservations())
        self.assert_retained_hold_after_restart(work)

    def test_failover_settlement_failure_keeps_final_first_attempt_and_stops_retry(self):
        work = self.job()
        with patch.object(self.gateway.work_runtime, "settle",
                          side_effect=WorkRuntimeError("storage_unavailable", 503)):
            status, body, _ = self.raw_post(work, simulate_failover=True)
        self.assertEqual(503, status)
        self.assertEqual("hormuz_ai_work_storage_unavailable", body["error"]["code"])
        self.assertEqual(1, len(fixtures.WorkProvider.requests))
        self.assertEqual([("rate_limited", None)], self.terminal_events())
        self.assertEqual(0, self.gateway.store.active_budget_reservations())
        self.assert_retained_hold_after_restart(work)

    def test_inline_modalities_are_denied_before_egress_under_text_work_reservation(self):
        cases = [
            ("/v1/responses", {"input": [{"role": "user", "content": [
                {"type": "input_image", "image_url": "data:image/png;base64,AA=="}]}]}),
            ("/v1/responses", {"input": [{"role": "user", "content": [
                {"type": "input_file", "filename": "tiny.pdf", "file_data": "data:application/pdf;base64,AA=="}]}]}),
            ("/v1/messages", {"model": "claude-standard", "messages": [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AA=="}}]}]}),
            ("/v1/messages", {"model": "claude-standard", "messages": [{"role": "user", "content": [
                {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": "AA=="}}]}]}),
        ]
        for endpoint, fields in cases:
            with self.subTest(endpoint=endpoint, fields=fields):
                work = self.job()
                status, body, _ = self.raw_post(work, endpoint=endpoint, **fields)
                self.assertEqual(422, status, body)
                self.assertIn("request_cost_unbounded", json.dumps(body))
                state = self.gateway.work_runtime.get_work(self.identity, work)
                self.assertEqual([], state["attempts"])
                self.assertEqual(0, state["costs"]["consumed_microusd"])
        self.assertEqual([], fixtures.WorkProvider.requests)
        self.assertEqual(0, self.gateway.store.active_budget_reservations())

    def test_inline_image_remains_relayed_when_ai_work_is_disabled(self):
        self.config_value["ai_work"]["enabled"] = False
        self.config_value["ai_work"]["cache_enabled"] = False
        self.restart()
        status, body, _ = self.raw_post(None, endpoint="/v1/responses", input=[{"role": "user", "content": [
            {"type": "input_image", "image_url": "data:image/png;base64,AA=="}]}])
        self.assertEqual(200, status, body)
        self.assertEqual(1, len(fixtures.WorkProvider.requests))
        self.assertEqual("completed", body["status"])


if __name__ == "__main__":
    unittest.main()
