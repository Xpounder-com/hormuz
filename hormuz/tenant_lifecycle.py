"""Owner-only PostgreSQL tenant deactivation, export, and delayed purge.

This module intentionally handles only control-plane metadata and encrypted
database exports. It is not a document-memory feature and never logs or emits
the exported tenant rows.
"""

from __future__ import annotations

import base64
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
from typing import Any, Callable, Iterator

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import Identity
from .postgres import (
    DEFAULT_POSTGRES_DSN_ENV,
    DEFAULT_POSTGRES_RUNTIME_ROLE,
    DEFAULT_POSTGRES_SCHEMA,
    POSTGRES_SCHEMA_VERSION,
    TENANT_TABLES,
    PostgresStorageError,
    TenantContext,
    _open_connection,
    postgres_dsn_from_env,
    tenant_transaction,
    validate_postgres_identifier,
    validate_tenant_id,
)


TENANT_EXPORT_SCHEMA = "hormuz.tenant-export.v1"
TENANT_EXPORT_ENVELOPE_SCHEMA = "hormuz.tenant-export-envelope.v1"
TENANT_EXPORT_ALGORITHM = "AES-256-GCM"

_ENVIRONMENT_NAME = re.compile(r"[A-Z][A-Z0-9_]{0,127}\Z")
_REASON_CODE = re.compile(r"[a-z][a-z0-9_.:-]{0,127}\Z")
_EXPORT_ID = re.compile(r"tex_[a-f0-9]{32}\Z")
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_BASE64URL = re.compile(r"[A-Za-z0-9_-]+\Z")
_MAX_EXPORT_BYTES = 512 * 1024 * 1024


class TenantLifecycleError(PostgresStorageError):
    """A content-free tenant lifecycle failure."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class TenantLifecycleStatus:
    organization_id: str
    state: str
    state_version: int
    deactivated_at: str | None
    purge_not_before: str | None
    changed: bool
    revoked_sessions: int = 0
    invalidated_enrollments: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "hormuz.tenant-lifecycle-status.v1",
            "organization_id": self.organization_id,
            "state": self.state,
            "state_version": self.state_version,
            "deactivated_at": self.deactivated_at,
            "purge_not_before": self.purge_not_before,
            "changed": self.changed,
            "revoked_sessions": self.revoked_sessions,
            "invalidated_enrollments": self.invalidated_enrollments,
        }


@dataclass(frozen=True)
class TenantExportReceipt:
    organization_id: str
    export_id: str
    created_at: str
    lifecycle_state_version: int
    payload_sha256: str
    ciphertext_sha256: str
    table_counts: dict[str, int]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "hormuz.tenant-export-receipt.v1",
            "organization_id": self.organization_id,
            "export_id": self.export_id,
            "created_at": self.created_at,
            "lifecycle_state_version": self.lifecycle_state_version,
            "payload_sha256": self.payload_sha256,
            "ciphertext_sha256": self.ciphertext_sha256,
            "table_counts": dict(sorted(self.table_counts.items())),
        }


@dataclass(frozen=True)
class TenantPurgeResult:
    organization_id: str
    purged_at: str
    export_id: str
    ciphertext_sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "hormuz.tenant-purge.v1",
            "organization_id": self.organization_id,
            "purged_at": self.purged_at,
            "export_id": self.export_id,
            "ciphertext_sha256": self.ciphertext_sha256,
        }


@dataclass(frozen=True)
class TenantReonboardResult:
    organization_id: str
    changed: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "hormuz.tenant-reonboard.v1",
            "organization_id": self.organization_id,
            "changed": self.changed,
            "next_step": "identities_sync",
        }


@dataclass(frozen=True)
class TenantRestorePlan:
    organization_id: str
    exported_at: str
    migration_version: int
    payload_sha256: str
    table_counts: dict[str, int]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "hormuz.tenant-restore-plan.v1",
            "organization_id": self.organization_id,
            "exported_at": self.exported_at,
            "migration_version": self.migration_version,
            "payload_sha256": self.payload_sha256,
            "table_counts": dict(sorted(self.table_counts.items())),
            "restore_automated": False,
        }


@dataclass(frozen=True)
class _LifecycleRow:
    state: str
    state_version: int
    deactivated_at: datetime | None
    purge_not_before: datetime | None
    required_export_id: str | None
    required_export_ciphertext_sha256: str | None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise TenantLifecycleError("tenant_lifecycle_corrupt_timestamp")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _required_environment_name(value: str) -> str:
    if not isinstance(value, str) or _ENVIRONMENT_NAME.fullmatch(value) is None:
        raise TenantLifecycleError("invalid_export_key_environment")
    return value


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _base64url_decode(value: object, *, code: str) -> bytes:
    if not isinstance(value, str) or not value or _BASE64URL.fullmatch(value) is None:
        raise TenantLifecycleError(code)
    padding = "=" * (-len(value) % 4)
    try:
        return base64.b64decode(value + padding, altchars=b"-_", validate=True)
    except (ValueError, TypeError):
        raise TenantLifecycleError(code) from None


def export_key_from_env(
    encryption_key_env: str,
    *,
    environ: dict[str, str] | None = None,
) -> bytes:
    """Load an exact 256-bit export key without exposing its value."""

    name = _required_environment_name(encryption_key_env)
    environment = os.environ if environ is None else environ
    value = environment.get(name)
    key = _base64url_decode(value, code="tenant_export_key_unavailable")
    if len(key) != 32:
        raise TenantLifecycleError("tenant_export_key_unavailable")
    return key


def _require_reason_code(value: str) -> str:
    if not isinstance(value, str) or _REASON_CODE.fullmatch(value) is None:
        raise TenantLifecycleError("invalid_tenant_deactivation_reason")
    return value


def _require_export_id(value: str) -> str:
    if not isinstance(value, str) or _EXPORT_ID.fullmatch(value) is None:
        raise TenantLifecycleError("invalid_tenant_export_id")
    return value


def _require_sha256(value: str, *, code: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise TenantLifecycleError(code)
    return value


def _json_value(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TenantLifecycleError("tenant_export_value_invalid")
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {
            "$hormuz_type": "bytes",
            "base64url": _base64url_encode(bytes(value)),
        }
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise TenantLifecycleError("tenant_export_value_invalid")
        return {
            "$hormuz_type": "datetime",
            "iso8601": value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
    if isinstance(value, Decimal):
        return {"$hormuz_type": "decimal", "value": format(value, "f")}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise TenantLifecycleError("tenant_export_value_invalid")
        return {key: _json_value(item) for key, item in value.items()}
    raise TenantLifecycleError("tenant_export_value_invalid")


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError):
        raise TenantLifecycleError("tenant_export_encoding_invalid") from None


def _encrypt_snapshot(snapshot: dict[str, object], key: bytes) -> tuple[bytes, str, str]:
    payload = _canonical_json(snapshot)
    payload_sha256 = hashlib.sha256(payload).hexdigest()
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(
        nonce,
        payload,
        TENANT_EXPORT_ENVELOPE_SCHEMA.encode("ascii"),
    )
    ciphertext_sha256 = hashlib.sha256(ciphertext).hexdigest()
    envelope = {
        "schema": TENANT_EXPORT_ENVELOPE_SCHEMA,
        "algorithm": TENANT_EXPORT_ALGORITHM,
        "nonce": _base64url_encode(nonce),
        "ciphertext": _base64url_encode(ciphertext),
        "ciphertext_sha256": ciphertext_sha256,
    }
    return _canonical_json(envelope), payload_sha256, ciphertext_sha256


def _private_create(path: Path, data: bytes) -> None:
    if not path.name or path.exists():
        raise TenantLifecycleError("tenant_export_output_exists")
    parent = path.parent
    if not parent.exists() or not parent.is_dir():
        raise TenantLifecycleError("tenant_export_output_parent_unavailable")
    descriptor: int | None = None
    created = False
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
        with os.fdopen(descriptor, "wb", closefd=True) as file_pointer:
            descriptor = None
            file_pointer.write(data)
            file_pointer.flush()
            os.fsync(file_pointer.fileno())
        os.chmod(path, 0o600)
        if stat.S_IMODE(path.stat().st_mode) != 0o600:
            raise TenantLifecycleError("tenant_export_output_permissions_invalid")
    except TenantLifecycleError:
        if descriptor is not None:
            os.close(descriptor)
        if created:
            try:
                path.unlink()
            except OSError:
                pass
        raise
    except OSError:
        if descriptor is not None:
            os.close(descriptor)
        if created:
            try:
                path.unlink()
            except OSError:
                pass
        raise TenantLifecycleError("tenant_export_output_unavailable") from None


def _read_private_export(path: Path) -> dict[str, object]:
    try:
        details = path.stat()
    except OSError:
        raise TenantLifecycleError("tenant_export_input_unavailable") from None
    if not stat.S_ISREG(details.st_mode) or details.st_size <= 0 or details.st_size > _MAX_EXPORT_BYTES:
        raise TenantLifecycleError("tenant_export_input_invalid")
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise TenantLifecycleError("tenant_export_input_invalid") from None
    if not isinstance(value, dict) or set(value) != {
        "schema", "algorithm", "nonce", "ciphertext", "ciphertext_sha256"
    }:
        raise TenantLifecycleError("tenant_export_envelope_invalid")
    if (
        value.get("schema") != TENANT_EXPORT_ENVELOPE_SCHEMA
        or value.get("algorithm") != TENANT_EXPORT_ALGORITHM
    ):
        raise TenantLifecycleError("tenant_export_envelope_invalid")
    return value


def _decrypt_snapshot(path: Path, key: bytes) -> tuple[dict[str, object], str]:
    envelope = _read_private_export(path)
    nonce = _base64url_decode(envelope.get("nonce"), code="tenant_export_envelope_invalid")
    ciphertext = _base64url_decode(
        envelope.get("ciphertext"), code="tenant_export_envelope_invalid"
    )
    expected_ciphertext_sha256 = _require_sha256(
        envelope.get("ciphertext_sha256"), code="tenant_export_envelope_invalid"
    )
    if len(nonce) != 12 or len(ciphertext) < 16:
        raise TenantLifecycleError("tenant_export_envelope_invalid")
    if not hmac.compare_digest(
        hashlib.sha256(ciphertext).hexdigest(), expected_ciphertext_sha256
    ):
        raise TenantLifecycleError("tenant_export_integrity_invalid")
    try:
        plaintext = AESGCM(key).decrypt(
            nonce,
            ciphertext,
            TENANT_EXPORT_ENVELOPE_SCHEMA.encode("ascii"),
        )
        snapshot = json.loads(plaintext)
    except (InvalidTag, ValueError, UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise TenantLifecycleError("tenant_export_decryption_failed") from None
    if not isinstance(snapshot, dict):
        raise TenantLifecycleError("tenant_export_snapshot_invalid")
    return snapshot, hashlib.sha256(plaintext).hexdigest()


class TenantLifecycleService:
    """Schema-owner lifecycle operations with encrypted external artifacts."""

    def __init__(
        self,
        dsn: str,
        *,
        schema: str = DEFAULT_POSTGRES_SCHEMA,
        connect: object | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        if not isinstance(dsn, str) or not dsn:
            raise TenantLifecycleError("postgres_dsn_unavailable")
        self._dsn = dsn
        self.schema = validate_postgres_identifier(schema, "postgres_schema")
        self._connect = connect
        self._clock = clock or _utc_now
        self._qualified = '"' + self.schema + '"'

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise TenantLifecycleError("tenant_lifecycle_clock_invalid")
        return value.astimezone(timezone.utc)

    @contextmanager
    def _owner_transaction(self, organization_id: str) -> Iterator[object]:
        validate_tenant_id(organization_id)
        connection = _open_connection(self._dsn, self._connect)  # type: ignore[arg-type]
        try:
            with connection.transaction():
                with connection.cursor() as cursor:  # type: ignore[attr-defined]
                    cursor.execute(
                        "SELECT pg_get_userbyid(nspowner), current_user "
                        "FROM pg_namespace WHERE nspname = %s",
                        (self.schema,),
                    )
                    owner = cursor.fetchone()
                    if (
                        not isinstance(owner, (tuple, list))
                        or len(owner) != 2
                        or owner[0] != owner[1]
                    ):
                        raise TenantLifecycleError("tenant_lifecycle_role_not_schema_owner")
                    cursor.execute(
                        "SELECT set_config('hormuz.tenant_id', %s, true), "
                        "set_config('hormuz.principal_id', 'tenant-lifecycle', true), "
                        "set_config('hormuz.client_id', 'hormuz-cli', true), "
                        "set_config('hormuz.authorization_version', '1', true)",
                        (organization_id,),
                    )
                    if cursor.fetchone() != (
                        organization_id,
                        "tenant-lifecycle",
                        "hormuz-cli",
                        "1",
                    ):
                        raise TenantLifecycleError("tenant_lifecycle_scope_not_bound")
                    cursor.execute(f"SET LOCAL search_path TO {self._qualified}, pg_catalog")
                yield connection
        except TenantLifecycleError:
            raise
        except PostgresStorageError as error:
            raise TenantLifecycleError(error.code) from None
        except Exception:
            raise TenantLifecycleError("tenant_lifecycle_store_unavailable") from None
        finally:
            connection.close()

    @staticmethod
    def _state_from_row(row: object) -> _LifecycleRow:
        if not isinstance(row, (tuple, list)) or len(row) != 6:
            raise TenantLifecycleError("tenant_lifecycle_state_invalid")
        state = row[0]
        state_version = row[1]
        if state not in {"active", "deactivated", "purge_scheduled"}:
            raise TenantLifecycleError("tenant_lifecycle_state_invalid")
        if isinstance(state_version, bool) or not isinstance(state_version, int) or state_version < 1:
            raise TenantLifecycleError("tenant_lifecycle_state_invalid")
        deactivated_at = row[2]
        purge_not_before = row[3]
        if deactivated_at is not None and (
            not isinstance(deactivated_at, datetime) or deactivated_at.tzinfo is None
        ):
            raise TenantLifecycleError("tenant_lifecycle_state_invalid")
        if purge_not_before is not None and (
            not isinstance(purge_not_before, datetime) or purge_not_before.tzinfo is None
        ):
            raise TenantLifecycleError("tenant_lifecycle_state_invalid")
        export_id = row[4]
        ciphertext_sha256 = row[5]
        if export_id is not None:
            _require_export_id(str(export_id))
        if ciphertext_sha256 is not None:
            _require_sha256(str(ciphertext_sha256), code="tenant_lifecycle_state_invalid")
        return _LifecycleRow(
            state=state,
            state_version=state_version,
            deactivated_at=deactivated_at,
            purge_not_before=purge_not_before,
            required_export_id=str(export_id) if export_id is not None else None,
            required_export_ciphertext_sha256=(
                str(ciphertext_sha256) if ciphertext_sha256 is not None else None
            ),
        )

    def _state_for_update(self, connection: object, organization_id: str) -> _LifecycleRow:
        with connection.cursor() as cursor:  # type: ignore[attr-defined]
            cursor.execute(
                "SELECT state, state_version, deactivated_at, purge_not_before, "
                "required_export_id, required_export_ciphertext_sha256 "
                "FROM gateway_tenant_lifecycle WHERE tenant_id = %s FOR UPDATE",
                (organization_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise TenantLifecycleError("tenant_lifecycle_missing")
        return self._state_from_row(row)

    def _exclusive_lifecycle_lock(self, connection: object, organization_id: str) -> None:
        with connection.cursor() as cursor:  # type: ignore[attr-defined]
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                ("hormuz:tenant-lifecycle:" + self.schema + ":" + organization_id,),
            )
            cursor.fetchone()

    @staticmethod
    def _status(
        organization_id: str,
        row: _LifecycleRow,
        *,
        changed: bool,
        revoked_sessions: int = 0,
        invalidated_enrollments: int = 0,
    ) -> TenantLifecycleStatus:
        return TenantLifecycleStatus(
            organization_id=organization_id,
            state=row.state,
            state_version=row.state_version,
            deactivated_at=_iso(row.deactivated_at),
            purge_not_before=_iso(row.purge_not_before),
            changed=changed,
            revoked_sessions=revoked_sessions,
            invalidated_enrollments=invalidated_enrollments,
        )

    def status(self, *, organization_id: str) -> TenantLifecycleStatus:
        validate_tenant_id(organization_id)
        with self._owner_transaction(organization_id) as connection:
            row = self._state_for_update(connection, organization_id)
        return self._status(organization_id, row, changed=False)

    def deactivate(
        self,
        *,
        organization_id: str,
        reason_code: str,
    ) -> TenantLifecycleStatus:
        validate_tenant_id(organization_id)
        reason_code = _require_reason_code(reason_code)
        now = self._now()
        with self._owner_transaction(organization_id) as connection:
            self._exclusive_lifecycle_lock(connection, organization_id)
            current = self._state_for_update(connection, organization_id)
            if current.state != "active":
                return self._status(organization_id, current, changed=False)
            with connection.cursor() as cursor:  # type: ignore[attr-defined]
                cursor.execute(
                    "UPDATE gateway_tenant_lifecycle SET state = 'deactivated', "
                    "state_version = state_version + 1, deactivated_at = %s, "
                    "deactivation_reason_code = %s, updated_at = %s "
                    "WHERE tenant_id = %s AND state = 'active'",
                    (now, reason_code, now, organization_id),
                )
                if cursor.rowcount != 1:
                    raise TenantLifecycleError("tenant_lifecycle_state_conflict")
                cursor.execute(
                    "UPDATE tenants SET authorization_version = authorization_version + 1, "
                    "updated_at = %s WHERE tenant_id = %s",
                    (now, organization_id),
                )
                if cursor.rowcount != 1:
                    raise TenantLifecycleError("tenant_lifecycle_missing")
                cursor.execute(
                    "UPDATE gateway_human_sessions SET revoked_at = COALESCE(revoked_at, %s) "
                    "WHERE tenant_id = %s AND revoked_at IS NULL",
                    (now, organization_id),
                )
                revoked_sessions = max(int(cursor.rowcount), 0)
                cursor.execute(
                    "UPDATE gateway_session_enrollments SET status = 'failed', secret_hash = NULL, "
                    "state_hash = NULL, browser_cookie_hash = NULL, encrypted_flow = NULL "
                    "WHERE tenant_id = %s AND status IN ('pending', 'authorizing', 'exchanging', 'authorized')",
                    (organization_id,),
                )
                invalidated_enrollments = max(int(cursor.rowcount), 0)
            row = _LifecycleRow(
                state="deactivated",
                state_version=current.state_version + 1,
                deactivated_at=now,
                purge_not_before=None,
                required_export_id=None,
                required_export_ciphertext_sha256=None,
            )
        return self._status(
            organization_id,
            row,
            changed=True,
            revoked_sessions=revoked_sessions,
            invalidated_enrollments=invalidated_enrollments,
        )

    def reactivate(self, *, organization_id: str) -> TenantLifecycleStatus:
        validate_tenant_id(organization_id)
        now = self._now()
        with self._owner_transaction(organization_id) as connection:
            self._exclusive_lifecycle_lock(connection, organization_id)
            current = self._state_for_update(connection, organization_id)
            if current.state == "active":
                return self._status(organization_id, current, changed=False)
            with connection.cursor() as cursor:  # type: ignore[attr-defined]
                cursor.execute(
                    "UPDATE gateway_tenant_lifecycle SET state = 'active', "
                    "state_version = state_version + 1, deactivated_at = NULL, "
                    "deactivation_reason_code = NULL, purge_not_before = NULL, "
                    "required_export_id = NULL, required_export_ciphertext_sha256 = NULL, "
                    "updated_at = %s WHERE tenant_id = %s AND state <> 'active'",
                    (now, organization_id),
                )
                if cursor.rowcount != 1:
                    raise TenantLifecycleError("tenant_lifecycle_state_conflict")
            row = _LifecycleRow(
                state="active",
                state_version=current.state_version + 1,
                deactivated_at=None,
                purge_not_before=None,
                required_export_id=None,
                required_export_ciphertext_sha256=None,
            )
        return self._status(organization_id, row, changed=True)

    def _snapshot(self, *, organization_id: str) -> tuple[dict[str, object], int, dict[str, int]]:
        with self._owner_transaction(organization_id) as connection:
            self._exclusive_lifecycle_lock(connection, organization_id)
            state = self._state_for_update(connection, organization_id)
            if state.state != "deactivated":
                raise TenantLifecycleError("tenant_export_requires_deactivation")
            tables: dict[str, list[dict[str, object]]] = {}
            for table in TENANT_TABLES:
                with connection.cursor() as cursor:  # type: ignore[attr-defined]
                    cursor.execute(f'SELECT * FROM "{table}" WHERE tenant_id = %s', (organization_id,))
                    description = getattr(cursor, "description", None)
                    if not isinstance(description, (list, tuple)) or not description:
                        raise TenantLifecycleError("tenant_export_columns_unavailable")
                    columns = tuple(getattr(column, "name", None) for column in description)
                    if any(not isinstance(column, str) or not column for column in columns):
                        raise TenantLifecycleError("tenant_export_columns_unavailable")
                    rows: list[dict[str, object]] = []
                    for row in cursor.fetchall():
                        if not isinstance(row, (tuple, list)) or len(row) != len(columns):
                            raise TenantLifecycleError("tenant_export_row_invalid")
                        rows.append(
                            {
                                column: _json_value(value)
                                for column, value in zip(columns, row, strict=True)
                            }
                        )
                    tables[table] = rows
            exported_at = self._now()
            snapshot = {
                "schema": TENANT_EXPORT_SCHEMA,
                "organization_id": organization_id,
                "exported_at": _iso(exported_at),
                "migration_version": POSTGRES_SCHEMA_VERSION,
                "tables": tables,
            }
        return snapshot, state.state_version, {name: len(rows) for name, rows in tables.items()}

    def export(
        self,
        *,
        organization_id: str,
        encryption_key: bytes,
        output: Path,
    ) -> TenantExportReceipt:
        validate_tenant_id(organization_id)
        if not isinstance(encryption_key, bytes) or len(encryption_key) != 32:
            raise TenantLifecycleError("tenant_export_key_unavailable")
        snapshot, state_version, table_counts = self._snapshot(organization_id=organization_id)
        envelope, payload_sha256, ciphertext_sha256 = _encrypt_snapshot(snapshot, encryption_key)
        _private_create(output, envelope)
        export_id = "tex_" + secrets.token_hex(16)
        created_at = self._now()
        with self._owner_transaction(organization_id) as connection:
            self._exclusive_lifecycle_lock(connection, organization_id)
            current = self._state_for_update(connection, organization_id)
            if current.state != "deactivated" or current.state_version != state_version:
                raise TenantLifecycleError("tenant_export_state_changed")
            with connection.cursor() as cursor:  # type: ignore[attr-defined]
                cursor.execute(
                    "INSERT INTO gateway_tenant_exports ("
                    "tenant_id, export_id, created_at, export_schema, encryption_algorithm, "
                    "lifecycle_state_version, payload_sha256, ciphertext_sha256, table_counts_json"
                    ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)",
                    (
                        organization_id,
                        export_id,
                        created_at,
                        TENANT_EXPORT_SCHEMA,
                        TENANT_EXPORT_ALGORITHM,
                        state_version,
                        payload_sha256,
                        ciphertext_sha256,
                        json.dumps(table_counts, sort_keys=True, separators=(",", ":")),
                    ),
                )
        return TenantExportReceipt(
            organization_id=organization_id,
            export_id=export_id,
            created_at=_iso(created_at) or "",
            lifecycle_state_version=state_version,
            payload_sha256=payload_sha256,
            ciphertext_sha256=ciphertext_sha256,
            table_counts=table_counts,
        )

    def schedule_purge(
        self,
        *,
        organization_id: str,
        export_id: str,
        retention_days: int,
    ) -> TenantLifecycleStatus:
        validate_tenant_id(organization_id)
        export_id = _require_export_id(export_id)
        if isinstance(retention_days, bool) or not isinstance(retention_days, int) or not 1 <= retention_days <= 3650:
            raise TenantLifecycleError("invalid_tenant_retention_days")
        now = self._now()
        with self._owner_transaction(organization_id) as connection:
            self._exclusive_lifecycle_lock(connection, organization_id)
            current = self._state_for_update(connection, organization_id)
            if current.state == "purge_scheduled":
                if current.required_export_id == export_id:
                    return self._status(organization_id, current, changed=False)
                raise TenantLifecycleError("tenant_purge_schedule_conflict")
            if current.state != "deactivated":
                raise TenantLifecycleError("tenant_purge_requires_deactivation")
            with connection.cursor() as cursor:  # type: ignore[attr-defined]
                cursor.execute(
                    "SELECT lifecycle_state_version, ciphertext_sha256 FROM gateway_tenant_exports "
                    "WHERE tenant_id = %s AND export_id = %s",
                    (organization_id, export_id),
                )
                receipt = cursor.fetchone()
                if (
                    not isinstance(receipt, (tuple, list))
                    or len(receipt) != 2
                    or int(receipt[0]) != current.state_version
                ):
                    raise TenantLifecycleError("tenant_export_not_authoritative")
                ciphertext_sha256 = _require_sha256(
                    str(receipt[1]), code="tenant_export_not_authoritative"
                )
                purge_not_before = now + timedelta(days=retention_days)
                cursor.execute(
                    "UPDATE gateway_tenant_lifecycle SET state = 'purge_scheduled', "
                    "state_version = state_version + 1, purge_not_before = %s, "
                    "required_export_id = %s, required_export_ciphertext_sha256 = %s, "
                    "updated_at = %s WHERE tenant_id = %s AND state = 'deactivated'",
                    (purge_not_before, export_id, ciphertext_sha256, now, organization_id),
                )
                if cursor.rowcount != 1:
                    raise TenantLifecycleError("tenant_lifecycle_state_conflict")
            row = _LifecycleRow(
                state="purge_scheduled",
                state_version=current.state_version + 1,
                deactivated_at=current.deactivated_at,
                purge_not_before=purge_not_before,
                required_export_id=export_id,
                required_export_ciphertext_sha256=ciphertext_sha256,
            )
        return self._status(organization_id, row, changed=True)

    def purge(
        self,
        *,
        organization_id: str,
        export_id: str,
        confirm_ciphertext_sha256: str,
    ) -> TenantPurgeResult:
        validate_tenant_id(organization_id)
        export_id = _require_export_id(export_id)
        confirmation = _require_sha256(
            confirm_ciphertext_sha256, code="invalid_tenant_purge_confirmation"
        )
        now = self._now()
        with self._owner_transaction(organization_id) as connection:
            self._exclusive_lifecycle_lock(connection, organization_id)
            current = self._state_for_update(connection, organization_id)
            if current.state != "purge_scheduled":
                raise TenantLifecycleError("tenant_purge_not_scheduled")
            if current.purge_not_before is None or current.purge_not_before > now:
                raise TenantLifecycleError("tenant_purge_retention_pending")
            if (
                current.required_export_id != export_id
                or current.required_export_ciphertext_sha256 is None
                or not hmac.compare_digest(
                    current.required_export_ciphertext_sha256,
                    confirmation,
                )
            ):
                raise TenantLifecycleError("tenant_purge_confirmation_mismatch")
            with connection.cursor() as cursor:  # type: ignore[attr-defined]
                cursor.execute(
                    "SELECT 1 FROM gateway_tenant_purge_tombstones WHERE tenant_id = %s",
                    (organization_id,),
                )
                if cursor.fetchone() is not None:
                    raise TenantLifecycleError("tenant_purge_tombstone_exists")
                cursor.execute(
                    "INSERT INTO gateway_tenant_purge_tombstones ("
                    "tenant_id, purged_at, export_id, export_ciphertext_sha256"
                    ") VALUES (%s, %s, %s, %s)",
                    (
                        organization_id,
                        now,
                        export_id,
                        current.required_export_ciphertext_sha256,
                    ),
                )
                cursor.execute("DELETE FROM tenants WHERE tenant_id = %s", (organization_id,))
                if cursor.rowcount != 1:
                    raise TenantLifecycleError("tenant_lifecycle_missing")
        return TenantPurgeResult(
            organization_id=organization_id,
            purged_at=_iso(now) or "",
            export_id=export_id,
            ciphertext_sha256=confirmation,
        )

    def re_onboard(self, *, organization_id: str) -> TenantReonboardResult:
        """Clear only the owner-only tombstone before an explicit identity sync."""

        validate_tenant_id(organization_id)
        with self._owner_transaction(organization_id) as connection:
            self._exclusive_lifecycle_lock(connection, organization_id)
            with connection.cursor() as cursor:  # type: ignore[attr-defined]
                cursor.execute("SELECT 1 FROM tenants WHERE tenant_id = %s", (organization_id,))
                if cursor.fetchone() is not None:
                    raise TenantLifecycleError("tenant_reonboard_requires_absent_tenant")
                cursor.execute(
                    "DELETE FROM gateway_tenant_purge_tombstones WHERE tenant_id = %s",
                    (organization_id,),
                )
                changed = cursor.rowcount == 1
        return TenantReonboardResult(organization_id=organization_id, changed=changed)

    @staticmethod
    def restore_plan(*, input_path: Path, encryption_key: bytes) -> TenantRestorePlan:
        if not isinstance(encryption_key, bytes) or len(encryption_key) != 32:
            raise TenantLifecycleError("tenant_export_key_unavailable")
        snapshot, payload_sha256 = _decrypt_snapshot(input_path, encryption_key)
        if set(snapshot) != {
            "schema", "organization_id", "exported_at", "migration_version", "tables"
        }:
            raise TenantLifecycleError("tenant_export_snapshot_invalid")
        if snapshot.get("schema") != TENANT_EXPORT_SCHEMA:
            raise TenantLifecycleError("tenant_export_snapshot_invalid")
        organization_id = snapshot.get("organization_id")
        if not isinstance(organization_id, str):
            raise TenantLifecycleError("tenant_export_snapshot_invalid")
        validate_tenant_id(organization_id)
        exported_at = snapshot.get("exported_at")
        migration_version = snapshot.get("migration_version")
        tables = snapshot.get("tables")
        if (
            not isinstance(exported_at, str)
            or isinstance(migration_version, bool)
            or not isinstance(migration_version, int)
            or not 1 <= migration_version <= POSTGRES_SCHEMA_VERSION
            or not isinstance(tables, dict)
            or set(tables) != set(TENANT_TABLES)
        ):
            raise TenantLifecycleError("tenant_export_snapshot_invalid")
        table_counts: dict[str, int] = {}
        for table, rows in tables.items():
            if not isinstance(table, str) or not isinstance(rows, list) or not all(
                isinstance(row, dict) for row in rows
            ):
                raise TenantLifecycleError("tenant_export_snapshot_invalid")
            table_counts[table] = len(rows)
        return TenantRestorePlan(
            organization_id=organization_id,
            exported_at=exported_at,
            migration_version=migration_version,
            payload_sha256=payload_sha256,
            table_counts=table_counts,
        )


class TenantLifecycleRuntimeGate:
    """Fail closed before a PostgreSQL-backed gateway accepts an identity."""

    def __init__(
        self,
        dsn: str,
        *,
        schema: str = DEFAULT_POSTGRES_SCHEMA,
        runtime_role: str = DEFAULT_POSTGRES_RUNTIME_ROLE,
        connect: object | None = None,
    ):
        if not isinstance(dsn, str) or not dsn:
            raise TenantLifecycleError("postgres_dsn_unavailable")
        self._dsn = dsn
        self.schema = validate_postgres_identifier(schema, "postgres_schema")
        self.runtime_role = validate_postgres_identifier(runtime_role, "runtime_role")
        self._connect = connect

    def require_active(self, identity: Identity) -> None:
        connection = _open_connection(self._dsn, self._connect)  # type: ignore[arg-type]
        try:
            with tenant_transaction(
                connection,
                TenantContext(
                    identity.organization_id,
                    identity.actor_id,
                    "hormuz-authentication",
                    1,
                ),
                runtime_role=self.runtime_role,
                schema=self.schema,
            ):
                pass
        except PostgresStorageError as error:
            raise TenantLifecycleError(error.code) from None
        finally:
            connection.close()


def tenant_lifecycle_service_from_env(
    *,
    dsn_env: str = DEFAULT_POSTGRES_DSN_ENV,
    schema: str = DEFAULT_POSTGRES_SCHEMA,
    environ: dict[str, str] | None = None,
) -> TenantLifecycleService:
    return TenantLifecycleService(
        postgres_dsn_from_env(environ, dsn_env=dsn_env),
        schema=schema,
    )
