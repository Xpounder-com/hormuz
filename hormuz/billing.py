from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
from typing import Mapping, Sequence


MAX_REPORT_PAGES = 1_000
MAX_REPORT_BUCKETS = 50_000
MAX_REPORT_ITEMS = 500_000
MAX_REPORT_PAGE_BYTES = 16 * 1024 * 1024
MAX_REPORT_TOTAL_BYTES = 32 * 1024 * 1024
MAX_AMOUNT_USD = Decimal("1000000000000")
MAX_AMOUNT_DECIMAL_PLACES = 12
MAX_RECONCILIATION_AMOUNT_USD = MAX_AMOUNT_USD * MAX_REPORT_ITEMS

_AUTHENTICATED_SOURCE_CONTRACTS = {
    "openai": (
        "openai.organization.costs.v1",
        "organization_all_projects_line_items",
    ),
    "anthropic": (
        "anthropic.organization.cost_report.2023-06-01",
        "organization_all_workspaces_descriptions",
    ),
}


class ProviderBillingError(ValueError):
    pass


@dataclass(frozen=True)
class BillingReconciliationPolicy:
    """Exact, versioned thresholds for aggregate provider reconciliation."""

    enabled: bool = False
    policy_version: str = "billing-reconciliation-disabled-v1"
    max_absolute_variance_usd: Decimal | None = None
    max_variance_basis_points: int | None = None
    max_unpriced_requests: int | None = None
    max_legacy_unattributed_requests: int | None = None
    max_unscoped_provider_items: int | None = None
    require_authenticated_source: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ValueError("Billing reconciliation enabled must be a boolean")
        if (
            not isinstance(self.policy_version, str)
            or not self.policy_version.strip()
            or self.policy_version != self.policy_version.strip()
            or len(self.policy_version.encode("utf-8")) > 128
            or any(character in self.policy_version for character in ("\n", "\r", "\x00"))
        ):
            raise ValueError(
                "Billing reconciliation policy_version must be a bounded single-line string"
            )
        if self.max_absolute_variance_usd is not None:
            value = self.max_absolute_variance_usd
            if (
                not isinstance(value, Decimal)
                or not value.is_finite()
                or value < 0
                or value > MAX_RECONCILIATION_AMOUNT_USD
                or value.as_tuple().exponent < -MAX_AMOUNT_DECIMAL_PLACES
            ):
                raise ValueError(
                    "Billing reconciliation max_absolute_variance_usd is invalid"
                )
        for name, value, maximum in (
            ("max_variance_basis_points", self.max_variance_basis_points, 1_000_000_000),
            ("max_unpriced_requests", self.max_unpriced_requests, 1_000_000_000),
            (
                "max_legacy_unattributed_requests",
                self.max_legacy_unattributed_requests,
                1_000_000_000,
            ),
            ("max_unscoped_provider_items", self.max_unscoped_provider_items, 500_000),
        ):
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= maximum
            ):
                raise ValueError(f"Billing reconciliation {name} is invalid")
        if not isinstance(self.require_authenticated_source, bool):
            raise ValueError(
                "Billing reconciliation require_authenticated_source must be a boolean"
            )
        if self.enabled and not any(
            (
                self.max_absolute_variance_usd is not None,
                self.max_variance_basis_points is not None,
                self.max_unpriced_requests is not None,
                self.max_legacy_unattributed_requests is not None,
                self.max_unscoped_provider_items is not None,
                self.require_authenticated_source,
            )
        ):
            raise ValueError(
                "Enabled billing reconciliation policy must contain at least one rule"
            )

    @property
    def policy_sha256(self) -> str:
        canonical = json.dumps(
            self.to_dict(include_digest=False),
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def to_dict(self, *, include_digest: bool = True) -> dict[str, object]:
        value: dict[str, object] = {
            "enabled": self.enabled,
            "policy_version": self.policy_version,
            "max_absolute_variance_usd": (
                None
                if self.max_absolute_variance_usd is None
                else _decimal_text(self.max_absolute_variance_usd)
            ),
            "max_variance_basis_points": self.max_variance_basis_points,
            "max_unpriced_requests": self.max_unpriced_requests,
            "max_legacy_unattributed_requests": self.max_legacy_unattributed_requests,
            "max_unscoped_provider_items": self.max_unscoped_provider_items,
            "require_authenticated_source": self.require_authenticated_source,
        }
        if include_digest:
            value["policy_sha256"] = self.policy_sha256
        return value


@dataclass(frozen=True)
class ProviderCostItem:
    bucket_start: str
    bucket_end: str
    amount_usd: str
    currency: str
    provider_scope_kind: str
    provider_scope_id: str | None
    line_item: str | None = None
    cost_type: str | None = None
    model: str | None = None
    service_tier: str | None = None
    token_type: str | None = None
    context_window: str | None = None
    inference_geo: str | None = None


@dataclass(frozen=True)
class ProviderCostReport:
    provider: str
    source_sha256: str
    page_count: int
    bucket_count: int
    report_start: str
    report_end: str
    items: tuple[ProviderCostItem, ...]


@dataclass(frozen=True)
class ProviderCostSource:
    kind: str
    api_contract: str | None = None
    query_start: str | None = None
    query_end: str | None = None
    query_scope: str | None = None

    @classmethod
    def offline(cls) -> ProviderCostSource:
        return cls(kind="offline_upload")

    @classmethod
    def authenticated(
        cls,
        *,
        provider: str,
        query_start: str,
        query_end: str,
    ) -> ProviderCostSource:
        contract = _AUTHENTICATED_SOURCE_CONTRACTS.get(provider)
        if contract is None:
            raise ProviderBillingError("Provider must be openai or anthropic")
        return cls(
            kind="authenticated_api",
            api_contract=contract[0],
            query_start=_rfc3339(query_start, "Provider query start"),
            query_end=_rfc3339(query_end, "Provider query end"),
            query_scope=contract[1],
        )


def evaluate_reconciliation(
    reconciliation: Mapping[str, object],
    policy: BillingReconciliationPolicy,
) -> dict[str, object]:
    """Apply exact thresholds without converting aggregate cost into chargeback."""

    if (
        not isinstance(reconciliation, Mapping)
        or isinstance(reconciliation.get("schema_version"), bool)
        or reconciliation.get("schema_version") != 1
    ):
        raise ProviderBillingError("Unsupported billing reconciliation schema")
    if not isinstance(policy, BillingReconciliationPolicy):
        raise ProviderBillingError("Billing reconciliation policy is required")
    provider_cost = _reconciliation_decimal(reconciliation, "provider_cost_usd")
    gateway_estimated_cost = _reconciliation_decimal(
        reconciliation,
        "gateway_estimated_cost_usd",
    )
    if gateway_estimated_cost < 0:
        raise ProviderBillingError("Billing reconciliation facts are invalid")
    reported_variance = _reconciliation_decimal(reconciliation, "variance_usd")
    with localcontext() as context:
        context.prec = 80
        variance = provider_cost - gateway_estimated_cost
        absolute_variance = abs(variance)
        provider_cost_denominator = abs(provider_cost)
        relative_numerator = absolute_variance * Decimal(10_000)
        if provider_cost_denominator == 0:
            variance_basis_points = Decimal(0) if absolute_variance == 0 else None
        else:
            variance_basis_points = relative_numerator / provider_cost_denominator
        relative_limit = (
            None
            if policy.max_variance_basis_points is None
            else provider_cost_denominator * policy.max_variance_basis_points
        )
    if reported_variance != variance:
        raise ProviderBillingError("Billing reconciliation facts are inconsistent")
    source_kind = reconciliation.get("provider_source_kind")
    if source_kind not in {"offline_upload", "authenticated_api"}:
        raise ProviderBillingError("Billing reconciliation facts are invalid")
    counts = {
        field: _reconciliation_count(reconciliation, field)
        for field in (
            "gateway_unpriced_requests",
            "legacy_unattributed_gateway_requests",
            "unscoped_provider_items",
        )
    }
    reasons: list[str] = []
    if policy.enabled:
        if (
            policy.require_authenticated_source
            and source_kind != "authenticated_api"
        ):
            reasons.append("provider_source_not_authenticated")
        if (
            policy.max_absolute_variance_usd is not None
            and absolute_variance > policy.max_absolute_variance_usd
        ):
            reasons.append("absolute_variance_exceeded")
        if policy.max_variance_basis_points is not None:
            if variance_basis_points is None:
                reasons.append("variance_basis_unavailable")
            elif relative_limit is not None and relative_numerator > relative_limit:
                reasons.append("relative_variance_exceeded")
        for field, threshold, reason in (
            (
                "gateway_unpriced_requests",
                policy.max_unpriced_requests,
                "unpriced_requests_exceeded",
            ),
            (
                "legacy_unattributed_gateway_requests",
                policy.max_legacy_unattributed_requests,
                "legacy_unattributed_requests_exceeded",
            ),
            (
                "unscoped_provider_items",
                policy.max_unscoped_provider_items,
                "unscoped_provider_items_exceeded",
            ),
        ):
            if threshold is not None and counts[field] > threshold:
                reasons.append(reason)

    result = dict(reconciliation)
    result.update(
        {
            "schema_version": 2,
            "variance_absolute_usd": _decimal_text(absolute_variance),
            "variance_basis_points": (
                None
                if variance_basis_points is None
                else _decimal_text(variance_basis_points)
            ),
            "variance_basis": "absolute_provider_reported_cost",
            "exception_status": (
                "not_evaluated"
                if not policy.enabled
                else "review_required"
                if reasons
                else "clear"
            ),
            "exception_reasons": reasons,
            "reconciliation_policy": policy.to_dict(),
        }
    )
    return result


def decode_provider_cost_page(payload: bytes) -> dict[str, object]:
    if not isinstance(payload, bytes):
        raise ProviderBillingError("Billing input page must be bytes")
    if len(payload) > MAX_REPORT_PAGE_BYTES:
        raise ProviderBillingError("Billing input page cannot exceed 16 MiB")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ProviderBillingError("Billing input page must be UTF-8 JSON") from error
    try:
        value = json.loads(
            text,
            parse_float=Decimal,
            parse_constant=_invalid_json_constant,
            object_pairs_hook=_unique_json_object,
        )
    except json.JSONDecodeError as error:
        raise ProviderBillingError(
            f"Billing input page must be valid JSON at line {error.lineno}, column {error.colno}"
        ) from error
    except RecursionError as error:
        raise ProviderBillingError("Billing input page exceeds the supported JSON depth") from error
    if not isinstance(value, dict):
        raise ProviderBillingError("Billing input page must be a JSON object")
    return value


def parse_provider_cost_pages(
    provider: str,
    pages: Sequence[Mapping[str, object]],
    *,
    expected_start: str | None = None,
    expected_end: str | None = None,
) -> ProviderCostReport:
    if provider not in {"openai", "anthropic"}:
        raise ProviderBillingError("Provider must be openai or anthropic")
    if not isinstance(pages, Sequence) or isinstance(pages, (str, bytes)):
        raise ProviderBillingError("Provider cost pages must be a sequence")
    if not 1 <= len(pages) <= MAX_REPORT_PAGES:
        raise ProviderBillingError(
            f"Provider cost report must contain 1 to {MAX_REPORT_PAGES} pages"
        )
    if (expected_start is None) != (expected_end is None):
        raise ProviderBillingError("Provider cost query bounds must be supplied together")
    normalized_expected_start: str | None = None
    normalized_expected_end: str | None = None
    if expected_start is not None and expected_end is not None:
        normalized_expected_start = _rfc3339(expected_start, "Provider query start")
        normalized_expected_end = _rfc3339(expected_end, "Provider query end")
        if normalized_expected_end <= normalized_expected_start:
            raise ProviderBillingError("Provider cost query end must be after its start")

    items: list[ProviderCostItem] = []
    bucket_bounds: list[tuple[str, str]] = []
    page_signatures: set[str] = set()
    bucket_count = 0
    for page_index, page in enumerate(pages):
        if not isinstance(page, Mapping):
            raise ProviderBillingError("Provider cost page must be a JSON object")
        _validate_pagination(page, page_index=page_index, page_count=len(pages))
        data = page.get("data")
        if not isinstance(data, list):
            raise ProviderBillingError("Provider cost page data must be an array")
        if provider == "openai" and page.get("object") != "page":
            raise ProviderBillingError("OpenAI cost report object must be page")
        page_items: list[ProviderCostItem] = []
        page_bounds: list[tuple[str, str]] = []
        for bucket in data:
            bucket_count += 1
            if bucket_count > MAX_REPORT_BUCKETS:
                raise ProviderBillingError("Provider cost report has too many buckets")
            if not isinstance(bucket, Mapping):
                raise ProviderBillingError("Provider cost bucket must be a JSON object")
            if provider == "openai":
                parsed_items, bounds = _parse_openai_bucket(bucket)
            else:
                parsed_items, bounds = _parse_anthropic_bucket(bucket)
            if len(items) + len(page_items) + len(parsed_items) > MAX_REPORT_ITEMS:
                raise ProviderBillingError("Provider cost report has too many items")
            page_items.extend(parsed_items)
            page_bounds.append(bounds)
        page_signature = json.dumps(
            {
                "buckets": page_bounds,
                "items": [asdict(item) for item in page_items],
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        if page_signature in page_signatures:
            raise ProviderBillingError("Provider cost pagination contains a duplicate page")
        page_signatures.add(page_signature)
        items.extend(page_items)
        bucket_bounds.extend(page_bounds)

    if not bucket_bounds and normalized_expected_start is None:
        raise ProviderBillingError("Provider cost report must contain at least one bucket")
    if normalized_expected_start is not None and normalized_expected_end is not None:
        if any(
            start < normalized_expected_start or end > normalized_expected_end
            for start, end in bucket_bounds
        ):
            raise ProviderBillingError("Provider cost bucket is outside the authenticated query window")
        report_start = normalized_expected_start
        report_end = normalized_expected_end
    else:
        report_start = min(start for start, _ in bucket_bounds)
        report_end = max(end for _, end in bucket_bounds)
    canonical_items = sorted(
        (asdict(item) for item in items),
        key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")),
    )
    canonical = json.dumps(
        {
            "schema_version": 1,
            "provider": provider,
            "buckets": sorted(
                ({"start": start, "end": end} for start, end in bucket_bounds),
                key=lambda bucket: (bucket["start"], bucket["end"]),
            ),
            "report_start": report_start,
            "report_end": report_end,
            "items": canonical_items,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return ProviderCostReport(
        provider=provider,
        source_sha256=hashlib.sha256(canonical).hexdigest(),
        page_count=len(pages),
        bucket_count=bucket_count,
        report_start=report_start,
        report_end=report_end,
        items=tuple(items),
    )


def _validate_pagination(
    page: Mapping[str, object],
    *,
    page_index: int,
    page_count: int,
) -> None:
    has_more = page.get("has_more")
    next_page = page.get("next_page")
    if not isinstance(has_more, bool):
        raise ProviderBillingError("Provider cost pagination has_more must be boolean")
    is_last = page_index == page_count - 1
    if not is_last:
        if not has_more or not _optional_string(next_page, "next_page", 2_048):
            raise ProviderBillingError("Provider cost pagination is inconsistent")
        return
    if has_more:
        raise ProviderBillingError(
            "Provider cost report is incomplete; supply every page through has_more=false"
        )
    if next_page is not None:
        raise ProviderBillingError("Provider cost pagination is inconsistent")


def _parse_openai_bucket(
    bucket: Mapping[str, object],
) -> tuple[list[ProviderCostItem], tuple[str, str]]:
    if bucket.get("object") != "bucket":
        raise ProviderBillingError("OpenAI cost bucket object must be bucket")
    start = _unix_timestamp(bucket.get("start_time"), "OpenAI bucket start_time")
    end = _unix_timestamp(bucket.get("end_time"), "OpenAI bucket end_time")
    _validate_bucket(start, end)
    results = bucket.get("results")
    if not isinstance(results, list):
        raise ProviderBillingError("OpenAI cost bucket results must be an array")
    parsed: list[ProviderCostItem] = []
    for result in results:
        if not isinstance(result, Mapping):
            raise ProviderBillingError("OpenAI cost result must be a JSON object")
        if result.get("object") != "organization.costs.result":
            raise ProviderBillingError("OpenAI cost result object is unsupported")
        amount = result.get("amount")
        if not isinstance(amount, Mapping):
            raise ProviderBillingError("OpenAI cost amount must be an object")
        value = amount.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
            raise ProviderBillingError("OpenAI cost amount value must be a JSON number")
        currency = _currency(amount.get("currency"))
        project_id = _optional_string(result.get("project_id"), "project_id", 256)
        parsed.append(
            ProviderCostItem(
                bucket_start=start,
                bucket_end=end,
                amount_usd=_amount_usd(Decimal(value)),
                currency=currency,
                provider_scope_kind="project" if project_id is not None else "unscoped",
                provider_scope_id=project_id,
                line_item=_optional_string(result.get("line_item"), "line_item", 512),
            )
        )
    return parsed, (start, end)


def _parse_anthropic_bucket(
    bucket: Mapping[str, object],
) -> tuple[list[ProviderCostItem], tuple[str, str]]:
    start = _rfc3339(bucket.get("starting_at"), "Anthropic bucket starting_at")
    end = _rfc3339(bucket.get("ending_at"), "Anthropic bucket ending_at")
    _validate_bucket(start, end)
    results = bucket.get("results")
    if not isinstance(results, list):
        raise ProviderBillingError("Anthropic cost bucket results must be an array")
    parsed: list[ProviderCostItem] = []
    for result in results:
        if not isinstance(result, Mapping):
            raise ProviderBillingError("Anthropic cost result must be a JSON object")
        raw_amount = result.get("amount")
        if not isinstance(raw_amount, str):
            raise ProviderBillingError("Anthropic cost amount must be a decimal string")
        try:
            amount_usd = Decimal(raw_amount) / Decimal(100)
        except InvalidOperation as error:
            raise ProviderBillingError("Anthropic cost amount is invalid") from error
        currency = _currency(result.get("currency"))
        workspace_id = _optional_string(result.get("workspace_id"), "workspace_id", 256)
        parsed.append(
            ProviderCostItem(
                bucket_start=start,
                bucket_end=end,
                amount_usd=_amount_usd(amount_usd),
                currency=currency,
                provider_scope_kind="workspace" if workspace_id is not None else "unscoped",
                provider_scope_id=workspace_id,
                line_item=_optional_string(result.get("description"), "description", 512),
                cost_type=_optional_string(result.get("cost_type"), "cost_type", 128),
                model=_optional_string(result.get("model"), "model", 256),
                service_tier=_optional_string(result.get("service_tier"), "service_tier", 128),
                token_type=_optional_string(result.get("token_type"), "token_type", 128),
                context_window=_optional_string(result.get("context_window"), "context_window", 128),
                inference_geo=_optional_string(result.get("inference_geo"), "inference_geo", 128),
            )
        )
    return parsed, (start, end)


def _amount_usd(value: Decimal) -> str:
    if not value.is_finite():
        raise ProviderBillingError("Provider cost amount must be finite")
    if abs(value) > MAX_AMOUNT_USD:
        raise ProviderBillingError("Provider cost amount exceeds the supported bound")
    exponent = value.as_tuple().exponent
    if exponent < -MAX_AMOUNT_DECIMAL_PLACES:
        raise ProviderBillingError(
            f"Provider cost amount supports at most {MAX_AMOUNT_DECIMAL_PLACES} decimal places in USD"
        )
    if value == 0:
        return "0"
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise ProviderBillingError("Billing reconciliation value must be finite")
    if value == 0:
        return "0"
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _reconciliation_decimal(
    reconciliation: Mapping[str, object],
    field: str,
) -> Decimal:
    value = reconciliation.get(field)
    if not isinstance(value, str):
        raise ProviderBillingError("Billing reconciliation facts are invalid")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise ProviderBillingError("Billing reconciliation facts are invalid") from error
    if (
        not parsed.is_finite()
        or abs(parsed) > MAX_RECONCILIATION_AMOUNT_USD
        or parsed.as_tuple().exponent < -MAX_AMOUNT_DECIMAL_PLACES
    ):
        raise ProviderBillingError("Billing reconciliation facts are invalid")
    return parsed


def _reconciliation_count(
    reconciliation: Mapping[str, object],
    field: str,
) -> int:
    value = reconciliation.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProviderBillingError("Billing reconciliation facts are invalid")
    return value


def _currency(value: object) -> str:
    if not isinstance(value, str) or value.upper() != "USD":
        raise ProviderBillingError("Provider cost currency must be USD")
    return "USD"


def _optional_string(value: object, label: str, maximum_bytes: int) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > maximum_bytes
        or any(character in value for character in ("\n", "\r", "\x00"))
    ):
        raise ProviderBillingError(f"Provider cost {label} must be a bounded single-line string")
    return value


def _unix_timestamp(value: object, label: str) -> str:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProviderBillingError(f"{label} must be an integer Unix timestamp")
    try:
        parsed = datetime.fromtimestamp(value, timezone.utc)
    except (OverflowError, OSError, ValueError) as error:
        raise ProviderBillingError(f"{label} is outside the supported range") from error
    return parsed.isoformat()


def _rfc3339(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ProviderBillingError(f"{label} must be an RFC 3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ProviderBillingError(f"{label} must be an RFC 3339 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProviderBillingError(f"{label} must include an offset")
    return parsed.astimezone(timezone.utc).isoformat()


def _validate_bucket(start: str, end: str) -> None:
    if end <= start:
        raise ProviderBillingError("Provider cost bucket end must be after its start")


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ProviderBillingError("Billing input contains a duplicate JSON object member")
        result[key] = value
    return result


def _invalid_json_constant(_value: str) -> object:
    raise ProviderBillingError("Billing input contains a non-standard JSON numeric constant")
