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
from datetime import datetime, timedelta, timezone
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
from .context_lifecycle import (
    ContextEvidence,
    LifecyclePolicy,
    evaluate_record_lifecycle,
    lifecycle_subject_sha256,
)


CONTEXT_STORE_SCHEMA_VERSION = 4
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
class StoredLifecycleEvidence:
    evidence: ContextEvidence
    subject_sha256: str
    actor_id: str
    policy_version: str
    created_at: datetime

    def to_dict(self) -> dict[str, object]:
        return {
            **self.evidence.to_dict(),
            "subject_sha256": self.subject_sha256,
            "storage": {
                "actor_id": self.actor_id,
                "policy_version": self.policy_version,
                "created_at": _isoformat(self.created_at),
            },
        }


@dataclass(frozen=True)
class LifecycleEvidenceResult:
    stored: StoredLifecycleEvidence
    created: bool


@dataclass(frozen=True)
class ContextRevalidationJob:
    job_id: str
    organization_id: str
    repository_id: str
    branch: str
    snapshot_sha256: str
    snapshot_version: int
    policy_version: str
    policy_sha256: str
    record_set_sha256: str
    evidence_set_sha256: str
    status: str
    cursor_record_id: str | None
    total_records: int
    processed_records: int
    promoted_records: int
    invalidated_records: int
    unchanged_records: int
    deferred_records: int
    created_at: datetime
    updated_at: datetime
    created_by: str
    last_actor_id: str
    lease_owner: str | None
    lease_expires_at: datetime | None

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "hormuz.context-revalidation-job.v1",
            "job_id": self.job_id,
            "organization_id": self.organization_id,
            "repository_id": self.repository_id,
            "branch": self.branch,
            "snapshot_sha256": self.snapshot_sha256,
            "snapshot_version": self.snapshot_version,
            "policy_version": self.policy_version,
            "policy_sha256": self.policy_sha256,
            "record_set_sha256": self.record_set_sha256,
            "evidence_set_sha256": self.evidence_set_sha256,
            "status": self.status,
            "cursor_record_id": self.cursor_record_id,
            "total_records": self.total_records,
            "processed_records": self.processed_records,
            "promoted_records": self.promoted_records,
            "invalidated_records": self.invalidated_records,
            "unchanged_records": self.unchanged_records,
            "deferred_records": self.deferred_records,
            "created_at": _isoformat(self.created_at),
            "updated_at": _isoformat(self.updated_at),
            "created_by": self.created_by,
            "last_actor_id": self.last_actor_id,
            "lease": {
                "owner": self.lease_owner,
                "expires_at": _isoformat(self.lease_expires_at),
            },
        }


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
            if schema_version not in {0, 1, 2, 3, CONTEXT_STORE_SCHEMA_VERSION}:
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

                CREATE TABLE IF NOT EXISTS context_evidence_events (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    record_version INTEGER NOT NULL CHECK (record_version >= 1),
                    subject_sha256 TEXT NOT NULL,
                    signal TEXT NOT NULL,
                    signal_family TEXT NOT NULL,
                    evidence_ref_sha256 TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    policy_version TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_context_evidence_subject
                    ON context_evidence_events (
                        organization_id, record_id, subject_sha256, observed_at, id
                    );
                CREATE INDEX IF NOT EXISTS idx_context_evidence_org_time
                    ON context_evidence_events (organization_id, created_at, id);

                CREATE TABLE IF NOT EXISTS context_revalidation_jobs (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL,
                    repository_id TEXT NOT NULL,
                    branch TEXT NOT NULL,
                    snapshot_sha256 TEXT NOT NULL,
                    snapshot_version INTEGER NOT NULL CHECK (snapshot_version >= 1),
                    policy_version TEXT NOT NULL,
                    policy_sha256 TEXT NOT NULL,
                    record_set_sha256 TEXT NOT NULL,
                    evidence_set_sha256 TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN ('pending', 'running', 'completed', 'superseded', 'failed')
                    ),
                    cursor_record_id TEXT,
                    total_records INTEGER NOT NULL CHECK (total_records >= 0),
                    processed_records INTEGER NOT NULL DEFAULT 0 CHECK (processed_records >= 0),
                    promoted_records INTEGER NOT NULL DEFAULT 0 CHECK (promoted_records >= 0),
                    invalidated_records INTEGER NOT NULL DEFAULT 0 CHECK (invalidated_records >= 0),
                    unchanged_records INTEGER NOT NULL DEFAULT 0 CHECK (unchanged_records >= 0),
                    deferred_records INTEGER NOT NULL DEFAULT 0 CHECK (deferred_records >= 0),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    last_actor_id TEXT NOT NULL,
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    UNIQUE (
                        organization_id, repository_id, branch,
                        snapshot_sha256, snapshot_version, policy_sha256,
                        record_set_sha256, evidence_set_sha256
                    )
                );
                CREATE INDEX IF NOT EXISTS idx_context_revalidation_scope
                    ON context_revalidation_jobs (
                        organization_id, repository_id, branch, created_at, id
                    );

                CREATE TABLE IF NOT EXISTS context_revalidation_changes (
                    job_id TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    prior_verification TEXT NOT NULL CHECK (
                        prior_verification IN ('provisional', 'verified')
                    ),
                    target_verification TEXT NOT NULL CHECK (
                        target_verification IN ('provisional', 'verified')
                    ),
                    reason TEXT NOT NULL,
                    evidence_count INTEGER NOT NULL CHECK (evidence_count >= 0),
                    matched_path_id TEXT,
                    prior_version INTEGER NOT NULL CHECK (prior_version >= 1),
                    new_version INTEGER NOT NULL CHECK (new_version >= 1),
                    PRIMARY KEY (job_id, record_id),
                    FOREIGN KEY (job_id) REFERENCES context_revalidation_jobs(id)
                );

                CREATE TABLE IF NOT EXISTS context_revalidation_events (
                    id TEXT PRIMARY KEY,
                    occurred_at TEXT NOT NULL,
                    organization_id TEXT NOT NULL,
                    repository_id TEXT NOT NULL,
                    branch TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    action TEXT NOT NULL CHECK (
                        action IN ('job_start', 'batch_complete', 'job_superseded')
                    ),
                    status TEXT NOT NULL,
                    batch_records INTEGER NOT NULL CHECK (batch_records >= 0),
                    actor_id TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    FOREIGN KEY (job_id) REFERENCES context_revalidation_jobs(id)
                );
                CREATE INDEX IF NOT EXISTS idx_context_revalidation_events_org_time
                    ON context_revalidation_events (organization_id, occurred_at, id);
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
        new_records_must_be_provisional: bool = False,
    ) -> IngestResult:
        return self.ingest_many(
            [record],
            actor_id=actor_id,
            policy_version=policy_version,
            occurred_at=occurred_at,
            new_records_must_be_provisional=new_records_must_be_provisional,
        )[0]

    def ingest_many(
        self,
        records: Iterable[ContextRecord],
        *,
        actor_id: str,
        policy_version: str,
        occurred_at: datetime | None = None,
        new_records_must_be_provisional: bool = False,
    ) -> list[IngestResult]:
        now = _utc(occurred_at or datetime.now(timezone.utc))
        _validate_mutation_identity(actor_id=actor_id, policy_version=policy_version)
        if not isinstance(new_records_must_be_provisional, bool):
            raise ContextStoreError("context_provisional_import_policy_invalid")
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
                    new_records_must_be_provisional=new_records_must_be_provisional,
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
        new_records_must_be_provisional: bool,
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
        if new_records_must_be_provisional and record.verification != "provisional":
            raise ContextConflict("context_lifecycle_new_record_must_be_provisional")
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

    def get_record(self, organization_id: str, record_id: str) -> StoredContextRecord:
        _validate_scope_value(organization_id, "organization_id")
        _validate_scope_value(record_id, "record_id")
        with self._lock, self._connection() as connection:
            row = self._fetch_row(
                connection,
                organization_id=organization_id,
                record_id=record_id,
            )
        if row is None:
            raise ContextNotFound("context_record_not_found")
        return self._row_to_stored(row)

    def record_lifecycle_evidence(
        self,
        evidence: ContextEvidence,
        *,
        actor_id: str,
        policy_version: str,
        occurred_at: datetime | None = None,
    ) -> LifecycleEvidenceResult:
        """Persist an immutable, subject-bound evidence fingerprint without its raw reference."""
        if not isinstance(evidence, ContextEvidence):
            raise ContextStoreError("context_lifecycle_evidence_required")
        _validate_mutation_identity(actor_id=actor_id, policy_version=policy_version)
        now = _utc(occurred_at or datetime.now(timezone.utc))
        if evidence.observed_at.astimezone(timezone.utc) > now + timedelta(minutes=5):
            raise ContextConflict("context_evidence_observed_at_in_future")
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM context_evidence_events WHERE id = ?",
                (evidence.evidence_id,),
            ).fetchone()
            if existing is not None:
                stored = self._row_to_lifecycle_evidence(existing)
                if stored.evidence != evidence:
                    raise ContextConflict("context_evidence_id_conflict")
                return LifecycleEvidenceResult(stored=stored, created=False)
            row = self._fetch_row(
                connection,
                organization_id=evidence.organization_id,
                record_id=evidence.record_id,
            )
            if row is None:
                raise ContextConflict("context_evidence_record_not_found")
            stored_record = self._row_to_stored(row)
            if stored_record.version != evidence.record_version:
                raise ContextConflict("context_evidence_record_version_conflict")
            subject_sha256 = lifecycle_subject_sha256(stored_record.record)
            connection.execute(
                """
                INSERT INTO context_evidence_events (
                    id, organization_id, record_id, record_version, subject_sha256,
                    signal, signal_family, evidence_ref_sha256, observed_at,
                    created_at, actor_id, policy_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence.evidence_id,
                    evidence.organization_id,
                    evidence.record_id,
                    evidence.record_version,
                    subject_sha256,
                    evidence.signal,
                    evidence.signal_family,
                    evidence.evidence_ref_sha256,
                    _isoformat(evidence.observed_at),
                    _isoformat(now),
                    actor_id,
                    policy_version,
                ),
            )
            stored_row = connection.execute(
                "SELECT * FROM context_evidence_events WHERE id = ?",
                (evidence.evidence_id,),
            ).fetchone()
            if stored_row is None:  # pragma: no cover - SQLite invariant
                raise ContextStoreError("context_evidence_missing_after_write")
            return LifecycleEvidenceResult(
                stored=self._row_to_lifecycle_evidence(stored_row),
                created=True,
            )

    def start_revalidation_job(
        self,
        *,
        organization_id: str,
        repository_id: str,
        branch: str,
        policy: LifecyclePolicy,
        actor_id: str,
        occurred_at: datetime | None = None,
    ) -> ContextRevalidationJob:
        for name, value in (
            ("organization_id", organization_id),
            ("repository_id", repository_id),
            ("branch", branch),
        ):
            _validate_scope_value(value, name)
        if not isinstance(policy, LifecyclePolicy):
            raise ContextStoreError("context_lifecycle_policy_required")
        _validate_mutation_identity(actor_id=actor_id, policy_version=policy.policy_version)
        now = _utc(occurred_at or datetime.now(timezone.utc))
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            snapshot_row = connection.execute(
                """
                SELECT * FROM context_lifecycle_snapshots
                WHERE organization_id = ? AND repository_id = ? AND branch = ?
                """,
                (organization_id, repository_id, branch),
            ).fetchone()
            if snapshot_row is None:
                raise ContextNotFound("context_revalidation_snapshot_not_found")
            stored_snapshot = self._row_to_lifecycle_snapshot(snapshot_row)
            scope_rows = connection.execute(
                """
                SELECT * FROM context_records
                WHERE organization_id = ? AND repository_id = ?
                  AND (branch IS NULL OR branch = ?)
                ORDER BY id
                """,
                (organization_id, repository_id, branch),
            ).fetchall()
            scoped_records = [self._row_to_stored(row) for row in scope_rows]
            total_records = len(scoped_records)
            record_set_sha256 = _lifecycle_record_set_sha256(scoped_records)
            evidence_set_sha256 = self._lifecycle_evidence_set_sha256(
                connection,
                scoped_records,
            )
            canonical = json.dumps(
                {
                    "organization_id": organization_id,
                    "repository_id": repository_id,
                    "branch": branch,
                    "snapshot_sha256": stored_snapshot.snapshot.snapshot_sha256,
                    "snapshot_version": stored_snapshot.version,
                    "policy_sha256": policy.policy_sha256,
                    "record_set_sha256": record_set_sha256,
                    "evidence_set_sha256": evidence_set_sha256,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            job_id = "ctxjob_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
            existing = connection.execute(
                "SELECT * FROM context_revalidation_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if existing is not None:
                return self._row_to_revalidation_job(existing)
            now_value = _isoformat(now)
            connection.execute(
                """
                INSERT INTO context_revalidation_jobs (
                    id, organization_id, repository_id, branch, snapshot_sha256,
                    snapshot_version, policy_version, policy_sha256,
                    record_set_sha256, evidence_set_sha256, status,
                    cursor_record_id, total_records, processed_records,
                    promoted_records, invalidated_records, unchanged_records,
                    deferred_records, created_at, updated_at, created_by,
                    last_actor_id, lease_owner, lease_expires_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', NULL, ?, 0, 0, 0, 0, 0,
                    ?, ?, ?, ?, NULL, NULL
                )
                """,
                (
                    job_id,
                    organization_id,
                    repository_id,
                    branch,
                    stored_snapshot.snapshot.snapshot_sha256,
                    stored_snapshot.version,
                    policy.policy_version,
                    policy.policy_sha256,
                    record_set_sha256,
                    evidence_set_sha256,
                    total_records,
                    now_value,
                    now_value,
                    actor_id,
                    actor_id,
                ),
            )
            self._insert_revalidation_event(
                connection,
                occurred_at=now,
                organization_id=organization_id,
                repository_id=repository_id,
                branch=branch,
                job_id=job_id,
                action="job_start",
                status="pending",
                batch_records=0,
                actor_id=actor_id,
                policy_version=policy.policy_version,
            )
            row = connection.execute(
                "SELECT * FROM context_revalidation_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if row is None:  # pragma: no cover - SQLite invariant
                raise ContextStoreError("context_revalidation_job_missing_after_write")
            return self._row_to_revalidation_job(row)

    def run_revalidation_batch(
        self,
        *,
        job_id: str,
        policy: LifecyclePolicy,
        actor_id: str,
        batch_size: int,
        lease_seconds: int,
        occurred_at: datetime | None = None,
    ) -> ContextRevalidationJob:
        _validate_scope_value(job_id, "job_id")
        if not isinstance(policy, LifecyclePolicy):
            raise ContextStoreError("context_lifecycle_policy_required")
        _validate_mutation_identity(actor_id=actor_id, policy_version=policy.policy_version)
        if (
            isinstance(batch_size, bool)
            or not isinstance(batch_size, int)
            or not 1 <= batch_size <= 1_000
        ):
            raise ContextStoreError("context_revalidation_batch_size_invalid")
        if (
            isinstance(lease_seconds, bool)
            or not isinstance(lease_seconds, int)
            or not 1 <= lease_seconds <= 3_600
        ):
            raise ContextStoreError("context_revalidation_lease_seconds_invalid")
        now = _utc(occurred_at or datetime.now(timezone.utc))
        lease_owner = "ctxworker_" + uuid.uuid4().hex
        lease_expires_at = now + timedelta(seconds=lease_seconds)

        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM context_revalidation_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise ContextNotFound("context_revalidation_job_not_found")
            job = self._row_to_revalidation_job(row)
            self._require_revalidation_policy(job, policy)
            if job.status in {"completed", "superseded", "failed"}:
                return job
            if (
                job.status == "running"
                and job.lease_expires_at is not None
                and job.lease_expires_at > now
            ):
                raise ContextConflict("context_revalidation_lease_conflict")
            current_snapshot = connection.execute(
                """
                SELECT * FROM context_lifecycle_snapshots
                WHERE organization_id = ? AND repository_id = ? AND branch = ?
                """,
                (job.organization_id, job.repository_id, job.branch),
            ).fetchone()
            if (
                current_snapshot is None
                or str(current_snapshot["snapshot_sha256"]) != job.snapshot_sha256
                or int(current_snapshot["version"]) != job.snapshot_version
                or self._current_record_set_sha256(connection, job) != job.record_set_sha256
                or self._current_evidence_set_sha256(connection, job)
                != job.evidence_set_sha256
            ):
                return self._supersede_revalidation_job(
                    connection,
                    job=job,
                    actor_id=actor_id,
                    occurred_at=now,
                )
            cursor = connection.execute(
                """
                UPDATE context_revalidation_jobs
                SET status = 'running', lease_owner = ?, lease_expires_at = ?,
                    updated_at = ?, last_actor_id = ?
                WHERE id = ? AND status IN ('pending', 'running')
                """,
                (
                    lease_owner,
                    _isoformat(lease_expires_at),
                    _isoformat(now),
                    actor_id,
                    job_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ContextConflict("context_revalidation_lease_conflict")

        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM context_revalidation_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if row is None:  # pragma: no cover - durable lease invariant
                raise ContextNotFound("context_revalidation_job_not_found")
            job = self._row_to_revalidation_job(row)
            if job.status != "running" or job.lease_owner != lease_owner:
                raise ContextConflict("context_revalidation_lease_lost")
            self._require_revalidation_policy(job, policy)
            current_snapshot_row = connection.execute(
                """
                SELECT * FROM context_lifecycle_snapshots
                WHERE organization_id = ? AND repository_id = ? AND branch = ?
                """,
                (job.organization_id, job.repository_id, job.branch),
            ).fetchone()
            if (
                current_snapshot_row is None
                or str(current_snapshot_row["snapshot_sha256"]) != job.snapshot_sha256
                or int(current_snapshot_row["version"]) != job.snapshot_version
                or self._current_record_set_sha256(connection, job) != job.record_set_sha256
                or self._current_evidence_set_sha256(connection, job)
                != job.evidence_set_sha256
            ):
                return self._supersede_revalidation_job(
                    connection,
                    job=job,
                    actor_id=actor_id,
                    occurred_at=now,
                )
            snapshot = self._row_to_lifecycle_snapshot(current_snapshot_row).snapshot
            record_parameters: list[object] = [
                job.organization_id,
                job.repository_id,
                job.branch,
            ]
            cursor_clause = ""
            if job.cursor_record_id is not None:
                cursor_clause = " AND id > ?"
                record_parameters.append(job.cursor_record_id)
            record_parameters.append(batch_size)
            record_rows = connection.execute(
                """
                SELECT * FROM context_records
                WHERE organization_id = ? AND repository_id = ?
                  AND (branch IS NULL OR branch = ?)
                """
                + cursor_clause
                + " ORDER BY id LIMIT ?",
                record_parameters,
            ).fetchall()
            stored_records = [self._row_to_stored(item) for item in record_rows]
            evidence_by_record: dict[str, list[ContextEvidence]] = {
                item.record.record_id: [] for item in stored_records
            }
            if stored_records:
                placeholders = ", ".join("?" for _ in stored_records)
                evidence_rows = connection.execute(
                    f"""
                    SELECT * FROM context_evidence_events
                    WHERE organization_id = ? AND record_id IN ({placeholders})
                    ORDER BY observed_at, id
                    """,
                    [job.organization_id, *(item.record.record_id for item in stored_records)],
                ).fetchall()
                subjects = {
                    item.record.record_id: lifecycle_subject_sha256(item.record)
                    for item in stored_records
                }
                for evidence_row in evidence_rows:
                    record_id = str(evidence_row["record_id"])
                    if str(evidence_row["subject_sha256"]) == subjects.get(record_id):
                        evidence_by_record[record_id].append(
                            self._row_to_lifecycle_evidence(evidence_row).evidence
                        )

            promoted = invalidated = unchanged = deferred = 0
            for stored_record in stored_records:
                decision = evaluate_record_lifecycle(
                    stored_record.record,
                    evidence_by_record[stored_record.record.record_id],
                    snapshot,
                    policy,
                )
                if decision.deferred:
                    deferred += 1
                    unchanged += 1
                    continue
                if decision.target_verification == stored_record.record.verification:
                    unchanged += 1
                    continue
                target = replace(
                    stored_record.record,
                    verification=decision.target_verification,
                    verification_evidence=decision.evidence_ids,
                    verified_at=now if decision.target_verification == "verified" else None,
                )
                new_version = stored_record.version + 1
                cursor = connection.execute(
                    """
                    UPDATE context_records
                    SET verification = ?, verification_evidence_json = ?,
                        verified_at = ?, version = ?, updated_at = ?
                    WHERE organization_id = ? AND id = ? AND version = ?
                    """,
                    (
                        target.verification,
                        _json_tuple(target.verification_evidence),
                        _isoformat(target.verified_at),
                        new_version,
                        _isoformat(now),
                        target.organization_id,
                        target.record_id,
                        stored_record.version,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ContextConflict("context_revalidation_record_version_conflict")
                self._insert_audit(
                    connection,
                    occurred_at=now,
                    actor_id=actor_id,
                    action="update",
                    prior_record_id=target.record_id,
                    prior_version=stored_record.version,
                    new_record_id=target.record_id,
                    new_version=new_version,
                    policy_version=policy.policy_version,
                    record=target,
                )
                connection.execute(
                    """
                    INSERT INTO context_revalidation_changes (
                        job_id, record_id, occurred_at, prior_verification,
                        target_verification, reason, evidence_count,
                        matched_path_id, prior_version, new_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        target.record_id,
                        _isoformat(now),
                        stored_record.record.verification,
                        target.verification,
                        decision.reason,
                        len(decision.evidence_ids),
                        decision.matched_path_id,
                        stored_record.version,
                        new_version,
                    ),
                )
                if target.verification == "verified":
                    promoted += 1
                else:
                    invalidated += 1

            batch_records = len(stored_records)
            processed_records = job.processed_records + batch_records
            cursor_record_id = (
                stored_records[-1].record.record_id if stored_records else job.cursor_record_id
            )
            has_more = False
            if cursor_record_id is not None:
                has_more = connection.execute(
                    """
                    SELECT 1 FROM context_records
                    WHERE organization_id = ? AND repository_id = ?
                      AND (branch IS NULL OR branch = ?) AND id > ? LIMIT 1
                    """,
                    (
                        job.organization_id,
                        job.repository_id,
                        job.branch,
                        cursor_record_id,
                    ),
                ).fetchone() is not None
            status = "pending" if has_more else "completed"
            job_cursor = connection.execute(
                """
                UPDATE context_revalidation_jobs SET
                    status = ?, cursor_record_id = ?, processed_records = ?,
                    promoted_records = promoted_records + ?,
                    invalidated_records = invalidated_records + ?,
                    unchanged_records = unchanged_records + ?,
                    deferred_records = deferred_records + ?,
                    updated_at = ?, last_actor_id = ?,
                    lease_owner = NULL, lease_expires_at = NULL
                WHERE id = ? AND lease_owner = ?
                """,
                (
                    status,
                    cursor_record_id,
                    processed_records,
                    promoted,
                    invalidated,
                    unchanged,
                    deferred,
                    _isoformat(now),
                    actor_id,
                    job_id,
                    lease_owner,
                ),
            )
            if job_cursor.rowcount != 1:
                raise ContextConflict("context_revalidation_lease_lost")
            self._insert_revalidation_event(
                connection,
                occurred_at=now,
                organization_id=job.organization_id,
                repository_id=job.repository_id,
                branch=job.branch,
                job_id=job_id,
                action="batch_complete",
                status=status,
                batch_records=batch_records,
                actor_id=actor_id,
                policy_version=policy.policy_version,
            )
            updated = connection.execute(
                "SELECT * FROM context_revalidation_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if updated is None:  # pragma: no cover - SQLite invariant
                raise ContextStoreError("context_revalidation_job_missing_after_batch")
            return self._row_to_revalidation_job(updated)

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

    def list_access_authorized(
        self,
        principal: ContextPrincipal,
    ) -> list[StoredContextRecord]:
        """Load identity-authorized candidates before lifecycle eligibility filtering."""
        if not isinstance(principal, ContextPrincipal):
            raise ContextStoreError("context_principal_required")
        allowed_classifications = CLASSIFICATIONS[: _CLASSIFICATION_RANK[principal.clearance] + 1]
        clauses = [
            "organization_id = ?",
            f"classification IN ({', '.join('?' for _ in allowed_classifications)})",
            "((visibility = 'organization' AND scope_id = ?) "
            "OR (visibility = 'team' AND scope_id = ?) "
            "OR (visibility = 'actor' AND scope_id = ?))",
            "(repository_id IS NULL OR repository_id = ?)",
            "(branch IS NULL OR branch = ?)",
        ]
        parameters: list[object] = [
            principal.organization_id,
            *allowed_classifications,
            principal.organization_id,
            principal.team_id,
            principal.actor_id,
            principal.repository_id,
            principal.branch,
        ]
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                f"SELECT * FROM context_records WHERE {' AND '.join(clauses)} ORDER BY id",
                parameters,
            ).fetchall()
        return [
            self._row_to_stored(row)
            for row in rows
            if self._row_metadata_is_access_authorized(row, principal=principal)
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
            evidence_rows = connection.execute(
                f"""
                SELECT
                    id, created_at AS occurred_at, organization_id, record_id,
                    record_version, signal, signal_family, actor_id, policy_version
                FROM context_evidence_events
                WHERE {' AND '.join(clauses)}
                ORDER BY created_at, id
                """,
                parameters,
            ).fetchall()
            revalidation_rows = connection.execute(
                f"""
                SELECT
                    id, occurred_at, organization_id, repository_id, branch,
                    job_id, action, status, batch_records, actor_id, policy_version
                FROM context_revalidation_events
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
        events.extend(
            {
                "schema_version": 1,
                "event_type": "context.evidence",
                "action": "evidence_recorded",
                **dict(row),
            }
            for row in evidence_rows
        )
        events.extend(
            {
                "schema_version": 1,
                "event_type": "context.revalidation",
                **dict(row),
            }
            for row in revalidation_rows
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

    def _current_record_set_sha256(
        self,
        connection: sqlite3.Connection,
        job: ContextRevalidationJob,
    ) -> str:
        rows = connection.execute(
            """
            SELECT * FROM context_records
            WHERE organization_id = ? AND repository_id = ?
              AND (branch IS NULL OR branch = ?)
            ORDER BY id
            """,
            (job.organization_id, job.repository_id, job.branch),
        ).fetchall()
        return _lifecycle_record_set_sha256(
            [self._row_to_stored(row) for row in rows]
        )

    def _current_evidence_set_sha256(
        self,
        connection: sqlite3.Connection,
        job: ContextRevalidationJob,
    ) -> str:
        rows = connection.execute(
            """
            SELECT * FROM context_records
            WHERE organization_id = ? AND repository_id = ?
              AND (branch IS NULL OR branch = ?)
            ORDER BY id
            """,
            (job.organization_id, job.repository_id, job.branch),
        ).fetchall()
        return self._lifecycle_evidence_set_sha256(
            connection,
            [self._row_to_stored(row) for row in rows],
        )

    def _lifecycle_evidence_set_sha256(
        self,
        connection: sqlite3.Connection,
        records: list[StoredContextRecord],
    ) -> str:
        current_subjects = {
            item.record.record_id: lifecycle_subject_sha256(item.record)
            for item in records
        }
        entries: list[dict[str, str]] = []
        if current_subjects:
            organization_ids = {item.record.organization_id for item in records}
            if len(organization_ids) != 1:
                raise ContextStoreError("context_revalidation_record_scope_corrupt")
            placeholders = ", ".join("?" for _ in current_subjects)
            evidence_rows = connection.execute(
                f"""
                SELECT id, record_id, subject_sha256
                FROM context_evidence_events
                WHERE organization_id = ? AND record_id IN ({placeholders})
                ORDER BY record_id, id
                """,
                [next(iter(organization_ids)), *current_subjects],
            ).fetchall()
            entries = [
                {
                    "record_id": str(row["record_id"]),
                    "evidence_id": str(row["id"]),
                    "subject_sha256": str(row["subject_sha256"]),
                }
                for row in evidence_rows
                if str(row["subject_sha256"])
                == current_subjects.get(str(row["record_id"]))
            ]
        canonical = json.dumps(entries, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _row_to_lifecycle_evidence(self, row: sqlite3.Row) -> StoredLifecycleEvidence:
        try:
            evidence = ContextEvidence(
                organization_id=str(row["organization_id"]),
                record_id=str(row["record_id"]),
                record_version=int(row["record_version"]),
                signal=str(row["signal"]),
                evidence_ref_sha256=str(row["evidence_ref_sha256"]),
                observed_at=_parse_required_datetime(row["observed_at"]),
            )
        except ValueError as error:
            raise ContextStoreError("context_lifecycle_evidence_corrupt") from error
        if evidence.evidence_id != str(row["id"]):
            raise ContextStoreError("context_lifecycle_evidence_integrity_failed")
        if evidence.signal_family != str(row["signal_family"]):
            raise ContextStoreError("context_lifecycle_evidence_corrupt")
        subject_sha256 = str(row["subject_sha256"])
        if len(subject_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in subject_sha256
        ):
            raise ContextStoreError("context_lifecycle_evidence_corrupt")
        return StoredLifecycleEvidence(
            evidence=evidence,
            subject_sha256=subject_sha256,
            actor_id=str(row["actor_id"]),
            policy_version=str(row["policy_version"]),
            created_at=_parse_required_datetime(row["created_at"]),
        )

    def _row_to_revalidation_job(self, row: sqlite3.Row) -> ContextRevalidationJob:
        status = str(row["status"])
        if status not in {"pending", "running", "completed", "superseded", "failed"}:
            raise ContextStoreError("context_revalidation_job_corrupt")
        fingerprints = tuple(
            str(row[name])
            for name in (
                "snapshot_sha256",
                "policy_sha256",
                "record_set_sha256",
                "evidence_set_sha256",
            )
        )
        if any(
            len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
            for value in fingerprints
        ):
            raise ContextStoreError("context_revalidation_job_corrupt")
        counts = tuple(
            int(row[name])
            for name in (
                "total_records",
                "processed_records",
                "promoted_records",
                "invalidated_records",
                "unchanged_records",
                "deferred_records",
            )
        )
        if any(value < 0 for value in counts) or counts[1] > counts[0]:
            raise ContextStoreError("context_revalidation_job_corrupt")
        return ContextRevalidationJob(
            job_id=str(row["id"]),
            organization_id=str(row["organization_id"]),
            repository_id=str(row["repository_id"]),
            branch=str(row["branch"]),
            snapshot_sha256=fingerprints[0],
            snapshot_version=int(row["snapshot_version"]),
            policy_version=str(row["policy_version"]),
            policy_sha256=fingerprints[1],
            record_set_sha256=fingerprints[2],
            evidence_set_sha256=fingerprints[3],
            status=status,
            cursor_record_id=_nullable_row_string(row, "cursor_record_id"),
            total_records=counts[0],
            processed_records=counts[1],
            promoted_records=counts[2],
            invalidated_records=counts[3],
            unchanged_records=counts[4],
            deferred_records=counts[5],
            created_at=_parse_required_datetime(row["created_at"]),
            updated_at=_parse_required_datetime(row["updated_at"]),
            created_by=str(row["created_by"]),
            last_actor_id=str(row["last_actor_id"]),
            lease_owner=_nullable_row_string(row, "lease_owner"),
            lease_expires_at=_parse_nullable_datetime(row["lease_expires_at"]),
        )

    def _require_revalidation_policy(
        self,
        job: ContextRevalidationJob,
        policy: LifecyclePolicy,
    ) -> None:
        if (
            job.policy_version != policy.policy_version
            or job.policy_sha256 != policy.policy_sha256
        ):
            raise ContextConflict("context_revalidation_policy_conflict")

    def _supersede_revalidation_job(
        self,
        connection: sqlite3.Connection,
        *,
        job: ContextRevalidationJob,
        actor_id: str,
        occurred_at: datetime,
    ) -> ContextRevalidationJob:
        connection.execute(
            """
            UPDATE context_revalidation_jobs
            SET status = 'superseded', updated_at = ?, last_actor_id = ?,
                lease_owner = NULL, lease_expires_at = NULL
            WHERE id = ?
            """,
            (_isoformat(occurred_at), actor_id, job.job_id),
        )
        self._insert_revalidation_event(
            connection,
            occurred_at=occurred_at,
            organization_id=job.organization_id,
            repository_id=job.repository_id,
            branch=job.branch,
            job_id=job.job_id,
            action="job_superseded",
            status="superseded",
            batch_records=0,
            actor_id=actor_id,
            policy_version=job.policy_version,
        )
        row = connection.execute(
            "SELECT * FROM context_revalidation_jobs WHERE id = ?",
            (job.job_id,),
        ).fetchone()
        if row is None:  # pragma: no cover - SQLite invariant
            raise ContextStoreError("context_revalidation_job_missing_after_supersede")
        return self._row_to_revalidation_job(row)

    def _insert_revalidation_event(
        self,
        connection: sqlite3.Connection,
        *,
        occurred_at: datetime,
        organization_id: str,
        repository_id: str,
        branch: str,
        job_id: str,
        action: str,
        status: str,
        batch_records: int,
        actor_id: str,
        policy_version: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO context_revalidation_events (
                id, occurred_at, organization_id, repository_id, branch,
                job_id, action, status, batch_records, actor_id, policy_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                _isoformat(occurred_at),
                organization_id,
                repository_id,
                branch,
                job_id,
                action,
                status,
                batch_records,
                actor_id,
                policy_version,
            ),
        )

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
        if not self._row_metadata_is_access_authorized(row, principal=principal):
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

    def _row_metadata_is_access_authorized(
        self,
        row: sqlite3.Row,
        *,
        principal: ContextPrincipal,
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


def _validate_scope_value(value: object, name: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value.encode("utf-8")) > 512
        or any(character in value for character in ("\n", "\r", "\x00"))
    ):
        raise ContextStoreError(f"context_{name}_required")
    return value


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


def _lifecycle_record_set_sha256(records: Iterable[StoredContextRecord]) -> str:
    canonical = json.dumps(
        [
            {
                "record_id": item.record.record_id,
                "subject_sha256": lifecycle_subject_sha256(item.record),
            }
            for item in records
        ],
        sort_keys=True,
        separators=(",", ":"),
    )
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
