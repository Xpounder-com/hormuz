"""Account-registration persistence kernel for the schema-13/18 successor.

The caller must authenticate before reading the request, then supply an
already locked tenant transaction, a reauthorization callback and a finite
audit-source append callback. It does not write attempt sidecars or infer
accounts for historical attempts.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from typing import Any, Callable, Iterator, Mapping
from uuid import uuid4

from .audit_chain import (
    AuditChainError,
    AuditChainSource,
    canonical_json_bytes,
    canonical_json_text,
)
from .finance_account_binding import (
    FinanceAccountCandidate,
    select_finance_account,
)
from .finance_account_evidence import (
    ACCOUNT_BINDING_SCHEMA_ID,
    validate_finance_account_binding_event,
)
from ._finance_account_binding_schema import REGISTRATION_TABLE, TABLE_DDL
from ._portfolio_sql import portfolio_transaction
from .config import GatewayConfig
from .finance_account_registration import (
    AccountBindingMatchError,
    AccountBindingRegistrationRequest,
    AccountBindingRequestError,
    match_active_account_binding_source,
    parse_account_binding_registration_request,
)
from .finance_collection_repository import (
    FinanceCollectionError,
    SourceBindingVersion,
    _append_audit,
    _binding_from_row,
)
from .portfolio_config import PortfolioPrincipal
from .portfolio_wire import PortfolioError
from .postgres import POSTGRES_SCHEMA_VERSION, PostgresConnectionPool


_SCHEMA_ID = ACCOUNT_BINDING_SCHEMA_ID
_RECEIPT_ID = "hormuz.finance-account-binding-receipt"
_ROW_COLUMNS = (
    "organization_id", "binding_id", "version", "binding_event_id",
    "previous_version", "binding_state", "reason_code",
    "upstream_reference_id", "upstream_reference_version", "transport_profile",
    "inference_credential_reference_id", "inference_credential_reference_version",
    "source_binding_id", "source_binding_version", "source_binding_digest",
    "provider", "provider_account_fingerprint", "scope_kind",
    "scope_fingerprints_json", "fingerprint_key_version", "content_digest",
    "request_digest", "registered_by", "registered_at", "evidence_json",
)
_CONTENT_COLUMNS = tuple(name for name in _ROW_COLUMNS if name not in {
    "content_digest", "request_digest", "binding_event_id", "registered_by",
    "registered_at", "evidence_json",
})
_INTEGER_COLUMNS = frozenset({
    "version", "upstream_reference_version", "inference_credential_reference_version",
    "source_binding_version", "fingerprint_key_version",
})
_TEXT_COLUMNS = frozenset(_ROW_COLUMNS) - _INTEGER_COLUMNS - {
    "previous_version", "evidence_json",
}


class AccountBindingStorageError(ValueError):
    """Fixed, content-free registration result; database details stay private."""

    def __init__(self, code: str) -> None:
        if code not in {"binding_conflict", "forbidden", "unavailable"}:
            raise ValueError("account_binding_storage_error_invalid")
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, repr=False)
class AccountBindingRegistrationReceipt:
    schema_id: str
    schema_version: int
    organization_id: str
    binding_id: str
    version: int
    binding_event_id: str
    content_digest: str
    binding_state: str
    registered_at: str

    def __repr__(self) -> str:
        return "AccountBindingRegistrationReceipt(<metadata>)"


def _canonical(value: object) -> str:
    try:
        return canonical_json_text(value)
    except AuditChainError:
        raise AccountBindingStorageError("unavailable") from None


def _digest(value: object) -> str:
    try:
        return hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    except AuditChainError:
        raise AccountBindingStorageError("unavailable") from None


def _event_from_row(row: Mapping[str, object]) -> dict[str, object]:
    """Reject a corrupted replay/current row instead of returning authority."""
    try:
        if set(row) != set(_ROW_COLUMNS):
            raise ValueError
        if (
            any(type(row[name]) is not int for name in _INTEGER_COLUMNS)
            or any(type(row[name]) is not str for name in _TEXT_COLUMNS)
            or (row["previous_version"] is not None
                and type(row["previous_version"]) is not int)
            or type(row["evidence_json"]) is not str
        ):
            raise ValueError
        event = json.loads(str(row["evidence_json"]))
        if (
            not isinstance(event, dict)
            or set(event) != (set(_ROW_COLUMNS) - {"evidence_json"}) | {
                "schema_id", "schema_version"
            }
            or event.get("schema_id") != _SCHEMA_ID
            or type(event.get("schema_version")) is not int
            or event["schema_version"] != 1
            or _canonical(event) != row["evidence_json"]
            or any(
                type(event[name]) is not type(row[name]) or event[name] != row[name]
                for name in _ROW_COLUMNS if name != "evidence_json"
            )
            or _digest({name: row[name] for name in _CONTENT_COLUMNS}) != row["content_digest"]
        ):
            raise ValueError
        validate_finance_account_binding_event(event)
        return event
    except (AuditChainError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise AccountBindingStorageError("unavailable") from None


def _receipt_from_row(row: Mapping[str, object]) -> AccountBindingRegistrationReceipt:
    event = _event_from_row(row)
    return AccountBindingRegistrationReceipt(
        _RECEIPT_ID,
        1,
        str(event["organization_id"]),
        str(event["binding_id"]),
        int(event["version"]),
        str(event["binding_event_id"]),
        str(event["content_digest"]),
        str(event["binding_state"]),
        str(event["registered_at"]),
    )


def _source_from_row(row: Mapping[str, object] | None) -> SourceBindingVersion:
    if row is None:
        raise AccountBindingStorageError("binding_conflict")
    try:
        return _binding_from_row(row)
    except FinanceCollectionError:
        raise AccountBindingStorageError("unavailable") from None


def _validate_parsed_request(request: AccountBindingRegistrationRequest) -> None:
    """Bind a public request object's fields to the parser's tenant digest."""

    wire = {
        "schema_id": "hormuz.finance-account-binding-request",
        "schema_version": 1,
        "binding_id": request.binding_id,
        "expected_version": request.expected_version,
        "upstream_reference_id": request.upstream_reference_id,
        "upstream_reference_version": request.upstream_reference_version,
        "transport_profile": request.transport_profile,
        "inference_credential_reference_id": request.inference_credential_reference_id,
        "inference_credential_reference_version": request.inference_credential_reference_version,
        "source_binding": {
            "binding_id": request.source_binding_id,
            "version": request.source_binding_version,
            "content_digest": request.source_binding_digest,
        },
        "state": request.state,
        "reason_code": request.reason_code,
    }
    try:
        parsed = parse_account_binding_registration_request(
            canonical_json_bytes(wire), organization_id=request.organization_id,
        )
    except (AccountBindingRequestError, AuditChainError):
        raise AccountBindingStorageError("binding_conflict") from None
    if parsed != request or any(
        type(getattr(parsed, field)) is not type(getattr(request, field))
        for field in parsed.__dataclass_fields__
    ):
        raise AccountBindingStorageError("binding_conflict")


def append_account_registration(
    sql: Any,
    request: AccountBindingRegistrationRequest,
    *,
    selected: FinanceAccountCandidate,
    actor_id: str,
    reauthorize: Callable[[], None],
    append_audit: Callable[[Mapping[str, object]], None],
) -> AccountBindingRegistrationReceipt:
    """Append an active or revoked version under the tenant serialization lock.

    This intentionally has no connection acquisition, CLI or HTTP route.
    `sql` must be a transaction-bound PortfolioSQL over the proposed table;
    `append_audit` must append the matching finite v2 source in that same
    transaction. Historical replay is checked before current-version CAS or
    source currency. Any exception must roll back the caller's transaction.
    """

    if (
        type(request) is not AccountBindingRegistrationRequest
        or not isinstance(selected, FinanceAccountCandidate)
        or not isinstance(actor_id, str)
        or not 1 <= len(actor_id) <= 128
        or not callable(reauthorize)
        or not callable(append_audit)
        or selected.binding.organization_id != request.organization_id
    ):
        raise AccountBindingStorageError("binding_conflict")
    _validate_parsed_request(request)
    reauthorize()
    tenant, binding_id = request.organization_id, request.binding_id
    replay = sql.one(
        f"SELECT * FROM {REGISTRATION_TABLE} "
        "WHERE organization_id=? AND binding_id=? AND request_digest=?",
        (tenant, binding_id, request.request_digest),
    )
    if replay is not None:
        receipt = _receipt_from_row(replay)
        reauthorize()
        return receipt

    latest_row = sql.one(
        f"SELECT * FROM {REGISTRATION_TABLE} "
        "WHERE organization_id=? AND binding_id=? ORDER BY version DESC LIMIT 1",
        (tenant, binding_id),
    )
    latest = None if latest_row is None else _receipt_from_row(latest_row)
    if (
        (latest is None and request.expected_version is not None)
        or (latest is not None and request.expected_version != latest.version)
    ):
        raise AccountBindingStorageError("binding_conflict")

    if request.state == "active":
        exact_row = sql.one(
            "SELECT * FROM portfolio_finance_source_binding_versions "
            "WHERE organization_id=? AND binding_id=? AND version=?",
            (tenant, request.source_binding_id, request.source_binding_version),
        )
        current_row = sql.one(
            "SELECT * FROM portfolio_finance_source_binding_versions "
            "WHERE organization_id=? AND binding_id=? ORDER BY version DESC LIMIT 1",
            (tenant, request.source_binding_id),
        )
        try:
            matched = match_active_account_binding_source(
                request, selected=selected, source=_source_from_row(exact_row),
                current_source=_source_from_row(current_row),
            )
        except AccountBindingMatchError:
            raise AccountBindingStorageError("binding_conflict") from None
        copied = {
            "version": matched.version,
            "upstream_reference_id": matched.upstream_reference_id,
            "upstream_reference_version": matched.upstream_reference_version,
            "transport_profile": matched.transport_profile,
            "inference_credential_reference_id": matched.inference_credential_reference_id,
            "inference_credential_reference_version": matched.inference_credential_reference_version,
            "source_binding_id": matched.source_binding_id,
            "source_binding_version": matched.source_binding_version,
            "source_binding_digest": matched.source_binding_digest,
            "provider": matched.provider,
            "provider_account_fingerprint": matched.provider_account_fingerprint,
            "scope_kind": matched.scope_kind,
            "scope_fingerprints_json": _canonical(list(matched.scope_fingerprints)),
            "fingerprint_key_version": matched.fingerprint_key_version,
        }
    else:
        if latest_row is None or request.state != "revoked":
            raise AccountBindingStorageError("binding_conflict")
        prior = _event_from_row(latest_row)
        identity, binding = selected.identity, selected.binding
        next_version = int(prior["version"]) + 1
        if (
            prior["binding_state"] != "active"
            or next_version > 2_147_483_647
            or binding.binding_id != request.binding_id
            or binding.binding_version != next_version
            or binding.upstream_reference_id != request.upstream_reference_id
            or identity.upstream_reference_id != request.upstream_reference_id
            or identity.upstream_reference_version != request.upstream_reference_version
            or identity.transport_profile != request.transport_profile
            or identity.inference_credential_reference_id
            != request.inference_credential_reference_id
            or identity.inference_credential_reference_version
            != request.inference_credential_reference_version
            or any(
                prior[field] != getattr(request, field)
                for field in (
                    "upstream_reference_id", "upstream_reference_version",
                    "transport_profile", "inference_credential_reference_id",
                    "inference_credential_reference_version", "source_binding_id",
                    "source_binding_version", "source_binding_digest",
                )
            )
        ):
            raise AccountBindingStorageError("binding_conflict")
        copied = {
            field: prior[field]
            for field in (
                "upstream_reference_id", "upstream_reference_version",
                "transport_profile", "inference_credential_reference_id",
                "inference_credential_reference_version", "source_binding_id",
                "source_binding_version", "source_binding_digest", "provider",
                "provider_account_fingerprint", "scope_kind",
                "scope_fingerprints_json", "fingerprint_key_version",
            )
        }
        copied["version"] = next_version

    row: dict[str, object] = {
        "organization_id": tenant,
        "binding_id": binding_id,
        "version": copied.pop("version"),
        "binding_event_id": str(uuid4()),
        "previous_version": request.expected_version,
        "binding_state": request.state,
        "reason_code": request.reason_code,
        **copied,
        "content_digest": "",
        "request_digest": request.request_digest,
        "registered_by": actor_id,
        "registered_at": sql.now(),
    }
    row["content_digest"] = _digest({name: row[name] for name in _CONTENT_COLUMNS})
    event = {"schema_id": _SCHEMA_ID, "schema_version": 1, **row}
    try:
        validate_finance_account_binding_event(event)
    except ValueError:
        raise AccountBindingStorageError("unavailable") from None
    row["evidence_json"] = _canonical(event)
    sql.insert(REGISTRATION_TABLE, row)
    append_audit(dict(event))
    reauthorize()
    return _receipt_from_row(row)


def append_active_account_registration(
    sql: Any,
    request: AccountBindingRegistrationRequest,
    *,
    selected: FinanceAccountCandidate,
    actor_id: str,
    reauthorize: Callable[[], None],
    append_audit: Callable[[Mapping[str, object]], None],
) -> AccountBindingRegistrationReceipt:
    """Compatibility name for the now complete registration transition."""

    return append_account_registration(
        sql,
        request,
        selected=selected,
        actor_id=actor_id,
        reauthorize=reauthorize,
        append_audit=append_audit,
    )


class FinanceAccountRegistrationRepository:
    """Authorize and commit account registrations without provider access."""

    def __init__(
        self,
        config: GatewayConfig,
        *,
        dsn: str,
        connection_pool: PostgresConnectionPool | None = None,
        read_only: bool = False,
    ) -> None:
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
            or not any(
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
            )
        ):
            raise AccountBindingStorageError("forbidden")

    @contextmanager
    def _transaction(
        self,
        principal: PortfolioPrincipal,
    ) -> Iterator[Any]:
        self._authorize(principal)
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
                        raise AccountBindingStorageError("unavailable")
                yield sql
                self._authorize(principal)
        except (PortfolioError, FinanceCollectionError):
            raise AccountBindingStorageError("unavailable") from None

    def _selected_candidate(
        self,
        request: AccountBindingRegistrationRequest,
    ) -> FinanceAccountCandidate:
        matches: list[FinanceAccountCandidate] = []
        for protocol, upstream in self.config.upstreams.items():
            selected = select_finance_account(
                organization_id=request.organization_id,
                protocol=protocol,
                base_url=upstream.base_url,
                identity=upstream.finance_identity,
                bindings=self.config.finance_account_bindings,
            )
            if (
                isinstance(selected, FinanceAccountCandidate)
                and selected.binding.binding_id == request.binding_id
                and selected.binding.binding_version
                == (1 if request.expected_version is None else request.expected_version + 1)
                and selected.identity.upstream_reference_id
                == request.upstream_reference_id
            ):
                matches.append(selected)
        if len(matches) != 1:
            raise AccountBindingStorageError("binding_conflict")
        return matches[0]

    def register(
        self,
        principal: PortfolioPrincipal,
        payload: bytes,
    ) -> AccountBindingRegistrationReceipt:
        """Parse only after authorization and return only after commit."""

        self._authorize(principal)
        request = parse_account_binding_registration_request(
            payload,
            organization_id=principal.organization_id,
        )
        with self._transaction(principal) as sql:
            # Exact canonical replays are durable facts. Resolve them under the
            # tenant lock before consulting configuration that may have moved
            # to a later binding version.
            replay = sql.one(
                f"SELECT * FROM {REGISTRATION_TABLE} "
                "WHERE organization_id=? AND binding_id=? AND request_digest=?",
                (
                    request.organization_id,
                    request.binding_id,
                    request.request_digest,
                ),
            )
            if replay is not None:
                receipt = _receipt_from_row(replay)
                self._authorize(principal)
                return receipt
            selected = self._selected_candidate(request)
            return append_account_registration(
                sql,
                request,
                selected=selected,
                actor_id=principal.actor_id,
                reauthorize=lambda: self._authorize(principal),
                append_audit=lambda event: _append_audit(
                    sql,
                    event=event,
                    source=AuditChainSource(
                        ACCOUNT_BINDING_SCHEMA_ID,
                        1,
                        str(event["binding_event_id"]),
                    ),
                ),
            )


def create_finance_account_registration_repository(
    config: GatewayConfig,
    *,
    environ: Mapping[str, str] | None = None,
    connection_pool: PostgresConnectionPool | None = None,
    read_only: bool = False,
) -> FinanceAccountRegistrationRepository:
    """Construct without migration, provider credentials or external I/O."""

    storage = config.usage_storage
    dsn = ""
    if storage.backend == "postgresql":
        environment = os.environ if environ is None else environ
        dsn = environment.get(storage.postgres_dsn_env, "")
        if not dsn:
            raise AccountBindingStorageError("unavailable")
    elif storage.backend != "sqlite":
        raise AccountBindingStorageError("unavailable")
    return FinanceAccountRegistrationRepository(
        config,
        dsn=dsn,
        connection_pool=connection_pool,
        read_only=read_only,
    )
