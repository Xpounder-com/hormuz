"""Provider-free runtime proofs for strict finance collection and persistence."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import replace
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock
from urllib.error import HTTPError

from hormuz.finance_collection import (
    CollectionQuery,
    FinanceCollectionError,
    MAX_PAGE_BYTES,
    fetch_collection_pages,
    normalize_collection_file,
    normalize_collection_pages,
    tenant_fingerprint,
    validate_normalized_collection,
)
from hormuz.finance_collection_repository import create_finance_collection_repository
from hormuz.config import UsageStorageConfig
from hormuz.portfolio_config import PortfolioPrincipal, PortfolioRoleBinding
from hormuz.store import UsageStore

if __package__:
    from ._sqlite import managed_sqlite_connection
    from ._portfolio_fixture import registry_config
else:
    from _sqlite import managed_sqlite_connection
    from _portfolio_fixture import registry_config


KEY = b"synthetic-finance-fingerprint-key"
ADMIN = PortfolioPrincipal("acme", "alice", ("portfolio_admin",))
START = "2026-01-01T00:00:00Z"
MIDDLE = "2026-01-02T00:00:00Z"
END = "2026-01-03T00:00:00Z"


def query(
    profile: str,
    *,
    start: str = START,
    end: str = MIDDLE,
    page_size: int = 1,
    binding_id: str = "provider-account",
    binding_version: int = 1,
) -> CollectionQuery:
    return CollectionQuery(
        "acme",
        binding_id,
        binding_version,
        profile,
        start,
        end,
        "1d",
        page_size,
    )


def openai_page(buckets, *, has_more=False, next_page=None) -> bytes:
    return json.dumps(
        {
            "object": "page",
            "data": buckets,
            "has_more": has_more,
            "next_page": next_page,
        },
        separators=(",", ":"),
    ).encode()


def anthropic_page(buckets, *, has_more=False, next_page=None) -> bytes:
    return json.dumps(
        {"data": buckets, "has_more": has_more, "next_page": next_page},
        separators=(",", ":"),
    ).encode()


def openai_bucket(start: str, end: str, records) -> dict[str, object]:
    return {
        "object": "bucket",
        "start_time": int(datetime.fromisoformat(start.replace("Z", "+00:00")).timestamp()),
        "end_time": int(datetime.fromisoformat(end.replace("Z", "+00:00")).timestamp()),
        "results": records,
    }


def anthropic_bucket(start: str, end: str, records) -> dict[str, object]:
    return {"starting_at": start, "ending_at": end, "results": records}


def openai_usage(**changes) -> dict[str, object]:
    return {
        "object": "organization.usage.completions.result",
        "input_tokens": 11,
        "output_tokens": 7,
        "num_model_requests": 2,
        "input_cached_tokens": 3,
        "project_id": "project-sensitive",
        "user_id": "person-sensitive",
        "api_key_id": "key-sensitive",
        "model": "gpt-5.1",
        "batch": False,
        "service_tier": "default",
        **changes,
    }


def openai_cost(**changes) -> dict[str, object]:
    return {
        "object": "organization.costs.result",
        "amount": {"value": 1.25, "currency": "usd"},
        "line_item": "sensitive-openai-line-item",
        "project_id": "project-sensitive",
        "api_key_id": "key-sensitive",
        "quantity": 1250,
        "quantity_unit": "tokens",
        **changes,
    }


def openai_cost_metadata_bucket(**changes) -> dict[str, object]:
    bucket = openai_bucket(START, MIDDLE, [openai_cost(
        organization_id="raw-provider-account",
        organization_name="private-organization-label",
        project_name="private-project-label",
        user_email=None,
        user_id=None,
        api_source=None,
        **changes,
    )])
    bucket.update(start_time_iso=START, end_time_iso=MIDDLE)
    return bucket


def anthropic_usage(**changes) -> dict[str, object]:
    return {
        "account_id": "person-account-sensitive",
        "service_account_id": "service-account-sensitive",
        "workspace_id": "workspace-sensitive",
        "api_key_id": "key-sensitive",
        "model": "claude-sonnet-4-5",
        "service_tier": "standard",
        "context_window": "0-200k",
        "inference_geo": "us",
        "speed": None,
        "uncached_input_tokens": 13,
        "cache_read_input_tokens": 5,
        "cache_creation": {
            "ephemeral_5m_input_tokens": 2,
            "ephemeral_1h_input_tokens": 1,
        },
        "output_tokens": 8,
        "server_tool_use": {"web_search_requests": 1},
        **changes,
    }


def anthropic_cost(**changes) -> dict[str, object]:
    return {
        "amount": "123.45",
        "currency": "USD",
        "description": "Claude Usage - Input Tokens",
        "workspace_id": "workspace-sensitive",
        "model": "claude-sonnet-4-5",
        "cost_type": "tokens",
        "token_type": "input",
        "service_tier": "standard",
        "context_window": "0-200k",
        "inference_geo": "us",
        **changes,
    }


def normalized_usage(
    value: CollectionQuery,
    *,
    records_by_day: tuple[tuple[dict[str, object], ...], ...] | None = None,
):
    intervals = ((START, MIDDLE),) if value.query_end_at == MIDDLE else ((START, MIDDLE), (MIDDLE, END))
    records_by_day = records_by_day or tuple((openai_usage(),) for _ in intervals)
    page = openai_page(
        [
            openai_bucket(start, end, list(records))
            for (start, end), records in zip(intervals, records_by_day, strict=True)
        ]
    )
    return normalize_collection_pages(
        value,
        (page,),
        fingerprint_key=KEY,
        fingerprint_key_version=1,
    )


class FinanceCollectionNormalizationTests(unittest.TestCase):
    def test_openai_cost_quantity_nullability_and_identity_preserve_native_values(self):
        value = query("openai.organization-costs.v1")

        def normalize(records):
            result = normalize_collection_pages(
                value, (openai_page([openai_bucket(START, MIDDLE, records)]),),
                fingerprint_key=KEY, fingerprint_key_version=1,
            )
            validate_normalized_collection(result)
            return result

        explicit_null = normalize([openai_cost(quantity=1, quantity_unit=None)])
        omitted = openai_cost(quantity=1)
        del omitted["quantity_unit"]
        self.assertEqual(normalize([omitted]).content_digest, explicit_null.content_digest)
        self.assertEqual(explicit_null.cost_observations[0].native_quantity, "1")
        self.assertIsNone(explicit_null.cost_observations[0].quantity_unit)
        changed_quantity = normalize([openai_cost(quantity=2, quantity_unit=None)])
        changed_unit = normalize([openai_cost(quantity=1, quantity_unit="1000_tokens")])
        self.assertEqual(len({item.content_digest for item in (explicit_null, changed_quantity, changed_unit)}), 3)
        self.assertEqual(len({item.cost_observations[0].observation_digest for item in
                              (explicit_null, changed_quantity, changed_unit)}), 3)
        # Quantity is a measured value; it must not create an implicit new
        # grouping dimension that permits two rows for the same semantic key.
        with self.assertRaisesRegex(FinanceCollectionError, "^provider_response_invalid$"):
            normalize([openai_cost(quantity=1, quantity_unit=None), openai_cost(quantity=2, quantity_unit=None)])
        for result in (explicit_null, changed_quantity, changed_unit):
            self.assertFalse(result.cost_observations[0].provider_final)
            self.assertFalse(result.cost_observations[0].invoice_final)

    def test_anthropic_cost_quantity_fields_remain_unsupported(self):
        value = query("anthropic.organization-costs.v1")
        baseline = normalize_collection_pages(
            value, (anthropic_page([anthropic_bucket(START, MIDDLE, [anthropic_cost()])]),),
            fingerprint_key=KEY, fingerprint_key_version=1,
        )
        validate_normalized_collection(baseline)
        self.assertIsNone(baseline.cost_observations[0].native_quantity)
        self.assertIsNone(baseline.cost_observations[0].quantity_unit)
        for fields in ({"quantity": 1, "quantity_unit": None}, {"quantity_unit": "duration_seconds"}):
            with self.subTest(fields=fields):
                with self.assertRaisesRegex(FinanceCollectionError, "^provider_response_invalid$"):
                    normalize_collection_pages(
                        value, (anthropic_page([anthropic_bucket(START, MIDDLE, [anthropic_cost(**fields)])]),),
                        fingerprint_key=KEY, fingerprint_key_version=1,
                    )

    def normalize_cost_metadata(self, bucket, *, account="raw-provider-account"):
        expected = None if account is None else tenant_fingerprint(
            KEY, organization_id="acme", kind="provider-account", value=account,
        )
        return normalize_collection_pages(
            query("openai.organization-costs.v1"), (openai_page([bucket]),),
            fingerprint_key=KEY, fingerprint_key_version=1,
            expected_provider_account_fingerprint=expected,
        )

    def test_cost_verified_metadata_preserves_exact_amount_and_content_identity(self):
        baseline = normalize_collection_pages(
            query("openai.organization-costs.v1"),
            (openai_page([openai_bucket(START, MIDDLE, [openai_cost()])]),),
            fingerprint_key=KEY, fingerprint_key_version=1,
        )
        for suffix in ("Z", "+00:00"):
            with self.subTest(suffix=suffix):
                bucket = openai_cost_metadata_bucket()
                bucket.update(start_time_iso=START[:-1] + suffix, end_time_iso=MIDDLE[:-1] + suffix)
                observed = self.normalize_cost_metadata(bucket)
                validate_normalized_collection(observed)
                self.assertEqual(observed, baseline)
                for private in ("raw-provider-account", "private-organization-label", "private-project-label"):
                    self.assertNotIn(private, repr(observed))
                self.assertFalse(observed.cost_observations[0].provider_final)
                self.assertFalse(observed.cost_observations[0].invoice_final)

    def test_cost_iso_aliases_reject_mismatch_non_utc_and_malformed_types(self):
        for name, value in (("start_time_iso", MIDDLE), ("end_time_iso", START),
                            ("start_time_iso", "2026-01-01T00:00:00+01:00"),
                            ("end_time_iso", None), ("start_time_iso", 1767225600),
                            ("start_time_iso", "2026-01-01T00:00:00.000001Z"),
                            ("start_time_iso", "2026-01-01T00:00:00.0000001Z"),
                            ("start_time_iso", "2026-01-01T00:00:00-00:00"),
                            ("start_time_iso", "2026-01-01T00:00:00")):
            with self.subTest(name=name, value=value):
                bucket = openai_cost_metadata_bucket()
                bucket[name] = value
                with self.assertRaisesRegex(FinanceCollectionError, "^provider_response_invalid$"):
                    self.normalize_cost_metadata(bucket)

    def test_cost_account_context_cannot_cross_tenants_or_fingerprint_keys(self):
        for key, tenant in ((KEY, "other-tenant"), (b"another-private-fingerprint-key-value", "acme")):
            with self.subTest(tenant=tenant):
                expected = tenant_fingerprint(key, organization_id=tenant, kind="provider-account",
                                              value="raw-provider-account")
                with self.assertRaisesRegex(FinanceCollectionError, "^provider_response_invalid$"):
                    normalize_collection_pages(
                        query("openai.organization-costs.v1"), (openai_page([openai_cost_metadata_bucket()]),),
                        fingerprint_key=KEY, fingerprint_key_version=1,
                        expected_provider_account_fingerprint=expected,
                    )

    def test_cost_null_account_metadata_preserves_legacy_context_optional_behavior(self):
        bucket = openai_cost_metadata_bucket()
        bucket["results"][0]["organization_id"] = None
        self.normalize_cost_metadata(bucket, account=None)

    def test_cost_account_metadata_requires_matching_context_and_string(self):
        for account in (None, "another-provider-account", "acme"):
            with self.subTest(account=account):
                with self.assertRaisesRegex(FinanceCollectionError, "^provider_response_invalid$"):
                    self.normalize_cost_metadata(openai_cost_metadata_bucket(), account=account)
        for value in (True, 17, [], {}, "bad\x00account", "a" * 2049):
            with self.subTest(type=type(value).__name__):
                bucket = openai_cost_metadata_bucket()
                bucket["results"][0]["organization_id"] = value
                with self.assertRaisesRegex(FinanceCollectionError, "^provider_response_invalid$"):
                    self.normalize_cost_metadata(bucket)

    def test_cost_optional_labels_validate_types_bounds_and_discard_values(self):
        for field in ("organization_name", "project_name"):
            for value in (None, "", "a" * 2048):
                with self.subTest(field=field, accepted=value is None):
                    bucket = openai_cost_metadata_bucket()
                    bucket["results"][0][field] = value
                    self.normalize_cost_metadata(bucket)
            for value in (True, 3, [], {}, "private\x00label", "a" * 2049, "é" * 1025):
                with self.subTest(field=field, type=type(value).__name__):
                    bucket = openai_cost_metadata_bucket()
                    bucket["results"][0][field] = value
                    with self.assertRaisesRegex(FinanceCollectionError, "^provider_response_invalid$"):
                        self.normalize_cost_metadata(bucket)

    def test_cost_nonnull_user_email_and_unknown_metadata_fail_closed(self):
        for field, value in (("user_email", "private-person@example.invalid"),
                             ("user_email", True), ("unknown_metadata", None)):
            with self.subTest(field=field):
                bucket = openai_cost_metadata_bucket()
                bucket["results"][0][field] = value
                with self.assertRaisesRegex(FinanceCollectionError, "^provider_response_invalid$") as caught:
                    self.normalize_cost_metadata(bucket)
                self.assertNotIn("private-person", str(caught.exception))
        bucket = openai_cost_metadata_bucket()
        bucket["unexpected_bucket_metadata"] = None
        with self.assertRaisesRegex(FinanceCollectionError, "^provider_response_invalid$"):
            self.normalize_cost_metadata(bucket)

    def test_cost_alias_compatibility_does_not_relax_usage_bucket_schema(self):
        bucket = openai_bucket(START, MIDDLE, [openai_usage()])
        bucket.update(start_time_iso=START, end_time_iso=MIDDLE)
        with self.assertRaisesRegex(FinanceCollectionError, "^provider_response_invalid$"):
            normalize_collection_pages(
                query("openai.organization-usage-completions.v1"), (openai_page([bucket]),),
                fingerprint_key=KEY, fingerprint_key_version=1,
            )

    def test_content_free_fingerprints_reject_invalid_unicode(self):
        with self.assertRaisesRegex(FinanceCollectionError, "invalid_request"):
            tenant_fingerprint(
                KEY,
                organization_id="acme",
                kind="workspace",
                value="\ud800",
            )

    def test_all_four_profiles_normalize_typed_private_exact_evidence(self):
        cases = (
            (
                query("openai.organization-usage-completions.v1"),
                openai_page([openai_bucket(START, MIDDLE, [openai_usage()])]),
                "usage",
            ),
            (
                query("openai.organization-costs.v1"),
                openai_page([openai_bucket(START, MIDDLE, [openai_cost()])]),
                "cost",
            ),
            (
                query("anthropic.organization-usage-messages.v1"),
                anthropic_page([anthropic_bucket(START, MIDDLE, [anthropic_usage()])]),
                "usage",
            ),
            (
                query("anthropic.organization-costs.v1"),
                anthropic_page([anthropic_bucket(START, MIDDLE, [anthropic_cost()])]),
                "cost",
            ),
        )
        for value, page, kind in cases:
            with self.subTest(profile=value.collection_profile):
                result = normalize_collection_pages(
                    value,
                    (page,),
                    fingerprint_key=KEY,
                    fingerprint_key_version=1,
                )
                validate_normalized_collection(result)
                self.assertEqual(result.record_count, 1)
                self.assertEqual(result.coverage[0].coverage_state, "observed")
                self.assertEqual(len(result.usage_observations), kind == "usage")
                self.assertEqual(len(result.cost_observations), kind == "cost")
                rendered = repr(result)
                for raw in (
                    "project-sensitive",
                    "workspace-sensitive",
                    "key-sensitive",
                    "person-sensitive",
                    "service-account-sensitive",
                    "sensitive-openai-line-item",
                ):
                    self.assertNotIn(raw, rendered)
        openai_result = normalize_collection_pages(
            cases[1][0], (cases[1][1],), fingerprint_key=KEY, fingerprint_key_version=1
        ).cost_observations[0]
        self.assertEqual((openai_result.native_amount, openai_result.canonical_amount), ("1.25", "1.25"))
        self.assertEqual(openai_result.currency, "USD")
        anthropic_result = normalize_collection_pages(
            cases[3][0], (cases[3][1],), fingerprint_key=KEY, fingerprint_key_version=1
        ).cost_observations[0]
        self.assertEqual((anthropic_result.native_amount, anthropic_result.canonical_amount), ("123.45", "1.2345"))
        self.assertFalse(anthropic_result.provider_final)
        self.assertFalse(anthropic_result.invoice_final)

    def test_content_identity_ignores_page_mechanics_but_page_chain_does_not(self):
        one = query("openai.organization-usage-completions.v1", end=END, page_size=1)
        two = query("openai.organization-usage-completions.v1", end=END, page_size=2)
        records = [openai_usage()]
        paged = normalize_collection_pages(
            one,
            (
                openai_page([openai_bucket(START, MIDDLE, records)], has_more=True, next_page="opaque-secret-cursor"),
                openai_page([openai_bucket(MIDDLE, END, records)]),
            ),
            fingerprint_key=KEY,
            fingerprint_key_version=1,
        )
        combined = normalize_collection_pages(
            two,
            (openai_page([openai_bucket(START, MIDDLE, records), openai_bucket(MIDDLE, END, records)]),),
            fingerprint_key=KEY,
            fingerprint_key_version=1,
        )
        self.assertEqual(paged.content_digest, combined.content_digest)
        self.assertNotEqual(paged.page_chain_digest, combined.page_chain_digest)
        self.assertNotIn("opaque-secret-cursor", repr(paged))

    def test_openai_cost_ungrouped_nullable_metadata_preserves_identity(self):
        value = query("openai.organization-costs.v1")
        baseline = normalize_collection_pages(
            value,
            (openai_page([openai_bucket(START, MIDDLE, [openai_cost()])]),),
            fingerprint_key=KEY,
            fingerprint_key_version=1,
        )
        self.assertEqual(value.profile.group_by, ("project_id", "line_item", "api_key_id"))
        for metadata in ({"user_id": None}, {"api_source": None}, {"user_id": None, "api_source": None}):
            with self.subTest(metadata=metadata):
                result = normalize_collection_pages(
                    value,
                    (openai_page([openai_bucket(START, MIDDLE, [openai_cost(**metadata)])]),),
                    fingerprint_key=KEY,
                    fingerprint_key_version=1,
                )
                validate_normalized_collection(result)
                self.assertEqual(result.cost_observations, baseline.cost_observations)
                self.assertEqual(result.coverage, baseline.coverage)
                self.assertEqual(result.content_digest, baseline.content_digest)
                self.assertEqual(result.page_chain_digest, baseline.page_chain_digest)
                self.assertEqual(result.record_count, 1)
                self.assertFalse(result.cost_observations[0].provider_final)
                self.assertFalse(result.cost_observations[0].invoice_final)

    def test_openai_cost_ungrouped_metadata_and_unknown_fields_fail_closed(self):
        value = query("openai.organization-costs.v1")
        for field in ("user_id", "api_source", "unknown_metadata"):
            for item in (None, "private-metadata-sentinel", "api", True, 0, [], {}):
                if field != "unknown_metadata" and item is None:
                    continue
                with self.subTest(field=field, item=item):
                    with self.assertRaisesRegex(FinanceCollectionError, "^provider_response_invalid$") as caught:
                        normalize_collection_pages(
                            value,
                            (openai_page([openai_bucket(START, MIDDLE, [openai_cost(**{field: item})])]),),
                            fingerprint_key=KEY,
                            fingerprint_key_version=1,
                        )
                    self.assertNotIn("private-metadata-sentinel", str(caught.exception))

    def test_empty_bucket_is_coverage_not_numeric_zero(self):
        value = query("openai.organization-usage-completions.v1")
        result = normalize_collection_pages(
            value,
            (openai_page([openai_bucket(START, MIDDLE, [])]),),
            fingerprint_key=KEY,
            fingerprint_key_version=1,
        )
        self.assertEqual(result.record_count, 0)
        self.assertEqual(result.coverage[0].coverage_state, "no_observation")
        self.assertEqual(result.coverage[0].observation_count, 0)
        self.assertEqual(result.usage_observations, ())

    def test_numeric_pagination_and_json_boundaries_fail_closed(self):
        value = query("openai.organization-usage-completions.v1")
        failures = (
            b'{"object":"page","object":"page","data":[],"has_more":false,"next_page":null}',
            openai_page([openai_bucket(START, MIDDLE, [openai_usage(input_tokens=True)])]),
            openai_page([openai_bucket(START, MIDDLE, [openai_usage(input_tokens=9223372036854775808)])]),
            openai_page([openai_bucket(START, MIDDLE, [openai_usage(), openai_usage(input_tokens=99)])]),
        )
        for payload in failures:
            with self.subTest(payload=payload[:40]):
                with self.assertRaises(FinanceCollectionError):
                    normalize_collection_pages(
                        value,
                        (payload,),
                        fingerprint_key=KEY,
                        fingerprint_key_version=1,
                    )
        with self.assertRaisesRegex(FinanceCollectionError, "pagination_invalid"):
            normalize_collection_pages(
                query("openai.organization-usage-completions.v1", end=END),
                (
                    openai_page([openai_bucket(START, MIDDLE, [openai_usage()])], has_more=True, next_page="same"),
                    openai_page([openai_bucket(MIDDLE, END, [openai_usage()])], has_more=True, next_page="same"),
                    openai_page([]),
                ),
                fingerprint_key=KEY,
                fingerprint_key_version=1,
            )
        cost_query = query("openai.organization-costs.v1")
        for amount in (True, 1e18, 0.0000000000000000001):
            with self.subTest(amount=amount):
                record = openai_cost(amount={"value": amount, "currency": "USD"})
                with self.assertRaises(FinanceCollectionError):
                    normalize_collection_pages(
                        cost_query,
                        (openai_page([openai_bucket(START, MIDDLE, [record])]),),
                        fingerprint_key=KEY,
                        fingerprint_key_version=1,
                    )

    def test_file_bundle_uses_identical_normalization_and_fingerprints_are_tenant_keyed(self):
        value = query("anthropic.organization-costs.v1")
        page = json.loads(
            anthropic_page([anthropic_bucket(START, MIDDLE, [anthropic_cost()])])
        )
        payload = json.dumps(
            {
                "schema_id": "hormuz.finance-collection-file-bundle",
                "schema_version": 1,
                "collection_profile": value.collection_profile,
                "query_start_at": value.query_start_at,
                "query_end_at": value.query_end_at,
                "bucket_width": value.bucket_width,
                "requested_page_size": value.requested_page_size,
                "pages": [page],
            },
            separators=(",", ":"),
        ).encode()
        imported = normalize_collection_file(
            value,
            payload,
            fingerprint_key=KEY,
            fingerprint_key_version=1,
        )
        direct = normalize_collection_pages(
            value,
            (anthropic_page([anthropic_bucket(START, MIDDLE, [anthropic_cost()])]),),
            fingerprint_key=KEY,
            fingerprint_key_version=1,
        )
        self.assertEqual(imported, direct)
        self.assertNotEqual(
            tenant_fingerprint(KEY, organization_id="acme", kind="workspace", value="same"),
            tenant_fingerprint(KEY, organization_id="beta", kind="workspace", value="same"),
        )


class _Response:
    def __init__(self, payload: bytes, url: str, *, status: int = 200):
        self.payload = payload
        self.url = url
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def getcode(self):
        return self.status

    def geturl(self):
        return self.url

    def read(self, maximum):
        return self.payload[:maximum]


class _Opener:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        if callable(outcome):
            return outcome(request)
        return outcome


class FinanceCollectionTransportTests(unittest.TestCase):
    def test_nonretryable_http_error_closes_response_before_failure(self):
        value = query("openai.organization-costs.v1")
        for status, code in ((401, "provider_unauthorized"), (403, "provider_unauthorized"), (400, "provider_response_invalid")):
            with self.subTest(status=status), io.BytesIO(b"synthetic error body") as body:
                error = HTTPError("https://api.openai.com", status, "synthetic", {}, body)
                opener = _Opener([error])
                with self.assertRaisesRegex(FinanceCollectionError, code):
                    fetch_collection_pages(value, credential="synthetic", base_url="https://api.openai.com",
                        opener=opener, clock=lambda: 0, sleep=lambda _delay: self.fail("nonretryable error slept"))
                self.assertTrue(body.closed)
                self.assertEqual(len(opener.requests), 1)

    def test_retryable_http_error_closes_response_before_sleep_and_next_attempt(self):
        value = query("openai.organization-costs.v1")
        payload = openai_page([openai_bucket(START, MIDDLE, [openai_cost()])])
        for status in (429, 503):
            with self.subTest(status=status), io.BytesIO(b"synthetic error body") as body:
                error = HTTPError("https://api.openai.com", status, "synthetic", {"Retry-After": "3"}, body)
                sleeps = []

                def next_response(request):
                    self.assertTrue(body.closed)
                    return _Response(payload, request.full_url)

                opener = _Opener([error, next_response])

                def sleep(delay):
                    self.assertTrue(body.closed)
                    self.assertEqual(len(opener.requests), 1)
                    sleeps.append(delay)

                self.assertEqual(fetch_collection_pages(value, credential="synthetic", base_url="https://api.openai.com",
                    opener=opener, clock=lambda: 0, sleep=sleep), (payload,))
                self.assertEqual(sleeps, [3.0])
                self.assertEqual(len(opener.requests), 2)

    def test_exhausted_http_errors_close_every_response_and_preserve_retry_bound(self):
        value = query("openai.organization-costs.v1")
        for status, code in ((429, "provider_rate_limited"), (503, "provider_unavailable")):
            with self.subTest(status=status), ExitStack() as cleanup:
                bodies = [cleanup.enter_context(io.BytesIO(b"synthetic error body")) for _ in range(3)]
                errors = [HTTPError("https://api.openai.com", status, "synthetic", {}, body) for body in bodies]
                opener = _Opener(errors)
                sleeps = []

                def sleep(delay):
                    self.assertTrue(all(body.closed for body in bodies[:len(opener.requests)]))
                    self.assertLess(len(opener.requests), 3)
                    sleeps.append(delay)

                with self.assertRaisesRegex(FinanceCollectionError, code):
                    fetch_collection_pages(value, credential="synthetic", base_url="https://api.openai.com",
                        opener=opener, clock=lambda: 0, sleep=sleep)
                self.assertTrue(all(body.closed for body in bodies))
                self.assertEqual(sleeps, [1.0, 2.0])
                self.assertEqual(len(opener.requests), 3)

    def test_http_error_close_failure_preserves_public_error(self):
        value = query("openai.organization-costs.v1")
        with io.BytesIO(b"synthetic error body") as body:
            error = HTTPError("https://api.openai.com", 403, "synthetic", {}, body)

            def close():
                body.close()
                raise OSError("synthetic close failure")

            with mock.patch.object(error, "close", side_effect=close) as close_error:
                with self.assertRaisesRegex(FinanceCollectionError, "provider_unauthorized"):
                    fetch_collection_pages(value, credential="synthetic", base_url="https://api.openai.com",
                        opener=_Opener([error]), clock=lambda: 0, sleep=lambda _delay: self.fail("unauthorized error slept"))
                close_error.assert_called_once_with()
                self.assertTrue(body.closed)

        payload = openai_page([openai_bucket(START, MIDDLE, [openai_cost()])])
        with io.BytesIO(b"synthetic error body") as body:
            headers = {"Retry-After": "3"}
            error = HTTPError("https://api.openai.com", 429, "synthetic", headers, body)
            sleeps = []

            def close():
                headers.clear()
                body.close()
                raise OSError("synthetic close failure")

            def sleep(delay):
                self.assertTrue(body.closed)
                sleeps.append(delay)

            opener = _Opener([error, lambda request: _Response(payload, request.full_url)])
            with mock.patch.object(error, "close", side_effect=close) as close_error:
                self.assertEqual(fetch_collection_pages(value, credential="synthetic", base_url="https://api.openai.com",
                    opener=opener, clock=lambda: 0, sleep=sleep), (payload,))
                close_error.assert_called_once_with()
                self.assertEqual(sleeps, [3.0])
                self.assertEqual(len(opener.requests), 2)

    def test_query_rejects_page_chain_that_cannot_fit_deadline_bound(self):
        with self.assertRaisesRegex(FinanceCollectionError, "invalid_request"):
            CollectionQuery(
                "acme",
                "provider-account",
                1,
                "openai.organization-usage-completions.v1",
                START,
                MIDDLE,
                "1m",
                7,
            )

    def test_response_reading_cannot_extend_past_collection_deadline(self):
        value = query("openai.organization-usage-completions.v1")
        payload = openai_page([openai_bucket(START, MIDDLE, [openai_usage()])])
        opener = _Opener([lambda request: _Response(payload, request.full_url)])
        clock_values = iter((0.0, 0.0, 59.0, 60.0))
        with self.assertRaisesRegex(FinanceCollectionError, "collection_deadline"):
            fetch_collection_pages(
                value,
                credential="secret",
                base_url="https://api.openai.com",
                opener=opener,
                clock=lambda: next(clock_values),
            )
        self.assertEqual(opener.requests[0][1], 1.0)

    def test_fixed_tls_endpoint_headers_and_complete_pagination(self):
        value = query("openai.organization-usage-completions.v1", end=END)
        first = openai_page(
            [openai_bucket(START, MIDDLE, [openai_usage()])],
            has_more=True,
            next_page="opaque",
        )
        second = openai_page([openai_bucket(MIDDLE, END, [openai_usage()])])
        opener = _Opener(
            [
                lambda request: _Response(first, request.full_url),
                lambda request: _Response(second, request.full_url),
            ]
        )
        pages = fetch_collection_pages(
            value,
            credential="provider-secret",
            base_url="https://api.openai.com",
            opener=opener,
        )
        self.assertEqual(pages, (first, second))
        self.assertEqual(len(opener.requests), 2)
        self.assertEqual(opener.requests[0][0].get_header("Authorization"), "Bearer provider-secret")
        self.assertIn("page=opaque", opener.requests[1][0].full_url)
        self.assertTrue(opener.requests[0][0].full_url.startswith("https://api.openai.com/v1/organization/usage/completions?"))
        with self.assertRaisesRegex(FinanceCollectionError, "invalid_request"):
            fetch_collection_pages(
                value,
                credential="provider-secret",
                base_url="http://api.openai.com",
                opener=_Opener([]),
            )

    def test_anthropic_uses_admin_key_header_without_bearer(self):
        value = query("anthropic.organization-costs.v1")
        payload = anthropic_page([anthropic_bucket(START, MIDDLE, [anthropic_cost()])])
        opener = _Opener([lambda request: _Response(payload, request.full_url)])
        fetch_collection_pages(
            value,
            credential="anthropic-secret",
            base_url="https://api.anthropic.com",
            opener=opener,
        )
        request = opener.requests[0][0]
        self.assertEqual(request.get_header("X-api-key"), "anthropic-secret")
        self.assertEqual(request.get_header("Anthropic-version"), "2023-06-01")
        self.assertIsNone(request.get_header("Authorization"))

    def test_redirect_oversize_retry_and_cursor_cycles_fail_closed(self):
        value = query("openai.organization-usage-completions.v1")
        payload = openai_page([openai_bucket(START, MIDDLE, [openai_usage()])])
        redirected = _Opener([_Response(payload, "https://redirect.invalid")])
        with self.assertRaisesRegex(FinanceCollectionError, "provider_response_invalid"):
            fetch_collection_pages(
                value,
                credential="secret",
                base_url="https://api.openai.com",
                opener=redirected,
            )
        oversized = _Opener(
            [lambda request: _Response(b"x" * (MAX_PAGE_BYTES + 1), request.full_url)]
        )
        with self.assertRaisesRegex(FinanceCollectionError, "provider_response_too_large"):
            fetch_collection_pages(
                value,
                credential="secret",
                base_url="https://api.openai.com",
                opener=oversized,
            )
        retry = _Opener(
            [
                HTTPError("https://api.openai.com", 429, "limited", {"Retry-After": "nan"}, None),
                lambda request: _Response(payload, request.full_url),
            ]
        )
        sleeps = []
        self.assertEqual(
            fetch_collection_pages(
                value,
                credential="secret",
                base_url="https://api.openai.com",
                opener=retry,
                sleep=sleeps.append,
            ),
            (payload,),
        )
        self.assertEqual(sleeps, [1.0])
        cycle_query = query("openai.organization-usage-completions.v1", end=END)
        cycle = _Opener(
            [
                lambda request: _Response(
                    openai_page(
                        [openai_bucket(START, MIDDLE, [openai_usage()])],
                        has_more=True,
                        next_page="same",
                    ),
                    request.full_url,
                ),
                lambda request: _Response(
                    openai_page(
                        [openai_bucket(MIDDLE, END, [openai_usage()])],
                        has_more=True,
                        next_page="same",
                    ),
                    request.full_url,
                ),
            ]
        )
        with self.assertRaisesRegex(FinanceCollectionError, "pagination_invalid"):
            fetch_collection_pages(
                cycle_query,
                credential="secret",
                base_url="https://api.openai.com",
                opener=cycle,
            )
        self.assertEqual(len(cycle.requests), 2)


class FinanceCollectionSQLiteRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = registry_config(self.root)
        self.store = UsageStore(self.config.database_path)
        self.repository = create_finance_collection_repository(self.config)

    def binding_request(self, **changes):
        return {
            "schema_id": "hormuz.finance-source-binding-request",
            "schema_version": 1,
            "binding_id": "provider-account",
            "expected_version": None,
            "provider": "openai",
            "provider_account_reference_id": "raw-provider-account",
            "scope": {"kind": "organization", "ids": []},
            "credential_reference_version": 1,
            "fingerprint_key_version": 1,
            "state": "active",
            "reason_code": "created",
            **changes,
        }

    def bind(self, **changes):
        return self.repository.bind_source(
            ADMIN,
            self.binding_request(**changes),
            fingerprint_key=KEY,
        )

    def test_prepared_account_context_is_reloaded_and_spoofing_blocks_publication(self):
        binding = self.bind()
        value = query("openai.organization-costs.v1")
        prepared = self.repository.prepare_collection(
            ADMIN, value, idempotency_key="bound-cost-account", evidence_origin="authenticated_api",
        )
        self.assertEqual(prepared.provider_account_fingerprint, binding.provider_account_fingerprint)
        restarted = create_finance_collection_repository(self.config)
        collection = normalize_collection_pages(
            value, (openai_page([openai_cost_metadata_bucket()]),),
            fingerprint_key=KEY, fingerprint_key_version=1,
            expected_provider_account_fingerprint=prepared.provider_account_fingerprint,
        )
        forged = replace(prepared, provider_account_fingerprint="0" * 64)
        with self.assertRaisesRegex(FinanceCollectionError, "^attempt_conflict$"):
            restarted.publish_collection(ADMIN, forged, collection)
        wrong_fingerprint = tenant_fingerprint(
            KEY, organization_id="acme", kind="provider-account", value="wrong-provider-account",
        )
        wrong_bucket = openai_cost_metadata_bucket()
        wrong_bucket["results"][0]["organization_id"] = "wrong-provider-account"
        wrong_collection = normalize_collection_pages(
            value, (openai_page([wrong_bucket]),), fingerprint_key=KEY, fingerprint_key_version=1,
            expected_provider_account_fingerprint=wrong_fingerprint,
        )
        with self.assertRaisesRegex(FinanceCollectionError, "^snapshot_conflict$"):
            restarted.publish_collection(ADMIN, prepared, wrong_collection)
        self.assertIsNotNone(restarted.publish_collection(ADMIN, prepared, collection).snapshot_id)
        replay = restarted.prepare_collection(
            ADMIN, value, idempotency_key="bound-cost-account", evidence_origin="authenticated_api",
        )
        self.assertEqual(replay.state, "succeeded")
        self.assertEqual(replay.provider_account_fingerprint, binding.provider_account_fingerprint)

    def test_fixed_precision_whole_second_storage_time_is_valid(self):
        with unittest.mock.patch(
            "hormuz._portfolio_sql.PortfolioSQL.now",
            return_value="2026-09-08T16:23:35.000000Z",
        ):
            binding = self.bind()
        self.assertEqual(binding.bound_at, "2026-09-08T16:23:35.000000Z")

    def test_binding_request_rejects_boolean_schema_version(self):
        with self.assertRaisesRegex(FinanceCollectionError, "invalid_request"):
            self.bind(schema_version=True)

    def test_disabled_postgres_collection_runtime_is_gated_before_connection(self):
        config = replace(
            self.config,
            usage_storage=UsageStorageConfig(backend="postgresql"),
        )
        repository = create_finance_collection_repository(
            config, environ={"HORMUZ_POSTGRES_DSN": "postgresql://redacted"}
        )
        with unittest.mock.patch(
            "hormuz.finance_collection_repository.POSTGRES_FINANCE_COLLECTION_RUNTIME_ENABLED",
            False,
        ), unittest.mock.patch(
            "hormuz.finance_collection_repository.portfolio_transaction",
            side_effect=AssertionError("postgres collection must remain gated"),
        ):
            with self.assertRaisesRegex(FinanceCollectionError, "unavailable"):
                repository.bind_source(
                    ADMIN, self.binding_request(), fingerprint_key=KEY
                )

    def test_binding_attempt_snapshot_receipt_and_chain_are_restart_safe(self):
        binding = self.bind()
        self.assertEqual(self.bind(), binding)
        value = query("openai.organization-usage-completions.v1")
        prepared = self.repository.prepare_collection(
            ADMIN, value, idempotency_key="stable", evidence_origin="customer_file"
        )
        receipt = self.repository.publish_collection(
            ADMIN, prepared, normalized_usage(value)
        )
        restarted = create_finance_collection_repository(self.config)
        completed = restarted.prepare_collection(
            ADMIN, value, idempotency_key="stable", evidence_origin="customer_file"
        )
        self.assertEqual(completed.state, "succeeded")
        self.assertEqual(restarted.receipt_for_prepared(ADMIN, completed), receipt)
        view = restarted.current_observations(
            ADMIN,
            binding_id=binding.binding_id,
            binding_version=1,
            collection_profile=value.collection_profile,
            start_at=START,
            end_at=MIDDLE,
        )
        self.assertEqual(len(view.coverage), 1)
        self.assertEqual(len(view.observations), 1)
        self.assertIs(view.observations[0]["batch"], False)
        self.assertIs(view.observations[0]["provider_final"], False)
        self.assertEqual(
            UsageStore(self.config.database_path).verify_audit_chain(
                organization_id="acme"
            ).sequence,
            3,
        )

    def test_authority_precedes_storage_and_is_rechecked_before_commit(self):
        viewer = PortfolioPrincipal("acme", "finance", ("finance_viewer",))
        with unittest.mock.patch(
            "hormuz.finance_collection_repository.portfolio_transaction",
            side_effect=AssertionError("must not connect"),
        ):
            with self.assertRaisesRegex(FinanceCollectionError, "forbidden"):
                self.repository.bind_source(
                    viewer, self.binding_request(), fingerprint_key=KEY
                )
        binding = self.bind()
        value = query("openai.organization-usage-completions.v1", binding_id=binding.binding_id)
        prepared = self.repository.prepare_collection(
            ADMIN, value, idempotency_key="revoked-role", evidence_origin="customer_file"
        )
        control = self.config.portfolio_control
        assert control is not None
        changed_roles = tuple(
            replace(item, roles=("platform_viewer",))
            if item.actor_id == "alice"
            else item
            for item in control.role_bindings
        )
        self.repository.config = replace(
            self.config,
            portfolio_control=replace(control, role_bindings=changed_roles),
        )
        with self.assertRaisesRegex(FinanceCollectionError, "forbidden"):
            self.repository.publish_collection(ADMIN, prepared, normalized_usage(value))
        with managed_sqlite_connection(self.config.database_path) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM portfolio_finance_snapshots"
                ).fetchone()[0],
                0,
            )

    def test_failure_rows_allow_null_receipts_and_all_collection_rows_are_append_only(self):
        self.bind()
        value = query("openai.organization-usage-completions.v1")
        for index in range(2):
            prepared = self.repository.prepare_collection(
                ADMIN,
                value,
                idempotency_key=f"failure-{index}",
                evidence_origin="customer_file",
            )
            self.repository.fail_collection(
                ADMIN, prepared, reason_code="normalization_failed"
            )
        with managed_sqlite_connection(self.config.database_path) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM portfolio_finance_collection_events WHERE receipt_id IS NULL"
                ).fetchone()[0],
                2,
            )
            before = connection.execute(
                "SELECT evidence_json FROM portfolio_finance_source_binding_versions"
            ).fetchone()[0]
            for statement in (
                "UPDATE portfolio_finance_source_binding_versions SET bound_by='changed'",
                "DELETE FROM portfolio_finance_source_binding_versions",
                "INSERT OR REPLACE INTO portfolio_finance_source_binding_versions SELECT * FROM portfolio_finance_source_binding_versions",
            ):
                with self.subTest(statement=statement):
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(statement)
            self.assertEqual(
                connection.execute(
                    "SELECT evidence_json FROM portfolio_finance_source_binding_versions"
                ).fetchone()[0],
                before,
            )

    def test_refresh_selection_uses_newest_exact_coverage_and_empty_suppresses_stale(self):
        self.bind()
        initial_query = query("openai.organization-usage-completions.v1", end=END)
        initial = self.repository.publish_collection(
            ADMIN,
            self.repository.prepare_collection(
                ADMIN, initial_query, idempotency_key="initial", evidence_origin="customer_file"
            ),
            normalized_usage(initial_query),
        )
        refresh_query = query(
            "openai.organization-usage-completions.v1", start=MIDDLE, end=END
        )
        empty_page = openai_page([openai_bucket(MIDDLE, END, [])])
        empty = normalize_collection_pages(
            refresh_query,
            (empty_page,),
            fingerprint_key=KEY,
            fingerprint_key_version=1,
        )
        refresh = self.repository.publish_collection(
            ADMIN,
            self.repository.prepare_collection(
                ADMIN, refresh_query, idempotency_key="refresh", evidence_origin="customer_file"
            ),
            empty,
        )
        self.assertIsNone(refresh.supersedes_snapshot_id)
        view = self.repository.current_observations(
            ADMIN,
            binding_id="provider-account",
            binding_version=1,
            collection_profile=initial_query.collection_profile,
            start_at=START,
            end_at=END,
        )
        self.assertEqual(
            [item["coverage_state"] for item in view.coverage],
            ["observed", "no_observation"],
        )
        self.assertEqual(len(view.observations), 1)
        exact_retry = self.repository.publish_collection(
            ADMIN,
            self.repository.prepare_collection(
                ADMIN,
                refresh_query,
                idempotency_key="refresh-again",
                evidence_origin="customer_file",
            ),
            empty,
        )
        self.assertEqual(exact_retry.supersedes_snapshot_id, refresh.snapshot_id)
        self.assertNotEqual(initial.snapshot_id, refresh.snapshot_id)

    def test_as_of_selection_replays_tenant_high_water_after_empty_refresh(self):
        self.bind()
        usage_query = query("openai.organization-usage-completions.v1", end=END)
        empty_before_publish = self.repository.observations_as_of(
            ADMIN, binding_id=usage_query.binding_id, binding_version=1,
            collection_profile=usage_query.collection_profile, start_at=START, end_at=END,
        )
        self.assertEqual(empty_before_publish.as_of_commit_sequence, 0)
        self.assertEqual(empty_before_publish.selected_snapshots, ())
        self.assertEqual(empty_before_publish.coverage, ())
        self.assertEqual(empty_before_publish.observations, ())

        initial = self.repository.publish_collection(
            ADMIN,
            self.repository.prepare_collection(
                ADMIN, usage_query, idempotency_key="as-of-initial",
                evidence_origin="customer_file",
            ),
            normalized_usage(usage_query),
        )
        first = self.repository.observations_as_of(
            ADMIN, binding_id=usage_query.binding_id, binding_version=1,
            collection_profile=usage_query.collection_profile, start_at=START, end_at=END,
        )
        self.assertEqual(first.as_of_commit_sequence, initial.commit_sequence)
        self.assertEqual(
            [(item.snapshot_id, item.content_digest, item.commit_sequence)
             for item in first.selected_snapshots],
            [(initial.snapshot_id, initial.content_digest, initial.commit_sequence)],
        )
        self.assertEqual(len(first.coverage), 2)
        self.assertEqual(len(first.observations), 2)

        # The cutoff belongs to the tenant, not to this binding/profile/window.
        cost_query = query("openai.organization-costs.v1")
        cost_collection = normalize_collection_pages(
            cost_query,
            (openai_page([openai_bucket(START, MIDDLE, [openai_cost()])]),),
            fingerprint_key=KEY, fingerprint_key_version=1,
        )
        cost = self.repository.publish_collection(
            ADMIN,
            self.repository.prepare_collection(
                ADMIN, cost_query, idempotency_key="as-of-cost",
                evidence_origin="customer_file",
            ),
            cost_collection,
        )
        at_cost = self.repository.observations_as_of(
            ADMIN, binding_id=usage_query.binding_id, binding_version=1,
            collection_profile=usage_query.collection_profile, start_at=START, end_at=END,
        )
        self.assertEqual(at_cost.as_of_commit_sequence, cost.commit_sequence)
        self.assertEqual(at_cost.selected_snapshots, first.selected_snapshots)
        self.assertEqual(at_cost.coverage, first.coverage)
        self.assertEqual(at_cost.observations, first.observations)

        refresh_query = query(
            usage_query.collection_profile, start=MIDDLE, end=END,
        )
        empty = normalize_collection_pages(
            refresh_query,
            (openai_page([openai_bucket(MIDDLE, END, [])]),),
            fingerprint_key=KEY, fingerprint_key_version=1,
        )
        refreshed = self.repository.publish_collection(
            ADMIN,
            self.repository.prepare_collection(
                ADMIN, refresh_query, idempotency_key="as-of-empty",
                evidence_origin="customer_file",
            ),
            empty,
        )
        latest = self.repository.observations_as_of(
            ADMIN, binding_id=usage_query.binding_id, binding_version=1,
            collection_profile=usage_query.collection_profile, start_at=START, end_at=END,
        )
        self.assertEqual(latest.as_of_commit_sequence, refreshed.commit_sequence)
        self.assertEqual(len(latest.observations), 1)
        with self.assertRaisesRegex(FinanceCollectionError, "invalid_request"):
            self.repository.observations_as_of(
                ADMIN, binding_id=usage_query.binding_id, binding_version=1,
                collection_profile=usage_query.collection_profile,
                start_at=START, end_at=END,
                as_of_commit_sequence=refreshed.commit_sequence + 1,
            )
        self.assertEqual(
            [(item["coverage_state"], item["snapshot_id"]) for item in latest.coverage],
            [("observed", initial.snapshot_id), ("no_observation", refreshed.snapshot_id)],
        )
        self.assertEqual(
            [(item.snapshot_id, item.content_digest) for item in latest.selected_snapshots],
            [(initial.snapshot_id, initial.content_digest),
             (refreshed.snapshot_id, refreshed.content_digest)],
        )
        current = self.repository.current_observations(
            ADMIN, binding_id=usage_query.binding_id, binding_version=1,
            collection_profile=usage_query.collection_profile, start_at=START, end_at=END,
        )
        self.assertEqual(current.coverage, latest.coverage)
        self.assertEqual(current.observations, latest.observations)

        restarted = self.restart() if hasattr(self, "restart") else create_finance_collection_repository(self.config)
        replay = restarted.observations_as_of(
            ADMIN, binding_id=usage_query.binding_id, binding_version=1,
            collection_profile=usage_query.collection_profile, start_at=START, end_at=END,
            as_of_commit_sequence=at_cost.as_of_commit_sequence,
        )
        self.assertEqual(replay, at_cost)
        before_any = restarted.observations_as_of(
            ADMIN, binding_id=usage_query.binding_id, binding_version=1,
            collection_profile=usage_query.collection_profile, start_at=START, end_at=END,
            as_of_commit_sequence=empty_before_publish.as_of_commit_sequence,
        )
        self.assertEqual(before_any, empty_before_publish)

    def test_as_of_selection_rejects_invalid_or_future_cutoffs_and_unauthorized_reads(self):
        self.bind()
        value = query("openai.organization-usage-completions.v1")
        arguments = dict(
            binding_id=value.binding_id, binding_version=1,
            collection_profile=value.collection_profile, start_at=START, end_at=MIDDLE,
        )
        for cutoff in (True, False, -1, 1, 9_223_372_036_854_775_808, "0", 1.0):
            with self.subTest(cutoff=cutoff), self.assertRaisesRegex(
                FinanceCollectionError, "invalid_request"
            ):
                self.repository.observations_as_of(
                    ADMIN, **arguments, as_of_commit_sequence=cutoff,
                )
        viewer = PortfolioPrincipal("acme", "finance", ("finance_viewer",))
        with mock.patch(
            "hormuz.finance_collection_repository.portfolio_transaction",
            side_effect=AssertionError("unauthorized selection must not connect"),
        ):
            with self.assertRaisesRegex(FinanceCollectionError, "forbidden"):
                self.repository.observations_as_of(viewer, **arguments)

    def test_current_cost_observations_expose_boolean_finality(self):
        self.bind()
        value = query("openai.organization-costs.v1")
        collection = normalize_collection_pages(
            value,
            (openai_page([openai_bucket(START, MIDDLE, [openai_cost()])]),),
            fingerprint_key=KEY,
            fingerprint_key_version=1,
        )
        self.repository.publish_collection(
            ADMIN,
            self.repository.prepare_collection(
                ADMIN, value, idempotency_key="cost-bools", evidence_origin="customer_file"
            ),
            collection,
        )
        observation = self.repository.current_observations(
            ADMIN,
            binding_id="provider-account",
            binding_version=1,
            collection_profile=value.collection_profile,
            start_at=START,
            end_at=MIDDLE,
        ).observations[0]
        self.assertIs(observation["provider_final"], False)
        self.assertIs(observation["invoice_final"], False)

    def test_binding_and_role_revocation_races_prevent_publication(self):
        first = self.bind()
        value = query("openai.organization-usage-completions.v1")
        prepared = self.repository.prepare_collection(
            ADMIN, value, idempotency_key="race", evidence_origin="customer_file"
        )
        revoked = self.bind(
            expected_version=first.version,
            state="revoked",
            reason_code="revoked",
        )
        self.assertEqual(revoked.version, 2)
        with self.assertRaisesRegex(FinanceCollectionError, "binding_inactive"):
            self.repository.publish_collection(ADMIN, prepared, normalized_usage(value))
        self.repository.fail_collection(
            ADMIN, prepared, reason_code="binding_revoked"
        )
        with self.assertRaisesRegex(FinanceCollectionError, "binding_inactive"):
            self.repository.prepare_collection(
                ADMIN, value, idempotency_key="later", evidence_origin="customer_file"
            )

    def test_same_attempt_two_replicas_converges_on_one_receipt(self):
        self.bind()
        value = query("openai.organization-usage-completions.v1")
        prepared = self.repository.prepare_collection(
            ADMIN, value, idempotency_key="concurrent", evidence_origin="customer_file"
        )
        collection = normalized_usage(value)
        barrier = threading.Barrier(2)

        def publish(_):
            repository = create_finance_collection_repository(self.config)
            barrier.wait(timeout=10)
            return repository.publish_collection(ADMIN, prepared, collection)

        with ThreadPoolExecutor(max_workers=2) as executor:
            receipts = list(executor.map(publish, range(2)))
        self.assertEqual(receipts[0], receipts[1])
        with managed_sqlite_connection(self.config.database_path) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM portfolio_finance_snapshots"
                ).fetchone()[0],
                1,
            )


if __name__ == "__main__":
    unittest.main()
