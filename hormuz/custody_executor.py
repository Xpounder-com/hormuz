"""Isolated governed custody-executor service and envelope reference runner.

This module is intentionally not wired into the human-administration CLI. A
separately deployed machine process supplies the executor database credential,
customer key-service credential, and protected-input resolver. Human custody
administrators can authorize an exact request but cannot execute it merely by
holding their administrator credential.
"""

from __future__ import annotations

import os
import stat
import time
from pathlib import Path
from typing import Mapping, Protocol
from uuid import UUID

from .config import GatewayConfig
from .custody import KEY_PURPOSES, CustodyError, EncryptedEnvelope, EnvelopeCipher
from .custody_execution_repository import (
    CustodyExecutionAttempt,
    CustodyExecutionError,
    CustodyExecutionRequest,
    CustodyExecutionResult,
)
from .custody_lifecycle import (
    CUSTODY_COORDINATION_LEASE_SECONDS,
    CustodyAsset,
    CustodyEnvelopeAttestation,
    CustodyLifecycleConfig,
    CustodyLifecycleEffect,
    CustodyLifecycleError,
)
from .custody_runtime import create_data_key_provider, read_envelope_file, write_envelope_file
from .postgres import PostgresStorageError
from .postgres_custody_executor_store import PostgresCustodyExecutorStore


_MAX_PROTECTED_INPUT_BYTES = 16 * 1024 * 1024
_AMBIGUOUS_PROVIDER_CODES = frozenset({"openbao_custody_unavailable", "aws_kms_unavailable"})
_COORDINATION_FINALIZATION_TIMEOUT_SECONDS = CUSTODY_COORDINATION_LEASE_SECONDS + 2
_COORDINATION_RETRY_SECONDS = 0.05


class ProtectedInputResolver(Protocol):
    """Executor-only resolver for a non-persistent protected input handle."""

    def resolve(self, *, organization_id: str, reference: str) -> bytes: ...


class CustodyOperationRunner(Protocol):
    """Execute one claimed operation without returning secret material."""

    def execute(self, request: CustodyExecutionRequest) -> CustodyExecutionResult | None: ...


class CustodyExecutionKnownFailure(RuntimeError):
    """A result known not to require unknown-outcome recovery."""


class CustodyExecutionAmbiguous(RuntimeError):
    """An external effect may have happened; preserve a pending attempt."""


class OwnerOnlyFileProtectedInputResolver:
    """Reference resolver for a secret-owner-provisioned mode-``0600`` file.

    The absolute path remains only in the in-memory execution request. The
    executor reads it after the pending ledger entry commits and never writes
    the path or bytes to a Hormuz ledger, event, log, or command argument.
    """

    def resolve(self, *, organization_id: str, reference: str) -> bytes:
        del organization_id  # The request/authorization tenant match is checked before resolution.
        path = Path(reference)
        if not path.is_absolute():
            raise CustodyExecutionKnownFailure("custody_execution_protected_input_unavailable")
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError:
            raise CustodyExecutionKnownFailure("custody_execution_protected_input_unavailable") from None
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_mode & 0o077
                or metadata.st_size <= 0
                or metadata.st_size > _MAX_PROTECTED_INPUT_BYTES
            ):
                raise CustodyExecutionKnownFailure("custody_execution_protected_input_invalid")
            chunks: list[bytes] = []
            remaining = metadata.st_size
            while remaining:
                chunk = os.read(descriptor, min(remaining, 1024 * 1024))
                if not chunk:
                    raise CustodyExecutionKnownFailure("custody_execution_protected_input_invalid")
                chunks.append(chunk)
                remaining -= len(chunk)
            return b"".join(chunks)
        finally:
            os.close(descriptor)


class EnvelopeRoutineExecutor:
    """Concrete routine runner over the existing vendor-neutral envelope API."""

    def __init__(
        self,
        config: GatewayConfig,
        *,
        protected_input_resolver: ProtectedInputResolver,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        if config.key_custody is None:
            raise CustodyExecutionError("key_custody_unconfigured")
        self._config = config
        self._resolver = protected_input_resolver
        self._cipher = EnvelopeCipher(create_data_key_provider(config, environ=environ))

    def execute(self, request: CustodyExecutionRequest) -> CustodyExecutionResult | None:
        if request.operation_type == "seal_envelope":
            self._seal(request)
            return None
        if request.operation_type == "rewrap_envelope":
            return self._rewrap(request)
        if request.operation_type == "verify_restore":
            return self._verify_restore(request)
        raise CustodyExecutionKnownFailure("custody_execution_operation_unsupported")

    def _seal(self, request: CustodyExecutionRequest) -> None:
        assert request.protected_input_reference is not None
        destination = _owner_only_file_target(request.target)
        purpose = _required_purpose(request.parameters)
        try:
            plaintext = self._resolver.resolve(
                organization_id=request.organization_id,
                reference=request.protected_input_reference,
            )
        except CustodyExecutionKnownFailure:
            raise
        except Exception:
            # A remote secret-manager resolver can have sent a request but
            # lost its response. Preserve pending evidence rather than risk a
            # second retrieval or a silent classification as zero work.
            raise CustodyExecutionAmbiguous("custody_execution_protected_input_ambiguous") from None
        if not isinstance(plaintext, bytes) or not plaintext:
            raise CustodyExecutionKnownFailure("custody_execution_protected_input_invalid")
        try:
            envelope = self._cipher.seal(
                plaintext,
                organization_id=request.organization_id,
                purpose=purpose,
                key_reference=self._config.key_custody.key_reference_for(purpose),
            )
        except CustodyError as error:
            _raise_custody_result(error)
        finally:
            # Python cannot guarantee zeroization of immutable bytes, but this
            # reference never stores, logs, returns, or serializes plaintext.
            plaintext = b""
        _write_envelope(destination, envelope)

    def _rewrap(self, request: CustodyExecutionRequest) -> CustodyExecutionResult | None:
        destination = _owner_only_file_target(request.target)
        source = _source_envelope_path(request.parameters)
        try:
            envelope = read_envelope_file(source)
        except CustodyError as error:
            raise CustodyExecutionKnownFailure("custody_execution_source_unavailable") from error
        if envelope.organization_id != request.organization_id:
            raise CustodyExecutionKnownFailure("custody_execution_envelope_organization_invalid")
        try:
            rewrapped = self._cipher.rewrap(
                envelope,
                destination_key_reference=self._config.key_custody.key_reference_for(envelope.purpose),
            )
        except CustodyError as error:
            _raise_custody_result(error)
        _write_envelope(destination, rewrapped)
        return self._rewrap_attestation(
            organization_id=request.organization_id,
            destination=destination,
            purpose=envelope.purpose,
            source_key_reference=envelope.key_reference,
            destination_key_reference=rewrapped.key_reference,
        )

    def _verify_restore(self, request: CustodyExecutionRequest) -> CustodyExecutionResult | None:
        if request.parameters:
            raise CustodyExecutionKnownFailure("custody_execution_parameters_invalid")
        source = _owner_only_file_target(request.target)
        try:
            envelope = read_envelope_file(source)
        except CustodyError as error:
            raise CustodyExecutionKnownFailure("custody_execution_source_unavailable") from error
        if envelope.organization_id != request.organization_id:
            raise CustodyExecutionKnownFailure("custody_execution_envelope_organization_invalid")
        recovered = b""
        try:
            try:
                recovered = self._cipher.unseal(envelope)
            except CustodyError as error:
                _raise_custody_result(error)
            if not recovered:
                raise CustodyExecutionKnownFailure("custody_execution_restore_invalid")
        finally:
            # The verification runner proves only recoverability and never
            # returns or serializes recovered bytes.
            recovered = b""
        return self._restore_attestation(
            organization_id=request.organization_id,
            source=source,
            purpose=envelope.purpose,
            key_reference=envelope.key_reference,
        )

    def _rewrap_attestation(
        self,
        *,
        organization_id: str,
        destination: Path,
        purpose: str,
        source_key_reference: str,
        destination_key_reference: str,
    ) -> CustodyExecutionResult | None:
        lifecycle = self._config.custody_lifecycle
        if lifecycle is None:
            return None
        envelope = _catalog_envelope_asset(lifecycle, organization_id=organization_id, path=destination)
        source_key = _catalog_key_asset(
            lifecycle,
            organization_id=organization_id,
            purpose=purpose,
            key_reference=source_key_reference,
        )
        destination_key = _catalog_key_asset(
            lifecycle,
            organization_id=organization_id,
            purpose=purpose,
            key_reference=destination_key_reference,
        )
        return CustodyExecutionResult(
            envelope_attestation=CustodyEnvelopeAttestation(
                kind="rewrapped",
                envelope_asset=envelope,
                source_key_asset=source_key,
                destination_key_asset=destination_key,
            )
        )

    def _restore_attestation(
        self,
        *,
        organization_id: str,
        source: Path,
        purpose: str,
        key_reference: str,
    ) -> CustodyExecutionResult | None:
        lifecycle = self._config.custody_lifecycle
        if lifecycle is None:
            return None
        envelope = _catalog_envelope_asset(lifecycle, organization_id=organization_id, path=source)
        destination_key = _catalog_key_asset(
            lifecycle,
            organization_id=organization_id,
            purpose=purpose,
            key_reference=key_reference,
        )
        return CustodyExecutionResult(
            envelope_attestation=CustodyEnvelopeAttestation(
                kind="restore_verified",
                envelope_asset=envelope,
                destination_key_asset=destination_key,
            )
        )


class LifecycleCustodyOperationRunner:
    """Machine executor for logical retirement and recovery-resolution events.

    These operations deliberately make no provider, KMS-administration, or
    filesystem deletion call. The durable lifecycle event and derived runtime
    projection are the only Hormuz effect.
    """

    def __init__(self, config: GatewayConfig) -> None:
        if config.custody_lifecycle is None:
            raise CustodyExecutionError("custody_lifecycle_configuration_required")
        self._lifecycle = config.custody_lifecycle

    def execute(self, request: CustodyExecutionRequest) -> CustodyExecutionResult:
        try:
            if request.operation_type == "disable_provider_credential":
                asset = self._lifecycle.assets.require_descriptor(
                    organization_id=request.organization_id,
                    value=request.target,
                )
                _require_empty_parameters(request.parameters)
                return CustodyExecutionResult(
                    lifecycle_effect=CustodyLifecycleEffect(
                        operation_type=request.operation_type,
                        asset=asset,
                    )
                )
            if request.operation_type == "retire_envelope":
                asset = self._lifecycle.assets.require_descriptor(
                    organization_id=request.organization_id,
                    value=request.target,
                )
                _require_empty_parameters(request.parameters)
                return CustodyExecutionResult(
                    lifecycle_effect=CustodyLifecycleEffect(
                        operation_type=request.operation_type,
                        asset=asset,
                    )
                )
            if request.operation_type == "retire_key_reference":
                asset = self._lifecycle.assets.require_descriptor(
                    organization_id=request.organization_id,
                    value=request.target,
                )
                replacement_descriptor = _required_replacement_asset(request.parameters)
                replacement = self._lifecycle.assets.require_descriptor(
                    organization_id=request.organization_id,
                    value=replacement_descriptor,
                )
                return CustodyExecutionResult(
                    lifecycle_effect=CustodyLifecycleEffect(
                        operation_type=request.operation_type,
                        asset=asset,
                        replacement_asset=replacement,
                    )
                )
            if request.operation_type == "resolve_recovery":
                recovery_execution_id = _recovery_execution_id(request.target)
                resolution_code = _recovery_resolution_code(request.parameters)
                return CustodyExecutionResult(
                    lifecycle_effect=CustodyLifecycleEffect(
                        operation_type=request.operation_type,
                        recovery_execution_id=recovery_execution_id,
                        recovery_resolution_code=resolution_code,
                    )
                )
        except CustodyLifecycleError as error:
            raise CustodyExecutionKnownFailure(error.code) from None
        except ValueError:
            raise CustodyExecutionKnownFailure("custody_lifecycle_execution_request_invalid") from None
        raise CustodyExecutionKnownFailure("custody_execution_operation_unsupported")


class GovernedCustodyOperationRunner:
    """Dispatch routine crypto work and logical lifecycle work by operation."""

    def __init__(
        self,
        config: GatewayConfig,
        *,
        protected_input_resolver: ProtectedInputResolver,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._routine = EnvelopeRoutineExecutor(
            config,
            protected_input_resolver=protected_input_resolver,
            environ=environ,
        )
        self._lifecycle = LifecycleCustodyOperationRunner(config) if config.custody_lifecycle is not None else None

    def execute(self, request: CustodyExecutionRequest) -> CustodyExecutionResult | None:
        if request.operation_type in {"seal_envelope", "rewrap_envelope", "verify_restore"}:
            return self._routine.execute(request)
        if self._lifecycle is None:
            raise CustodyExecutionKnownFailure("custody_lifecycle_configuration_required")
        return self._lifecycle.execute(request)


class CustodyExecutorService:
    """Machine-only boundary for claim, side effect, terminal event, and sweep."""

    def __init__(
        self,
        config: GatewayConfig,
        *,
        protected_input_resolver: ProtectedInputResolver | None = None,
        environ: Mapping[str, str] | None = None,
        runner: CustodyOperationRunner | None = None,
    ) -> None:
        if config.custody_control.mode != "postgresql":
            raise CustodyExecutionError("custody_executor_postgresql_required")
        environment = os.environ if environ is None else environ
        dsn = environment.get(config.custody_executor.postgres_executor_dsn_env, "")
        if not dsn:
            raise PostgresStorageError("custody_executor_dsn_unavailable")
        self._config = config
        self._repository = PostgresCustodyExecutorStore(
            dsn,
            schema=config.usage_storage.postgres_schema,
            custody_executor_role=config.custody_executor.postgres_executor_role,
            pending_attempt_ttl_seconds=config.custody_executor.pending_attempt_ttl_seconds,
            asset_catalog=config.custody_lifecycle.assets if config.custody_lifecycle is not None else None,
        )
        if runner is not None:
            self._runner: CustodyOperationRunner | None = runner
        elif protected_input_resolver is not None:
            self._runner = GovernedCustodyOperationRunner(
                config,
                protected_input_resolver=protected_input_resolver,
                environ=environment,
            )
        else:
            # Catalog registration needs the executor's restricted database
            # identity, but neither protected inputs nor a provider/KMS path.
            # Do not fabricate a resolver merely to make that maintenance
            # operation possible.
            self._runner = None

    def execute(self, *, request: CustodyExecutionRequest) -> CustodyExecutionAttempt:
        if request.organization_id not in self._config.organization_ids:
            raise CustodyExecutionError("custody_execution_organization_not_configured")
        if self._runner is None:
            raise CustodyExecutionError("custody_executor_runner_unconfigured")
        attempt = self._repository.claim(request=request)
        try:
            result = self._runner.execute(request)
        except CustodyExecutionKnownFailure:
            return self._finalize_known_failure(attempt)
        except CustodyExecutionAmbiguous:
            # Deliberately leave the pre-egress evidence pending. A sweeper
            # records ``outcome_unknown`` later; no automatic operation replay
            # is permitted, and a fresh human authorization is required.
            raise CustodyExecutionError("custody_execution_outcome_unknown_pending") from None
        except Exception:
            # Treat an unexpected runner failure as ambiguous. This is more
            # conservative than asserting an external operation did not occur.
            raise CustodyExecutionError("custody_execution_outcome_unknown_pending") from None
        if result is not None and not isinstance(result, CustodyExecutionResult):
            raise CustodyExecutionError("custody_execution_outcome_unknown_pending")
        effect = result.lifecycle_effect if result is not None else None
        if effect is not None and effect.asset is not None:
            try:
                self._repository.prepare_restriction(
                    organization_id=attempt.organization_id,
                    execution_id=attempt.execution_id,
                    result=result,
                )
            except (CustodyExecutionError, PostgresStorageError):
                raise CustodyExecutionError("custody_execution_finalization_unavailable") from None
            return self._finalize_coordinated(attempt=attempt, result=result)
        try:
            return self._repository.finalize(
                organization_id=attempt.organization_id,
                execution_id=attempt.execution_id,
                state="succeeded",
                result=result,
            )
        except (CustodyExecutionError, PostgresStorageError):
            # The pending root persists if the terminal write cannot be proven;
            # callers must not replay this work automatically.
            raise CustodyExecutionError("custody_execution_finalization_unavailable") from None

    def _finalize_coordinated(
        self,
        *,
        attempt: CustodyExecutionAttempt,
        result: CustodyExecutionResult,
    ) -> CustodyExecutionAttempt:
        deadline = time.monotonic() + _COORDINATION_FINALIZATION_TIMEOUT_SECONDS
        while True:
            try:
                return self._repository.finalize(
                    organization_id=attempt.organization_id,
                    execution_id=attempt.execution_id,
                    state="succeeded",
                    result=result,
                )
            except CustodyExecutionError as error:
                if error.code != "custody_execution_coordination_pending":
                    raise CustodyExecutionError("custody_execution_finalization_unavailable") from None
                if time.monotonic() >= deadline:
                    # The durable barrier remains installed and the attempt
                    # remains pending. No provider/KMS request or lifecycle
                    # activation is replayed automatically.
                    raise CustodyExecutionError("custody_execution_coordination_pending") from None
                time.sleep(_COORDINATION_RETRY_SECONDS)
            except PostgresStorageError:
                raise CustodyExecutionError("custody_execution_finalization_unavailable") from None

    def register_asset_catalog(self) -> None:
        """Perform machine-only initial registration for configured asset generations."""

        self._repository.register_asset_catalog(organization_ids=self._config.organization_ids)

    def sweep_stale_pending(self) -> int:
        return self._repository.sweep_stale_pending(organization_ids=self._config.organization_ids)

    def _finalize_known_failure(self, attempt: CustodyExecutionAttempt) -> CustodyExecutionAttempt:
        try:
            return self._repository.finalize(
                organization_id=attempt.organization_id,
                execution_id=attempt.execution_id,
                state="failed",
                reason_code="execution_failed",
            )
        except (CustodyExecutionError, PostgresStorageError):
            raise CustodyExecutionError("custody_execution_finalization_unavailable") from None


def _catalog_envelope_asset(
    lifecycle: CustodyLifecycleConfig,
    *,
    organization_id: str,
    path: Path,
) -> CustodyAsset:
    matches = tuple(
        asset
        for asset in lifecycle.assets.assets_for(organization_id=organization_id, asset_type="envelope")
        if asset.binding.get("path") == str(path)
    )
    if len(matches) != 1:
        raise CustodyExecutionKnownFailure("custody_lifecycle_envelope_asset_not_configured")
    return matches[0]


def _catalog_key_asset(
    lifecycle: CustodyLifecycleConfig,
    *,
    organization_id: str,
    purpose: str,
    key_reference: str,
) -> CustodyAsset:
    matches = tuple(
        asset
        for asset in lifecycle.assets.assets_for(organization_id=organization_id, asset_type="key_reference")
        if asset.binding.get("purpose") == purpose and asset.binding.get("key_reference") == key_reference
    )
    if len(matches) != 1:
        raise CustodyExecutionKnownFailure("custody_lifecycle_key_asset_not_configured")
    return matches[0]


def _require_empty_parameters(value: Mapping[str, object]) -> None:
    if value:
        raise CustodyExecutionKnownFailure("custody_lifecycle_execution_request_invalid")


def _required_replacement_asset(value: Mapping[str, object]) -> Mapping[str, object]:
    if set(value) != {"replacement_asset"} or not isinstance(value.get("replacement_asset"), Mapping):
        raise CustodyExecutionKnownFailure("custody_lifecycle_execution_request_invalid")
    return value["replacement_asset"]


def _recovery_execution_id(value: Mapping[str, object]) -> str:
    if set(value) != {"recovery_execution_id"} or not isinstance(value.get("recovery_execution_id"), str):
        raise CustodyExecutionKnownFailure("custody_lifecycle_execution_request_invalid")
    candidate = value["recovery_execution_id"]
    try:
        UUID(candidate)
    except (ValueError, TypeError, AttributeError):
        raise CustodyExecutionKnownFailure("custody_lifecycle_execution_request_invalid") from None
    return candidate


def _recovery_resolution_code(value: Mapping[str, object]) -> str:
    if set(value) != {"resolution_code"} or not isinstance(value.get("resolution_code"), str):
        raise CustodyExecutionKnownFailure("custody_lifecycle_execution_request_invalid")
    candidate = value["resolution_code"]
    if candidate not in {
        "confirmed_applied",
        "confirmed_not_applied",
        "compensating_action_completed",
    }:
        raise CustodyExecutionKnownFailure("custody_lifecycle_execution_request_invalid")
    return candidate


def _owner_only_file_target(value: Mapping[str, object]) -> Path:
    if set(value) != {"kind", "path"} or value.get("kind") != "owner_only_file":
        raise CustodyExecutionKnownFailure("custody_execution_target_invalid")
    path = value.get("path")
    if not isinstance(path, str) or not path or "\x00" in path:
        raise CustodyExecutionKnownFailure("custody_execution_target_invalid")
    selected = Path(path)
    if not selected.is_absolute():
        raise CustodyExecutionKnownFailure("custody_execution_target_invalid")
    return selected


def _required_purpose(value: Mapping[str, object]) -> str:
    if set(value) != {"purpose"}:
        raise CustodyExecutionKnownFailure("custody_execution_parameters_invalid")
    purpose = value.get("purpose")
    if not isinstance(purpose, str) or purpose not in KEY_PURPOSES:
        raise CustodyExecutionKnownFailure("custody_execution_parameters_invalid")
    return purpose


def _source_envelope_path(value: Mapping[str, object]) -> Path:
    if set(value) != {"source_envelope_path"}:
        raise CustodyExecutionKnownFailure("custody_execution_parameters_invalid")
    path = value.get("source_envelope_path")
    if not isinstance(path, str) or not path or "\x00" in path:
        raise CustodyExecutionKnownFailure("custody_execution_parameters_invalid")
    selected = Path(path)
    if not selected.is_absolute():
        raise CustodyExecutionKnownFailure("custody_execution_parameters_invalid")
    return selected


def _write_envelope(path: Path, envelope: EncryptedEnvelope) -> None:
    try:
        # Every governed attempt writes a new immutable destination. Replacing
        # a prior envelope is a later explicit destructive-lifecycle decision.
        write_envelope_file(path, envelope, force=False)
    except CustodyError:
        # A filesystem failure after a KMS call can be ambiguous. Preserve the
        # pending attempt instead of retrying or declaring the operation failed.
        raise CustodyExecutionAmbiguous("custody_execution_destination_ambiguous") from None


def _raise_custody_result(error: CustodyError) -> None:
    if error.code in _AMBIGUOUS_PROVIDER_CODES:
        raise CustodyExecutionAmbiguous("custody_execution_provider_ambiguous") from error
    raise CustodyExecutionKnownFailure("custody_execution_operation_failed") from error
