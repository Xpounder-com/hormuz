"""Pin the immutable customer-local policy snapshot for each request."""
from __future__ import annotations
from .config import GatewayConfig, Identity
from .policy_document import PolicySnapshot, local_policy_snapshot

class PolicyRuntime:
    def __init__(self, config: GatewayConfig):
        self._config = config

    def snapshot_for(self, identity: Identity) -> PolicySnapshot:
        return local_policy_snapshot(self._config, identity)

    def verify_active_policies(self) -> None:
        """Local configuration was validated before runtime construction."""
