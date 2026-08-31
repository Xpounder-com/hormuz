"""Metadata-only internal outcome contracts; no provider authentication here.

Adapters must select verified source identifiers, never encode work content in
an ID. The closed public event/page/receipt remain the approved wire contracts.
Only authenticated source bytes may enter this decoder. Nothing retains them.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
import hashlib
import hmac
import json
import math
import re
from types import MappingProxyType
from typing import Mapping

from .portfolio_config import PortfolioConnectorBinding
from .portfolio_wire import PortfolioError, canonical, validate


REQUEST_BYTES = 1048576
JSON_DEPTH = 16
JSON_MEMBERS = 4096
EVENTS_PER_DELIVERY = 100
DEAD_LETTER_BYTES = 2048
ORDERING_DOMAINS = frozenset({"source_revision_counter_v1", "source_updated_at_v1"})
_UUID = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
_SOURCE_ID = re.compile(rf"(?:[1-9][0-9]{{0,19}}|{_UUID}|[0-9a-f]{{32}}|[0-9a-f]{{64}})(?::(?:0|[1-9][0-9]?))?\Z")


def source_id(value: object) -> None:
    if not isinstance(value, str) or not _SOURCE_ID.fullmatch(value):
        raise PortfolioError("invalid_request")


def timestamp(value: str) -> str:
    validate(value, "timestamp")
    return datetime.fromisoformat(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def decode_source_body(data: bytes) -> dict:
    """Reject ambiguous or oversized JSON, without reflecting rejected values."""
    if type(data) is not bytes or not 1 <= len(data) <= REQUEST_BYTES:
        raise PortfolioError("invalid_request")
    # Bound nesting before the decoder allocates a deeply recursive object.
    depth, quoted, escaped = 0, False, False
    for character in data:
        if quoted:
            if escaped:
                escaped = False
            elif character == 92:
                escaped = True
            elif character == 34:
                quoted = False
        elif character == 34:
            quoted = True
        elif character in (91, 123):
            depth += 1
            if depth > JSON_DEPTH:
                raise PortfolioError("invalid_request")
        elif character in (93, 125):
            depth -= 1

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise PortfolioError("invalid_request")
            result[key] = value
        return result

    def nonfinite(_value):
        raise PortfolioError("invalid_request")

    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=pairs, parse_constant=nonfinite)
    except (ValueError, UnicodeError, RecursionError):
        raise PortfolioError("invalid_request") from None
    if not isinstance(value, dict):
        raise PortfolioError("invalid_request")
    pending, members = [(value, 1)], 0
    while pending:
        item, depth = pending.pop()
        if depth > JSON_DEPTH or (isinstance(item, float) and not math.isfinite(item)):
            raise PortfolioError("invalid_request")
        if isinstance(item, dict):
            members += len(item)
            pending.extend((key, depth + 1) for key in item)
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            members += len(item)
            pending.extend((child, depth + 1) for child in item)
        elif isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeError:
                raise PortfolioError("invalid_request") from None
        if members > JSON_MEMBERS:
            raise PortfolioError("invalid_request")
    return value


@dataclass(frozen=True)
class SourceObservation:
    schema_id: str
    schema_version: int
    source_event_id: str
    external_object_id: str
    container_id: str
    source_revision: str | None
    ordering_domain: str | None
    revision_order: str | None
    object_type: str
    event_type: str
    quality_state: str
    duration_ms: str | None
    state: str
    supersedes_source_event_id: str | None
    reason_code: str
    event_at: str | None


def observation_from_mapping(value: object, binding: PortfolioConnectorBinding) -> SourceObservation:
    if not isinstance(value, dict) or set(value) != {item.name for item in fields(SourceObservation)}:
        raise PortfolioError("invalid_request")
    if value["schema_id"] != "hormuz.source-outcome-observation" or type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise PortfolioError("invalid_request")
    source_id(value["source_event_id"])
    object_pattern = r"[1-9][0-9]{0,19}" if binding.provider == "github" else _UUID if binding.provider == "linear" else None
    if object_pattern is None:
        raise PortfolioError("forbidden")
    for name in ("external_object_id", "container_id"):
        if not isinstance(value[name], str) or not re.fullmatch(object_pattern, value[name]):
            raise PortfolioError("invalid_request")
    if value["container_id"] not in binding.external_object_ids:
        raise PortfolioError("forbidden")
    revision, domain, order = (value[name] for name in ("source_revision", "ordering_domain", "revision_order"))
    if revision is not None and revision != "0":
        try:
            source_id(revision)
        except PortfolioError:
            timestamp(revision)
    if (domain is None) != (order is None):
        raise PortfolioError("invalid_request")
    if domain is not None:
        if not isinstance(domain, str) or domain not in ORDERING_DOMAINS or revision is None:
            raise PortfolioError("invalid_request")
        if not isinstance(order, str) or not re.fullmatch(r"(?:0|[1-9][0-9]{0,18})", order) or int(order) > 9223372036854775807:
            raise PortfolioError("invalid_request")
        if domain == "source_revision_counter_v1" and revision != order:
            raise PortfolioError("invalid_request")
        if domain == "source_updated_at_v1":
            instant = datetime.fromisoformat(timestamp(revision))
            difference = instant - datetime(1970, 1, 1, tzinfo=timezone.utc)
            if difference.days * 86400000000 + difference.seconds * 1000000 + difference.microseconds != int(order):
                raise PortfolioError("invalid_request")
    prior, state, reason = (value[name] for name in ("supersedes_source_event_id", "state", "reason_code"))
    if prior is not None:
        source_id(prior)
        if prior == value["source_event_id"]:
            raise PortfolioError("invalid_request")
    if not isinstance(reason, str) or reason not in {"observed", "corrected", "superseded", "tombstoned", "unsupported", "missing_evidence"}:
        raise PortfolioError("invalid_request")
    if (reason in {"corrected", "superseded"} and prior is None) or (prior is not None and reason not in {"corrected", "superseded", "tombstoned"}):
        raise PortfolioError("invalid_request")
    if (state == "tombstoned") != (reason == "tombstoned") or (state == "superseded") != (reason == "superseded"):
        raise PortfolioError("invalid_request")
    if state == "tombstoned" and (value["event_type"] != "deleted" or value["duration_ms"] is not None):
        raise PortfolioError("invalid_request")
    event_time = timestamp(value["event_at"]) if value["event_at"] is not None else "1970-01-01T00:00:00.000000Z"
    # Reuse the unchanged public contract for shared fields; internal authority,
    # ordering, and source-time uncertainty are never added to that envelope.
    validate({
        "schema_id": "hormuz.work-outcome-event", "schema_version": 1,
        "organization_id": binding.organization_id, "connector_id": binding.connector_id,
        "source_delivery_id": "1", "provenance_digest": "0" * 64,
        **{name: value[name] for name in (
            "source_event_id", "external_object_id", "source_revision", "object_type", "event_type",
            "quality_state", "duration_ms", "state", "supersedes_source_event_id", "reason_code",
        )},
        "evidence_level": "descriptive", "event_at": event_time,
        "observed_at": event_time, "ingested_at": event_time,
    }, "hormuz.work-outcome-event")
    return SourceObservation(**value)


CONTEXT_FIELDS = frozenset({
    "schema_id", "schema_version", "organization_id", "connector_id", "source_event_id",
    "provider", "authority_id", "source_container_id", "actor_id", "authentication_kind",
    "work_scope_id", "work_scope_version", "binding_event_id", "registry_sequence",
    "key_version", "credential_version", "source_time_known", "ordering_domain",
    "revision_order", "ordering_state", "scope_state",
})


def validate_context(value: object) -> None:
    if not isinstance(value, dict) or set(value) != CONTEXT_FIELDS:
        raise PortfolioError("invalid_request")
    if value["schema_id"] != "hormuz.outcome-observation-context" or type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise PortfolioError("invalid_request")
    for name in ("organization_id", "connector_id", "key_version", "credential_version"):
        validate(value[name], "opaque_id")
    source_id(value["source_event_id"])
    if value["provider"] not in ("github", "linear"):
        raise PortfolioError("invalid_request")
    pattern = r"[1-9][0-9]{0,19}" if value["provider"] == "github" else _UUID
    for name in ("authority_id", "source_container_id"):
        if not isinstance(value[name], str) or not re.fullmatch(pattern, value[name]):
            raise PortfolioError("invalid_request")
    if value["authentication_kind"] not in ("verified_connector", "authorized_retention"):
        raise PortfolioError("invalid_request")
    if (value["authentication_kind"] == "verified_connector") != (value["actor_id"] is None):
        raise PortfolioError("invalid_request")
    for name in ("actor_id", "work_scope_id", "binding_event_id"):
        if value[name] is not None:
            validate(value[name], "opaque_id")
    if (value["work_scope_id"] is None) != (value["work_scope_version"] is None):
        raise PortfolioError("invalid_request")
    if value["work_scope_version"] is not None:
        validate(value["work_scope_version"], "version")
    if type(value["registry_sequence"]) is not int or not 0 <= value["registry_sequence"] <= 9223372036854775807:
        raise PortfolioError("invalid_request")
    if type(value["source_time_known"]) is not int or value["source_time_known"] not in (0, 1):
        raise PortfolioError("invalid_request")
    if value["ordering_domain"] is not None and value["ordering_domain"] not in tuple(ORDERING_DOMAINS):
        raise PortfolioError("invalid_request")
    if (value["ordering_domain"] is None) != (value["revision_order"] is None):
        raise PortfolioError("invalid_request")
    if value["revision_order"] is not None and (type(value["revision_order"]) is not int or not 0 <= value["revision_order"] <= 9223372036854775807):
        raise PortfolioError("invalid_request")
    if value["ordering_state"] not in ("authoritative", "late", "uncertain", "superseded", "tombstoned"):
        raise PortfolioError("invalid_request")
    if value["scope_state"] not in ("matched", "unmatched", "ambiguous", "excluded"):
        raise PortfolioError("invalid_request")
    if value["scope_state"] == "matched" and (value["work_scope_id"] is None or value["binding_event_id"] is None or not value["source_time_known"]):
        raise PortfolioError("invalid_request")


COVERAGE_FIELDS = frozenset({
    "schema_id", "schema_version", "organization_id", "coverage_event_id", "connector_id",
    "source_delivery_id", "source_event_id", "state", "reason_code", "eligibility_state",
    "rule_id", "rule_version", "member_count", "member_unit", "ingested_at", "sequence",
})


def validate_coverage(value: object) -> None:
    if not isinstance(value, dict) or set(value) != COVERAGE_FIELDS:
        raise PortfolioError("invalid_request")
    if value["schema_id"] != "hormuz.outcome-coverage-event" or type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise PortfolioError("invalid_request")
    for name in ("organization_id", "coverage_event_id", "connector_id"):
        validate(value[name], "opaque_id")
    source_id(value["source_delivery_id"])
    if value["source_event_id"] is not None:
        source_id(value["source_event_id"])
    if value["state"] not in ("observed", "unmatched", "ambiguous", "late", "excluded", "superseded", "unsupported", "failed"):
        raise PortfolioError("invalid_request")
    if value["reason_code"] not in (
        "observed", "unmatched", "ambiguous", "excluded", "superseded", "unsupported",
        "missing_evidence", "tombstoned", "invalid_shape", "unauthorized_scope",
        "conflicting_identity", "dependency_unavailable",
    ):
        raise PortfolioError("invalid_request")
    if value["eligibility_state"] != "inconclusive" or value["rule_id"] is not None or value["rule_version"] is not None:
        raise PortfolioError("invalid_request")
    if type(value["member_count"]) is not int or not 1 <= value["member_count"] <= 100:
        raise PortfolioError("invalid_request")
    if value["member_unit"] not in ("source_event", "delivery") or (value["member_unit"] == "source_event") != (value["source_event_id"] is not None):
        raise PortfolioError("invalid_request")
    if type(value["sequence"]) is not int or not 1 <= value["sequence"] <= 9223372036854775807:
        raise PortfolioError("invalid_request")
    timestamp(value["ingested_at"])


def validate_retention(value: object) -> None:
    required = {
        "schema_id", "schema_version", "organization_id", "retention_event_id", "connector_id",
        "source_event_id", "actor_id", "reason_code", "event_at", "observed_at", "ingested_at",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise PortfolioError("invalid_request")
    if value["schema_id"] != "hormuz.outcome-retention-event" or type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise PortfolioError("invalid_request")
    for name in ("organization_id", "retention_event_id", "connector_id", "actor_id"):
        validate(value[name], "opaque_id")
    source_id(value["source_event_id"])
    if value["reason_code"] != "tombstoned":
        raise PortfolioError("invalid_request")
    for name in ("event_at", "observed_at", "ingested_at"):
        timestamp(value[name])


@dataclass(frozen=True, init=False)
class OutcomeKeys:
    """Injected tenant-scoped key versions, not configuration or secret storage.

    Registered adapters supply keys through their separately gated credential
    boundary. Keep old versions for the replay/retention horizon. Losing an old
    key fails closed; rotation must not silently reinterpret a previous delivery.
    """

    current_version: str
    _material: Mapping[str, bytes] = field(repr=False)

    def __init__(self, current_version: str, material: Mapping[str, bytes]):
        validate(current_version, "opaque_id")
        if not isinstance(material, Mapping) or not 1 <= len(material) <= 100 or current_version not in material:
            raise PortfolioError("invalid_request")
        for version, key in material.items():
            validate(version, "opaque_id")
            if type(key) is not bytes or not 32 <= len(key) <= 128:
                raise PortfolioError("invalid_request")
        object.__setattr__(self, "current_version", current_version)
        object.__setattr__(self, "_material", MappingProxyType(dict(material)))

    def _digest(self, version: str, domain: str, organization: str, connector: str, identity: str, payload: bytes) -> str:
        key = self._material.get(version)
        if key is None:
            raise PortfolioError("unavailable")
        header = canonical([domain, version, organization, connector, identity]).encode("ascii")
        return hmac.new(key, len(header).to_bytes(4, "big") + header + payload, hashlib.sha256).hexdigest()

    def delivery_digest(self, version: str, organization: str, connector: str, delivery: str, payload: bytes) -> str:
        if type(payload) is not bytes or len(payload) > REQUEST_BYTES:
            raise PortfolioError("invalid_request")
        return self._digest(version, "outcome-delivery-v1", organization, connector, delivery, payload)

    def metadata_digest(self, version: str, organization: str, connector: str, identity: str, metadata: object) -> str:
        return self._digest(version, "outcome-provenance-v1", organization, connector, identity, canonical(metadata).encode("ascii"))
