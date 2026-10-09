"""Provider examples must opt into live spending and preserve wire boundaries."""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tools import ai_work_provider_examples as examples


class ProviderExamplesTests(unittest.TestCase):
    def test_catalog_is_unique_and_default_live_calls_are_small(self):
        self.assertEqual(len(examples.EXAMPLES), 21)
        self.assertEqual(len({e.name for e in examples.EXAMPLES}), 21)
        for provider in ("openai", "anthropic", "both"):
            selected = examples.selected_examples(None, provider)
            self.assertEqual(len(selected), 3)
            self.assertTrue(all(e.live for e in selected))
        with self.assertRaises(ValueError):
            examples.selected_examples(["correction-bypasses-reuse"], "openai")
        with self.assertRaises(ValueError):
            examples.selected_examples(["anthropic-smoke"], "openai")

    def test_rate_card_never_substitutes_fixture_rates_or_accepts_unbounded_values(self):
        good = {"openai": {"model": "approved", **{key: 1 for key in examples.RATES}}}
        self.assertEqual(examples.qualified_routes(good, {"openai"})["openai"]["upstream_model"], "approved")
        for invalid in (None, True, -1, float("nan"), float("inf"), 0):
            value = {"openai": {**good["openai"], "input_cost_per_million": invalid}}
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                examples.qualified_routes(value, {"openai"})
        for model in ("", "model\nAuthorization", "x" * 129):
            with self.assertRaises(ValueError):
                examples.qualified_routes({"openai": {**good["openai"], "model": model}}, {"openai"})

    def test_list_never_loads_credentials_or_starts_a_gateway(self):
        with mock.patch.object(examples, "execute") as execute, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(examples.main(["--list"]), 0)
        execute.assert_not_called()

    def test_live_missing_rate_card_stops_before_gateway_egress(self):
        arguments = argparse.Namespace(live="openai", rate_card=None)
        with mock.patch.object(examples, "run_gateway") as run:
            with self.assertRaisesRegex(ValueError, "live_rate_card_required"):
                examples.execute(arguments, examples.selected_examples(None, "openai"))
        run.assert_not_called()

    def test_streaming_and_tool_payloads_match_each_endpoint(self):
        catalog = {e.name: e for e in examples.EXAMPLES}
        chat = examples.request_body(catalog["chat-stream"])
        self.assertEqual(chat["stream_options"], {"include_usage": True})
        self.assertEqual(chat["max_completion_tokens"], 256)
        responses = examples.request_body(catalog["responses-tool-declaration"])
        self.assertEqual(responses["tools"][0]["type"], "function")
        self.assertFalse(responses["store"])
        messages = examples.request_body(catalog["messages-tool-declaration"])
        self.assertIn("input_schema", messages["tools"][0])
        self.assertEqual(messages["max_tokens"], 256)

    def test_error_output_does_not_echo_credentials_or_provider_response(self):
        captured = io.StringIO()
        with mock.patch.object(examples, "execute", side_effect=OSError("private-secret-provider-body")), contextlib.redirect_stderr(captured):
            self.assertEqual(examples.main([]), 1)
        self.assertNotIn("private-secret", captured.getvalue())
        self.assertEqual(json.loads(captured.getvalue())["reason"], "example_execution_unavailable")

    def test_receipt_destination_is_reserved_before_live_execution(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "receipt.json"
            path.write_text("existing evidence")
            with mock.patch.object(examples, "execute") as execute, contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(examples.main(["--live", "both", "--receipt", str(path)]), 1)
            execute.assert_not_called()
            self.assertEqual(path.read_text(), "existing evidence")
            path.unlink()
            with mock.patch.object(examples, "execute", side_effect=OSError("offline")), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(examples.main(["--receipt", str(path)]), 1)
            self.assertFalse(path.exists())

    def test_startup_failure_still_closes_server_without_shutdown_deadlock(self):
        server = mock.Mock()
        with mock.patch.object(examples.threading, "Thread") as thread:
            thread.return_value.start.side_effect = RuntimeError("thread unavailable")
            with self.assertRaises(RuntimeError):
                with examples.serving(server):
                    self.fail("must not enter")
        server.shutdown.assert_not_called()
        server.server_close.assert_called_once()

    def test_actual_config_has_shorter_upstream_timeout_and_live_cache_disabled(self):
        from hormuz.config import GatewayConfig
        from tools.provider_example_transport import REQUEST_TIMEOUT, UPSTREAM_TIMEOUT
        routes = {"openai": {"protocol": "openai", "upstream_model": "synthetic-openai", **{key: 3 for key in examples.RATES}}}
        upstreams = {"openai": {"base_url": "http://127.0.0.1:8788", "api_key_env": "FIXTURE_KEY"}}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "gateway.json"
            path.write_text(json.dumps(examples.gateway_profile(routes, upstreams, live=True)))
            config = GatewayConfig.load(path, environ={"FIXTURE_KEY": "synthetic-key", "HORMUZ_EXAMPLE_TOKEN": "synthetic-example-token"})
        self.assertEqual(config.upstream_timeout_seconds, UPSTREAM_TIMEOUT)
        self.assertLess(config.upstream_timeout_seconds, REQUEST_TIMEOUT)
        self.assertFalse(config.ai_work.cache_enabled)
