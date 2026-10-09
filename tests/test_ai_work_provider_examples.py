"""Provider examples must opt into live spending and preserve wire boundaries."""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
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

    def test_no_thinking_canary_preserves_bounds_and_other_provider_payloads(self):
        for example in examples.EXAMPLES:
            original = examples.request_body(example)
            changed = examples.request_body(example, anthropic_no_thinking=True)
            with self.subTest(example=example.name):
                if example.provider == "anthropic":
                    self.assertNotIn("thinking", original)
                    self.assertNotIn("output_config", original)
                    self.assertEqual(changed["thinking"], {"type": "disabled"})
                    self.assertEqual(changed["output_config"], {"effort": "low"})
                    self.assertEqual(changed["max_tokens"], 256)
                    self.assertTrue({"temperature", "top_p", "top_k"}.isdisjoint(changed))
                    self.assertEqual({key: value for key, value in changed.items() if key not in {"thinking", "output_config"}}, original)
                else:
                    self.assertEqual(changed, original)

    def test_no_thinking_argument_requires_selected_live_anthropic(self):
        for arguments in ([], ["--live", "openai"], ["--live", "both", "--example", "openai-smoke"]):
            with self.subTest(arguments=arguments), mock.patch.object(examples, "execute") as execute, contextlib.redirect_stderr(io.StringIO()) as captured:
                self.assertEqual(examples.main([*arguments, "--anthropic-no-thinking"]), 1)
            execute.assert_not_called()
            self.assertEqual(json.loads(captured.getvalue())["reason"], "anthropic_no_thinking_requires_live_anthropic")
        with mock.patch.object(examples, "execute", return_value={}) as execute, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(examples.main(["--live", "anthropic", "--example", "anthropic-smoke", "--anthropic-no-thinking", "--max-live-calls", "1"]), 0)
        arguments, selected = execute.call_args.args
        self.assertTrue(arguments.anthropic_no_thinking)
        self.assertEqual([example.name for example in selected], ["anthropic-smoke"])
        self.assertEqual(arguments.max_live_calls, 1)

    def test_live_execution_forwards_no_thinking_and_records_configuration(self):
        rates = {"anthropic": {"model": "approved-haiku", **{key: 1 for key in examples.RATES}}}
        with tempfile.TemporaryDirectory() as folder:
            rate_card = Path(folder) / "rates.json"
            rate_card.write_text(json.dumps(rates))
            arguments = argparse.Namespace(live="anthropic", rate_card=str(rate_card), budget_microusd=100_000, max_live_calls=1, anthropic_no_thinking=True)
            with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "synthetic-unit-test-key"}, clear=True), mock.patch.object(examples, "run_gateway", return_value={}) as run, mock.patch.object(examples.subprocess, "run", return_value=mock.Mock(stdout="test-commit")):
                receipt = examples.execute(arguments, examples.selected_examples(["anthropic-smoke"], "anthropic"))
        self.assertTrue(run.call_args.kwargs["live"])
        self.assertTrue(run.call_args.kwargs["anthropic_no_thinking"])
        self.assertEqual(run.call_args.kwargs["max_live_calls"], 1)
        self.assertEqual(receipt["conditions"]["anthropic_thinking"], "disabled_low_effort")
        self.assertNotIn("synthetic-unit-test-key", json.dumps(receipt))

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

    def test_anthropic_only_config_requires_no_openai_key_or_selectable_route(self):
        from hormuz.config import GatewayConfig
        from hormuz.custody_runtime import resolve_upstream_credentials
        routes = {"anthropic": {"protocol": "anthropic", "upstream_model": "synthetic-anthropic", **{key: 3 for key in examples.RATES}}}
        upstreams = {"anthropic": {"base_url": "http://127.0.0.1:8788", "api_key_env": "FIXTURE_KEY"}}
        profile = examples.gateway_profile(routes, upstreams, live=True)
        environment = {"FIXTURE_KEY": "synthetic-unit-test-key", "HORMUZ_EXAMPLE_TOKEN": "synthetic-example-token"}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "gateway.json"
            path.write_text(json.dumps(profile))
            config = GatewayConfig.load(path, environ=environment)
        self.assertEqual(set(upstreams), {"anthropic"})
        self.assertEqual(set(config.model_routes), {"anthropic"})
        self.assertEqual(profile["policies"]["organization"]["allowed_models"], ["anthropic"])
        self.assertEqual(profile["policies"]["organization"]["fallback_models"], {"anthropic": "anthropic"})
        self.assertEqual(config.upstreams["openai"].base_url, "http://127.0.0.1:1")
        self.assertEqual(resolve_upstream_credentials(config, environ=environment)["openai"], "")


class ProviderExampleIntegrationTests(unittest.TestCase):
    """One explicitly selected local integration call; no provider accounts."""

    def test_anthropic_only_actual_gateway_uses_one_owned_fixture_and_closes(self):
        from tools.ai_work_proof import ProofGatewayServer
        from tools.ai_work_sdk_qualification import LoopbackBoundary
        from tools.provider_example_transport import BoundedFixtureServer
        initial_threads = set(threading.enumerate())
        original_calls = examples.ProviderFixture.calls
        examples.ProviderFixture.calls = []
        gateways, boundaries = [], []
        try:
            with mock.patch.dict(os.environ, {}, clear=True), contextlib.ExitStack() as resources:
                provider = BoundedFixtureServer(("127.0.0.1", 0), examples.ProviderFixture)
                resources.enter_context(examples.serving(provider))

                def create_gateway(config, *, environ):
                    gateway = ProofGatewayServer(config, environ=environ)
                    gateways.append(gateway)
                    try:
                        self.assertEqual(gateway.upstream_credentials["openai"], "")
                        boundary = LoopbackBoundary(gateway.server_port, provider.server_port)
                        boundaries.append(boundary)
                        resources.enter_context(boundary.enforce())
                    except BaseException:
                        gateway.server_close()
                        raise
                    return gateway

                routes = {"anthropic": {"protocol": "anthropic", "upstream_model": "synthetic-anthropic", **{key: 3 for key in examples.RATES}}}
                upstreams = {"anthropic": {"base_url": f"http://127.0.0.1:{provider.server_port}", "api_key_env": "FIXTURE_KEY"}}
                with mock.patch("hormuz.server.GatewayServer", side_effect=create_gateway):
                    result = examples.run_gateway(examples.selected_examples(["anthropic-smoke"], "anthropic"), routes, upstreams, {"FIXTURE_KEY": "synthetic-unit-test-key"}, live=True, budget_microusd=100_000, max_live_calls=1, anthropic_no_thinking=True)
                self.assertEqual(result["gateway_requests_attempted"], 1)
                self.assertEqual(result["successful_uncached_provider_responses_observed"], 1)
                self.assertTrue(result["checks"][0]["passed"])
                self.assertEqual(examples.ProviderFixture.calls, [{"path": "/v1/messages", "model": "synthetic-anthropic"}])
                self.assertEqual(len(boundaries), 1)
                self.assertEqual(boundaries[0].connections["provider"], 1)
                self.assertEqual(boundaries[0].refused, 0)
            self.assertEqual(provider.fileno(), -1)
            self.assertTrue(all(gateway.fileno() == -1 for gateway in gateways))
            self.assertFalse(any(thread not in initial_threads for thread in threading.enumerate()))
        finally:
            examples.ProviderFixture.calls = original_calls
