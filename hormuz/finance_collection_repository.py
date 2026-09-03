"""Authorized append-only repository for provider aggregate finance evidence."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
import hashlib
import hmac
import json
import os
from typing import Any, Iterator, Mapping
from uuid import uuid4

from ._finance_collection_schema import (
    COLLECTION_ATTEMPT_TABLE,
    COLLECTION_EVENT_TABLE,
    COST_TABLE,
    COVERAGE_TABLE,
    SNAPSHOT_TABLE,
    SOURCE_BINDING_TABLE,
    TABLE_DDL,
    USAGE_TABLE,
)
from ._portfolio_sql import portfolio_transaction
from .audit_chain import (
    AuditChainError,
    AuditChainSource,
    build_audit_chain_entry,
    canonical_json_text,
)
from .config import GatewayConfig
from .finance_collection import (
    PROFILE_SPECS,
    CollectionQuery,
    FinanceCollectionError,
    NormalizedCollection,
    tenant_fingerprint,
    _unicode_safe,
    validate_normalized_collection,
    validate_finance_collection_event,
    validate_finance_snapshot_event,
    validate_finance_source_binding_event,
)
from .portfolio_config import PortfolioPrincipal
from .portfolio_wire import PortfolioError
from .postgres import POSTGRES_SCHEMA_VERSION, PostgresConnectionPool


# The SQLite collection runtime is the only accepted adapter in this
# candidate.  PostgreSQL 16 provisions the owner-controlled collection shape,
# but its runtime-role grants remain withheld until a separate ACL review
# accepts a new literal boundary.  Keep this as a fixed code gate rather than
# inferring readiness from whichever privileges happen to exist in a database.
POSTGRES_FINANCE_COLLECTION_RUNTIME_ACCEPTED = False


_BIND_REQUEST_KEYS = {
    "schema_id",
    "schema_version",
    "binding_id",
    "expected_version",
    "provider",
    "provider_account_reference_id",
    "scope",
    "credential_reference_version",
    "fingerprint_key_version",
    "state",
    "reason_code",
}
_ID_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-"
)
_TERMINAL_REASON_CODES = frozenset(
    {
        "completed",
        "provider_unauthorized",
        "provider_rate_limited",
        "provider_unavailable",
        "collection_deadline",
        "normalization_failed",
        "authorization_revoked",
        "binding_revoked",
        "credential_unavailable",
        "fingerprint_key_unavailable",
        "operator_abandoned",
    }
)


@dataclass(frozen=True)
class SourceBindingVersion:
    organization_id: str
    binding_id: str
    version: int
    binding_event_id: str
    provider: str
    provider_account_fingerprint: str
    scope_kind: str
    scope_fingerprints: tuple[str, ...]
    credential_reference_id: str
    credential_reference_version: int
    fingerprint_key_version: int
    binding_state: str
    previous_version: int | None
    content_digest: str
    bound_by: str
    bound_at: str
    reason_code: str


@dataclass(frozen=True)
class PreparedCollectionAttempt:
    organization_id: str
    attempt_id: str
    query: CollectionQuery
    provider: str
    source_kind: str
    evidence_origin: str
    idempotency_digest: str
    request_digest: str
    credential_reference_id: str
    credential_reference_version: int
    fingerprint_key_version: int
    prepared_by: str
    prepared_at: str
    state: str = "pending"
    receipt_id: str | None = None
    snapshot_id: str | None = None


@dataclass(frozen=True)
class CollectionReceipt:
    organization_id: str
    attempt_id: str
    event_id: str
    receipt_id: str
    snapshot_id: str
    content_digest: str
    page_chain_digest: str
    supersedes_snapshot_id: str | None
    commit_sequence: int
    occurred_at: str


@dataclass(frozen=True)
class CurrentCollectionView:
    organization_id: str
    binding_id: str
    binding_version: int
    collection_profile: str
    coverage: tuple[Mapping[str, object], ...]
    observations: tuple[Mapping[str, object], ...]


class FinanceCollectionRepository:
    """Own collection state while rechecking configured tenant authority."""

    def __init__(
        self,
        config: GatewayConfig,
        *,
        dsn: str,
        connection_pool: PostgresConnectionPool | None = None,
        read_only: bool = False,
    ):
        self.config = config
        self._dsn = dsn
        self._pool = connection_pool
        self._read_only = read_only

    def _authorize(self, principal: PortfolioPrincipal) -> None:
        control = self.config.portfolio_control
        if (
            self._read_only
            or type(principal) is not PortfolioPrincipal
            or control is None
            or principal.organization_id not in self.config.organization_ids
        ):
            raise FinanceCollectionError("forbidden")
        if not any(
            (
                binding.organization_id,
                binding.actor_id,
                binding.roles,
            )
            == (
                principal.organization_id,
                principal.actor_id,
                principal.roles,
            )
            and "portfolio_admin" in binding.roles
            for binding in control.role_bindings
        ):
            raise FinanceCollectionError("forbidden")

    @contextmanager
    def _transaction(
        self,
        principal: PortfolioPrincipal,
    ) -> Iterator[Any]:
        self._authorize(principal)
        if (
            self.config.usage_storage.backend == "postgresql"
            and not POSTGRES_FINANCE_COLLECTION_RUNTIME_ACCEPTED
        ):
            raise FinanceCollectionError("unavailable")
        try:
            with portfolio_transaction(
                self.config,
                principal.organization_id,
                dsn=self._dsn,
                connection_pool=self._pool,
                tables=TABLE_DDL,
                statement_timeout_ms=10_000,
            ) as sql:
                self._authorize(principal)
                if sql.postgres:
                    rows = sql.execute(
                        "SELECT version, state FROM hormuz_schema_migrations "
                        "ORDER BY version LIMIT ?",
                        (POSTGRES_SCHEMA_VERSION + 1,),
                    ).fetchall()
                    if [
                        (int(row["version"]), str(row["state"])) for row in rows
                    ] != [
                        (version, "applied")
                        for version in range(1, POSTGRES_SCHEMA_VERSION + 1)
                    ]:
                        raise FinanceCollectionError("unavailable")
                yield sql
                self._authorize(principal)
        except PortfolioError:
            raise FinanceCollectionError("unavailable") from None

    def bind_source(
        self,
        principal: PortfolioPrincipal,
        request: Mapping[str, object],
        *,
        fingerprint_key: bytes,
    ) -> SourceBindingVersion:
        """Append one normalized binding version; exact retries return it."""

        self._authorize(principal)
        normalized = _normalize_binding_request(
            request,
            organization_id=principal.organization_id,
            fingerprint_key=fingerprint_key,
        )
        with self._transaction(principal) as sql:
            latest = sql.one(
                f"SELECT * FROM {SOURCE_BINDING_TABLE} "
                "WHERE organization_id=? AND binding_id=? "
                "ORDER BY version DESC LIMIT 1",
                (principal.organization_id, normalized["binding_id"]),
            )
            expected = normalized.pop("expected_version")
            if latest is None:
                if expected is not None:
                    raise FinanceCollectionError("binding_conflict")
                version, previous = 1, None
            else:
                current = _binding_from_row(latest)
                candidate_content = _binding_content(
                    normalized,
                    version=current.version,
                    previous_version=current.previous_version,
                )
                if hmac.compare_digest(
                    _digest(candidate_content), current.content_digest
                ):
                    return current
                if expected != current.version:
                    raise FinanceCollectionError("binding_conflict")
                version, previous = current.version + 1, current.version

            content = _binding_content(
                normalized,
                version=version,
                previous_version=previous,
            )
            event_id = str(uuid4())
            now = sql.now()
            event = {
                "schema_id": "hormuz.finance-source-binding-version",
                "schema_version": 1,
                "binding_event_id": event_id,
                "organization_id": principal.organization_id,
                "binding_id": normalized["binding_id"],
                "version": version,
                "provider": normalized["provider"],
                "provider_account_fingerprint": normalized[
                    "provider_account_fingerprint"
                ],
                "scope_kind": normalized["scope_kind"],
                "scope_fingerprints": list(normalized["scope_fingerprints"]),
                "credential_reference_id": normalized[
                    "credential_reference_id"
                ],
                "credential_reference_version": normalized[
                    "credential_reference_version"
                ],
                "fingerprint_key_version": normalized[
                    "fingerprint_key_version"
                ],
                "binding_state": normalized["binding_state"],
                "previous_version": previous,
                "content_digest": _digest(content),
                "bound_by": principal.actor_id,
                "bound_at": now,
                "reason_code": normalized["reason_code"],
            }
            validate_finance_source_binding_event(event)
            row = {
                "organization_id": principal.organization_id,
                "binding_id": event["binding_id"],
                "version": version,
                "binding_event_id": event_id,
                "provider": event["provider"],
                "provider_account_fingerprint": event[
                    "provider_account_fingerprint"
                ],
                "scope_kind": event["scope_kind"],
                "scope_fingerprints_json": _canonical(
                    event["scope_fingerprints"]
                ),
                "credential_reference_id": event["credential_reference_id"],
                "credential_reference_version": event[
                    "credential_reference_version"
                ],
                "fingerprint_key_version": event["fingerprint_key_version"],
                "binding_state": event["binding_state"],
                "previous_version": previous,
                "content_digest": event["content_digest"],
                "bound_by": principal.actor_id,
                "bound_at": now,
                "evidence_json": _canonical(event),
            }
            sql.insert(SOURCE_BINDING_TABLE, row)
            _append_audit(
                sql,
                event=event,
                source=AuditChainSource(
                    "hormuz.finance-source-binding-version", 1, event_id
                ),
            )
            return _binding_from_row(row)

    def prepare_collection(
        self,
        principal: PortfolioPrincipal,
        query: CollectionQuery,
        *,
        idempotency_key: str,
        evidence_origin: str,
    ) -> PreparedCollectionAttempt:
        """Commit a content-free pending root before any external I/O."""

        self._authorize(principal)
        if (
            type(query) is not CollectionQuery
            or query.organization_id != principal.organization_id
            or evidence_origin not in {"authenticated_api", "customer_file"}
            or not isinstance(idempotency_key, str)
            or "\x00" in idempotency_key
            or not _unicode_safe(idempotency_key)
            or not 1 <= len(idempotency_key.encode("utf-8")) <= 256
        ):
            raise FinanceCollectionError("invalid_request")
        idempotency_digest = _digest(
            [principal.organization_id, idempotency_key]
        )
        request_digest = _digest(
            {
                **asdict(query),
                "evidence_origin": evidence_origin,
                "idempotency_digest": idempotency_digest,
            }
        )
        with self._transaction(principal) as sql:
            binding_row = sql.one(
                f"SELECT * FROM {SOURCE_BINDING_TABLE} "
                "WHERE organization_id=? AND binding_id=? "
                "ORDER BY version DESC LIMIT 1",
                (principal.organization_id, query.binding_id),
            )
            if binding_row is None:
                raise FinanceCollectionError("binding_inactive")
            binding = _binding_from_row(binding_row)
            if (
                binding.version != query.binding_version
                or binding.binding_state != "active"
                or binding.provider != query.profile.provider
            ):
                raise FinanceCollectionError("binding_inactive")
            existing = sql.one(
                f"SELECT * FROM {COLLECTION_ATTEMPT_TABLE} "
                "WHERE organization_id=? AND binding_id=? AND binding_version=? "
                "AND collection_profile=? AND query_start_at=? AND query_end_at=? "
                "AND idempotency_digest=?",
                (
                    principal.organization_id,
                    query.binding_id,
                    query.binding_version,
                    query.collection_profile,
                    query.query_start_at,
                    query.query_end_at,
                    idempotency_digest,
                ),
            )
            if existing is not None:
                prepared = _prepared_from_row(sql, existing)
                if not hmac.compare_digest(prepared.request_digest, request_digest):
                    raise FinanceCollectionError("attempt_conflict")
                if prepared.state == "succeeded":
                    return prepared
                if prepared.state == "pending":
                    raise FinanceCollectionError("attempt_pending")
                raise FinanceCollectionError("attempt_terminal")
            attempt_id = str(uuid4())
            now = sql.now()
            row = {
                "organization_id": principal.organization_id,
                "attempt_id": attempt_id,
                "binding_id": query.binding_id,
                "binding_version": query.binding_version,
                "provider": binding.provider,
                "collection_profile": query.collection_profile,
                "source_kind": query.profile.source_kind,
                "query_start_at": query.query_start_at,
                "query_end_at": query.query_end_at,
                "bucket_width": query.bucket_width,
                "requested_page_size": query.requested_page_size,
                "evidence_origin": evidence_origin,
                "idempotency_digest": idempotency_digest,
                "request_digest": request_digest,
                "credential_reference_id": binding.credential_reference_id,
                "credential_reference_version": binding.credential_reference_version,
                "fingerprint_key_version": binding.fingerprint_key_version,
                "prepared_by": principal.actor_id,
                "prepared_at": now,
            }
            sql.insert(COLLECTION_ATTEMPT_TABLE, row)
            return _prepared_from_row(sql, row)

    def publish_collection(
        self,
        principal: PortfolioPrincipal,
        prepared: PreparedCollectionAttempt,
        collection: NormalizedCollection,
    ) -> CollectionReceipt:
        """Atomically publish a complete snapshot, coverage, terminal, and audit."""

        self._authorize(principal)
        if (
            type(prepared) is not PreparedCollectionAttempt
            or type(collection) is not NormalizedCollection
            or prepared.organization_id != principal.organization_id
            or prepared.state != "pending"
            or collection.query != prepared.query
            or collection.fingerprint_key_version
            != prepared.fingerprint_key_version
        ):
            raise FinanceCollectionError("invalid_request")
        validate_normalized_collection(collection)
        with self._transaction(principal) as sql:
            attempt_row = sql.one(
                f"SELECT * FROM {COLLECTION_ATTEMPT_TABLE} "
                "WHERE organization_id=? AND attempt_id=?",
                (principal.organization_id, prepared.attempt_id),
            )
            if attempt_row is None:
                raise FinanceCollectionError("attempt_conflict")
            durable = _prepared_from_row(sql, attempt_row)
            if not _same_prepared_root(prepared, durable):
                raise FinanceCollectionError("attempt_conflict")
            if durable.state == "succeeded":
                return _receipt_for_attempt(
                    sql,
                    principal.organization_id,
                    durable.attempt_id,
                    collection,
                )
            if durable.state != "pending":
                raise FinanceCollectionError("attempt_terminal")
            binding_row = sql.one(
                f"SELECT * FROM {SOURCE_BINDING_TABLE} "
                "WHERE organization_id=? AND binding_id=? "
                "ORDER BY version DESC LIMIT 1",
                (principal.organization_id, prepared.query.binding_id),
            )
            if binding_row is None:
                raise FinanceCollectionError("binding_inactive")
            binding = _binding_from_row(binding_row)
            if (
                binding.version != prepared.query.binding_version
                or binding.binding_state != "active"
                or binding.provider != prepared.provider
                or binding.credential_reference_id
                != prepared.credential_reference_id
                or binding.credential_reference_version
                != prepared.credential_reference_version
                or binding.fingerprint_key_version
                != prepared.fingerprint_key_version
            ):
                raise FinanceCollectionError("binding_inactive")
            _validate_binding_scope(binding, collection)

            predecessor = sql.one(
                f"SELECT snapshot_id FROM {SNAPSHOT_TABLE} "
                "WHERE organization_id=? AND binding_id=? AND binding_version=? "
                "AND collection_profile=? AND query_start_at=? AND query_end_at=? "
                "ORDER BY commit_sequence DESC LIMIT 1",
                (
                    principal.organization_id,
                    prepared.query.binding_id,
                    prepared.query.binding_version,
                    prepared.query.collection_profile,
                    prepared.query.query_start_at,
                    prepared.query.query_end_at,
                ),
            )
            supersedes = None if predecessor is None else predecessor["snapshot_id"]
            maximum = sql.one(
                f"SELECT COALESCE(MAX(commit_sequence),0) AS sequence "
                f"FROM {SNAPSHOT_TABLE} WHERE organization_id=?",
                (principal.organization_id,),
            )["sequence"]
            if type(maximum) is not int or not 0 <= maximum < 9_223_372_036_854_775_807:
                raise FinanceCollectionError("unavailable")
            commit_sequence = maximum + 1
            snapshot_id = str(uuid4())
            event_id = str(uuid4())
            receipt_id = uuid4().hex
            now = sql.now()
            scope_provenance = (
                "authenticated_query_scope_unverified"
                if prepared.evidence_origin == "authenticated_api"
                else "customer_supplied_scope_unverified"
            )
            snapshot_event = {
                "schema_id": "hormuz.finance-snapshot",
                "schema_version": 1,
                "snapshot_id": snapshot_id,
                "organization_id": principal.organization_id,
                "attempt_id": prepared.attempt_id,
                "binding_id": prepared.query.binding_id,
                "binding_version": prepared.query.binding_version,
                "collection_profile": prepared.query.collection_profile,
                "source_kind": prepared.source_kind,
                "query_start_at": prepared.query.query_start_at,
                "query_end_at": prepared.query.query_end_at,
                "evidence_origin": prepared.evidence_origin,
                "scope_provenance": scope_provenance,
                "parser_version": collection.parser_version,
                "page_count": collection.page_count,
                "record_count": collection.record_count,
                "requested_page_size": prepared.query.requested_page_size,
                "page_chain_digest": collection.page_chain_digest,
                "content_digest": collection.content_digest,
                "supersedes_snapshot_id": supersedes,
                "commit_sequence": commit_sequence,
                "published_by": principal.actor_id,
                "published_at": now,
                "provider_final": False,
                "invoice_final": False,
            }
            validate_finance_snapshot_event(snapshot_event)
            snapshot_row = {
                key: (0 if value is False else value)
                for key, value in snapshot_event.items()
                if key not in {"schema_id", "schema_version"}
            }
            snapshot_row["evidence_json"] = _canonical(snapshot_event)
            sql.insert(SNAPSHOT_TABLE, snapshot_row)

            counts = _observation_counts(collection)
            for coverage in collection.coverage:
                interval = (coverage.bucket_start_at, coverage.bucket_end_at)
                if counts.get(interval, 0) != coverage.observation_count:
                    raise FinanceCollectionError("snapshot_conflict")
                sql.insert(
                    COVERAGE_TABLE,
                    {
                        "organization_id": principal.organization_id,
                        "coverage_id": str(uuid4()),
                        "snapshot_id": snapshot_id,
                        **asdict(coverage),
                    },
                )
            if set(counts) != {
                (item.bucket_start_at, item.bucket_end_at)
                for item in collection.coverage
                if item.observation_count
            }:
                raise FinanceCollectionError("snapshot_conflict")
            for observation in collection.usage_observations:
                row = asdict(observation)
                row.update(
                    {
                        "organization_id": principal.organization_id,
                        "observation_id": str(uuid4()),
                        "snapshot_id": snapshot_id,
                    }
                )
                row["batch"] = (
                    None if row["batch"] is None else int(row["batch"])
                )
                row["provider_final"] = 0
                sql.insert(USAGE_TABLE, row)
            for observation in collection.cost_observations:
                row = asdict(observation)
                row.update(
                    {
                        "organization_id": principal.organization_id,
                        "observation_id": str(uuid4()),
                        "snapshot_id": snapshot_id,
                    }
                )
                row["provider_final"] = 0
                row["invoice_final"] = 0
                sql.insert(COST_TABLE, row)

            terminal_event = {
                "schema_id": "hormuz.finance-collection-event",
                "schema_version": 1,
                "event_id": event_id,
                "organization_id": principal.organization_id,
                "attempt_id": prepared.attempt_id,
                "state": "succeeded",
                "reason_code": "completed",
                "receipt_id": receipt_id,
                "snapshot_id": snapshot_id,
                "actor_id": principal.actor_id,
                "occurred_at": now,
            }
            validate_finance_collection_event(terminal_event)
            sql.insert(
                COLLECTION_EVENT_TABLE,
                {
                    key: value
                    for key, value in terminal_event.items()
                    if key not in {"schema_id", "schema_version"}
                }
                | {"evidence_json": _canonical(terminal_event)},
            )
            _append_audit(
                sql,
                event=snapshot_event,
                source=AuditChainSource("hormuz.finance-snapshot", 1, snapshot_id),
            )
            _append_audit(
                sql,
                event=terminal_event,
                source=AuditChainSource(
                    "hormuz.finance-collection-event", 1, event_id
                ),
            )
            return CollectionReceipt(
                principal.organization_id,
                prepared.attempt_id,
                event_id,
                receipt_id,
                snapshot_id,
                collection.content_digest,
                collection.page_chain_digest,
                supersedes,
                commit_sequence,
                now,
            )

    def receipt_for_prepared(
        self,
        principal: PortfolioPrincipal,
        prepared: PreparedCollectionAttempt,
    ) -> CollectionReceipt:
        """Return an already succeeded idempotent attempt without provider replay."""

        self._authorize(principal)
        if (
            type(prepared) is not PreparedCollectionAttempt
            or prepared.organization_id != principal.organization_id
            or prepared.state != "succeeded"
        ):
            raise FinanceCollectionError("attempt_pending")
        with self._transaction(principal) as sql:
            durable_row = sql.one(
                f"SELECT * FROM {COLLECTION_ATTEMPT_TABLE} "
                "WHERE organization_id=? AND attempt_id=?",
                (principal.organization_id, prepared.attempt_id),
            )
            if durable_row is None:
                raise FinanceCollectionError("attempt_conflict")
            durable = _prepared_from_row(sql, durable_row)
            if not _same_prepared_root(prepared, durable):
                raise FinanceCollectionError("attempt_conflict")
            if durable.state != "succeeded":
                raise FinanceCollectionError("attempt_terminal")
            return _receipt_for_attempt(
                sql,
                principal.organization_id,
                durable.attempt_id,
                None,
            )

    def fail_collection(
        self,
        principal: PortfolioPrincipal,
        prepared: PreparedCollectionAttempt,
        *,
        reason_code: str,
        abandoned: bool = False,
    ) -> None:
        """Commit one content-free terminal failure without partial evidence."""

        self._authorize(principal)
        state = "abandoned" if abandoned else "failed"
        if (
            type(prepared) is not PreparedCollectionAttempt
            or prepared.organization_id != principal.organization_id
            or prepared.state != "pending"
            or reason_code not in _TERMINAL_REASON_CODES - {"completed"}
            or (abandoned and reason_code != "operator_abandoned")
        ):
            raise FinanceCollectionError("invalid_request")
        with self._transaction(principal) as sql:
            attempt = sql.one(
                f"SELECT * FROM {COLLECTION_ATTEMPT_TABLE} "
                "WHERE organization_id=? AND attempt_id=?",
                (principal.organization_id, prepared.attempt_id),
            )
            if attempt is None or not _same_prepared_root(
                prepared, _prepared_from_row(sql, attempt)
            ):
                raise FinanceCollectionError("attempt_conflict")
            if sql.one(
                f"SELECT event_id FROM {COLLECTION_EVENT_TABLE} "
                "WHERE organization_id=? AND attempt_id=?",
                (principal.organization_id, prepared.attempt_id),
            ) is not None:
                raise FinanceCollectionError("attempt_terminal")
            event_id, now = str(uuid4()), sql.now()
            event = {
                "schema_id": "hormuz.finance-collection-event",
                "schema_version": 1,
                "event_id": event_id,
                "organization_id": principal.organization_id,
                "attempt_id": prepared.attempt_id,
                "state": state,
                "reason_code": reason_code,
                "receipt_id": None,
                "snapshot_id": None,
                "actor_id": principal.actor_id,
                "occurred_at": now,
            }
            validate_finance_collection_event(event)
            sql.insert(
                COLLECTION_EVENT_TABLE,
                {
                    key: value
                    for key, value in event.items()
                    if key not in {"schema_id", "schema_version"}
                }
                | {"evidence_json": _canonical(event)},
            )
            _append_audit(
                sql,
                event=event,
                source=AuditChainSource(
                    "hormuz.finance-collection-event", 1, event_id
                ),
            )

    def current_observations(
        self,
        principal: PortfolioPrincipal,
        *,
        binding_id: str,
        binding_version: int,
        collection_profile: str,
        start_at: str,
        end_at: str,
    ) -> CurrentCollectionView:
        """Select coverage first, including authoritative empty refreshes."""

        self._authorize(principal)
        if (
            not _safe_id(binding_id)
            or type(binding_version) is not int
            or not isinstance(collection_profile, str)
            or collection_profile not in PROFILE_SPECS
        ):
            raise FinanceCollectionError("invalid_request")
        start_at, end_at = _selection_bounds(start_at, end_at)
        with self._transaction(principal) as sql:
            coverage_rows = sql.execute(
                f"WITH ranked AS ("
                f"SELECT coverage.*, snapshot.commit_sequence, "
                f"ROW_NUMBER() OVER (PARTITION BY coverage.bucket_start_at, coverage.bucket_end_at "
                f"ORDER BY snapshot.commit_sequence DESC) AS selection_rank "
                f"FROM {COVERAGE_TABLE} coverage JOIN {SNAPSHOT_TABLE} snapshot "
                f"ON snapshot.organization_id=coverage.organization_id "
                f"AND snapshot.snapshot_id=coverage.snapshot_id "
                f"WHERE snapshot.organization_id=? AND snapshot.binding_id=? "
                f"AND snapshot.binding_version=? AND snapshot.collection_profile=? "
                f"AND coverage.bucket_start_at>=? AND coverage.bucket_end_at<=?) "
                f"SELECT * FROM ranked WHERE selection_rank=1 "
                f"ORDER BY bucket_start_at,bucket_end_at",
                (
                    principal.organization_id,
                    binding_id,
                    binding_version,
                    collection_profile,
                    start_at,
                    end_at,
                ),
            ).fetchall()
            coverage = tuple(
                {
                    "bucket_start_at": row["bucket_start_at"],
                    "bucket_end_at": row["bucket_end_at"],
                    "coverage_state": row["coverage_state"],
                    "observation_count": int(row["observation_count"]),
                    "snapshot_id": row["snapshot_id"],
                    "commit_sequence": int(row["commit_sequence"]),
                }
                for row in coverage_rows
            )
            table = USAGE_TABLE if PROFILE_SPECS[collection_profile].source_kind == "usage" else COST_TABLE
            observations: list[Mapping[str, object]] = []
            for selected in coverage:
                if selected["coverage_state"] == "no_observation":
                    continue
                rows = sql.execute(
                    f"SELECT * FROM {table} WHERE organization_id=? "
                    "AND snapshot_id=? AND bucket_start_at=? AND bucket_end_at=? "
                    "ORDER BY observation_digest",
                    (
                        principal.organization_id,
                        selected["snapshot_id"],
                        selected["bucket_start_at"],
                        selected["bucket_end_at"],
                    ),
                ).fetchall()
                if len(rows) != selected["observation_count"]:
                    raise FinanceCollectionError("unavailable")
                for row in rows:
                    observation = {
                        key: value
                        for key, value in dict(row).items()
                        if key not in {"organization_id", "observation_id"}
                    }
                    for field in ("batch", "provider_final", "invoice_final"):
                        if field not in observation:
                            continue
                        value = observation[field]
                        if value is None or type(value) is bool:
                            continue
                        if type(value) is int and value in {0, 1}:
                            observation[field] = bool(value)
                            continue
                        raise FinanceCollectionError("unavailable")
                    observations.append(observation)
            return CurrentCollectionView(
                principal.organization_id,
                binding_id,
                binding_version,
                collection_profile,
                coverage,
                tuple(observations),
            )


def create_finance_collection_repository(
    config: GatewayConfig,
    *,
    environ: Mapping[str, str] | None = None,
    connection_pool: PostgresConnectionPool | None = None,
    read_only: bool = False,
) -> FinanceCollectionRepository:
    """Construct without migration, credential resolution, or provider I/O."""

    storage = config.usage_storage
    dsn = ""
    if storage.backend == "postgresql":
        environment = os.environ if environ is None else environ
        dsn = environment.get(storage.postgres_dsn_env, "")
        if not dsn:
            raise FinanceCollectionError("unavailable")
    elif storage.backend != "sqlite":
        raise FinanceCollectionError("unavailable")
    return FinanceCollectionRepository(
        config,
        dsn=dsn,
        connection_pool=connection_pool,
        read_only=read_only,
    )


def _normalize_binding_request(
    request: Mapping[str, object],
    *,
    organization_id: str,
    fingerprint_key: bytes,
) -> dict[str, object]:
    if not isinstance(request, Mapping) or set(request) != _BIND_REQUEST_KEYS:
        raise FinanceCollectionError("invalid_request")
    if (
        request.get("schema_id") != "hormuz.finance-source-binding-request"
        or type(request.get("schema_version")) is not int
        or request.get("schema_version") != 1
        or not _safe_id(request.get("binding_id"))
        or request.get("provider") not in {"openai", "anthropic"}
        or not _safe_id(request.get("reason_code"))
        or request.get("state") not in {"active", "revoked"}
    ):
        raise FinanceCollectionError("invalid_request")
    expected = request.get("expected_version")
    if expected is not None and (
        type(expected) is not int or not 1 <= expected <= 2_147_483_647
    ):
        raise FinanceCollectionError("invalid_request")
    credential_version = request.get("credential_reference_version")
    key_version = request.get("fingerprint_key_version")
    if (
        type(credential_version) is not int
        or not 1 <= credential_version <= 2_147_483_647
        or type(key_version) is not int
        or not 1 <= key_version <= 2_147_483_647
    ):
        raise FinanceCollectionError("invalid_request")
    account = request.get("provider_account_reference_id")
    if not isinstance(account, str):
        raise FinanceCollectionError("invalid_request")
    scope = request.get("scope")
    if not isinstance(scope, Mapping) or set(scope) != {"kind", "ids"}:
        raise FinanceCollectionError("invalid_request")
    scope_kind, scope_ids = scope.get("kind"), scope.get("ids")
    if (
        scope_kind not in {"organization", "projects", "workspaces"}
        or not isinstance(scope_ids, list)
        or len(scope_ids) > 1000
        or any(not isinstance(item, str) for item in scope_ids)
        or len(set(scope_ids)) != len(scope_ids)
        or (scope_kind == "organization") != (scope_ids == [])
        or (request["provider"] == "openai" and scope_kind == "workspaces")
        or (request["provider"] == "anthropic" and scope_kind == "projects")
    ):
        raise FinanceCollectionError("invalid_request")
    if scope_kind != "organization" and not scope_ids:
        raise FinanceCollectionError("invalid_request")
    try:
        account_fingerprint = tenant_fingerprint(
            fingerprint_key,
            organization_id=organization_id,
            kind="provider-account",
            value=account,
        )
        scope_fingerprints = tuple(
            sorted(
                tenant_fingerprint(
                    fingerprint_key,
                    organization_id=organization_id,
                    kind="project" if scope_kind == "projects" else "workspace",
                    value=item,
                )
                for item in scope_ids
            )
        )
    except FinanceCollectionError:
        raise FinanceCollectionError("invalid_request") from None
    provider = str(request["provider"])
    return {
        "binding_id": request["binding_id"],
        "expected_version": expected,
        "provider": provider,
        "provider_account_fingerprint": account_fingerprint,
        "scope_kind": scope_kind,
        "scope_fingerprints": scope_fingerprints,
        "credential_reference_id": f"upstream:{provider}",
        "credential_reference_version": credential_version,
        "fingerprint_key_version": key_version,
        "binding_state": request["state"],
        "reason_code": request["reason_code"],
    }


def _binding_content(
    normalized: Mapping[str, object],
    *,
    version: int,
    previous_version: int | None,
) -> dict[str, object]:
    return {
        key: (list(value) if key == "scope_fingerprints" else value)
        for key, value in normalized.items()
        if key != "expected_version"
    } | {"version": version, "previous_version": previous_version}


def _binding_from_row(row: Mapping[str, object]) -> SourceBindingVersion:
    try:
        event = json.loads(str(row["evidence_json"]))
        if not isinstance(event, dict) or _canonical(event) != row["evidence_json"]:
            raise ValueError
        validate_finance_source_binding_event(event)
        scopes = json.loads(str(row["scope_fingerprints_json"]))
        if scopes != event["scope_fingerprints"]:
            raise ValueError
        expected = {
            "organization_id": event["organization_id"],
            "binding_id": event["binding_id"],
            "version": event["version"],
            "binding_event_id": event["binding_event_id"],
            "provider": event["provider"],
            "provider_account_fingerprint": event["provider_account_fingerprint"],
            "scope_kind": event["scope_kind"],
            "scope_fingerprints_json": _canonical(scopes),
            "credential_reference_id": event["credential_reference_id"],
            "credential_reference_version": event["credential_reference_version"],
            "fingerprint_key_version": event["fingerprint_key_version"],
            "binding_state": event["binding_state"],
            "previous_version": event["previous_version"],
            "content_digest": event["content_digest"],
            "bound_by": event["bound_by"],
            "bound_at": event["bound_at"],
            "evidence_json": _canonical(event),
        }
        if dict(row) != expected:
            raise ValueError
        return SourceBindingVersion(
            event["organization_id"],
            event["binding_id"],
            event["version"],
            event["binding_event_id"],
            event["provider"],
            event["provider_account_fingerprint"],
            event["scope_kind"],
            tuple(scopes),
            event["credential_reference_id"],
            event["credential_reference_version"],
            event["fingerprint_key_version"],
            event["binding_state"],
            event["previous_version"],
            event["content_digest"],
            event["bound_by"],
            event["bound_at"],
            event["reason_code"],
        )
    except (FinanceCollectionError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise FinanceCollectionError("unavailable") from None


def _prepared_from_row(sql: Any, row: Mapping[str, object]) -> PreparedCollectionAttempt:
    try:
        query = CollectionQuery(
            str(row["organization_id"]),
            str(row["binding_id"]),
            int(row["binding_version"]),
            str(row["collection_profile"]),
            str(row["query_start_at"]),
            str(row["query_end_at"]),
            str(row["bucket_width"]),
            int(row["requested_page_size"]),
        )
        terminal = sql.one(
            f"SELECT state,receipt_id,snapshot_id FROM {COLLECTION_EVENT_TABLE} "
            "WHERE organization_id=? AND attempt_id=?",
            (row["organization_id"], row["attempt_id"]),
        )
        state = "pending" if terminal is None else str(terminal["state"])
        return PreparedCollectionAttempt(
            str(row["organization_id"]),
            str(row["attempt_id"]),
            query,
            str(row["provider"]),
            str(row["source_kind"]),
            str(row["evidence_origin"]),
            str(row["idempotency_digest"]),
            str(row["request_digest"]),
            str(row["credential_reference_id"]),
            int(row["credential_reference_version"]),
            int(row["fingerprint_key_version"]),
            str(row["prepared_by"]),
            str(row["prepared_at"]),
            state,
            None if terminal is None else terminal["receipt_id"],
            None if terminal is None else terminal["snapshot_id"],
        )
    except (FinanceCollectionError, KeyError, TypeError, ValueError):
        raise FinanceCollectionError("unavailable") from None


def _same_prepared_root(
    first: PreparedCollectionAttempt,
    second: PreparedCollectionAttempt,
) -> bool:
    return (
        first.organization_id,
        first.attempt_id,
        first.query,
        first.provider,
        first.source_kind,
        first.evidence_origin,
        first.idempotency_digest,
        first.request_digest,
        first.credential_reference_id,
        first.credential_reference_version,
        first.fingerprint_key_version,
        first.prepared_by,
        first.prepared_at,
    ) == (
        second.organization_id,
        second.attempt_id,
        second.query,
        second.provider,
        second.source_kind,
        second.evidence_origin,
        second.idempotency_digest,
        second.request_digest,
        second.credential_reference_id,
        second.credential_reference_version,
        second.fingerprint_key_version,
        second.prepared_by,
        second.prepared_at,
    )


def _validate_binding_scope(
    binding: SourceBindingVersion,
    collection: NormalizedCollection,
) -> None:
    if binding.scope_kind == "organization":
        return
    allowed = set(binding.scope_fingerprints)
    observations = (
        collection.usage_observations
        if collection.query.profile.source_kind == "usage"
        else collection.cost_observations
    )
    field = (
        "provider_project_fingerprint"
        if binding.scope_kind == "projects"
        else "provider_workspace_fingerprint"
    )
    if any(getattr(observation, field) not in allowed for observation in observations):
        raise FinanceCollectionError("binding_inactive")


def _observation_counts(
    collection: NormalizedCollection,
) -> dict[tuple[str, str], int]:
    result: dict[tuple[str, str], int] = {}
    observations = (
        collection.usage_observations
        if collection.query.profile.source_kind == "usage"
        else collection.cost_observations
    )
    for item in observations:
        key = (item.bucket_start_at, item.bucket_end_at)
        result[key] = result.get(key, 0) + 1
    return result


def _receipt_for_attempt(
    sql: Any,
    organization_id: str,
    attempt_id: str,
    collection: NormalizedCollection | None,
) -> CollectionReceipt:
    row = sql.one(
        f"SELECT event.event_id,event.receipt_id,event.snapshot_id,event.occurred_at,"
        f"snapshot.content_digest,snapshot.page_chain_digest,"
        f"snapshot.supersedes_snapshot_id,snapshot.commit_sequence,snapshot.organization_id "
        f"FROM {COLLECTION_EVENT_TABLE} event JOIN {SNAPSHOT_TABLE} snapshot "
        f"ON snapshot.organization_id=event.organization_id "
        f"AND snapshot.snapshot_id=event.snapshot_id "
        f"WHERE event.organization_id=? AND event.attempt_id=? "
        f"AND event.state='succeeded'",
        (organization_id, attempt_id),
    )
    if (
        row is None
        or (
            collection is not None
            and (
                not hmac.compare_digest(
                    str(row["content_digest"]), collection.content_digest
                )
                or not hmac.compare_digest(
                    str(row["page_chain_digest"]),
                    collection.page_chain_digest,
                )
            )
        )
    ):
        raise FinanceCollectionError("attempt_conflict")
    return CollectionReceipt(
        str(row["organization_id"]),
        attempt_id,
        str(row["event_id"]),
        str(row["receipt_id"]),
        str(row["snapshot_id"]),
        str(row["content_digest"]),
        str(row["page_chain_digest"]),
        None if row["supersedes_snapshot_id"] is None else str(row["supersedes_snapshot_id"]),
        int(row["commit_sequence"]),
        str(row["occurred_at"]),
    )


def _append_audit(
    sql: Any,
    *,
    event: Mapping[str, object],
    source: AuditChainSource,
) -> None:
    organization_id = event.get("organization_id")
    if not isinstance(organization_id, str):
        raise FinanceCollectionError("unavailable")
    now = sql.now()
    if sql.postgres:
        sql.execute(
            "INSERT INTO gateway_audit_chain_epochs "
            "(organization_id,chain_version,chain_epoch,created_at,reason_code) "
            "VALUES (?,1,1,?,'initial_adoption') ON CONFLICT DO NOTHING",
            (organization_id, now),
        )
        sql.execute(
            "INSERT INTO gateway_audit_chain_heads "
            "(organization_id,chain_version,chain_epoch,sequence,head_digest) "
            "VALUES (?,1,1,0,NULL) ON CONFLICT DO NOTHING",
            (organization_id,),
        )
        head = sql.one(
            "SELECT chain_version,chain_epoch,sequence,head_digest "
            "FROM gateway_audit_chain_heads WHERE organization_id=? FOR UPDATE",
            (organization_id,),
        )
    else:
        sql.execute(
            "INSERT OR IGNORE INTO gateway_audit_chain_epochs "
            "(organization_id,chain_version,chain_epoch,created_at,reason_code) "
            "VALUES (?,1,1,?,'initial_adoption')",
            (organization_id, now),
        )
        sql.execute(
            "INSERT OR IGNORE INTO gateway_audit_chain_heads "
            "(organization_id,chain_version,chain_epoch,sequence,head_digest) "
            "VALUES (?,1,1,0,NULL)",
            (organization_id,),
        )
        head = sql.one(
            "SELECT chain_version,chain_epoch,sequence,head_digest "
            "FROM gateway_audit_chain_heads WHERE organization_id=?",
            (organization_id,),
        )
    if head is None:
        raise FinanceCollectionError("unavailable")
    try:
        entry = build_audit_chain_entry(
            event,
            chain_version=int(head["chain_version"]),
            chain_epoch=int(head["chain_epoch"]),
            sequence=int(head["sequence"]) + 1,
            previous_digest=head["head_digest"],
            entry_schema_version=2,
            source=source,
        )
        event_value = entry["event"]
        if not isinstance(event_value, Mapping):
            raise AuditChainError("audit_chain_entry_malformed")
        inserted = sql.execute(
            "INSERT INTO gateway_audit_chain_entries "
            "(organization_id,chain_version,chain_epoch,sequence,entry_schema_id,"
            "entry_schema_version,event_id,previous_digest,event_digest,event_json,"
            "appended_at,source_schema_id,source_schema_version,source_event_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                organization_id,
                entry["chain_version"],
                entry["chain_epoch"],
                entry["sequence"],
                entry["schema_id"],
                entry["schema_version"],
                source.event_id,
                entry["previous_digest"],
                entry["event_digest"],
                canonical_json_text(dict(event_value)),
                now,
                source.schema_id,
                source.schema_version,
                source.event_id,
            ),
        )
        if inserted.rowcount != 1:
            raise FinanceCollectionError("unavailable")
        predicate = (
            "head_digest IS NOT DISTINCT FROM ?"
            if sql.postgres
            else "head_digest IS ?"
        )
        updated = sql.execute(
            "UPDATE gateway_audit_chain_heads SET sequence=?,head_digest=? "
            "WHERE organization_id=? AND chain_version=? AND chain_epoch=? "
            f"AND sequence=? AND {predicate}",
            (
                entry["sequence"],
                entry["event_digest"],
                organization_id,
                head["chain_version"],
                head["chain_epoch"],
                head["sequence"],
                head["head_digest"],
            ),
        )
        if updated.rowcount != 1:
            raise FinanceCollectionError("unavailable")
    except AuditChainError:
        raise FinanceCollectionError("unavailable") from None


def _canonical(value: object) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise FinanceCollectionError("invalid_request") from None


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _safe_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 128
        and value[0].isalnum()
        and all(character in _ID_CHARS for character in value)
    )


def _selection_bounds(start_at: object, end_at: object) -> tuple[str, str]:
    """Validate a bounded canonical UTC reporting interval without re-bucketing it."""

    from datetime import timedelta

    from .finance_collection import MAX_WINDOW_DAYS, _parse_time, _time_text

    if not isinstance(start_at, str) or not isinstance(end_at, str):
        raise FinanceCollectionError("invalid_request")
    start = _parse_time(start_at)
    end = _parse_time(end_at)
    if (
        _time_text(start) != start_at
        or _time_text(end) != end_at
        or end <= start
        or end - start > timedelta(days=MAX_WINDOW_DAYS)
    ):
        raise FinanceCollectionError("invalid_request")
    return start_at, end_at
