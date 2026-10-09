"""Protect SDK work attachment, transport isolation and reporting boundaries."""

import argparse
import contextlib
from decimal import Inexact, InvalidOperation, ROUND_DOWN, Rounded, localcontext
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location("work_sdk_example", Path(__file__).parents[1] / "examples/sdk/work_sdk.py")
example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(example)


def sdk_json(api):
    if api == "openai-responses":
        return {"object": "response", "status": "completed", "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "42"}]}]}
    if api == "openai-chat":
        return {"object": "chat.completion", "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "42"}}]}
    return {"type": "message", "role": "assistant", "stop_reason": "end_turn", "content": [{"type": "text", "text": "42"}]}


def sdk_events(api):
    if api == "openai-responses":
        return [{"type": "response.output_text.delta", "delta": "42"}, {"type": "response.completed", "response": sdk_json(api)}]
    if api == "openai-chat":
        return [{"object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"content": "42"}, "finish_reason": None}]}, {"object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}, {"object": "chat.completion.chunk", "choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 1}}]
    return [{"type": "message_start", "message": {**sdk_json(api), "content": [], "stop_reason": None}}, {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "42"}}, {"type": "message_delta", "delta": {"stop_reason": "end_turn"}}, {"type": "message_stop"}]


class SDKStream:
    """Yield SDK objects while exercising the same public byte iterator hook."""

    def __init__(self, events, raw=b'data: {}\n\ndata: [DONE]\n\n', split=1):
        self.events, self.raw, self.split = events, raw, split
        self.byte_reads = 0
        self.close = mock.Mock()
        self.response = types.SimpleNamespace(iter_bytes=self.iter_bytes)

    def iter_bytes(self):
        self.byte_reads += 1
        for offset in range(0, len(self.raw), self.split):
            yield self.raw[offset:offset + self.split]

    def __iter__(self):
        for _chunk in self.response.iter_bytes():
            pass
        yield from self.events


def sdk_target(sdk, api):
    return sdk.responses if api == "openai-responses" else sdk.chat.completions if api == "openai-chat" else sdk.messages


class WorkSDKExamplesTests(unittest.TestCase):
    def test_precise_budget_rejects_unbounded_or_negative_values(self):
        self.assertEqual(example.budget("0.10"), 100_000)
        self.assertEqual(example.budget("0.000001"), 1)
        for value in ("0", "-1", "NaN", "Infinity", "100.000001", "0.0000001", "text"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                example.budget(value)

    def test_budget_preserves_ambient_decimal_context_and_exact_fraction(self):
        with localcontext() as context:
            context.prec = 2
            context.rounding = ROUND_DOWN
            context.traps[Inexact] = context.traps[Rounded] = True
            context.flags[InvalidOperation] = True
            flags, traps = dict(context.flags), dict(context.traps)
            self.assertEqual(example.budget("12.345678"), 12_345_678)
            self.assertEqual(example.budget("0.10000000000000000000000000000"), 100_000)
            for value in ("text", "NaN", "1e-999999999", "0.10000000000000000000000000001"):
                with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                    example.budget(value)
            self.assertEqual(dict(context.flags), flags)
            self.assertEqual(dict(context.traps), traps)
            self.assertEqual(context.prec, 2)
            self.assertEqual(context.rounding, ROUND_DOWN)

    def test_invalid_budget_does_not_set_callers_decimal_flags(self):
        with localcontext() as context:
            context.clear_flags()
            before = dict(context.flags)
            with self.assertRaises(argparse.ArgumentTypeError):
                example.budget("text")
            self.assertEqual(dict(context.flags), before)

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
                sdk_target(sdk, api).create.return_value = sdk_json(api)
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
                sdk_target(sdk, api).create.return_value = sdk_json(api)
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
        job.state.side_effect = [{"state": "active", "attempts": []}, {"state": "active", "attempts": [{"request_id": "request-new", "response_succeeded": True}]}]
        with mock.patch.object(example, "dispatch") as send:
            receipt = example.execute(args, client, "private-fixture-credential")
        client.set_plan.assert_called_once_with("job", "work-fixture", budget_microusd=100_000, objective="cost")
        self.assertEqual(send.call_args.args[3]["extra_headers"], job.headers)
        job.observe.assert_not_called()
        self.assertFalse(receipt["completion_observation_submitted"])
        self.assertNotIn("credential", receipt)
        self.assertFalse(receipt["provider_invoice_reconciled"])
        self.assertTrue(receipt["response_confirmed_by_gateway"])
        self.assertTrue(receipt["endpoint_completion_verified"])
        self.assertEqual(receipt["cost_basis"], "unknown")

    def test_complete_response_preserves_actual_unknown_hold_and_job_cost_basis(self):
        from hormuz.config import Identity, ModelRoute
        from hormuz.work_runtime import WorkRuntime

        identity = Identity("TOKEN", "private-fixture-token", "owner", "Owner", "team", "Team", organization_id="org")
        model = ModelRoute("synthetic", "openai", "synthetic", input_cost_per_million=1)
        for confirmed_prior_charge in (False, True):
            with self.subTest(confirmed_prior_charge=confirmed_prior_charge), tempfile.TemporaryDirectory() as folder:
                runtime = WorkRuntime(Path(folder) / "work.sqlite3")
                try:
                    identifier = runtime.create_work(identity, "synthetic/repository")["work_id"]
                    runtime.reserve(identity, identifier, "prior", model, "openai", 10)
                    runtime.settle(identity, "prior", 2)
                    if confirmed_prior_charge:
                        # Synthetic trusted-import evidence exercises the real
                        # aggregate basis; this is no live account qualification.
                        runtime.confirm_cost(identity, "prior", 2, "synthetic-receipt", source="provider_receipt", verified=True)
                    args = example.parser().parse_args(["--api", "openai-responses", "--model", "synthetic", "--work-id", identifier, "--execute"])
                    client = mock.Mock(endpoint="https://gateway.example")
                    job = client.job.return_value
                    job.work_id, job.headers = identifier, {"X-Hormuz-Work-Id": identifier}
                    job.state.side_effect = lambda: runtime.get_work(identity, identifier)

                    def completed_unknown_charge(*args):
                        runtime.reserve(identity, identifier, "new-response", model, "openai", 10)
                        runtime.settle(identity, "new-response", status="succeeded")

                    with mock.patch.object(example, "dispatch", side_effect=completed_unknown_charge) as dispatch:
                        receipt = example.execute(args, client, "private-fixture-token")
                    state = runtime.get_work(identity, identifier)
                    response = next(row for row in state["attempts"] if row["request_id"] == "new-response")
                    self.assertTrue(response["response_succeeded"])
                    self.assertEqual(response["state"], "unknown")
                    self.assertEqual(receipt["cost_basis"], state["costs"]["cost_basis"])
                    self.assertEqual(receipt["unsettled_job_charges"], {"pending_microusd": 0, "uncertain_microusd": 10})
                    self.assertEqual(state["costs"]["consumed_microusd"], 12)
                    self.assertEqual(receipt["gateway_confirmation_reads"], 1)
                    self.assertFalse(receipt["provider_invoice_reconciled"])
                    dispatch.assert_called_once()
                    client.create_job.assert_not_called()
                finally:
                    runtime.close()

    def test_retry_reuses_work_instead_of_creating_another_job(self):
        args = example.parser().parse_args(["--api", "openai-responses", "--model", "approved-alias", "--work-id", "work-existing", "--execute"])
        client = mock.Mock(endpoint="https://gateway.example")
        job = client.job.return_value
        job.work_id, job.headers = "work-existing", {"X-Hormuz-Work-Id": "work-existing"}
        old = {"request_id": "request-old", "response_succeeded": True}
        new = {"request_id": "request-new", "response_succeeded": True}
        job.state.side_effect = [{"state": "active", "attempts": [old]}, {"state": "active", "attempts": [old, new]}]
        with mock.patch.object(example, "dispatch") as dispatch:
            receipt = example.execute(args, client, "private-fixture-credential")
        client.create_job.assert_not_called()
        client.job.assert_called_once_with("work-existing")
        self.assertEqual(dispatch.call_args.args[3]["extra_headers"], job.headers)
        self.assertEqual(receipt["work_id"], "work-existing")
        self.assertEqual(receipt["attempts_visible"], 2)

    def test_only_a_new_successful_ledger_attempt_confirms_the_sdk_response(self):
        args = example.parser().parse_args(["--api", "openai-responses", "--model", "approved-alias", "--work-id", "work-existing", "--execute", "--stream"])
        old = {"request_id": "request-old", "response_succeeded": True}
        for attempts in ([old], [old, {"request_id": "request-new", "response_succeeded": False, "state": "unknown"}]):
            with self.subTest(attempts=attempts):
                client = mock.Mock(endpoint="https://gateway.example")
                job = client.job.return_value
                job.work_id, job.headers = "work-existing", {"X-Hormuz-Work-Id": "work-existing"}
                job.state.side_effect = [{"state": "active", "attempts": [old]}, {"state": "active", "attempts": attempts}]
                with mock.patch.object(example, "dispatch"), self.assertRaises(example.SDKExampleFailure) as caught:
                    example.execute(args, client, "private-fixture-credential")
                self.assertEqual(caught.exception.work_id, "work-existing")
                self.assertEqual(caught.exception.failure_type, "ValueError")
                self.assertEqual(str(caught.exception), "sdk_example_failed")
                client.create_job.assert_not_called()

    def test_concurrent_logical_groups_cannot_confirm_another_sdk_request(self):
        args = example.parser().parse_args(["--api", "openai-chat", "--model", "approved-alias", "--work-id", "work-existing", "--execute", "--stream"])
        for own_state in ("pending", "succeeded"):
            with self.subTest(own_state=own_state):
                client = mock.Mock(endpoint="https://gateway.example")
                job = client.job.return_value
                job.work_id, job.headers = "work-existing", {"X-Hormuz-Work-Id": "work-existing"}
                attempts = [{"request_id": "other-request", "logical_request_id": "other-logical", "state": "succeeded", "response_succeeded": True}, {"request_id": "sdk-request", "logical_request_id": "sdk-logical", "state": own_state, "response_succeeded": own_state == "succeeded"}]
                job.state.side_effect = [{"state": "active", "attempts": []}, {"state": "active", "attempts": attempts}]
                with mock.patch.object(example, "dispatch") as dispatch, mock.patch.object(example.time, "sleep") as sleep, self.assertRaises(example.SDKExampleFailure) as caught:
                    example.execute(args, client, "private-fixture-credential")
                self.assertEqual(caught.exception.work_id, "work-existing")
                self.assertEqual(caught.exception.failure_type, "ValueError")
                self.assertEqual(str(caught.exception), "sdk_example_failed")
                self.assertEqual(job.state.call_count, 2)
                dispatch.assert_called_once()
                sleep.assert_not_called()
                client.create_job.assert_not_called()

    def test_one_logical_failover_or_cache_group_confirms_without_replay(self):
        for attempts in (
            [{"request_id": "primary", "logical_request_id": "logical", "state": "failed", "response_succeeded": False}, {"request_id": "fallback", "logical_request_id": "logical", "retry_of": "primary", "state": "succeeded", "response_succeeded": True}],
            [{"request_id": "cache", "logical_request_id": "logical", "state": "cache_hit", "response_succeeded": True}],
            [{"request_id": "legacy", "state": "succeeded", "response_succeeded": True}],
        ):
            with self.subTest(attempts=attempts):
                job = mock.Mock()
                job.state.return_value = {"state": "active", "attempts": attempts}
                with mock.patch.object(example.time, "sleep") as sleep:
                    state, reads = example.confirm_gateway_response(job, set())
                self.assertEqual(state["attempts"], attempts)
                self.assertEqual(reads, 1)
                job.state.assert_called_once()
                sleep.assert_not_called()

    def test_distinct_legacy_request_ids_remain_ambiguous(self):
        job = mock.Mock()
        job.state.return_value = {"state": "active", "attempts": [{"request_id": "legacy-success", "state": "succeeded", "response_succeeded": True}, {"request_id": "legacy-pending", "logical_request_id": None, "state": "pending", "response_succeeded": False}]}
        with mock.patch.object(example.time, "sleep") as sleep, self.assertRaisesRegex(ValueError, "^sdk_gateway_confirmation_ambiguous$"):
            example.confirm_gateway_response(job, set())
        job.state.assert_called_once()
        sleep.assert_not_called()

    def test_pending_gateway_settlement_is_reread_without_another_inference(self):
        args = example.parser().parse_args(["--api", "openai-chat", "--model", "approved-alias", "--work-id", "work-existing", "--execute", "--stream"])
        client = mock.Mock(endpoint="https://gateway.example")
        job = client.job.return_value
        job.work_id, job.headers = "work-existing", {"X-Hormuz-Work-Id": "work-existing"}
        pending = {"request_id": "request-new", "state": "pending", "response_succeeded": False}
        successful = {**pending, "state": "succeeded", "response_succeeded": True}
        job.state.side_effect = [{"state": "active", "attempts": []}, {"state": "active", "attempts": [pending]}, {"state": "active", "attempts": [successful]}]
        with mock.patch.object(example, "dispatch") as dispatch, mock.patch.object(example.time, "sleep") as sleep, mock.patch.object(example.random, "uniform", return_value=0.01) as jitter:
            receipt = example.execute(args, client, "private-fixture-credential")
        self.assertEqual(receipt["gateway_confirmation_reads"], 2)
        self.assertTrue(receipt["response_confirmed_by_gateway"])
        self.assertEqual(job.state.call_count, 3)  # Pre-dispatch snapshot plus confirmation.
        dispatch.assert_called_once()
        client.create_job.assert_not_called()
        sleep.assert_called_once()
        self.assertAlmostEqual(sleep.call_args.args[0], 0.06)
        jitter.assert_called_once_with(0, 0.025)

    def test_terminal_gateway_results_are_not_polled_or_replayed(self):
        args = example.parser().parse_args(["--api", "openai-chat", "--model", "approved-alias", "--work-id", "work-existing", "--execute", "--stream"])
        for state in ("failed", "denied", "unknown"):
            with self.subTest(state=state):
                client = mock.Mock(endpoint="https://gateway.example")
                job = client.job.return_value
                job.work_id, job.headers = "work-existing", {"X-Hormuz-Work-Id": "work-existing"}
                job.state.side_effect = [{"state": "active", "attempts": []}, {"state": "active", "attempts": [{"request_id": "request-new", "state": state, "response_succeeded": False}]}]
                with mock.patch.object(example, "dispatch") as dispatch, mock.patch.object(example.time, "sleep") as sleep, self.assertRaises(example.SDKExampleFailure) as caught:
                    example.execute(args, client, "private-fixture-credential")
                self.assertEqual(caught.exception.work_id, "work-existing")
                self.assertEqual(caught.exception.failure_type, "ValueError")
                self.assertEqual(job.state.call_count, 2)
                dispatch.assert_called_once()
                sleep.assert_not_called()
                client.create_job.assert_not_called()

    def test_gateway_confirmation_wait_is_bounded_and_keeps_work_on_exhaustion(self):
        args = example.parser().parse_args(["--api", "openai-chat", "--model", "approved-alias", "--work-id", "work-existing", "--execute", "--stream"])
        client = mock.Mock(endpoint="https://gateway.example")
        job = client.job.return_value
        job.work_id, job.headers = "work-existing", {"X-Hormuz-Work-Id": "work-existing"}
        pending = {"state": "active", "attempts": [{"request_id": "request-new", "state": "pending", "response_succeeded": False}]}
        job.state.side_effect = [{"state": "active", "attempts": []}, pending, pending, pending, pending]
        with mock.patch.object(example, "dispatch") as dispatch, mock.patch.object(example.time, "sleep") as sleep, mock.patch.object(example.random, "uniform", return_value=0), self.assertRaises(example.SDKExampleFailure) as caught:
            example.execute(args, client, "private-fixture-credential")
        self.assertEqual(caught.exception.work_id, "work-existing")
        self.assertEqual(caught.exception.failure_type, "ValueError")
        self.assertEqual(job.state.call_count, 5)
        self.assertEqual(sleep.call_args_list, [mock.call(0.05), mock.call(0.1), mock.call(0.2)])
        dispatch.assert_called_once()
        client.create_job.assert_not_called()

    def test_metadata_timeout_after_pending_settlement_preserves_work_without_replay(self):
        args = example.parser().parse_args(["--api", "openai-chat", "--model", "approved-alias", "--work-id", "work-existing", "--execute", "--stream"])
        client = mock.Mock(endpoint="https://gateway.example")
        job = client.job.return_value
        job.work_id, job.headers = "work-existing", {"X-Hormuz-Work-Id": "work-existing"}
        job.state.side_effect = [{"state": "active", "attempts": []}, {"state": "active", "attempts": [{"request_id": "request-new", "state": "pending", "response_succeeded": False}]}, TimeoutError("private-metadata-error-body")]
        with mock.patch.object(example, "dispatch") as dispatch, mock.patch.object(example.time, "sleep"), self.assertRaises(example.SDKExampleFailure) as caught:
            example.execute(args, client, "private-fixture-credential")
        self.assertEqual(caught.exception.work_id, "work-existing")
        self.assertEqual(caught.exception.failure_type, "TimeoutError")
        self.assertEqual(str(caught.exception), "sdk_example_failed")
        self.assertEqual(job.state.call_count, 3)
        dispatch.assert_called_once()
        client.create_job.assert_not_called()

    def test_post_create_failures_keep_work_id_without_sdk_body(self):
        from hormuz.work_client import WorkClient
        client = mock.Mock(endpoint="https://gateway.example")
        client.create_job.return_value = {"work_id": "work-created"}
        client.job.return_value.work_id = "work-created"
        client.job.return_value.headers = {"X-Hormuz-Work-Id": "work-created"}
        client.job.return_value.state.return_value = {"state": "active", "attempts": []}
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

    def test_all_sdk_json_endpoints_check_completion_objects_without_serializing(self):
        for api in example.APIS:
            value = sdk_json(api)
            with self.subTest(api=api):
                example.validate_sdk_response(api, value)
                # Vendor SDK models expose attributes; no model_dump or text
                # serialization is needed for the same metadata validation.
                example.validate_sdk_response(api, types.SimpleNamespace(**value))

    def test_json_failure_and_incomplete_reasons_are_rejected(self):
        cases = [
            ("openai-responses", {**sdk_json("openai-responses"), "status": "failed"}, "sdk_provider_response_failed"),
            ("openai-responses", {**sdk_json("openai-responses"), "status": "incomplete"}, "sdk_provider_response_incomplete"),
            ("openai-responses", {**sdk_json("openai-responses"), "error": {"message": "private-error"}}, "sdk_provider_response_failed"),
            ("openai-responses", {**sdk_json("openai-responses"), "status": None}, "sdk_provider_response_incomplete"),
            ("anthropic-messages", {"type": "error", "error": {"message": "private-error"}}, "sdk_provider_response_failed"),
        ]
        for reason, expected in ((None, "sdk_provider_response_incomplete"), ("length", "sdk_provider_response_incomplete"), ("content_filter", "sdk_provider_response_failed")):
            cases.append(("openai-chat", {**sdk_json("openai-chat"), "choices": [{"finish_reason": reason, "message": {"role": "assistant"}}]}, expected))
        for reason in (None, "max_tokens", "pause_turn"):
            cases.append(("anthropic-messages", {**sdk_json("anthropic-messages"), "stop_reason": reason}, "sdk_provider_response_incomplete"))
        for api, response, reason in cases:
            with self.subTest(api=api, reason=reason), self.assertRaisesRegex(ValueError, "^" + reason + "$" ):
                example.validate_sdk_response(api, response)

    def test_all_sdk_streams_require_terminal_events_and_restore_byte_iterator(self):
        for api in example.APIS:
            with self.subTest(api=api):
                response = SDKStream(sdk_events(api))
                original = response.response.iter_bytes
                example.consume_sdk_stream(api, response)
                self.assertIs(response.response.iter_bytes, original)
                self.assertEqual(response.byte_reads, 1)

    def test_missing_stream_terminal_and_error_events_fail_each_endpoint(self):
        for api in example.APIS:
            if api == "openai-chat":
                partial = sdk_events(api)[:1]
            else:
                partial = sdk_events(api)[:-1]
            for events, reason in ((partial, "sdk_provider_response_incomplete"), ([{"type": "error", "error": {"message": "private-error"}}], "sdk_provider_response_failed")):
                with self.subTest(api=api, reason=reason), self.assertRaisesRegex(ValueError, "^" + reason + "$"):
                    example.consume_sdk_stream(api, SDKStream(events))

    def test_stream_terminal_incomplete_and_failed_payloads_are_rejected(self):
        for events, reason in (([{"type": "response.failed"}], "sdk_provider_response_failed"), ([{"type": "response.incomplete"}], "sdk_provider_response_incomplete"), ([{"type": "response.completed", "response": {**sdk_json("openai-responses"), "status": "incomplete"}}], "sdk_provider_response_incomplete")):
            with self.subTest(events=events), self.assertRaisesRegex(ValueError, "^" + reason + "$"):
                example.consume_sdk_stream("openai-responses", SDKStream(events))
        for reason in ("length", "content_filter"):
            events = [{"object": "chat.completion.chunk", "choices": [{"index": 0, "finish_reason": reason}]}]
            expected = "sdk_provider_response_incomplete" if reason == "length" else "sdk_provider_response_failed"
            with self.assertRaisesRegex(ValueError, "^" + expected + "$"):
                example.consume_sdk_stream("openai-chat", SDKStream(events))
        for reason in ("max_tokens", "pause_turn"):
            events = sdk_events("anthropic-messages")
            events[-2] = {"type": "message_delta", "delta": {"stop_reason": reason}}
            with self.assertRaisesRegex(ValueError, "^sdk_provider_response_incomplete$"):
                example.consume_sdk_stream("anthropic-messages", SDKStream(events))

    def test_chat_done_observer_handles_chunk_boundaries_and_rejects_truncated_frames(self):
        events = sdk_events("openai-chat")
        for raw in (b'data: [DONE]\n\n', b'data:[DONE]\r\n\r\n', b': keepalive\ndata: [DONE]\n\n'):
            for split in (1, 3, len(raw)):
                with self.subTest(raw=raw, split=split):
                    example.consume_sdk_stream("openai-chat", SDKStream(events, raw, split))
        for raw in (b'data: {}\n\n', b'data: [DONE]\n', b'data: [DON', b'data: [DONE]\ndata: {}\n\n'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                example.consume_sdk_stream("openai-chat", SDKStream(events, raw))

    def test_chat_all_choices_must_finish_and_data_after_done_is_invalid(self):
        events = sdk_events("openai-chat")
        events.insert(1, {"object": "chat.completion.chunk", "choices": [{"index": 1, "finish_reason": None}]})
        with self.assertRaisesRegex(ValueError, "^sdk_provider_response_incomplete$"):
            example.consume_sdk_stream("openai-chat", SDKStream(events))
        with self.assertRaisesRegex(ValueError, "^sdk_provider_response_invalid$"):
            example.consume_sdk_stream("openai-chat", SDKStream(sdk_events("openai-chat"), b'data: [DONE]\n\ndata: {}\n\n'))

    def test_stream_consumption_is_bounded_by_bytes_and_event_count(self):
        for api in example.APIS:
            with self.subTest(api=api), mock.patch.object(example, "MAX_STREAM_BYTES", 4), self.assertRaisesRegex(ValueError, "^sdk_stream_limit_exceeded$"):
                example.consume_sdk_stream(api, SDKStream(sdk_events(api), b'12345'))
            with self.subTest(api=api), mock.patch.object(example, "MAX_STREAM_EVENTS", 1), self.assertRaisesRegex(ValueError, "^sdk_stream_limit_exceeded$"):
                example.consume_sdk_stream(api, SDKStream(sdk_events(api)))

    def test_incomplete_sdk_stream_closes_resources_and_preserves_work_id(self):
        from hormuz.work_client import WorkClient
        for api in example.APIS:
            with self.subTest(api=api):
                transport, sdk = mock.MagicMock(), mock.MagicMock()
                sdk.__enter__.return_value = sdk
                response = SDKStream(sdk_events(api)[:1])
                sdk_target(sdk, api).create.return_value = response
                creator = mock.Mock(return_value=sdk)
                modules = {"openai": types.SimpleNamespace(OpenAI=creator, DefaultHttpx2Client=mock.Mock(return_value=transport)), "anthropic": types.SimpleNamespace(Anthropic=creator, DefaultHttpxClient=mock.Mock(return_value=transport))}
                client = mock.Mock(endpoint="https://gateway.example")
                client.create_job.return_value = {"work_id": "work-created"}
                job = client.job.return_value
                job.work_id, job.headers = "work-created", {"X-Hormuz-Work-Id": "work-created"}
                job.state.return_value = {"state": "active", "attempts": []}
                captured = io.StringIO()
                with mock.patch.dict("sys.modules", modules), mock.patch.object(WorkClient, "from_environment", return_value=client), mock.patch.dict(example.os.environ, {"HORMUZ_TOKEN": "private-fixture-credential"}), contextlib.redirect_stdout(captured):
                    self.assertEqual(example.main(["--api", api, "--model", "approved-alias", "--stream", "--execute"]), 1)
                receipt = json.loads(captured.getvalue())
                self.assertEqual(receipt["work_id"], "work-created")
                self.assertEqual(receipt["failure_type"], "ValueError")
                self.assertNotIn("private-fixture", captured.getvalue())
                response.close.assert_called_once()
                sdk.__exit__.assert_called_once()
                transport.close.assert_called_once()
