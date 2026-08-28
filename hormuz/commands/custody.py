"""Custody command registration and execution.

The public CLI entry points, argv normalization, and cross-command error
conventions remain in :mod:`hormuz.cli`. This module owns only the custody
command family and intentionally does not introduce a command framework.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..config import GatewayConfig
from ..contracts import CUSTODY_CONTROL_STATUS_SCHEMA_ID, contract_envelope
from ..custody import (
    KEY_PURPOSES,
    AuditAnchorSink,
    CustodyError,
    DataKeyProvider,
    EncryptedEnvelope,
    EnvelopeCipher,
    serialize_envelope,
)
from ..custody_control import CustodyControlService
from ..custody_execution_repository import CustodyExecutionError
from ..custody_executor import CustodyExecutorService
from ..custody_repository import (
    CUSTODY_OPERATIONS,
    CustodyControlError,
    CustodyControlStatus,
    CustodyOperationIntent,
)


@dataclass(frozen=True)
class CustodyCommandDependencies:
    """Narrow compatibility seam supplied by :mod:`hormuz.cli`."""

    custody_control_service: Callable[[GatewayConfig], CustodyControlService]
    custody_executor_service: Callable[[GatewayConfig], CustodyExecutorService]
    create_audit_anchor_sink: Callable[[GatewayConfig], AuditAnchorSink]
    create_data_key_provider: Callable[[GatewayConfig], DataKeyProvider]
    read_envelope_file: Callable[[Path], EncryptedEnvelope]
    write_envelope_file: Callable[..., None]
    required_organization: Callable[[GatewayConfig], str]


def add_custody_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    custody = subparsers.add_parser("custody", help="Operate configured encrypted credential custody")
    custody_subparsers = custody.add_subparsers(dest="custody_command", required=True)
    custody_subparsers.add_parser(
        "verify",
        help="Exercise configured key custody and verify Object Lock readiness without writing an audit object",
    )
    seal = custody_subparsers.add_parser(
        "seal",
        help="Seal a value from an environment variable into an owner-only encrypted envelope",
    )
    seal.add_argument("--purpose", choices=sorted(KEY_PURPOSES), required=True)
    seal.add_argument("--input-env", required=True, help="Environment variable containing the value to seal")
    seal.add_argument("--output", required=True, help="Owner-only encrypted envelope output path")
    seal.add_argument("--force", action="store_true", help="Allow replacing an existing envelope path")
    rewrap = custody_subparsers.add_parser(
        "rewrap",
        help="Move an encrypted data key to the current key for its existing purpose",
    )
    rewrap.add_argument("--input", required=True, help="Existing encrypted envelope path")
    rewrap.add_argument("--output", required=True, help="Owner-only rewrapped envelope output path")
    rewrap.add_argument("--force", action="store_true", help="Allow replacing an existing envelope path")

    custody_bootstrap = custody_subparsers.add_parser(
        "bootstrap",
        help="Persist one-time configuration-seeded custody administrators",
    )
    _custody_control_auth_arguments(custody_bootstrap)

    custody_status = custody_subparsers.add_parser(
        "status",
        help="Show tenant custody authorities and content-free approval intents",
    )
    _custody_control_auth_arguments(custody_status)
    custody_status.add_argument("--json", action="store_true", help="Emit machine-readable metadata-only JSON")

    custody_administrator = custody_subparsers.add_parser(
        "administrator",
        help="Manage governed custody administrators",
    )
    custody_administrator_subparsers = custody_administrator.add_subparsers(
        dest="custody_administrator_command",
        required=True,
    )
    for action in ("grant", "revoke"):
        command = custody_administrator_subparsers.add_parser(
            action,
            help=f"{action.title()} an OIDC custody administrator",
        )
        _custody_control_auth_arguments(command)
        command.add_argument("--issuer", required=True, help="Configured OIDC issuer URL")
        command.add_argument("--subject", required=True, help="Stable OIDC subject")
    custody_retire = custody_administrator_subparsers.add_parser(
        "retire",
        help="Retire a persisted bootstrap authority",
    )
    custody_retire_subparsers = custody_retire.add_subparsers(
        dest="custody_administrator_retire_command",
        required=True,
    )
    custody_retire_static = custody_retire_subparsers.add_parser(
        "static",
        help="Retire a persisted static bootstrap custody administrator",
    )
    _custody_control_auth_arguments(custody_retire_static)
    custody_retire_static.add_argument("--actor-id", required=True, help="Persisted static bootstrap actor ID")

    authorize = custody_subparsers.add_parser(
        "authorize",
        help="Create an exact content-free custody-operation intent and record the first approval",
    )
    _custody_control_auth_arguments(authorize)
    authorize.add_argument("--operation", required=True, choices=sorted(CUSTODY_OPERATIONS))
    authorize.add_argument("--target-sha256", required=True, help="Digest of the exact lifecycle target")
    authorize.add_argument("--parameters-sha256", required=True, help="Digest of the normalized execution plan")
    authorize.add_argument(
        "--protected-input-ref-sha256",
        help="Digest of a protected input handle; required only for initial envelope sealing",
    )

    approve = custody_subparsers.add_parser(
        "approve",
        help="Add the distinct second administrator approval required by a destructive operation",
    )
    _custody_control_auth_arguments(approve)
    approve.add_argument("--operation-id", required=True, help="Immutable custody operation identifier")

    custody_evidence = custody_subparsers.add_parser(
        "evidence",
        help="Export or inspect governed, metadata-only custody evidence",
    )
    custody_evidence_subparsers = custody_evidence.add_subparsers(
        dest="custody_evidence_command",
        required=True,
    )
    custody_evidence_export = custody_evidence_subparsers.add_parser(
        "export",
        help="Write a strict tenant-scoped metadata-only custody evidence export to stdout",
    )
    _custody_control_auth_arguments(custody_evidence_export)
    custody_evidence_deletion = custody_evidence_subparsers.add_parser(
        "deletion",
        help="Inspect deletion constraints without deleting evidence",
    )
    custody_evidence_deletion_subparsers = custody_evidence_deletion.add_subparsers(
        dest="custody_evidence_deletion_command",
        required=True,
    )
    custody_evidence_delete_check = custody_evidence_deletion_subparsers.add_parser(
        "check",
        help="Record why a custody evidence record cannot be deleted; this never deletes data",
    )
    custody_evidence_delete_check.set_defaults(custody_evidence_command="deletion-check")
    _custody_control_auth_arguments(custody_evidence_delete_check)
    custody_evidence_delete_check.add_argument(
        "--source-schema-id",
        required=True,
        choices=[
            "hormuz.custody-control-event",
            "hormuz.custody-execution-attempt",
            "hormuz.custody-execution-event",
            "hormuz.custody-lifecycle-event",
            "hormuz.custody-envelope-attestation",
            "hormuz.custody-deletion-event",
        ],
        help="Strict source schema of the already committed evidence record",
    )
    custody_evidence_delete_check.add_argument(
        "--source-schema-version",
        type=int,
        required=True,
        help="Strict source schema version of the already committed evidence record",
    )
    custody_evidence_delete_check.add_argument(
        "--source-event-id",
        required=True,
        help="Immutable source event identifier",
    )

    custody_executor = custody_subparsers.add_parser("executor", help="Run machine-only custody maintenance")
    custody_executor_subparsers = custody_executor.add_subparsers(
        dest="custody_executor_action",
        required=True,
    )
    custody_executor_register = custody_executor_subparsers.add_parser(
        "register",
        help="Register configured custody resources",
    )
    custody_executor_register_subparsers = custody_executor_register.add_subparsers(
        dest="custody_executor_register_command",
        required=True,
    )
    custody_executor_assets = custody_executor_register_subparsers.add_parser(
        "assets",
        help="Persist configured custody asset generations through the restricted executor boundary",
    )
    custody_executor_assets.set_defaults(custody_executor_command="register-assets")


def _custody_control_auth_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--organization", required=True, help="Tenant organization ID")
    parser.add_argument(
        "--credential-env",
        default="HORMUZ_CUSTODY_ADMIN_TOKEN",
        help="Environment variable holding an authenticated custody-admin credential",
    )


def _custody(
    config: GatewayConfig,
    args: argparse.Namespace,
    dependencies: CustodyCommandDependencies,
) -> int:
    if args.custody_command == "executor":
        return _custody_executor(config, args, dependencies)
    if args.custody_command in {"bootstrap", "status", "administrator", "authorize", "approve", "evidence"}:
        return _custody_control(config, args, dependencies)
    if config.custody_control.mode == "postgresql":
        raise CustodyControlError("custody_governed_executor_required")
    if args.custody_command == "verify":
        return _custody_verify(config, dependencies)
    if args.custody_command == "seal":
        return _custody_seal(config, args, dependencies)
    if args.custody_command == "rewrap":
        return _custody_rewrap(config, args, dependencies)
    raise CustodyError("custody_command_unsupported")


def _custody_executor(
    config: GatewayConfig,
    args: argparse.Namespace,
    dependencies: CustodyCommandDependencies,
) -> int:
    """Run a machine-only custody action through the restricted service boundary.

    This command intentionally has no human actor, approval, plaintext input,
    or provider/KMS operation. It must run where the distinct
    ``custody_executor`` credential is available; ordinary gateway and
    custody-admin environments do not receive that credential.
    """

    if args.custody_executor_command != "register-assets":
        raise CustodyExecutionError("custody_executor_command_unsupported")
    if config.custody_lifecycle is None:
        raise CustodyExecutionError("custody_lifecycle_configuration_required")
    service = dependencies.custody_executor_service(config)
    service.register_asset_catalog()
    print(
        "custody asset catalog registered: "
        f"organizations={len(config.organization_ids)} assets={len(config.custody_lifecycle.assets.assets)}"
    )
    return 0


def _custody_control(
    config: GatewayConfig,
    args: argparse.Namespace,
    dependencies: CustodyCommandDependencies,
) -> int:
    """Run human authorization through the custody-control service only."""

    service = dependencies.custody_control_service(config)
    command = args.custody_command
    if command == "bootstrap":
        administrators = service.bootstrap(
            organization_id=args.organization,
            credential_env=args.credential_env,
        )
        print(f"custody bootstrap initialized: organization={args.organization} administrators={len(administrators)}")
        return 0
    if command == "status":
        _print_custody_status(
            service.status(
                organization_id=args.organization,
                credential_env=args.credential_env,
            ),
            as_json=args.json,
        )
        return 0
    if command == "administrator":
        if args.custody_administrator_command == "grant":
            administrator = service.grant_oidc_administrator(
                organization_id=args.organization,
                credential_env=args.credential_env,
                issuer=args.issuer,
                subject=args.subject,
            )
            print(
                "custody administrator granted: "
                f"organization={administrator.organization_id} issuer={administrator.issuer} "
                f"subject={administrator.subject}"
            )
            return 0
        if args.custody_administrator_command == "revoke":
            service.revoke_oidc_administrator(
                organization_id=args.organization,
                credential_env=args.credential_env,
                issuer=args.issuer,
                subject=args.subject,
            )
            print(
                "custody administrator revoked: "
                f"organization={args.organization} issuer={args.issuer} subject={args.subject}"
            )
            return 0
        if (
            args.custody_administrator_command == "retire"
            and args.custody_administrator_retire_command == "static"
        ):
            service.revoke_static_administrator(
                organization_id=args.organization,
                credential_env=args.credential_env,
                actor_id=args.actor_id,
            )
            print(
                "static custody administrator revoked: "
                f"organization={args.organization} actor_id={args.actor_id}"
            )
            return 0
    if command == "authorize":
        operation = service.authorize_operation(
            organization_id=args.organization,
            credential_env=args.credential_env,
            operation_type=args.operation,
            target_sha256=args.target_sha256,
            parameters_sha256=args.parameters_sha256,
            protected_input_ref_sha256=args.protected_input_ref_sha256,
        )
        _print_custody_operation("custody operation recorded", operation)
        return 0
    if command == "approve":
        operation = service.approve_operation(
            organization_id=args.organization,
            credential_env=args.credential_env,
            operation_id=args.operation_id,
        )
        _print_custody_operation("custody operation approved", operation)
        return 0
    if command == "evidence":
        if args.custody_evidence_command == "export":
            print(
                json.dumps(
                    service.export_evidence(
                        organization_id=args.organization,
                        credential_env=args.credential_env,
                    ),
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
        if args.custody_evidence_command == "deletion-check":
            print(
                json.dumps(
                    service.record_deletion_blocked(
                        organization_id=args.organization,
                        credential_env=args.credential_env,
                        source_schema_id=args.source_schema_id,
                        source_schema_version=args.source_schema_version,
                        source_event_id=args.source_event_id,
                    ),
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
    raise CustodyControlError("custody_control_command_unsupported")


def _print_custody_operation(prefix: str, operation: CustodyOperationIntent) -> None:
    print(
        f"{prefix}: organization={operation.organization_id} operation_id={operation.operation_id} "
        f"operation={operation.operation_type} state={operation.effective_state()} "
        f"approvals={len(operation.approvals)}/{operation.required_approvals}"
    )


def _print_custody_status(status: CustodyControlStatus, *, as_json: bool) -> None:
    execution_status = status.execution_status
    payload = {
        "organization_id": status.organization_id,
        "initialized": status.initialized,
        "administrators": [administrator.audit_ref() for administrator in status.administrators],
        "operation_count": status.operation_count,
        "operations": [
            {
                "operation_id": operation.operation_id,
                "operation_type": operation.operation_type,
                "risk_level": operation.risk_level,
                "target_kind": operation.target_kind,
                "target_sha256": operation.target_sha256,
                "parameters_sha256": operation.parameters_sha256,
                "protected_input_ref_sha256": operation.protected_input_ref_sha256,
                "state": operation.effective_state(),
                "required_approvals": operation.required_approvals,
                "approval_count": len(operation.approvals),
                "created_at": operation.created_at.isoformat(),
                "expires_at": operation.expires_at.isoformat(),
                "authorized_at": operation.authorized_at.isoformat() if operation.authorized_at else None,
                "requested_by_kind": operation.requested_by_kind,
                "requested_by_identity_key": operation.requested_by_identity_key,
                "approvals": [
                    {
                        "approver_kind": approval.approver_kind,
                        "approver_identity_key": approval.approver_identity_key,
                        "approved_at": approval.approved_at.isoformat(),
                    }
                    for approval in operation.approvals
                ],
            }
            for operation in status.operations
        ],
        "execution_attempt_count": execution_status.attempt_count if execution_status is not None else 0,
        "execution_attempts": [
            {
                **attempt.contract_record(),
                "events": [event.contract_record() for event in attempt.events],
            }
            for attempt in (execution_status.attempts if execution_status is not None else ())
        ],
    }
    if as_json:
        print(json.dumps(contract_envelope(CUSTODY_CONTROL_STATUS_SCHEMA_ID, payload), indent=2, sort_keys=True))
        return
    print(f"organization: {status.organization_id}")
    print(f"initialized: {str(status.initialized).lower()}")
    print(f"active custody administrators: {len(status.administrators)}")
    print(f"custody operations: {status.operation_count}")
    print(f"operations shown: {len(status.operations)}")
    print(f"custody execution attempts: {execution_status.attempt_count if execution_status is not None else 0}")


def _custody_verify(
    config: GatewayConfig,
    dependencies: CustodyCommandDependencies,
) -> int:
    """Exercise the configured custody profile without writing an audit object."""

    from ..aws_custody import AWSKMSKeyCustodian, S3ObjectLockAuditAnchorSink, verify_aws_kms_profile
    from ..openbao_custody import OpenBaoTransitDataKeyProvider, verify_openbao_transit_profile
    from ..self_hosted_custody import EncryptedS3ObjectLockAuditAnchorSink

    key_custody = config.key_custody
    anchor_config = config.audit_anchor
    if key_custody is None or anchor_config is None:
        raise CustodyError("custody_profile_unconfigured")
    provider = dependencies.create_data_key_provider(config)
    sink = dependencies.create_audit_anchor_sink(config)
    organization_id = dependencies.required_organization(config)
    if key_custody.backend == "aws-kms":
        if not isinstance(provider, AWSKMSKeyCustodian) or not isinstance(sink, S3ObjectLockAuditAnchorSink):
            raise CustodyError("custody_profile_backend_unsupported")
        statuses = verify_aws_kms_profile(
            provider,
            key_custody.key_references,
            organization_id=organization_id,
        )
        sink.verify_configuration()
        print(
            f"key_custody=aws-kms verified_purposes={len(statuses)} data_key_round_trip=passed "
            "audit_anchor=aws-s3-object-lock object_lock=enabled versioning=enabled",
        )
        return 0
    if key_custody.backend == "openbao-transit":
        if not isinstance(provider, OpenBaoTransitDataKeyProvider) or not isinstance(
            sink, EncryptedS3ObjectLockAuditAnchorSink
        ):
            raise CustodyError("custody_profile_backend_unsupported")
        verified = verify_openbao_transit_profile(
            provider,
            key_custody.key_references,
            organization_id=organization_id,
        )
        sink.verify_configuration()
        print(
            f"key_custody=openbao-transit verified_purposes={verified} data_key_round_trip=passed "
            "audit_anchor=s3-compatible-object-lock payload_encryption=envelope "
            "object_lock=enabled versioning=enabled",
        )
        return 0
    raise CustodyError("custody_profile_backend_unsupported")


def _custody_seal(
    config: GatewayConfig,
    args: argparse.Namespace,
    dependencies: CustodyCommandDependencies,
) -> int:
    source = os.environ.get(args.input_env, "")
    if not source:
        raise CustodyError("custody_input_unavailable")
    if "\x00" in source or "\r" in source or "\n" in source:
        raise CustodyError("custody_input_invalid")
    key_custody = config.key_custody
    if key_custody is None:
        raise CustodyError("key_custody_unconfigured")
    organization_id = dependencies.required_organization(config)
    envelope = EnvelopeCipher(dependencies.create_data_key_provider(config)).seal(
        source.encode("utf-8"),
        organization_id=organization_id,
        purpose=args.purpose,
        key_reference=key_custody.key_reference_for(args.purpose),
    )
    dependencies.write_envelope_file(
        Path(args.output).expanduser().absolute(), envelope, force=args.force
    )
    print(
        f"sealed_envelope={envelope.purpose} sha256={hashlib.sha256(serialize_envelope(envelope)).hexdigest()}",
    )
    return 0


def _custody_rewrap(
    config: GatewayConfig,
    args: argparse.Namespace,
    dependencies: CustodyCommandDependencies,
) -> int:
    key_custody = config.key_custody
    if key_custody is None:
        raise CustodyError("key_custody_unconfigured")
    envelope = dependencies.read_envelope_file(Path(args.input).expanduser().absolute())
    if envelope.organization_id != dependencies.required_organization(config):
        raise CustodyError("encrypted_envelope_organization_invalid")
    rewrapped = EnvelopeCipher(dependencies.create_data_key_provider(config)).rewrap(
        envelope,
        destination_key_reference=key_custody.key_reference_for(envelope.purpose),
    )
    dependencies.write_envelope_file(
        Path(args.output).expanduser().absolute(), rewrapped, force=args.force
    )
    print(
        f"rewrapped_envelope={rewrapped.purpose} sha256={hashlib.sha256(serialize_envelope(rewrapped)).hexdigest()}",
    )
    return 0
