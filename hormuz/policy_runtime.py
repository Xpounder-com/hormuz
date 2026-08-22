"""Resolve the single policy snapshot pinned at gateway request start."""

from __future__ import annotations

import os
from typing import Mapping

from .config import GatewayConfig, Identity
from .policy_document import PolicyDocumentError, PolicySnapshot, local_policy_snapshot
from .postgres import PostgresConnectionPool, PostgresStorageError
from .postgres_policy_store import PostgresPolicyRuntimeStore


class PolicyRuntime:
    """Read local policy only in local mode; otherwise read PostgreSQL per request.

    There is intentionally no process-local managed-policy cache. The active
    pointer is small and request-time reads make every gateway instance converge
    immediately after the activation transaction commits.
    """

    def __init__(
        self,
        config: GatewayConfig,
        *,
        environ: Mapping[str, str] | None = None,
        connection_pool: PostgresConnectionPool | None = None,
    ) -> None:
        self._config = config
        self._store: PostgresPolicyRuntimeStore | None = None
        if config.policy_control.mode == "local":
            return
        if config.policy_control.mode != "postgresql":  # Parsing rejects this path.
            raise PostgresStorageError("policy_control_mode_unsupported")
        environment = os.environ if environ is None else environ
        dsn = environment.get(config.usage_storage.postgres_dsn_env, "")
        if not dsn:
            raise PostgresStorageError("postgres_dsn_unavailable")
        self._store = PostgresPolicyRuntimeStore(
            dsn,
            config=config,
            schema=config.usage_storage.postgres_schema,
            runtime_role=config.usage_storage.postgres_runtime_role,
            connection_pool=connection_pool,
        )

    def snapshot_for(self, identity: Identity) -> PolicySnapshot:
        if self._store is None:
            return local_policy_snapshot(self._config, identity)
        record = self._store.active_version(organization_id=identity.organization_id)
        if record.version_id != record.document.version_id or record.content_sha256 != record.document.content_sha256:
            raise PostgresStorageError("policy_document_invalid")
        try:
            return record.document.snapshot_for(identity)
        except PolicyDocumentError as error:
            raise PostgresStorageError(error.code) from None

    def verify_active_policies(self) -> None:
        """Fail startup/doctor if a managed tenant lacks a safe active version."""

        if self._store is None:
            return
        for organization_id in self._config.organization_ids:
            self._store.active_version(organization_id=organization_id)
