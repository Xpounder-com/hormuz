"""Authenticated, metadata-only Linear reconciliation snapshot ingestion."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import hmac
import re
import threading
import time
from types import MappingProxyType
from typing import Mapping

from .linear_connector import (
    LinearOutcomeAdapter,
    LinearProjection,
    _event_clock,
    _optional_timestamp,
    _uuid,
)
from .linear_webhook_auth import _expiry_ms
from .outcome_connector_config import LinearOutcomeChannelConfig
from .outcome_ingest import AuthenticatedDelivery, registered_binding
from .outcome_wire import REQUEST_BYTES, OutcomeKeys, decode_source_body, timestamp
from .portfolio_config import PortfolioConnectorBinding
from .portfolio_wire import PortfolioError


LINEAR_SNAPSHOTS_PATH = "/v1/connectors/linear/snapshots"
SNAPSHOT_ITEMS = 100
SNAPSHOT_PAGES = 100
SNAPSHOT_FRESHNESS_MS = 300_000
_INGEST_SLOTS = threading.BoundedSemaphore(4)
_SIGNATURE = re.compile(r"[0-9a-f]{64}\Z")
_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
_V4_UUID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)
_HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")
_SECURITY_HEADERS = frozenset({
    "x-hormuz-linear-snapshot-signature",
    "x-hormuz-linear-snapshot-timestamp",
})
_KINDS = {"Initiative": "initiative", "Project": "project", "Cycle": "cycle", "Issue": "issue"}
_COMMON_DATA_FIELDS = frozenset({
    "id", "updatedAt", "archivedAt", "completedAt", "canceledAt", "startedAt",
    "state", "status", "teamId", "teamIds",
})
_RELATIONSHIP_FIELDS = {
    "initiative": frozenset({"parentInitiativeId"}),
    "project": frozenset({"initiatives"}),
    "cycle": frozenset(),
    "issue": frozenset({"projectId", "cycleId"}),
}
_REQUIRED_FIELDS = {
    "initiative": frozenset({"id", "updatedAt", "parentInitiativeId"}),
    "project": frozenset({"id", "updatedAt", "initiatives"}),
    "cycle": frozenset({"id", "updatedAt"}),
    "issue": frozenset({"id", "updatedAt", "projectId", "cycleId"}),
}


@dataclass(frozen=True)
class LinearVerifiedSnapshot(AuthenticatedDelivery):
    reconciliation_id: str
    snapshot_id: str
    page_id: str
    page_number: int
    page_count: int
    captured_at: str
    body_fingerprints: tuple[tuple[str, str], ...]
    signed_timestamp_ms: int
    received_timestamp_ms: int
    fresh: bool


def _headers(headers: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(headers, Mapping) or len(headers) > 64:
        raise PortfolioError("invalid_request")
    selected: dict[str, str] = {}
    total = 0
    for name, value in headers.items():
        if (
            type(name) is not str
            or type(value) is not str
            or len(name) > 128
            or _HEADER_NAME.fullmatch(name) is None
            or len(value) > 2048
            or any((ord(char) < 32 and char != "\t") or ord(char) == 127 for char in value)
        ):
            raise PortfolioError("invalid_request")
        try:
            total += len(name.encode("ascii")) + len(value.encode("utf-8"))
        except UnicodeError:
            raise PortfolioError("invalid_request") from None
        if total > 8192:
            raise PortfolioError("invalid_request")
        normalized = name.lower()
        if normalized in _SECURITY_HEADERS:
            if normalized in selected:
                raise PortfolioError("invalid_request")
            selected[normalized] = value
    if not _SECURITY_HEADERS.issubset(selected):
        raise PortfolioError("unauthenticated")
    return selected


def _exact(value: object, fields: set[str] | frozenset[str]) -> dict:
    if not isinstance(value, dict) or set(value) != set(fields):
        raise PortfolioError("invalid_request")
    return value


def _v4_uuid(value: object) -> str:
    if not isinstance(value, str) or _V4_UUID.fullmatch(value) is None:
        raise PortfolioError("invalid_request")
    return value


def _team_ids(data: dict, allowed: frozenset[str]) -> tuple[str, ...] | None:
    team_id = data.get("teamId")
    team_ids = data.get("teamIds")
    observed: tuple[str, ...] | None = None
    if team_id is not None:
        if not isinstance(team_id, str) or team_id not in allowed:
            raise PortfolioError("forbidden")
        observed = (team_id,)
    if team_ids is not None:
        if (
            not isinstance(team_ids, list)
            or not 1 <= len(team_ids) <= 100
            or any(not isinstance(value, str) or value not in allowed for value in team_ids)
            or len(set(team_ids)) != len(team_ids)
            or (team_id is not None and team_id not in team_ids)
        ):
            raise PortfolioError("forbidden")
        observed = tuple(sorted(team_ids))
    return observed


class LinearSnapshotAuthenticator:
    """Authenticate an operator-generated snapshot with a dedicated secret."""

    def __init__(self, config, channel: LinearOutcomeChannelConfig) -> None:
        binding = registered_binding(config, channel.organization_id, channel.connector_id)
        if (
            binding.provider != "linear"
            or binding.installation_id is not None
            or binding.workspace_id is None
            or set(channel.typed_enrollment["project"]) != set(binding.external_object_ids)
        ):
            raise PortfolioError("forbidden")
        raw_secrets = channel.resolved_snapshot_secrets()
        if not 1 <= len(raw_secrets) <= 2:
            raise PortfolioError("forbidden")
        secrets: dict[str, tuple[bytes, int | None]] = {}
        active = 0
        for version, (secret, expires_at) in raw_secrets.items():
            if (
                type(secret) is not bytes
                or not 32 <= len(secret) <= 128
                or secret in {item[0] for item in secrets.values()}
            ):
                raise PortfolioError("invalid_request")
            expires_ms = _expiry_ms(expires_at)
            active += expires_ms is None
            secrets[version] = (secret, expires_ms)
        if active != 1:
            raise PortfolioError("invalid_request")
        self.binding: PortfolioConnectorBinding = binding
        self.channel = channel
        self.secrets = MappingProxyType(secrets)
        self.keys: OutcomeKeys = channel.resolved_identity_keys()
        self._team_ids = frozenset(channel.source_team_ids)
        self._typed = MappingProxyType({
            kind: frozenset(values) for kind, values in channel.typed_enrollment.items()
        })

    def authenticate(
        self,
        headers: Mapping[str, str],
        raw: bytes,
        *,
        now_ms: int | None = None,
    ) -> tuple[LinearVerifiedSnapshot, dict]:
        if type(raw) is not bytes or not 1 <= len(raw) <= REQUEST_BYTES:
            raise PortfolioError("invalid_request")
        supplied = _headers(headers)
        signature = supplied["x-hormuz-linear-snapshot-signature"]
        timestamp_header = supplied["x-hormuz-linear-snapshot-timestamp"]
        if _SIGNATURE.fullmatch(signature) is None or re.fullmatch(r"(?:0|[1-9][0-9]{0,15})", timestamp_header) is None:
            raise PortfolioError("unauthenticated")
        signed_ms = int(timestamp_header)
        current_ms = int(time.time() * 1000) if now_ms is None else now_ms
        if type(current_ms) is not int or current_ms < 0:
            raise PortfolioError("invalid_request")
        signed_input = timestamp_header.encode("ascii") + b"." + raw
        actual = bytes.fromhex(signature)
        matched = None
        for version, (secret, expires_ms) in self.secrets.items():
            expected = hmac.new(secret, signed_input, hashlib.sha256).digest()
            usable = expires_ms is None or current_ms <= expires_ms
            if usable and hmac.compare_digest(actual, expected):
                if matched is not None:
                    raise PortfolioError("unavailable")
                matched = version
        if matched is None:
            raise PortfolioError("unauthenticated")

        body = _exact(decode_source_body(raw), {
            "schema_id", "schema_version", "workspace_id", "reconciliation_id",
            "snapshot_id", "page_id", "page_number", "page_count", "captured_at", "items",
        })
        if (
            body["schema_id"] != "hormuz.linear-authorized-snapshot"
            or type(body["schema_version"]) is not int
            or body["schema_version"] != 1
        ):
            raise PortfolioError("invalid_request")
        if body["workspace_id"] != self.binding.workspace_id:
            raise PortfolioError("forbidden")
        reconciliation_id = _v4_uuid(body["reconciliation_id"])
        snapshot_id = _v4_uuid(body["snapshot_id"])
        page_id = _v4_uuid(body["page_id"])
        page_number, page_count = body["page_number"], body["page_count"]
        if (
            type(page_number) is not int
            or type(page_count) is not int
            or not 1 <= page_number <= page_count <= SNAPSHOT_PAGES
        ):
            raise PortfolioError("invalid_request")
        captured_at = timestamp(body["captured_at"])
        captured_ms = int(datetime.fromisoformat(captured_at).timestamp() * 1000)
        if (
            captured_ms > signed_ms
            or signed_ms - captured_ms > SNAPSHOT_FRESHNESS_MS
        ):
            raise PortfolioError("invalid_request")
        items = body["items"]
        if not isinstance(items, list) or not 1 <= len(items) <= SNAPSHOT_ITEMS:
            raise PortfolioError("invalid_request")
        identities: set[tuple[str, str]] = set()
        for raw_item in items:
            item = _exact(raw_item, {"type", "data"})
            kind = _KINDS.get(item["type"])
            if kind is None:
                raise PortfolioError("invalid_request")
            data = item["data"]
            allowed_fields = _COMMON_DATA_FIELDS | _RELATIONSHIP_FIELDS[kind]
            if (
                not isinstance(data, dict)
                or not _REQUIRED_FIELDS[kind].issubset(data)
                or not set(data).issubset(allowed_fields)
                or ("state" in data and "status" in data)
            ):
                raise PortfolioError("invalid_request")
            object_id = data.get("id")
            if not isinstance(object_id, str) or _UUID.fullmatch(object_id) is None:
                raise PortfolioError("invalid_request")
            if object_id not in self._typed[kind]:
                raise PortfolioError("forbidden")
            identity = kind, object_id
            if identity in identities:
                raise PortfolioError("idempotency_conflict")
            identities.add(identity)
            _team_ids(data, self._team_ids)
            for field in ("state", "status"):
                if field in data and data[field] is not None:
                    state = _exact(data[field], {"type"})
                    if not isinstance(state["type"], str) or not 1 <= len(state["type"]) <= 64:
                        raise PortfolioError("invalid_request")
            if kind == "issue":
                for field, parent_kind in (("projectId", "project"), ("cycleId", "cycle")):
                    value = data[field]
                    if value is not None and (
                        not isinstance(value, str)
                        or _UUID.fullmatch(value) is None
                        or value not in self._typed[parent_kind]
                    ):
                        raise PortfolioError("forbidden")
            elif kind == "initiative":
                parent = data["parentInitiativeId"]
                if parent is not None and (
                    not isinstance(parent, str)
                    or _UUID.fullmatch(parent) is None
                    or parent not in self._typed["initiative"]
                ):
                    raise PortfolioError("forbidden")
            elif kind == "project":
                initiatives = data["initiatives"]
                if not isinstance(initiatives, list) or len(initiatives) > 100:
                    raise PortfolioError("invalid_request")
                initiative_ids = []
                for raw_parent in initiatives:
                    parent = _exact(raw_parent, {"id"})
                    parent_id = parent["id"]
                    if (
                        not isinstance(parent_id, str)
                        or _UUID.fullmatch(parent_id) is None
                        or parent_id not in self._typed["initiative"]
                    ):
                        raise PortfolioError("forbidden")
                    initiative_ids.append(parent_id)
                if len(set(initiative_ids)) != len(initiative_ids):
                    raise PortfolioError("invalid_request")

        fingerprints = tuple(
            (
                version,
                self.keys.delivery_digest(
                    version,
                    self.binding.organization_id,
                    self.binding.connector_id,
                    "linear-authorized-snapshot-v1",
                    raw,
                ),
            )
            for version in self.channel.identity_key_versions
        )
        return LinearVerifiedSnapshot(
            self.binding.organization_id,
            self.binding.connector_id,
            "linear",
            None,
            self.binding.workspace_id,
            page_id,
            matched,
            reconciliation_id,
            snapshot_id,
            page_id,
            page_number,
            page_count,
            captured_at,
            fingerprints,
            signed_ms,
            current_ms,
            abs(current_ms - signed_ms) <= SNAPSHOT_FRESHNESS_MS,
        ), body


class LinearSnapshotAdapter:
    def __init__(self, channel: LinearOutcomeChannelConfig) -> None:
        self._webhook = LinearOutcomeAdapter(channel)
        self._team_ids = frozenset(channel.source_team_ids)

    def normalize(self, verified: LinearVerifiedSnapshot, body: dict) -> tuple[LinearProjection, ...]:
        projections = []
        for item in body["items"]:
            kind = _KINDS[item["type"]]
            data = item["data"]
            revision_value, revision_order = _event_clock(data["updatedAt"])
            if timestamp(revision_value) > verified.captured_at:
                raise PortfolioError("invalid_request")
            lifecycle_times = {
                field: _optional_timestamp(data.get(field))
                for field in ("archivedAt", "completedAt", "canceledAt", "startedAt")
            }
            if any(
                value is not None and value > verified.captured_at
                for value in lifecycle_times.values()
            ):
                raise PortfolioError("invalid_request")
            normalized_state, _state_basis = self._webhook._state(data)
            relationships, coverage, _project_id = self._webhook._relationships(kind, data)
            archived = lifecycle_times["archivedAt"]
            lifecycle = "archived" if archived is not None else "updated"
            context = {
                "object": {"kind": kind, "id": _uuid(data["id"])},
                "source_team_ids": (
                    list(value) if (value := _team_ids(data, self._team_ids)) is not None else None
                ),
                "lifecycle": lifecycle,
                "normalized_state": normalized_state,
                "relationships": relationships,
                "relationship_coverage": coverage,
                "revision": {"kind": "source_updated_at_v1", "value": revision_value},
                "event_at": revision_value,
            }
            source_fact = {
                "workspace_id": verified.workspace_id,
                **context,
            }
            projections.append(LinearProjection(context, None, source_fact))
        return tuple(projections)


class LinearSnapshotReceiver:
    def __init__(self, config, repository) -> None:
        runtime = config.outcome_connectors
        if runtime is None:
            raise PortfolioError("forbidden")
        channels = []
        seen_material: set[bytes] = set()
        for channel in runtime.linear:
            if channel.active_snapshot_secret is None:
                continue
            authenticator = LinearSnapshotAuthenticator(config, channel)
            material = [value[0] for value in authenticator.secrets.values()]
            material.extend(
                reference.value for reference in channel.identity_keys
                if reference.value is not None
            )
            if len(material) != len(set(material)) or seen_material.intersection(material):
                raise PortfolioError("invalid_request")
            seen_material.update(material)
            channels.append((
                channel,
                authenticator,
                LinearSnapshotAdapter(channel),
                authenticator.keys,
            ))
        if not channels:
            raise PortfolioError("forbidden")
        self.config = config
        self.repository = repository
        self._channels = tuple(channels)

    def ingest(
        self,
        headers: Mapping[str, str],
        raw: bytes,
        *,
        deadline: float | None = None,
        now_ms: int | None = None,
    ) -> dict:
        deadline = time.monotonic() + 4 if deadline is None else deadline
        if not _INGEST_SLOTS.acquire(blocking=False):
            raise PortfolioError("rate_limited")
        try:
            failure: PortfolioError | None = None
            for channel, authenticator, adapter, keys in self._channels:
                if time.monotonic() >= deadline:
                    raise PortfolioError("unavailable")
                try:
                    verified, body = authenticator.authenticate(headers, raw, now_ms=now_ms)
                except PortfolioError as error:
                    if error.code in {"unauthenticated", "forbidden"}:
                        failure = error
                        continue
                    raise
                binding = registered_binding(
                    self.config,
                    channel.organization_id,
                    channel.connector_id,
                )
                prior = self.repository.replay_snapshot(
                    channel=channel,
                    binding=binding,
                    verified=verified,
                    deadline=deadline,
                )
                if prior is not None:
                    return prior
                if not verified.fresh:
                    raise PortfolioError("unauthenticated")
                projections = adapter.normalize(verified, body)
                return self.repository.accept_snapshot(
                    channel=channel,
                    binding=binding,
                    verified=verified,
                    keys=keys,
                    projections=projections,
                    deadline=deadline,
                )
            if failure is not None:
                raise failure
            raise PortfolioError("unauthenticated")
        finally:
            _INGEST_SLOTS.release()
