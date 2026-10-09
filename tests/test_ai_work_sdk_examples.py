"""Protect SDK work attachment, transport isolation and reporting boundaries."""

import argparse
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import types
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location("work_sdk_example", Path(__file__).parents[1] / "examples/sdk/work_sdk.py")
example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(example)


class WorkSDKExamplesTests(unittest.TestCase):
    def test_precise_budget_rejects_unbounded_or_negative_values(self):
        self.assertEqual(example.budget("0.10"), 100_000)
        self.assertEqual(example.budget("0.000001"), 1)
        for value in ("0", "-1", "NaN", "Infinity", "100.000001", "0.0000001", "text"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                example.budget(value)

    def test_protocol_payloads_preserve_work_id_and_usage_contracts(self):
        for api in example.APIS:
            headers = {"X-Hormuz-Work-Id": "work-fixture"}
            request = example.payload(api, "approved-alias", headers, stream=True, max_output_tokens=128)
            self.assertEqual(request["extra_headers"], headers)
            self.assertIsNot(request["extra_headers"], headers)
            self.assertNotIn("api_key", request)
            if api == "openai-chat":
                self.assertEqual(request["stream_options"], {"include_usage": True})
                self.assertEqual(request["max_completion_tokens"], 128)
            elif api == "openai-responses":
                self.assertFalse(request["store"])
                self.assertEqual(request["max_output_tokens"], 128)
            else:
                self.assertEqual(request["max_tokens"], 128)

    def test_sdk_transport_never_follows_redirects_or_implicitly_retries(self):
        for api in example.APIS:
            with self.subTest(api=api):
                transport = mock.MagicMock()
                sdk = mock.MagicMock()
                sdk.__enter__.return_value = sdk
                creator = mock.Mock(return_value=sdk)
                selected = mock.Mock(return_value=transport)
                legacy = mock.Mock()
                modules = {"openai": types.SimpleNamespace(OpenAI=creator, DefaultHttpx2Client=selected, DefaultHttpxClient=legacy), "anthropic": types.SimpleNamespace(Anthropic=creator, DefaultHttpx2Client=selected, DefaultHttpxClient=legacy)}
                with mock.patch.dict("sys.modules", modules):
                    example.dispatch(api, "https://gateway.example", "private-fixture-credential", {"stream": False})
                selected.assert_called_once_with(follow_redirects=False, timeout=30)
                legacy.assert_not_called()
                self.assertEqual(creator.call_args.kwargs["max_retries"], 0)
                self.assertEqual(creator.call_args.kwargs["timeout"], 30)
                self.assertIs(creator.call_args.kwargs["http_client"], transport)
                self.assertEqual(creator.call_args.kwargs["base_url"], "https://gateway.example" + ("/v1" if api.startswith("openai-") else ""))
                sdk.__exit__.assert_called_once()
                transport.close.assert_called_once()

    def test_each_vendor_legacy_transport_export_is_compatible(self):
        # Anthropic's current public name is still DefaultHttpxClient even
        # when its implementation wraps HTTPX2; older OpenAI uses it as well.
        for api, vendor, constructor in (("openai-responses", "openai", "OpenAI"), ("anthropic-messages", "anthropic", "Anthropic")):
            with self.subTest(api=api):
                transport, sdk = mock.MagicMock(), mock.MagicMock()
                sdk.__enter__.return_value = sdk
                factory = mock.Mock(return_value=transport)
                creator = mock.Mock(return_value=sdk)
                module = types.SimpleNamespace(DefaultHttpxClient=factory, **{constructor: creator})
                with mock.patch.dict("sys.modules", {vendor: module}):
                    example.dispatch(api, "https://gateway.example", "private-fixture-credential", {"stream": False})
                factory.assert_called_once_with(follow_redirects=False, timeout=30)
                self.assertIs(creator.call_args.kwargs["http_client"], transport)
                transport.close.assert_called_once()

    def test_missing_transport_stops_before_sdk_construction(self):
        creator = mock.Mock()
        with mock.patch.dict("sys.modules", {"openai": types.SimpleNamespace(OpenAI=creator)}):
            with self.assertRaisesRegex(ValueError, "^sdk_transport_unavailable$"):
                example.dispatch("openai-responses", "https://gateway.example", "private-fixture-credential", {"stream": False})
        creator.assert_not_called()

    def test_transport_closes_when_sdk_construction_fails(self):
        transport = mock.Mock()
        creator = mock.Mock(side_effect=TypeError("private-sdk-error-body"))
        module = types.SimpleNamespace(OpenAI=creator, DefaultHttpx2Client=mock.Mock(return_value=transport))
        with mock.patch.dict("sys.modules", {"openai": module}):
            with self.assertRaises(TypeError):
                example.dispatch("openai-responses", "https://gateway.example", "private-fixture-credential", {"stream": False})
        transport.close.assert_called_once()

    def test_failed_stream_closes_stream_sdk_and_transport(self):
        for api, vendor, constructor in (("openai-responses", "openai", "OpenAI"), ("anthropic-messages", "anthropic", "Anthropic")):
            with self.subTest(api=api):
                transport, sdk, response = mock.MagicMock(), mock.MagicMock(), mock.MagicMock()
                sdk.__enter__.return_value = sdk
                response.__iter__.side_effect = OSError("private-stream-error-body")
                target = sdk.responses if vendor == "openai" else sdk.messages
                target.create.return_value = response
                module = types.SimpleNamespace(DefaultHttpxClient=mock.Mock(return_value=transport), **{constructor: mock.Mock(return_value=sdk)})
                with mock.patch.dict("sys.modules", {vendor: module}):
                    with self.assertRaises(OSError):
                        example.dispatch(api, "https://gateway.example", "private-fixture-credential", {"stream": True})
                response.close.assert_called_once()
                sdk.__exit__.assert_called_once()
                transport.close.assert_called_once()

    def test_sdk_response_is_attached_but_never_declares_job_complete(self):
        args = example.parser().parse_args(["--api", "openai-responses", "--model", "approved-alias", "--execute"])
        client = mock.Mock(endpoint="https://gateway.example")
        client.create_job.return_value = {"work_id": "work-fixture"}
        job = client.job.return_value
        job.work_id = "work-fixture"
        job.headers = {"X-Hormuz-Work-Id": "work-fixture"}
        job.state.return_value = {"state": "active", "attempts": [{}]}
        with mock.patch.object(example, "dispatch") as send:
            receipt = example.execute(args, client, "private-fixture-credential")
        client.set_plan.assert_called_once_with("job", "work-fixture", budget_microusd=100_000, objective="cost")
        self.assertEqual(send.call_args.args[3]["extra_headers"], job.headers)
        job.observe.assert_not_called()
        self.assertFalse(receipt["completion_observation_submitted"])
        self.assertNotIn("credential", receipt)
        self.assertFalse(receipt["provider_invoice_reconciled"])

    def test_retry_reuses_work_instead_of_creating_another_job(self):
        args = example.parser().parse_args(["--api", "openai-responses", "--model", "approved-alias", "--work-id", "work-existing", "--execute"])
        client = mock.Mock(endpoint="https://gateway.example")
        job = client.job.return_value
        job.work_id, job.headers = "work-existing", {"X-Hormuz-Work-Id": "work-existing"}
        job.state.return_value = {"state": "active", "attempts": [{}, {}]}
        with mock.patch.object(example, "dispatch") as dispatch:
            receipt = example.execute(args, client, "private-fixture-credential")
        client.create_job.assert_not_called()
        client.job.assert_called_once_with("work-existing")
        self.assertEqual(dispatch.call_args.args[3]["extra_headers"], job.headers)
        self.assertEqual(receipt["work_id"], "work-existing")
        self.assertEqual(receipt["attempts_visible"], 2)

    def test_post_create_failures_keep_work_id_without_sdk_body(self):
        from hormuz.work_client import WorkClient
        client = mock.Mock(endpoint="https://gateway.example")
        client.create_job.return_value = {"work_id": "work-created"}
        client.job.return_value.work_id = "work-created"
        client.job.return_value.headers = {"X-Hormuz-Work-Id": "work-created"}
        captured = io.StringIO()
        with mock.patch.object(WorkClient, "from_environment", return_value=client), mock.patch.dict(example.os.environ, {"HORMUZ_TOKEN": "private-fixture-credential"}), mock.patch.object(example, "dispatch", side_effect=RuntimeError("private-provider-body")), contextlib.redirect_stdout(captured):
            self.assertEqual(example.main(["--api", "openai-responses", "--model", "approved-alias", "--execute"]), 1)
        receipt = json.loads(captured.getvalue())
        self.assertEqual(receipt["work_id"], "work-created")
        self.assertEqual(receipt["failure_type"], "RuntimeError")
        self.assertNotIn("private-provider", captured.getvalue())
        self.assertNotIn("private-fixture", captured.getvalue())
        client.create_job.assert_called_once()

    def test_plan_and_state_failures_keep_the_same_work_handle(self):
        args = example.parser().parse_args(["--api", "anthropic-messages", "--model", "approved-alias", "--execute"])
        for stage in ("set_plan", "state"):
            with self.subTest(stage=stage):
                client = mock.Mock(endpoint="https://gateway.example")
                client.create_job.return_value = {"work_id": "work-created"}
                job = client.job.return_value
                job.work_id, job.headers = "work-created", {"X-Hormuz-Work-Id": "work-created"}
                target = client.set_plan if stage == "set_plan" else job.state
                target.side_effect = OSError("private-gateway-detail")
                with mock.patch.object(example, "dispatch"), self.assertRaises(example.SDKExampleFailure) as caught:
                    example.execute(args, client, "private-fixture-credential")
                self.assertEqual(caught.exception.work_id, "work-created")
                self.assertEqual(caught.exception.failure_type, "OSError")
                self.assertEqual(str(caught.exception), "sdk_example_failed")

    def test_reused_work_id_rejects_paths_and_header_injection(self):
        for identifier in ("work/other", "work\nAuthorization", "", "x" * 129):
            with self.subTest(identifier=identifier), self.assertRaises(argparse.ArgumentTypeError):
                example.work_id(identifier)
