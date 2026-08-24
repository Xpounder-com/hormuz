"""Isolated routine custody-executor service and envelope reference runner.

This module is intentionally not wired into the human-administration CLI. A
separately deployed machine process supplies the executor database credential,
customer key-service credential, and protected-input resolver. Human custody
administrators can authorize an exact request but cannot execute it merely by
holding their administrator credential.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Mapping, Protocol

from .config import GatewayConfig
from .custody import KEY_PURPOSES, CustodyError, EncryptedEnvelope, EnvelopeCipher
from .custody_execution_repository import CustodyExecutionAttempt, CustodyExecutionError, CustodyExecutionRequest
from .custody_runtime import create_data_key_provider, read_envelope_file, write_envelope_file
from .postgres import PostgresStorageError
from .postgres_custody_executor_store import PostgresCustodyExecutorStore


_MAX_PROTECTED_INPUT_BYTES = 16 * 1024 * 1024
_AMBIGUOUS_PROVIDER_CODES = frozenset({"openbao_custody_unavailable", "aws_kms_unavailable"})


class ProtectedInputResolver(Protocol):
    """Executor-only resolver for a non-persistent protected input handle."""

    def resolve(self, *, organization_id: str, reference: str) -> bytes: ...


class RoutineCustodyOperationRunner(Protocol):
    """Execute one already-claimed routine operation without returning secret data."""

    def execute(self, request: CustodyExecutionRequest) -> None: ...


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

    def execute(self, request: CustodyExecutionRequest) -> None:
        if request.operation_type == "seal_envelope":
            self._seal(request)
            return
        if request.operation_type == "rewrap_envelope":
            self._rewrap(request)
            return
        if request.operation_type == "verify_restore":
            self._verify_restore(request)
            return
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

    def _rewrap(self, request: CustodyExecutionRequest) -> None:
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

    def _verify_restore(self, request: CustodyExecutionRequest) -> None:
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


class CustodyExecutorService:
    """Machine-only boundary for claim, side effect, terminal event, and sweep."""

    def __init__(
        self,
        config: GatewayConfig,
        *,
        protected_input_resolver: ProtectedInputResolver,
        environ: Mapping[str, str] | None = None,
        runner: RoutineCustodyOperationRunner | None = None,
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
        )
        self._runner = runner or EnvelopeRoutineExecutor(
            config,
            protected_input_resolver=protected_input_resolver,
            environ=environment,
        )

    def execute(self, *, request: CustodyExecutionRequest) -> CustodyExecutionAttempt:
        if request.organization_id not in self._config.organization_ids:
            raise CustodyExecutionError("custody_execution_organization_not_configured")
        attempt = self._repository.claim(request=request)
        try:
            self._runner.execute(request)
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
        try:
            return self._repository.finalize(
                organization_id=attempt.organization_id,
                execution_id=attempt.execution_id,
                state="succeeded",
            )
        except (CustodyExecutionError, PostgresStorageError):
            # The pending root persists if the terminal write cannot be proven;
            # callers must not replay this work automatically.
            raise CustodyExecutionError("custody_execution_finalization_unavailable") from None

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
