"""Closed, metadata-only evidence contracts for Linear audit sources.

These validators deliberately do not import the connector or repository.  The
finite audit-chain source union can therefore validate a stored event without
opening an import cycle or accepting arbitrary JSON alongside a known source
identity.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import hashlib
import re

from .outcome_wire import timestamp
from .portfolio_wire import PortfolioError, canonical, validate


LINEAR_SOURCE_SCHEMA_IDS = frozenset({
    "hormuz.linear-source-binding-version",
    "hormuz.linear-delivery-receipt",
    "hormuz.linear-snapshot-receipt",
    "hormuz.linear-context-event",
    "hormuz.linear-context-retention",
})

_BINDING_FIELDS = frozenset({
    "schema_id", "schema_version", "organization_id", "connector_id",
    "version", "binding_state", "source_workspace_id", "source_webhook_id",
    "source_team_ids", "typed_enrollment", "credential_version",
    "fingerprint_key_version", "registered_by", "binding_event_id",
    "content_digest", "request_digest", "registered_at",
})
_RECEIPT_FIELDS = frozenset({
    "schema_id", "schema_version", "organization_id", "connector_id",
    "receipt_id", "binding_version", "source_delivery_id",
    "credential_version", "body_fingerprint_key_version",
    "source_fact_key_version", "received_at", "committed_at",
    "context_event_id", "outcome_source_delivery_id", "response",
})
_SNAPSHOT_RECEIPT_FIELDS = frozenset({
    "schema_id", "schema_version", "organization_id", "connector_id",
    "receipt_id", "binding_version", "reconciliation_id", "snapshot_id",
    "page_id", "page_number", "page_count", "credential_version",
    "body_fingerprint_key_version", "received_at", "captured_at", "committed_at",
    "accepted_context_count", "response",
})
_CONTEXT_FIELDS = frozenset({
    "schema_id", "schema_version", "organization_id", "connector_id",
    "context_event_id", "source_workspace_id", "source_team_ids", "object",
    "capture_kind", "source_delivery_id", "authority_binding", "normalizer",
    "credential_version", "source_authentication", "lifecycle",
    "normalized_state", "relationships", "relationship_coverage", "revision",
    "ordering_state", "scope_state", "binding",
    "supersedes_context_event_id", "event_at", "observed_at", "ingested_at",
    "provenance_digest", "evidence_level", "association_eligibility",
    "reader_role", "commit_sequence",
})
_RETENTION_FIELDS = frozenset({
    "schema_id", "schema_version", "organization_id", "connector_id",
    "retention_event_id", "target_context_event_id", "actor_id",
    "reader_role", "reason_code", "observed_at", "ingested_at",
    "provenance_digest",
})
_VERSION_REF_FIELDS = frozenset({"id", "version", "content_digest"})
_OBJECT_FIELDS = frozenset({"kind", "id"})
_RELATIONSHIP_FIELDS = frozenset({"kind", "parent"})
_REVISION_FIELDS = frozenset({"kind", "value"})
_BINDING_REF_FIELDS = frozenset({"binding_event_id", "work_scope", "registry_sequence"})
_WORK_SCOPE_FIELDS = frozenset({"work_scope_id", "version"})
_TYPED_ENROLLMENT_FIELDS = frozenset({"initiative", "project", "cycle", "issue"})

_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_COUNTER = re.compile(r"(?:0|[1-9][0-9]{0,18})\Z")
_MAX_CONFIG_VERSION = 2_147_483_647
_MAX_WIRE_INTEGER = 9_007_199_254_740_991
_MAX_COUNTER = 9_223_372_036_854_775_807
_KINDS = frozenset({"initiative", "project", "cycle", "issue"})
_RELATIONSHIP_PAIRS = {
    "initiative_project": ("project", "initiative"),
    "project_issue": ("issue", "project"),
    "cycle_issue": ("issue", "cycle"),
    "initiative_parent": ("initiative", "initiative"),
}
_NORMALIZER_RULES = {
    "schema_id": "hormuz.linear-webhook-normalizer",
    "schema_version": 1,
    "actions": ["create", "remove", "update"],
    "entities": ["cycle", "initiative", "issue", "project"],
    "revision": "source_updated_at_v1",
    "content": "opaque_ids_and_timestamps_only",
    "state_outcomes": "updated_from_transition_only",
    "relationship_coverage": "explicit_empty_complete_absent_unknown",
}
_NORMALIZER_DIGEST = hashlib.sha256(
    canonical(_NORMALIZER_RULES).encode("ascii")
).hexdigest()


def _fail() -> None:
    raise ValueError("linear_evidence_invalid")


def _exact(value: object, fields: frozenset[str]) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != fields:
        _fail()
    return value


def _opaque(value: object) -> str:
    try:
        validate(value, "opaque_id")
    except PortfolioError:
        _fail()
    assert isinstance(value, str)
    return value


def _uuid(value: object) -> str:
    if not isinstance(value, str) or _UUID.fullmatch(value) is None:
        _fail()
    return value


def _digest(value: object) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        _fail()
    return value


def _version(value: object, maximum: int = _MAX_CONFIG_VERSION) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        _fail()
    return value


def _count(value: object) -> int:
    if type(value) is not int or not 0 <= value <= _MAX_WIRE_INTEGER:
        _fail()
    return value


def _timestamp(value: object) -> str:
    if not isinstance(value, str):
        _fail()
    try:
        return timestamp(value)
    except (PortfolioError, TypeError, ValueError):
        _fail()


def _uuid_list(
    value: object,
    *,
    minimum: int,
    maximum: int,
) -> list[str]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        _fail()
    result = [_uuid(item) for item in value]
    if result != sorted(set(result)):
        _fail()
    return result


def _common(event: Mapping[str, object], schema_id: str) -> None:
    if event.get("schema_id") != schema_id or event.get("schema_version") != 1:
        _fail()
    _opaque(event.get("organization_id"))
    _opaque(event.get("connector_id"))


def _validate_binding(event: Mapping[str, object]) -> None:
    _exact(event, _BINDING_FIELDS)
    _common(event, "hormuz.linear-source-binding-version")
    version = _version(event.get("version"))
    if event.get("binding_state") not in {"active", "revoked"}:
        _fail()
    _uuid(event.get("binding_event_id"))
    _uuid(event.get("source_workspace_id"))
    _uuid(event.get("source_webhook_id"))
    teams = _uuid_list(event.get("source_team_ids"), minimum=1, maximum=100)
    enrollment = _exact(event.get("typed_enrollment"), _TYPED_ENROLLMENT_FIELDS)
    typed = {
        kind: _uuid_list(enrollment[kind], minimum=0, maximum=1000)
        for kind in sorted(_TYPED_ENROLLMENT_FIELDS)
    }
    _opaque(event.get("credential_version"))
    fingerprint_version = _version(event.get("fingerprint_key_version"))
    _opaque(event.get("registered_by"))
    _timestamp(event.get("registered_at"))
    _digest(event.get("request_digest"))
    expected_basis = {
        "organization_id": event["organization_id"],
        "connector_id": event["connector_id"],
        "version": version,
        "binding_state": event["binding_state"],
        "source_workspace_id": event["source_workspace_id"],
        "source_webhook_id": event["source_webhook_id"],
        "source_team_ids": teams,
        "typed_enrollment": typed,
        "credential_version": event["credential_version"],
        "fingerprint_key_version": fingerprint_version,
        "registered_by": event["registered_by"],
    }
    try:
        expected_digest = hashlib.sha256(
            canonical(expected_basis).encode("ascii")
        ).hexdigest()
    except PortfolioError:
        _fail()
    if event.get("content_digest") != expected_digest:
        _fail()


def _validate_receipt(event: Mapping[str, object]) -> None:
    _exact(event, _RECEIPT_FIELDS)
    _common(event, "hormuz.linear-delivery-receipt")
    _uuid(event.get("receipt_id"))
    _version(event.get("binding_version"))
    _uuid(event.get("source_delivery_id"))
    _opaque(event.get("credential_version"))
    _version(event.get("body_fingerprint_key_version"))
    _version(event.get("source_fact_key_version"))
    _timestamp(event.get("received_at"))
    _timestamp(event.get("committed_at"))
    _uuid(event.get("context_event_id"))
    outcome_delivery = _digest(event.get("outcome_source_delivery_id"))
    response = event.get("response")
    try:
        validate(response, "hormuz.connector-ingest-receipt")
    except PortfolioError:
        _fail()
    assert isinstance(response, dict)
    if (
        response.get("organization_id") != event.get("organization_id")
        or response.get("connector_id") != event.get("connector_id")
        or response.get("source_delivery_id") != outcome_delivery
        or response.get("disposition") != "accepted"
        or response.get("accepted_event_count") not in {1, 2}
    ):
        _fail()


def _validate_snapshot_receipt(event: Mapping[str, object]) -> None:
    _exact(event, _SNAPSHOT_RECEIPT_FIELDS)
    _common(event, "hormuz.linear-snapshot-receipt")
    receipt_id = _uuid(event.get("receipt_id"))
    _version(event.get("binding_version"))
    _uuid(event.get("reconciliation_id"))
    _uuid(event.get("snapshot_id"))
    page_id = _uuid(event.get("page_id"))
    page_number = _version(event.get("page_number"), 100)
    page_count = _version(event.get("page_count"), 100)
    if page_number > page_count:
        _fail()
    _opaque(event.get("credential_version"))
    _version(event.get("body_fingerprint_key_version"))
    _timestamp(event.get("received_at"))
    _timestamp(event.get("captured_at"))
    committed_at = _timestamp(event.get("committed_at"))
    accepted = _count(event.get("accepted_context_count"))
    if accepted > 100:
        _fail()
    response = event.get("response")
    try:
        validate(response, "hormuz.connector-ingest-receipt")
    except PortfolioError:
        _fail()
    assert isinstance(response, dict)
    if (
        response.get("organization_id") != event.get("organization_id")
        or response.get("connector_id") != event.get("connector_id")
        or response.get("source_delivery_id") != page_id
        or response.get("receipt_id") != receipt_id
        or response.get("accepted_event_count") != accepted
        or response.get("ingested_at") != committed_at
        or response.get("disposition") != ("accepted" if accepted else "duplicate")
    ):
        _fail()


def _version_ref(value: object) -> Mapping[str, object]:
    result = _exact(value, _VERSION_REF_FIELDS)
    _opaque(result.get("id"))
    _version(result.get("version"), _MAX_WIRE_INTEGER)
    _digest(result.get("content_digest"))
    return result


def _binding_ref(value: object) -> None:
    result = _exact(value, _BINDING_REF_FIELDS)
    _opaque(result.get("binding_event_id"))
    work_scope = _exact(result.get("work_scope"), _WORK_SCOPE_FIELDS)
    _opaque(work_scope.get("work_scope_id"))
    _version(work_scope.get("version"), _MAX_WIRE_INTEGER)
    _count(result.get("registry_sequence"))


def _validate_context(event: Mapping[str, object]) -> None:
    _exact(event, _CONTEXT_FIELDS)
    _common(event, "hormuz.linear-context-event")
    context_id = _uuid(event.get("context_event_id"))
    _uuid(event.get("source_workspace_id"))
    team_ids = event.get("source_team_ids")
    if team_ids is not None:
        _uuid_list(team_ids, minimum=0, maximum=100)

    source = _exact(event.get("object"), _OBJECT_FIELDS)
    source_kind = source.get("kind")
    if source_kind not in _KINDS:
        _fail()
    source_id = _uuid(source.get("id"))

    capture_kind = event.get("capture_kind")
    if capture_kind not in {"webhook", "authorized_snapshot"}:
        _fail()
    source_delivery = event.get("source_delivery_id")
    if source_delivery is not None:
        _uuid(source_delivery)
    if (capture_kind == "webhook") != (source_delivery is not None):
        _fail()

    authority = _version_ref(event.get("authority_binding"))
    if authority.get("id") != event.get("connector_id"):
        _fail()
    normalizer = _version_ref(event.get("normalizer"))
    if (
        normalizer.get("id") != "linear-webhook-normalizer"
        or normalizer.get("version") != 1
        or normalizer.get("content_digest") != _NORMALIZER_DIGEST
    ):
        _fail()
    _opaque(event.get("credential_version"))
    if (
        event.get("source_authentication") != "verified_connector"
        or event.get("lifecycle")
        not in {"created", "updated", "archived", "restored", "deleted"}
        or event.get("normalized_state")
        not in {"not_started", "in_progress", "completed", "canceled", "unknown"}
    ):
        _fail()

    relationships = event.get("relationships")
    if not isinstance(relationships, list) or len(relationships) > 100:
        _fail()
    relationship_keys: list[tuple[str, str]] = []
    for relationship in relationships:
        item = _exact(relationship, _RELATIONSHIP_FIELDS)
        relationship_kind = item.get("kind")
        if relationship_kind not in _RELATIONSHIP_PAIRS:
            _fail()
        parent = _exact(item.get("parent"), _OBJECT_FIELDS)
        parent_kind = parent.get("kind")
        parent_id = _uuid(parent.get("id"))
        if (
            (source_kind, parent_kind) != _RELATIONSHIP_PAIRS[relationship_kind]
            or source_id == parent_id
        ):
            _fail()
        relationship_keys.append((relationship_kind, parent_id))
    if relationship_keys != sorted(set(relationship_keys)):
        _fail()
    for relationship_kind in ("project_issue", "cycle_issue"):
        if sum(key[0] == relationship_kind for key in relationship_keys) > 1:
            _fail()
    coverage = event.get("relationship_coverage")
    if coverage not in {"complete", "partial", "unknown", "not_applicable"}:
        _fail()
    if coverage in {"unknown", "not_applicable"} and relationships:
        _fail()

    revision = _exact(event.get("revision"), _REVISION_FIELDS)
    revision_kind, revision_value = revision.get("kind"), revision.get("value")
    ordering = event.get("ordering_state")
    if ordering not in {"current", "late", "incomparable", "unknown"}:
        _fail()
    if revision_kind == "unknown":
        if revision_value is not None or ordering not in {"unknown", "incomparable"}:
            _fail()
    elif revision_kind == "source_updated_at_v1":
        _timestamp(revision_value)
    elif revision_kind == "source_revision_counter_v1":
        if (
            not isinstance(revision_value, str)
            or _COUNTER.fullmatch(revision_value) is None
            or int(revision_value) > _MAX_COUNTER
        ):
            _fail()
    else:
        _fail()

    scope_state = event.get("scope_state")
    if scope_state not in {"matched", "unmatched", "excluded"}:
        _fail()
    binding = event.get("binding")
    event_at = event.get("event_at")
    observed_at = _timestamp(event.get("observed_at"))
    _timestamp(event.get("ingested_at"))
    if event_at is not None:
        event_at = _timestamp(event_at)
    if scope_state == "matched":
        if binding is None or event_at is None:
            _fail()
        _binding_ref(binding)
        if datetime.fromisoformat(event_at) > datetime.fromisoformat(observed_at):
            _fail()
    elif binding is not None:
        _fail()

    supersedes = event.get("supersedes_context_event_id")
    if supersedes is not None:
        _uuid(supersedes)
        if (
            supersedes == context_id
            or revision_kind == "unknown"
            or ordering != "current"
            or coverage != "complete"
        ):
            _fail()
    _digest(event.get("provenance_digest"))
    if (
        event.get("evidence_level") != "descriptive"
        or event.get("association_eligibility") != "inconclusive"
        or event.get("reader_role") != "portfolio_admin"
    ):
        _fail()
    _version(event.get("commit_sequence"), _MAX_WIRE_INTEGER)


def _validate_retention(event: Mapping[str, object]) -> None:
    _exact(event, _RETENTION_FIELDS)
    _common(event, "hormuz.linear-context-retention")
    retention_id = _uuid(event.get("retention_event_id"))
    target_id = _uuid(event.get("target_context_event_id"))
    if retention_id == target_id:
        _fail()
    _opaque(event.get("actor_id"))
    if event.get("reader_role") != "portfolio_admin" or event.get("reason_code") != "tombstoned":
        _fail()
    _timestamp(event.get("observed_at"))
    _timestamp(event.get("ingested_at"))
    _digest(event.get("provenance_digest"))


def validate_linear_evidence(schema_id: str, event: Mapping[str, object]) -> None:
    if schema_id not in LINEAR_SOURCE_SCHEMA_IDS or not isinstance(event, Mapping):
        _fail()
    if schema_id == "hormuz.linear-source-binding-version":
        _validate_binding(event)
    elif schema_id == "hormuz.linear-delivery-receipt":
        _validate_receipt(event)
    elif schema_id == "hormuz.linear-snapshot-receipt":
        _validate_snapshot_receipt(event)
    elif schema_id == "hormuz.linear-context-event":
        _validate_context(event)
    else:
        _validate_retention(event)


def linear_source_identity(
    schema_id: str,
    event: Mapping[str, object],
    *,
    validate_event: bool = True,
) -> str:
    if validate_event:
        validate_linear_evidence(schema_id, event)
    field = {
        "hormuz.linear-source-binding-version": "binding_event_id",
        "hormuz.linear-delivery-receipt": "receipt_id",
        "hormuz.linear-snapshot-receipt": "receipt_id",
        "hormuz.linear-context-event": "context_event_id",
        "hormuz.linear-context-retention": "retention_event_id",
    }.get(schema_id)
    if field is None:
        _fail()
    return _uuid(event.get(field))
