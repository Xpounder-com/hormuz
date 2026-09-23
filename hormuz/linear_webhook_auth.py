"""Linear raw-body HMAC authentication and exact typed route authority."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import hmac
import re
import time
from types import MappingProxyType
from typing import Mapping

from .config import GatewayConfig
from .outcome_connector_config import LinearOutcomeChannelConfig
from .outcome_ingest import AuthenticatedDelivery, registered_binding
from .outcome_wire import OutcomeKeys, REQUEST_BYTES, decode_source_body
from .portfolio_config import PortfolioConnectorBinding
from .portfolio_wire import PortfolioError


_SIGNATURE = re.compile(r"[0-9a-f]{64}\Z")
_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
_DELIVERY_UUID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)
_HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")
_KINDS = {"Initiative": "initiative", "Project": "project", "Cycle": "cycle", "Issue": "issue"}
_ACTIONS = frozenset({"create", "update", "remove"})
_SECURITY_HEADERS = frozenset({"linear-signature", "linear-delivery", "linear-event"})
_SCOPE_OVERRIDES = frozenset({
    "organization_id", "tenant_id", "tenantId", "work_scope_id", "workScopeId",
    "connector_id", "connectorId", "hormuzOrganizationId",
})


@dataclass(frozen=True)
class LinearVerifiedDelivery(AuthenticatedDelivery):
    provider_delivery_id: str
    source_webhook_id: str
    object_kind: str
    object_id: str
    action: str
    body_fingerprints: tuple[tuple[str, str], ...]
    signed_timestamp_ms: int
    received_timestamp_ms: int
    fresh: bool
    source_team_ids: tuple[str, ...] | None


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


def _expiry_ms(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise PortfolioError("invalid_request") from None
    return int(parsed.timestamp() * 1000)


class LinearWebhookAuthenticator:
    """Authenticate one immutable server-enrolled workspace/webhook route."""

    def __init__(
        self,
        config: GatewayConfig,
        channel: LinearOutcomeChannelConfig,
    ) -> None:
        binding = registered_binding(config, channel.organization_id, channel.connector_id)
        if (
            binding.provider != "linear"
            or binding.installation_id is not None
            or binding.workspace_id is None
            or set(channel.typed_enrollment["project"]) != set(binding.external_object_ids)
        ):
            raise PortfolioError("forbidden")
        raw_secrets = channel.resolved_webhook_secrets()
        if not 1 <= len(raw_secrets) <= 2:
            raise PortfolioError("invalid_request")
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
        keys = channel.resolved_identity_keys()
        for version in channel.identity_key_versions:
            keys.delivery_digest(
                version,
                channel.organization_id,
                channel.connector_id,
                "linear-signed-body-v1",
                b"probe",
            )
        self.binding: PortfolioConnectorBinding = binding
        self.channel = channel
        self.secrets = MappingProxyType(secrets)
        self.keys: OutcomeKeys = keys
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
    ) -> tuple[LinearVerifiedDelivery, dict]:
        if type(raw) is not bytes or not 1 <= len(raw) <= REQUEST_BYTES:
            raise PortfolioError("invalid_request")
        supplied = _headers(headers)
        signature = supplied["linear-signature"]
        if _SIGNATURE.fullmatch(signature) is None:
            raise PortfolioError("unauthenticated")
        actual = bytes.fromhex(signature)
        current_ms = int(time.time() * 1000) if now_ms is None else now_ms
        if type(current_ms) is not int or current_ms < 0:
            raise PortfolioError("invalid_request")
        matched = None
        for version, (secret, expires_ms) in self.secrets.items():
            expected = hmac.new(secret, raw, hashlib.sha256).digest()
            usable = expires_ms is None or current_ms <= expires_ms
            if usable and hmac.compare_digest(actual, expected):
                if matched is not None:
                    raise PortfolioError("unavailable")
                matched = version
        if matched is None:
            raise PortfolioError("unauthenticated")

        body = decode_source_body(raw)
        data = body.get("data")
        if _SCOPE_OVERRIDES.intersection(body) or not isinstance(data, dict):
            raise PortfolioError("forbidden")
        if _SCOPE_OVERRIDES.intersection(data):
            raise PortfolioError("forbidden")
        if (
            body.get("organizationId") != self.binding.workspace_id
            or body.get("webhookId") != self.channel.source_webhook_id
        ):
            raise PortfolioError("forbidden")
        signed_ms = body.get("webhookTimestamp")
        if type(signed_ms) is not int or signed_ms < 0:
            raise PortfolioError("unauthenticated")
        provider_delivery = supplied["linear-delivery"]
        if _DELIVERY_UUID.fullmatch(provider_delivery) is None:
            raise PortfolioError("invalid_request")
        source_type = body.get("type")
        if source_type not in _KINDS or supplied["linear-event"] != source_type:
            raise PortfolioError("invalid_request")
        action = body.get("action")
        if action not in _ACTIONS:
            raise PortfolioError("invalid_request")
        kind = _KINDS[source_type]
        object_id = data.get("id")
        if (
            not isinstance(object_id, str)
            or _UUID.fullmatch(object_id) is None
            or object_id not in self._typed[kind]
        ):
            raise PortfolioError("forbidden")

        team_id = data.get("teamId")
        team_ids = data.get("teamIds")
        observed_teams: tuple[str, ...] | None = None
        if team_id is not None:
            if not isinstance(team_id, str) or team_id not in self._team_ids:
                raise PortfolioError("forbidden")
            observed_teams = (team_id,)
        if team_ids is not None:
            if (
                not isinstance(team_ids, list)
                or not 1 <= len(team_ids) <= 100
                or any(not isinstance(value, str) or value not in self._team_ids for value in team_ids)
                or len(set(team_ids)) != len(team_ids)
                or (team_id is not None and team_id not in team_ids)
            ):
                raise PortfolioError("forbidden")
            observed_teams = tuple(sorted(team_ids))

        fingerprints = tuple(
            (
                version,
                self.keys.delivery_digest(
                    version,
                    self.binding.organization_id,
                    self.binding.connector_id,
                    "linear-signed-body-v1",
                    raw,
                ),
            )
            for version in self.channel.identity_key_versions
        )
        current_fingerprint = dict(fingerprints)[self.channel.body_fingerprint_key_version]
        return LinearVerifiedDelivery(
            self.binding.organization_id,
            self.binding.connector_id,
            "linear",
            None,
            self.binding.workspace_id,
            current_fingerprint,
            matched,
            provider_delivery,
            self.channel.source_webhook_id,
            kind,
            object_id,
            action,
            fingerprints,
            signed_ms,
            current_ms,
            abs(current_ms - signed_ms) <= 60_000,
            observed_teams,
        ), body
