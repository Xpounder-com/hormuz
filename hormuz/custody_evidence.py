"""Shared custody-evidence retention and v2 audit-chain write boundary.

The helpers in this module deliberately accept an existing PostgreSQL cursor.
Callers insert a validated, metadata-only source record and then append its
strict v2 chain entry in the *same* transaction.  The database migration backs
this with deferred source-entry checks, so a direct writer cannot commit a new
custody source record without its matching chain entry.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from .audit_chain import AuditChainError, build_custody_audit_chain_entry, canonical_json_text
from .contracts import AUDIT_CHAIN_VERSION
from .postgres import PostgresStorageError


CUSTODY_CONTROL_AUDIT_SOURCE = ("hormuz.custody-control-event", 1)
CUSTODY_EXECUTION_ATTEMPT_AUDIT_SOURCE = ("hormuz.custody-execution-attempt", 2)
CUSTODY_EXECUTION_EVENT_AUDIT_SOURCE = ("hormuz.custody-execution-event", 1)
CUSTODY_LIFECYCLE_AUDIT_SOURCE = ("hormuz.custody-lifecycle-event", 1)
CUSTODY_ENVELOPE_ATTESTATION_AUDIT_SOURCE = ("hormuz.custody-envelope-attestation", 1)
CUSTODY_DELETION_AUDIT_SOURCE = ("hormuz.custody-deletion-event", 1)


def custody_execution_event_source_id(*, execution_id: str, sequence: int) -> str:
    """Return the immutable source identity for one execution state transition."""

    return f"{execution_id}:{sequence}"


def canonical_custody_evidence_json(event: Mapping[str, object]) -> str:
    """Return canonical bytes-as-text only after the caller's strict validation."""

    try:
        return canonical_json_text(event, code="custody_evidence_invalid")
    except AuditChainError as error:
        raise PostgresStorageError("custody_evidence_invalid") from error


def custody_evidence_timestamps(
    cursor: Any,
    *,
    organization_id: str,
) -> tuple[datetime, datetime, bool]:
    """Read tenant retention and derive immutable timestamps from PostgreSQL.

    Configuration seeds this tenant policy only at custody bootstrap.  The
    stored value, not a later process-local configuration change, determines
    every subsequent retention deadline.
    """

    cursor.execute(
        """
        SELECT retention_days, retention_legal_hold
        FROM custody_tenants
        WHERE organization_id = %s
        """,
        (organization_id,),
    )
    policy = cursor.fetchone()
    if policy is None:
        raise PostgresStorageError("custody_retention_required")
    try:
        retention_days = int(policy["retention_days"])
        legal_hold = policy["retention_legal_hold"]
    except (KeyError, TypeError, ValueError) as error:
        raise PostgresStorageError("custody_retention_required") from error
    if retention_days < 1 or not isinstance(legal_hold, bool):
        raise PostgresStorageError("custody_retention_required")
    cursor.execute(
        """
        WITH authoritative_clock AS (
            SELECT clock_timestamp() AS occurred_at
        )
        SELECT occurred_at,
               occurred_at + make_interval(days => %s) AS retain_until
        FROM authoritative_clock
        """,
        (retention_days,),
    )
    timestamps = cursor.fetchone()
    if timestamps is None:
        raise PostgresStorageError("custody_retention_timestamp_unavailable")
    occurred_at = timestamps.get("occurred_at")
    retain_until = timestamps.get("retain_until")
    if not isinstance(occurred_at, datetime) or not isinstance(retain_until, datetime):
        raise PostgresStorageError("custody_retention_timestamp_unavailable")
    return occurred_at, retain_until, legal_hold


def bootstrap_custody_evidence_timestamps(
    cursor: Any,
    *,
    retention_days: int,
    legal_hold: bool,
) -> tuple[datetime, datetime, bool]:
    """Use PostgreSQL's clock for the first tenant evidence record as well."""

    if isinstance(retention_days, bool) or not isinstance(retention_days, int) or retention_days < 1:
        raise PostgresStorageError("custody_retention_required")
    if not isinstance(legal_hold, bool):
        raise PostgresStorageError("custody_retention_required")
    cursor.execute(
        """
        WITH authoritative_clock AS (
            SELECT clock_timestamp() AS occurred_at
        )
        SELECT occurred_at,
               occurred_at + make_interval(days => %s) AS retain_until
        FROM authoritative_clock
        """,
        (retention_days,),
    )
    timestamps = cursor.fetchone()
    if timestamps is None:
        raise PostgresStorageError("custody_retention_timestamp_unavailable")
    occurred_at = timestamps.get("occurred_at")
    retain_until = timestamps.get("retain_until")
    if not isinstance(occurred_at, datetime) or not isinstance(retain_until, datetime):
        raise PostgresStorageError("custody_retention_timestamp_unavailable")
    return occurred_at, retain_until, legal_hold


def append_custody_audit_chain_entry(
    cursor: Any,
    *,
    organization_id: str,
    source_schema_id: str,
    source_schema_version: int,
    source_event_id: str,
    event: Mapping[str, object],
) -> None:
    """Append the exact v2 chain entry through the restricted SQL function."""

    cursor.execute(
        """
        SELECT chain_version, chain_epoch, next_sequence, previous_digest
        FROM custody_audit_chain_next_position(%s)
        """,
        (organization_id,),
    )
    position = cursor.fetchone()
    if position is None:
        raise PostgresStorageError("audit_chain_head_unavailable")
    try:
        chain_version = int(position["chain_version"])
        chain_epoch = int(position["chain_epoch"])
        sequence = int(position["next_sequence"])
        previous_digest = position["previous_digest"]
    except (KeyError, TypeError, ValueError) as error:
        raise PostgresStorageError("audit_chain_head_unavailable") from error
    if chain_version != AUDIT_CHAIN_VERSION or sequence < 1 or chain_epoch < 1:
        raise PostgresStorageError("audit_chain_head_unavailable")
    if previous_digest is not None and not isinstance(previous_digest, str):
        raise PostgresStorageError("audit_chain_head_unavailable")
    try:
        entry = build_custody_audit_chain_entry(
            event,
            source_schema_id=source_schema_id,
            source_schema_version=source_schema_version,
            source_event_id=source_event_id,
            chain_version=chain_version,
            chain_epoch=chain_epoch,
            sequence=sequence,
            previous_digest=previous_digest,
        )
        event_json = canonical_custody_evidence_json(entry["event"])
        event_digest = entry["event_digest"]
    except (AuditChainError, KeyError, TypeError) as error:
        raise PostgresStorageError("audit_chain_entry_malformed") from error
    if not isinstance(event_digest, str):
        raise PostgresStorageError("audit_chain_entry_malformed")
    cursor.execute(
        """
        SELECT custody_audit_chain_append_entry(
            %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        """,
        (
            organization_id,
            source_schema_id,
            source_schema_version,
            source_event_id,
            chain_epoch,
            sequence,
            previous_digest,
            event_digest,
            event_json,
        ),
    )
