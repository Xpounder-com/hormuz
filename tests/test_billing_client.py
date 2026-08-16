from __future__ import annotations

import io
import json
import unittest
import urllib.error
import urllib.parse
from datetime import date, datetime, timezone

from hormuz.billing_client import ProviderBillingClient, ProviderBillingClientError


class FakeResponse:
    def __init__(self, body: bytes, url: str, status: int = 200):
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


class QueueOpener:
    def __init__(self, bodies: list[bytes]):
        self.bodies = list(bodies)
        self.requests = []

    def open(self, request, timeout: float):
        self.requests.append((request, timeout))
        if not self.bodies:
            raise AssertionError("unexpected provider request")
        return FakeResponse(self.bodies.pop(0), request.full_url)


def _openai_page(*, start: int, has_more: bool, next_page: str | None) -> bytes:
    return json.dumps(
        {
            "object": "page",
            "data": [
                {
                    "object": "bucket",
                    "start_time": start,
                    "end_time": start + 86_400,
                    "results": [
                        {
                            "object": "organization.costs.result",
                            "amount": {"value": 1.25, "currency": "usd"},
                            "line_item": "Responses API",
                            "project_id": "proj_alpha",
                        }
                    ],
                }
            ],
            "has_more": has_more,
            "next_page": next_page,
        }
    ).encode("utf-8")


def _anthropic_page(*, has_more: bool = False, next_page: str | None = None) -> bytes:
    return json.dumps(
        {
            "data": [
                {
                    "starting_at": "2026-08-01T00:00:00Z",
                    "ending_at": "2026-08-02T00:00:00Z",
                    "results": [
                        {
                            "amount": "123.45",
                            "currency": "USD",
                            "workspace_id": "wrkspc_alpha",
                            "description": "Claude cache read tokens",
                            "cost_type": "tokens",
                            "model": "claude-sonnet-4-6",
                            "service_tier": "standard",
                            "token_type": "cache_read_input_tokens",
                            "context_window": "0-200k",
                            "inference_geo": "global",
                        }
                    ],
                }
            ],
            "has_more": has_more,
            "next_page": next_page,
        }
    ).encode("utf-8")


class ProviderBillingClientTests(unittest.TestCase):
    def test_openai_fetch_uses_fixed_cost_endpoint_and_follows_cursor(self) -> None:
        start = datetime(2026, 8, 1, tzinfo=timezone.utc)
        first = int(start.timestamp())
        opener = QueueOpener(
            [
                _openai_page(start=first, has_more=True, next_page="cursor-2"),
                _openai_page(start=first + 86_400, has_more=False, next_page=None),
            ]
        )
        secret = "openai-admin-secret-must-not-persist"
        client = ProviderBillingClient("openai", credential=secret, opener=opener)

        fetched = client.fetch(start=date(2026, 8, 1), end=date(2026, 8, 3))

        self.assertEqual(fetched.report.provider, "openai")
        self.assertEqual(fetched.report.page_count, 2)
        self.assertEqual(fetched.source.kind, "authenticated_api")
        self.assertEqual(fetched.source.api_contract, "openai.organization.costs.v1")
        self.assertEqual(
            fetched.source.query_scope,
            "organization_all_projects_line_items",
        )
        self.assertNotIn(secret, repr(fetched))
        self.assertEqual(len(opener.requests), 2)
        for index, (request, timeout) in enumerate(opener.requests):
            parsed = urllib.parse.urlparse(request.full_url)
            query = urllib.parse.parse_qs(parsed.query)
            self.assertEqual((parsed.scheme, parsed.netloc, parsed.path), (
                "https",
                "api.openai.com",
                "/v1/organization/costs",
            ))
            self.assertEqual(query["start_time"], [str(first)])
            self.assertEqual(query["end_time"], [str(first + (2 * 86_400))])
            self.assertEqual(query["bucket_width"], ["1d"])
            self.assertEqual(query["limit"], ["180"])
            self.assertEqual(query["group_by"], ["project_id", "line_item"])
            self.assertEqual(query.get("page"), None if index == 0 else ["cursor-2"])
            self.assertEqual(request.get_header("Authorization"), "Bearer " + secret)
            self.assertIsNone(request.get_header("X-api-key"))
            self.assertNotIn(secret, request.full_url)
            self.assertEqual(timeout, 30)

    def test_anthropic_fetch_uses_admin_contract_and_detailed_grouping(self) -> None:
        opener = QueueOpener([_anthropic_page()])
        secret = "anthropic-admin-secret-must-not-persist"
        client = ProviderBillingClient("anthropic", credential=secret, opener=opener)

        fetched = client.fetch(start=date(2026, 8, 1), end=date(2026, 8, 2))

        request, _ = opener.requests[0]
        parsed = urllib.parse.urlparse(request.full_url)
        query = urllib.parse.parse_qs(parsed.query)
        self.assertEqual((parsed.scheme, parsed.netloc, parsed.path), (
            "https",
            "api.anthropic.com",
            "/v1/organizations/cost_report",
        ))
        self.assertEqual(query["starting_at"], ["2026-08-01T00:00:00Z"])
        self.assertEqual(query["ending_at"], ["2026-08-02T00:00:00Z"])
        self.assertEqual(query["bucket_width"], ["1d"])
        self.assertEqual(query["limit"], ["31"])
        self.assertEqual(query["group_by[]"], ["workspace_id", "description"])
        self.assertEqual(request.get_header("X-api-key"), secret)
        self.assertEqual(request.get_header("Anthropic-version"), "2023-06-01")
        self.assertIsNone(request.get_header("Authorization"))
        self.assertEqual(fetched.source.api_contract, "anthropic.organization.cost_report.2023-06-01")
        self.assertEqual(fetched.report.items[0].amount_usd, "1.2345")
        self.assertNotIn(secret, repr(fetched))

    def test_empty_authenticated_window_is_a_valid_zero_cost_report(self) -> None:
        opener = QueueOpener(
            [
                json.dumps(
                    {
                        "object": "page",
                        "data": [],
                        "has_more": False,
                        "next_page": None,
                    }
                ).encode("utf-8")
            ]
        )
        fetched = ProviderBillingClient(
            "openai",
            credential="admin-key",
            opener=opener,
        ).fetch(start=date(2026, 8, 1), end=date(2026, 8, 2))

        self.assertEqual(fetched.report.bucket_count, 0)
        self.assertEqual(fetched.report.items, ())
        self.assertEqual(fetched.report.report_start, "2026-08-01T00:00:00+00:00")
        self.assertEqual(fetched.report.report_end, "2026-08-02T00:00:00+00:00")

    def test_invalid_window_and_credential_fail_before_network_work(self) -> None:
        opener = QueueOpener([])
        for credential in ("bad\ncredential", "bad\tcredential", " bad-credential"):
            with self.subTest(credential=repr(credential)):
                with self.assertRaisesRegex(ProviderBillingClientError, "invalid_credential"):
                    ProviderBillingClient("openai", credential=credential, opener=opener)
        client = ProviderBillingClient("openai", credential="admin-key", opener=opener)
        for start, end in (
            (date(2026, 8, 2), date(2026, 8, 2)),
            (date(2026, 8, 2), date(2026, 8, 1)),
            (date(2025, 1, 1), date(2026, 8, 2)),
        ):
            with self.subTest(start=start, end=end):
                with self.assertRaisesRegex(ProviderBillingClientError, "invalid_billing_window"):
                    client.fetch(start=start, end=end)
        self.assertEqual(opener.requests, [])

    def test_redirects_oversize_non_strict_json_and_repeated_cursor_fail_closed(self) -> None:
        client = ProviderBillingClient("openai", credential="admin-key")
        client._opener = QueueOpener([b"{}"])
        client._opener.open = lambda request, timeout: FakeResponse(  # type: ignore[method-assign]
            b"{}",
            "https://attacker.example/capture",
        )
        with self.assertRaisesRegex(ProviderBillingClientError, "unexpected_provider_redirect"):
            client.fetch(start=date(2026, 8, 1), end=date(2026, 8, 2))

        client._opener = QueueOpener([b"x" * ((16 * 1024 * 1024) + 1)])
        with self.assertRaisesRegex(ProviderBillingClientError, "provider_response_too_large"):
            client.fetch(start=date(2026, 8, 1), end=date(2026, 8, 2))

        client._opener = QueueOpener(
            [b'{"object":"page","data":[],"data":[],"has_more":false,"next_page":null}']
        )
        with self.assertRaisesRegex(ProviderBillingClientError, "invalid_provider_response"):
            client.fetch(start=date(2026, 8, 1), end=date(2026, 8, 2))

        repeated = _openai_page(
            start=int(datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp()),
            has_more=True,
            next_page="same-cursor",
        )
        client._opener = QueueOpener([repeated, repeated])
        with self.assertRaisesRegex(ProviderBillingClientError, "repeated_provider_cursor"):
            client.fetch(start=date(2026, 8, 1), end=date(2026, 8, 3))

    def test_provider_errors_do_not_reflect_remote_body_or_credential(self) -> None:
        secret = "admin-secret-must-not-escape"
        error = urllib.error.HTTPError(
            "https://api.openai.com/v1/organization/costs",
            401,
            "Unauthorized " + secret,
            {},
            io.BytesIO(("remote body " + secret).encode("utf-8")),
        )
        client = ProviderBillingClient("openai", credential=secret)
        client._opener = QueueOpener([])
        client._opener.open = lambda request, timeout: (_ for _ in ()).throw(error)  # type: ignore[method-assign]

        with self.assertRaises(ProviderBillingClientError) as caught:
            client.fetch(start=date(2026, 8, 1), end=date(2026, 8, 2))

        self.assertEqual(caught.exception.code, "provider_authentication_failed")
        self.assertNotIn(secret, str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)

    def test_retryable_status_uses_bounded_retry_and_nonretryable_status_does_not(self) -> None:
        first = int(datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp())
        retry = urllib.error.HTTPError(
            "https://api.openai.com/v1/organization/costs",
            429,
            "Rate limited",
            {"Retry-After": "0"},
            io.BytesIO(b"ignored"),
        )
        opener = QueueOpener([])
        responses = iter(
            [
                retry,
                FakeResponse(
                    _openai_page(start=first, has_more=False, next_page=None),
                    "https://api.openai.com/v1/organization/costs",
                ),
            ]
        )

        def open_next(request, timeout):
            opener.requests.append((request, timeout))
            value = next(responses)
            if isinstance(value, Exception):
                raise value
            return value

        opener.open = open_next  # type: ignore[method-assign]
        delays: list[float] = []
        result = ProviderBillingClient(
            "openai",
            credential="admin-key",
            opener=opener,
            sleeper=delays.append,
        ).fetch(start=date(2026, 8, 1), end=date(2026, 8, 2))
        self.assertEqual(result.report.page_count, 1)
        self.assertEqual(delays, [0.0])
        self.assertEqual(len(opener.requests), 2)

        rejection = urllib.error.HTTPError(
            "https://api.openai.com/v1/organization/costs",
            400,
            "Bad request",
            {},
            io.BytesIO(b"ignored"),
        )
        opener = QueueOpener([])
        opener.open = lambda request, timeout: (_ for _ in ()).throw(rejection)  # type: ignore[method-assign]
        with self.assertRaises(ProviderBillingClientError) as caught:
            ProviderBillingClient(
                "openai",
                credential="admin-key",
                opener=opener,
                sleeper=lambda _delay: self.fail("nonretryable response was retried"),
            ).fetch(start=date(2026, 8, 1), end=date(2026, 8, 2))
        self.assertEqual(caught.exception.code, "provider_request_rejected")


if __name__ == "__main__":
    unittest.main()
