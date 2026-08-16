from __future__ import annotations

import io
import json
import unittest
import urllib.error
from unittest import mock

from hormuz.context_lifecycle_client import (
    ContextLifecycleClient,
    ContextLifecycleClientError,
)


EVIDENCE = {
    "schema_version": "hormuz.context-evidence.v1",
    "organization_id": "xpounder",
    "record_id": "retry",
    "record_version": 1,
    "signal": "ci_passed",
    "evidence_ref": "ci:private:123",
    "observed_at": "2026-08-16T12:00:00Z",
}


class FakeResponse:
    def __init__(self, *, body: bytes, url: str, status: int = 200):
        self.body = body
        self.url = url
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


class ContextLifecycleClientTests(unittest.TestCase):
    def test_gateway_and_timeout_validation_fail_with_stable_client_errors(self) -> None:
        cases = (
            ("http://hormuz.example", False, 10, "gateway_requires_https"),
            ("https://user@hormuz.example", False, 10, "invalid_gateway_url"),
            ("https://hormuz.example/base", False, 10, "invalid_gateway_url"),
            ("https://hormuz.example", False, 0, "invalid_timeout"),
            ("https://hormuz.example", False, float("nan"), "invalid_timeout"),
        )
        for gateway, allow_http, timeout, expected in cases:
            with self.subTest(gateway=gateway, timeout=timeout):
                with self.assertRaises(ContextLifecycleClientError) as caught:
                    ContextLifecycleClient(
                        gateway,
                        credential="credential",
                        allow_insecure_http=allow_http,
                        timeout_seconds=timeout,
                    )
                self.assertEqual(caught.exception.code, expected)

    def test_local_schema_validation_happens_before_network_work(self) -> None:
        client = ContextLifecycleClient(
            "https://hormuz.example",
            credential="credential",
        )
        client._opener = mock.Mock()

        with self.assertRaises(ContextLifecycleClientError) as caught:
            client.record_evidence({**EVIDENCE, "unknown": True})

        self.assertEqual(caught.exception.code, "invalid_context_evidence")
        client._opener.open.assert_not_called()

    def test_gateway_error_code_is_preserved_without_reflecting_error_message(self) -> None:
        client = ContextLifecycleClient(
            "https://hormuz.example",
            credential="credential",
        )
        body = json.dumps(
            {
                "error": {
                    "code": "context_lifecycle_conflict",
                    "message": "untrusted remote detail",
                }
            }
        ).encode("utf-8")
        error = urllib.error.HTTPError(
            "https://hormuz.example/v1/context/evidence",
            409,
            "Conflict",
            {},
            io.BytesIO(body),
        )
        client._opener = mock.Mock()
        client._opener.open.side_effect = error

        with self.assertRaises(ContextLifecycleClientError) as caught:
            client.record_evidence(EVIDENCE)

        self.assertEqual(caught.exception.code, "context_lifecycle_conflict")
        self.assertNotIn("untrusted remote detail", str(caught.exception))

    def test_cross_origin_response_and_non_strict_json_are_rejected(self) -> None:
        client = ContextLifecycleClient(
            "https://hormuz.example",
            credential="credential",
        )
        client._opener = mock.Mock()
        client._opener.open.return_value = FakeResponse(
            body=b"{}",
            url="https://attacker.example/v1/context/evidence",
        )
        with self.assertRaises(ContextLifecycleClientError) as caught:
            client.record_evidence(EVIDENCE)
        self.assertEqual(caught.exception.code, "unexpected_gateway_redirect")

        client._opener.open.return_value = FakeResponse(
            body=b'{"schema_version":"one","schema_version":"two"}',
            url="https://hormuz.example/v1/context/evidence",
        )
        with self.assertRaises(ContextLifecycleClientError) as caught:
            client.record_evidence(EVIDENCE)
        self.assertEqual(caught.exception.code, "invalid_gateway_response")

    def test_success_response_requires_the_exact_metadata_only_shape(self) -> None:
        client = ContextLifecycleClient(
            "https://hormuz.example",
            credential="credential",
        )
        response = {
            "schema_version": "hormuz.context-evidence-result.v1",
            "created": True,
            "evidence_id": "ctxev_123",
            "organization_id": "xpounder",
            "record_id": "retry",
            "record_version": 1,
            "signal": "ci_passed",
            "signal_family": "ci",
            "observed_at": "2026-08-16T12:00:00Z",
            "policy_version": "lifecycle-v1",
            "raw_evidence_ref_retained": False,
            "evidence_ref": "must-not-be-accepted",
        }
        client._opener = mock.Mock()
        client._opener.open.return_value = FakeResponse(
            body=json.dumps(response).encode("utf-8"),
            url="https://hormuz.example/v1/context/evidence",
        )

        with self.assertRaises(ContextLifecycleClientError) as caught:
            client.record_evidence(EVIDENCE)

        self.assertEqual(caught.exception.code, "invalid_gateway_response")

    def test_request_and_response_size_limits_fail_before_unbounded_processing(self) -> None:
        client = ContextLifecycleClient(
            "https://hormuz.example",
            credential="credential",
        )
        client._opener = mock.Mock()
        with self.assertRaises(ContextLifecycleClientError) as caught:
            client._request(
                "POST",
                "/v1/context/evidence",
                {"value": "larger-than-two-bytes"},
                max_request_bytes=2,
            )
        self.assertEqual(caught.exception.code, "context_request_too_large")
        client._opener.open.assert_not_called()

        client._opener.open.return_value = FakeResponse(
            body=b"x" * ((256 * 1024) + 1),
            url="https://hormuz.example/v1/context/evidence",
        )
        with self.assertRaises(ContextLifecycleClientError) as caught:
            client.record_evidence(EVIDENCE)
        self.assertEqual(caught.exception.code, "gateway_response_too_large")


if __name__ == "__main__":
    unittest.main()
