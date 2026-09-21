#!/usr/bin/env python3
"""Offline #220 authentication oracle; not a Linear receiver or runtime adapter.

The oracle accepts only synthetic inputs in tests. It intentionally stops at an
authenticated, typed candidate: source action/state mapping, durable receipts,
HTTP acknowledgment, persistence, and live authority are separate gates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import hmac
import json
from pathlib import Path
import re
import sys
from types import MappingProxyType
from typing import Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hormuz.outcome_wire import REQUEST_BYTES, decode_source_body
from hormuz.portfolio_wire import PortfolioError


UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
DELIVERY_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z")
OPAQUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
SIGNATURE = re.compile(r"[0-9a-f]{64}\Z")
HEADER_NAME = re.compile(r"[A-Za-z0-9-]+\Z")
PLAN_SHA256 = "ffd2e14abffea1586789095569b5ff5ba0b8683d2eb99799ff4ee5a53d0da39e"
KINDS = {"Initiative": "initiative", "Project": "project", "Cycle": "cycle", "Issue": "issue"}
ACTIONS = frozenset({"create", "update", "remove"})
SCOPE_OVERRIDES = frozenset({
    "organization_id", "tenant_id", "tenantId", "work_scope_id", "workScopeId",
    "connector_id", "connectorId", "hormuzOrganizationId",
})
SECURITY_HEADERS = frozenset({"linear-signature", "linear-delivery", "linear-event"})
_VALIDATED_REGISTRY = object()


class PreflightError(ValueError):
    """Fixed content-free code; source bytes and secret material are excluded."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class SigningKey:
    version: str
    secret: bytes = field(repr=False)
    usable_until_ms: int | None = None


@dataclass(frozen=True)
class Authority:
    route_id: str
    organization_id: str
    connector_id: str
    workspace_id: str
    webhook_id: str
    team_ids: tuple[str, ...]
    typed_object_ids: Mapping[str, tuple[str, ...]]
    signing_keys: tuple[SigningKey, ...]
    enabled: bool = True


@dataclass(frozen=True)
class PreparedAuthority:
    binding: Authority
    team_ids: frozenset[str]
    typed_object_ids: Mapping[str, frozenset[str]]


@dataclass(frozen=True, init=False)
class PreparedRegistry:
    """Immutable, fully validated route index built before receiving deliveries."""

    routes: Mapping[str, PreparedAuthority]

    def __init__(self, routes: Mapping[str, PreparedAuthority], *, _token: object) -> None:
        if _token is not _VALIDATED_REGISTRY:
            raise PreflightError("authority_invalid")
        object.__setattr__(self, "routes", MappingProxyType(dict(routes)))


@dataclass(frozen=True)
class VerifiedCandidate:
    organization_id: str
    connector_id: str
    workspace_id: str
    webhook_id: str
    object_kind: str
    object_id: str
    action: str
    delivery_id_hint: str
    credential_version: str
    fingerprint_key_version: str
    keyed_body_fingerprint: str
    team_claim: str


def validate_registry(registry: Mapping[str, Authority]) -> PreparedRegistry:
    """Validate once and snapshot route authority before accepting deliveries."""
    if not isinstance(registry, Mapping) or len(registry) > 1000:
        raise PreflightError("authority_invalid")
    owners: dict[str, str] = {}
    webhooks: set[str] = set()
    routes: dict[str, PreparedAuthority] = {}
    for route, authority in registry.items():
        if not isinstance(authority, Authority) or route != authority.route_id:
            raise PreflightError("authority_invalid")
        if any(not isinstance(value, str) or not OPAQUE.fullmatch(value) for value in
               (route, authority.organization_id, authority.connector_id)):
            raise PreflightError("authority_invalid")
        if any(not isinstance(value, str) or not UUID.fullmatch(value) for value in
               (authority.workspace_id, authority.webhook_id)):
            raise PreflightError("authority_invalid")
        if type(authority.enabled) is not bool:
            raise PreflightError("authority_invalid")
        owner = owners.setdefault(authority.workspace_id, authority.organization_id)
        if owner != authority.organization_id or authority.webhook_id in webhooks:
            raise PreflightError("authority_ambiguous")
        webhooks.add(authority.webhook_id)
        if (not isinstance(authority.team_ids, tuple) or not 1 <= len(authority.team_ids) <= 100
                or any(not isinstance(value, str) or not UUID.fullmatch(value) for value in authority.team_ids)
                or len(set(authority.team_ids)) != len(authority.team_ids)):
            raise PreflightError("authority_invalid")
        if (not isinstance(authority.typed_object_ids, Mapping)
                or set(authority.typed_object_ids) != set(KINDS.values())):
            raise PreflightError("authority_invalid")
        for values in authority.typed_object_ids.values():
            if (not isinstance(values, tuple) or len(values) > 1000
                    or any(not isinstance(value, str) or not UUID.fullmatch(value) for value in values)
                    or len(set(values)) != len(values)):
                raise PreflightError("authority_invalid")
        if not isinstance(authority.signing_keys, tuple) or not 1 <= len(authority.signing_keys) <= 2:
            raise PreflightError("authority_invalid")
        versions = set()
        secrets = set()
        active = 0
        for key in authority.signing_keys:
            if (not isinstance(key, SigningKey) or not isinstance(key.version, str)
                    or not OPAQUE.fullmatch(key.version) or key.version in versions
                    or type(key.secret) is not bytes or len(key.secret) < 32
                    or key.secret in secrets
                    or (key.usable_until_ms is not None and
                        (type(key.usable_until_ms) is not int or key.usable_until_ms < 0))):
                raise PreflightError("authority_invalid")
            versions.add(key.version)
            secrets.add(key.secret)
            active += key.usable_until_ms is None
        if active != 1:
            raise PreflightError("authority_invalid")
        copied = Authority(
            route, authority.organization_id, authority.connector_id,
            authority.workspace_id, authority.webhook_id, tuple(authority.team_ids),
            MappingProxyType({kind: tuple(values) for kind, values in authority.typed_object_ids.items()}),
            tuple(authority.signing_keys), authority.enabled,
        )
        routes[route] = PreparedAuthority(
            copied, frozenset(copied.team_ids),
            MappingProxyType({kind: frozenset(values) for kind, values in copied.typed_object_ids.items()}),
        )
    return PreparedRegistry(routes, _token=_VALIDATED_REGISTRY)


def _headers(pairs: Sequence[tuple[str, str]]) -> dict[str, str]:
    if not isinstance(pairs, (list, tuple)) or len(pairs) > 32:
        raise PreflightError("invalid_request")
    headers: dict[str, str] = {}
    for pair in pairs:
        if (not isinstance(pair, tuple) or len(pair) != 2 or
                any(not isinstance(value, str) or len(value) > 8192 for value in pair)
                or not HEADER_NAME.fullmatch(pair[0]) or "\r" in pair[1] or "\n" in pair[1]):
            raise PreflightError("invalid_request")
        key = pair[0].lower()
        if key in SECURITY_HEADERS and key in headers:
            raise PreflightError("invalid_request")
        if key in SECURITY_HEADERS:
            headers[key] = pair[1]
    required = SECURITY_HEADERS
    if not required.issubset(headers):
        raise PreflightError("unauthenticated")
    return headers


def authenticate_candidate(
    *, route_id: str, registry: PreparedRegistry, headers: Sequence[tuple[str, str]],
    raw: bytes, now_ms: int, fingerprint_key: bytes, fingerprint_key_version: str,
) -> VerifiedCandidate:
    """Verify raw bytes and server authority before parsing; never normalize."""
    if (type(now_ms) is not int or now_ms < 0 or type(fingerprint_key) is not bytes
            or len(fingerprint_key) < 32 or not isinstance(fingerprint_key_version, str)
            or not OPAQUE.fullmatch(fingerprint_key_version)):
        raise PreflightError("authority_invalid")
    if not isinstance(route_id, str):
        raise PreflightError("forbidden")
    if not isinstance(registry, PreparedRegistry):
        raise PreflightError("authority_invalid")
    prepared = registry.routes.get(route_id)
    if prepared is None or not prepared.binding.enabled:
        raise PreflightError("forbidden")
    authority = prepared.binding
    if type(raw) is not bytes or not 1 <= len(raw) <= REQUEST_BYTES:
        raise PreflightError("invalid_request")
    supplied = _headers(headers)
    signature = supplied["linear-signature"]
    if not SIGNATURE.fullmatch(signature):
        raise PreflightError("unauthenticated")
    actual = bytes.fromhex(signature)
    matched: SigningKey | None = None
    for key in authority.signing_keys:
        if key.usable_until_ms is None or now_ms <= key.usable_until_ms:
            expected = hmac.new(key.secret, raw, hashlib.sha256).digest()
            if hmac.compare_digest(actual, expected):
                matched = key
    if matched is None:
        raise PreflightError("unauthenticated")
    try:
        body = decode_source_body(raw)
    except (PortfolioError, UnicodeError, ValueError):
        raise PreflightError("invalid_request") from None
    if SCOPE_OVERRIDES.intersection(body):
        raise PreflightError("forbidden")
    if body.get("organizationId") != authority.workspace_id or body.get("webhookId") != authority.webhook_id:
        raise PreflightError("forbidden")
    sent_ms = body.get("webhookTimestamp")
    if type(sent_ms) is not int or abs(now_ms - sent_ms) > 60_000:
        raise PreflightError("unauthenticated")
    delivery = supplied["linear-delivery"]
    if not DELIVERY_UUID.fullmatch(delivery):
        raise PreflightError("invalid_request")
    source_type = body.get("type")
    if not isinstance(source_type, str) or supplied["linear-event"] != source_type or source_type not in KINDS:
        raise PreflightError("unsupported")
    action = body.get("action")
    if not isinstance(action, str) or action not in ACTIONS:
        raise PreflightError("unsupported")
    data = body.get("data")
    if not isinstance(data, dict):
        raise PreflightError("invalid_request")
    if SCOPE_OVERRIDES.intersection(data):
        raise PreflightError("forbidden")
    kind = KINDS[source_type]
    object_id = data.get("id")
    if not isinstance(object_id, str) or not UUID.fullmatch(object_id):
        raise PreflightError("invalid_request")
    if object_id not in prepared.typed_object_ids[kind]:
        raise PreflightError("forbidden")
    team_id = data.get("teamId")
    if team_id is not None and (not isinstance(team_id, str) or team_id not in prepared.team_ids):
        raise PreflightError("forbidden")
    team_ids = data.get("teamIds")
    if team_ids is not None:
        if (not isinstance(team_ids, list) or not 1 <= len(team_ids) <= 100
                or any(not isinstance(value, str) or value not in prepared.team_ids for value in team_ids)
                or len(set(team_ids)) != len(team_ids)
                or (team_id is not None and team_id not in team_ids)):
            raise PreflightError("forbidden")
    fingerprint = hmac.new(
        fingerprint_key,
        b"linear-preflight-v1\0" + fingerprint_key_version.encode() + b"\0" +
        authority.organization_id.encode() + b"\0" +
        authority.connector_id.encode() + b"\0" + raw,
        hashlib.sha256,
    ).hexdigest()
    return VerifiedCandidate(
        authority.organization_id, authority.connector_id, authority.workspace_id,
        authority.webhook_id, kind, object_id, action, delivery, matched.version,
        fingerprint_key_version, fingerprint,
        "signed_claim_matches_enrollment" if team_id is not None or team_ids is not None else "unproven",
    )


def verify_plan(root: Path = ROOT) -> dict[str, object]:
    plan_bytes = (root / "docs/linear-connector-preflight-v1.json").read_bytes()
    if hashlib.sha256(plan_bytes).hexdigest() != PLAN_SHA256:
        raise PreflightError("plan_changed")
    plan = json.loads(plan_bytes)
    if (plan.get("schema_id") != "hormuz.linear-connector-preflight"
            or plan.get("schema_version") != 1
            or plan.get("base_main_commit") != "7c8e5296329255bca35ef5e7d2cda9735885e7df"
            or plan.get("stage") != "offline_pre_implementation"
            or plan.get("target_release") != "1.3.0"):
        raise PreflightError("plan_invalid")
    for relative, digest in plan["frozen_file_sha256"].items():
        if hashlib.sha256((root / relative).read_bytes()).hexdigest() != digest:
            raise PreflightError("frozen_contract_changed")
    if plan["baseline_schema_at_base_main_commit"] != {"sqlite": 12, "postgresql": 17}:
        raise PreflightError("plan_invalid")
    successor = plan["successor_schema"]
    if (successor["linear_sqlite"] is not None or successor["linear_postgresql"] is not None
            or successor["historical_finance_proposal_at_base_sqlite"] != 13
            or successor["historical_finance_proposal_at_base_postgresql"] != 18
            or successor["assignment"] != "unassigned_pending_integration_order"):
        raise PreflightError("schema_assignment_conflict")
    if plan["gates"] != {
        "preflight_accepted": False, "runtime_implemented": False,
        "live_workspace_authorized": False, "live_delivery_verified": False,
        "final_candidate_accepted": False, "released": False,
    }:
        raise PreflightError("plan_invalid")
    if set(plan["required_transition_cases"]) != {
        "missing_successor_refusal", "ddl_failure_rollback_retry", "old_binary_partial_newer_refusal",
        "quiesced_old_pair_restore", "post_checkpoint_forward_recovery",
        "receipt_replay_and_concurrent_conflict", "durable_ack_outage_and_deadline",
    }:
        raise PreflightError("plan_invalid")
    return {"status": "offline_linear_preflight_plan_verified", "plan_sha256": PLAN_SHA256,
            "runtime_implemented": False,
            "transition_cases_executed": 0, "live_delivery_verified": False}


if __name__ == "__main__":
    print(json.dumps(verify_plan(), sort_keys=True))
