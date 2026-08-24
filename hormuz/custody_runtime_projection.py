"""Coordinated, read-only runtime enforcement of custody restrictions."""

from __future__ import annotations

import hmac
import os
import threading
import time
from dataclasses import dataclass
from typing import Mapping, Protocol
from uuid import uuid4

from .config import GatewayConfig
from .custody_lifecycle import (
    CUSTODY_COORDINATION_LEASE_SECONDS,
    CustodyAsset,
    CustodyAssetCatalog,
    CustodyLifecycleError,
    CustodyProjectionBarrier,
    CustodyProjectionCoordinationSnapshot,
    CustodyProjectionSnapshot,
)
from .postgres import PostgresConnectionPool, PostgresStorageError
from .postgres_custody_lifecycle_store import PostgresCustodyProjectionStore


_COORDINATION_POLL_SECONDS = 0.25


class CustodyRuntimeProjectionError(RuntimeError):
    """Stable gateway-side result of an unavailable or restrictive projection."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class CustodyProjectionStore(Protocol):
    """The narrow coordination boundary used by each gateway replica."""

    def verify_catalog(
        self,
        *,
        organization_id: str,
        catalog: CustodyAssetCatalog,
    ) -> None: ...

    def synchronize(
        self,
        *,
        organization_id: str,
        replica_id: str,
        observed_projection_version: int,
    ) -> CustodyProjectionCoordinationSnapshot: ...

    def acknowledge(
        self,
        *,
        organization_id: str,
        replica_id: str,
        barrier_id: str,
        observed_projection_version: int,
    ) -> None: ...

    def retire_replica(self, *, organization_id: str, replica_id: str) -> None: ...


@dataclass(frozen=True)
class _LeasedCoordination:
    snapshot: CustodyProjectionSnapshot
    barriers: Mapping[tuple[str, str, int], CustodyProjectionBarrier]
    expires_at_monotonic: float


class CustodyRuntimeProjection:
    """Coordinate local admission barriers without a database read per request.

    A barrier is installed locally before this replica acknowledges it.
    PostgreSQL cannot activate the associated restriction while any healthy
    replica remains unacknowledged. The five-second monotonic lease is only the
    partition-safety backstop; synchronization failure makes readiness
    unhealthy immediately.
    """

    def __init__(
        self,
        config: GatewayConfig,
        *,
        environ: Mapping[str, str] | None = None,
        connection_pool: PostgresConnectionPool | None = None,
        projection_store: CustodyProjectionStore | None = None,
        start_background: bool = True,
        replica_id: str | None = None,
    ) -> None:
        self._config = config
        self._lifecycle = config.custody_lifecycle
        self._lock = threading.RLock()
        self._leases: dict[str, _LeasedCoordination] = {}
        self._store: CustodyProjectionStore | None = None
        self._replica_id = str(uuid4()) if replica_id is None else replica_id
        self._stop = threading.Event()
        self._coordinator: threading.Thread | None = None
        self._coordination_failed = False
        self._closed = False
        if self._lifecycle is None:
            return
        if self._lifecycle.freshness_lease_seconds != CUSTODY_COORDINATION_LEASE_SECONDS:
            raise CustodyRuntimeProjectionError("custody_runtime_projection_configuration_invalid")
        try:
            if projection_store is not None:
                self._store = projection_store
            else:
                environment = os.environ if environ is None else environ
                dsn = environment.get(config.usage_storage.postgres_dsn_env, "")
                if not dsn:
                    raise CustodyRuntimeProjectionError("custody_runtime_projection_unavailable")
                self._store = PostgresCustodyProjectionStore(
                    dsn,
                    schema=config.usage_storage.postgres_schema,
                    runtime_role=config.usage_storage.postgres_runtime_role,
                    connection_pool=connection_pool,
                )
            for organization_id in config.organization_ids:
                self._store.verify_catalog(
                    organization_id=organization_id,
                    catalog=self._lifecycle.assets,
                )
                self._synchronize(organization_id)
            if start_background:
                self._coordinator = threading.Thread(
                    target=self._coordination_loop,
                    name=f"hormuz-custody-coordinator-{self._replica_id[:8]}",
                    daemon=True,
                )
                self._coordinator.start()
        except Exception:
            self._retire_quietly()
            raise CustodyRuntimeProjectionError("custody_runtime_projection_unavailable") from None

    @property
    def enabled(self) -> bool:
        return self._lifecycle is not None

    @property
    def replica_id(self) -> str:
        return self._replica_id

    def readiness_healthy(self) -> bool:
        """Return whether every tenant has coordinated, fresh local state."""

        if self._lifecycle is None:
            return True
        now = time.monotonic()
        with self._lock:
            return (
                not self._closed
                and not self._coordination_failed
                and len(self._leases) == len(self._config.organization_ids)
                and all(
                    lease.expires_at_monotonic > now
                    for organization_id in self._config.organization_ids
                    if (lease := self._leases.get(organization_id)) is not None
                )
            )

    def require_provider_usable(self, *, organization_id: str, protocol: str) -> None:
        """Deny restricted selection before a new gateway request is pinned."""

        if self._lifecycle is None:
            return
        state = self._fresh_state(organization_id)
        credential = self._provider_asset(organization_id=organization_id, protocol=protocol)
        self._require_unrestricted(state, credential)
        upstream = self._config.upstreams[protocol]
        if upstream.api_key_envelope_path is None:
            return
        envelope = self._envelope_asset(
            organization_id=organization_id,
            protocol=protocol,
            path=str(upstream.api_key_envelope_path),
            credential=credential,
        )
        self._require_unrestricted(state, envelope)

    def require_key_writable(self, *, organization_id: str, purpose: str) -> None:
        """Block sealing or rewrapping under a write-retired key generation."""

        if self._lifecycle is None:
            return
        state = self._fresh_state(organization_id)
        key_reference = self._config.key_custody.key_reference_for(purpose) if self._config.key_custody else ""
        matches = tuple(
            asset
            for asset in self._lifecycle.assets.assets_for(organization_id=organization_id, asset_type="key_reference")
            if asset.binding.get("purpose") == purpose and asset.binding.get("key_reference") == key_reference
        )
        if len(matches) != 1:
            raise CustodyRuntimeProjectionError("custody_runtime_projection_configuration_invalid")
        self._require_unrestricted(state, matches[0])

    def close(self) -> None:
        """Stop coordination and relinquish leases after the listener drains."""

        if self._lifecycle is None:
            return
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._coordination_failed = True
        self._stop.set()
        if self._coordinator is not None:
            self._coordinator.join(timeout=max(1.0, _COORDINATION_POLL_SECONDS * 4))
        self._retire_quietly()

    def _coordination_loop(self) -> None:
        assert self._store is not None
        listener_factory = getattr(self._store, "notification_listener", None)
        while not self._stop.is_set():
            if listener_factory is None:
                self._stop.wait(_COORDINATION_POLL_SECONDS)
                self._synchronize_all()
                continue
            try:
                with listener_factory() as wait_for_change:
                    while not self._stop.is_set():
                        wait_for_change(_COORDINATION_POLL_SECONDS)
                        self._synchronize_all()
            except Exception:
                # LISTEN reduces invalidation latency. The durable scan remains
                # authoritative and also renews the bounded serving lease.
                self._synchronize_all()
                self._stop.wait(_COORDINATION_POLL_SECONDS)

    def _synchronize_all(self) -> None:
        if self._stop.is_set():
            return
        failed = False
        for organization_id in self._config.organization_ids:
            try:
                self._synchronize(organization_id)
            except Exception:
                failed = True
        with self._lock:
            self._coordination_failed = self._closed or failed

    def _synchronize(self, organization_id: str) -> None:
        if self._lifecycle is None or self._store is None:
            raise CustodyRuntimeProjectionError("custody_runtime_projection_unavailable")
        with self._lock:
            current = self._leases.get(organization_id)
            observed_version = current.snapshot.version if current is not None else 0
        call_started = time.monotonic()
        coordinated = self._store.synchronize(
            organization_id=organization_id,
            replica_id=self._replica_id,
            observed_projection_version=observed_version,
        )
        if coordinated.projection.organization_id != organization_id:
            raise CustodyLifecycleError("custody_runtime_projection_invalid")
        barriers = self._validated_barriers(coordinated)
        expires_at = call_started + CUSTODY_COORDINATION_LEASE_SECONDS
        if expires_at <= time.monotonic():
            raise PostgresStorageError("custody_runtime_projection_unavailable")
        # Install locally before acknowledging. From this point, the affected
        # asset is denied even though the authoritative version is not active.
        with self._lock:
            self._leases[organization_id] = _LeasedCoordination(
                snapshot=coordinated.projection,
                barriers=barriers,
                expires_at_monotonic=expires_at,
            )
        for barrier in coordinated.barriers:
            acknowledgement_started = time.monotonic()
            self._store.acknowledge(
                organization_id=organization_id,
                replica_id=self._replica_id,
                barrier_id=barrier.barrier_id,
                observed_projection_version=coordinated.projection.version,
            )
            acknowledgement_expires = acknowledgement_started + CUSTODY_COORDINATION_LEASE_SECONDS
            if acknowledgement_expires <= time.monotonic():
                raise PostgresStorageError("custody_runtime_projection_unavailable")
            with self._lock:
                installed = self._leases.get(organization_id)
                if installed is not None:
                    self._leases[organization_id] = _LeasedCoordination(
                        snapshot=installed.snapshot,
                        barriers=installed.barriers,
                        expires_at_monotonic=acknowledgement_expires,
                    )

    def _validated_barriers(
        self,
        coordinated: CustodyProjectionCoordinationSnapshot,
    ) -> Mapping[tuple[str, str, int], CustodyProjectionBarrier]:
        assert self._lifecycle is not None
        barriers: dict[tuple[str, str, int], CustodyProjectionBarrier] = {}
        for barrier in coordinated.barriers:
            if barrier.proposed_version != coordinated.projection.version + 1:
                raise CustodyLifecycleError("custody_runtime_projection_invalid")
            asset = self._lifecycle.assets.asset(
                organization_id=barrier.organization_id,
                asset_type=barrier.asset_type,
                asset_id=barrier.asset_id,
                generation=barrier.generation,
            )
            if not hmac.compare_digest(asset.binding_fingerprint, barrier.binding_fingerprint):
                raise CustodyLifecycleError("custody_runtime_projection_invalid")
            if barrier.asset_key in barriers:
                raise CustodyLifecycleError("custody_runtime_projection_invalid")
            barriers[barrier.asset_key] = barrier
        return barriers

    def _fresh_state(self, organization_id: str) -> _LeasedCoordination:
        now = time.monotonic()
        with self._lock:
            state = self._leases.get(organization_id)
            if (
                self._closed
                or self._coordination_failed
                or state is None
                or state.expires_at_monotonic <= now
            ):
                raise CustodyRuntimeProjectionError("custody_runtime_projection_unavailable")
            return state

    def _retire_quietly(self) -> None:
        if self._lifecycle is None or self._store is None:
            return
        for organization_id in self._config.organization_ids:
            try:
                self._store.retire_replica(
                    organization_id=organization_id,
                    replica_id=self._replica_id,
                )
            except Exception:
                pass

    def _provider_asset(self, *, organization_id: str, protocol: str) -> CustodyAsset:
        assert self._lifecycle is not None
        matches = tuple(
            asset
            for asset in self._lifecycle.assets.assets_for(organization_id=organization_id, asset_type="provider_credential")
            if asset.binding.get("protocol") == protocol
        )
        if len(matches) != 1:
            raise CustodyRuntimeProjectionError("custody_runtime_projection_configuration_invalid")
        return matches[0]

    def _envelope_asset(
        self,
        *,
        organization_id: str,
        protocol: str,
        path: str,
        credential: CustodyAsset,
    ) -> CustodyAsset:
        assert self._lifecycle is not None
        expected_credential = f"{credential.asset_id}@{credential.generation}"
        matches = tuple(
            asset
            for asset in self._lifecycle.assets.assets_for(organization_id=organization_id, asset_type="envelope")
            if asset.binding.get("path") == path
            and asset.binding.get("provider_credential_asset") == expected_credential
        )
        if len(matches) != 1:
            raise CustodyRuntimeProjectionError("custody_runtime_projection_configuration_invalid")
        return matches[0]

    @staticmethod
    def _require_unrestricted(state: _LeasedCoordination, asset: CustodyAsset) -> None:
        barrier = state.barriers.get((asset.asset_type, asset.asset_id, asset.generation))
        restriction = barrier.restriction_kind if barrier is not None else state.snapshot.restriction_for(asset)
        if restriction == "provider_credential_disabled":
            raise CustodyRuntimeProjectionError("custody_provider_credential_disabled")
        if restriction == "envelope_retired":
            raise CustodyRuntimeProjectionError("custody_envelope_retired")
        if restriction == "key_reference_write_retired":
            raise CustodyRuntimeProjectionError("custody_key_reference_write_retired")
        if restriction is not None:
            raise CustodyRuntimeProjectionError("custody_runtime_projection_restricted")
