"""Authenticated service boundary for tenant custody authorization.

This service records human authorization only. It never receives plaintext,
constructs a KMS client, changes customer IAM, or executes an envelope
lifecycle operation. A separately permissioned executor may consume an
authorized intent in a later release gate.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from typing import Mapping

from .auth import AuthenticationError, Authenticator, ControlPrincipal
from .config import BootstrapAdministrator, GatewayConfig
from .custody_repository import (
    CustodyAdministrator,
    CustodyControlError,
    CustodyControlStatus,
    CustodyOperationIntent,
    validate_sha256,
)
from .postgres import PostgresStorageError
from .postgres_custody_store import PostgresCustodyControlStore


class CustodyControlService:
    """Authorize custody administration and content-free operation intents."""

    def __init__(self, config: GatewayConfig, *, environ: Mapping[str, str] | None = None) -> None:
        if config.custody_control.mode != "postgresql":
            raise CustodyControlError("custody_control_postgresql_required")
        environment = os.environ if environ is None else environ
        dsn = environment.get(config.custody_control.postgres_control_dsn_env, "")
        if not dsn:
            raise PostgresStorageError("custody_control_dsn_unavailable")
        self._config = config
        self._environ = environment
        self._authenticator = Authenticator(config)
        self._repository = PostgresCustodyControlStore(
            dsn,
            schema=config.usage_storage.postgres_schema,
            custody_control_role=config.custody_control.postgres_control_role,
        )

    def bootstrap(self, *, organization_id: str, credential_env: str) -> tuple[CustodyAdministrator, ...]:
        if self._repository.is_initialized(organization_id=organization_id):
            raise CustodyControlError("custody_bootstrap_already_initialized")
        self._require_configured_organization(organization_id)
        caller = self._authenticated_administrator(organization_id=organization_id, credential_env=credential_env)
        try:
            configured = tuple(
                _bootstrap_to_administrator(item)
                for item in self._config.custody_control.bootstrap_administrators
                if item.organization_id == organization_id
            )
        except ValueError as error:
            raise CustodyControlError("custody_bootstrap_administrator_invalid") from error
        if not configured or not any(_same_identity(caller, administrator) for administrator in configured):
            raise CustodyControlError("custody_bootstrap_credential_not_authorized")
        retention = self._config.custody_retention
        if retention is None:
            raise CustodyControlError("custody_retention_required")
        return self._repository.bootstrap(
            organization_id=organization_id,
            caller=caller,
            administrators=configured,
            retention_days=retention.retention_days,
            retention_legal_hold=retention.legal_hold,
        )

    def grant_oidc_administrator(
        self,
        *,
        organization_id: str,
        credential_env: str,
        issuer: str,
        subject: str,
    ) -> CustodyAdministrator:
        return self._repository.grant_administrator(
            organization_id=organization_id,
            caller=self._authenticated_administrator(organization_id=organization_id, credential_env=credential_env),
            administrator=self._oidc_administrator(
                organization_id=organization_id,
                issuer=issuer,
                subject=subject,
            ),
        )

    def revoke_oidc_administrator(
        self,
        *,
        organization_id: str,
        credential_env: str,
        issuer: str,
        subject: str,
    ) -> None:
        self._repository.revoke_administrator(
            organization_id=organization_id,
            caller=self._authenticated_administrator(organization_id=organization_id, credential_env=credential_env),
            administrator=self._oidc_administrator(
                organization_id=organization_id,
                issuer=issuer,
                subject=subject,
            ),
        )

    def revoke_static_administrator(
        self,
        *,
        organization_id: str,
        credential_env: str,
        actor_id: str,
    ) -> None:
        try:
            administrator = CustodyAdministrator(
                organization_id=organization_id,
                authentication_kind="static",
                actor_id=actor_id,
            )
        except ValueError as error:
            raise CustodyControlError("custody_administrator_actor_invalid") from error
        self._repository.revoke_administrator(
            organization_id=organization_id,
            caller=self._authenticated_administrator(organization_id=organization_id, credential_env=credential_env),
            administrator=administrator,
        )

    def authorize_operation(
        self,
        *,
        organization_id: str,
        credential_env: str,
        operation_type: str,
        target_sha256: str,
        parameters_sha256: str,
        protected_input_ref_sha256: str | None = None,
    ) -> CustodyOperationIntent:
        validate_sha256(target_sha256, "target_sha256")
        validate_sha256(parameters_sha256, "parameters_sha256")
        if protected_input_ref_sha256 is not None:
            validate_sha256(protected_input_ref_sha256, "protected_input_ref_sha256")
        return self._repository.request_operation(
            organization_id=organization_id,
            caller=self._authenticated_administrator(organization_id=organization_id, credential_env=credential_env),
            operation_type=operation_type,
            target_sha256=target_sha256,
            parameters_sha256=parameters_sha256,
            protected_input_ref_sha256=protected_input_ref_sha256,
            expires_at=datetime.now(timezone.utc)
            + timedelta(seconds=self._config.custody_control.authorization_ttl_seconds),
        )

    def approve_operation(
        self,
        *,
        organization_id: str,
        credential_env: str,
        operation_id: str,
    ) -> CustodyOperationIntent:
        return self._repository.approve_operation(
            organization_id=organization_id,
            caller=self._authenticated_administrator(organization_id=organization_id, credential_env=credential_env),
            operation_id=operation_id,
        )

    def status(self, *, organization_id: str, credential_env: str) -> CustodyControlStatus:
        return self._repository.status(
            organization_id=organization_id,
            caller=self._authenticated_administrator(organization_id=organization_id, credential_env=credential_env),
        )

    def export_evidence(self, *, organization_id: str, credential_env: str) -> dict[str, object]:
        """Export tenant custody evidence through the authenticated control boundary."""

        return self._repository.export_evidence(
            organization_id=organization_id,
            caller=self._authenticated_administrator(organization_id=organization_id, credential_env=credential_env),
        )

    def record_deletion_blocked(
        self,
        *,
        organization_id: str,
        credential_env: str,
        source_schema_id: str,
        source_schema_version: int,
        source_event_id: str,
    ) -> dict[str, object]:
        """Record a governed custody-evidence deletion refusal without deleting."""

        return self._repository.record_deletion_blocked(
            organization_id=organization_id,
            caller=self._authenticated_administrator(organization_id=organization_id, credential_env=credential_env),
            source_schema_id=source_schema_id,
            source_schema_version=source_schema_version,
            source_event_id=source_event_id,
        )

    def _authenticated_administrator(self, *, organization_id: str, credential_env: str) -> CustodyAdministrator:
        credential_env = _credential_env(credential_env)
        credential = self._environ.get(credential_env, "")
        if not credential:
            raise CustodyControlError("custody_control_credential_unavailable")
        try:
            principal = self._authenticator.authenticate_control(credential)
        except AuthenticationError as error:
            raise CustodyControlError("custody_control_credential_invalid") from error
        return _principal_to_administrator(principal, organization_id=organization_id)

    def _require_configured_organization(self, organization_id: str) -> None:
        if organization_id not in self._config.organization_ids:
            raise CustodyControlError("custody_organization_not_configured")

    def _oidc_administrator(self, *, organization_id: str, issuer: str, subject: str) -> CustodyAdministrator:
        if issuer not in self._config.oidc_issuers:
            raise CustodyControlError("custody_administrator_issuer_untrusted")
        try:
            return CustodyAdministrator(
                organization_id=organization_id,
                authentication_kind="oidc",
                issuer=issuer,
                subject=subject,
            )
        except ValueError as error:
            raise CustodyControlError("custody_administrator_subject_invalid") from error


def _bootstrap_to_administrator(value: BootstrapAdministrator) -> CustodyAdministrator:
    return CustodyAdministrator(
        organization_id=value.organization_id,
        authentication_kind=value.authentication_kind,
        actor_id=value.actor_id,
        issuer=value.issuer,
        subject=value.subject,
    )


def _principal_to_administrator(principal: ControlPrincipal, *, organization_id: str) -> CustodyAdministrator:
    try:
        if principal.authentication_kind == "static":
            if principal.organization_id != organization_id or not principal.actor_id:
                raise CustodyControlError("custody_organization_mismatch")
            return CustodyAdministrator(
                organization_id=organization_id,
                authentication_kind="static",
                actor_id=principal.actor_id,
            )
        if principal.authentication_kind == "oidc" and principal.issuer and principal.subject:
            return CustodyAdministrator(
                organization_id=organization_id,
                authentication_kind="oidc",
                issuer=principal.issuer,
                subject=principal.subject,
            )
    except ValueError as error:
        raise CustodyControlError("custody_control_credential_invalid") from error
    raise CustodyControlError("custody_control_credential_invalid")


def _same_identity(left: CustodyAdministrator, right: CustodyAdministrator) -> bool:
    return (
        left.organization_id == right.organization_id
        and left.authentication_kind == right.authentication_kind
        and left.actor_id == right.actor_id
        and left.issuer == right.issuer
        and left.subject == right.subject
    )


def _credential_env(value: str) -> str:
    if not value or not value.replace("_", "A").isalnum() or value[0].isdigit():
        raise CustodyControlError("custody_control_credential_env_invalid")
    return value
