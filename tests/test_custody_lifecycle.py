from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest

from hormuz.contracts import ContractValidationError, validate_custody_lifecycle_event
from hormuz.custody_lifecycle import (
    CustodyAsset,
    CustodyAssetCatalog,
    CustodyLifecycleEffect,
    CustodyLifecycleError,
    CustodyLifecycleConfig,
    CustodyProjectionBarrier,
    CustodyProjectionCoordinationSnapshot,
    CustodyProjectionSnapshot,
    binding_fingerprint,
    build_custody_lifecycle_event,
)
from hormuz.custody_runtime_projection import CustodyRuntimeProjection, CustodyRuntimeProjectionError
from hormuz.postgres import PostgresStorageError


_NOW = datetime(2026, 8, 23, tzinfo=timezone.utc)
_TARGET_DIGEST = "0" * 64
_PARAMETERS_DIGEST = "1" * 64
_EVENT_ID = "12345678-89ab-4def-8123-456789abcdef"
_EXECUTION_ID = "22345678-89ab-4def-8123-456789abcdef"
_OPERATION_ID = "32345678-89ab-4def-8123-456789abcdef"


def _asset(
    *,
    organization_id: str = "acme",
    asset_type: str,
    asset_id: str,
    generation: int = 1,
    binding: dict[str, str],
) -> CustodyAsset:
    return CustodyAsset(
        organization_id=organization_id,
        asset_type=asset_type,
        asset_id=asset_id,
        generation=generation,
        binding_fingerprint=binding_fingerprint(
            organization_id=organization_id,
            asset_type=asset_type,
            asset_id=asset_id,
            generation=generation,
            binding=binding,
        ),
        binding=binding,
    )


class _ProjectionStore:
    def __init__(self, snapshot: CustodyProjectionSnapshot) -> None:
        self.snapshot = snapshot
        self.calls = 0
        self.catalog_checks = 0
        self.unavailable = False
        self.barriers: tuple[CustodyProjectionBarrier, ...] = ()
        self.acknowledged: list[str] = []
        self.on_ack = None

    def verify_catalog(self, *, organization_id: str, catalog: CustodyAssetCatalog) -> None:
        self.catalog_checks += 1
        if organization_id != self.snapshot.organization_id or not catalog.assets:
            raise AssertionError("unexpected catalog")

    def synchronize(
        self,
        *,
        organization_id: str,
        replica_id: str,
        observed_projection_version: int,
    ) -> CustodyProjectionCoordinationSnapshot:
        del replica_id, observed_projection_version
        self.calls += 1
        if self.unavailable:
            raise PostgresStorageError("custody_runtime_projection_unavailable")
        if organization_id != self.snapshot.organization_id:
            raise AssertionError("unexpected tenant")
        return CustodyProjectionCoordinationSnapshot(projection=self.snapshot, barriers=self.barriers)

    def acknowledge(
        self,
        *,
        organization_id: str,
        replica_id: str,
        barrier_id: str,
        observed_projection_version: int,
    ) -> None:
        del organization_id, replica_id, observed_projection_version
        if self.on_ack is not None:
            self.on_ack()
        self.acknowledged.append(barrier_id)

    def retire_replica(self, *, organization_id: str, replica_id: str) -> None:
        del organization_id, replica_id


class CustodyLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = _asset(
            asset_type="provider_credential",
            asset_id="openai-primary",
            binding={"protocol": "openai", "source": "env:OPENAI_API_KEY"},
        )
        self.old_key = _asset(
            asset_type="key_reference",
            asset_id="provider-old",
            binding={"purpose": "provider_credential", "key_reference": "customer-key-old"},
        )
        self.replacement_key = _asset(
            asset_type="key_reference",
            asset_id="provider-current",
            generation=2,
            binding={"purpose": "provider_credential", "key_reference": "customer-key-current"},
        )
        self.envelope = _asset(
            asset_type="envelope",
            asset_id="openai-envelope",
            binding={
                "path": "/private/customer/openai.envelope",
                "provider_credential_asset": "openai-primary@1",
                "key_reference_asset": "provider-old@1",
            },
        )
        self.catalog = CustodyAssetCatalog((self.provider, self.old_key, self.replacement_key, self.envelope))

    def test_lifecycle_evidence_is_hash_linked_and_never_contains_a_binding(self) -> None:
        event = build_custody_lifecycle_event(
            organization_id="acme",
            lifecycle_event_id=_EVENT_ID,
            execution_id=_EXECUTION_ID,
            operation_id=_OPERATION_ID,
            occurred_at=_NOW,
            effect=CustodyLifecycleEffect(
                operation_type="retire_key_reference",
                asset=self.old_key,
                replacement_asset=self.replacement_key,
            ),
            target_sha256=_TARGET_DIGEST,
            parameters_sha256=_PARAMETERS_DIGEST,
            chain_version=1,
            sequence=1,
            previous_digest=None,
        )

        record = event.contract_record()
        validate_custody_lifecycle_event(record)
        serialized = str(record)
        self.assertNotIn("/private/customer/openai.envelope", serialized)
        self.assertNotIn("customer-key-old", serialized)
        self.assertIn(self.old_key.asset_id, serialized)
        self.assertIn(self.old_key.binding_fingerprint, serialized)
        with self.assertRaises(ContractValidationError):
            validate_custody_lifecycle_event({**record, "path": "/private/customer/openai.envelope"})

    def test_key_retirement_cannot_cross_tenants_or_reuse_its_asset_generation(self) -> None:
        other_tenant_replacement = _asset(
            organization_id="other",
            asset_type="key_reference",
            asset_id="provider-current",
            generation=2,
            binding={"purpose": "provider_credential", "key_reference": "other-key"},
        )
        with self.assertRaises(ValueError):
            CustodyLifecycleEffect(
                operation_type="retire_key_reference",
                asset=self.old_key,
                replacement_asset=other_tenant_replacement,
            )
        with self.assertRaises(ValueError):
            CustodyLifecycleEffect(
                operation_type="retire_key_reference",
                asset=self.old_key,
                replacement_asset=self.old_key,
            )
        with self.assertRaises(ValueError):
            CustodyLifecycleEffect(
                operation_type="retire_key_reference",
                asset=self.old_key,
                replacement_asset=_asset(
                    asset_type="key_reference",
                    asset_id="data-encryption-current",
                    binding={"purpose": "data_encryption", "key_reference": "customer-data-key"},
                ),
            )

    def test_asset_ids_are_opaque_identifiers_not_paths_or_key_references(self) -> None:
        with self.assertRaises(ValueError):
            _asset(
                asset_type="provider_credential",
                asset_id="/private/customer/openai.key",
                binding={"protocol": "openai", "source": "env:OPENAI_API_KEY"},
            )

    def test_gateway_uses_a_fresh_leased_projection_without_a_database_read_per_request(self) -> None:
        lifecycle = CustodyLifecycleConfig(freshness_lease_seconds=5, assets=CustodyAssetCatalog((self.provider,)))
        snapshot = CustodyProjectionSnapshot(
            organization_id="acme",
            version=0,
            committed_at=_NOW,
            restrictions={},
        )
        store = _ProjectionStore(snapshot)
        config = SimpleNamespace(
            custody_lifecycle=lifecycle,
            organization_ids=("acme",),
            upstreams={"openai": SimpleNamespace(api_key_envelope_path=None)},
        )
        projection = CustodyRuntimeProjection(config, projection_store=store, start_background=False)

        self.assertEqual(store.calls, 1, "startup obtains one fresh snapshot")
        self.assertEqual(store.catalog_checks, 1, "startup verifies every configured asset identity")
        projection.require_provider_usable(organization_id="acme", protocol="openai")
        projection.require_provider_usable(organization_id="acme", protocol="openai")
        self.assertEqual(store.calls, 1, "a valid lease serves normal requests locally")

        store.unavailable = True
        projection._synchronize_all()
        with self.assertRaises(CustodyRuntimeProjectionError) as raised:
            projection.require_provider_usable(organization_id="acme", protocol="openai")
        self.assertEqual(raised.exception.code, "custody_runtime_projection_unavailable")
        self.assertFalse(projection.readiness_healthy())

    def test_projection_refuses_a_restriction_for_another_tenant(self) -> None:
        snapshot = CustodyProjectionSnapshot(
            organization_id="acme",
            version=1,
            committed_at=_NOW,
            restrictions={},
        )
        with self.assertRaises(CustodyLifecycleError):
            snapshot.restriction_for(
                _asset(
                    organization_id="other",
                    asset_type="provider_credential",
                    asset_id="provider",
                    binding={"protocol": "openai", "source": "env:OTHER"},
                )
            )

    def test_prepared_barrier_is_installed_locally_before_replica_acknowledgement(self) -> None:
        lifecycle = CustodyLifecycleConfig(freshness_lease_seconds=5, assets=CustodyAssetCatalog((self.provider,)))
        snapshot = CustodyProjectionSnapshot(
            organization_id="acme",
            version=0,
            committed_at=_NOW,
            restrictions={},
        )
        store = _ProjectionStore(snapshot)
        config = SimpleNamespace(
            custody_lifecycle=lifecycle,
            organization_ids=("acme",),
            upstreams={"openai": SimpleNamespace(api_key_envelope_path=None)},
        )
        projection = CustodyRuntimeProjection(config, projection_store=store, start_background=False)
        store.barriers = (
            CustodyProjectionBarrier(
                organization_id="acme",
                barrier_id="42345678-89ab-4def-8123-456789abcdef",
                execution_id=_EXECUTION_ID,
                proposed_version=1,
                asset_type=self.provider.asset_type,
                asset_id=self.provider.asset_id,
                generation=self.provider.generation,
                binding_fingerprint=self.provider.binding_fingerprint,
                restriction_kind="provider_credential_disabled",
                prepared_at=_NOW,
            ),
        )

        def assert_barrier_already_installed() -> None:
            with self.assertRaises(CustodyRuntimeProjectionError) as raised:
                projection.require_provider_usable(organization_id="acme", protocol="openai")
            self.assertEqual(raised.exception.code, "custody_provider_credential_disabled")

        store.on_ack = assert_barrier_already_installed
        projection._synchronize("acme")
        self.assertEqual(store.acknowledged, [store.barriers[0].barrier_id])


if __name__ == "__main__":
    unittest.main()
