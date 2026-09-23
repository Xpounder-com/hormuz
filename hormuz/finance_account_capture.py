"""Resolve and append one finance-account sidecar before provider egress."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Callable, Mapping
from uuid import uuid4

from ._finance_account_binding_schema import ATTEMPT_BINDING_TABLE, REGISTRATION_TABLE
from .finance_account_binding import FinanceAccountCandidate, UnavailableFinance
from .finance_collection import FinanceCollectionError
from .finance_account_evidence import (
    ATTEMPT_ACCOUNT_BINDING_FIELDS,
    ATTEMPT_ACCOUNT_BINDING_SCHEMA_ID,
    UNBOUND_REASONS,
    canonical_evidence_text,
    validate_finance_account_binding_event,
    validate_finance_attempt_account_binding_event,
)


class FinanceAccountCaptureError(ValueError):
    def __init__(self, code: str = "unavailable") -> None:
        if code != "unavailable":
            raise ValueError("finance_account_capture_error_invalid")
        self.code = code
        super().__init__(code)


class FinanceAccountCaptureSQL:
    """Minimal transaction-bound SQL adapter for both built-in stores."""

    def __init__(self, connection: Any, *, postgres: bool) -> None:
        self.connection = connection
        self.postgres = postgres

    def execute(self, statement: str, values: tuple[object, ...] = ()) -> Any:
        try:
            return self.connection.execute(
                statement.replace("?", "%s") if self.postgres else statement,
                values,
            )
        except sqlite3.Error:
            raise FinanceAccountCaptureError() from None
        except Exception as error:
            # Psycopg is optional for SQLite-only installs. Normalize its
            # DB-API failures without swallowing unrelated programming bugs.
            if type(error).__module__.split(".", 1)[0] == "psycopg":
                raise FinanceAccountCaptureError() from None
            raise

    def one(self, statement: str, values: tuple[object, ...] = ()) -> dict[str, object] | None:
        row = self.execute(statement, values).fetchone()
        return None if row is None else dict(row)

    def insert(self, table: str, row: Mapping[str, object]) -> None:
        if table != ATTEMPT_BINDING_TABLE:
            raise FinanceAccountCaptureError()
        self.execute(
            f"INSERT INTO {table} ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
            tuple(row.values()),
        )


def _registration_event(row: Mapping[str, object]) -> dict[str, object]:
    try:
        evidence_json = row.get("evidence_json")
        if not isinstance(evidence_json, str):
            raise ValueError
        event = json.loads(evidence_json)
        if (
            not isinstance(event, dict)
            or canonical_evidence_text(event) != evidence_json
            or set(row) != (set(event) - {"schema_id", "schema_version"}) | {"evidence_json"}
            or any(row[key] != value for key, value in event.items()
                   if key not in {"schema_id", "schema_version"})
        ):
            raise ValueError
        validate_finance_account_binding_event(event)
        return event
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise FinanceAccountCaptureError() from None


def _source_event(row: Mapping[str, object]) -> dict[str, object]:
    try:
        from .finance_collection import finance_collection_source_identity

        evidence_json = row.get("evidence_json")
        if not isinstance(evidence_json, str):
            raise ValueError
        event = json.loads(evidence_json)
        if (
            not isinstance(event, dict)
            or canonical_evidence_text(event) != evidence_json
            or finance_collection_source_identity(
                "hormuz.finance-source-binding-version", event
            ) != event.get("binding_event_id")
        ):
            raise ValueError
        return event
    except (FinanceCollectionError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise FinanceAccountCaptureError() from None


def _unbound_event(
    *,
    organization_id: str,
    request_attempt_id: str,
    captured_at: str,
    reason: str,
) -> dict[str, object]:
    event: dict[str, object] = {
        "schema_id": ATTEMPT_ACCOUNT_BINDING_SCHEMA_ID,
        "schema_version": 1,
        "organization_id": organization_id,
        "request_attempt_id": request_attempt_id,
        "event_id": str(uuid4()),
        "captured_at": captured_at,
        "state": "unbound",
        "reason_code": reason if reason in UNBOUND_REASONS else "binding_invalid",
    }
    for field in ATTEMPT_ACCOUNT_BINDING_FIELDS - set(event):
        event[field] = None
    validate_finance_attempt_account_binding_event(event)
    return event


def _resolve_event(
    sql: FinanceAccountCaptureSQL,
    *,
    organization_id: str,
    request_attempt_id: str,
    captured_at: str,
    selected: FinanceAccountCandidate | UnavailableFinance,
) -> dict[str, object]:
    if isinstance(selected, UnavailableFinance):
        return _unbound_event(
            organization_id=organization_id,
            request_attempt_id=request_attempt_id,
            captured_at=captured_at,
            reason=selected.reason,
        )
    if not isinstance(selected, FinanceAccountCandidate):
        return _unbound_event(
            organization_id=organization_id,
            request_attempt_id=request_attempt_id,
            captured_at=captured_at,
            reason="binding_invalid",
        )
    configured = selected.binding
    identity = selected.identity
    if configured.organization_id != organization_id:
        reason = "tenant_mismatch"
    else:
        reason = ""
    latest = sql.one(
        f"SELECT * FROM {REGISTRATION_TABLE} WHERE organization_id=? AND binding_id=? "
        "ORDER BY version DESC LIMIT 1",
        (organization_id, configured.binding_id),
    )
    exact = sql.one(
        f"SELECT * FROM {REGISTRATION_TABLE} WHERE organization_id=? AND binding_id=? AND version=?",
        (organization_id, configured.binding_id, configured.binding_version),
    )
    if not reason and exact is None:
        reason = "binding_missing" if latest is None else "binding_version_stale"
    if reason:
        return _unbound_event(
            organization_id=organization_id,
            request_attempt_id=request_attempt_id,
            captured_at=captured_at,
            reason=reason,
        )
    assert exact is not None
    registration = _registration_event(exact)
    if latest is None:
        raise FinanceAccountCaptureError()
    current = _registration_event(latest)
    if current["version"] != registration["version"]:
        reason = "binding_version_stale"
    elif registration["binding_state"] != "active":
        reason = "binding_revoked"
    elif registration["upstream_reference_id"] != identity.upstream_reference_id:
        reason = "upstream_reference_mismatch"
    elif registration["upstream_reference_version"] != identity.upstream_reference_version:
        reason = "upstream_reference_mismatch"
    elif registration["transport_profile"] != identity.transport_profile:
        reason = "unsupported_transport"
    elif (
        registration["inference_credential_reference_id"]
        != identity.inference_credential_reference_id
        or registration["inference_credential_reference_version"]
        != identity.inference_credential_reference_version
    ):
        reason = "inference_reference_mismatch"
    if reason:
        return _unbound_event(
            organization_id=organization_id,
            request_attempt_id=request_attempt_id,
            captured_at=captured_at,
            reason=reason,
        )

    source_exact = sql.one(
        "SELECT * FROM portfolio_finance_source_binding_versions "
        "WHERE organization_id=? AND binding_id=? AND version=?",
        (
            organization_id,
            registration["source_binding_id"],
            registration["source_binding_version"],
        ),
    )
    source_latest = sql.one(
        "SELECT * FROM portfolio_finance_source_binding_versions "
        "WHERE organization_id=? AND binding_id=? ORDER BY version DESC LIMIT 1",
        (organization_id, registration["source_binding_id"]),
    )
    if source_exact is None:
        reason = "source_binding_missing"
    elif source_latest is None:
        raise FinanceAccountCaptureError()
    else:
        source = _source_event(source_exact)
        current_source = _source_event(source_latest)
        if current_source["version"] != source["version"]:
            reason = "source_binding_version_stale"
        elif source["binding_state"] != "active":
            reason = "source_binding_revoked"
        elif source["fingerprint_key_version"] != registration["fingerprint_key_version"]:
            reason = "fingerprint_key_version_mismatch"
        elif any(
            source[field] != registration[target]
            for field, target in (
                ("content_digest", "source_binding_digest"),
                ("provider", "provider"),
                ("provider_account_fingerprint", "provider_account_fingerprint"),
                ("scope_kind", "scope_kind"),
                ("fingerprint_key_version", "fingerprint_key_version"),
            )
        ) or canonical_evidence_text(source["scope_fingerprints"]) != registration[
            "scope_fingerprints_json"
        ]:
            raise FinanceAccountCaptureError()
    if reason:
        return _unbound_event(
            organization_id=organization_id,
            request_attempt_id=request_attempt_id,
            captured_at=captured_at,
            reason=reason,
        )

    event = {
        "schema_id": ATTEMPT_ACCOUNT_BINDING_SCHEMA_ID,
        "schema_version": 1,
        "organization_id": organization_id,
        "request_attempt_id": request_attempt_id,
        "event_id": str(uuid4()),
        "captured_at": captured_at,
        "state": "bound",
        "reason_code": None,
        "binding_id": registration["binding_id"],
        "binding_version": registration["version"],
        "binding_digest": registration["content_digest"],
        "upstream_reference_id": registration["upstream_reference_id"],
        "upstream_reference_version": registration["upstream_reference_version"],
        "transport_profile": registration["transport_profile"],
        "inference_credential_reference_id": registration["inference_credential_reference_id"],
        "inference_credential_reference_version": registration["inference_credential_reference_version"],
        "source_binding_id": registration["source_binding_id"],
        "source_binding_version": registration["source_binding_version"],
        "source_binding_digest": registration["source_binding_digest"],
        "provider": registration["provider"],
        "provider_account_fingerprint": registration["provider_account_fingerprint"],
        "scope_kind": registration["scope_kind"],
        "scope_fingerprints_json": registration["scope_fingerprints_json"],
        "fingerprint_key_version": registration["fingerprint_key_version"],
    }
    validate_finance_attempt_account_binding_event(event)
    return event


def append_finance_attempt_account_binding(
    sql: FinanceAccountCaptureSQL,
    *,
    root: Mapping[str, object],
    selected: FinanceAccountCandidate | UnavailableFinance,
    append_audit: Callable[[Mapping[str, object]], None],
) -> Mapping[str, object]:
    """Append exactly one bound or explicit-unbound sidecar and audit source."""

    try:
        organization_id = root["organization_id"]
        request_attempt_id = root["attempt_id"]
        captured_at = root["created_at"]
        if not all(isinstance(value, str) for value in (
            organization_id, request_attempt_id, captured_at,
        )):
            raise ValueError
        event = _resolve_event(
            sql,
            organization_id=organization_id,
            request_attempt_id=request_attempt_id,
            captured_at=captured_at,
            selected=selected,
        )
        validate_finance_attempt_account_binding_event(event)
        row = {
            key: value
            for key, value in event.items()
            if key not in {"schema_id", "schema_version"}
        }
        row["evidence_json"] = canonical_evidence_text(event)
        sql.insert(ATTEMPT_BINDING_TABLE, row)
        append_audit(event)
        return event
    except FinanceAccountCaptureError:
        raise
    except (FinanceCollectionError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise FinanceAccountCaptureError() from None
