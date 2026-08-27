"""Types and repository contract for governed policy administration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ._contract_schemas.constants import POLICY_HISTORY_DEFAULT_LIMIT, POLICY_HISTORY_MAX_LIMIT
from .policy_document import PolicyDocument


class PolicyControlError(RuntimeError):
    """Stable, content-free control-plane failure."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PolicyAdministrator:
    organization_id: str
    authentication_kind: str
    actor_id: str | None = None
    issuer: str | None = None
    subject: str | None = None

    def __post_init__(self) -> None:
        _stable_identifier(self.organization_id, "organization_id")
        if self.authentication_kind == "static":
            if not self.actor_id or self.issuer is not None or self.subject is not None:
                raise ValueError("Static policy administrators require only actor_id")
            _stable_identifier(self.actor_id, "actor_id")
            return
        if self.authentication_kind == "oidc":
            if not self.issuer or not self.subject or self.actor_id is not None:
                raise ValueError("OIDC policy administrators require only issuer and subject")
            _stable_identifier(self.issuer, "issuer")
            _stable_identifier(self.subject, "subject")
            return
        raise ValueError("Unsupported policy administrator authentication kind")

    def audit_ref(self) -> dict[str, str | None]:
        """Stable identity metadata, intentionally excluding names and groups."""

        return {
            "authentication_kind": self.authentication_kind,
            "actor_id": self.actor_id,
            "issuer": self.issuer,
            "subject": self.subject,
        }


@dataclass(frozen=True)
class PolicyVersionRecord:
    organization_id: str
    version_id: str
    content_sha256: str
    created_at: datetime
    author_kind: str
    author_identity_key: str
    change_summary: dict[str, object]
    document: PolicyDocument


@dataclass(frozen=True)
class PolicyLifecycleEvent:
    organization_id: str
    event_type: str
    version_id: str
    content_sha256: str
    occurred_at: datetime
    actor_kind: str
    actor_identity_key: str
    generation: int | None
    change_summary: dict[str, object]


@dataclass(frozen=True)
class PolicyHistory:
    organization_id: str
    limit: int
    has_more: bool
    events: tuple[PolicyLifecycleEvent, ...]


@dataclass(frozen=True)
class PolicyActivation:
    organization_id: str
    version_id: str
    generation: int
    activated_at: datetime
    activated_by_kind: str
    activated_by_identity_key: str
    action: str


@dataclass(frozen=True)
class PolicyControlStatus:
    organization_id: str
    initialized: bool
    active: PolicyActivation | None
    versions: tuple[PolicyVersionRecord, ...]
    administrators: tuple[PolicyAdministrator, ...]


def _stable_identifier(value: str, field: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 1024
        or "\x00" in value
        or "\n" in value
        or "\r" in value
    ):
        raise ValueError(f"Policy administrator {field} is invalid")


class PolicyRuntimeRepository(Protocol):
    """Gateway read path: active immutable document only."""

    def active_version(self, *, organization_id: str) -> PolicyVersionRecord: ...


class PolicyControlRepository(PolicyRuntimeRepository, Protocol):
    """Policy-service transaction contract; the CLI never uses SQL directly."""

    def is_initialized(self, *, organization_id: str) -> bool: ...

    def bootstrap(
        self,
        *,
        organization_id: str,
        caller: PolicyAdministrator,
        administrators: tuple[PolicyAdministrator, ...],
    ) -> tuple[PolicyAdministrator, ...]: ...

    def stage(
        self,
        *,
        organization_id: str,
        caller: PolicyAdministrator,
        document: PolicyDocument,
    ) -> PolicyVersionRecord: ...

    def apply(
        self,
        *,
        organization_id: str,
        caller: PolicyAdministrator,
        document: PolicyDocument,
        expected_active_version_id: str | None = None,
    ) -> PolicyActivation: ...

    def activate(
        self,
        *,
        organization_id: str,
        caller: PolicyAdministrator,
        version_id: str,
        expected_active_version_id: str | None = None,
    ) -> PolicyActivation: ...

    def rollback(
        self,
        *,
        organization_id: str,
        caller: PolicyAdministrator,
        version_id: str | None = None,
        expected_active_version_id: str | None = None,
    ) -> PolicyActivation: ...

    def grant_administrator(
        self,
        *,
        organization_id: str,
        caller: PolicyAdministrator,
        administrator: PolicyAdministrator,
    ) -> PolicyAdministrator: ...

    def revoke_administrator(
        self,
        *,
        organization_id: str,
        caller: PolicyAdministrator,
        administrator: PolicyAdministrator,
    ) -> None: ...

    def break_glass_recover(
        self,
        *,
        organization_id: str,
        administrator: PolicyAdministrator,
        reason_code: str,
    ) -> PolicyAdministrator: ...

    def status(self, *, organization_id: str, caller: PolicyAdministrator) -> PolicyControlStatus: ...

    def policy_version(
        self,
        *,
        organization_id: str,
        caller: PolicyAdministrator,
        version_id: str | None,
    ) -> PolicyVersionRecord: ...

    def history(
        self,
        *,
        organization_id: str,
        caller: PolicyAdministrator,
        limit: int,
    ) -> PolicyHistory: ...
