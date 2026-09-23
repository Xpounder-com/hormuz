"""Authorized append-only repository for provider aggregate finance evidence."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from typing import TYPE_CHECKING, Any, Iterator, Mapping
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
from ._finance_account_binding_schema import (
    ATTEMPT_BINDING_TABLE,
    QUERY_AUDIT_TABLE,
    REGISTRATION_TABLE,
)
from ._portfolio_sql import FINANCE_COLLECTION_ACCOUNT_TABLES, portfolio_transaction
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
    MAX_WINDOW_DAYS,
    NormalizedCollection,
    finance_collection_source_identity,
    tenant_fingerprint,
    _unicode_safe,
    validate_normalized_collection,
    validate_finance_collection_event,
    validate_finance_snapshot_event,
    validate_finance_source_binding_event,
    _digest as _collection_digest,
)
from .finance_attempts import finance_attempt_event_from_row
from .finance_account_evidence import (
    ACCOUNT_BINDING_FIELDS,
    ATTEMPT_ACCOUNT_BINDING_FIELDS,
    QUERY_AUDIT_SCHEMA_ID,
    canonical_evidence_text,
    validate_finance_account_binding_event,
    validate_finance_attempt_account_binding_event,
    validate_finance_query_audit_event,
)
from .finance_values import FinanceValueError, currency_code
from .portfolio_config import PortfolioPrincipal
from .portfolio_wire import PortfolioError
from .postgres import POSTGRES_SCHEMA_VERSION, PostgresConnectionPool

if TYPE_CHECKING:
    from .finance_reconciliation_coverage import FinanceCoveragePreview


# Schema 17 implements the separately approved fixed 199-permission boundary.
# Implementation is not release acceptance: that requires exact-head/main
# evidence recorded on #8 and #214. Neither flag is inferred from database ACLs.
POSTGRES_FINANCE_COLLECTION_RUNTIME_ENABLED = True
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


@dataclass(frozen=True)
class SelectedCollectionSnapshot:
    snapshot_id: str
    content_digest: str
    commit_sequence: int


@dataclass(frozen=True)
class AsOfCollectionView:
    organization_id: str
    binding_id: str
    binding_version: int
    collection_profile: str
    as_of_commit_sequence: int
    selected_snapshots: tuple[SelectedCollectionSnapshot, ...]
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
        *,
        include_query_audit: bool = False,
    ) -> Iterator[Any]:
        self._authorize(principal)
        if (
            self.config.usage_storage.backend == "postgresql"
            and not POSTGRES_FINANCE_COLLECTION_RUNTIME_ENABLED
        ):
            raise FinanceCollectionError("unavailable")
        try:
            with portfolio_transaction(
                self.config,
                principal.organization_id,
                dsn=self._dsn,
                connection_pool=self._pool,
                tables=(
                    FINANCE_COLLECTION_ACCOUNT_TABLES
                    if include_query_audit
                    else TABLE_DDL
                ),
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
        _validate_collection_selection(binding_id, binding_version, collection_profile)
        start_at, end_at = _selection_bounds(start_at, end_at)
        with self._transaction(principal) as sql:
            coverage, observations, _ = _select_collection_observations(
                sql,
                organization_id=principal.organization_id,
                binding_id=binding_id,
                binding_version=binding_version,
                collection_profile=collection_profile,
                start_at=start_at,
                end_at=end_at,
            )
            return CurrentCollectionView(
                principal.organization_id,
                binding_id,
                binding_version,
                collection_profile,
                coverage,
                observations,
            )

    def observations_as_of(
        self,
        principal: PortfolioPrincipal,
        *,
        binding_id: str,
        binding_version: int,
        collection_profile: str,
        start_at: str,
        end_at: str,
        as_of_commit_sequence: int | None = None,
    ) -> AsOfCollectionView:
        """Pin a tenant-wide publication cutoff and replay exact bucket selection.

        A caller must retain the returned cutoff when it needs the same view
        after later collection publishes. The cutoff is not provider authority.
        """

        self._authorize(principal)
        _validate_collection_selection(binding_id, binding_version, collection_profile)
        start_at, end_at = _selection_bounds(start_at, end_at)
        if as_of_commit_sequence is not None and (
            type(as_of_commit_sequence) is not int
            or not 0 <= as_of_commit_sequence <= 9_223_372_036_854_775_807
        ):
            raise FinanceCollectionError("invalid_request")
        with self._transaction(principal) as sql:
            maximum = sql.one(
                f"SELECT COALESCE(MAX(commit_sequence),0) AS sequence "
                f"FROM {SNAPSHOT_TABLE} WHERE organization_id=?",
                (principal.organization_id,),
            )["sequence"]
            if type(maximum) is not int or not 0 <= maximum <= 9_223_372_036_854_775_807:
                raise FinanceCollectionError("unavailable")
            cutoff = maximum if as_of_commit_sequence is None else as_of_commit_sequence
            if cutoff > maximum:
                raise FinanceCollectionError("invalid_request")
            coverage, observations, snapshots = _select_collection_observations(
                sql,
                organization_id=principal.organization_id,
                binding_id=binding_id,
                binding_version=binding_version,
                collection_profile=collection_profile,
                start_at=start_at,
                end_at=end_at,
                as_of_commit_sequence=cutoff,
            )
            return AsOfCollectionView(
                principal.organization_id,
                binding_id,
                binding_version,
                collection_profile,
                cutoff,
                snapshots,
                coverage,
                observations,
            )

    def coverage_report_evidence(
        self,
        principal: PortfolioPrincipal,
        *,
        binding_id: str,
        binding_version: int,
        collection_profile: str,
        start_at: str,
        end_at: str,
        currency: str,
        as_of_commit_sequence: int | None = None,
    ) -> tuple[
        FinanceCoveragePreview,
        int,
        tuple[Mapping[str, str], ...],
        str,
    ]:
        result = self._coverage_report_evidence_internal(
            principal,
            source_binding_id=binding_id,
            source_binding_version=binding_version,
            account_binding_id=None,
            account_binding_version=None,
            collection_profile=collection_profile,
            start_at=start_at,
            end_at=end_at,
            currency=currency,
            as_of_commit_sequence=as_of_commit_sequence,
        )
        preview, missing_sidecars, provenance, query_event_id, _ = result
        return preview, missing_sidecars, provenance, query_event_id

    def account_reconciliation_report_evidence(
        self,
        principal: PortfolioPrincipal,
        *,
        account_binding_id: str,
        account_binding_version: int,
        collection_profile: str,
        start_at: str,
        end_at: str,
        currency: str,
        as_of_commit_sequence: int | None = None,
    ) -> tuple[
        FinanceCoveragePreview,
        int,
        tuple[Mapping[str, str], ...],
        str,
        Mapping[str, object],
    ]:
        """Build an account-bound comparison without provider or credential access."""

        return self._coverage_report_evidence_internal(
            principal,
            source_binding_id=None,
            source_binding_version=None,
            account_binding_id=account_binding_id,
            account_binding_version=account_binding_version,
            collection_profile=collection_profile,
            start_at=start_at,
            end_at=end_at,
            currency=currency,
            as_of_commit_sequence=as_of_commit_sequence,
        )

    def _coverage_report_evidence_internal(
        self,
        principal: PortfolioPrincipal,
        *,
        source_binding_id: str | None,
        source_binding_version: int | None,
        account_binding_id: str | None,
        account_binding_version: int | None,
        collection_profile: str,
        start_at: str,
        end_at: str,
        currency: str,
        as_of_commit_sequence: int | None = None,
    ) -> tuple[
        FinanceCoveragePreview,
        int,
        tuple[Mapping[str, str], ...],
        str,
        Mapping[str, object] | None,
    ]:
        """Read a cost selection and every terminal sidecar in one tenant transaction.

        The integer counts terminal attempts without a finance sidecar. The
        provenance tuple carries the stored source origin and scope for each
        selected cost snapshot. The final string is the committed query-audit
        event receipt. Source-mode reports retain the existing unbound preview;
        account mode admits only attempts carrying the exact immutable binding.
        """

        self._authorize(principal)
        source_mode = (
            source_binding_id is not None
            and source_binding_version is not None
            and account_binding_id is None
            and account_binding_version is None
        )
        account_mode = (
            account_binding_id is not None
            and account_binding_version is not None
            and source_binding_id is None
            and source_binding_version is None
        )
        if source_mode:
            binding_id = source_binding_id
            binding_version = source_binding_version
        elif account_mode:
            binding_id = account_binding_id
            binding_version = account_binding_version
        else:
            raise FinanceCollectionError("invalid_request")
        _validate_collection_selection(binding_id, binding_version, collection_profile)
        try:
            if currency_code(currency) != currency:
                raise FinanceValueError("finance_invalid_amount")
        except FinanceValueError:
            raise FinanceCollectionError("invalid_request") from None
        profile = PROFILE_SPECS[collection_profile]
        if profile.source_kind != "cost":
            raise FinanceCollectionError("invalid_request")
        start_at, end_at = _selection_bounds(start_at, end_at)
        if start_at[11:] != "00:00:00Z" or end_at[11:] != "00:00:00Z":
            raise FinanceCollectionError("invalid_request")
        if as_of_commit_sequence is not None and (
            type(as_of_commit_sequence) is not int
            or not 0 <= as_of_commit_sequence <= 9_223_372_036_854_775_807
        ):
            raise FinanceCollectionError("invalid_request")

        # The sidecar stores canonical +00:00 timestamps, whereas collection
        # bucket bounds use Z. Normalize before SQLite lexical comparison;
        # PostgreSQL accepts the same value as TIMESTAMPTZ.
        terminal_start = start_at[:-1] + "+00:00"
        terminal_end = end_at[:-1] + "+00:00"
        provider_profile = {
            "openai": "openai.responses.usage.v1",
            "anthropic": "anthropic.messages.usage.v1",
        }[profile.provider]
        from .finance_reconciliation_coverage import (
            MAX_PREVIEW_ROWS,
            build_finance_coverage_preview,
        )

        with self._transaction(principal, include_query_audit=True) as sql:
            selected_account: Mapping[str, object] | None = None
            selected_source: SourceBindingVersion | None = None
            if account_mode:
                selected_account = _selected_account_binding_event(
                    sql,
                    organization_id=principal.organization_id,
                    binding_id=binding_id,
                    binding_version=binding_version,
                )
                if selected_account["binding_state"] != "active":
                    raise FinanceCollectionError("invalid_request")
                selected_source = _selected_account_source_binding(
                    sql,
                    organization_id=principal.organization_id,
                    account_event=selected_account,
                )
                if selected_source.provider != profile.provider:
                    raise FinanceCollectionError("invalid_request")
                binding_id = selected_source.binding_id
                binding_version = selected_source.version

            maximum = sql.one(
                f"SELECT COALESCE(MAX(commit_sequence),0) AS sequence "
                f"FROM {SNAPSHOT_TABLE} WHERE organization_id=?",
                (principal.organization_id,),
            )["sequence"]
            if type(maximum) is not int or not 0 <= maximum <= 9_223_372_036_854_775_807:
                raise FinanceCollectionError("unavailable")
            cutoff = maximum if as_of_commit_sequence is None else as_of_commit_sequence
            if cutoff > maximum:
                raise FinanceCollectionError("invalid_request")
            coverage, observations, snapshots = _select_collection_observations(
                sql,
                organization_id=principal.organization_id,
                binding_id=binding_id,
                binding_version=binding_version,
                collection_profile=collection_profile,
                start_at=start_at,
                end_at=end_at,
                as_of_commit_sequence=cutoff,
                max_observations=MAX_PREVIEW_ROWS,
                max_coverage=MAX_WINDOW_DAYS,
            )
            for observation in observations:
                body = dict(observation)
                digest = body.pop("observation_digest", None)
                body.pop("snapshot_id", None)
                try:
                    if _collection_digest(body) != digest:
                        raise ValueError
                except (FinanceCollectionError, TypeError, ValueError):
                    raise FinanceCollectionError("unavailable") from None
            selected_provenance: list[Mapping[str, str]] = []
            for selected in snapshots:
                stored = sql.one(
                    f"SELECT snapshot.evidence_json, chain.event_json AS audit_event_json "
                    f"FROM {SNAPSHOT_TABLE} snapshot "
                    "LEFT JOIN gateway_audit_chain_entries chain "
                    "ON chain.organization_id=snapshot.organization_id "
                    "AND chain.entry_schema_version=2 "
                    "AND chain.source_schema_id='hormuz.finance-snapshot' "
                    "AND chain.source_schema_version=1 "
                    "AND chain.source_event_id=snapshot.snapshot_id "
                    "WHERE snapshot.organization_id=? AND snapshot.snapshot_id=?",
                    (principal.organization_id, selected.snapshot_id),
                )
                try:
                    if stored is None or stored["audit_event_json"] != stored["evidence_json"]:
                        raise ValueError
                    source = json.loads(stored["evidence_json"])
                    if (
                        canonical_json_text(source) != stored["evidence_json"]
                        or finance_collection_source_identity("hormuz.finance-snapshot", source)
                        != selected.snapshot_id
                        or source["organization_id"] != principal.organization_id
                        or source["binding_id"] != binding_id
                        or source["binding_version"] != binding_version
                        or source["collection_profile"] != collection_profile
                        or source["commit_sequence"] != selected.commit_sequence
                        or source["content_digest"] != selected.content_digest
                    ):
                        raise ValueError
                    selected_provenance.append({
                        "snapshot_id": selected.snapshot_id,
                        "evidence_origin": source["evidence_origin"],
                        "scope_provenance": source["scope_provenance"],
                    })
                except (
                    AuditChainError, FinanceCollectionError, KeyError, OverflowError,
                    RecursionError, TypeError, UnicodeError, ValueError,
                ):
                    raise FinanceCollectionError("unavailable") from None

            account_columns = ", ".join(
                f"account.{field} AS account_{field}"
                for field in sorted(
                    ATTEMPT_ACCOUNT_BINDING_FIELDS - {"schema_id", "schema_version"}
                )
            )
            account_evidence_select = (
                f"{account_columns}, account.evidence_json AS account_evidence_json, "
                "account_chain.event_json AS account_audit_event_json, "
                "attempt_registration.evidence_json AS account_registration_evidence_json, "
                "registration_chain.event_json AS account_registration_audit_event_json, "
                "attempt_source.evidence_json AS account_source_evidence_json, "
                "source_chain.event_json AS account_source_audit_event_json"
            )
            account_select = (
                f", {account_evidence_select}, root.attempt_id AS root_attempt_id, "
                "root.created_at AS root_created_at "
                if account_mode
                else ""
            )
            account_joins = (
                f"LEFT JOIN {ATTEMPT_BINDING_TABLE} account "
                "ON account.organization_id=root.organization_id "
                "AND account.request_attempt_id=root.attempt_id "
                "LEFT JOIN gateway_audit_chain_entries account_chain "
                "ON account_chain.organization_id=account.organization_id "
                "AND account_chain.entry_schema_version=2 "
                "AND account_chain.source_schema_id='hormuz.finance-attempt-account-binding' "
                "AND account_chain.source_schema_version=1 "
                "AND account_chain.source_event_id=account.event_id "
                f"LEFT JOIN {REGISTRATION_TABLE} attempt_registration "
                "ON attempt_registration.organization_id=account.organization_id "
                "AND attempt_registration.binding_id=account.binding_id "
                "AND attempt_registration.version=account.binding_version "
                "LEFT JOIN gateway_audit_chain_entries registration_chain "
                "ON registration_chain.organization_id=attempt_registration.organization_id "
                "AND registration_chain.entry_schema_version=2 "
                "AND registration_chain.source_schema_id='hormuz.finance-account-binding-version' "
                "AND registration_chain.source_schema_version=1 "
                "AND registration_chain.source_event_id=attempt_registration.binding_event_id "
                f"LEFT JOIN {SOURCE_BINDING_TABLE} attempt_source "
                "ON attempt_source.organization_id=account.organization_id "
                "AND attempt_source.binding_id=account.source_binding_id "
                "AND attempt_source.version=account.source_binding_version "
                "LEFT JOIN gateway_audit_chain_entries source_chain "
                "ON source_chain.organization_id=attempt_source.organization_id "
                "AND source_chain.entry_schema_version=2 "
                "AND source_chain.source_schema_id='hormuz.finance-source-binding-version' "
                "AND source_chain.source_schema_version=1 "
                "AND source_chain.source_event_id=attempt_source.binding_event_id "
                if account_mode
                else ""
            )
            if account_mode:
                account_attempt_rows = sql.execute(
                    f"SELECT terminal.id AS terminal_id, "
                    "terminal.state AS terminal_state_check, "
                    "terminal.occurred_at AS terminal_occurred_at, finance.*, "
                    f"chain.event_json AS audit_event_json{account_select}, "
                    "CASE WHEN terminal.occurred_at>=? AND terminal.occurred_at<? "
                    "THEN 1 ELSE 0 END AS in_audit_window "
                    "FROM gateway_request_attempts root "
                    "LEFT JOIN gateway_request_attempt_events terminal "
                    "ON terminal.organization_id=root.organization_id "
                    "AND terminal.attempt_id=root.attempt_id "
                    "AND terminal.state IN "
                    "('succeeded','failed','rate_limited','outcome_unknown') "
                    "LEFT JOIN gateway_finance_attempt_evidence finance "
                    "ON finance.organization_id=terminal.organization_id "
                    "AND finance.terminal_attempt_event_id=terminal.id "
                    "LEFT JOIN gateway_audit_chain_entries chain "
                    "ON chain.organization_id=finance.organization_id "
                    "AND chain.entry_schema_version=2 "
                    "AND chain.source_schema_id='hormuz.finance-attempt-evidence' "
                    "AND chain.source_schema_version=1 "
                    "AND chain.source_event_id=finance.evidence_event_id "
                    f"{account_joins}"
                    "WHERE root.organization_id=? AND root.protocol=? AND ("
                    "(terminal.occurred_at>=? AND terminal.occurred_at<?) OR "
                    "(root.created_at<? AND "
                    "(terminal.id IS NULL OR terminal.occurred_at>=?))) "
                    "ORDER BY COALESCE(terminal.occurred_at,root.created_at), "
                    "root.attempt_id LIMIT ?",
                    (
                        terminal_start,
                        terminal_end,
                        principal.organization_id,
                        profile.provider,
                        terminal_start,
                        terminal_end,
                        terminal_end,
                        terminal_end,
                        MAX_PREVIEW_ROWS + 1,
                    ),
                ).fetchall()
                if len(account_attempt_rows) > MAX_PREVIEW_ROWS:
                    raise FinanceCollectionError("unavailable")
                base_terminal_rows = tuple(
                    row for row in account_attempt_rows
                    if row["terminal_id"] is not None and bool(row["in_audit_window"])
                )
                boundary_terminal_rows = tuple(
                    row for row in account_attempt_rows
                    if row["terminal_id"] is not None and not bool(row["in_audit_window"])
                )
                pending_attempt_rows = tuple(
                    row for row in account_attempt_rows
                    if row["terminal_id"] is None
                )
            else:
                base_terminal_rows = sql.execute(
                    "SELECT terminal.id AS terminal_id, "
                    "terminal.state AS terminal_state_check, "
                    "terminal.occurred_at AS terminal_occurred_at, finance.*, "
                    "chain.event_json AS audit_event_json "
                    "FROM gateway_request_attempt_events terminal "
                    "JOIN gateway_request_attempts root "
                    "ON root.organization_id=terminal.organization_id "
                    "AND root.attempt_id=terminal.attempt_id "
                    "LEFT JOIN gateway_finance_attempt_evidence finance "
                    "ON finance.organization_id=terminal.organization_id "
                    "AND finance.terminal_attempt_event_id=terminal.id "
                    "LEFT JOIN gateway_audit_chain_entries chain "
                    "ON chain.organization_id=finance.organization_id "
                    "AND chain.entry_schema_version=2 "
                    "AND chain.source_schema_id='hormuz.finance-attempt-evidence' "
                    "AND chain.source_schema_version=1 "
                    "AND chain.source_event_id=finance.evidence_event_id "
                    "WHERE terminal.organization_id=? AND root.protocol=? "
                    "AND terminal.state IN "
                    "('succeeded','failed','rate_limited','outcome_unknown') "
                    "AND terminal.occurred_at>=? AND terminal.occurred_at<? "
                    "ORDER BY terminal.occurred_at,terminal.id LIMIT ?",
                    (
                        principal.organization_id,
                        profile.provider,
                        terminal_start,
                        terminal_end,
                        MAX_PREVIEW_ROWS + 1,
                    ),
                ).fetchall()
                boundary_terminal_rows = ()
                pending_attempt_rows = ()
            terminal_rows = (
                tuple((row, True) for row in base_terminal_rows)
                + tuple((row, False) for row in boundary_terminal_rows)
            )
            if len(terminal_rows) + len(pending_attempt_rows) > MAX_PREVIEW_ROWS:
                raise FinanceCollectionError("unavailable")
            events: list[Mapping[str, object]] = []
            missing_sidecars = 0
            audit_missing_sidecars = 0
            matched_attempts = 0
            matched_missing_finance = 0
            unbound_attempts = 0
            historical_missing_account = 0
            same_account_other_binding = 0
            other_account_attempts = 0
            period_boundary_crossing_attempts = 0
            pending_account_gap = 0
            other_account_pending = 0
            unbound_reasons: dict[str, int] = {}
            for raw, in_audit_window in terminal_rows:
                row = dict(raw)
                account_class = "matched"
                if account_mode:
                    if selected_account is None:
                        raise FinanceCollectionError("unavailable")
                    account_event = _attempt_account_binding_event(
                        row,
                        organization_id=principal.organization_id,
                        provider=profile.provider,
                    )
                    if account_event is None:
                        account_class = "historical_missing"
                        historical_missing_account += 1
                    elif account_event["state"] == "unbound":
                        account_class = "unbound"
                        unbound_attempts += 1
                        reason = str(account_event["reason_code"])
                        unbound_reasons[reason] = unbound_reasons.get(reason, 0) + 1
                    elif (
                        account_event["binding_id"] == selected_account["binding_id"]
                        and account_event["binding_version"] == selected_account["version"]
                        and account_event["binding_digest"]
                        == selected_account["content_digest"]
                    ):
                        if _attempt_lifetime_within_period(
                            row,
                            start_at=start_at,
                            end_at=end_at,
                        ):
                            matched_attempts += 1
                        else:
                            account_class = "period_boundary_crossing"
                            period_boundary_crossing_attempts += 1
                    elif (
                        account_event["binding_id"] == selected_account["binding_id"]
                        or _same_account_grain(account_event, selected_account)
                    ):
                        account_class = "same_account_other_binding"
                        same_account_other_binding += 1
                    else:
                        account_class = "other_account"
                        other_account_attempts += 1

                finance_event: Mapping[str, object] | None = None
                if row["evidence_event_id"] is None:
                    missing_sidecars += 1
                    audit_missing_sidecars += in_audit_window
                    if account_class == "matched" and account_mode:
                        matched_missing_finance += 1
                else:
                    try:
                        event = finance_attempt_event_from_row(row)
                        occurred = _stored_timestamp_text(row["terminal_occurred_at"])
                        if (
                            event["organization_id"] != principal.organization_id
                            or event["provider_schema_id"] != provider_profile
                            or event["terminal_attempt_event_id"] != row["terminal_id"]
                            or event["terminal_state"] != row["terminal_state_check"]
                            or event["occurred_at"] != occurred
                            or canonical_json_text(event) != row["evidence_json"]
                            or row["audit_event_json"] != row["evidence_json"]
                        ):
                            raise ValueError
                    except (
                        AuditChainError, KeyError, OverflowError, RecursionError,
                        TypeError, UnicodeError, ValueError,
                    ):
                        raise FinanceCollectionError("unavailable") from None
                    finance_event = event
                if finance_event is not None and account_class == "matched":
                    events.append(finance_event)
            for raw in pending_attempt_rows:
                if selected_account is None:
                    raise FinanceCollectionError("unavailable")
                account_event = _attempt_account_binding_event(
                    dict(raw),
                    organization_id=principal.organization_id,
                    provider=profile.provider,
                )
                if (
                    account_event is None
                    or account_event["state"] == "unbound"
                    or account_event["binding_id"] == selected_account["binding_id"]
                    or _same_account_grain(account_event, selected_account)
                ):
                    pending_account_gap += 1
                else:
                    other_account_pending += 1
            view = AsOfCollectionView(
                principal.organization_id, binding_id, binding_version,
                collection_profile, cutoff, snapshots, coverage, observations,
            )
            event_values = tuple(events)
            preview = build_finance_coverage_preview(
                provider_view=view,
                gateway_attempt_events=event_values,
                start_at=start_at,
                end_at=end_at,
                currency=currency,
                account_binding_state="matched" if account_mode else "unavailable",
            )
            reconciliation = None
            if account_mode:
                if selected_account is None or selected_source is None:
                    raise FinanceCollectionError("unavailable")
                reconciliation = _account_reconciliation_result(
                    account_event=selected_account,
                    source_binding=selected_source,
                    preview=preview,
                    provider_observations=observations,
                    gateway_attempt_events=event_values,
                    matched_attempt_count=matched_attempts,
                    matched_missing_finance_sidecar_count=matched_missing_finance,
                    unbound_attempt_count=unbound_attempts,
                    historical_missing_account_binding_count=historical_missing_account,
                    same_account_other_binding_count=same_account_other_binding,
                    other_account_attempt_count=other_account_attempts,
                    period_boundary_crossing_attempt_count=(
                        period_boundary_crossing_attempts
                    ),
                    pending_account_gap_count=pending_account_gap,
                    other_account_pending_attempt_count=other_account_pending,
                    unbound_reason_counts=unbound_reasons,
                )
            query_event_id = str(uuid4())
            _append_query_audit(
                sql,
                event={
                    "schema_id": QUERY_AUDIT_SCHEMA_ID,
                    "schema_version": 1,
                    "organization_id": principal.organization_id,
                    "query_event_id": query_event_id,
                    "actor_id": principal.actor_id,
                    "query_class": "finance_coverage_report_v1",
                    "binding_id": binding_id,
                    "binding_version": binding_version,
                    "collection_profile": collection_profile,
                    "query_start_at": start_at,
                    "query_end_at": end_at,
                    "as_of_commit_sequence": cutoff,
                    "currency": currency,
                    "selected_snapshot_count": len(snapshots),
                    "coverage_bucket_count": len(coverage),
                    "provider_observation_count": len(observations),
                    "terminal_attempt_count": len(base_terminal_rows),
                    "terminal_attempts_missing_sidecar_count": audit_missing_sidecars,
                    "occurred_at": sql.now(),
                },
            )
            self._authorize(principal)
            return (
                preview,
                missing_sidecars,
                tuple(selected_provenance),
                query_event_id,
                reconciliation,
            )


_ACCOUNT_BINDING_STORAGE_FIELDS = (
    ACCOUNT_BINDING_FIELDS - {"schema_id", "schema_version"}
) | {"evidence_json"}
_ATTEMPT_ACCOUNT_STORAGE_FIELDS = (
    ATTEMPT_ACCOUNT_BINDING_FIELDS - {"schema_id", "schema_version"}
)
_ATTEMPT_ACCOUNT_LINK_FIELDS = (
    "account_registration_evidence_json",
    "account_registration_audit_event_json",
    "account_source_evidence_json",
    "account_source_audit_event_json",
)


def _validated_account_event(
    evidence_json: object,
    audit_event_json: object,
) -> Mapping[str, object]:
    try:
        if (
            not isinstance(evidence_json, str)
            or audit_event_json != evidence_json
        ):
            raise ValueError
        event = json.loads(evidence_json)
        if (
            not isinstance(event, dict)
            or canonical_evidence_text(event) != evidence_json
        ):
            raise ValueError
        validate_finance_account_binding_event(event)
        return event
    except (
        KeyError,
        OverflowError,
        RecursionError,
        TypeError,
        UnicodeError,
        ValueError,
        json.JSONDecodeError,
    ):
        raise FinanceCollectionError("unavailable") from None


def _selected_account_binding_event(
    sql: Any,
    *,
    organization_id: str,
    binding_id: str,
    binding_version: int,
) -> Mapping[str, object]:
    row = sql.one(
        f"SELECT registration.*, chain.event_json AS audit_event_json "
        f"FROM {REGISTRATION_TABLE} registration "
        "LEFT JOIN gateway_audit_chain_entries chain "
        "ON chain.organization_id=registration.organization_id "
        "AND chain.entry_schema_version=2 "
        "AND chain.source_schema_id='hormuz.finance-account-binding-version' "
        "AND chain.source_schema_version=1 "
        "AND chain.source_event_id=registration.binding_event_id "
        "WHERE registration.organization_id=? AND registration.binding_id=? "
        "AND registration.version=?",
        (organization_id, binding_id, binding_version),
    )
    try:
        if row is None:
            raise ValueError
        stored = dict(row)
        audit_event_json = stored.pop("audit_event_json")
        if set(stored) != _ACCOUNT_BINDING_STORAGE_FIELDS:
            raise ValueError
        event = _validated_account_event(
            stored["evidence_json"],
            audit_event_json,
        )
        if any(
            type(stored[field]) is not type(event[field])
            or stored[field] != event[field]
            for field in ACCOUNT_BINDING_FIELDS - {"schema_id", "schema_version"}
        ):
            raise ValueError
        if (
            event["organization_id"] != organization_id
            or event["binding_id"] != binding_id
            or event["version"] != binding_version
        ):
            raise ValueError
        return event
    except FinanceCollectionError:
        raise
    except (
        KeyError,
        OverflowError,
        RecursionError,
        TypeError,
        UnicodeError,
        ValueError,
    ):
        raise FinanceCollectionError("unavailable") from None


def _selected_account_source_binding(
    sql: Any,
    *,
    organization_id: str,
    account_event: Mapping[str, object],
) -> SourceBindingVersion:
    row = sql.one(
        f"SELECT source.*, chain.event_json AS audit_event_json "
        f"FROM {SOURCE_BINDING_TABLE} source "
        "LEFT JOIN gateway_audit_chain_entries chain "
        "ON chain.organization_id=source.organization_id "
        "AND chain.entry_schema_version=2 "
        "AND chain.source_schema_id='hormuz.finance-source-binding-version' "
        "AND chain.source_schema_version=1 "
        "AND chain.source_event_id=source.binding_event_id "
        "WHERE source.organization_id=? AND source.binding_id=? AND source.version=?",
        (
            organization_id,
            account_event["source_binding_id"],
            account_event["source_binding_version"],
        ),
    )
    try:
        if row is None:
            raise ValueError
        stored = dict(row)
        audit_event_json = stored.pop("audit_event_json")
        if audit_event_json != stored.get("evidence_json"):
            raise ValueError
        source = _binding_from_row(stored)
        if (
            source.organization_id != organization_id
            or source.binding_id != account_event["source_binding_id"]
            or source.version != account_event["source_binding_version"]
            or source.content_digest != account_event["source_binding_digest"]
            or source.provider != account_event["provider"]
            or source.provider_account_fingerprint
            != account_event["provider_account_fingerprint"]
            or source.scope_kind != account_event["scope_kind"]
            or canonical_evidence_text(list(source.scope_fingerprints))
            != account_event["scope_fingerprints_json"]
            or source.fingerprint_key_version
            != account_event["fingerprint_key_version"]
        ):
            raise ValueError
        return source
    except FinanceCollectionError:
        raise
    except (
        KeyError,
        OverflowError,
        RecursionError,
        TypeError,
        UnicodeError,
        ValueError,
    ):
        raise FinanceCollectionError("unavailable") from None


def _stored_timestamp_text(value: object) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("finance_timestamp_invalid")
        return value.astimezone(timezone.utc).isoformat()
    if not isinstance(value, str):
        raise ValueError("finance_timestamp_invalid")
    return value


def _attempt_lifetime_within_period(
    row: Mapping[str, object],
    *,
    start_at: str,
    end_at: str,
) -> bool:
    try:
        start = datetime.fromisoformat(start_at.replace("Z", "+00:00"))
        end = datetime.fromisoformat(end_at.replace("Z", "+00:00"))
        created = datetime.fromisoformat(
            _stored_timestamp_text(row["root_created_at"]).replace("Z", "+00:00")
        )
        terminal = datetime.fromisoformat(
            _stored_timestamp_text(row["terminal_occurred_at"]).replace("Z", "+00:00")
        )
        if (
            any(value.tzinfo is None for value in (start, end, created, terminal))
            or created > terminal
        ):
            raise ValueError
        return start <= created and terminal < end
    except (KeyError, TypeError, ValueError):
        raise FinanceCollectionError("unavailable") from None


def _validated_source_event(
    evidence_json: object,
    audit_event_json: object,
) -> Mapping[str, object]:
    try:
        if (
            not isinstance(evidence_json, str)
            or audit_event_json != evidence_json
        ):
            raise ValueError
        event = json.loads(evidence_json)
        if (
            not isinstance(event, dict)
            or canonical_evidence_text(event) != evidence_json
            or finance_collection_source_identity(
                "hormuz.finance-source-binding-version",
                event,
            )
            != event.get("binding_event_id")
        ):
            raise ValueError
        return event
    except (
        FinanceCollectionError,
        KeyError,
        OverflowError,
        RecursionError,
        TypeError,
        UnicodeError,
        ValueError,
        json.JSONDecodeError,
    ):
        raise FinanceCollectionError("unavailable") from None


def _attempt_account_binding_event(
    row: Mapping[str, object],
    *,
    organization_id: str,
    provider: str,
) -> Mapping[str, object] | None:
    try:
        stored = {
            field: row[f"account_{field}"]
            for field in _ATTEMPT_ACCOUNT_STORAGE_FIELDS
        }
        evidence_json = row["account_evidence_json"]
        audit_event_json = row["account_audit_event_json"]
        link_values = tuple(row[field] for field in _ATTEMPT_ACCOUNT_LINK_FIELDS)
        if evidence_json is None:
            if (
                audit_event_json is not None
                or any(value is not None for value in stored.values())
                or any(value is not None for value in link_values)
            ):
                raise ValueError
            return None
        if not isinstance(evidence_json, str) or audit_event_json != evidence_json:
            raise ValueError
        event = json.loads(evidence_json)
        if (
            not isinstance(event, dict)
            or canonical_evidence_text(event) != evidence_json
        ):
            raise ValueError
        validate_finance_attempt_account_binding_event(event)
        if any(
            type(stored[field]) is not type(event[field])
            or stored[field] != event[field]
            for field in _ATTEMPT_ACCOUNT_STORAGE_FIELDS
        ):
            raise ValueError
        if (
            event["organization_id"] != organization_id
            or event["request_attempt_id"] != row["root_attempt_id"]
            or event["captured_at"]
            != _stored_timestamp_text(row["root_created_at"])
        ):
            raise ValueError
        if event["state"] == "unbound":
            if any(value is not None for value in link_values):
                raise ValueError
            return event

        registration = _validated_account_event(link_values[0], link_values[1])
        source = _validated_source_event(link_values[2], link_values[3])
        registration_pairs = (
            ("binding_id", "binding_id"),
            ("binding_version", "version"),
            ("binding_digest", "content_digest"),
            ("upstream_reference_id", "upstream_reference_id"),
            ("upstream_reference_version", "upstream_reference_version"),
            ("transport_profile", "transport_profile"),
            (
                "inference_credential_reference_id",
                "inference_credential_reference_id",
            ),
            (
                "inference_credential_reference_version",
                "inference_credential_reference_version",
            ),
            ("source_binding_id", "source_binding_id"),
            ("source_binding_version", "source_binding_version"),
            ("source_binding_digest", "source_binding_digest"),
            ("provider", "provider"),
            ("provider_account_fingerprint", "provider_account_fingerprint"),
            ("scope_kind", "scope_kind"),
            ("scope_fingerprints_json", "scope_fingerprints_json"),
            ("fingerprint_key_version", "fingerprint_key_version"),
        )
        if (
            registration["organization_id"] != organization_id
            or registration["binding_state"] != "active"
            or any(event[left] != registration[right] for left, right in registration_pairs)
            or source["organization_id"] != organization_id
            or source["binding_id"] != event["source_binding_id"]
            or source["version"] != event["source_binding_version"]
            or source["content_digest"] != event["source_binding_digest"]
            or source["provider"] != event["provider"]
            or source["provider"] != provider
            or source["provider_account_fingerprint"]
            != event["provider_account_fingerprint"]
            or source["scope_kind"] != event["scope_kind"]
            or canonical_evidence_text(source["scope_fingerprints"])
            != event["scope_fingerprints_json"]
            or source["fingerprint_key_version"]
            != event["fingerprint_key_version"]
        ):
            raise ValueError
        return event
    except FinanceCollectionError:
        raise
    except (
        KeyError,
        OverflowError,
        RecursionError,
        TypeError,
        UnicodeError,
        ValueError,
        json.JSONDecodeError,
    ):
        raise FinanceCollectionError("unavailable") from None


def _same_account_grain(
    attempt_event: Mapping[str, object],
    selected_account: Mapping[str, object],
) -> bool:
    return all(
        attempt_event[field] == selected_account[field]
        for field in (
            "source_binding_id",
            "source_binding_version",
            "source_binding_digest",
            "provider",
            "provider_account_fingerprint",
            "scope_kind",
            "scope_fingerprints_json",
            "fingerprint_key_version",
        )
    )


def _account_reconciliation_result(
    *,
    account_event: Mapping[str, object],
    source_binding: SourceBindingVersion,
    preview: FinanceCoveragePreview,
    provider_observations: tuple[Mapping[str, object], ...],
    gateway_attempt_events: tuple[Mapping[str, object], ...],
    matched_attempt_count: int,
    matched_missing_finance_sidecar_count: int,
    unbound_attempt_count: int,
    historical_missing_account_binding_count: int,
    same_account_other_binding_count: int,
    other_account_attempt_count: int,
    period_boundary_crossing_attempt_count: int,
    pending_account_gap_count: int,
    other_account_pending_attempt_count: int,
    unbound_reason_counts: Mapping[str, int],
) -> Mapping[str, object]:
    from .finance_variance_reference import (
        BoundGatewayScopeClaim,
        ComparableAccountGrain,
        FinanceVarianceReferenceError,
        GatewayEstimateRow,
        ProviderCostRow,
        calculate_reference_variance,
    )

    result: dict[str, object] = {
        "account_binding_id": account_event["binding_id"],
        "account_binding_version": account_event["version"],
        "account_binding_digest": account_event["content_digest"],
        "account_binding_event_id": account_event["binding_event_id"],
        "source_binding_id": source_binding.binding_id,
        "source_binding_version": source_binding.version,
        "source_binding_digest": source_binding.content_digest,
        "matching_basis": "operator_attested_unverified",
        "matched_terminal_attempt_count": matched_attempt_count,
        "matched_finance_attempt_count": len(gateway_attempt_events),
        "matched_terminal_attempts_missing_finance_sidecar_count": (
            matched_missing_finance_sidecar_count
        ),
        "unbound_terminal_attempt_count": unbound_attempt_count,
        "unbound_reason_counts": dict(sorted(unbound_reason_counts.items())),
        "historical_missing_account_binding_count": (
            historical_missing_account_binding_count
        ),
        "same_account_other_binding_count": same_account_other_binding_count,
        "other_account_attempt_count": other_account_attempt_count,
        "period_boundary_crossing_attempt_count": (
            period_boundary_crossing_attempt_count
        ),
        "pending_account_gap_count": pending_account_gap_count,
        "other_account_pending_attempt_count": (
            other_account_pending_attempt_count
        ),
        "period_matching_basis": (
            "attempt_lifetime_fully_contained_in_selected_utc_window_unverified"
        ),
        "provider_coverage_state": preview.provider_cost.numeric_selection_state,
        "gateway_pricing_state": preview.gateway_estimate.supplied_attempt_pricing,
        "provider_total": preview.provider_cost.known_subtotal,
        "gateway_estimate_known_subtotal": preview.gateway_estimate.known_subtotal,
        "signed_variance": None,
        "absolute_variance": None,
        "relative_variance": None,
        "variance_state": "provider_coverage_incomplete",
        "review_status": "not_evaluated",
        "bypass_state": "unknown",
        "provider_final": False,
        "invoice_final": False,
    }
    account_gap_count = (
        matched_missing_finance_sidecar_count
        + unbound_attempt_count
        + historical_missing_account_binding_count
        + same_account_other_binding_count
        + period_boundary_crossing_attempt_count
        + pending_account_gap_count
    )
    provider_complete = (
        preview.provider_cost.numeric_selection_state
        == "all_selected_buckets_observed"
        and preview.provider_cost.known_subtotal is not None
    )
    gateway_complete = (
        matched_missing_finance_sidecar_count == 0
        and preview.gateway_estimate.attempt_count == matched_attempt_count
        and preview.gateway_estimate.unpriced_attempt_count == 0
        and preview.gateway_estimate.currency_mismatch_attempt_count == 0
        and preview.gateway_estimate.supplied_attempt_pricing
        in {"all_supplied_attempts_priced", "no_attempts"}
    )
    if not provider_complete:
        return result
    if account_gap_count:
        result["variance_state"] = "account_or_gateway_evidence_incomplete"
        return result
    if not gateway_complete:
        result["variance_state"] = "gateway_pricing_incomplete"
        return result

    try:
        grain = ComparableAccountGrain(
            organization_id=source_binding.organization_id,
            provider=source_binding.provider,
            provider_account_fingerprint=source_binding.provider_account_fingerprint,
            fingerprint_key_version=source_binding.fingerprint_key_version,
            source_binding_id=source_binding.binding_id,
            source_binding_version=source_binding.version,
            source_binding_digest=source_binding.content_digest,
            scope_kind=source_binding.scope_kind,
            scope_fingerprints=source_binding.scope_fingerprints,
            period_start_at=preview.period_start_at,
            period_end_at=preview.period_end_at,
            currency=preview.provider_cost.currency,
            product=f"{source_binding.provider}.costs",
            collection_profile=preview.collection_profile,
        )
        binding = BoundGatewayScopeClaim(
            binding_id=str(account_event["binding_id"]),
            binding_version=int(account_event["version"]),
            binding_digest=str(account_event["content_digest"]),
            source_binding_id=source_binding.binding_id,
            source_binding_version=source_binding.version,
            source_binding_digest=source_binding.content_digest,
        )
        variance = calculate_reference_variance(
            provider_grain=grain,
            gateway_grain=grain,
            gateway_binding=binding,
            provider_coverage="complete",
            gateway_coverage="complete",
            provider_rows=tuple(
                ProviderCostRow(
                    snapshot_id=str(row["snapshot_id"]),
                    observation_digest=str(row["observation_digest"]),
                    signed_amount=str(row["canonical_amount"]),
                )
                for row in provider_observations
            ),
            gateway_rows=tuple(
                GatewayEstimateRow(
                    attempt_id=str(event["request_attempt_id"]),
                    price_identity_digest=str(event["configured_rate_card_digest"]),
                    configured_amount=str(event["configured_estimate_amount"]),
                )
                for event in gateway_attempt_events
            ),
        )
    except (
        FinanceVarianceReferenceError,
        KeyError,
        OverflowError,
        RecursionError,
        TypeError,
        UnicodeError,
        ValueError,
    ):
        raise FinanceCollectionError("unavailable") from None
    result.update({
        "provider_total": variance.provider_total,
        "gateway_estimate_known_subtotal": (
            variance.configured_estimate_known_subtotal
        ),
        "signed_variance": variance.signed_variance,
        "absolute_variance": variance.absolute_variance,
        "relative_variance": (
            None
            if variance.relative_variance is None
            else asdict(variance.relative_variance)
        ),
        "variance_state": "comparable_operator_attested",
        "review_status": variance.review_status,
    })
    return result


def _validate_collection_selection(
    binding_id: str, binding_version: int, collection_profile: str,
) -> None:
    if (
        not _safe_id(binding_id)
        or type(binding_version) is not int
        or not isinstance(collection_profile, str)
        or collection_profile not in PROFILE_SPECS
    ):
        raise FinanceCollectionError("invalid_request")


def _select_collection_observations(
    sql: Any,
    *,
    organization_id: str,
    binding_id: str,
    binding_version: int,
    collection_profile: str,
    start_at: str,
    end_at: str,
    as_of_commit_sequence: int | None = None,
    max_observations: int | None = None,
    max_coverage: int | None = None,
) -> tuple[
    tuple[Mapping[str, object], ...],
    tuple[Mapping[str, object], ...],
    tuple[SelectedCollectionSnapshot, ...],
]:
    cutoff_clause = "" if as_of_commit_sequence is None else "AND snapshot.commit_sequence<=? "
    parameters: tuple[object, ...] = (
        organization_id,
        binding_id,
        binding_version,
        collection_profile,
        start_at,
        end_at,
    )
    if as_of_commit_sequence is not None:
        parameters += (as_of_commit_sequence,)
    coverage_rows = sql.execute(
        f"WITH ranked AS ("
        f"SELECT coverage.*, snapshot.commit_sequence, snapshot.content_digest, "
        f"ROW_NUMBER() OVER (PARTITION BY coverage.bucket_start_at, coverage.bucket_end_at "
        f"ORDER BY snapshot.commit_sequence DESC) AS selection_rank "
        f"FROM {COVERAGE_TABLE} coverage JOIN {SNAPSHOT_TABLE} snapshot "
        f"ON snapshot.organization_id=coverage.organization_id "
        f"AND snapshot.snapshot_id=coverage.snapshot_id "
        f"WHERE snapshot.organization_id=? AND snapshot.binding_id=? "
        f"AND snapshot.binding_version=? AND snapshot.collection_profile=? "
        f"AND coverage.bucket_start_at>=? AND coverage.bucket_end_at<=? "
        f"{cutoff_clause}) "
        f"SELECT * FROM ranked WHERE selection_rank=1 "
        f"ORDER BY bucket_start_at,bucket_end_at "
        f"{'LIMIT ?' if max_coverage is not None else ''}",
        parameters + ((max_coverage + 1,) if max_coverage is not None else ()),
    ).fetchall()
    if max_coverage is not None and len(coverage_rows) > max_coverage:
        raise FinanceCollectionError("unavailable")
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
    if max_observations is not None and sum(
        row["observation_count"] for row in coverage
    ) > max_observations:
        raise FinanceCollectionError("unavailable")
    selected_snapshots = tuple(sorted(
        {
            str(row["snapshot_id"]): SelectedCollectionSnapshot(
                str(row["snapshot_id"]),
                str(row["content_digest"]),
                int(row["commit_sequence"]),
            )
            for row in coverage_rows
        }.values(),
        key=lambda selected: (selected.commit_sequence, selected.snapshot_id),
    ))
    table = USAGE_TABLE if PROFILE_SPECS[collection_profile].source_kind == "usage" else COST_TABLE
    observations: list[Mapping[str, object]] = []
    for selected in coverage:
        if selected["coverage_state"] == "no_observation":
            continue
        rows = sql.execute(
            f"SELECT * FROM {table} WHERE organization_id=? "
            "AND snapshot_id=? AND bucket_start_at=? AND bucket_end_at=? "
            "ORDER BY observation_digest "
            f"{'LIMIT ?' if max_observations is not None else ''}",
            (
                organization_id,
                selected["snapshot_id"],
                selected["bucket_start_at"],
                selected["bucket_end_at"],
            ) + ((selected["observation_count"] + 1,) if max_observations is not None else ()),
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
    return coverage, tuple(observations), selected_snapshots


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


def _append_query_audit(
    sql: Any,
    *,
    event: Mapping[str, object],
) -> None:
    try:
        validate_finance_query_audit_event(event)
        event_id = str(event["query_event_id"])
        evidence_json = canonical_json_text(dict(event))
    except (AuditChainError, KeyError, TypeError, ValueError):
        raise FinanceCollectionError("unavailable") from None
    row = {
        key: value
        for key, value in event.items()
        if key not in {"schema_id", "schema_version"}
    }
    row["evidence_json"] = evidence_json
    sql.insert(QUERY_AUDIT_TABLE, row)
    _append_audit(
        sql,
        event=event,
        source=AuditChainSource(QUERY_AUDIT_SCHEMA_ID, 1, event_id),
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
