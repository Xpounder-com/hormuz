"""Provider examples must opt into live spending and preserve wire boundaries."""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

from tools import ai_work_provider_examples as examples


@contextlib.contextmanager
def without_provider_credentials():
    # Keep TMPDIR/TEMP/TMP: the first tempfile lookup caches its choice globally.
    with mock.patch.dict(os.environ):
        for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "HORMUZ_EXAMPLE_TOKEN",
                     "HORMUZ_EXAMPLE_UNUSED_OPENAI_KEY", "HORMUZ_SYNTHETIC_PROVIDER_KEY"):
            os.environ.pop(name, None)
        yield


class ProviderExamplesTests(unittest.TestCase):
    def test_credential_isolation_preserves_first_tempfile_choice_in_fresh_process(self):
        with tempfile.TemporaryDirectory(prefix="hormuz-temp-isolation-", dir=Path.cwd()) as folder:
            code = """import json, os, tempfile
from pathlib import Path
from tests.test_ai_work_provider_examples import without_provider_credentials
expected = Path(os.environ['TMPDIR'])
assert tempfile.tempdir is None
with without_provider_credentials():
    assert all(name not in os.environ for name in ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'HORMUZ_EXAMPLE_TOKEN'))
    assert all(Path(os.environ[name]) == expected for name in ('TMPDIR', 'TEMP', 'TMP'))
    assert os.environ['HORMUZ_TEMP_ISOLATION_SENTINEL'] == 'preserved'
    with tempfile.TemporaryDirectory() as child:
        assert Path(child).parent == expected
assert Path(tempfile.gettempdir()) == expected
assert os.environ['OPENAI_API_KEY'] == 'synthetic-isolation-test-key'
print(json.dumps({'first_tempfile_choice_preserved': True, 'credentials_restored': True}))
"""
            environment = {**os.environ, "TMPDIR": folder, "TEMP": folder, "TMP": folder,
                           "OPENAI_API_KEY": "synthetic-isolation-test-key",
                           "ANTHROPIC_API_KEY": "synthetic-isolation-test-key",
                           "HORMUZ_EXAMPLE_TOKEN": "synthetic-isolation-test-token",
                           "HORMUZ_TEMP_ISOLATION_SENTINEL": "preserved"}
            result = subprocess.run([sys.executable, "-c", code], cwd=examples.ROOT,
                                    env=environment, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {"first_tempfile_choice_preserved": True, "credentials_restored": True})

    def run_in_memory_batch(self, names, *, failure_kind="incomplete", snapshot_error=False):
        """Real SQLite costs, mocked transport/lifecycle, and zero listeners."""
        from hormuz.work_runtime import WorkRuntime
        from hormuz.work_client import WorkClientError
        gateways, requests, cleanup, creations = [], [], [], []

        class Gateway:
            def __init__(self, config, *, environ):
                self.server_port, self.config = 12345, config
                self.owner = next(iter(config.identities_by_token.values()))
                self.work_runtime = WorkRuntime(config.ai_work.database_path, config)
                self.path = self.work_runtime.path
                self.closed = False
                self.pending = None
                report = self.work_runtime.report

                def after_drain(identity, *, administrator):
                    self_test.assertTrue(self.closed)
                    self_test.assertTrue(self.path.exists())
                    self_test.assertFalse(administrator)
                    if snapshot_error:
                        raise OSError("private_snapshot_error_body")
                    return report(identity, administrator=administrator)
                self.work_runtime.report = after_drain
                gateways.append(self)

        @contextlib.contextmanager
        def serving(gateway):
            try:
                yield gateway
            finally:
                if gateway.pending:
                    gateway.work_runtime.settle(gateway.owner, gateway.pending, 7, "failed")
                gateway.work_runtime.close()
                gateway.closed = True
                cleanup.append("gateway")
                if failure_kind == "cleanup":
                    raise OSError("private_cleanup_body")

        class Client:
            def __init__(self, *_args, **_kwargs):
                self.gateway = gateways[-1]

            def set_plan(self, scope, identifier, **values):
                return self.gateway.work_runtime.set_plan(self.gateway.owner.organization_id, scope, identifier, **values)

            def create_job(self, repository, **values):
                creations.append(values)
                if failure_kind == "job_creation" and len(creations) == 2:
                    raise ValueError("private_secret_identifier")
                return self.gateway.work_runtime.create_work(self.gateway.owner, repository, **values)

            def job(self, work_id):
                return SimpleNamespace(work_id=work_id, headers={"synthetic-work-id": work_id},
                                       state=lambda: self.gateway.work_runtime.get_work(self.gateway.owner, work_id))

        class Transport:
            def __init__(self, _port):
                pass

            def __enter__(self):
                if failure_kind == "transport_enter":
                    raise ValueError("private_secret_identifier")
                return self

            def __exit__(self, *_args):
                cleanup.append("transport")

            def request(self, _method, api, _body, headers):
                gateway = gateways[-1]
                identifier = "synthetic-request-" + str(len(requests) + 1)
                requests.append(api)
                gateway.work_runtime.reserve(gateway.owner, headers["synthetic-work-id"], identifier, "openai", "openai", 19)
                if len(requests) == 1:
                    gateway.work_runtime.settle(gateway.owner, identifier, 11, "succeeded")
                    return 200, {"content-type": "application/json"}, json.dumps({"object": "response", "status": "completed", "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "private_response_body"}]}]}).encode()
                if failure_kind == "timeout":
                    gateway.work_runtime.settle(gateway.owner, identifier, status="unknown")
                    raise WorkClientError("example_transport_timeout")
                gateway.pending = identifier
                if failure_kind == "unsafe_exception":
                    raise ValueError("private_secret_identifier")
                if failure_kind == "stream":
                    return 200, {"content-type": "text/event-stream"}, b'data: {"object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"private_response_body"},"finish_reason":"stop"}]}\n\n'
                return 200, {"content-type": "application/json"}, b'{"object":"response","status":"incomplete","output":[],"private":"private_response_body"}'

        self_test = self
        routes = {"openai": {"protocol": "openai", "upstream_model": "synthetic-openai", **{key: 3 for key in examples.RATES}}}
        upstreams = {"openai": {"base_url": "http://127.0.0.1:1", "api_key_env": "FIXTURE_KEY"}}
        selected = examples.selected_examples(names, None)
        with mock.patch("hormuz.server.GatewayServer", Gateway), mock.patch("hormuz.work_client.WorkClient", Client), mock.patch.object(examples, "serving", serving), mock.patch.object(examples, "LoopbackTransport", Transport):
            result = examples.run_gateway(selected, routes, upstreams, {"FIXTURE_KEY": "synthetic-unit-test-key"}, live=all(item.live for item in selected), budget_microusd=100_000, max_live_calls=len(names))
        self.assertEqual(cleanup, ["gateway"] if failure_kind == "transport_enter" else ["transport", "gateway"])
        self.assertFalse(gateways[0].path.exists())
        self.assertNotIn("private_", json.dumps(result))
        return result, requests

    def test_failed_batch_keeps_prior_checks_and_final_failed_cost_without_replay(self):
        for kind, second in (("incomplete", "openai-explain-code"), ("stream", "chat-stream")):
            with self.subTest(kind=kind):
                result, requests = self.run_in_memory_batch(["openai-smoke", second, "openai-review-change"], failure_kind=kind)
                self.assertEqual(result["status"], "failed")
                self.assertEqual([row["example"] for row in result["checks"]], ["openai-smoke"])
                self.assertEqual(result["examples_completed"], 1)
                self.assertEqual(result["gateway_requests_attempted"], 2)
                self.assertEqual(len(requests), 2)
                self.assertEqual(result["successful_uncached_provider_responses_observed"], 1)
                self.assertEqual(result["failure"], {"example": second, "api": "/v1/chat/completions" if kind == "stream" else "/v1/responses", "behavior": "stream" if kind == "stream" else "request", "scenario_index": 2, "phase": "endpoint_validation", "observed_status": 200, "passed": False, "reason": "incomplete_provider_response"})
                self.assertEqual(result["gateway_totals"]["committed_microusd"], 18)
                self.assertEqual(result["gateway_totals"]["failed_attempts"], 1)
                self.assertEqual(result["gateway_totals"]["pending_microusd"], 0)
                self.assertFalse(result["gateway_totals"]["invoice_finality"])
                self.assertTrue(result["outcome_observations"].startswith("none"))

    def test_timeout_receipt_retains_unknown_charge_hold_and_no_status_guess(self):
        result, requests = self.run_in_memory_batch(["openai-smoke", "openai-explain-code", "openai-review-change"], failure_kind="timeout")
        self.assertEqual(len(requests), 2)
        self.assertEqual(result["failure"]["reason"], "example_transport_timeout")
        self.assertIsNone(result["failure"]["observed_status"])
        self.assertEqual(result["gateway_totals"]["committed_microusd"], 11)
        self.assertEqual(result["gateway_totals"]["uncertain_microusd"], 19)
        self.assertEqual(result["gateway_totals"]["unknown_attempts"], 1)
        self.assertEqual(result["gateway_totals"]["consumed_microusd"], 30)

    def test_failed_snapshot_is_null_and_does_not_replace_original_failure(self):
        result, _ = self.run_in_memory_batch(["openai-smoke", "openai-explain-code"], snapshot_error=True)
        self.assertEqual(result["failure"]["reason"], "incomplete_provider_response")
        self.assertIsNone(result["gateway_totals"])
        self.assertEqual(result["gateway_totals_error"], "example_ledger_snapshot_unavailable")

    def test_arbitrary_identifier_exception_is_not_receipt_metadata(self):
        result, _ = self.run_in_memory_batch(["openai-smoke", "openai-explain-code"], failure_kind="unsafe_exception")
        self.assertEqual(result["failure"]["reason"], "example_execution_unavailable")

    def test_new_scenario_setup_failure_cannot_inherit_prior_http_status(self):
        result, requests = self.run_in_memory_batch(["openai-smoke", "openai-explain-code", "openai-review-change"], failure_kind="job_creation")
        self.assertEqual(len(requests), 1)
        self.assertEqual(result["gateway_requests_attempted"], 1)
        self.assertEqual(result["failure"]["example"], "openai-explain-code")
        self.assertEqual(result["failure"]["phase"], "job_creation")
        self.assertIsNone(result["failure"]["observed_status"])
        self.assertEqual(result["gateway_totals"]["attempts"], 1)
        self.assertEqual(result["gateway_totals"]["committed_microusd"], 11)

    def test_context_job_creation_failure_cannot_inherit_first_response_status(self):
        result, requests = self.run_in_memory_batch(["context-change"], failure_kind="job_creation")
        self.assertEqual(len(requests), 1)
        self.assertEqual(result["failure"]["phase"], "job_creation")
        self.assertIsNone(result["failure"]["observed_status"])
        self.assertEqual(result["gateway_totals"]["committed_microusd"], 11)

    def test_lifecycle_errors_preserve_original_failure_and_conservative_snapshot(self):
        result, requests = self.run_in_memory_batch(["openai-smoke", "openai-explain-code"], failure_kind="cleanup")
        self.assertEqual(len(requests), 2)
        self.assertEqual(result["failure"]["reason"], "incomplete_provider_response")
        self.assertEqual(result["cleanup_error"], "example_gateway_cleanup_unavailable")
        self.assertEqual(result["gateway_totals_snapshot"], "after_lifecycle_error")
        result, requests = self.run_in_memory_batch(["openai-smoke"], failure_kind="transport_enter")
        self.assertEqual(requests, [])
        self.assertEqual(result["failure"]["phase"], "gateway_startup")
        self.assertEqual(result["gateway_requests_attempted"], 0)
        self.assertEqual(result["gateway_totals"]["attempts"], 0)

    def test_internal_source_fence_retains_run_metadata_and_rejects_changed_or_missing_source(self):
        for mutation in (None, "change", "remove"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                names = ["tools/ai_work_provider_examples.py", "tools/provider_example_transport.py", "tools/provider_example_responses.py", "core.py"]
                for name in names:
                    path = root / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("original source")
                manifest = root / "docs/evidence/ai-work-functional/receipt.json"
                manifest.parent.mkdir(parents=True)
                manifest.write_text(json.dumps({"source_files": {"core.py": "predecessor-digest"}}))
                rates = root / "private-rates.json"
                rates.write_text(json.dumps({"openai": {"model": "synthetic-qualified", **{key: 1 for key in examples.RATES}}}))
                arguments = argparse.Namespace(live="openai", rate_card=str(rates), budget_microusd=100_000, max_live_calls=1)

                def run(*_args, **_kwargs):
                    if mutation == "change":
                        (root / "core.py").write_text("modified during dispatch")
                    elif mutation == "remove":
                        (root / "core.py").unlink()
                    result = {"status": "passed", "gateway_requests_attempted": 1, "gateway_totals": {"uncertain_microusd": 19}}
                    if mutation == "remove":
                        result.update(status="failed", failure={"reason": "incomplete_provider_response"})
                    return result

                with mock.patch.object(examples, "ROOT", root), mock.patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-unit-test-key"}, clear=True), mock.patch.object(examples, "run_gateway", side_effect=run) as dispatch, mock.patch.object(examples.subprocess, "run", return_value=mock.Mock(stdout="starting-commit")):
                    receipt = examples.execute(arguments, examples.selected_examples(["openai-smoke"], "openai"))
                self.assertEqual(dispatch.call_count, 1)
                self.assertEqual(receipt["status"], "failed" if mutation else "passed")
                self.assertEqual(receipt["gateway_totals"]["uncertain_microusd"], 19)
                self.assertEqual(receipt["source_verified_before_and_after"], mutation is None)
                if mutation == "change":
                    self.assertNotEqual(receipt["source_files_after"]["core.py"], receipt["source_files"]["core.py"])
                    self.assertEqual(receipt["source_verification_error"], "example_source_changed")
                elif mutation == "remove":
                    self.assertEqual(receipt["source_verification_error"], "example_source_manifest_unavailable")
                    self.assertEqual(receipt["failure"]["reason"], "incomplete_provider_response")
                self.assertNotIn("synthetic-unit-test-key", json.dumps(receipt))

    def test_failed_run_saves_partial_receipt_and_returns_nonzero_without_reexecution(self):
        result, requests = self.run_in_memory_batch(["openai-smoke", "openai-explain-code"])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "failed-receipt.json"
            with mock.patch.object(examples, "execute", return_value=result) as execute, contextlib.redirect_stderr(io.StringIO()) as captured:
                self.assertEqual(examples.main(["--receipt", str(path)]), 1)
            execute.assert_called_once()
            self.assertEqual(json.loads(path.read_text()), result)
            self.assertEqual(json.loads(captured.getvalue()), result)
        self.assertEqual(len(requests), 2)

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
            with without_provider_credentials(), contextlib.ExitStack() as resources:
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
