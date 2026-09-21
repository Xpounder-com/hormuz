"""Pure account-grain variance reference; no source authority or provider I/O.

Only a future authorized report builder may establish that durable attempt
bindings and selected provider observations belong to the same account grain.
Current gateway attempts lack that binding. Caller-supplied reference claims
here are not account-ownership proof, invoice facts, or a runtime report.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, DecimalException
import re

from .finance_collection import PROFILE_SPECS
from .finance_values import FinanceValueError, currency_code, decimal_text, exact_context


MAX_REFERENCE_ROWS = 10_000
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_UTC = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z\Z")


class FinanceVarianceReferenceError(ValueError):
    """Fixed, content-free error for a synthetic reference calculation."""

    def __init__(self, code: str):
        self.code = code if code == "finance_grain_not_comparable" else "finance_reference_invalid"
        super().__init__(self.code)


def _identifier(value: object) -> bool:
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def _digest(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _version(value: object) -> bool:
    return type(value) is int and 1 <= value <= 2_147_483_647


def _utc(value: object) -> datetime:
    if not isinstance(value, str) or _UTC.fullmatch(value) is None:
        raise FinanceVarianceReferenceError("finance_reference_invalid")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise FinanceVarianceReferenceError("finance_reference_invalid") from None


@dataclass(frozen=True)
class ComparableAccountGrain:
    """Exact caller-supplied coordinates, limited to provider-account grain.

    No team, actor, application, model, or token-weighted allocation is
    representable here. Equality is necessary but cannot authenticate a source.
    """

    organization_id: str
    provider: str
    provider_account_fingerprint: str
    fingerprint_key_version: int
    source_binding_id: str
    source_binding_version: int
    source_binding_digest: str
    scope_kind: str
    scope_fingerprints: tuple[str, ...]
    period_start_at: str
    period_end_at: str
    currency: str
    product: str
    collection_profile: str

    def __post_init__(self) -> None:
        try:
            valid_currency = self.currency == currency_code(self.currency)
        except FinanceValueError:
            valid_currency = False
        profile = PROFILE_SPECS.get(self.collection_profile) if isinstance(self.collection_profile, str) else None
        if (
            not _identifier(self.organization_id)
            or not isinstance(self.provider, str)
            or self.provider not in {"openai", "anthropic"}
            or not _digest(self.provider_account_fingerprint)
            or not _version(self.fingerprint_key_version)
            or not _identifier(self.source_binding_id)
            or not _version(self.source_binding_version)
            or not _digest(self.source_binding_digest)
            or not isinstance(self.scope_kind, str)
            or self.scope_kind not in {"organization", "projects", "workspaces"}
            or type(self.scope_fingerprints) is not tuple
            or len(self.scope_fingerprints) > 1000
            or any(not _digest(item) for item in self.scope_fingerprints)
            or tuple(sorted(set(self.scope_fingerprints))) != self.scope_fingerprints
            or (self.scope_kind == "organization") != (self.scope_fingerprints == ())
            or (self.provider == "openai" and self.scope_kind == "workspaces")
            or (self.provider == "anthropic" and self.scope_kind == "projects")
            or not valid_currency
            or not _identifier(self.product)
            or profile is None
            or profile.provider != self.provider
            or profile.source_kind != "cost"
            or _utc(self.period_start_at) >= _utc(self.period_end_at)
        ):
            raise FinanceVarianceReferenceError("finance_reference_invalid")


@dataclass(frozen=True)
class BoundGatewayScopeClaim:
    """An explicit operator-attested binding claim, not verification of it."""

    binding_id: str
    binding_version: int
    binding_digest: str
    source_binding_id: str
    source_binding_version: int
    source_binding_digest: str
    authority_basis: str = "operator_attested_unverified"

    def __post_init__(self) -> None:
        if (
            not _identifier(self.binding_id)
            or not _version(self.binding_version)
            or not _digest(self.binding_digest)
            or not _identifier(self.source_binding_id)
            or not _version(self.source_binding_version)
            or not _digest(self.source_binding_digest)
            or self.authority_basis != "operator_attested_unverified"
        ):
            raise FinanceVarianceReferenceError("finance_reference_invalid")


@dataclass(frozen=True)
class ProviderCostRow:
    snapshot_id: str
    observation_digest: str
    signed_amount: str


@dataclass(frozen=True)
class GatewayEstimateRow:
    attempt_id: str
    price_identity_digest: str
    configured_amount: str | None


@dataclass(frozen=True)
class ExactRelativeVariance:
    """Signed numerator over positive denominator, without decimal rounding."""

    numerator: str
    denominator: str


@dataclass(frozen=True)
class VarianceReference:
    provider_total: str
    configured_estimate_known_subtotal: str
    unpriced_attempt_count: int
    signed_variance: str
    absolute_variance: str
    relative_variance: ExactRelativeVariance | None
    supplied_attempt_pricing: str
    review_status: str
    provider_observation_keys: tuple[tuple[str, str], ...]
    gateway_attempt_ids: tuple[str, ...]
    gateway_price_identity_digests: tuple[str, ...]
    provider_cost_basis: str = "provider_reported_aggregate"
    gateway_cost_basis: str = "configured_rate_card_estimate"
    evidence_basis: str = "operator_attested_unverified_reference"


def calculate_reference_variance(
    *,
    provider_grain: ComparableAccountGrain,
    gateway_grain: ComparableAccountGrain | None,
    gateway_binding: BoundGatewayScopeClaim | None,
    provider_coverage: str,
    gateway_coverage: str,
    provider_rows: tuple[ProviderCostRow, ...],
    gateway_rows: tuple[GatewayEstimateRow, ...],
) -> VarianceReference:
    """Calculate exact variance only after explicit account-grain preconditions.

    A production caller must first authorize the tenant, pin one collection
    cutoff, verify every attempt's immutable binding, and establish complete
    comparable scope/time/product coverage. This function cannot do that work.
    An empty refresh is not an explicit zero provider total.
    """

    if (
        not isinstance(provider_grain, ComparableAccountGrain)
        or not isinstance(gateway_grain, ComparableAccountGrain)
        or not isinstance(gateway_binding, BoundGatewayScopeClaim)
        or provider_grain != gateway_grain
        or provider_coverage != "complete"
        or gateway_coverage != "complete"
        or gateway_binding.source_binding_id != gateway_grain.source_binding_id
        or gateway_binding.source_binding_version != gateway_grain.source_binding_version
        or gateway_binding.source_binding_digest != gateway_grain.source_binding_digest
    ):
        raise FinanceVarianceReferenceError("finance_grain_not_comparable")
    if (
        type(provider_rows) is not tuple
        or type(gateway_rows) is not tuple
        or not 1 <= len(provider_rows) <= MAX_REFERENCE_ROWS
        or len(gateway_rows) > MAX_REFERENCE_ROWS
    ):
        raise FinanceVarianceReferenceError("finance_reference_invalid")

    provider_keys: list[tuple[str, str]] = []
    attempt_ids: list[str] = []
    price_identity_digests: list[str] = []
    unpriced = 0
    try:
        with exact_context():
            provider_total = Decimal(0)
            for row in provider_rows:
                if (
                    not isinstance(row, ProviderCostRow)
                    or not _identifier(row.snapshot_id)
                    or not _digest(row.observation_digest)
                ):
                    raise FinanceVarianceReferenceError("finance_reference_invalid")
                provider_keys.append((row.snapshot_id, row.observation_digest))
                amount = decimal_text(row.signed_amount)
                if amount != row.signed_amount:
                    raise FinanceVarianceReferenceError("finance_reference_invalid")
                provider_total += Decimal(amount)

            estimate_subtotal = Decimal(0)
            for row in gateway_rows:
                if (
                    not isinstance(row, GatewayEstimateRow)
                    or not _identifier(row.attempt_id)
                    or not _digest(row.price_identity_digest)
                ):
                    raise FinanceVarianceReferenceError("finance_reference_invalid")
                attempt_ids.append(row.attempt_id)
                price_identity_digests.append(row.price_identity_digest)
                if row.configured_amount is None:
                    unpriced += 1
                    continue
                amount = decimal_text(row.configured_amount)
                if amount != row.configured_amount or Decimal(amount) < 0:
                    raise FinanceVarianceReferenceError("finance_reference_invalid")
                estimate_subtotal += Decimal(amount)
            signed = provider_total - estimate_subtotal
            provider_text = decimal_text(provider_total)
            estimate_text = decimal_text(estimate_subtotal)
            signed_text = decimal_text(signed)
            absolute_text = decimal_text(abs(signed))
    except (FinanceValueError, DecimalException):
        raise FinanceVarianceReferenceError("finance_reference_invalid") from None
    if (
        len({digest for _, digest in provider_keys}) != len(provider_keys)
        or len(set(attempt_ids)) != len(attempt_ids)
    ):
        raise FinanceVarianceReferenceError("finance_reference_invalid")
    relative = None if estimate_subtotal.is_zero() else ExactRelativeVariance(
        signed_text, decimal_text(abs(estimate_subtotal)),
    )
    return VarianceReference(
        provider_total=provider_text,
        configured_estimate_known_subtotal=estimate_text,
        unpriced_attempt_count=unpriced,
        signed_variance=signed_text,
        absolute_variance=absolute_text,
        relative_variance=relative,
        supplied_attempt_pricing=(
            "no_gateway_attempts" if not gateway_rows else "incomplete" if unpriced else "complete"
        ),
        review_status="not_evaluated",
        provider_observation_keys=tuple(provider_keys),
        gateway_attempt_ids=tuple(attempt_ids),
        gateway_price_identity_digests=tuple(price_identity_digests),
    )
