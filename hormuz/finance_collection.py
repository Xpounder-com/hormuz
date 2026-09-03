"""Strict provider-aggregate normalization and bounded collection transport.

Raw pages exist only in memory.  The public results contain allowlisted typed
dimensions, exact numeric text, tenant-keyed fingerprints, and canonical
digests suitable for the append-only finance collection repository.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import hmac
import json
import math
import re
import socket
import threading
import time
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener


MAX_PAGE_BYTES = 1_048_576
MAX_TOTAL_BYTES = 16_777_216
MAX_PAGES = 32
MAX_RECORDS = 4096
MAX_WINDOW_DAYS = 31
MAX_JSON_DEPTH = 16
MAX_JSON_MEMBERS_PER_PAGE = 65_536
MAX_CURSOR_BYTES = 2048
MAX_NUMERIC_LEXEME_BYTES = 128
MAX_RETRIES_PER_PAGE = 2
MAX_RETRY_DELAY_SECONDS = 5
REQUEST_TIMEOUT_SECONDS = 10
COLLECTION_DEADLINE_SECONDS = 60
PARSER_VERSION = 1
FINANCE_SOURCE_BINDING_SCHEMA_ID = "hormuz.finance-source-binding-version"
FINANCE_COLLECTION_EVENT_SCHEMA_ID = "hormuz.finance-collection-event"
FINANCE_SNAPSHOT_SCHEMA_ID = "hormuz.finance-snapshot"
FINANCE_COLLECTION_SOURCE_SCHEMA_IDS = frozenset(
    {
        FINANCE_SOURCE_BINDING_SCHEMA_ID,
        FINANCE_COLLECTION_EVENT_SCHEMA_ID,
        FINANCE_SNAPSHOT_SCHEMA_ID,
    }
)

_MAX_INT64 = 9_223_372_036_854_775_807
_MONEY_BOUND = Decimal("1000000000000000000")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_DIMENSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+ -]{0,127}\Z")
_JSON_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z")
_COUNT_NUMBER = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_CURRENCY = re.compile(r"[A-Za-z]{3}\Z")
_QUANTITY_UNITS = frozenset(
    {"tokens", "requests", "images", "seconds", "minutes", "hours", "bytes", "characters"}
)


class FinanceCollectionError(RuntimeError):
    """Fixed, content-free failure safe for logs and operator output."""

    _CODES = frozenset(
        {
            "forbidden",
            "unauthenticated",
            "invalid_request",
            "binding_inactive",
            "binding_conflict",
            "attempt_conflict",
            "attempt_pending",
            "attempt_terminal",
            "collection_busy",
            "collection_deadline",
            "provider_unauthorized",
            "provider_rate_limited",
            "provider_unavailable",
            "provider_response_invalid",
            "provider_response_too_large",
            "pagination_invalid",
            "numeric_domain_invalid",
            "snapshot_conflict",
            "unavailable",
        }
    )

    def __init__(self, code: str):
        self.code = code if code in self._CODES else "unavailable"
        super().__init__(self.code)


class JsonNumber(str):
    """A source JSON numeric lexeme retained without binary-float coercion."""


@dataclass(frozen=True)
class ProfileSpec:
    provider: str
    source_kind: str
    host: str
    path: str
    bucket_widths: tuple[str, ...]
    group_by: tuple[str, ...]


PROFILE_SPECS = MappingProxyType(
    {
        "openai.organization-usage-completions.v1": ProfileSpec(
            "openai",
            "usage",
            "api.openai.com",
            "/v1/organization/usage/completions",
            ("1m", "1h", "1d"),
            ("project_id", "api_key_id", "model", "batch", "service_tier"),
        ),
        "openai.organization-costs.v1": ProfileSpec(
            "openai",
            "cost",
            "api.openai.com",
            "/v1/organization/costs",
            ("1d",),
            ("project_id", "line_item", "api_key_id"),
        ),
        "anthropic.organization-usage-messages.v1": ProfileSpec(
            "anthropic",
            "usage",
            "api.anthropic.com",
            "/v1/organizations/usage_report/messages",
            ("1m", "1h", "1d"),
            (
                "workspace_id",
                "api_key_id",
                "model",
                "service_tier",
                "context_window",
                "inference_geo",
            ),
        ),
        "anthropic.organization-costs.v1": ProfileSpec(
            "anthropic",
            "cost",
            "api.anthropic.com",
            "/v1/organizations/cost_report",
            ("1d",),
            ("workspace_id", "description"),
        ),
    }
)


@dataclass(frozen=True)
class CollectionQuery:
    organization_id: str
    binding_id: str
    binding_version: int
    collection_profile: str
    query_start_at: str
    query_end_at: str
    bucket_width: str = "1d"
    requested_page_size: int = 7

    def __post_init__(self) -> None:
        if (
            not _safe_id(self.organization_id)
            or not _safe_id(self.binding_id)
            or type(self.binding_version) is not int
            or not 1 <= self.binding_version <= 2_147_483_647
            or not isinstance(self.collection_profile, str)
            or self.collection_profile not in PROFILE_SPECS
            or type(self.requested_page_size) is not int
            or not 1 <= self.requested_page_size <= 1440
        ):
            raise FinanceCollectionError("invalid_request")
        spec = PROFILE_SPECS[self.collection_profile]
        if self.bucket_width not in spec.bucket_widths:
            raise FinanceCollectionError("invalid_request")
        start = _parse_time(self.query_start_at)
        end = _parse_time(self.query_end_at)
        width = _bucket_delta(self.bucket_width)
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        if (
            end <= start
            or end - start > timedelta(days=MAX_WINDOW_DAYS)
            or (start - epoch).total_seconds() % width.total_seconds()
            or (end - start).total_seconds() % width.total_seconds()
        ):
            raise FinanceCollectionError("invalid_request")
        canonical_start = _time_text(start)
        canonical_end = _time_text(end)
        if self.query_start_at != canonical_start or self.query_end_at != canonical_end:
            raise FinanceCollectionError("invalid_request")
        provider_limit = _provider_page_limit(spec, self.bucket_width)
        if self.requested_page_size > provider_limit:
            raise FinanceCollectionError("invalid_request")

    @property
    def profile(self) -> ProfileSpec:
        return PROFILE_SPECS[self.collection_profile]


@dataclass(frozen=True)
class BucketCoverage:
    bucket_start_at: str
    bucket_end_at: str
    coverage_state: str
    observation_count: int


_USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "num_model_requests",
    "input_cached_tokens",
    "input_cache_write_tokens",
    "input_uncached_tokens",
    "input_text_tokens",
    "input_image_tokens",
    "input_audio_tokens",
    "input_cached_text_tokens",
    "input_cached_image_tokens",
    "input_cached_audio_tokens",
    "output_text_tokens",
    "output_image_tokens",
    "output_audio_tokens",
    "uncached_input_tokens",
    "cache_read_input_tokens",
    "cache_creation_5m_input_tokens",
    "cache_creation_1h_input_tokens",
    "server_tool_web_search_requests",
)


@dataclass(frozen=True)
class UsageObservation:
    bucket_start_at: str
    bucket_end_at: str
    observation_digest: str
    provider_project_fingerprint: str | None = None
    provider_workspace_fingerprint: str | None = None
    api_key_fingerprint: str | None = None
    model: str | None = None
    batch: bool | None = None
    service_tier: str | None = None
    context_window: str | None = None
    inference_geo: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    num_model_requests: int | None = None
    input_cached_tokens: int | None = None
    input_cache_write_tokens: int | None = None
    input_uncached_tokens: int | None = None
    input_text_tokens: int | None = None
    input_image_tokens: int | None = None
    input_audio_tokens: int | None = None
    input_cached_text_tokens: int | None = None
    input_cached_image_tokens: int | None = None
    input_cached_audio_tokens: int | None = None
    output_text_tokens: int | None = None
    output_image_tokens: int | None = None
    output_audio_tokens: int | None = None
    uncached_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    cache_creation_5m_input_tokens: int | None = None
    cache_creation_1h_input_tokens: int | None = None
    server_tool_web_search_requests: int | None = None
    usage_basis: str = "provider_native_aggregate_observation"
    provider_final: bool = False


@dataclass(frozen=True)
class CostObservation:
    bucket_start_at: str
    bucket_end_at: str
    observation_digest: str
    native_amount: str
    canonical_amount: str
    currency: str
    free_text_classification: str
    provider_project_fingerprint: str | None = None
    provider_workspace_fingerprint: str | None = None
    api_key_fingerprint: str | None = None
    free_text_fingerprint: str | None = None
    model: str | None = None
    cost_type: str | None = None
    token_type: str | None = None
    service_tier: str | None = None
    context_window: str | None = None
    inference_geo: str | None = None
    native_quantity: str | None = None
    quantity_unit: str | None = None
    cost_basis: str = "provider_reported_aggregate"
    provider_final: bool = False
    invoice_final: bool = False


@dataclass(frozen=True)
class NormalizedCollection:
    query: CollectionQuery
    page_count: int
    record_count: int
    page_chain_digest: str
    content_digest: str
    coverage: tuple[BucketCoverage, ...]
    usage_observations: tuple[UsageObservation, ...]
    cost_observations: tuple[CostObservation, ...]
    fingerprint_key_version: int
    parser_version: int = PARSER_VERSION


def tenant_fingerprint(
    key: bytes,
    *,
    organization_id: str,
    kind: str,
    value: str,
) -> str:
    """Return a domain-separated HMAC with no cross-tenant stable input."""

    if (
        type(key) is not bytes
        or not 32 <= len(key) <= 4096
        or not _safe_id(organization_id)
        or not _safe_id(kind)
        or not isinstance(value, str)
        or not 1 <= len(value.encode("utf-8")) <= 2048
        or not _unicode_safe(value)
    ):
        raise FinanceCollectionError("invalid_request")
    fields = ("hormuz.finance-fingerprint.v1", organization_id, kind, value)
    message = b"".join(
        len(field.encode("utf-8")).to_bytes(4, "big") + field.encode("utf-8")
        for field in fields
    )
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def validate_finance_source_binding_event(value: Mapping[str, Any]) -> None:
    """Validate the finite audit source for one immutable binding version."""

    _exact_keys(
        value,
        {
            "schema_id",
            "schema_version",
            "binding_event_id",
            "organization_id",
            "binding_id",
            "version",
            "provider",
            "provider_account_fingerprint",
            "scope_kind",
            "scope_fingerprints",
            "credential_reference_id",
            "credential_reference_version",
            "fingerprint_key_version",
            "binding_state",
            "previous_version",
            "content_digest",
            "bound_by",
            "bound_at",
            "reason_code",
        },
    )
    version = value.get("version")
    previous = value.get("previous_version")
    scopes = value.get("scope_fingerprints")
    if (
        value.get("schema_id") != FINANCE_SOURCE_BINDING_SCHEMA_ID
        or value.get("schema_version") != 1
        or not _uuid_string(value.get("binding_event_id"))
        or not _safe_id(value.get("organization_id"))
        or not _safe_id(value.get("binding_id"))
        or type(version) is not int
        or not 1 <= version <= 2_147_483_647
        or (version == 1 and previous is not None)
        or (version > 1 and previous != version - 1)
        or value.get("provider") not in {"openai", "anthropic"}
        or not _sha(value.get("provider_account_fingerprint"))
        or value.get("scope_kind") not in {"organization", "projects", "workspaces"}
        or not isinstance(scopes, list)
        or len(scopes) > 1000
        or any(not _sha(item) for item in scopes)
        or scopes != sorted(set(scopes))
        or not _safe_id(value.get("credential_reference_id"))
        or type(value.get("credential_reference_version")) is not int
        or not 1 <= value["credential_reference_version"] <= 2_147_483_647
        or type(value.get("fingerprint_key_version")) is not int
        or not 1 <= value["fingerprint_key_version"] <= 2_147_483_647
        or value.get("binding_state") not in {"active", "revoked"}
        or not _sha(value.get("content_digest"))
        or not _safe_id(value.get("bound_by"))
        or not _canonical_time(value.get("bound_at"))
        or not _safe_id(value.get("reason_code"))
    ):
        raise FinanceCollectionError("provider_response_invalid")
    if (value["scope_kind"] == "organization") != (scopes == []):
        raise FinanceCollectionError("provider_response_invalid")
    if value["provider"] == "openai" and value["scope_kind"] == "workspaces":
        raise FinanceCollectionError("provider_response_invalid")
    if value["provider"] == "anthropic" and value["scope_kind"] == "projects":
        raise FinanceCollectionError("provider_response_invalid")


def validate_finance_collection_event(value: Mapping[str, Any]) -> None:
    """Validate a content-free terminal collection event audit source."""

    _exact_keys(
        value,
        {
            "schema_id",
            "schema_version",
            "event_id",
            "organization_id",
            "attempt_id",
            "state",
            "reason_code",
            "receipt_id",
            "snapshot_id",
            "actor_id",
            "occurred_at",
        },
    )
    succeeded = value.get("state") == "succeeded"
    if (
        value.get("schema_id") != FINANCE_COLLECTION_EVENT_SCHEMA_ID
        or value.get("schema_version") != 1
        or not _uuid_string(value.get("event_id"))
        or not _safe_id(value.get("organization_id"))
        or not _uuid_string(value.get("attempt_id"))
        or value.get("state") not in {"succeeded", "failed", "abandoned"}
        or not _safe_id(value.get("reason_code"))
        or not _safe_id(value.get("actor_id"))
        or not _canonical_time(value.get("occurred_at"))
        or (succeeded and value.get("reason_code") != "completed")
        or (succeeded and not _hex(value.get("receipt_id"), 32))
        or (succeeded and not _uuid_string(value.get("snapshot_id")))
        or (not succeeded and value.get("receipt_id") is not None)
        or (not succeeded and value.get("snapshot_id") is not None)
    ):
        raise FinanceCollectionError("provider_response_invalid")


def validate_finance_snapshot_event(value: Mapping[str, Any]) -> None:
    """Validate the finite audit source for one complete published snapshot."""

    _exact_keys(
        value,
        {
            "schema_id",
            "schema_version",
            "snapshot_id",
            "organization_id",
            "attempt_id",
            "binding_id",
            "binding_version",
            "collection_profile",
            "source_kind",
            "query_start_at",
            "query_end_at",
            "evidence_origin",
            "scope_provenance",
            "parser_version",
            "page_count",
            "record_count",
            "requested_page_size",
            "page_chain_digest",
            "content_digest",
            "supersedes_snapshot_id",
            "commit_sequence",
            "published_by",
            "published_at",
            "provider_final",
            "invoice_final",
        },
    )
    profile = value.get("collection_profile")
    if (
        value.get("schema_id") != FINANCE_SNAPSHOT_SCHEMA_ID
        or value.get("schema_version") != 1
        or not _uuid_string(value.get("snapshot_id"))
        or not _uuid_string(value.get("attempt_id"))
        or not _safe_id(value.get("organization_id"))
        or not _safe_id(value.get("binding_id"))
        or type(value.get("binding_version")) is not int
        or not 1 <= value["binding_version"] <= 2_147_483_647
        or not isinstance(profile, str)
        or profile not in PROFILE_SPECS
        or value.get("source_kind") != PROFILE_SPECS[profile].source_kind
        or not _canonical_time(value.get("query_start_at"))
        or not _canonical_time(value.get("query_end_at"))
        or value.get("evidence_origin") not in {"authenticated_api", "customer_file"}
        or value.get("scope_provenance") not in {
            "authenticated_query_scope_unverified",
            "customer_supplied_scope_unverified",
        }
        or type(value.get("parser_version")) is not int
        or value["parser_version"] != PARSER_VERSION
        or type(value.get("page_count")) is not int
        or not 1 <= value["page_count"] <= MAX_PAGES
        or type(value.get("record_count")) is not int
        or not 0 <= value["record_count"] <= MAX_RECORDS
        or type(value.get("requested_page_size")) is not int
        or not 1 <= value["requested_page_size"] <= 1440
        or not _sha(value.get("page_chain_digest"))
        or not _sha(value.get("content_digest"))
        or (
            value.get("supersedes_snapshot_id") is not None
            and not _uuid_string(value.get("supersedes_snapshot_id"))
        )
        or type(value.get("commit_sequence")) is not int
        or not 1 <= value["commit_sequence"] <= _MAX_INT64
        or not _safe_id(value.get("published_by"))
        or not _canonical_time(value.get("published_at"))
        or value.get("provider_final") is not False
        or value.get("invoice_final") is not False
    ):
        raise FinanceCollectionError("provider_response_invalid")


def finance_collection_source_identity(
    schema_id: str,
    event: Mapping[str, Any],
) -> str:
    """Validate a reviewed finance source and return its exact event identity."""

    if schema_id == FINANCE_SOURCE_BINDING_SCHEMA_ID:
        validate_finance_source_binding_event(event)
        identity = event.get("binding_event_id")
    elif schema_id == FINANCE_COLLECTION_EVENT_SCHEMA_ID:
        validate_finance_collection_event(event)
        identity = event.get("event_id")
    elif schema_id == FINANCE_SNAPSHOT_SCHEMA_ID:
        validate_finance_snapshot_event(event)
        identity = event.get("snapshot_id")
    else:
        raise FinanceCollectionError("provider_response_invalid")
    assert isinstance(identity, str)
    return identity


def validate_normalized_collection(value: NormalizedCollection) -> None:
    """Recheck a normalized object before it crosses the storage boundary."""

    if (
        type(value) is not NormalizedCollection
        or type(value.query) is not CollectionQuery
        or type(value.page_count) is not int
        or not 1 <= value.page_count <= MAX_PAGES
        or type(value.record_count) is not int
        or not 0 <= value.record_count <= MAX_RECORDS
        or type(value.fingerprint_key_version) is not int
        or not 1 <= value.fingerprint_key_version <= 2_147_483_647
        or value.parser_version != PARSER_VERSION
        or not _sha(value.page_chain_digest)
        or not _sha(value.content_digest)
    ):
        raise FinanceCollectionError("snapshot_conflict")
    expected_grid = _expected_grid(value.query)
    if tuple(
        (item.bucket_start_at, item.bucket_end_at) for item in value.coverage
    ) != expected_grid:
        raise FinanceCollectionError("snapshot_conflict")
    if any(
        type(item) is not BucketCoverage
        or item.coverage_state not in {"observed", "no_observation"}
        or type(item.observation_count) is not int
        or not 0 <= item.observation_count <= MAX_RECORDS
        or (item.observation_count == 0) != (item.coverage_state == "no_observation")
        for item in value.coverage
    ):
        raise FinanceCollectionError("snapshot_conflict")
    if value.query.profile.source_kind == "usage":
        if value.cost_observations:
            raise FinanceCollectionError("snapshot_conflict")
        observations: Sequence[UsageObservation | CostObservation] = (
            value.usage_observations
        )
    else:
        if value.usage_observations:
            raise FinanceCollectionError("snapshot_conflict")
        observations = value.cost_observations
    if value.record_count != len(observations):
        raise FinanceCollectionError("snapshot_conflict")
    semantic_keys: set[tuple[object, ...]] = set()
    for item in observations:
        semantic = (
            _validate_normalized_usage(item)
            if value.query.profile.source_kind == "usage"
            else _validate_normalized_cost(value.query, item)
        )
        if semantic in semantic_keys:
            raise FinanceCollectionError("snapshot_conflict")
        semantic_keys.add(semantic)
    if tuple(item.observation_digest for item in observations) != tuple(
        sorted(item.observation_digest for item in observations)
    ):
        raise FinanceCollectionError("snapshot_conflict")
    counts: dict[tuple[str, str], int] = {}
    for item in observations:
        interval = (item.bucket_start_at, item.bucket_end_at)
        if interval not in expected_grid:
            raise FinanceCollectionError("snapshot_conflict")
        counts[interval] = counts.get(interval, 0) + 1
    if any(
        counts.get((item.bucket_start_at, item.bucket_end_at), 0)
        != item.observation_count
        for item in value.coverage
    ):
        raise FinanceCollectionError("snapshot_conflict")
    content = {
        "source": {
            "organization_id": value.query.organization_id,
            "binding_id": value.query.binding_id,
            "binding_version": value.query.binding_version,
            "collection_profile": value.query.collection_profile,
            "query_start_at": value.query.query_start_at,
            "query_end_at": value.query.query_end_at,
        },
        "coverage": [asdict(item) for item in value.coverage],
        "usage_observations": [asdict(item) for item in value.usage_observations],
        "cost_observations": [asdict(item) for item in value.cost_observations],
    }
    if not hmac.compare_digest(_digest(content), value.content_digest):
        raise FinanceCollectionError("snapshot_conflict")


def _validate_normalized_usage(
    value: UsageObservation | CostObservation,
) -> tuple[object, ...]:
    if type(value) is not UsageObservation:
        raise FinanceCollectionError("snapshot_conflict")
    body = asdict(value)
    digest = body.pop("observation_digest")
    if (
        not _canonical_time(value.bucket_start_at)
        or not _canonical_time(value.bucket_end_at)
        or _parse_time(value.bucket_end_at) <= _parse_time(value.bucket_start_at)
        or not _sha(digest)
        or not hmac.compare_digest(_digest(body), digest)
        or any(
            item is not None and not _sha(item)
            for item in (
                value.provider_project_fingerprint,
                value.provider_workspace_fingerprint,
                value.api_key_fingerprint,
            )
        )
        or any(
            item is not None and _optional_dimension(item) != item
            for item in (
                value.model,
                value.service_tier,
                value.context_window,
                value.inference_geo,
            )
        )
        or (value.batch is not None and type(value.batch) is not bool)
        or value.usage_basis != "provider_native_aggregate_observation"
        or value.provider_final is not False
    ):
        raise FinanceCollectionError("snapshot_conflict")
    counts = tuple(getattr(value, field) for field in _USAGE_FIELDS)
    if all(item is None for item in counts) or any(
        item is not None
        and (type(item) is not int or not 0 <= item <= _MAX_INT64)
        for item in counts
    ):
        raise FinanceCollectionError("snapshot_conflict")
    return (
        "usage",
        value.bucket_start_at,
        value.bucket_end_at,
        value.provider_project_fingerprint,
        value.provider_workspace_fingerprint,
        value.api_key_fingerprint,
        value.model,
        value.batch,
        value.service_tier,
        value.context_window,
        value.inference_geo,
    )


def _validate_normalized_cost(
    query: CollectionQuery,
    value: UsageObservation | CostObservation,
) -> tuple[object, ...]:
    if type(value) is not CostObservation:
        raise FinanceCollectionError("snapshot_conflict")
    body = asdict(value)
    digest = body.pop("observation_digest")
    try:
        if query.profile.provider == "openai":
            _, _, canonical = _decimal_value(
                JsonNumber(value.native_amount),
                require_number=True,
            )
            if value.native_quantity is not None:
                _decimal_value(JsonNumber(value.native_quantity), require_number=True)
        else:
            _, native, _ = _decimal_value(value.native_amount, require_number=False)
            with localcontext() as context:
                context.prec = 100
                canonical = _decimal_text(native.scaleb(-2))
            if value.native_quantity is not None:
                raise FinanceCollectionError("snapshot_conflict")
        unit = _quantity_unit(
            value.quantity_unit,
            required=value.native_quantity is not None,
        )
    except FinanceCollectionError:
        raise FinanceCollectionError("snapshot_conflict") from None
    if (
        not _canonical_time(value.bucket_start_at)
        or not _canonical_time(value.bucket_end_at)
        or _parse_time(value.bucket_end_at) <= _parse_time(value.bucket_start_at)
        or not _sha(digest)
        or not hmac.compare_digest(_digest(body), digest)
        or canonical != value.canonical_amount
        or _currency(value.currency) != value.currency
        or any(
            item is not None and not _sha(item)
            for item in (
                value.provider_project_fingerprint,
                value.provider_workspace_fingerprint,
                value.api_key_fingerprint,
                value.free_text_fingerprint,
            )
        )
        or value.free_text_classification
        not in {
            "unclassified",
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
        }
        or (
            value.free_text_classification != "unclassified"
            and value.free_text_fingerprint is None
        )
        or any(
            item is not None and _optional_dimension(item) != item
            for item in (
                value.model,
                value.cost_type,
                value.token_type,
                value.service_tier,
                value.context_window,
                value.inference_geo,
            )
        )
        or unit != value.quantity_unit
        or value.cost_basis != "provider_reported_aggregate"
        or value.provider_final is not False
        or value.invoice_final is not False
    ):
        raise FinanceCollectionError("snapshot_conflict")
    return (
        "cost",
        value.bucket_start_at,
        value.bucket_end_at,
        value.provider_project_fingerprint,
        value.provider_workspace_fingerprint,
        value.api_key_fingerprint,
        value.free_text_fingerprint,
        value.model,
        value.cost_type,
        value.token_type,
        value.service_tier,
        value.context_window,
        value.inference_geo,
        value.quantity_unit,
    )


def normalize_collection_pages(
    query: CollectionQuery,
    pages: Sequence[bytes],
    *,
    fingerprint_key: bytes,
    fingerprint_key_version: int,
) -> NormalizedCollection:
    """Validate a complete provider page chain and discard all raw bytes."""

    if type(query) is not CollectionQuery or type(fingerprint_key) is not bytes:
        raise FinanceCollectionError("invalid_request")
    if (
        not isinstance(pages, (tuple, list))
        or not 1 <= len(pages) <= MAX_PAGES
        or any(type(page) is not bytes for page in pages)
    ):
        raise FinanceCollectionError("provider_response_invalid")
    total = sum(len(page) for page in pages)
    if total > MAX_TOTAL_BYTES:
        raise FinanceCollectionError("provider_response_too_large")
    decoded = tuple(_decode_json_page(page) for page in pages)
    return _normalize_decoded_pages(
        query,
        decoded,
        fingerprint_key=fingerprint_key,
        fingerprint_key_version=fingerprint_key_version,
    )


def normalize_collection_file(
    query: CollectionQuery,
    payload: bytes,
    *,
    fingerprint_key: bytes,
    fingerprint_key_version: int,
) -> NormalizedCollection:
    """Normalize one customer-supplied bundle under the already prepared query."""

    if type(query) is not CollectionQuery or type(payload) is not bytes:
        raise FinanceCollectionError("invalid_request")
    if not 1 <= len(payload) <= MAX_TOTAL_BYTES:
        raise FinanceCollectionError("provider_response_too_large")
    value, _ = _decode_json(payload, member_limit=MAX_JSON_MEMBERS_PER_PAGE * MAX_PAGES)
    value = _mapping(value)
    _exact_keys(
        value,
        {
            "schema_id",
            "schema_version",
            "collection_profile",
            "query_start_at",
            "query_end_at",
            "bucket_width",
            "requested_page_size",
            "pages",
        },
    )
    if (
        value["schema_id"] != "hormuz.finance-collection-file-bundle"
        or value["schema_version"] != JsonNumber("1")
        or value["collection_profile"] != query.collection_profile
        or value["query_start_at"] != query.query_start_at
        or value["query_end_at"] != query.query_end_at
        or value["bucket_width"] != query.bucket_width
        or _source_count(value["requested_page_size"]) != query.requested_page_size
        or not isinstance(value["pages"], list)
        or not 1 <= len(value["pages"]) <= MAX_PAGES
    ):
        raise FinanceCollectionError("provider_response_invalid")
    pages = tuple(_mapping(page) for page in value["pages"])
    if any(_approx_json_size(page) > MAX_PAGE_BYTES for page in pages):
        raise FinanceCollectionError("provider_response_too_large")
    return _normalize_decoded_pages(
        query,
        pages,
        fingerprint_key=fingerprint_key,
        fingerprint_key_version=fingerprint_key_version,
    )


def _normalize_decoded_pages(
    query: CollectionQuery,
    pages: Sequence[Mapping[str, Any]],
    *,
    fingerprint_key: bytes,
    fingerprint_key_version: int,
) -> NormalizedCollection:
    if (
        not 32 <= len(fingerprint_key) <= 4096
        or type(fingerprint_key_version) is not int
        or not 1 <= fingerprint_key_version <= 2_147_483_647
    ):
        raise FinanceCollectionError("invalid_request")
    expected_grid = _expected_grid(query)
    observed_buckets: dict[tuple[str, str], tuple[dict[str, Any], ...]] = {}
    seen_cursors: set[str] = set()
    page_provenance: list[dict[str, object]] = []
    total_records = 0
    for index, page in enumerate(pages):
        buckets, has_more, cursor = _page_parts(query.profile, page)
        if has_more != (index < len(pages) - 1):
            raise FinanceCollectionError("pagination_invalid")
        if has_more:
            if cursor is None or cursor in seen_cursors:
                raise FinanceCollectionError("pagination_invalid")
            seen_cursors.add(cursor)
        elif cursor is not None:
            raise FinanceCollectionError("pagination_invalid")
        page_intervals: list[tuple[str, str]] = []
        page_record_count = 0
        for bucket in buckets:
            start, end, records = _bucket_parts(query.profile, bucket)
            interval = (start, end)
            if interval not in expected_grid or interval in observed_buckets:
                raise FinanceCollectionError("provider_response_invalid")
            observed_buckets[interval] = records
            page_intervals.append(interval)
            page_record_count += len(records)
        total_records += page_record_count
        if total_records > MAX_RECORDS:
            raise FinanceCollectionError("provider_response_invalid")
        page_provenance.append(
            {
                "page": index + 1,
                "bucket_count": len(page_intervals),
                "record_count": page_record_count,
                "first_bucket": list(page_intervals[0]) if page_intervals else None,
                "last_bucket": list(page_intervals[-1]) if page_intervals else None,
            }
        )
    if set(observed_buckets) != set(expected_grid):
        raise FinanceCollectionError("provider_response_invalid")

    usage: list[UsageObservation] = []
    costs: list[CostObservation] = []
    coverage: list[BucketCoverage] = []
    semantic_keys: set[tuple[object, ...]] = set()
    for start, end in expected_grid:
        records = observed_buckets[(start, end)]
        for record in records:
            if query.profile.source_kind == "usage":
                normalized, semantic = _usage_observation(
                    query,
                    start,
                    end,
                    record,
                    fingerprint_key,
                )
                usage.append(normalized)
            else:
                normalized, semantic = _cost_observation(
                    query,
                    start,
                    end,
                    record,
                    fingerprint_key,
                )
                costs.append(normalized)
            if semantic in semantic_keys:
                raise FinanceCollectionError("provider_response_invalid")
            semantic_keys.add(semantic)
        count = len(records)
        coverage.append(
            BucketCoverage(
                start,
                end,
                "observed" if count else "no_observation",
                count,
            )
        )

    usage.sort(key=lambda item: item.observation_digest)
    costs.sort(key=lambda item: item.observation_digest)
    content = {
        "source": {
            "organization_id": query.organization_id,
            "binding_id": query.binding_id,
            "binding_version": query.binding_version,
            "collection_profile": query.collection_profile,
            "query_start_at": query.query_start_at,
            "query_end_at": query.query_end_at,
        },
        "coverage": [asdict(item) for item in coverage],
        "usage_observations": [asdict(item) for item in usage],
        "cost_observations": [asdict(item) for item in costs],
    }
    page_chain = {
        "requested_page_size": query.requested_page_size,
        "pages": page_provenance,
    }
    return NormalizedCollection(
        query=query,
        page_count=len(pages),
        record_count=total_records,
        page_chain_digest=_digest(page_chain),
        content_digest=_digest(content),
        coverage=tuple(coverage),
        usage_observations=tuple(usage),
        cost_observations=tuple(costs),
        fingerprint_key_version=fingerprint_key_version,
    )


def _usage_observation(
    query: CollectionQuery,
    start: str,
    end: str,
    value: Mapping[str, Any],
    key: bytes,
) -> tuple[UsageObservation, tuple[object, ...]]:
    if query.profile.provider == "openai":
        allowed = {
            "object",
            "input_tokens",
            "output_tokens",
            "num_model_requests",
            "input_cached_tokens",
            "input_cache_write_tokens",
            "input_uncached_tokens",
            "input_text_tokens",
            "input_image_tokens",
            "input_audio_tokens",
            "input_cached_text_tokens",
            "input_cached_image_tokens",
            "input_cached_audio_tokens",
            "output_text_tokens",
            "output_image_tokens",
            "output_audio_tokens",
            "project_id",
            "user_id",
            "api_key_id",
            "model",
            "batch",
            "service_tier",
        }
        _allowed_keys(value, allowed)
        if value.get("object") != "organization.usage.completions.result":
            raise FinanceCollectionError("provider_response_invalid")
        project = _optional_fingerprint(key, query, "project", value.get("project_id"))
        workspace = None
        api_key = _optional_fingerprint(key, query, "api-key", value.get("api_key_id"))
        _discarded_identifier(value.get("user_id"))
        dimensions = {
            "model": _optional_dimension(value.get("model")),
            "batch": _optional_bool(value.get("batch")),
            "service_tier": _optional_dimension(value.get("service_tier")),
            "context_window": None,
            "inference_geo": None,
        }
        counts = {field: _optional_source_count(value.get(field)) for field in _USAGE_FIELDS}
    else:
        allowed = {
            "account_id",
            "api_key_id",
            "cache_creation",
            "cache_read_input_tokens",
            "context_window",
            "inference_geo",
            "model",
            "output_tokens",
            "server_tool_use",
            "service_account_id",
            "service_tier",
            "speed",
            "uncached_input_tokens",
            "workspace_id",
        }
        _allowed_keys(value, allowed)
        _discarded_identifier(value.get("account_id"))
        _discarded_identifier(value.get("service_account_id"))
        if value.get("speed") is not None:
            raise FinanceCollectionError("provider_response_invalid")
        project = None
        workspace = _optional_fingerprint(key, query, "workspace", value.get("workspace_id"))
        api_key = _optional_fingerprint(key, query, "api-key", value.get("api_key_id"))
        dimensions = {
            "model": _optional_dimension(value.get("model")),
            "batch": None,
            "service_tier": _optional_dimension(value.get("service_tier")),
            "context_window": _optional_dimension(value.get("context_window")),
            "inference_geo": _optional_dimension(value.get("inference_geo")),
        }
        cache = _optional_mapping(value.get("cache_creation"), {"ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens"})
        tools = _optional_mapping(value.get("server_tool_use"), {"web_search_requests"})
        counts = {field: None for field in _USAGE_FIELDS}
        counts.update(
            {
                "output_tokens": _optional_source_count(value.get("output_tokens")),
                "uncached_input_tokens": _optional_source_count(value.get("uncached_input_tokens")),
                "cache_read_input_tokens": _optional_source_count(value.get("cache_read_input_tokens")),
                "cache_creation_5m_input_tokens": _optional_source_count(cache.get("ephemeral_5m_input_tokens")),
                "cache_creation_1h_input_tokens": _optional_source_count(cache.get("ephemeral_1h_input_tokens")),
                "server_tool_web_search_requests": _optional_source_count(tools.get("web_search_requests")),
            }
        )
    if all(counts[field] is None for field in _USAGE_FIELDS):
        raise FinanceCollectionError("provider_response_invalid")
    body = {
        "bucket_start_at": start,
        "bucket_end_at": end,
        "provider_project_fingerprint": project,
        "provider_workspace_fingerprint": workspace,
        "api_key_fingerprint": api_key,
        **dimensions,
        **counts,
        "usage_basis": "provider_native_aggregate_observation",
        "provider_final": False,
    }
    digest = _digest(body)
    item = UsageObservation(observation_digest=digest, **body)
    semantic = (
        "usage",
        start,
        end,
        project,
        workspace,
        api_key,
        *(dimensions[field] for field in dimensions),
    )
    return item, semantic


def _cost_observation(
    query: CollectionQuery,
    start: str,
    end: str,
    value: Mapping[str, Any],
    key: bytes,
) -> tuple[CostObservation, tuple[object, ...]]:
    if query.profile.provider == "openai":
        _allowed_keys(
            value,
            {
                "object",
                "amount",
                "line_item",
                "project_id",
                "api_key_id",
                "quantity",
                "quantity_unit",
            },
        )
        if value.get("object") != "organization.costs.result":
            raise FinanceCollectionError("provider_response_invalid")
        amount = _mapping(value.get("amount"))
        _exact_keys(amount, {"value", "currency"})
        native_amount, _, canonical_amount = _decimal_value(amount.get("value"), require_number=True)
        currency = _currency(amount.get("currency"))
        project = _optional_fingerprint(key, query, "project", value.get("project_id"))
        workspace = None
        api_key = _optional_fingerprint(key, query, "api-key", value.get("api_key_id"))
        text = value.get("line_item")
        dimensions = {
            "model": None,
            "cost_type": None,
            "token_type": None,
            "service_tier": None,
            "context_window": None,
            "inference_geo": None,
        }
        quantity_value = value.get("quantity")
        native_quantity = None
        if quantity_value is not None:
            native_quantity, _, _ = _decimal_value(quantity_value, require_number=True)
        quantity_unit = _quantity_unit(value.get("quantity_unit"), required=native_quantity is not None)
    else:
        _allowed_keys(
            value,
            {
                "amount",
                "currency",
                "description",
                "workspace_id",
                "model",
                "cost_type",
                "token_type",
                "service_tier",
                "context_window",
                "inference_geo",
            },
        )
        native_amount, decimal_amount, _ = _decimal_value(value.get("amount"), require_number=False)
        with localcontext() as context:
            context.prec = 100
            major = decimal_amount.scaleb(-2)
        _validate_decimal(major)
        canonical_amount = _decimal_text(major)
        currency = _currency(value.get("currency"))
        if currency != "USD":
            raise FinanceCollectionError("provider_response_invalid")
        project = None
        workspace = _optional_fingerprint(key, query, "workspace", value.get("workspace_id"))
        api_key = None
        text = value.get("description")
        dimensions = {
            "model": _optional_dimension(value.get("model")),
            "cost_type": _optional_dimension(value.get("cost_type")),
            "token_type": _optional_dimension(value.get("token_type")),
            "service_tier": _optional_dimension(value.get("service_tier")),
            "context_window": _optional_dimension(value.get("context_window")),
            "inference_geo": _optional_dimension(value.get("inference_geo")),
        }
        native_quantity = None
        quantity_unit = None
    classification, text_fingerprint = _free_text(key, query, text)
    body = {
        "bucket_start_at": start,
        "bucket_end_at": end,
        "native_amount": native_amount,
        "canonical_amount": canonical_amount,
        "currency": currency,
        "free_text_classification": classification,
        "provider_project_fingerprint": project,
        "provider_workspace_fingerprint": workspace,
        "api_key_fingerprint": api_key,
        "free_text_fingerprint": text_fingerprint,
        **dimensions,
        "native_quantity": native_quantity,
        "quantity_unit": quantity_unit,
        "cost_basis": "provider_reported_aggregate",
        "provider_final": False,
        "invoice_final": False,
    }
    digest = _digest(body)
    item = CostObservation(observation_digest=digest, **body)
    semantic = (
        "cost",
        start,
        end,
        project,
        workspace,
        api_key,
        text_fingerprint,
        *(dimensions[field] for field in dimensions),
        quantity_unit,
    )
    return item, semantic


def _free_text(
    key: bytes,
    query: CollectionQuery,
    value: object,
) -> tuple[str, str | None]:
    if value is None:
        return "unclassified", None
    if (
        not isinstance(value, str)
        or not 1 <= len(value.encode("utf-8")) <= 2048
        or not _unicode_safe(value)
    ):
        raise FinanceCollectionError("provider_response_invalid")
    known = {
        "Claude Usage - Input Tokens": "input_tokens",
        "Claude Usage - Output Tokens": "output_tokens",
        "Claude Usage - Cache Read Tokens": "cache_read_tokens",
        "Claude Usage - Cache Write Tokens": "cache_write_tokens",
    }
    classification = known.get(value, "unclassified")
    return classification, tenant_fingerprint(
        key,
        organization_id=query.organization_id,
        kind="provider-cost-text",
        value=value,
    )


def _decimal_value(value: object, *, require_number: bool) -> tuple[str, Decimal, str]:
    if require_number:
        if type(value) is not JsonNumber:
            raise FinanceCollectionError("numeric_domain_invalid")
    elif not isinstance(value, str) or type(value) is JsonNumber:
        # Anthropic publishes its fractional-cent amount as a JSON string.
        raise FinanceCollectionError("numeric_domain_invalid")
    source = str(value)
    if (
        not 1 <= len(source.encode("utf-8")) <= MAX_NUMERIC_LEXEME_BYTES
        or _JSON_NUMBER.fullmatch(source) is None
    ):
        raise FinanceCollectionError("numeric_domain_invalid")
    try:
        decimal_value = Decimal(source)
    except InvalidOperation:
        raise FinanceCollectionError("numeric_domain_invalid") from None
    _validate_decimal(decimal_value)
    return source, decimal_value, _decimal_text(decimal_value)


def _validate_decimal(value: Decimal) -> None:
    if not value.is_finite() or not -_MONEY_BOUND < value < _MONEY_BOUND:
        raise FinanceCollectionError("numeric_domain_invalid")
    text = format(value, "f")
    unsigned = text.lstrip("-")
    integer, dot, fractional = unsigned.partition(".")
    if len(integer.lstrip("0")) > 18 or (
        bool(dot) and len(fractional) > 18
    ):
        raise FinanceCollectionError("numeric_domain_invalid")
    digits = value.as_tuple().digits
    first_nonzero = next((index for index, digit in enumerate(digits) if digit), len(digits))
    if len(digits[first_nonzero:]) > 36:
        raise FinanceCollectionError("numeric_domain_invalid")
    if value:
        exponent = value.normalize().as_tuple().exponent
        if not -18 <= exponent <= 17:
            raise FinanceCollectionError("numeric_domain_invalid")


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in {"", "-0"}:
        return "0"
    return text


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):  # noqa: ANN001
        return None


_COLLECTION_SEMAPHORE = threading.BoundedSemaphore(4)


def fetch_collection_pages(
    query: CollectionQuery,
    *,
    credential: str,
    base_url: str,
    opener: Any | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[bytes, ...]:
    """Fetch one fixed-endpoint chain with TLS, no redirects, and bounded retry."""

    if (
        type(query) is not CollectionQuery
        or not isinstance(credential, str)
        or not 1 <= len(credential.encode("utf-8")) <= 4096
        or any(character in credential for character in ("\x00", "\r", "\n"))
        or base_url != f"https://{query.profile.host}"
    ):
        raise FinanceCollectionError("invalid_request")
    started = clock()
    if not _COLLECTION_SEMAPHORE.acquire(timeout=COLLECTION_DEADLINE_SECONDS):
        raise FinanceCollectionError("collection_busy")
    try:
        client = opener if opener is not None else build_opener(_NoRedirect())
        pages: list[bytes] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        total = 0
        while True:
            if len(pages) >= MAX_PAGES:
                raise FinanceCollectionError("pagination_invalid")
            url = _request_url(query, cursor)
            payload = _request_page(
                client,
                query,
                url,
                credential,
                started=started,
                clock=clock,
                sleep=sleep,
            )
            total += len(payload)
            if total > MAX_TOTAL_BYTES:
                raise FinanceCollectionError("provider_response_too_large")
            page = _decode_json_page(payload)
            _, has_more, next_cursor = _page_parts(query.profile, page)
            pages.append(payload)
            if not has_more:
                return tuple(pages)
            if next_cursor is None or next_cursor in seen_cursors:
                raise FinanceCollectionError("pagination_invalid")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
    finally:
        _COLLECTION_SEMAPHORE.release()


def _request_page(
    opener: Any,
    query: CollectionQuery,
    url: str,
    credential: str,
    *,
    started: float,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> bytes:
    headers = {"Accept": "application/json"}
    if query.profile.provider == "openai":
        headers["Authorization"] = f"Bearer {credential}"
    else:
        headers["x-api-key"] = credential
        headers["anthropic-version"] = "2023-06-01"
    for attempt in range(MAX_RETRIES_PER_PAGE + 1):
        if clock() - started >= COLLECTION_DEADLINE_SECONDS:
            raise FinanceCollectionError("collection_deadline")
        request = Request(url, headers=headers, method="GET")
        try:
            response = opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS)
            with response:
                status = getattr(response, "status", response.getcode())
                if status != 200 or response.geturl() != url:
                    raise FinanceCollectionError("provider_response_invalid")
                payload = response.read(MAX_PAGE_BYTES + 1)
            if len(payload) > MAX_PAGE_BYTES:
                raise FinanceCollectionError("provider_response_too_large")
            if not payload:
                raise FinanceCollectionError("provider_response_invalid")
            return payload
        except HTTPError as error:
            status = error.code
            if status in {401, 403}:
                raise FinanceCollectionError("provider_unauthorized") from None
            retryable = status == 429 or 500 <= status <= 599
            if not retryable or attempt >= MAX_RETRIES_PER_PAGE:
                code = (
                    "provider_rate_limited"
                    if status == 429
                    else "provider_unavailable"
                    if 500 <= status <= 599
                    else "provider_response_invalid"
                )
                raise FinanceCollectionError(code) from None
            delay = _retry_delay(error.headers, attempt)
        except (URLError, TimeoutError, socket.timeout, OSError):
            if attempt >= MAX_RETRIES_PER_PAGE:
                raise FinanceCollectionError("provider_unavailable") from None
            delay = min(2**attempt, MAX_RETRY_DELAY_SECONDS)
        if clock() - started + delay >= COLLECTION_DEADLINE_SECONDS:
            raise FinanceCollectionError("collection_deadline")
        sleep(delay)
    raise FinanceCollectionError("provider_unavailable")


def _retry_delay(headers: Any, attempt: int) -> float:
    value = None if headers is None else headers.get("Retry-After")
    try:
        delay = float(value) if value is not None else float(2**attempt)
    except (TypeError, ValueError, OverflowError):
        delay = float(2**attempt)
    if not math.isfinite(delay):
        delay = float(2**attempt)
    if delay < 0:
        delay = 0
    return min(delay, float(MAX_RETRY_DELAY_SECONDS))


def _request_url(query: CollectionQuery, cursor: str | None) -> str:
    spec = query.profile
    start = _parse_time(query.query_start_at)
    end = _parse_time(query.query_end_at)
    if spec.provider == "openai":
        parameters: list[tuple[str, object]] = [
            ("start_time", int(start.timestamp())),
            ("end_time", int(end.timestamp())),
            ("bucket_width", query.bucket_width),
            ("limit", query.requested_page_size),
        ]
    else:
        parameters = [
            ("starting_at", query.query_start_at),
            ("ending_at", query.query_end_at),
            ("bucket_width", query.bucket_width),
            ("limit", query.requested_page_size),
        ]
    parameters.extend(("group_by[]", field) for field in spec.group_by)
    if cursor is not None:
        parameters.append(("page", cursor))
    return f"https://{spec.host}{spec.path}?{urlencode(parameters)}"


def _page_parts(
    spec: ProfileSpec,
    value: Mapping[str, Any],
) -> tuple[tuple[Mapping[str, Any], ...], bool, str | None]:
    expected = {"data", "has_more", "next_page"}
    if spec.provider == "openai":
        expected.add("object")
    _exact_keys(value, expected)
    if spec.provider == "openai" and value.get("object") != "page":
        raise FinanceCollectionError("provider_response_invalid")
    data = value.get("data")
    if not isinstance(data, list):
        raise FinanceCollectionError("provider_response_invalid")
    has_more = value.get("has_more")
    if type(has_more) is not bool:
        raise FinanceCollectionError("provider_response_invalid")
    cursor = value.get("next_page")
    if cursor is not None and (
        not isinstance(cursor, str)
        or not 1 <= len(cursor.encode("utf-8")) <= MAX_CURSOR_BYTES
        or not _unicode_safe(cursor)
    ):
        raise FinanceCollectionError("pagination_invalid")
    return tuple(_mapping(bucket) for bucket in data), has_more, cursor


def _bucket_parts(
    spec: ProfileSpec,
    value: Mapping[str, Any],
) -> tuple[str, str, tuple[dict[str, Any], ...]]:
    if spec.provider == "openai":
        _exact_keys(value, {"object", "start_time", "end_time", "results"})
        if value.get("object") != "bucket":
            raise FinanceCollectionError("provider_response_invalid")
        start = _unix_time(value.get("start_time"))
        end = _unix_time(value.get("end_time"))
    else:
        _exact_keys(value, {"starting_at", "ending_at", "results"})
        start_value = value.get("starting_at")
        end_value = value.get("ending_at")
        if not isinstance(start_value, str) or not isinstance(end_value, str):
            raise FinanceCollectionError("provider_response_invalid")
        start = _time_text(_parse_time(start_value, response=True))
        end = _time_text(_parse_time(end_value, response=True))
    results = value.get("results")
    if not isinstance(results, list) or len(results) > MAX_RECORDS:
        raise FinanceCollectionError("provider_response_invalid")
    return start, end, tuple(dict(_mapping(record)) for record in results)


def _decode_json_page(payload: bytes) -> Mapping[str, Any]:
    if not 1 <= len(payload) <= MAX_PAGE_BYTES:
        code = "provider_response_too_large" if len(payload) > MAX_PAGE_BYTES else "provider_response_invalid"
        raise FinanceCollectionError(code)
    value, _ = _decode_json(payload, member_limit=MAX_JSON_MEMBERS_PER_PAGE)
    return _mapping(value)


def _decode_json(payload: bytes, *, member_limit: int) -> tuple[object, int]:
    members = 0

    def unique(pairs):
        nonlocal members
        members += len(pairs)
        if members > member_limit:
            raise FinanceCollectionError("provider_response_invalid")
        result = {}
        for name, value in pairs:
            if name in result:
                raise FinanceCollectionError("provider_response_invalid")
            result[name] = value
        return result

    def nonfinite(_value):
        raise FinanceCollectionError("numeric_domain_invalid")

    try:
        decoded = payload.decode("utf-8")
        value = json.loads(
            decoded,
            object_pairs_hook=unique,
            parse_int=JsonNumber,
            parse_float=JsonNumber,
            parse_constant=nonfinite,
        )
        _validate_tree(value, depth=1)
        return value, members
    except FinanceCollectionError:
        raise
    except (UnicodeError, json.JSONDecodeError, RecursionError, ValueError, TypeError):
        raise FinanceCollectionError("provider_response_invalid") from None


def _validate_tree(value: object, *, depth: int) -> None:
    if depth > MAX_JSON_DEPTH:
        raise FinanceCollectionError("provider_response_invalid")
    if isinstance(value, str) and not _unicode_safe(value):
        raise FinanceCollectionError("provider_response_invalid")
    if isinstance(value, Mapping):
        for name, child in value.items():
            if not isinstance(name, str) or not _unicode_safe(name):
                raise FinanceCollectionError("provider_response_invalid")
            _validate_tree(child, depth=depth + 1)
    elif isinstance(value, list):
        for child in value:
            _validate_tree(child, depth=depth + 1)


def _expected_grid(query: CollectionQuery) -> tuple[tuple[str, str], ...]:
    start = _parse_time(query.query_start_at)
    end = _parse_time(query.query_end_at)
    width = _bucket_delta(query.bucket_width)
    result = []
    current = start
    while current < end:
        following = current + width
        result.append((_time_text(current), _time_text(following)))
        current = following
    return tuple(result)


def _mapping(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise FinanceCollectionError("provider_response_invalid")
    return value


def _optional_mapping(value: object, keys: set[str]) -> Mapping[str, Any]:
    if value is None:
        return {}
    result = _mapping(value)
    _allowed_keys(result, keys)
    return result


def _exact_keys(value: object, keys: set[str]) -> None:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise FinanceCollectionError("provider_response_invalid")


def _allowed_keys(value: object, keys: set[str]) -> None:
    if not isinstance(value, Mapping) or not set(value).issubset(keys):
        raise FinanceCollectionError("provider_response_invalid")


def _source_count(value: object) -> int:
    if type(value) is not JsonNumber or _COUNT_NUMBER.fullmatch(value) is None:
        raise FinanceCollectionError("numeric_domain_invalid")
    result = int(value)
    if not 0 <= result <= _MAX_INT64:
        raise FinanceCollectionError("numeric_domain_invalid")
    return result


def _optional_source_count(value: object) -> int | None:
    return None if value is None else _source_count(value)


def _unix_time(value: object) -> str:
    seconds = _source_count(value)
    try:
        return _time_text(datetime.fromtimestamp(seconds, tz=timezone.utc))
    except (OverflowError, OSError, ValueError):
        raise FinanceCollectionError("provider_response_invalid") from None


def _parse_time(value: str, *, response: bool = False) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z") or not _unicode_safe(value):
        raise FinanceCollectionError("provider_response_invalid" if response else "invalid_request")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise FinanceCollectionError("provider_response_invalid" if response else "invalid_request") from None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise FinanceCollectionError("provider_response_invalid" if response else "invalid_request")
    return parsed.astimezone(timezone.utc)


def _time_text(value: datetime) -> str:
    timespec = "seconds" if value.microsecond == 0 else "microseconds"
    return value.astimezone(timezone.utc).isoformat(timespec=timespec).replace("+00:00", "Z")


def _bucket_delta(value: str) -> timedelta:
    return {"1m": timedelta(minutes=1), "1h": timedelta(hours=1), "1d": timedelta(days=1)}[value]


def _provider_page_limit(spec: ProfileSpec, bucket_width: str) -> int:
    if spec.provider == "openai" and spec.source_kind == "cost":
        return 180
    if spec.provider == "anthropic" and spec.source_kind == "cost":
        return 31
    return {"1m": 1440, "1h": 168, "1d": 31}[bucket_width]


def _optional_fingerprint(
    key: bytes,
    query: CollectionQuery,
    kind: str,
    value: object,
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise FinanceCollectionError("provider_response_invalid")
    try:
        return tenant_fingerprint(
            key,
            organization_id=query.organization_id,
            kind=kind,
            value=value,
        )
    except FinanceCollectionError:
        raise FinanceCollectionError("provider_response_invalid") from None


def _discarded_identifier(value: object) -> None:
    if value is not None and (
        not isinstance(value, str)
        or not 1 <= len(value.encode("utf-8")) <= 2048
        or not _unicode_safe(value)
    ):
        raise FinanceCollectionError("provider_response_invalid")


def _optional_dimension(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _DIMENSION.fullmatch(value) is None or not _unicode_safe(value):
        raise FinanceCollectionError("provider_response_invalid")
    return value


def _optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    if type(value) is not bool:
        raise FinanceCollectionError("provider_response_invalid")
    return value


def _currency(value: object) -> str:
    if not isinstance(value, str) or _CURRENCY.fullmatch(value) is None:
        raise FinanceCollectionError("provider_response_invalid")
    return value.upper()


def _quantity_unit(value: object, *, required: bool) -> str | None:
    if value is None:
        if required:
            raise FinanceCollectionError("provider_response_invalid")
        return None
    if not isinstance(value, str) or value not in _QUANTITY_UNITS:
        raise FinanceCollectionError("provider_response_invalid")
    return value


def _safe_id(value: object) -> bool:
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def _uuid_string(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        from uuid import UUID

        return str(UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def _hex(value: object, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in "0123456789abcdef" for character in value)
    )


def _sha(value: object) -> bool:
    return _hex(value, 64)


def _canonical_time(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return _time_text(_parse_time(value, response=True)) == value
    except FinanceCollectionError:
        return False


def _unicode_safe(value: str) -> bool:
    if "\x00" in value:
        return False
    try:
        value.encode("utf-8")
    except UnicodeError:
        return False
    return not any(0xD800 <= ord(character) <= 0xDFFF for character in value)


def _digest(value: object) -> str:
    try:
        payload = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise FinanceCollectionError("provider_response_invalid") from None
    return hashlib.sha256(payload).hexdigest()


def _approx_json_size(value: object) -> int:
    if value is None:
        return 4
    if value is True:
        return 4
    if value is False:
        return 5
    if type(value) is JsonNumber:
        return len(value.encode("utf-8"))
    if isinstance(value, str):
        return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
    if isinstance(value, list):
        return 2 + sum(_approx_json_size(item) for item in value) + max(0, len(value) - 1)
    if isinstance(value, Mapping):
        return 2 + sum(
            len(json.dumps(str(name), ensure_ascii=False).encode("utf-8"))
            + 1
            + _approx_json_size(item)
            for name, item in value.items()
        ) + max(0, len(value) - 1)
    raise FinanceCollectionError("provider_response_invalid")
