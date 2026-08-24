"""PostgreSQL lifecycle evidence and cached runtime-projection persistence.

The executor can append one immutable lifecycle event. PostgreSQL triggers
then derive the chain head and runtime projection in the same transaction.
The normal gateway role has only tenant-scoped ``SELECT`` access to the
projection and never writes lifecycle authority or projection state.
"""

from __future__ import annotations

import hmac
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Iterator
from uuid import uuid4

from .contracts import ContractValidationError, validate_custody_lifecycle_event
from .custody_execution_repository import CustodyExecutionAttempt, execution_descriptor_sha256
from .custody_lifecycle import (
    CUSTODY_LIFECYCLE_CHAIN_VERSION,
    CustodyAsset,
    CustodyAssetCatalog,
    CustodyEnvelopeAttestation,
    CustodyLifecycleEffect,
    CustodyLifecycleError,
    CustodyLifecycleEvent,
    CustodyProjectionBarrier,
    CustodyProjectionCoordinationSnapshot,
    CustodyProjectionSnapshot,
    build_custody_lifecycle_event,
)
from .postgres import (
    PostgresConnectionPool,
    PostgresStorageError,
    _driver,
    _storage_error,
    postgres_transaction,
    verify_postgres_schema,
)


def register_custody_asset_catalog(
    cursor: Any,
    *,
    organization_id: str,
    catalog: CustodyAssetCatalog,
    registered_at: datetime,
) -> None:
    """Remember immutable asset identities without persisting their bindings."""

    for asset in sorted(catalog.assets, key=lambda item: (item.asset_type == "envelope", item.key)):
        if asset.organization_id != organization_id:
            continue
        envelope_key_asset_id, envelope_key_generation, envelope_key_binding_fingerprint = _envelope_key_binding(
            asset=asset,
            catalog=catalog,
        )
        cursor.execute(
            """
            INSERT INTO custody_lifecycle_asset_identities (
                organization_id, asset_type, asset_id, generation, binding_fingerprint,
                envelope_key_asset_id, envelope_key_generation, envelope_key_binding_fingerprint,
                registered_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (organization_id, asset_type, asset_id, generation) DO NOTHING
            """,
            (
                asset.organization_id,
                asset.asset_type,
                asset.asset_id,
                asset.generation,
                asset.binding_fingerprint,
                envelope_key_asset_id,
                envelope_key_generation,
                envelope_key_binding_fingerprint,
                registered_at,
            ),
        )
        cursor.execute(
            """
            SELECT binding_fingerprint, envelope_key_asset_id, envelope_key_generation,
                   envelope_key_binding_fingerprint
            FROM custody_lifecycle_asset_identities
            WHERE organization_id = %s AND asset_type = %s AND asset_id = %s AND generation = %s
            """,
            (asset.organization_id, asset.asset_type, asset.asset_id, asset.generation),
        )
        row = cursor.fetchone()
        fingerprint = row.get("binding_fingerprint") if row is not None else None
        if (
            not isinstance(fingerprint, str)
            or not hmac.compare_digest(fingerprint, asset.binding_fingerprint)
            or _nullable_text(row, "envelope_key_asset_id") != envelope_key_asset_id
            or _nullable_integer(row, "envelope_key_generation") != envelope_key_generation
            or _nullable_text(row, "envelope_key_binding_fingerprint") != envelope_key_binding_fingerprint
        ):
            raise CustodyLifecycleError("custody_lifecycle_asset_identity_reused")


def verify_custody_asset_catalog(
    cursor: Any,
    *,
    organization_id: str,
    catalog: CustodyAssetCatalog,
) -> None:
    """Fail closed unless each active configured asset has its immutable identity.

    Historical generations may remain in the registry after they leave a
    runtime configuration. The gateway verifies only the catalog it is about
    to select, and has read-only access to fingerprints rather than bindings.
    """

    cursor.execute(
        """
        SELECT asset_type, asset_id, generation, binding_fingerprint,
               envelope_key_asset_id, envelope_key_generation, envelope_key_binding_fingerprint
        FROM custody_lifecycle_asset_identities
        WHERE organization_id = %s
        """,
        (organization_id,),
    )
    observed: dict[tuple[str, str, int], tuple[str, str | None, int | None, str | None]] = {}
    for row in cursor.fetchall():
        try:
            observed[(str(row["asset_type"]), str(row["asset_id"]), int(row["generation"]))] = (
                str(row["binding_fingerprint"]),
                _nullable_text(row, "envelope_key_asset_id"),
                _nullable_integer(row, "envelope_key_generation"),
                _nullable_text(row, "envelope_key_binding_fingerprint"),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise PostgresStorageError("custody_lifecycle_asset_identity_invalid") from error
    for asset in catalog.assets:
        if asset.organization_id != organization_id:
            continue
        registered = observed.get((asset.asset_type, asset.asset_id, asset.generation))
        if registered is None:
            raise CustodyLifecycleError("custody_lifecycle_asset_identity_unregistered")
        envelope_key_asset_id, envelope_key_generation, envelope_key_binding_fingerprint = _envelope_key_binding(
            asset=asset,
            catalog=catalog,
        )
        if (
            not hmac.compare_digest(registered[0], asset.binding_fingerprint)
            or registered[1] != envelope_key_asset_id
            or registered[2] != envelope_key_generation
            or registered[3] != envelope_key_binding_fingerprint
        ):
            raise CustodyLifecycleError("custody_lifecycle_asset_identity_reused")


def _envelope_key_binding(
    *,
    asset: CustodyAsset,
    catalog: CustodyAssetCatalog,
) -> tuple[str | None, int | None, str | None]:
    """Return only an envelope's linked key identity, never its key reference."""

    if asset.asset_type != "envelope":
        return None, None, None
    linked = asset.binding.get("key_reference_asset")
    if not isinstance(linked, str):
        raise CustodyLifecycleError("custody_lifecycle_asset_identity_invalid")
    try:
        asset_id, generation_text = linked.rsplit("@", 1)
        key_asset = catalog.asset(
            organization_id=asset.organization_id,
            asset_type="key_reference",
            asset_id=asset_id,
            generation=int(generation_text),
        )
    except (CustodyLifecycleError, TypeError, ValueError):
        raise CustodyLifecycleError("custody_lifecycle_asset_identity_invalid") from None
    return key_asset.asset_id, key_asset.generation, key_asset.binding_fingerprint


def _nullable_text(row: Any, key: str) -> str | None:
    value = row.get(key) if row is not None else None
    return value if isinstance(value, str) else None


def _nullable_integer(row: Any, key: str) -> int | None:
    value = row.get(key) if row is not None else None
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def append_custody_lifecycle_event(
    cursor: Any,
    *,
    attempt: CustodyExecutionAttempt,
    effect: CustodyLifecycleEffect,
    occurred_at: datetime,
) -> CustodyLifecycleEvent:
    """Append an exact effect; the database derives the projection atomically."""

    if effect.asset is not None and attempt.organization_id != effect.asset.organization_id:
        raise CustodyLifecycleError("custody_lifecycle_tenant_mismatch")
    expected_target, expected_parameters = lifecycle_effect_descriptors(effect)
    if (
        not hmac.compare_digest(attempt.target_sha256, execution_descriptor_sha256(expected_target))
        or not hmac.compare_digest(attempt.parameters_sha256, execution_descriptor_sha256(expected_parameters))
    ):
        raise CustodyLifecycleError("custody_lifecycle_authorization_mismatch")
    cursor.execute(
        """
        SELECT next_sequence, previous_digest
        FROM custody_lifecycle_next_chain_head(%s)
        """,
        (attempt.organization_id,),
    )
    head = cursor.fetchone()
    if head is None:
        raise PostgresStorageError("custody_lifecycle_chain_head_invalid")
    try:
        sequence = int(head["next_sequence"])
        previous_digest = head["previous_digest"]
    except (KeyError, TypeError, ValueError) as error:
        raise PostgresStorageError("custody_lifecycle_chain_head_invalid") from error
    if sequence < 1 or (previous_digest is not None and not isinstance(previous_digest, str)):
        raise PostgresStorageError("custody_lifecycle_chain_head_invalid")
    lifecycle_event_id = str(uuid4())
    event = build_custody_lifecycle_event(
        organization_id=attempt.organization_id,
        lifecycle_event_id=lifecycle_event_id,
        execution_id=attempt.execution_id,
        operation_id=attempt.operation_id,
        occurred_at=occurred_at,
        effect=effect,
        target_sha256=attempt.target_sha256,
        parameters_sha256=attempt.parameters_sha256,
        chain_version=CUSTODY_LIFECYCLE_CHAIN_VERSION,
        sequence=sequence,
        previous_digest=previous_digest,
    )
    try:
        validate_custody_lifecycle_event(event.contract_record())
    except ContractValidationError as error:
        raise CustodyLifecycleError("custody_lifecycle_evidence_invalid") from error
    record = event.contract_record()
    cursor.execute(
        """
        INSERT INTO custody_lifecycle_events (
            organization_id, lifecycle_event_id, lifecycle_schema_id, lifecycle_schema_version,
            execution_id, operation_id, occurred_at, operation_type, target_sha256, parameters_sha256,
            asset_type, asset_id, asset_generation, asset_binding_fingerprint,
            replacement_asset_type, replacement_asset_id, replacement_asset_generation,
            replacement_asset_binding_fingerprint, recovery_execution_id, recovery_resolution_code,
            chain_version, sequence, previous_digest, event_digest
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s
        )
        """,
        (
            record["organization_id"],
            record["lifecycle_event_id"],
            record["lifecycle_schema_id"],
            record["lifecycle_schema_version"],
            record["execution_id"],
            record["operation_id"],
            event.occurred_at,
            record["operation_type"],
            record["target_sha256"],
            record["parameters_sha256"],
            record["asset_type"],
            record["asset_id"],
            record["asset_generation"],
            record["asset_binding_fingerprint"],
            record["replacement_asset_type"],
            record["replacement_asset_id"],
            record["replacement_asset_generation"],
            record["replacement_asset_binding_fingerprint"],
            record["recovery_execution_id"],
            record["recovery_resolution_code"],
            record["chain_version"],
            record["sequence"],
            record["previous_digest"],
            record["event_digest"],
        ),
    )
    if cursor.rowcount != 1:
        raise PostgresStorageError("custody_lifecycle_event_unavailable")
    return event


def prepare_custody_runtime_barrier(
    cursor: Any,
    *,
    attempt: CustodyExecutionAttempt,
    effect: CustodyLifecycleEffect,
    prepared_at: datetime,
) -> CustodyProjectionBarrier:
    """Durably prepare one exact restrictive projection version."""

    if effect.asset is None or effect.operation_type == "resolve_recovery":
        raise CustodyLifecycleError("custody_lifecycle_restriction_required")
    if attempt.organization_id != effect.asset.organization_id:
        raise CustodyLifecycleError("custody_lifecycle_tenant_mismatch")
    expected_target, expected_parameters = lifecycle_effect_descriptors(effect)
    if (
        not hmac.compare_digest(attempt.target_sha256, execution_descriptor_sha256(expected_target))
        or not hmac.compare_digest(attempt.parameters_sha256, execution_descriptor_sha256(expected_parameters))
    ):
        raise CustodyLifecycleError("custody_lifecycle_authorization_mismatch")
    restriction_kind = {
        "disable_provider_credential": "provider_credential_disabled",
        "retire_envelope": "envelope_retired",
        "retire_key_reference": "key_reference_write_retired",
    }.get(effect.operation_type)
    if restriction_kind is None:
        raise CustodyLifecycleError("custody_lifecycle_restriction_required")
    cursor.execute(
        """
        SELECT version
        FROM custody_runtime_projection_heads
        WHERE organization_id = %s
        """,
        (attempt.organization_id,),
    )
    head = cursor.fetchone()
    try:
        proposed_version = (int(head["version"]) if head is not None else 0) + 1
    except (KeyError, TypeError, ValueError) as error:
        raise PostgresStorageError("custody_runtime_projection_invalid") from error
    barrier = CustodyProjectionBarrier(
        organization_id=attempt.organization_id,
        barrier_id=str(uuid4()),
        execution_id=attempt.execution_id,
        proposed_version=proposed_version,
        asset_type=effect.asset.asset_type,
        asset_id=effect.asset.asset_id,
        generation=effect.asset.generation,
        binding_fingerprint=effect.asset.binding_fingerprint,
        restriction_kind=restriction_kind,
        prepared_at=prepared_at,
    )
    replacement = effect.replacement_asset
    cursor.execute(
        """
        INSERT INTO custody_runtime_projection_barriers (
            organization_id, barrier_id, execution_id, proposed_version,
            operation_type, target_sha256, parameters_sha256,
            asset_type, asset_id, asset_generation, asset_binding_fingerprint,
            restriction_kind, replacement_asset_type, replacement_asset_id,
            replacement_asset_generation, replacement_asset_binding_fingerprint,
            prepared_at
        ) VALUES (
            %s, %s::uuid, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s
        )
        """,
        (
            barrier.organization_id,
            barrier.barrier_id,
            barrier.execution_id,
            barrier.proposed_version,
            effect.operation_type,
            attempt.target_sha256,
            attempt.parameters_sha256,
            barrier.asset_type,
            barrier.asset_id,
            barrier.generation,
            barrier.binding_fingerprint,
            barrier.restriction_kind,
            replacement.asset_type if replacement is not None else None,
            replacement.asset_id if replacement is not None else None,
            replacement.generation if replacement is not None else None,
            replacement.binding_fingerprint if replacement is not None else None,
            prepared_at,
        ),
    )
    if cursor.rowcount != 1:
        raise PostgresStorageError("custody_runtime_projection_barrier_unavailable")
    return barrier


def record_custody_envelope_attestation(
    cursor: Any,
    *,
    attempt: CustodyExecutionAttempt,
    attestation: CustodyEnvelopeAttestation,
    occurred_at: datetime,
) -> None:
    """Persist a successful routine proof used by later key retirement."""

    if attestation.kind == "rewrapped" and attempt.operation_type != "rewrap_envelope":
        raise CustodyLifecycleError("custody_lifecycle_attestation_operation_invalid")
    if attestation.kind == "restore_verified" and attempt.operation_type != "verify_restore":
        raise CustodyLifecycleError("custody_lifecycle_attestation_operation_invalid")
    if (
        attestation.envelope_asset.organization_id != attempt.organization_id
        or attestation.destination_key_asset.organization_id != attempt.organization_id
        or (
            attestation.source_key_asset is not None
            and attestation.source_key_asset.organization_id != attempt.organization_id
        )
    ):
        raise CustodyLifecycleError("custody_lifecycle_tenant_mismatch")
    source = attestation.source_key_asset
    cursor.execute(
        """
        INSERT INTO custody_envelope_attestations (
            organization_id, execution_id, attestation_kind,
            envelope_asset_id, envelope_generation, envelope_binding_fingerprint,
            source_key_asset_id, source_key_generation, source_key_binding_fingerprint,
            destination_key_asset_id, destination_key_generation, destination_key_binding_fingerprint,
            occurred_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            attempt.organization_id,
            attempt.execution_id,
            attestation.kind,
            attestation.envelope_asset.asset_id,
            attestation.envelope_asset.generation,
            attestation.envelope_asset.binding_fingerprint,
            source.asset_id if source is not None else None,
            source.generation if source is not None else None,
            source.binding_fingerprint if source is not None else None,
            attestation.destination_key_asset.asset_id,
            attestation.destination_key_asset.generation,
            attestation.destination_key_asset.binding_fingerprint,
            occurred_at,
        ),
    )
    if cursor.rowcount != 1:
        raise PostgresStorageError("custody_lifecycle_attestation_unavailable")


class PostgresCustodyProjectionStore:
    """Read-only runtime view of the derived tenant projection."""

    def __init__(
        self,
        dsn: str,
        *,
        schema: str,
        runtime_role: str,
        connection_pool: PostgresConnectionPool | None = None,
    ) -> None:
        self._dsn = dsn
        self._schema = schema
        self._runtime_role = runtime_role
        self._connection_pool = connection_pool
        verify_postgres_schema(
            dsn,
            schema=schema,
            runtime_role=runtime_role,
            connection_pool=connection_pool,
            verify_runtime_schema=True,
        )

    def load(self, *, organization_id: str) -> CustodyProjectionSnapshot:
        with self._transaction(organization_id) as connection:
            with connection.cursor() as cursor:
                return self._load_projection(cursor, organization_id=organization_id)

    def synchronize(
        self,
        *,
        organization_id: str,
        replica_id: str,
        observed_projection_version: int,
    ) -> CustodyProjectionCoordinationSnapshot:
        """Renew one replica lease and atomically read projection plus barriers."""

        with self._transaction(organization_id) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT custody_runtime_sync_replica(%s, %s::uuid, %s)",
                    (organization_id, replica_id, observed_projection_version),
                )
                projection = self._load_projection(cursor, organization_id=organization_id)
                barriers = self._load_barriers(cursor, organization_id=organization_id)
        return CustodyProjectionCoordinationSnapshot(projection=projection, barriers=barriers)

    def acknowledge(
        self,
        *,
        organization_id: str,
        replica_id: str,
        barrier_id: str,
        observed_projection_version: int,
    ) -> None:
        """Persist an acknowledgement only after the caller installed a barrier."""

        with self._transaction(organization_id) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT custody_runtime_ack_barrier(%s, %s::uuid, %s::uuid, %s)",
                    (organization_id, replica_id, barrier_id, observed_projection_version),
                )

    def retire_replica(self, *, organization_id: str, replica_id: str) -> None:
        """Relinquish admission authority after the local listener has drained."""

        with self._transaction(organization_id) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT custody_runtime_retire_replica(%s, %s::uuid)",
                    (organization_id, replica_id),
                )

    @contextmanager
    def notification_listener(self) -> Iterator[Callable[[float], bool]]:
        """Yield a PostgreSQL LISTEN waiter; polling remains the durable fallback."""

        psycopg, _ = _driver()
        connection = None
        try:
            connection = psycopg.connect(self._dsn, autocommit=True)
            with connection.cursor() as cursor:
                cursor.execute("LISTEN hormuz_custody_projection")

            def wait(timeout: float) -> bool:
                return next(connection.notifies(timeout=timeout, stop_after=1), None) is not None

            yield wait
        except psycopg.Error as error:
            raise _storage_error(error) from None
        finally:
            if connection is not None:
                try:
                    connection.close()
                except psycopg.Error:
                    pass

    @staticmethod
    def _load_projection(cursor: Any, *, organization_id: str) -> CustodyProjectionSnapshot:
        cursor.execute(
            """
            SELECT version, committed_at
            FROM custody_runtime_projection_heads
            WHERE organization_id = %s
            """,
            (organization_id,),
        )
        head = cursor.fetchone()
        cursor.execute(
            """
            SELECT asset_type, asset_id, generation, restriction_kind
            FROM custody_runtime_projection_restrictions
            WHERE organization_id = %s
            """,
            (organization_id,),
        )
        rows = cursor.fetchall()
        restrictions: dict[tuple[str, str, int], str] = {}
        for row in rows:
            try:
                restrictions[(str(row["asset_type"]), str(row["asset_id"]), int(row["generation"]))] = str(
                    row["restriction_kind"]
                )
            except (KeyError, TypeError, ValueError) as error:
                raise PostgresStorageError("custody_runtime_projection_invalid") from error
        if head is None:
            return CustodyProjectionSnapshot(
                organization_id=organization_id,
                version=0,
                committed_at=datetime.now(timezone.utc),
                restrictions=restrictions,
            )
        committed_at = head.get("committed_at")
        if not isinstance(committed_at, datetime):
            raise PostgresStorageError("custody_runtime_projection_invalid")
        try:
            version = int(head["version"])
        except (KeyError, TypeError, ValueError) as error:
            raise PostgresStorageError("custody_runtime_projection_invalid") from error
        return CustodyProjectionSnapshot(
            organization_id=organization_id,
            version=version,
            committed_at=committed_at,
            restrictions=restrictions,
        )

    @staticmethod
    def _load_barriers(cursor: Any, *, organization_id: str) -> tuple[CustodyProjectionBarrier, ...]:
        cursor.execute(
            """
            SELECT barrier_id, execution_id, proposed_version,
                   asset_type, asset_id, asset_generation, asset_binding_fingerprint,
                   restriction_kind, prepared_at
            FROM custody_runtime_projection_barriers
            WHERE organization_id = %s
              AND activated_at IS NULL
              AND resolved_at IS NULL
            ORDER BY proposed_version, barrier_id
            """,
            (organization_id,),
        )
        barriers: list[CustodyProjectionBarrier] = []
        for row in cursor.fetchall():
            try:
                barriers.append(
                    CustodyProjectionBarrier(
                        organization_id=organization_id,
                        barrier_id=str(row["barrier_id"]),
                        execution_id=str(row["execution_id"]),
                        proposed_version=int(row["proposed_version"]),
                        asset_type=str(row["asset_type"]),
                        asset_id=str(row["asset_id"]),
                        generation=int(row["asset_generation"]),
                        binding_fingerprint=str(row["asset_binding_fingerprint"]),
                        restriction_kind=str(row["restriction_kind"]),
                        prepared_at=row["prepared_at"],
                    )
                )
            except (KeyError, TypeError, ValueError) as error:
                raise PostgresStorageError("custody_runtime_projection_invalid") from error
        return tuple(barriers)

    def verify_catalog(self, *, organization_id: str, catalog: CustodyAssetCatalog) -> None:
        """Verify the configured identity mapping before a gateway can select it."""

        with self._transaction(organization_id) as connection:
            with connection.cursor() as cursor:
                verify_custody_asset_catalog(
                    cursor,
                    organization_id=organization_id,
                    catalog=catalog,
                )

    def _transaction(self, organization_id: str) -> Iterator[Any]:
        return postgres_transaction(
            self._dsn,
            schema=self._schema,
            runtime_role=self._runtime_role,
            organization_id=organization_id,
            connection_pool=self._connection_pool,
        )


def lifecycle_effect_descriptors(effect: CustodyLifecycleEffect) -> tuple[dict[str, object], dict[str, object]]:
    """The exact non-secret descriptors a custody administrator must approve."""

    if effect.operation_type in {"disable_provider_credential", "retire_envelope"}:
        assert effect.asset is not None
        return effect.asset.audit_ref(), {}
    if effect.operation_type == "retire_key_reference":
        assert effect.asset is not None and effect.replacement_asset is not None
        return effect.asset.audit_ref(), {"replacement_asset": effect.replacement_asset.audit_ref()}
    assert effect.operation_type == "resolve_recovery"
    assert effect.recovery_execution_id is not None and effect.recovery_resolution_code is not None
    return {"recovery_execution_id": effect.recovery_execution_id}, {"resolution_code": effect.recovery_resolution_code}
