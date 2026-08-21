"""Request-time resolution of immutable tenant policy versions."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import threading

from .config import (
    GatewayConfig,
    Identity,
    ResolvedSCIMGroupAuthorization,
    configuration_from_policy_projection,
)
from .policy_projection import policy_projection, policy_projection_sha256
from .postgres_policy_store import ActivePolicy, PolicyAdminError, PostgresPolicyStore


@dataclass(frozen=True)
class ResolvedPolicy:
    config: GatewayConfig
    version_id: str


class PolicyRuntime:
    """Resolve the active pointer on every request and cache immutable materializations."""

    def __init__(
        self,
        base: GatewayConfig,
        store: PostgresPolicyStore | None,
        *,
        cache_size: int = 64,
    ):
        self.base = base
        self.store = store
        self.cache_size = cache_size
        self._cache: OrderedDict[tuple[str, str], GatewayConfig] = OrderedDict()
        self._lock = threading.Lock()

    def resolve(self, identity: Identity) -> ResolvedPolicy:
        active = self.store.active(identity=identity) if self.store is not None else None
        return self._resolve_active(identity.organization_id, active)

    def resolve_for_organization(self, organization_id: str) -> ResolvedPolicy:
        """Resolve policy before a SCIM subject has become an Identity."""

        active = (
            self.store.active_for_organization(organization_id)
            if self.store is not None
            else None
        )
        return self._resolve_active(organization_id, active)

    def _resolve_active(
        self,
        organization_id: str,
        active: object | None,
    ) -> ResolvedPolicy:
        if active is None:
            projection = policy_projection(self.base, organization_id)
            return ResolvedPolicy(
                config=self.base,
                version_id="hpv_v1_" + policy_projection_sha256(projection),
            )
        if not isinstance(active, ActivePolicy):
            raise PolicyAdminError("active_policy_invalid")
        key = (organization_id, active.version_id)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return ResolvedPolicy(cached, active.version_id)
        try:
            candidate = configuration_from_policy_projection(
                self.base,
                active.projection,
                organization_id=organization_id,
            )
            active_schema = active.projection.get("schema")
            if not isinstance(active_schema, str):
                raise PolicyAdminError("active_policy_invalid")
            canonical = policy_projection(
                candidate,
                organization_id,
                schema=active_schema,
            )
            fingerprint = policy_projection_sha256(canonical)
        except Exception as error:
            if isinstance(error, PolicyAdminError):
                raise
            raise PolicyAdminError("active_policy_invalid") from None
        if canonical != active.projection or fingerprint != active.projection_sha256:
            raise PolicyAdminError("active_policy_invalid")
        with self._lock:
            self._cache[key] = candidate
            self._cache.move_to_end(key)
            while len(self._cache) > self.cache_size:
                self._cache.popitem(last=False)
        return ResolvedPolicy(candidate, active.version_id)

    def resolve_scim_group_authorization(
        self,
        organization_id: str,
        scim_group_external_ids: tuple[str, ...],
    ) -> ResolvedSCIMGroupAuthorization:
        resolved = self.resolve_for_organization(organization_id)
        return resolved.config.resolve_scim_group_authorization(
            organization_id,
            scim_group_external_ids,
        )

    def invalidate(self, organization_id: str) -> None:
        with self._lock:
            for key in tuple(self._cache):
                if key[0] == organization_id:
                    del self._cache[key]
