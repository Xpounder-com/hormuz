"""Request-time resolution of immutable tenant policy versions."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import threading

from .config import GatewayConfig, Identity, configuration_from_policy_projection
from .policy_projection import policy_projection, policy_projection_sha256
from .postgres_policy_store import PolicyAdminError, PostgresPolicyStore


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
        if self.store is None:
            projection = policy_projection(self.base, identity.organization_id)
            return ResolvedPolicy(
                config=self.base,
                version_id="hpv_v1_" + policy_projection_sha256(projection),
            )
        active = self.store.active(identity=identity)
        if active is None:
            projection = policy_projection(self.base, identity.organization_id)
            return ResolvedPolicy(
                config=self.base,
                version_id="hpv_v1_" + policy_projection_sha256(projection),
            )
        key = (identity.organization_id, active.version_id)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return ResolvedPolicy(cached, active.version_id)
        try:
            candidate = configuration_from_policy_projection(
                self.base,
                active.projection,
                organization_id=identity.organization_id,
            )
            active_schema = active.projection.get("schema")
            if not isinstance(active_schema, str):
                raise PolicyAdminError("active_policy_invalid")
            canonical = policy_projection(
                candidate,
                identity.organization_id,
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

    def invalidate(self, organization_id: str) -> None:
        with self._lock:
            for key in tuple(self._cache):
                if key[0] == organization_id:
                    del self._cache[key]
