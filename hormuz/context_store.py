from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Protocol

from .context import (
    CLASSIFICATIONS,
    ContextArtifact,
    ContextError,
    ContextLifecycleSnapshot,
    ContextPack,
    ContextPrincipal,
    ContextRecord,
)


CONTEXT_STORE_SCHEMA_VERSION = 3
MAX_CONTEXT_CONTENT_BYTES = 25 * 1024 * 1024
_CLASSIFICATION_RANK = {name: index for index, name in enumerate(CLASSIFICATIONS)}
_LEGACY_EFFECTIVE_AT = datetime(1970, 1, 1, tzinfo=timezone.utc)


class ContextStoreError(RuntimeError):
    """Base failure for the durable governed-context boundary."""


class ContextConflict(ContextStoreError):
    pass


class ContextNotFound(ContextStoreError):
    pass


class ContextContentCodec(Protocol):
    """Storage codec boundary for a future KMS-backed content implementation."""

    codec_id: str

    def encode(self, plaintext: bytes) -> bytes: ...

    def decode(self, stored: bytes) -> bytes: ...


@dataclass(frozen=True)
class PlaintextLocalCodec:
    """Local-development codec. It is intentionally not production encryption."""

    codec_id: str = "plaintext-local-v1"

    def encode(self, plaintext: bytes) -> bytes:
        return plaintext

    def decode(self, stored: bytes) -> bytes:
        return stored


@dataclass(frozen=True)
class StoredContextRecord:
    record: ContextRecord
    version: int
    created_at: datetime
    updated_at: datetime

    def to_dict(self) -> dict[str, object]:
        return {
            **self.record.to_dict(),
            "storage": {
                "version": self.version,
                "created_at": _isoformat(self.created_at),
                "updated_at": _isoformat(self.updated_at),
            },
        }


@dataclass(frozen=True)
class IngestResult:
    stored: StoredContextRecord
    created: bool


@dataclass(frozen=True)
class StoredLifecycleSnapshot:
    organization_id: str
    repository_id: str
    branch: str
    snapshot: ContextLifecycleSnapshot
    version: int
    observed_at: datetime
    actor_id: str
    policy_version: str

    def to_dict(self) -> dict[str, object]:
        return {
            **self.snapshot.to_dict(),
            "organization_id": self.organization_id,
            "repository_id": self.repository_id,
            "branch": self.branch,
            "snapshot_sha256": self.snapshot.snapshot_sha256,
            "storage": {
                "version": self.version,
                "observed_at": _isoformat(self.observed_at),
                "actor_id": self.actor_id,
                "policy_version": self.policy_version,
            },
        }


class SQLiteContextRepository:
    """Single-node local repository; not the accepted enterprise persistence topology."""

    def __init__(self, path: Path, *, codec: ContextContentCodec | None = None):
        self.path = path
        self.codec = codec or PlaintextLocalCodec()
        self._lock = threading.RLock()
        if self.path.exists() and self.path.is_symlink():
            raise ContextStoreError("context_store_symlink_refused")
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._prepare_storage_file()
        self._initialize()

    def _prepare_storage_file(self) -> None:
        flags = (
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(self.path, flags, 0o600)
        except OSError as error:
            raise ContextStoreError("context_store_open_failed") from error
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ContextStoreError("context_store_regular_file_required")
            if hasattr(os, "fchmod"):
                os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
        if self.path.is_symlink():  # Defense for platforms without O_NOFOLLOW.
            raise ContextStoreError("context_store_symlink_refused")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA secure_delete = ON")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()
            self._secure_files()

    def _initialize(self) -> None:
        with self._connection() as connection:
            schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if schema_version > CONTEXT_STORE_SCHEMA_VERSION:
                raise ContextStoreError("context_store_schema_newer_than_binary")
            if schema_version not in {0, 1, 2, CONTEXT_STORE_SCHEMA_VERSION}:
                raise ContextStoreError("context_store_schema_migration_required")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                f"""
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS context_records (
                    organization_id TEXT NOT NULL,
                    id TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK (kind IN ('claim', 'decision')),
                    title TEXT NOT NULL,
                    content BLOB NOT NULL,
                    content_encoding TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    visibility TEXT NOT NULL CHECK (visibility IN ('organization', 'team', 'actor')),
                    scope_id TEXT NOT NULL,
                    classification TEXT NOT NULL CHECK (
                        classification IN ('public', 'internal', 'confidential', 'restricted')
                    ),
                    classification_rank INTEGER NOT NULL CHECK (classification_rank BETWEEN 0 AND 3),
                    source_uri TEXT NOT NULL,
                    source_revision TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL,
                    source_item_key TEXT NOT NULL,
                    repository_id TEXT,
                    branch TEXT,
                    verification TEXT NOT NULL CHECK (verification IN ('provisional', 'verified')),
                    verification_evidence_json TEXT NOT NULL,
                    effective_at TEXT NOT NULL,
                    verified_at TEXT,
                    expires_at TEXT,
                    supersedes_id TEXT,
                    invalidation_rules_json TEXT NOT NULL,
                    dependencies_json TEXT NOT NULL DEFAULT '[]',
                    assertion_key TEXT,
                    assertion_value TEXT,
                    tags_json TEXT NOT NULL,
                    version INTEGER NOT NULL CHECK (version >= 1),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (organization_id, id),
                    UNIQUE (organization_id, source_uri, source_revision, source_item_key)
                );
                CREATE INDEX IF NOT EXISTS idx_context_authorized_candidates
                    ON context_records (
                        organization_id, visibility, scope_id, classification_rank,
                        verification, effective_at, expires_at
                    );
                CREATE INDEX IF NOT EXISTS idx_context_repository_scope
                    ON context_records (organization_id, repository_id, branch);
                CREATE INDEX IF NOT EXISTS idx_context_supersedes
                    ON context_records (organization_id, supersedes_id);

                CREATE TABLE IF NOT EXISTS context_mutation_events (
                    id TEXT PRIMARY KEY,
                    occurred_at TEXT NOT NULL,
                    organization_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    action TEXT NOT NULL CHECK (action IN ('ingest', 'update', 'delete')),
                    prior_record_id TEXT,
                    prior_version INTEGER,
                    new_record_id TEXT,
                    new_version INTEGER,
                    policy_version TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    classification TEXT NOT NULL,
                    visibility TEXT NOT NULL,
                    repository_id TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_context_mutation_org_time
                    ON context_mutation_events (organization_id, occurred_at, id);
                CREATE INDEX IF NOT EXISTS idx_context_mutation_actor_time
                    ON context_mutation_events (organization_id, actor_id, occurred_at);

                CREATE TABLE IF NOT EXISTS context_access_events (
                    id TEXT PRIMARY KEY,
                    occurred_at TEXT NOT NULL,
                    organization_id TEXT NOT NULL,
                    team_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    action TEXT NOT NULL CHECK (action IN ('pack')),
                    pack_id TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    repository_id TEXT,
                    branch TEXT,
                    clearance TEXT NOT NULL CHECK (
                        clearance IN ('public', 'internal', 'confidential', 'restricted')
                    ),
                    include_provisional INTEGER NOT NULL CHECK (include_provisional IN (0, 1)),
                    selected_records INTEGER NOT NULL CHECK (selected_records >= 0),
                    eligible_records INTEGER NOT NULL CHECK (eligible_records >= 0),
                    matched_records INTEGER NOT NULL CHECK (matched_records >= 0),
                    estimated_tokens INTEGER NOT NULL CHECK (estimated_tokens >= 0),
                    lifecycle_outcome TEXT NOT NULL DEFAULT 'complete' CHECK (
                        lifecycle_outcome IN ('complete', 'partial', 'requires_resolution')
                    ),
                    excluded_records INTEGER NOT NULL DEFAULT 0 CHECK (excluded_records >= 0),
                    contradiction_groups INTEGER NOT NULL DEFAULT 0 CHECK (contradiction_groups >= 0)
                );
                CREATE INDEX IF NOT EXISTS idx_context_access_org_time
                    ON context_access_events (organization_id, occurred_at, id);
                CREATE INDEX IF NOT EXISTS idx_context_access_actor_time
                    ON context_access_events (organization_id, actor_id, occurred_at);

                CREATE TABLE IF NOT EXISTS context_lifecycle_snapshots (
                    organization_id TEXT NOT NULL,
                    repository_id TEXT NOT NULL,
                    branch TEXT NOT NULL,
                    repository_revision TEXT NOT NULL,
                    artifacts_json TEXT NOT NULL,
                    snapshot_sha256 TEXT NOT NULL,
                    version INTEGER NOT NULL CHECK (version >= 1),
                    observed_at TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    PRIMARY KEY (organization_id, repository_id, branch)
                );
                CREATE INDEX IF NOT EXISTS idx_context_lifecycle_observed
                    ON context_lifecycle_snapshots (organization_id, observed_at);

                CREATE TABLE IF NOT EXISTS context_lifecycle_events (
                    id TEXT PRIMARY KEY,
                    occurred_at TEXT NOT NULL,
                    organization_id TEXT NOT NULL,
                    repository_id TEXT NOT NULL,
                    branch TEXT NOT NULL,
                    action TEXT NOT NULL CHECK (action IN ('snapshot_create', 'snapshot_update')),
                    prior_version INTEGER,
                    new_version INTEGER NOT NULL,
                    snapshot_sha256 TEXT NOT NULL,
                    artifact_count INTEGER NOT NULL CHECK (artifact_count >= 0),
                    actor_id TEXT NOT NULL,
                    policy_version TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_context_lifecycle_events_org_time
                    ON context_lifecycle_events (organization_id, occurred_at, id);
                COMMIT;
                """
            )
            connection.execute("BEGIN IMMEDIATE")
            record_columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(context_records)")
            }
            if "dependencies_json" not in record_columns:
                connection.execute(
                    "ALTER TABLE context_records ADD COLUMN dependencies_json TEXT NOT NULL DEFAULT '[]'"
                )
            if "assertion_key" not in record_columns:
                connection.execute("ALTER TABLE context_records ADD COLUMN assertion_key TEXT")
            if "assertion_value" not in record_columns:
                connection.execute("ALTER TABLE context_records ADD COLUMN assertion_value TEXT")
            access_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(context_access_events)")
            }
            if "lifecycle_outcome" not in access_columns:
                connection.execute(
                    "ALTER TABLE context_access_events ADD COLUMN lifecycle_outcome "
                    "TEXT NOT NULL DEFAULT 'complete'"
                )
            if "excluded_records" not in access_columns:
                connection.execute(
                    "ALTER TABLE context_access_events ADD COLUMN excluded_records "
                    "INTEGER NOT NULL DEFAULT 0"
                )
            if "contradiction_groups" not in access_columns:
                connection.execute(
                    "ALTER TABLE context_access_events ADD COLUMN contradiction_groups "
                    "INTEGER NOT NULL DEFAULT 0"
                )
            connection.execute(f"PRAGMA user_version = {CONTEXT_STORE_SCHEMA_VERSION}")
            connection.commit()

    def ingest(
        self,
        record: ContextRecord,
        *,
        actor_id: str,
        policy_version: str,
        occurred_at: datetime | None = None,
    ) -> IngestResult:
        return self.ingest_many(
            [record],
            actor_id=actor_id,
            policy_version=policy_version,
            occurred_at=occurred_at,
        )[0]

    def ingest_many(
        self,
        records: Iterable[ContextRecord],
        *,
        actor_id: str,
        policy_version: str,
        occurred_at: datetime | None = None,
    ) -> list[IngestResult]:
        now = _utc(occurred_at or datetime.now(timezone.utc))
        _validate_mutation_identity(actor_id=actor_id, policy_version=policy_version)
        prepared: list[tuple[ContextRecord, bytes]] = []
        for record in records:
            normalized = _normalize_for_storage(record, actor_id=actor_id)
            encoded = _encode_content(self.codec, normalized.content)
            prepared.append((normalized, encoded))
        if not prepared:
            return []

        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return [
                self._ingest_prepared(
                    connection,
                    record=normalized,
                    encoded=encoded,
                    actor_id=actor_id,
                    policy_version=policy_version,
                    occurred_at=now,
                )
                for normalized, encoded in prepared
            ]

    def _ingest_prepared(
        self,
        connection: sqlite3.Connection,
        *,
        record: ContextRecord,
        encoded: bytes,
        actor_id: str,
        policy_version: str,
        occurred_at: datetime,
    ) -> IngestResult:
        existing_source = self._fetch_source_identity(connection, record)
        if existing_source is not None:
            stored = self._row_to_stored(existing_source)
            if _record_fingerprint(stored.record) == _record_fingerprint(record):
                return IngestResult(stored=stored, created=False)
            raise ContextConflict("context_source_revision_conflict")
        existing_id = self._fetch_row(
            connection,
            organization_id=record.organization_id,
            record_id=record.record_id,
        )
        if existing_id is not None:
            stored = self._row_to_stored(existing_id)
            if _record_fingerprint(stored.record) == _record_fingerprint(record):
                return IngestResult(stored=stored, created=False)
            raise ContextConflict("context_record_id_conflict")
        self._require_superseded_record(connection, record)
        now_value = _isoformat(occurred_at)
        connection.execute(
            """
            INSERT INTO context_records (
                organization_id, id, kind, title, content, content_encoding,
                content_sha256, owner_id, visibility, scope_id, classification,
                classification_rank, source_uri, source_revision, source_sha256,
                source_item_key, repository_id, branch, verification,
                verification_evidence_json, effective_at, verified_at, expires_at,
                supersedes_id, invalidation_rules_json, dependencies_json,
                assertion_key, assertion_value, tags_json, version,
                created_at, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?
            )
            """,
            _record_values(record, encoded, self.codec.codec_id) + (now_value, now_value),
        )
        self._insert_audit(
            connection,
            occurred_at=occurred_at,
            actor_id=actor_id,
            action="ingest",
            prior_record_id=None,
            prior_version=None,
            new_record_id=record.record_id,
            new_version=1,
            policy_version=policy_version,
            record=record,
        )
        row = self._fetch_row(
            connection,
            organization_id=record.organization_id,
            record_id=record.record_id,
        )
        if row is None:  # pragma: no cover - SQLite invariant
            raise ContextStoreError("context_ingest_missing_after_insert")
        return IngestResult(stored=self._row_to_stored(row), created=True)

    def update(
        self,
        record: ContextRecord,
        *,
        expected_version: int,
        actor_id: str,
        policy_version: str,
        occurred_at: datetime | None = None,
    ) -> StoredContextRecord:
        if isinstance(expected_version, bool) or not isinstance(expected_version, int) or expected_version < 1:
            raise ContextStoreError("context_expected_version_invalid")
        _validate_mutation_identity(actor_id=actor_id, policy_version=policy_version)
        now = _utc(occurred_at or datetime.now(timezone.utc))
        normalized = _normalize_for_storage(record, actor_id=actor_id)
        encoded = _encode_content(self.codec, normalized.content)

        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._fetch_row(
                connection,
                organization_id=normalized.organization_id,
                record_id=normalized.record_id,
            )
            if row is None:
                raise ContextNotFound("context_record_not_found")
            previous = self._row_to_stored(row)
            if previous.version != expected_version:
                raise ContextConflict("context_version_conflict")
            previous_source_identity = (
                previous.record.source_uri,
                previous.record.source_revision,
                previous.record.source_item_key,
            )
            new_source_identity = (
                normalized.source_uri,
                normalized.source_revision,
                normalized.source_item_key,
            )
            if (
                previous.record.content_sha256 != normalized.content_sha256
                and previous_source_identity == new_source_identity
            ):
                raise ContextConflict("context_source_revision_immutable")
            source_row = self._fetch_source_identity(connection, normalized)
            if source_row is not None and str(source_row["id"]) != normalized.record_id:
                raise ContextConflict("context_source_revision_conflict")
            self._require_superseded_record(connection, normalized)
            new_version = expected_version + 1
            cursor = connection.execute(
                """
                UPDATE context_records SET
                    kind = ?, title = ?, content = ?, content_encoding = ?,
                    content_sha256 = ?, owner_id = ?, visibility = ?, scope_id = ?,
                    classification = ?, classification_rank = ?, source_uri = ?,
                    source_revision = ?, source_sha256 = ?, source_item_key = ?,
                    repository_id = ?, branch = ?, verification = ?,
                    verification_evidence_json = ?, effective_at = ?, verified_at = ?,
                    expires_at = ?, supersedes_id = ?, invalidation_rules_json = ?,
                    dependencies_json = ?, assertion_key = ?, assertion_value = ?,
                    tags_json = ?, version = ?, updated_at = ?
                WHERE organization_id = ? AND id = ? AND version = ?
                """,
                _update_values(normalized, encoded, self.codec.codec_id)
                + (
                    new_version,
                    _isoformat(now),
                    normalized.organization_id,
                    normalized.record_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise ContextConflict("context_version_conflict")
            self._insert_audit(
                connection,
                occurred_at=now,
                actor_id=actor_id,
                action="update",
                prior_record_id=previous.record.record_id,
                prior_version=previous.version,
                new_record_id=normalized.record_id,
                new_version=new_version,
                policy_version=policy_version,
                record=normalized,
            )
            updated = self._fetch_row(
                connection,
                organization_id=normalized.organization_id,
                record_id=normalized.record_id,
            )
            if updated is None:  # pragma: no cover - SQLite invariant
                raise ContextStoreError("context_update_missing_after_write")
            return self._row_to_stored(updated)

    def delete(
        self,
        *,
        organization_id: str,
        record_id: str,
        expected_version: int,
        actor_id: str,
        policy_version: str,
        occurred_at: datetime | None = None,
    ) -> None:
        if not organization_id.strip() or not record_id.strip():
            raise ContextStoreError("context_delete_scope_invalid")
        if isinstance(expected_version, bool) or not isinstance(expected_version, int) or expected_version < 1:
            raise ContextStoreError("context_expected_version_invalid")
        _validate_mutation_identity(actor_id=actor_id, policy_version=policy_version)
        now = _utc(occurred_at or datetime.now(timezone.utc))
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._fetch_row(
                connection,
                organization_id=organization_id,
                record_id=record_id,
            )
            if row is None:
                raise ContextNotFound("context_record_not_found")
            stored = self._row_to_stored(row)
            if stored.version != expected_version:
                raise ContextConflict("context_version_conflict")
            referenced = connection.execute(
                """
                SELECT 1 FROM context_records
                WHERE organization_id = ? AND supersedes_id = ? LIMIT 1
                """,
                (organization_id, record_id),
            ).fetchone()
            if referenced is not None:
                raise ContextConflict("context_record_has_superseding_reference")
            cursor = connection.execute(
                "DELETE FROM context_records WHERE organization_id = ? AND id = ? AND version = ?",
                (organization_id, record_id, expected_version),
            )
            if cursor.rowcount != 1:
                raise ContextConflict("context_version_conflict")
            self._insert_audit(
                connection,
                occurred_at=now,
                actor_id=actor_id,
                action="delete",
                prior_record_id=record_id,
                prior_version=expected_version,
                new_record_id=None,
                new_version=None,
                policy_version=policy_version,
                record=stored.record,
            )
        self._checkpoint_deleted_content()

    def observe_lifecycle_snapshot(
        self,
        *,
        organization_id: str,
        repository_id: str,
        branch: str,
        snapshot: ContextLifecycleSnapshot,
        expected_version: int | None,
        actor_id: str,
        policy_version: str,
        occurred_at: datetime | None = None,
    ) -> StoredLifecycleSnapshot:
        """Atomically record trusted source/dependency state for later pack evaluation."""
        for name, value in (
            ("organization_id", organization_id),
            ("repository_id", repository_id),
            ("branch", branch),
        ):
            if (
                not isinstance(value, str)
                or not value.strip()
                or len(value.encode("utf-8")) > 512
                or any(character in value for character in ("\n", "\r", "\x00"))
            ):
                raise ContextStoreError(f"context_lifecycle_{name}_required")
        if not isinstance(snapshot, ContextLifecycleSnapshot):
            raise ContextStoreError("context_lifecycle_snapshot_required")
        if expected_version is not None and (
            isinstance(expected_version, bool)
            or not isinstance(expected_version, int)
            or expected_version < 1
        ):
            raise ContextStoreError("context_lifecycle_expected_version_invalid")
        _validate_mutation_identity(actor_id=actor_id, policy_version=policy_version)
        now = _utc(occurred_at or datetime.now(timezone.utc))
        artifacts_json = _json_artifact_tuple(snapshot.artifacts)
        snapshot_sha256 = snapshot.snapshot_sha256
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM context_lifecycle_snapshots
                WHERE organization_id = ? AND repository_id = ? AND branch = ?
                """,
                (organization_id, repository_id, branch),
            ).fetchone()
            if row is not None and str(row["snapshot_sha256"]) == snapshot_sha256:
                return self._row_to_lifecycle_snapshot(row)
            if row is None:
                if expected_version is not None:
                    raise ContextConflict("context_lifecycle_version_conflict")
                new_version = 1
                action = "snapshot_create"
                prior_version = None
                connection.execute(
                    """
                    INSERT INTO context_lifecycle_snapshots (
                        organization_id, repository_id, branch, repository_revision,
                        artifacts_json, snapshot_sha256, version, observed_at,
                        actor_id, policy_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        organization_id,
                        repository_id,
                        branch,
                        snapshot.repository_revision,
                        artifacts_json,
                        snapshot_sha256,
                        new_version,
                        _isoformat(now),
                        actor_id,
                        policy_version,
                    ),
                )
            else:
                prior_version = int(row["version"])
                if expected_version != prior_version:
                    raise ContextConflict("context_lifecycle_version_conflict")
                new_version = prior_version + 1
                action = "snapshot_update"
                cursor = connection.execute(
                    """
                    UPDATE context_lifecycle_snapshots
                    SET repository_revision = ?, artifacts_json = ?, snapshot_sha256 = ?,
                        version = ?, observed_at = ?, actor_id = ?, policy_version = ?
                    WHERE organization_id = ? AND repository_id = ? AND branch = ?
                      AND version = ?
                    """,
                    (
                        snapshot.repository_revision,
                        artifacts_json,
                        snapshot_sha256,
                        new_version,
                        _isoformat(now),
                        actor_id,
                        policy_version,
                        organization_id,
                        repository_id,
                        branch,
                        prior_version,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ContextConflict("context_lifecycle_version_conflict")
            connection.execute(
                """
                INSERT INTO context_lifecycle_events (
                    id, occurred_at, organization_id, repository_id, branch,
                    action, prior_version, new_version, snapshot_sha256,
                    artifact_count, actor_id, policy_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    _isoformat(now),
                    organization_id,
                    repository_id,
                    branch,
                    action,
                    prior_version,
                    new_version,
                    snapshot_sha256,
                    len(snapshot.artifacts),
                    actor_id,
                    policy_version,
                ),
            )
            stored = connection.execute(
                """
                SELECT * FROM context_lifecycle_snapshots
                WHERE organization_id = ? AND repository_id = ? AND branch = ?
                """,
                (organization_id, repository_id, branch),
            ).fetchone()
            if stored is None:  # pragma: no cover - SQLite invariant
                raise ContextStoreError("context_lifecycle_missing_after_write")
            return self._row_to_lifecycle_snapshot(stored)

    def get_lifecycle_snapshot(
        self,
        *,
        organization_id: str,
        repository_id: str,
        branch: str,
    ) -> StoredLifecycleSnapshot | None:
        for value in (organization_id, repository_id, branch):
            if (
                not isinstance(value, str)
                or not value.strip()
                or len(value.encode("utf-8")) > 512
                or any(character in value for character in ("\n", "\r", "\x00"))
            ):
                raise ContextStoreError("context_lifecycle_scope_required")
        with self._lock, self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM context_lifecycle_snapshots
                WHERE organization_id = ? AND repository_id = ? AND branch = ?
                """,
                (organization_id, repository_id, branch),
            ).fetchone()
        return None if row is None else self._row_to_lifecycle_snapshot(row)

    def list_authorized(
        self,
        principal: ContextPrincipal,
        *,
        as_of: datetime,
        include_provisional: bool = False,
    ) -> list[StoredContextRecord]:
        if not isinstance(principal, ContextPrincipal):
            raise ContextStoreError("context_principal_required")
        instant = _isoformat(_utc(as_of))
        allowed_classifications = CLASSIFICATIONS[: _CLASSIFICATION_RANK[principal.clearance] + 1]
        clauses = [
            "organization_id = ?",
            f"classification IN ({', '.join('?' for _ in allowed_classifications)})",
            "((visibility = 'organization' AND scope_id = ?) "
            "OR (visibility = 'team' AND scope_id = ?) "
            "OR (visibility = 'actor' AND scope_id = ?))",
            "(repository_id IS NULL OR repository_id = ?)",
            "(branch IS NULL OR branch = ?)",
            "effective_at <= ?",
            "(verified_at IS NULL OR verified_at <= ?)",
            "(expires_at IS NULL OR expires_at > ?)",
        ]
        parameters: list[object] = [
            principal.organization_id,
            *allowed_classifications,
            principal.organization_id,
            principal.team_id,
            principal.actor_id,
            principal.repository_id,
            principal.branch,
            instant,
            instant,
            instant,
        ]
        if not include_provisional:
            clauses.append("verification = 'verified'")
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                f"SELECT * FROM context_records WHERE {' AND '.join(clauses)} ORDER BY id",
                parameters,
            ).fetchall()
        return [
            self._row_to_stored(row)
            for row in rows
            if self._row_metadata_is_authorized(
                row,
                principal=principal,
                as_of=_utc(as_of),
                include_provisional=include_provisional,
            )
        ]

    def record_pack_read(
        self,
        pack: ContextPack,
        *,
        occurred_at: datetime | None = None,
    ) -> str:
        """Durably record metadata for a successful pack read before content leaves Hormuz."""
        if not isinstance(pack, ContextPack):
            raise ContextStoreError("context_pack_required")
        principal = pack.request.principal
        if not pack.pack_id.strip() or not pack.request.policy_version.strip():
            raise ContextStoreError("context_access_audit_invalid")
        counts = (
            len(pack.items),
            pack.eligible_records,
            pack.matched_records,
            pack.estimated_tokens,
            len(pack.exclusions),
            len(pack.contradictions),
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in counts
        ):
            raise ContextStoreError("context_access_audit_invalid")
        (
            selected_records,
            eligible_records,
            matched_records,
            _estimated_tokens,
            _excluded_records,
            _contradiction_groups,
        ) = counts
        if selected_records > matched_records or matched_records > eligible_records:
            raise ContextStoreError("context_access_audit_invalid")
        if pack.outcome not in {"complete", "partial", "requires_resolution"}:
            raise ContextStoreError("context_access_audit_invalid")
        event_id = str(uuid.uuid4())
        now = _utc(occurred_at or datetime.now(timezone.utc))
        try:
            with self._lock, self._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO context_access_events (
                        id, occurred_at, organization_id, team_id, actor_id, action,
                        pack_id, policy_version, repository_id, branch, clearance,
                        include_provisional, selected_records, eligible_records,
                        matched_records, estimated_tokens, lifecycle_outcome,
                        excluded_records, contradiction_groups
                    ) VALUES (?, ?, ?, ?, ?, 'pack', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        _isoformat(now),
                        principal.organization_id,
                        principal.team_id,
                        principal.actor_id,
                        pack.pack_id,
                        pack.request.policy_version,
                        principal.repository_id,
                        principal.branch,
                        principal.clearance,
                        int(pack.request.include_provisional),
                        len(pack.items),
                        pack.eligible_records,
                        pack.matched_records,
                        pack.estimated_tokens,
                        pack.outcome,
                        len(pack.exclusions),
                        len(pack.contradictions),
                    ),
                )
        except sqlite3.Error as error:
            raise ContextStoreError("context_access_audit_failed") from error
        return event_id

    def audit_events(
        self,
        *,
        organization_id: str,
        since: datetime | None = None,
    ) -> list[dict[str, object]]:
        clauses = ["organization_id = ?"]
        parameters: list[object] = [organization_id]
        if since is not None:
            clauses.append("occurred_at >= ?")
            parameters.append(_isoformat(_utc(since)))
        with self._lock, self._connection() as connection:
            mutation_rows = connection.execute(
                f"""
                SELECT
                    id, occurred_at, organization_id, actor_id, action,
                    prior_record_id, prior_version, new_record_id, new_version,
                    policy_version, kind, classification, visibility, repository_id
                FROM context_mutation_events
                WHERE {' AND '.join(clauses)}
                ORDER BY occurred_at, id
                """,
                parameters,
            ).fetchall()
            access_rows = connection.execute(
                f"""
                SELECT
                    id, occurred_at, organization_id, team_id, actor_id, action,
                    pack_id, policy_version, repository_id, branch, clearance,
                    include_provisional, selected_records, eligible_records,
                    matched_records, estimated_tokens, lifecycle_outcome,
                    excluded_records, contradiction_groups
                FROM context_access_events
                WHERE {' AND '.join(clauses)}
                ORDER BY occurred_at, id
                """,
                parameters,
            ).fetchall()
            lifecycle_rows = connection.execute(
                f"""
                SELECT
                    id, occurred_at, organization_id, repository_id, branch,
                    action, prior_version, new_version, snapshot_sha256,
                    artifact_count, actor_id, policy_version
                FROM context_lifecycle_events
                WHERE {' AND '.join(clauses)}
                ORDER BY occurred_at, id
                """,
                parameters,
            ).fetchall()
        events = [
            {
                "schema_version": 1,
                "event_type": "context.mutation",
                **dict(row),
            }
            for row in mutation_rows
        ]
        events.extend(
            {
                "schema_version": 1,
                "event_type": "context.read",
                **dict(row),
                "include_provisional": bool(row["include_provisional"]),
            }
            for row in access_rows
        )
        events.extend(
            {
                "schema_version": 1,
                "event_type": "context.lifecycle",
                **dict(row),
            }
            for row in lifecycle_rows
        )
        events.sort(key=lambda event: (str(event["occurred_at"]), str(event["id"])))
        return events

    def _fetch_row(
        self,
        connection: sqlite3.Connection,
        *,
        organization_id: str,
        record_id: str,
    ) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT * FROM context_records WHERE organization_id = ? AND id = ?",
            (organization_id, record_id),
        ).fetchone()

    def _fetch_source_identity(
        self,
        connection: sqlite3.Connection,
        record: ContextRecord,
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT * FROM context_records
            WHERE organization_id = ? AND source_uri = ? AND source_revision = ?
              AND source_item_key = ?
            """,
            (
                record.organization_id,
                record.source_uri,
                record.source_revision,
                record.source_item_key,
            ),
        ).fetchone()

    def _require_superseded_record(
        self,
        connection: sqlite3.Connection,
        record: ContextRecord,
    ) -> None:
        if record.supersedes_id is None:
            return
        target = self._fetch_row(
            connection,
            organization_id=record.organization_id,
            record_id=record.supersedes_id,
        )
        if target is None:
            raise ContextConflict("context_superseded_record_not_found")
        current_id: str | None = record.supersedes_id
        seen = {record.record_id}
        while current_id is not None:
            if current_id in seen:
                raise ContextConflict("context_supersession_cycle")
            seen.add(current_id)
            row = self._fetch_row(
                connection,
                organization_id=record.organization_id,
                record_id=current_id,
            )
            current_id = str(row["supersedes_id"]) if row is not None and row["supersedes_id"] else None

    def _row_to_lifecycle_snapshot(self, row: sqlite3.Row) -> StoredLifecycleSnapshot:
        try:
            artifacts = _json_artifacts(row, "artifacts_json")
            snapshot = ContextLifecycleSnapshot(
                repository_revision=str(row["repository_revision"]),
                artifacts=artifacts,
            )
        except (ContextError, ContextStoreError) as error:
            raise ContextStoreError("context_lifecycle_snapshot_corrupt") from error
        if snapshot.snapshot_sha256 != str(row["snapshot_sha256"]):
            raise ContextStoreError("context_lifecycle_snapshot_integrity_failed")
        version = int(row["version"])
        if version < 1:
            raise ContextStoreError("context_lifecycle_snapshot_corrupt")
        return StoredLifecycleSnapshot(
            organization_id=str(row["organization_id"]),
            repository_id=str(row["repository_id"]),
            branch=str(row["branch"]),
            snapshot=snapshot,
            version=version,
            observed_at=_parse_required_datetime(row["observed_at"]),
            actor_id=str(row["actor_id"]),
            policy_version=str(row["policy_version"]),
        )

    def _row_to_stored(self, row: sqlite3.Row) -> StoredContextRecord:
        if str(row["content_encoding"]) != self.codec.codec_id:
            raise ContextStoreError("context_content_codec_unavailable")
        content = _decode_content(self.codec, row["content"])
        actual_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if actual_hash != str(row["content_sha256"]):
            raise ContextStoreError("context_content_integrity_failed")
        try:
            record = ContextRecord(
                record_id=str(row["id"]),
                record_kind=str(row["kind"]),
                title=str(row["title"]),
                content=content,
                owner_id=str(row["owner_id"]),
                organization_id=str(row["organization_id"]),
                visibility=str(row["visibility"]),
                scope_id=str(row["scope_id"]),
                classification=str(row["classification"]),
                source_uri=str(row["source_uri"]),
                source_revision=str(row["source_revision"]),
                source_sha256=str(row["source_sha256"]),
                source_item_key=str(row["source_item_key"]),
                repository_id=_nullable_row_string(row, "repository_id"),
                branch=_nullable_row_string(row, "branch"),
                verification=str(row["verification"]),
                verification_evidence=_json_string_tuple(row, "verification_evidence_json"),
                effective_at=_parse_required_datetime(row["effective_at"]),
                verified_at=_parse_nullable_datetime(row["verified_at"]),
                expires_at=_parse_nullable_datetime(row["expires_at"]),
                supersedes_id=_nullable_row_string(row, "supersedes_id"),
                invalidation_rules=_json_string_tuple(row, "invalidation_rules_json"),
                dependencies=_json_artifacts(row, "dependencies_json"),
                assertion_key=_nullable_row_string(row, "assertion_key"),
                assertion_value=_nullable_row_string(row, "assertion_value"),
                tags=_json_string_tuple(row, "tags_json"),
            )
        except ContextError as error:
            raise ContextStoreError("context_record_corrupt") from error
        return StoredContextRecord(
            record=record,
            version=int(row["version"]),
            created_at=_parse_required_datetime(row["created_at"]),
            updated_at=_parse_required_datetime(row["updated_at"]),
        )

    def _row_metadata_is_authorized(
        self,
        row: sqlite3.Row,
        *,
        principal: ContextPrincipal,
        as_of: datetime,
        include_provisional: bool,
    ) -> bool:
        classification = str(row["classification"])
        if classification not in _CLASSIFICATION_RANK:
            raise ContextStoreError("context_record_classification_corrupt")
        if int(row["classification_rank"]) != _CLASSIFICATION_RANK[classification]:
            raise ContextStoreError("context_record_classification_corrupt")
        if str(row["organization_id"]) != principal.organization_id:
            return False
        visibility = str(row["visibility"])
        expected_scopes = {
            "organization": principal.organization_id,
            "team": principal.team_id,
            "actor": principal.actor_id,
        }
        if visibility not in expected_scopes:
            raise ContextStoreError("context_record_visibility_corrupt")
        if str(row["scope_id"]) != expected_scopes[visibility]:
            return False
        if _CLASSIFICATION_RANK[classification] > _CLASSIFICATION_RANK[principal.clearance]:
            return False
        repository_id = _nullable_row_string(row, "repository_id")
        branch = _nullable_row_string(row, "branch")
        if repository_id is not None and repository_id != principal.repository_id:
            return False
        if branch is not None and branch != principal.branch:
            return False
        verification = str(row["verification"])
        if verification not in {"provisional", "verified"}:
            raise ContextStoreError("context_record_verification_corrupt")
        if verification != "verified" and not include_provisional:
            return False
        effective_at = _parse_required_datetime(row["effective_at"])
        verified_at = _parse_nullable_datetime(row["verified_at"])
        expires_at = _parse_nullable_datetime(row["expires_at"])
        if effective_at > as_of or (verified_at is not None and verified_at > as_of):
            return False
        if expires_at is not None and expires_at <= as_of:
            return False
        return True

    def _insert_audit(
        self,
        connection: sqlite3.Connection,
        *,
        occurred_at: datetime,
        actor_id: str,
        action: str,
        prior_record_id: str | None,
        prior_version: int | None,
        new_record_id: str | None,
        new_version: int | None,
        policy_version: str,
        record: ContextRecord,
    ) -> None:
        connection.execute(
            """
            INSERT INTO context_mutation_events (
                id, occurred_at, organization_id, actor_id, action,
                prior_record_id, prior_version, new_record_id, new_version,
                policy_version, kind, classification, visibility, repository_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                _isoformat(occurred_at),
                record.organization_id,
                actor_id,
                action,
                prior_record_id,
                prior_version,
                new_record_id,
                new_version,
                policy_version,
                record.record_kind,
                record.classification,
                record.visibility,
                record.repository_id,
            ),
        )

    def _secure_files(self) -> None:
        for candidate in (self.path, Path(f"{self.path}-wal"), Path(f"{self.path}-shm")):
            try:
                os.chmod(candidate, 0o600)
            except FileNotFoundError:
                pass

    def _checkpoint_deleted_content(self) -> None:
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                connection.close()
                self._secure_files()


def _normalize_for_storage(record: ContextRecord, *, actor_id: str) -> ContextRecord:
    if not isinstance(record, ContextRecord):
        raise ContextStoreError("context_record_required")
    normalized = replace(
        record,
        owner_id=record.owner_id or actor_id,
        source_sha256=record.source_sha256 or record.content_sha256,
        source_item_key=record.source_item_key or record.record_id,
        effective_at=record.effective_at or record.verified_at or _LEGACY_EFFECTIVE_AT,
    )
    if normalized.verification == "verified" and not normalized.verification_evidence:
        raise ContextStoreError("context_verified_evidence_required")
    return normalized


def _encode_content(codec: ContextContentCodec, content: str) -> bytes:
    try:
        encoded = codec.encode(content.encode("utf-8"))
    except Exception as error:
        raise ContextStoreError("context_content_encode_failed") from error
    if not isinstance(encoded, bytes):
        raise ContextStoreError("context_content_codec_invalid")
    if len(encoded) > MAX_CONTEXT_CONTENT_BYTES:
        raise ContextStoreError("context_content_too_large")
    return encoded


def _decode_content(codec: ContextContentCodec, stored: object) -> str:
    if not isinstance(stored, (bytes, bytearray, memoryview)):
        raise ContextStoreError("context_content_codec_invalid")
    try:
        plaintext = codec.decode(bytes(stored))
    except Exception as error:
        raise ContextStoreError("context_content_decode_failed") from error
    if not isinstance(plaintext, bytes):
        raise ContextStoreError("context_content_codec_invalid")
    try:
        return plaintext.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ContextStoreError("context_content_decode_failed") from error


def _validate_mutation_identity(*, actor_id: str, policy_version: str) -> None:
    if not isinstance(actor_id, str) or not actor_id.strip():
        raise ContextStoreError("context_actor_required")
    if not isinstance(policy_version, str) or not policy_version.strip():
        raise ContextStoreError("context_policy_version_required")


def _record_values(
    record: ContextRecord,
    encoded_content: bytes,
    codec_id: str,
) -> tuple[object, ...]:
    return (
        record.organization_id,
        record.record_id,
        record.record_kind,
        record.title,
        encoded_content,
        codec_id,
        record.content_sha256,
        record.owner_id,
        record.visibility,
        record.scope_id,
        record.classification,
        _CLASSIFICATION_RANK[record.classification],
        record.source_uri,
        record.source_revision,
        record.source_sha256,
        record.source_item_key,
        record.repository_id,
        record.branch,
        record.verification,
        _json_tuple(record.verification_evidence),
        _isoformat_required(record.effective_at),
        _isoformat(record.verified_at),
        _isoformat(record.expires_at),
        record.supersedes_id,
        _json_tuple(record.invalidation_rules),
        _json_artifact_tuple(record.dependencies),
        record.assertion_key,
        record.assertion_value,
        _json_tuple(record.tags),
    )


def _update_values(
    record: ContextRecord,
    encoded_content: bytes,
    codec_id: str,
) -> tuple[object, ...]:
    values = _record_values(record, encoded_content, codec_id)
    return values[2:]


def _record_fingerprint(record: ContextRecord) -> str:
    canonical = json.dumps(record.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _json_tuple(values: tuple[str, ...]) -> str:
    return json.dumps(list(values), separators=(",", ":"), ensure_ascii=False)


def _json_artifact_tuple(values: tuple[ContextArtifact, ...]) -> str:
    return json.dumps(
        [item.to_dict() for item in sorted(values)],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _json_string_tuple(row: sqlite3.Row, name: str) -> tuple[str, ...]:
    try:
        value = json.loads(str(row[name]))
    except json.JSONDecodeError as error:
        raise ContextStoreError("context_record_json_corrupt") from error
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ContextStoreError("context_record_json_corrupt")
    return tuple(value)


def _json_artifacts(row: sqlite3.Row, name: str) -> tuple[ContextArtifact, ...]:
    try:
        value = json.loads(str(row[name]))
        if not isinstance(value, list):
            raise ContextError("context artifacts must be an array")
        return tuple(ContextArtifact.from_dict(item) for item in value)
    except (json.JSONDecodeError, ContextError) as error:
        raise ContextStoreError("context_record_json_corrupt") from error


def _nullable_row_string(row: sqlite3.Row, name: str) -> str | None:
    value = row[name]
    return str(value) if value is not None else None


def _parse_required_datetime(value: object) -> datetime:
    parsed = _parse_nullable_datetime(value)
    if parsed is None:
        raise ContextStoreError("context_record_datetime_corrupt")
    return parsed


def _parse_nullable_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as error:
        raise ContextStoreError("context_record_datetime_corrupt") from error
    if parsed.tzinfo is None:
        raise ContextStoreError("context_record_datetime_corrupt")
    return parsed.astimezone(timezone.utc)


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ContextStoreError("context_timestamp_timezone_required")
    return value.astimezone(timezone.utc)


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _utc(value).isoformat().replace("+00:00", "Z")


def _isoformat_required(value: datetime | None) -> str:
    formatted = _isoformat(value)
    if formatted is None:  # pragma: no cover - normalization invariant
        raise ContextStoreError("context_effective_at_required")
    return formatted
