"""Provider-free runtime proofs for strict finance collection and persistence."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
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

    def test_binding_request_rejects_boolean_schema_version(self):
        with self.assertRaisesRegex(FinanceCollectionError, "invalid_request"):
            self.bind(schema_version=True)

    def test_postgres_collection_runtime_is_gated_before_connection(self):
        config = replace(
            self.config,
            usage_storage=UsageStorageConfig(backend="postgresql"),
        )
        repository = create_finance_collection_repository(
            config, environ={"HORMUZ_POSTGRES_DSN": "postgresql://redacted"}
        )
        with unittest.mock.patch(
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
