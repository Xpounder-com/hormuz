"""Domain types and repository contract for governed custody authorization."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

if TYPE_CHECKING:
    from .custody_execution_repository import CustodyExecutionStatus


CUSTODY_ROUTINE_OPERATIONS = frozenset(
    {
        "seal_envelope",
        "rewrap_envelope",
        "verify_restore",
    }
)
CUSTODY_DESTRUCTIVE_OPERATIONS = frozenset(
    {
        "retire_envelope",
        "disable_provider_credential",
        "retire_key_reference",
        "resolve_recovery",
    }
)
CUSTODY_OPERATIONS = CUSTODY_ROUTINE_OPERATIONS | CUSTODY_DESTRUCTIVE_OPERATIONS
CUSTODY_OPERATION_TARGET_KINDS = {
    "seal_envelope": "envelope",
    "rewrap_envelope": "envelope",
    "verify_restore": "restore",
    "retire_envelope": "envelope",
    "disable_provider_credential": "provider_credential",
    "retire_key_reference": "key_reference",
    "resolve_recovery": "recovery",
}


class CustodyControlError(RuntimeError):
    """Stable, content-free custody-control failure."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class CustodyAdministrator:
    """One tenant-qualified root custody authority.

    This identity carries no inference, policy, identity, database, or KMS
    entitlement. It is only an authorization fact consulted by the custody
    control service.
    """

    organization_id: str
    authentication_kind: str
    actor_id: str | None = None
    issuer: str | None = None
    subject: str | None = None

    def __post_init__(self) -> None:
        _stable_identifier(self.organization_id, "organization_id")
        if self.authentication_kind == "static":
            if not self.actor_id or self.issuer is not None or self.subject is not None:
                raise ValueError("Static custody administrators require only actor_id")
            _stable_identifier(self.actor_id, "actor_id")
            return
        if self.authentication_kind == "oidc":
            if not self.issuer or not self.subject or self.actor_id is not None:
                raise ValueError("OIDC custody administrators require only issuer and subject")
            _stable_identifier(self.issuer, "issuer")
            _stable_identifier(self.subject, "subject")
            return
        raise ValueError("Unsupported custody administrator authentication kind")

    def audit_ref(self) -> dict[str, str | None]:
        return {
            "authentication_kind": self.authentication_kind,
            "actor_id": self.actor_id,
            "issuer": self.issuer,
            "subject": self.subject,
        }


@dataclass(frozen=True)
class CustodyApproval:
    approver_kind: str
    approver_identity_key: str
    approved_at: datetime

    def __post_init__(self) -> None:
        if self.approver_kind not in {"static", "oidc"}:
            raise ValueError("Custody approval kind is invalid")
        _opaque_identity_key(self.approver_kind, self.approver_identity_key)
        if self.approved_at.tzinfo is None:
            raise ValueError("Custody approval timestamp must be timezone-aware")


@dataclass(frozen=True)
class CustodyOperationIntent:
    """Content-free, immutable authorization target plus append-only approvals."""

    organization_id: str
    operation_id: str
    operation_type: str
    risk_level: str
    target_kind: str
    target_sha256: str
    parameters_sha256: str
    protected_input_ref_sha256: str | None
    state: str
    required_approvals: int
    approvals: tuple[CustodyApproval, ...]
    created_at: datetime
    expires_at: datetime
    authorized_at: datetime | None
    requested_by_kind: str
    requested_by_identity_key: str

    def __post_init__(self) -> None:
        _stable_identifier(self.organization_id, "organization_id")
        try:
            UUID(self.operation_id)
        except (ValueError, TypeError, AttributeError) as error:
            raise ValueError("Custody operation_id is invalid") from error
        if self.operation_type not in CUSTODY_OPERATIONS:
            raise ValueError("Custody operation_type is invalid")
        expected_risk = "routine" if self.operation_type in CUSTODY_ROUTINE_OPERATIONS else "destructive"
        if self.risk_level != expected_risk:
            raise ValueError("Custody risk_level is invalid")
        if self.target_kind != CUSTODY_OPERATION_TARGET_KINDS[self.operation_type]:
            raise ValueError("Custody target_kind is invalid")
        _sha256(self.target_sha256, "target_sha256")
        _sha256(self.parameters_sha256, "parameters_sha256")
        if self.operation_type == "seal_envelope":
            if self.protected_input_ref_sha256 is None:
                raise ValueError("seal_envelope requires protected_input_ref_sha256")
            _sha256(self.protected_input_ref_sha256, "protected_input_ref_sha256")
        elif self.protected_input_ref_sha256 is not None:
            raise ValueError("Only seal_envelope accepts protected_input_ref_sha256")
        if self.state not in {"pending", "authorized"}:
            raise ValueError("Custody operation state is invalid")
        expected_approvals = 1 if self.risk_level == "routine" else 2
        if self.required_approvals != expected_approvals:
            raise ValueError("Custody required_approvals is invalid")
        if len(self.approvals) > self.required_approvals:
            raise ValueError("Custody approval count is invalid")
        if len({approval.approver_identity_key for approval in self.approvals}) != len(self.approvals):
            raise ValueError("Custody approvals must use distinct administrators")
        if self.created_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("Custody operation timestamps must be timezone-aware")
        if self.expires_at <= self.created_at:
            raise ValueError("Custody operation expiry is invalid")
        if self.state == "authorized":
            if len(self.approvals) != self.required_approvals or self.authorized_at is None:
                raise ValueError("Authorized custody operation is incomplete")
        elif len(self.approvals) >= self.required_approvals or self.authorized_at is not None:
            raise ValueError("Pending custody operation cannot be authorized")
        if self.authorized_at is not None and self.authorized_at.tzinfo is None:
            raise ValueError("Custody authorized_at must be timezone-aware")
        if self.requested_by_kind not in {"static", "oidc"}:
            raise ValueError("Custody requested_by_kind is invalid")
        _opaque_identity_key(self.requested_by_kind, self.requested_by_identity_key)

    def effective_state(self, *, now: datetime | None = None) -> str:
        current = datetime.now(timezone.utc) if now is None else now
        if current >= self.expires_at:
            return "expired"
        return self.state


@dataclass(frozen=True)
class CustodyControlStatus:
    organization_id: str
    initialized: bool
    administrators: tuple[CustodyAdministrator, ...]
    operation_count: int
    operations: tuple[CustodyOperationIntent, ...]
    execution_status: "CustodyExecutionStatus | None" = None


class CustodyControlRepository(Protocol):
    def is_initialized(self, *, organization_id: str) -> bool: ...

    def bootstrap(
        self,
        *,
        organization_id: str,
        caller: CustodyAdministrator,
        administrators: tuple[CustodyAdministrator, ...],
    ) -> tuple[CustodyAdministrator, ...]: ...

    def grant_administrator(
        self,
        *,
        organization_id: str,
        caller: CustodyAdministrator,
        administrator: CustodyAdministrator,
    ) -> CustodyAdministrator: ...

    def revoke_administrator(
        self,
        *,
        organization_id: str,
        caller: CustodyAdministrator,
        administrator: CustodyAdministrator,
    ) -> None: ...

    def request_operation(
        self,
        *,
        organization_id: str,
        caller: CustodyAdministrator,
        operation_type: str,
        target_sha256: str,
        parameters_sha256: str,
        protected_input_ref_sha256: str | None,
        expires_at: datetime,
    ) -> CustodyOperationIntent: ...

    def approve_operation(
        self,
        *,
        organization_id: str,
        caller: CustodyAdministrator,
        operation_id: str,
    ) -> CustodyOperationIntent: ...

    def status(
        self,
        *,
        organization_id: str,
        caller: CustodyAdministrator,
    ) -> CustodyControlStatus: ...


def required_approvals(operation_type: str) -> int:
    if operation_type in CUSTODY_ROUTINE_OPERATIONS:
        return 1
    if operation_type in CUSTODY_DESTRUCTIVE_OPERATIONS:
        return 2
    raise CustodyControlError("custody_operation_type_invalid")


def operation_target_kind(operation_type: str) -> str:
    try:
        return CUSTODY_OPERATION_TARGET_KINDS[operation_type]
    except KeyError as error:
        raise CustodyControlError("custody_operation_type_invalid") from error


def validate_sha256(value: str, field: str) -> str:
    try:
        _sha256(value, field)
    except ValueError as error:
        raise CustodyControlError(f"custody_{field}_invalid") from error
    return value


def _stable_identifier(value: str, field: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 1024
        or "\x00" in value
        or "\n" in value
        or "\r" in value
    ):
        raise ValueError(f"Custody administrator {field} is invalid")


def _sha256(value: str, field: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"Custody {field} is invalid")


def _opaque_identity_key(kind: str, value: str) -> None:
    prefix = f"{kind}:"
    digest = value.removeprefix(prefix)
    if (
        kind not in {"static", "oidc"}
        or not value.startswith(prefix)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise ValueError("Custody identity key is invalid")
