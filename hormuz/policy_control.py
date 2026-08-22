"""Governed service boundary for CLI policy administration.

The CLI authenticates a credential and submits a command to this service. It
does not accept an actor flag and it does not manipulate PostgreSQL tables
directly. A future authenticated HTTP or local-socket transport can call the
same service without changing policy semantics.
"""

from __future__ import annotations

import hmac
import os
from pathlib import Path
from typing import Mapping

from .auth import AuthenticationError, Authenticator, ControlPrincipal
from .config import BootstrapAdministrator, GatewayConfig
from .policy_document import PolicyDocument
from .policy_repository import (
    PolicyActivation,
    PolicyAdministrator,
    PolicyControlError,
    PolicyControlStatus,
    PolicyVersionRecord,
)
from .postgres import PostgresStorageError
from .postgres_policy_store import PostgresPolicyControlStore


_MAX_POLICY_DOCUMENT_BYTES = 1024 * 1024
_BREAK_GLASS_REASON_CODES = frozenset({"all_administrators_lost", "administrator_store_recovered"})


class PolicyControlService:
    """Authorize and execute policy-control commands through one narrow API."""

    def __init__(self, config: GatewayConfig, *, environ: Mapping[str, str] | None = None) -> None:
        if config.policy_control.mode != "postgresql":
            raise PolicyControlError("policy_control_postgresql_required")
        environment = os.environ if environ is None else environ
        dsn = environment.get(config.policy_control.postgres_control_dsn_env, "")
        if not dsn:
            raise PostgresStorageError("policy_control_dsn_unavailable")
        self._config = config
        self._environ = environment
        self._authenticator = Authenticator(config)
        self._repository = PostgresPolicyControlStore(
            dsn,
            config=config,
            schema=config.usage_storage.postgres_schema,
            policy_control_role=config.policy_control.postgres_control_role,
        )

    def bootstrap(self, *, organization_id: str, credential_env: str) -> tuple[PolicyAdministrator, ...]:
        # Once initialized, do not inspect bootstrap configuration again. That
        # is the precise boundary that prevents configuration drift from
        # changing everyday root authority.
        if self._repository.is_initialized(organization_id=organization_id):
            raise PolicyControlError("policy_bootstrap_already_initialized")
        self._require_configured_organization(organization_id)
        caller = self._authenticated_administrator(organization_id=organization_id, credential_env=credential_env)
        try:
            configured = tuple(
                _bootstrap_to_administrator(item)
                for item in self._config.policy_control.bootstrap_administrators
                if item.organization_id == organization_id
            )
        except ValueError as error:
            raise PolicyControlError("policy_bootstrap_administrator_invalid") from error
        if not configured or not any(_same_identity(caller, item) for item in configured):
            raise PolicyControlError("policy_bootstrap_credential_not_authorized")
        return self._repository.bootstrap(
            organization_id=organization_id,
            caller=caller,
            administrators=configured,
        )

    def stage(
        self,
        *,
        organization_id: str,
        credential_env: str,
        policy_path: str | Path,
    ) -> PolicyVersionRecord:
        self._require_configured_organization(organization_id)
        caller = self._authenticated_administrator(organization_id=organization_id, credential_env=credential_env)
        document = self._read_document(policy_path)
        return self._repository.stage(
            organization_id=organization_id,
            caller=caller,
            document=document,
        )

    def activate(
        self,
        *,
        organization_id: str,
        credential_env: str,
        version_id: str,
    ) -> PolicyActivation:
        return self._repository.activate(
            organization_id=organization_id,
            caller=self._authenticated_administrator(organization_id=organization_id, credential_env=credential_env),
            version_id=_version_id(version_id),
        )

    def rollback(
        self,
        *,
        organization_id: str,
        credential_env: str,
        version_id: str,
    ) -> PolicyActivation:
        return self._repository.rollback(
            organization_id=organization_id,
            caller=self._authenticated_administrator(organization_id=organization_id, credential_env=credential_env),
            version_id=_version_id(version_id),
        )

    def grant_oidc_administrator(
        self,
        *,
        organization_id: str,
        credential_env: str,
        issuer: str,
        subject: str,
    ) -> PolicyAdministrator:
        administrator = self._oidc_administrator(organization_id=organization_id, issuer=issuer, subject=subject)
        return self._repository.grant_administrator(
            organization_id=organization_id,
            caller=self._authenticated_administrator(organization_id=organization_id, credential_env=credential_env),
            administrator=administrator,
        )

    def revoke_oidc_administrator(
        self,
        *,
        organization_id: str,
        credential_env: str,
        issuer: str,
        subject: str,
    ) -> None:
        administrator = self._oidc_administrator(organization_id=organization_id, issuer=issuer, subject=subject)
        self._repository.revoke_administrator(
            organization_id=organization_id,
            caller=self._authenticated_administrator(organization_id=organization_id, credential_env=credential_env),
            administrator=administrator,
        )

    def revoke_static_administrator(
        self,
        *,
        organization_id: str,
        credential_env: str,
        actor_id: str,
    ) -> None:
        """Retire a persisted bootstrap authority through the governed service.

        Static identities exist only as one-time bootstrap identities; this
        command intentionally does not grant new static authorities. It does
        permit an existing root authority to remove a bootstrap identity
        without falling back to a direct database edit.
        """

        if not actor_id or len(actor_id) > 1024 or "\x00" in actor_id or "\n" in actor_id or "\r" in actor_id:
            raise PolicyControlError("policy_administrator_actor_invalid")
        self._repository.revoke_administrator(
            organization_id=organization_id,
            caller=self._authenticated_administrator(organization_id=organization_id, credential_env=credential_env),
            administrator=PolicyAdministrator(
                organization_id=organization_id,
                authentication_kind="static",
                actor_id=actor_id,
            ),
        )

    def status(self, *, organization_id: str, credential_env: str) -> PolicyControlStatus:
        return self._repository.status(
            organization_id=organization_id,
            caller=self._authenticated_administrator(organization_id=organization_id, credential_env=credential_env),
        )

    def break_glass_recover(
        self,
        *,
        organization_id: str,
        recovery_secret: str,
        issuer: str,
        subject: str,
        reason_code: str,
    ) -> PolicyAdministrator:
        if not self._config.policy_control.break_glass.enabled:
            raise PolicyControlError("policy_break_glass_disabled")
        if reason_code not in _BREAK_GLASS_REASON_CODES:
            raise PolicyControlError("policy_break_glass_reason_invalid")
        configured_env = self._config.policy_control.break_glass.token_env
        configured_secret = self._environ.get(configured_env, "")
        if len(configured_secret) < 24 or len(configured_secret) > 64 * 1024:
            raise PolicyControlError("policy_break_glass_credential_unavailable")
        if (
            not isinstance(recovery_secret, str)
            or len(recovery_secret) > 64 * 1024
            or not recovery_secret
            or not hmac.compare_digest(recovery_secret, configured_secret)
        ):
            raise PolicyControlError("policy_break_glass_credential_invalid")
        return self._repository.break_glass_recover(
            organization_id=organization_id,
            administrator=self._oidc_administrator(
                organization_id=organization_id,
                issuer=issuer,
                subject=subject,
            ),
            reason_code=reason_code,
        )

    def _authenticated_administrator(self, *, organization_id: str, credential_env: str) -> PolicyAdministrator:
        credential_env = _credential_env(credential_env)
        credential = self._environ.get(credential_env, "")
        if not credential:
            raise PolicyControlError("policy_control_credential_unavailable")
        try:
            principal = self._authenticator.authenticate_control(credential)
        except AuthenticationError as error:
            raise PolicyControlError("policy_control_credential_invalid") from error
        return _principal_to_administrator(principal, organization_id=organization_id)

    def _require_configured_organization(self, organization_id: str) -> None:
        if organization_id not in self._config.organization_ids:
            raise PolicyControlError("policy_organization_not_configured")

    def _oidc_administrator(self, *, organization_id: str, issuer: str, subject: str) -> PolicyAdministrator:
        if issuer not in self._config.oidc_issuers:
            raise PolicyControlError("policy_administrator_issuer_untrusted")
        if not subject or len(subject) > 1024 or "\x00" in subject or "\n" in subject or "\r" in subject:
            raise PolicyControlError("policy_administrator_subject_invalid")
        return PolicyAdministrator(
            organization_id=organization_id,
            authentication_kind="oidc",
            issuer=issuer,
            subject=subject,
        )

    def _read_document(self, policy_path: str | Path) -> PolicyDocument:
        path = Path(policy_path).expanduser()
        try:
            with path.open("rb") as source:
                content = source.read(_MAX_POLICY_DOCUMENT_BYTES + 1)
        except OSError as error:
            raise PolicyControlError("policy_document_unavailable") from error
        if len(content) > _MAX_POLICY_DOCUMENT_BYTES:
            raise PolicyControlError("policy_document_too_large")
        return PolicyDocument.from_json_bytes(content, config=self._config)


def _bootstrap_to_administrator(value: BootstrapAdministrator) -> PolicyAdministrator:
    return PolicyAdministrator(
        organization_id=value.organization_id,
        authentication_kind=value.authentication_kind,
        actor_id=value.actor_id,
        issuer=value.issuer,
        subject=value.subject,
    )


def _principal_to_administrator(principal: ControlPrincipal, *, organization_id: str) -> PolicyAdministrator:
    try:
        if principal.authentication_kind == "static":
            if principal.organization_id != organization_id or not principal.actor_id:
                raise PolicyControlError("policy_organization_mismatch")
            return PolicyAdministrator(
                organization_id=organization_id,
                authentication_kind="static",
                actor_id=principal.actor_id,
            )
        if principal.authentication_kind == "oidc" and principal.issuer and principal.subject:
            return PolicyAdministrator(
                organization_id=organization_id,
                authentication_kind="oidc",
                issuer=principal.issuer,
                subject=principal.subject,
            )
    except ValueError as error:
        raise PolicyControlError("policy_control_credential_invalid") from error
    raise PolicyControlError("policy_control_credential_invalid")


def _same_identity(left: PolicyAdministrator, right: PolicyAdministrator) -> bool:
    return (
        left.organization_id == right.organization_id
        and left.authentication_kind == right.authentication_kind
        and left.actor_id == right.actor_id
        and left.issuer == right.issuer
        and left.subject == right.subject
    )


def _credential_env(value: str) -> str:
    if not value or not value.replace("_", "A").isalnum() or value[0].isdigit():
        raise PolicyControlError("policy_control_credential_env_invalid")
    return value


def _version_id(value: str) -> str:
    if not isinstance(value, str) or not value.startswith("sha256:") or len(value) != len("sha256:") + 64:
        raise PolicyControlError("policy_version_invalid")
    digest = value[len("sha256:") :]
    if any(character not in "0123456789abcdef" for character in digest):
        raise PolicyControlError("policy_version_invalid")
    return value
