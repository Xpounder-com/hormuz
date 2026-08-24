"""Immutable custody-lifecycle asset and projection contracts.

This module is deliberately free of provider clients, database drivers, and
plaintext credentials. It defines the metadata-only boundary shared by the
isolated custody executor and the gateway's cached runtime projection.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any, Mapping
from uuid import UUID

from ._contract_schemas.constants import (
    CUSTODY_LIFECYCLE_EVENT_SCHEMA_ID,
    CUSTODY_LIFECYCLE_EVENT_SCHEMA_VERSION,
)


CUSTODY_ASSET_TYPES = frozenset({"provider_credential", "envelope", "key_reference"})
CUSTODY_RESTRICTION_KINDS = frozenset(
    {"provider_credential_disabled", "envelope_retired", "key_reference_write_retired"}
)
CUSTODY_RECOVERY_RESOLUTION_CODES = frozenset(
    {"confirmed_applied", "confirmed_not_applied", "compensating_action_completed"}
)
CUSTODY_LIFECYCLE_CHAIN_VERSION = 1
CUSTODY_COORDINATION_LEASE_SECONDS = 5
_ASSET_IDENTIFIER_FIRST_CHARACTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
_ASSET_IDENTIFIER_CHARACTERS = _ASSET_IDENTIFIER_FIRST_CHARACTERS + "._-"


class CustodyLifecycleError(RuntimeError):
    """Stable, content-free custody lifecycle failure."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class CustodyAsset:
    """An immutable tenant-qualified protected-asset generation.

    ``binding`` is configuration-only and intentionally excluded from repr,
    status output, lifecycle evidence, and the runtime projection. Its
    fingerprint rejects reuse of an identity for a different local binding.
    """

    organization_id: str
    asset_type: str
    asset_id: str
    generation: int
    binding_fingerprint: str
    binding: Mapping[str, str] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        _identifier(self.organization_id, "organization_id")
        if self.asset_type not in CUSTODY_ASSET_TYPES:
            raise ValueError("Custody asset type is invalid")
        _asset_identifier(self.asset_id)
        if isinstance(self.generation, bool) or not isinstance(self.generation, int) or self.generation < 1:
            raise ValueError("Custody asset generation is invalid")
        _sha256(self.binding_fingerprint, "binding_fingerprint")
        if not isinstance(self.binding, Mapping):
            raise ValueError("Custody asset binding is invalid")
        normalized: dict[str, str] = {}
        for key, value in self.binding.items():
            if not isinstance(key, str) or not key or not isinstance(value, str) or not value:
                raise ValueError("Custody asset binding is invalid")
            normalized[key] = value
        object.__setattr__(self, "binding", MappingProxyType(dict(sorted(normalized.items()))))

    @property
    def key(self) -> tuple[str, str, str, int]:
        return (self.organization_id, self.asset_type, self.asset_id, self.generation)

    def audit_ref(self) -> dict[str, object]:
        return {
            "asset_type": self.asset_type,
            "asset_id": self.asset_id,
            "generation": self.generation,
            "binding_fingerprint": self.binding_fingerprint,
        }


class CustodyAssetCatalog:
    """Configuration-owned mapping between asset identities and local bindings."""

    def __init__(self, assets: tuple[CustodyAsset, ...]) -> None:
        if not assets:
            raise ValueError("Custody lifecycle assets are required")
        by_key: dict[tuple[str, str, str, int], CustodyAsset] = {}
        by_binding: dict[tuple[str, tuple[tuple[str, str], ...]], CustodyAsset] = {}
        for asset in assets:
            if asset.key in by_key:
                raise ValueError("Custody asset identity is duplicated")
            binding_key = (asset.organization_id, tuple(sorted(asset.binding.items())))
            if binding_key in by_binding:
                raise ValueError("Custody asset binding is duplicated")
            by_key[asset.key] = asset
            by_binding[binding_key] = asset
        self._assets = tuple(sorted(by_key.values(), key=lambda item: item.key))
        self._by_key = MappingProxyType(by_key)
        self._by_binding = MappingProxyType(by_binding)

    @property
    def assets(self) -> tuple[CustodyAsset, ...]:
        return self._assets

    def asset(self, *, organization_id: str, asset_type: str, asset_id: str, generation: int) -> CustodyAsset:
        try:
            return self._by_key[(organization_id, asset_type, asset_id, generation)]
        except KeyError as error:
            raise CustodyLifecycleError("custody_lifecycle_asset_not_configured") from error

    def asset_for_binding(self, *, organization_id: str, binding: Mapping[str, str]) -> CustodyAsset:
        normalized = tuple(sorted((str(key), str(value)) for key, value in binding.items()))
        try:
            return self._by_binding[(organization_id, normalized)]
        except KeyError as error:
            raise CustodyLifecycleError("custody_lifecycle_asset_not_configured") from error

    def assets_for(self, *, organization_id: str, asset_type: str) -> tuple[CustodyAsset, ...]:
        return tuple(
            asset
            for asset in self._assets
            if asset.organization_id == organization_id and asset.asset_type == asset_type
        )

    def require_descriptor(self, *, organization_id: str, value: Mapping[str, Any]) -> CustodyAsset:
        if not isinstance(value, Mapping) or set(value) != {
            "asset_type",
            "asset_id",
            "generation",
            "binding_fingerprint",
        }:
            raise CustodyLifecycleError("custody_lifecycle_asset_descriptor_invalid")
        asset_type = value.get("asset_type")
        asset_id = value.get("asset_id")
        generation = value.get("generation")
        fingerprint = value.get("binding_fingerprint")
        if (
            not isinstance(asset_type, str)
            or not isinstance(asset_id, str)
            or isinstance(generation, bool)
            or not isinstance(generation, int)
            or not isinstance(fingerprint, str)
        ):
            raise CustodyLifecycleError("custody_lifecycle_asset_descriptor_invalid")
        asset = self.asset(
            organization_id=organization_id,
            asset_type=asset_type,
            asset_id=asset_id,
            generation=generation,
        )
        if not hmac.compare_digest(asset.binding_fingerprint, fingerprint):
            raise CustodyLifecycleError("custody_lifecycle_asset_descriptor_invalid")
        return asset


@dataclass(frozen=True)
class CustodyLifecycleConfig:
    """Opt-in runtime-projection configuration for governed custody lifecycle."""

    freshness_lease_seconds: int
    assets: CustodyAssetCatalog

    def __post_init__(self) -> None:
        if (
            isinstance(self.freshness_lease_seconds, bool)
            or not isinstance(self.freshness_lease_seconds, int)
            or self.freshness_lease_seconds != CUSTODY_COORDINATION_LEASE_SECONDS
        ):
            raise ValueError("Custody lifecycle freshness lease is invalid")
        if not isinstance(self.assets, CustodyAssetCatalog):
            raise ValueError("Custody lifecycle asset catalog is invalid")


@dataclass(frozen=True)
class CustodyLifecycleEffect:
    """The planned, metadata-only durable result of a destructive operation."""

    operation_type: str
    asset: CustodyAsset | None = None
    replacement_asset: CustodyAsset | None = None
    recovery_execution_id: str | None = None
    recovery_resolution_code: str | None = None

    def __post_init__(self) -> None:
        if self.operation_type == "disable_provider_credential":
            _require_asset_type(self.asset, "provider_credential")
            _require_none(self.replacement_asset, self.recovery_execution_id, self.recovery_resolution_code)
            return
        if self.operation_type == "retire_envelope":
            _require_asset_type(self.asset, "envelope")
            _require_none(self.replacement_asset, self.recovery_execution_id, self.recovery_resolution_code)
            return
        if self.operation_type == "retire_key_reference":
            _require_asset_type(self.asset, "key_reference")
            _require_asset_type(self.replacement_asset, "key_reference")
            if self.asset is not None and self.replacement_asset is not None:
                if self.asset.organization_id != self.replacement_asset.organization_id:
                    raise ValueError("Custody lifecycle key replacement tenant is invalid")
                if self.asset.key == self.replacement_asset.key:
                    raise ValueError("Custody lifecycle key replacement is invalid")
                if self.asset.binding.get("purpose") != self.replacement_asset.binding.get("purpose"):
                    raise ValueError("Custody lifecycle key replacement purpose is invalid")
            _require_none(self.recovery_execution_id, self.recovery_resolution_code)
            return
        if self.operation_type == "resolve_recovery":
            if self.asset is not None or self.replacement_asset is not None:
                raise ValueError("Custody recovery resolution must not select an asset")
            _uuid(self.recovery_execution_id, "recovery_execution_id")
            if self.recovery_resolution_code not in CUSTODY_RECOVERY_RESOLUTION_CODES:
                raise ValueError("Custody recovery resolution code is invalid")
            return
        raise ValueError("Custody lifecycle operation is invalid")


@dataclass(frozen=True)
class CustodyEnvelopeAttestation:
    """A successful rewrap or restore verification bound to configured assets."""

    kind: str
    envelope_asset: CustodyAsset
    destination_key_asset: CustodyAsset
    source_key_asset: CustodyAsset | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"rewrapped", "restore_verified"}:
            raise ValueError("Custody envelope attestation kind is invalid")
        _require_asset_type(self.envelope_asset, "envelope")
        _require_asset_type(self.destination_key_asset, "key_reference")
        if self.kind == "rewrapped":
            _require_asset_type(self.source_key_asset, "key_reference")
            return
        if self.source_key_asset is not None:
            raise ValueError("Custody restore attestation source key is invalid")


@dataclass(frozen=True)
class CustodyLifecycleEvent:
    """One immutable, hash-linked metadata-only lifecycle event."""

    organization_id: str
    lifecycle_event_id: str
    execution_id: str
    operation_id: str
    occurred_at: datetime
    effect: CustodyLifecycleEffect
    target_sha256: str
    parameters_sha256: str
    chain_version: int
    sequence: int
    previous_digest: str | None
    event_digest: str

    def __post_init__(self) -> None:
        _identifier(self.organization_id, "organization_id")
        _uuid(self.lifecycle_event_id, "lifecycle_event_id")
        _uuid(self.execution_id, "execution_id")
        _uuid(self.operation_id, "operation_id")
        if self.occurred_at.tzinfo is None:
            raise ValueError("Custody lifecycle event timestamp must be timezone-aware")
        _sha256(self.target_sha256, "target_sha256")
        _sha256(self.parameters_sha256, "parameters_sha256")
        if self.chain_version != CUSTODY_LIFECYCLE_CHAIN_VERSION or self.sequence < 1:
            raise ValueError("Custody lifecycle chain position is invalid")
        if self.previous_digest is not None:
            _sha256(self.previous_digest, "previous_digest")
        _sha256(self.event_digest, "event_digest")
        expected = lifecycle_event_digest(
            self.metadata_record(),
            organization_id=self.organization_id,
            chain_version=self.chain_version,
            sequence=self.sequence,
            previous_digest=self.previous_digest,
        )
        if not hmac.compare_digest(self.event_digest, expected):
            raise ValueError("Custody lifecycle event digest is invalid")

    def metadata_record(self) -> dict[str, object]:
        return _lifecycle_event_metadata(
            organization_id=self.organization_id,
            lifecycle_event_id=self.lifecycle_event_id,
            execution_id=self.execution_id,
            operation_id=self.operation_id,
            occurred_at=self.occurred_at,
            effect=self.effect,
            target_sha256=self.target_sha256,
            parameters_sha256=self.parameters_sha256,
        )

    def contract_record(self) -> dict[str, object]:
        return {
            **self.metadata_record(),
            "chain_version": self.chain_version,
            "sequence": self.sequence,
            "previous_digest": self.previous_digest,
            "event_digest": self.event_digest,
        }


@dataclass(frozen=True)
class CustodyProjectionSnapshot:
    """A versioned immutable runtime projection synchronized by one replica."""

    organization_id: str
    version: int
    committed_at: datetime
    restrictions: Mapping[tuple[str, str, int], str]

    def __post_init__(self) -> None:
        _identifier(self.organization_id, "organization_id")
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 0:
            raise ValueError("Custody projection version is invalid")
        if self.committed_at.tzinfo is None:
            raise ValueError("Custody projection timestamp must be timezone-aware")
        normalized: dict[tuple[str, str, int], str] = {}
        for key, restriction in self.restrictions.items():
            if (
                not isinstance(key, tuple)
                or len(key) != 3
                or not isinstance(key[0], str)
                or not isinstance(key[1], str)
                or isinstance(key[2], bool)
                or not isinstance(key[2], int)
                or restriction not in CUSTODY_RESTRICTION_KINDS
            ):
                raise ValueError("Custody projection restriction is invalid")
            normalized[key] = restriction
        object.__setattr__(self, "restrictions", MappingProxyType(normalized))

    def restriction_for(self, asset: CustodyAsset) -> str | None:
        if asset.organization_id != self.organization_id:
            raise CustodyLifecycleError("custody_lifecycle_tenant_mismatch")
        return self.restrictions.get((asset.asset_type, asset.asset_id, asset.generation))


@dataclass(frozen=True)
class CustodyProjectionBarrier:
    """A prepared restriction installed locally before a replica acknowledges it.

    The barrier is coordination state, not authoritative lifecycle history. It
    contains only the immutable asset identity and proposed restriction; local
    bindings, paths, provider keys, and KMS references never cross this boundary.
    """

    organization_id: str
    barrier_id: str
    execution_id: str
    proposed_version: int
    asset_type: str
    asset_id: str
    generation: int
    binding_fingerprint: str
    restriction_kind: str
    prepared_at: datetime

    def __post_init__(self) -> None:
        _identifier(self.organization_id, "organization_id")
        _uuid(self.barrier_id, "barrier_id")
        _uuid(self.execution_id, "execution_id")
        if (
            isinstance(self.proposed_version, bool)
            or not isinstance(self.proposed_version, int)
            or self.proposed_version < 1
        ):
            raise ValueError("Custody projection barrier version is invalid")
        if self.asset_type not in CUSTODY_ASSET_TYPES:
            raise ValueError("Custody projection barrier asset type is invalid")
        _asset_identifier(self.asset_id)
        if isinstance(self.generation, bool) or not isinstance(self.generation, int) or self.generation < 1:
            raise ValueError("Custody projection barrier generation is invalid")
        _sha256(self.binding_fingerprint, "binding_fingerprint")
        if self.restriction_kind not in CUSTODY_RESTRICTION_KINDS:
            raise ValueError("Custody projection barrier restriction is invalid")
        expected_asset_type = {
            "provider_credential_disabled": "provider_credential",
            "envelope_retired": "envelope",
            "key_reference_write_retired": "key_reference",
        }[self.restriction_kind]
        if self.asset_type != expected_asset_type:
            raise ValueError("Custody projection barrier restriction is invalid")
        if self.prepared_at.tzinfo is None:
            raise ValueError("Custody projection barrier timestamp must be timezone-aware")

    @property
    def asset_key(self) -> tuple[str, str, int]:
        return (self.asset_type, self.asset_id, self.generation)


@dataclass(frozen=True)
class CustodyProjectionCoordinationSnapshot:
    """One atomic runtime read of active projection and prepared barriers."""

    projection: CustodyProjectionSnapshot
    barriers: tuple[CustodyProjectionBarrier, ...]

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for barrier in self.barriers:
            if barrier.organization_id != self.projection.organization_id:
                raise ValueError("Custody projection coordination tenant is invalid")
            if barrier.barrier_id in seen:
                raise ValueError("Custody projection coordination barrier is duplicated")
            seen.add(barrier.barrier_id)


def build_custody_lifecycle_event(
    *,
    organization_id: str,
    lifecycle_event_id: str,
    execution_id: str,
    operation_id: str,
    occurred_at: datetime,
    effect: CustodyLifecycleEffect,
    target_sha256: str,
    parameters_sha256: str,
    chain_version: int,
    sequence: int,
    previous_digest: str | None,
) -> CustodyLifecycleEvent:
    """Construct one digest-validated lifecycle event without a mutable phase."""

    metadata = _lifecycle_event_metadata(
        organization_id=organization_id,
        lifecycle_event_id=lifecycle_event_id,
        execution_id=execution_id,
        operation_id=operation_id,
        occurred_at=occurred_at,
        effect=effect,
        target_sha256=target_sha256,
        parameters_sha256=parameters_sha256,
    )
    digest = lifecycle_event_digest(
        metadata,
        organization_id=organization_id,
        chain_version=chain_version,
        sequence=sequence,
        previous_digest=previous_digest,
    )
    return CustodyLifecycleEvent(
        organization_id=organization_id,
        lifecycle_event_id=lifecycle_event_id,
        execution_id=execution_id,
        operation_id=operation_id,
        occurred_at=occurred_at,
        effect=effect,
        target_sha256=target_sha256,
        parameters_sha256=parameters_sha256,
        chain_version=chain_version,
        sequence=sequence,
        previous_digest=previous_digest,
        event_digest=digest,
    )


def binding_fingerprint(
    *,
    organization_id: str,
    asset_type: str,
    asset_id: str,
    generation: int,
    binding: Mapping[str, str],
) -> str:
    payload = {
        "organization_id": organization_id,
        "asset_type": asset_type,
        "asset_id": asset_id,
        "generation": generation,
        "binding": dict(sorted(binding.items())),
    }
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def lifecycle_event_digest(
    metadata: Mapping[str, object],
    *,
    organization_id: str,
    chain_version: int,
    sequence: int,
    previous_digest: str | None,
) -> str:
    value = {
        "organization_id": organization_id,
        "chain_version": chain_version,
        "sequence": sequence,
        "previous_digest": previous_digest,
        "event": dict(metadata),
    }
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError):
        raise CustodyLifecycleError("custody_lifecycle_canonicalization_invalid") from None


def _lifecycle_event_metadata(
    *,
    organization_id: str,
    lifecycle_event_id: str,
    execution_id: str,
    operation_id: str,
    occurred_at: datetime,
    effect: CustodyLifecycleEffect,
    target_sha256: str,
    parameters_sha256: str,
) -> dict[str, object]:
    asset = effect.asset
    replacement = effect.replacement_asset
    return {
        "lifecycle_schema_id": CUSTODY_LIFECYCLE_EVENT_SCHEMA_ID,
        "lifecycle_schema_version": CUSTODY_LIFECYCLE_EVENT_SCHEMA_VERSION,
        "organization_id": organization_id,
        "lifecycle_event_id": lifecycle_event_id,
        "execution_id": execution_id,
        "operation_id": operation_id,
        "occurred_at": occurred_at.isoformat(),
        "operation_type": effect.operation_type,
        "target_sha256": target_sha256,
        "parameters_sha256": parameters_sha256,
        "asset_type": asset.asset_type if asset is not None else None,
        "asset_id": asset.asset_id if asset is not None else None,
        "asset_generation": asset.generation if asset is not None else None,
        "asset_binding_fingerprint": asset.binding_fingerprint if asset is not None else None,
        "replacement_asset_type": replacement.asset_type if replacement is not None else None,
        "replacement_asset_id": replacement.asset_id if replacement is not None else None,
        "replacement_asset_generation": replacement.generation if replacement is not None else None,
        "replacement_asset_binding_fingerprint": replacement.binding_fingerprint if replacement is not None else None,
        "recovery_execution_id": effect.recovery_execution_id,
        "recovery_resolution_code": effect.recovery_resolution_code,
    }


def _identifier(value: object, field: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 256
        or any(character in value for character in "\x00\r\n")
    ):
        raise ValueError(f"Custody {field} is invalid")


def _asset_identifier(value: object) -> None:
    if not (
        isinstance(value, str)
        and 1 <= len(value) <= 256
        and value[0] in _ASSET_IDENTIFIER_FIRST_CHARACTERS
        and all(character in _ASSET_IDENTIFIER_CHARACTERS for character in value)
    ):
        raise ValueError("Custody asset_id is invalid")


def _sha256(value: object, field: str) -> None:
    if not (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"Custody {field} is invalid")


def _uuid(value: object, field: str) -> None:
    try:
        UUID(str(value))
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError(f"Custody {field} is invalid") from error


def _require_asset_type(asset: CustodyAsset | None, expected: str) -> None:
    if not isinstance(asset, CustodyAsset) or asset.asset_type != expected:
        raise ValueError("Custody lifecycle asset is invalid")


def _require_none(*values: object) -> None:
    if any(value is not None for value in values):
        raise ValueError("Custody lifecycle operation fields are invalid")
