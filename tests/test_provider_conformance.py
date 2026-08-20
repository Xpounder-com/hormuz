from __future__ import annotations

import http.client
import io
import json
import os
import stat
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from hormuz.provider_conformance import (
    ProviderConformanceClient,
    ProviderConformanceError,
    write_conformance_result,
)
from hormuz.cli import build_parser, main


MARKER = "HORMUZ_OK_1"


def _openai_body(*, marker: str = MARKER) -> bytes:
    return json.dumps(
        {
            "id": "resp_secret_provider_identifier",
            "object": "response",
            "status": "completed",
            "model": "gpt-5.6-luna-2026-07-01",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": marker}],
                }
            ],
            "usage": {
                "input_tokens": 14,
                "output_tokens": 6,
                "total_tokens": 20,
                "input_tokens_details": {"cached_tokens": 2},
                "output_tokens_details": {"reasoning_tokens": 1},
            },
        }
    ).encode("utf-8")


def _anthropic_body(*, marker: str = MARKER) -> bytes:
    return json.dumps(
        {
            "id": "msg_secret_provider_identifier",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-5-20260801",
            "content": [{"type": "text", "text": marker}],
            "stop_reason": "end_turn",
            "usage": {
                "input_tokens": 12,
                "output_tokens": 5,
                "cache_read_input_tokens": 3,
                "cache_creation_input_tokens": 2,
            },
        }
    ).encode("utf-8")


class FakeResponse:
    def __init__(
        self,
        body: bytes,
        *,
        url: str,
        headers: dict[str, str],
        status: int = 200,
    ) -> None:
        self.body = body
        self.url = url
        self.headers = headers
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        return None

    def getcode(self) -> int:
        return self.status

    def geturl(self) -> str:
        return self.url

    def read(self, amount: int) -> bytes:
        return self.body[:amount]


class QueueOpener:
    def __init__(self, responses: list[FakeResponse | Exception]) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[object, float]] = []

    def open(self, request, timeout: float):
        self.requests.append((request, timeout))
        if not self.responses:
            raise AssertionError("unexpected conformance request")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _headers(*, requested: str, routed: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-Hormuz-Policy-Decision": "allowed+capped",
        "X-Hormuz-Requested-Model": requested,
        "X-Hormuz-Routed-Model": routed,
        "X-Request-Id": "provider-request-id-must-not-be-retained",
    }


class ProviderConformanceClientTests(unittest.TestCase):
    def test_openai_probe_uses_gateway_credential_and_emits_content_free_evidence(self) -> None:
        secret = "employee-gateway-secret-must-not-persist"
        endpoint = "http://127.0.0.1:8787/v1/responses"
        opener = QueueOpener(
            [
                FakeResponse(
                    _openai_body(),
                    url=endpoint,
                    headers=_headers(
                        requested="openai-live",
                        routed="gpt-5.6-luna",
                    ),
                )
            ]
        )
        result = ProviderConformanceClient(
            "openai",
            gateway="http://127.0.0.1:8787",
            credential=secret,
            allow_insecure_http=True,
            opener=opener,
            clock=lambda: 100.0,
        ).run(model="openai-live", max_output_tokens=16)

        request, timeout = opener.requests[0]
        body = json.loads(request.data)
        self.assertEqual(request.full_url, endpoint)
        self.assertEqual(request.get_header("Authorization"), "Bearer " + secret)
        self.assertIsNone(request.get_header("X-api-key"))
        self.assertEqual(body["model"], "openai-live")
        self.assertEqual(body["max_output_tokens"], 16)
        self.assertIs(body["store"], False)
        self.assertNotIn(secret, request.full_url)
        self.assertEqual(timeout, 30)

        self.assertEqual(result["schema_version"], "hormuz.provider-conformance.v1")
        self.assertEqual(result["provider"], "openai")
        self.assertEqual(result["interface"], "POST /v1/responses")
        self.assertEqual(result["requested_model"], "openai-live")
        self.assertEqual(result["routed_model"], "gpt-5.6-luna")
        self.assertEqual(result["actual_model"], "gpt-5.6-luna-2026-07-01")
        self.assertEqual(result["usage"]["input_tokens"], 14)
        self.assertEqual(result["usage"]["cache_read_tokens"], 2)
        self.assertEqual(result["usage"]["reasoning_tokens"], 1)
        self.assertTrue(result["assurances"]["marker_verified"])
        serialized = json.dumps(result, sort_keys=True)
        for forbidden in (
            secret,
            MARKER,
            "Reply with exactly",
            "resp_secret_provider_identifier",
            "provider-request-id-must-not-be-retained",
            "127.0.0.1",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_anthropic_probe_uses_messages_contract_and_normalizes_usage(self) -> None:
        endpoint = "https://hormuz.example/v1/messages"
        opener = QueueOpener(
            [
                FakeResponse(
                    _anthropic_body(),
                    url=endpoint,
                    headers=_headers(
                        requested="anthropic-live",
                        routed="claude-sonnet-5",
                    ),
                )
            ]
        )
        result = ProviderConformanceClient(
            "anthropic",
            gateway="https://hormuz.example/v1",
            credential="employee-token",
            opener=opener,
            clock=lambda: 25.0,
        ).run(model="anthropic-live", max_output_tokens=16)

        request, _ = opener.requests[0]
        body = json.loads(request.data)
        self.assertEqual(request.full_url, endpoint)
        self.assertEqual(request.get_header("X-api-key"), "employee-token")
        self.assertEqual(request.get_header("Anthropic-version"), "2023-06-01")
        self.assertIsNone(request.get_header("Authorization"))
        self.assertEqual(body["max_tokens"], 16)
        self.assertEqual(result["provider"], "anthropic")
        self.assertEqual(result["usage"]["input_tokens"], 12)
        self.assertEqual(result["usage"]["output_tokens"], 5)
        self.assertEqual(result["usage"]["cache_read_tokens"], 3)
        self.assertEqual(result["usage"]["cache_write_tokens"], 2)

    def test_bad_marker_invalid_usage_and_unsafe_gateway_response_fail_closed(self) -> None:
        cases = (
            (_openai_body(marker="WRONG"), _headers(requested="live", routed="gpt-5.6-luna"), "marker_mismatch"),
            (
                json.dumps(
                    {
                        "object": "response",
                        "status": "completed",
                        "model": "gpt-5.6-luna",
                        "output": [{"type": "message", "content": [{"type": "output_text", "text": MARKER}]}],
                    }
                ).encode(),
                _headers(requested="live", routed="gpt-5.6-luna"),
                "missing_provider_usage",
            ),
            (
                _openai_body(),
                {
                    **_headers(requested="live", routed="gpt-5.6-luna"),
                    "Content-Type": "text/html",
                },
                "invalid_gateway_response",
            ),
            (_openai_body(), _headers(requested="other", routed="gpt-5.6-luna"), "gateway_policy_mismatch"),
        )
        for body, headers, code in cases:
            with self.subTest(code=code):
                endpoint = "https://hormuz.example/v1/responses"
                client = ProviderConformanceClient(
                    "openai",
                    gateway="https://hormuz.example",
                    credential="employee-token",
                    opener=QueueOpener([FakeResponse(body, url=endpoint, headers=headers)]),
                )
                with self.assertRaises(ProviderConformanceError) as caught:
                    client.run(model="live")
                self.assertEqual(caught.exception.code, code)
                self.assertNotIn("WRONG", str(caught.exception))

    def test_redirect_oversize_timeout_and_remote_error_never_reflect_content(self) -> None:
        endpoint = "https://hormuz.example/v1/responses"
        secret = "employee-secret-must-not-escape"
        remote = "remote-body-must-not-escape"
        errors: tuple[tuple[object, str], ...] = (
            (FakeResponse(_openai_body(), url="https://attacker.example/capture", headers=_headers(requested="live", routed="gpt-5.6-luna")), "unexpected_gateway_redirect"),
            (FakeResponse(b"x" * ((1024 * 1024) + 1), url=endpoint, headers=_headers(requested="live", routed="gpt-5.6-luna")), "gateway_response_too_large"),
            (TimeoutError(remote), "gateway_unavailable"),
            (http.client.IncompleteRead((remote + secret).encode(), 100), "gateway_unavailable"),
            (
                urllib.error.HTTPError(
                    endpoint,
                    403,
                    remote,
                    {},
                    io.BytesIO((remote + secret).encode()),
                ),
                "gateway_request_rejected",
            ),
        )
        for response, code in errors:
            with self.subTest(code=code):
                client = ProviderConformanceClient(
                    "openai",
                    gateway="https://hormuz.example",
                    credential=secret,
                    opener=QueueOpener([response]),
                )
                with self.assertRaises(ProviderConformanceError) as caught:
                    client.run(model="live")
                self.assertEqual(caught.exception.code, code)
                self.assertNotIn(remote, str(caught.exception))
                self.assertNotIn(secret, str(caught.exception))
                self.assertIsNone(caught.exception.__cause__)

    def test_invalid_inputs_fail_before_network_work(self) -> None:
        invalid = (
            {"provider": "other"},
            {"gateway": "http://hormuz.example"},
            {"gateway": "https://user:secret@hormuz.example"},
            {"gateway": "https://hormuz.example/path"},
            {"credential": "bad\ncredential"},
            {"timeout_seconds": 0},
        )
        for override in invalid:
            with self.subTest(override=override):
                values = {
                    "provider": "openai",
                    "gateway": "https://hormuz.example",
                    "credential": "employee-token",
                    "opener": QueueOpener([]),
                }
                values.update(override)
                with self.assertRaises(ProviderConformanceError):
                    ProviderConformanceClient(**values)

        opener = QueueOpener([])
        client = ProviderConformanceClient(
            "openai",
            gateway="https://hormuz.example",
            credential="employee-token",
            opener=opener,
        )
        for model, maximum in (("bad\nmodel", 16), ("live", 0), ("live", 65)):
            with self.subTest(model=model, maximum=maximum):
                with self.assertRaises(ProviderConformanceError):
                    client.run(model=model, max_output_tokens=maximum)
        self.assertEqual(opener.requests, [])

    def test_evidence_file_is_private_exclusive_and_removed_after_write_failure(self) -> None:
        value = {"schema_version": "hormuz.provider-conformance.v1", "status": "verified"}
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "evidence.json"
            write_conformance_result(value, str(output))
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            with self.assertRaisesRegex(ProviderConformanceError, "evidence_output_exists"):
                write_conformance_result(value, str(output))

            failed = Path(temporary) / "failed.json"
            with mock.patch("os.fsync", side_effect=OSError("disk marker")):
                with self.assertRaisesRegex(ProviderConformanceError, "evidence_write_failed"):
                    write_conformance_result(value, str(failed))
            self.assertFalse(failed.exists())

    def test_checked_in_live_evidence_uses_the_exact_content_free_schema(self) -> None:
        root = Path(__file__).resolve().parents[1]
        value = json.loads(
            (root / "evidence/provider-conformance-openai-2026-08-19.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            set(value),
            {
                "actual_model",
                "assurances",
                "gateway_transport",
                "generated_at",
                "http_status",
                "interface",
                "latency_milliseconds",
                "policy_decision",
                "probe_version",
                "provider",
                "requested_model",
                "routed_model",
                "runner",
                "schema_version",
                "status",
                "usage",
            },
        )
        self.assertEqual(
            set(value["usage"]),
            {
                "billable_tokens",
                "cache_read_tokens",
                "cache_write_tokens",
                "input_tokens",
                "output_tokens",
                "reasoning_tokens",
            },
        )
        self.assertEqual(
            set(value["assurances"]),
            {
                "credential_retained",
                "fixed_content_probe",
                "gateway_policy_headers_verified",
                "gateway_url_retained",
                "marker_verified",
                "prompt_retained",
                "provider_request_id_retained",
                "provider_usage_verified",
                "response_content_retained",
            },
        )
        self.assertEqual(value["status"], "verified")
        self.assertEqual(value["provider"], "openai")
        self.assertNotIn(MARKER, json.dumps(value))

        client_value = json.loads(
            (root / "evidence/codex-openai-live-2026-08-19.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            set(client_value),
            {
                "actual_model",
                "assurances",
                "client",
                "client_exit_code",
                "generated_at",
                "gateway_interface",
                "latency",
                "policy_decision",
                "provider",
                "requested_model",
                "routed_model",
                "schema_version",
                "status",
                "usage",
            },
        )
        self.assertEqual(
            set(client_value["assurances"]),
            {
                "client_marker_verified",
                "client_output_retained",
                "employee_credential_retained",
                "gateway_policy_verified",
                "gateway_url_retained",
                "prompt_retained",
                "provider_credential_removed_from_client_environment",
                "provider_credential_retained",
                "provider_response_retained",
            },
        )
        self.assertEqual(client_value["client"], {"name": "OpenAI Codex CLI", "version": "0.147.0"})
        self.assertEqual(client_value["client_exit_code"], 0)
        self.assertEqual(client_value["status"], "verified")
        self.assertNotIn("CODEX_GATEWAY_OK", json.dumps(client_value))


class ProviderConformanceCLITests(unittest.TestCase):
    def test_parser_exposes_bounded_opt_in_command(self) -> None:
        args = build_parser().parse_args(
            [
                "provider-conformance",
                "--provider",
                "openai",
                "--gateway",
                "http://127.0.0.1:8787",
                "--model",
                "openai-live",
                "--allow-insecure-http",
            ]
        )
        self.assertEqual(args.command, "provider-conformance")
        self.assertEqual(args.max_output_tokens, 16)
        self.assertEqual(args.credential_env, "HORMUZ_TOKEN")

    def test_cli_uses_named_gateway_credential_and_writes_verified_result(self) -> None:
        verified = {
            "schema_version": "hormuz.provider-conformance.v1",
            "status": "verified",
        }
        client = mock.Mock()
        client.run.return_value = verified
        with (
            mock.patch.dict(os.environ, {"TEST_HORMUZ_TOKEN": "employee-token"}),
            mock.patch("hormuz.cli.ProviderConformanceClient", return_value=client) as constructor,
            mock.patch("hormuz.cli.write_conformance_result") as writer,
        ):
            result = main(
                [
                    "provider-conformance",
                    "--provider",
                    "anthropic",
                    "--gateway",
                    "https://hormuz.example",
                    "--model",
                    "anthropic-live",
                    "--credential-env",
                    "TEST_HORMUZ_TOKEN",
                    "--output",
                    "evidence.json",
                ]
            )
        self.assertEqual(result, 0)
        constructor.assert_called_once_with(
            "anthropic",
            gateway="https://hormuz.example",
            credential="employee-token",
            timeout_seconds=30,
            allow_insecure_http=False,
        )
        client.run.assert_called_once_with(model="anthropic-live", max_output_tokens=16)
        writer.assert_called_once_with(verified, "evidence.json", force=False)

    def test_cli_missing_credential_is_content_free_and_does_not_load_config(self) -> None:
        error = io.StringIO()
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch("hormuz.cli.GatewayConfig.load") as load,
            redirect_stderr(error),
        ):
            result = main(
                [
                    "provider-conformance",
                    "--provider",
                    "openai",
                    "--gateway",
                    "https://hormuz.example",
                    "--model",
                    "openai-live",
                ]
            )
        self.assertEqual(result, 1)
        self.assertEqual(error.getvalue(), "provider conformance failed: credential_not_set\n")
        load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
