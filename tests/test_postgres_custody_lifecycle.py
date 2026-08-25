from __future__ import annotations

import http.client
import json
import os
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from hormuz.custody_control import CustodyControlService
from hormuz.custody_execution_repository import CustodyExecutionError, CustodyExecutionRequest, CustodyExecutionResult
from hormuz.custody_executor import (
    CustodyExecutionAmbiguous,
    CustodyExecutorService,
    LifecycleCustodyOperationRunner,
)
from hormuz.custody_lifecycle import (
    CustodyAssetCatalog,
    CustodyEnvelopeAttestation,
    CustodyLifecycleConfig,
    CustodyLifecycleError,
    binding_fingerprint,
)
from hormuz.custody_runtime_projection import CustodyRuntimeProjection, CustodyRuntimeProjectionError
from hormuz.postgres import PostgresStorageError
from hormuz.postgres_custody_lifecycle_store import PostgresCustodyProjectionStore
from hormuz.postgres_usage_store import PostgresUsageStore
from hormuz.server import GatewayServer, serve_in_thread

if __package__:
    from ._postgres_fixture import PostgresTestCase, _free_port
else:  # Isolated wheel compatibility discovery uses the tests directory as its import root.
    from _postgres_fixture import PostgresTestCase, _free_port


class _Resolver:
    def resolve(self, *, organization_id: str, reference: str) -> bytes:
        del organization_id, reference
        return b"lifecycle-test-secret"


class _LifecycleRunner:
    def __init__(self, config) -> None:
        self._delegate = LifecycleCustodyOperationRunner(config)
        self.requests: list[CustodyExecutionRequest] = []

    def execute(self, request: CustodyExecutionRequest):
        self.requests.append(request)
        return self._delegate.execute(request)


class _ResultRunner:
    def __init__(self, result: CustodyExecutionResult) -> None:
        self._result = result
        self.requests: list[CustodyExecutionRequest] = []

    def execute(self, request: CustodyExecutionRequest) -> CustodyExecutionResult:
        self.requests.append(request)
        return self._result


class _AmbiguousRunner:
    def execute(self, request: CustodyExecutionRequest) -> CustodyExecutionResult:
        del request
        raise CustodyExecutionAmbiguous("simulated-provider-ambiguity")


class _DisconnectingProjectionStore:
    def __init__(self, delegate: PostgresCustodyProjectionStore) -> None:
        self._delegate = delegate
        self.unavailable = False

    def verify_catalog(self, **kwargs):
        return self._delegate.verify_catalog(**kwargs)

    def synchronize(self, **kwargs):
        if self.unavailable:
            raise PostgresStorageError("storage_unavailable")
        return self._delegate.synchronize(**kwargs)

    def acknowledge(self, **kwargs):
        if self.unavailable:
            raise PostgresStorageError("storage_unavailable")
        return self._delegate.acknowledge(**kwargs)

    def retire_replica(self, **kwargs):
        return self._delegate.retire_replica(**kwargs)


class PostgresCustodyLifecycleTests(PostgresTestCase):
    def test_two_person_disablement_appends_metadata_only_evidence_and_projects_atomically(self) -> None:
        config, environment, _issuer = self._managed_custody_config(lifecycle=True)
        control = CustodyControlService(config, environ=environment)
        control.bootstrap(organization_id="xpounder", credential_env="HORMUZ_CUSTODY_ADMIN_TOKEN")
        assert config.custody_lifecycle is not None
        provider = config.custody_lifecycle.assets.asset(
            organization_id="xpounder",
            asset_type="provider_credential",
            asset_id="openai-primary",
            generation=1,
        )
        request = self._authorized_lifecycle_request(
            control,
            operation_type="disable_provider_credential",
            target=provider.audit_ref(),
            parameters={},
        )
        runner = _LifecycleRunner(config)
        executor = CustodyExecutorService(
            config,
            protected_input_resolver=_Resolver(),
            environ=environment,
            runner=runner,
        )
        executor.register_asset_catalog()

        attempt = executor.execute(request=request)
        self.assertEqual(attempt.execution_schema_version, 2)
        self.assertEqual(attempt.state, "succeeded")
        self.assertEqual(runner.requests, [request])

        # v2 custody entries are verified through the runtime's bounded source
        # reader; it has no direct access to custody-control tables.
        custody_auditor = PostgresUsageStore(
            self.runtime_dsn,
            organization_ids=("xpounder",),
            schema=self.schema,
            runtime_role=self.runtime_role,
        )
        chain_head = custody_auditor.verify_audit_chain(organization_id="xpounder")
        self.assertGreater(chain_head.sequence, 0)

        projection = PostgresCustodyProjectionStore(
            self.runtime_dsn,
            schema=self.schema,
            runtime_role=self.runtime_role,
        ).load(organization_id="xpounder")
        self.assertEqual(projection.version, 1)
        self.assertEqual(projection.restriction_for(provider), "provider_credential_disabled")
        runtime_projection = CustodyRuntimeProjection(config, environ=environment, start_background=False)
        self.addCleanup(runtime_projection.close)
        with self.assertRaises(CustodyRuntimeProjectionError) as raised:
            runtime_projection.require_provider_usable(organization_id="xpounder", protocol="openai")
        self.assertEqual(raised.exception.code, "custody_provider_credential_disabled")

        with self.psycopg.connect(self.owner_dsn) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        self.sql.SQL(
                            "SELECT operation_type, asset_id, asset_binding_fingerprint, event_digest "
                            "FROM {}.custody_lifecycle_events"
                        ).format(self.sql.Identifier(self.schema))
                    )
                    row = cursor.fetchone()
        assert row is not None
        self.assertEqual(row[0], "disable_provider_credential")
        serialized = json.dumps(
            {
                "operation_type": row[0],
                "asset_id": row[1],
                "asset_binding_fingerprint": row[2],
                "event_digest": row[3],
            },
            sort_keys=True,
        )
        self.assertNotIn("OPENAI_API_KEY", serialized)
        self.assertNotIn("provider-key", serialized)
        self.assertNotIn("/private/", serialized)

        for role, statement in (
            (self.runtime_role, "UPDATE custody_runtime_projection_restrictions SET restriction_kind = restriction_kind"),
            (self.runtime_role, "UPDATE custody_runtime_replicas SET heartbeat_at = heartbeat_at"),
            (self.custody_control_role, "UPDATE custody_lifecycle_events SET event_digest = event_digest"),
            (self.custody_control_role, "DELETE FROM custody_runtime_projection_acks"),
            (self.custody_executor_role, "DELETE FROM custody_runtime_projection_restrictions"),
            (self.custody_executor_role, "UPDATE custody_runtime_projection_barriers SET prepared_at = prepared_at"),
        ):
            with self.subTest(role=role):
                with self.psycopg.connect(self.owner_dsn) as connection:
                    with self.assertRaises(self.psycopg.Error):
                        with connection.transaction():
                            with connection.cursor() as cursor:
                                self._set_role_and_tenant(cursor, role, "xpounder")
                                cursor.execute(statement)

        with self.psycopg.connect(self.owner_dsn) as connection:
            with self.assertRaises(self.psycopg.Error):
                with connection.transaction():
                    with connection.cursor() as cursor:
                        self._set_role_and_tenant(cursor, self.runtime_role, "other")
                        cursor.execute(
                            "SELECT custody_runtime_sync_replica(%s, %s::uuid, 0)",
                            ("xpounder", "40000000-0000-4000-8000-000000000004"),
                        )

    def test_two_live_replicas_acknowledge_before_atomic_restriction_activation(self) -> None:
        config, environment, _issuer = self._managed_custody_config(lifecycle=True)
        control = CustodyControlService(config, environ=environment)
        control.bootstrap(organization_id="xpounder", credential_env="HORMUZ_CUSTODY_ADMIN_TOKEN")
        registrar = CustodyExecutorService(config, environ=environment)
        registrar.register_asset_catalog()
        first = CustodyRuntimeProjection(
            config,
            environ=environment,
            replica_id="10000000-0000-4000-8000-000000000001",
        )
        second = CustodyRuntimeProjection(
            config,
            environ=environment,
            replica_id="20000000-0000-4000-8000-000000000002",
        )
        self.addCleanup(second.close)
        self.addCleanup(first.close)
        self.assertTrue(first.readiness_healthy())
        self.assertTrue(second.readiness_healthy())

        assert config.custody_lifecycle is not None
        provider = config.custody_lifecycle.assets.asset(
            organization_id="xpounder",
            asset_type="provider_credential",
            asset_id="openai-primary",
            generation=1,
        )
        request = self._authorized_lifecycle_request(
            control,
            operation_type="disable_provider_credential",
            target=provider.audit_ref(),
            parameters={},
        )
        completed = CustodyExecutorService(
            config,
            environ=environment,
            runner=_LifecycleRunner(config),
        ).execute(request=request)
        self.assertEqual(completed.state, "succeeded")

        for replica in (first, second):
            with self.assertRaises(CustodyRuntimeProjectionError) as raised:
                replica.require_provider_usable(organization_id="xpounder", protocol="openai")
            self.assertEqual(raised.exception.code, "custody_provider_credential_disabled")

        with self.psycopg.connect(self.owner_dsn) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        self.sql.SQL(
                            "SELECT barrier.activated_at, barrier.lifecycle_event_id, "
                            "COUNT(ack.replica_id) "
                            "FROM {}.custody_runtime_projection_barriers AS barrier "
                            "JOIN {}.custody_runtime_projection_acks AS ack "
                            "ON ack.organization_id = barrier.organization_id "
                            "AND ack.barrier_id = barrier.barrier_id "
                            "WHERE barrier.organization_id = %s "
                            "GROUP BY barrier.activated_at, barrier.lifecycle_event_id"
                        ).format(self.sql.Identifier(self.schema), self.sql.Identifier(self.schema)),
                        ("xpounder",),
                    )
                    row = cursor.fetchone()
        assert row is not None
        self.assertIsNotNone(row[0])
        self.assertIsNotNone(row[1])
        self.assertEqual(row[2], 2)

    def test_partitioned_replica_fences_locally_before_its_lease_can_be_excluded(self) -> None:
        config, environment, _issuer = self._managed_custody_config(lifecycle=True)
        control = CustodyControlService(config, environ=environment)
        control.bootstrap(organization_id="xpounder", credential_env="HORMUZ_CUSTODY_ADMIN_TOKEN")
        CustodyExecutorService(config, environ=environment).register_asset_catalog()
        store = _DisconnectingProjectionStore(
            PostgresCustodyProjectionStore(
                self.runtime_dsn,
                schema=self.schema,
                runtime_role=self.runtime_role,
            )
        )
        replica = CustodyRuntimeProjection(
            config,
            projection_store=store,
            start_background=False,
            replica_id="30000000-0000-4000-8000-000000000003",
        )
        self.addCleanup(replica.close)
        store.unavailable = True
        replica._synchronize_all()
        self.assertFalse(replica.readiness_healthy())
        with self.assertRaises(CustodyRuntimeProjectionError) as unavailable:
            replica.require_provider_usable(organization_id="xpounder", protocol="openai")
        self.assertEqual(unavailable.exception.code, "custody_runtime_projection_unavailable")

        assert config.custody_lifecycle is not None
        provider = config.custody_lifecycle.assets.asset(
            organization_id="xpounder",
            asset_type="provider_credential",
            asset_id="openai-primary",
            generation=1,
        )
        request = self._authorized_lifecycle_request(
            control,
            operation_type="disable_provider_credential",
            target=provider.audit_ref(),
            parameters={},
        )
        started = time.monotonic()
        completed = CustodyExecutorService(
            config,
            environ=environment,
            runner=_LifecycleRunner(config),
        ).execute(request=request)
        elapsed = time.monotonic() - started
        self.assertEqual(completed.state, "succeeded")
        self.assertGreaterEqual(elapsed, 4.0)
        self.assertLess(elapsed, 7.0)

        with self.psycopg.connect(self.owner_dsn) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        self.sql.SQL(
                            "SELECT barrier.activated_at, COUNT(ack.replica_id), "
                            "bool_and(replica.lease_expires_at <= clock_timestamp()) "
                            "FROM {}.custody_runtime_projection_barriers AS barrier "
                            "LEFT JOIN {}.custody_runtime_projection_acks AS ack "
                            "ON ack.organization_id = barrier.organization_id "
                            "AND ack.barrier_id = barrier.barrier_id "
                            "JOIN {}.custody_runtime_replicas AS replica "
                            "ON replica.organization_id = barrier.organization_id "
                            "WHERE barrier.organization_id = %s "
                            "GROUP BY barrier.activated_at"
                        ).format(
                            self.sql.Identifier(self.schema),
                            self.sql.Identifier(self.schema),
                            self.sql.Identifier(self.schema),
                        ),
                        ("xpounder",),
                    )
                    row = cursor.fetchone()
        assert row is not None
        self.assertIsNotNone(row[0])
        self.assertEqual(row[1], 0)
        self.assertTrue(row[2])

    def test_revoking_one_destructive_approver_blocks_the_machine_before_lifecycle_execution(self) -> None:
        config, environment, _issuer = self._managed_custody_config(lifecycle=True)
        control = CustodyControlService(config, environ=environment)
        control.bootstrap(organization_id="xpounder", credential_env="HORMUZ_CUSTODY_ADMIN_TOKEN")
        assert config.custody_lifecycle is not None
        provider = config.custody_lifecycle.assets.asset(
            organization_id="xpounder",
            asset_type="provider_credential",
            asset_id="openai-primary",
            generation=1,
        )
        request = self._authorized_lifecycle_request(
            control,
            operation_type="disable_provider_credential",
            target=provider.audit_ref(),
            parameters={},
        )
        control.revoke_static_administrator(
            organization_id="xpounder",
            credential_env="HORMUZ_CUSTODY_ADMIN_TOKEN",
            actor_id="bob",
        )
        runner = _LifecycleRunner(config)
        executor = CustodyExecutorService(
            config,
            protected_input_resolver=_Resolver(),
            environ=environment,
            runner=runner,
        )

        with self.assertRaises(CustodyExecutionError) as raised:
            executor.execute(request=request)
        self.assertEqual(raised.exception.code, "custody_execution_active_approvers_required")
        self.assertEqual(runner.requests, [])

    def test_committed_disablement_denies_a_new_gateway_request_before_provider_egress(self) -> None:
        config, environment, _issuer = self._managed_custody_config(lifecycle=True)
        control = CustodyControlService(config, environ=environment)
        control.bootstrap(organization_id="xpounder", credential_env="HORMUZ_CUSTODY_ADMIN_TOKEN")
        assert config.custody_lifecycle is not None
        provider = config.custody_lifecycle.assets.asset(
            organization_id="xpounder",
            asset_type="provider_credential",
            asset_id="openai-primary",
            generation=1,
        )
        request = self._authorized_lifecycle_request(
            control,
            operation_type="disable_provider_credential",
            target=provider.audit_ref(),
            parameters={},
        )
        executor = CustodyExecutorService(
            config,
            environ=environment,
            runner=_LifecycleRunner(config),
        )
        self.assertEqual(executor.execute(request=request).state, "succeeded")

        gateway: GatewayServer | None = None
        gateway_thread: threading.Thread | None = None
        runtime_config = replace(config, listen=replace(config.listen, port=_free_port()))
        runtime_environment = {
            **environment,
            "OPENAI_API_KEY": "lifecycle-openai-provider-key",
            "ANTHROPIC_API_KEY": "lifecycle-anthropic-provider-key",
        }
        try:
            with (
                mock.patch.dict(os.environ, runtime_environment, clear=False),
                mock.patch("hormuz.server.urllib.request.urlopen") as provider_egress,
            ):
                gateway = GatewayServer(runtime_config)
                gateway_thread = serve_in_thread(gateway)
                connection = http.client.HTTPConnection("127.0.0.1", gateway.server_port, timeout=5)
                try:
                    connection.request(
                        "POST",
                        "/v1/responses",
                        body=json.dumps({"model": "gpt-5.4-mini", "input": "must not egress"}),
                        headers={
                            "Authorization": "Bearer custody-test-alice-token",
                            "Content-Type": "application/json",
                        },
                    )
                    response = connection.getresponse()
                    body = response.read()
                finally:
                    connection.close()
                self.assertEqual(response.status, 403, body)
                self.assertEqual(json.loads(body)["error"]["code"], "hormuz_custody_restricted")
                provider_egress.assert_not_called()
        finally:
            if gateway is not None and gateway_thread is not None:
                gateway.shutdown()
            if gateway is not None:
                gateway.server_close()
            if gateway_thread is not None:
                gateway_thread.join(timeout=10)
                self.assertFalse(gateway_thread.is_alive())

    def test_retired_envelope_blocks_runtime_selection(self) -> None:
        config, environment, _issuer = self._managed_custody_config(
            lifecycle=True,
            include_retirement_fixture_assets=True,
        )
        control = CustodyControlService(config, environ=environment)
        control.bootstrap(organization_id="xpounder", credential_env="HORMUZ_CUSTODY_ADMIN_TOKEN")
        assert config.custody_lifecycle is not None
        envelope = config.custody_lifecycle.assets.asset(
            organization_id="xpounder",
            asset_type="envelope",
            asset_id="openai-primary-envelope",
            generation=1,
        )
        request = self._authorized_lifecycle_request(
            control,
            operation_type="retire_envelope",
            target=envelope.audit_ref(),
            parameters={},
        )
        executor = CustodyExecutorService(
            config,
            environ=environment,
            runner=_LifecycleRunner(config),
        )
        self.assertEqual(executor.execute(request=request).state, "succeeded")

        runtime_config = replace(
            config,
            listen=replace(config.listen, port=_free_port()),
            upstreams={
                **config.upstreams,
                "openai": replace(
                    config.upstreams["openai"],
                    api_key_env=None,
                    api_key_envelope_path=Path(envelope.binding["path"]),
                ),
            },
        )
        projection = CustodyRuntimeProjection(runtime_config, environ=environment, start_background=False)
        self.addCleanup(projection.close)
        with self.assertRaises(CustodyRuntimeProjectionError) as raised:
            projection.require_provider_usable(organization_id="xpounder", protocol="openai")
        self.assertEqual(raised.exception.code, "custody_envelope_retired")

        gateway: GatewayServer | None = None
        try:
            with (
                mock.patch.dict(os.environ, {**environment, "ANTHROPIC_API_KEY": "test-anthropic-key"}, clear=False),
                mock.patch("hormuz.custody_runtime.read_envelope_file") as read_envelope,
            ):
                gateway = GatewayServer(runtime_config)
            read_envelope.assert_not_called()
        finally:
            if gateway is not None:
                gateway.server_close()

    def test_key_retirement_without_rewrap_and_restore_proof_leaves_no_terminal_event_or_projection(self) -> None:
        config, environment, _issuer = self._managed_custody_config(
            lifecycle=True,
            include_retirement_fixture_assets=True,
        )
        control = CustodyControlService(config, environ=environment)
        control.bootstrap(organization_id="xpounder", credential_env="HORMUZ_CUSTODY_ADMIN_TOKEN")
        assert config.custody_lifecycle is not None
        old_key = config.custody_lifecycle.assets.asset(
            organization_id="xpounder",
            asset_type="key_reference",
            asset_id="provider-credential-prior",
            generation=1,
        )
        replacement_key = config.custody_lifecycle.assets.asset(
            organization_id="xpounder",
            asset_type="key_reference",
            asset_id="provider_credential-current",
            generation=1,
        )
        request = self._authorized_lifecycle_request(
            control,
            operation_type="retire_key_reference",
            target=old_key.audit_ref(),
            parameters={"replacement_asset": replacement_key.audit_ref()},
        )
        executor = CustodyExecutorService(
            config,
            protected_input_resolver=_Resolver(),
            environ=environment,
            runner=_LifecycleRunner(config),
        )

        with self.assertRaises(CustodyExecutionError) as raised:
            executor.execute(request=request)
        self.assertEqual(raised.exception.code, "custody_execution_finalization_unavailable")

        status = control.status(organization_id="xpounder", credential_env="HORMUZ_CUSTODY_ADMIN_TOKEN")
        assert status.execution_status is not None
        self.assertEqual(status.execution_status.attempts[0].state, "pending")
        projection = PostgresCustodyProjectionStore(
            self.runtime_dsn,
            schema=self.schema,
            runtime_role=self.runtime_role,
        ).load(organization_id="xpounder")
        self.assertEqual(projection.version, 0)
        self.assertIsNone(projection.restriction_for(old_key))

    def test_key_retirement_requires_attested_rewrap_and_restore_then_write_retires_only_old_key(self) -> None:
        config, environment, _issuer = self._managed_custody_config(
            lifecycle=True,
            include_retirement_fixture_assets=True,
        )
        control = CustodyControlService(config, environ=environment)
        control.bootstrap(organization_id="xpounder", credential_env="HORMUZ_CUSTODY_ADMIN_TOKEN")
        assert config.custody_lifecycle is not None
        old_key = config.custody_lifecycle.assets.asset(
            organization_id="xpounder",
            asset_type="key_reference",
            asset_id="provider-credential-prior",
            generation=1,
        )
        replacement_key = config.custody_lifecycle.assets.asset(
            organization_id="xpounder",
            asset_type="key_reference",
            asset_id="provider_credential-current",
            generation=1,
        )
        envelope = config.custody_lifecycle.assets.asset(
            organization_id="xpounder",
            asset_type="envelope",
            asset_id="openai-primary-envelope",
            generation=1,
        )
        registrar = CustodyExecutorService(config, environ=environment)
        registrar.register_asset_catalog()

        rewrap_request = self._authorized_request(
            control,
            operation_type="rewrap_envelope",
            target={"path": envelope.binding["path"]},
            parameters={"source_path": "/private/retired-input.envelope"},
        )
        rewrap_runner = _ResultRunner(
            CustodyExecutionResult(
                envelope_attestation=CustodyEnvelopeAttestation(
                    kind="rewrapped",
                    envelope_asset=envelope,
                    source_key_asset=old_key,
                    destination_key_asset=replacement_key,
                )
            )
        )
        rewrap_executor = CustodyExecutorService(config, environ=environment, runner=rewrap_runner)
        self.assertEqual(rewrap_executor.execute(request=rewrap_request).state, "succeeded")

        restore_request = self._authorized_request(
            control,
            operation_type="verify_restore",
            target={"path": envelope.binding["path"]},
            parameters={},
        )
        restore_runner = _ResultRunner(
            CustodyExecutionResult(
                envelope_attestation=CustodyEnvelopeAttestation(
                    kind="restore_verified",
                    envelope_asset=envelope,
                    destination_key_asset=replacement_key,
                )
            )
        )
        restore_executor = CustodyExecutorService(config, environ=environment, runner=restore_runner)
        self.assertEqual(restore_executor.execute(request=restore_request).state, "succeeded")

        retirement_request = self._authorized_lifecycle_request(
            control,
            operation_type="retire_key_reference",
            target=old_key.audit_ref(),
            parameters={"replacement_asset": replacement_key.audit_ref()},
        )
        retirement_executor = CustodyExecutorService(
            config,
            environ=environment,
            runner=_LifecycleRunner(config),
        )
        retired = retirement_executor.execute(request=retirement_request)
        self.assertEqual(retired.state, "succeeded")

        projection = PostgresCustodyProjectionStore(
            self.runtime_dsn,
            schema=self.schema,
            runtime_role=self.runtime_role,
        ).load(organization_id="xpounder")
        self.assertEqual(projection.restriction_for(old_key), "key_reference_write_retired")
        self.assertIsNone(projection.restriction_for(replacement_key))

        blocked_rewrap = self._authorized_request(
            control,
            operation_type="rewrap_envelope",
            target={"path": envelope.binding["path"]},
            parameters={"source_path": "/private/second-retired-input.envelope"},
        )
        blocked_runner = _ResultRunner(CustodyExecutionResult())
        blocked_executor = CustodyExecutorService(config, environ=environment, runner=blocked_runner)
        with self.assertRaises(CustodyExecutionError) as raised:
            blocked_executor.execute(request=blocked_rewrap)
        self.assertEqual(raised.exception.code, "custody_key_reference_write_retired")
        self.assertEqual(blocked_runner.requests, [])

    def test_recovery_resolution_appends_a_new_event_without_rewriting_unknown_attempt(self) -> None:
        config, environment, _issuer = self._managed_custody_config(lifecycle=True)
        control = CustodyControlService(config, environ=environment)
        control.bootstrap(organization_id="xpounder", credential_env="HORMUZ_CUSTODY_ADMIN_TOKEN")
        assert config.custody_lifecycle is not None
        provider = config.custody_lifecycle.assets.asset(
            organization_id="xpounder",
            asset_type="provider_credential",
            asset_id="openai-primary",
            generation=1,
        )
        ambiguous_request = self._authorized_lifecycle_request(
            control,
            operation_type="disable_provider_credential",
            target=provider.audit_ref(),
            parameters={},
        )
        executor = CustodyExecutorService(config, environ=environment, runner=_AmbiguousRunner())
        executor.register_asset_catalog()
        with self.assertRaises(CustodyExecutionError) as raised:
            executor.execute(request=ambiguous_request)
        self.assertEqual(raised.exception.code, "custody_execution_outcome_unknown_pending")
        status = control.status(organization_id="xpounder", credential_env="HORMUZ_CUSTODY_ADMIN_TOKEN")
        assert status.execution_status is not None
        unknown = status.execution_status.attempts[0]
        executor._repository.finalize(
            organization_id="xpounder",
            execution_id=unknown.execution_id,
            state="outcome_unknown",
            reason_code="external_result_ambiguous",
        )

        resolution_request = self._authorized_lifecycle_request(
            control,
            operation_type="resolve_recovery",
            target={"recovery_execution_id": unknown.execution_id},
            parameters={"resolution_code": "confirmed_not_applied"},
        )
        resolution = CustodyExecutorService(
            config,
            environ=environment,
            runner=_LifecycleRunner(config),
        ).execute(request=resolution_request)
        self.assertEqual(resolution.state, "succeeded")

        status = control.status(organization_id="xpounder", credential_env="HORMUZ_CUSTODY_ADMIN_TOKEN")
        assert status.execution_status is not None
        original = next(attempt for attempt in status.execution_status.attempts if attempt.execution_id == unknown.execution_id)
        self.assertEqual(original.state, "outcome_unknown")
        with self.psycopg.connect(self.owner_dsn) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        self.sql.SQL(
                            "SELECT operation_type, recovery_execution_id, recovery_resolution_code "
                            "FROM {}.custody_lifecycle_events"
                        ).format(self.sql.Identifier(self.schema))
                    )
                    row = cursor.fetchone()
        self.assertEqual(
            row,
            ("resolve_recovery", unknown.execution_id, "confirmed_not_applied"),
        )

    def test_confirmed_not_applied_resolution_releases_only_an_uncommitted_prepared_barrier(self) -> None:
        config, environment, _issuer = self._managed_custody_config(lifecycle=True)
        control = CustodyControlService(config, environ=environment)
        control.bootstrap(organization_id="xpounder", credential_env="HORMUZ_CUSTODY_ADMIN_TOKEN")
        assert config.custody_lifecycle is not None
        provider = config.custody_lifecycle.assets.asset(
            organization_id="xpounder",
            asset_type="provider_credential",
            asset_id="openai-primary",
            generation=1,
        )
        request = self._authorized_lifecycle_request(
            control,
            operation_type="disable_provider_credential",
            target=provider.audit_ref(),
            parameters={},
        )
        runner = _LifecycleRunner(config)
        executor = CustodyExecutorService(config, environ=environment, runner=runner)
        executor.register_asset_catalog()
        replica = CustodyRuntimeProjection(
            config,
            environ=environment,
            start_background=False,
            replica_id="50000000-0000-4000-8000-000000000005",
        )
        self.addCleanup(replica.close)
        pending = executor._repository.claim(request=request)
        result = runner.execute(request)
        assert result is not None
        executor._repository.prepare_restriction(
            organization_id="xpounder",
            execution_id=pending.execution_id,
            result=result,
        )
        with self.psycopg.connect(self.owner_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self.sql.SQL(
                        "SELECT barrier_id FROM {}.custody_runtime_projection_barriers "
                        "WHERE organization_id = %s AND execution_id = %s"
                    ).format(self.sql.Identifier(self.schema)),
                    ("xpounder", pending.execution_id),
                )
                barrier_id = cursor.fetchone()[0]
        with self.psycopg.connect(self.owner_dsn) as connection:
            with self.assertRaises(self.psycopg.Error):
                with connection.transaction():
                    with connection.cursor() as cursor:
                        self._set_role_and_tenant(cursor, self.runtime_role, "xpounder")
                        cursor.execute(
                            "SELECT custody_runtime_ack_barrier(%s, %s::uuid, %s::uuid, %s)",
                            ("xpounder", replica.replica_id, barrier_id, 99),
                        )
        replica._synchronize("xpounder")
        # A duplicated notification may make a healthy replica observe and
        # acknowledge the same prepared barrier again. The database contract
        # keeps that replay idempotent while the stale-version acknowledgement
        # above remains rejected.
        replica._synchronize("xpounder")
        with self.psycopg.connect(self.owner_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self.sql.SQL(
                        "SELECT COUNT(*) FROM {}.custody_runtime_projection_acks "
                        "WHERE organization_id = %s AND barrier_id = %s AND replica_id = %s"
                    ).format(self.sql.Identifier(self.schema)),
                    ("xpounder", barrier_id, replica.replica_id),
                )
                self.assertEqual(cursor.fetchone()[0], 1)
        unknown = executor._repository.finalize(
            organization_id="xpounder",
            execution_id=pending.execution_id,
            state="outcome_unknown",
            reason_code="external_result_ambiguous",
        )
        self.assertEqual(unknown.state, "outcome_unknown")
        with self.assertRaises(CustodyRuntimeProjectionError) as prepared:
            replica.require_provider_usable(organization_id="xpounder", protocol="openai")
        self.assertEqual(prepared.exception.code, "custody_provider_credential_disabled")

        invalid_resolution = self._authorized_lifecycle_request(
            control,
            operation_type="resolve_recovery",
            target={"recovery_execution_id": pending.execution_id},
            parameters={"resolution_code": "confirmed_applied"},
        )
        invalid_runner = _LifecycleRunner(config)
        with self.assertRaises(CustodyExecutionError) as invalid:
            CustodyExecutorService(
                config,
                environ=environment,
                runner=invalid_runner,
            ).execute(request=invalid_resolution)
        self.assertEqual(invalid.exception.code, "custody_recovery_resolution_invalid")
        self.assertEqual(invalid_runner.requests, [])

        resolution_request = self._authorized_lifecycle_request(
            control,
            operation_type="resolve_recovery",
            target={"recovery_execution_id": pending.execution_id},
            parameters={"resolution_code": "confirmed_not_applied"},
        )
        resolution = CustodyExecutorService(
            config,
            environ=environment,
            runner=_LifecycleRunner(config),
        ).execute(request=resolution_request)
        self.assertEqual(resolution.state, "succeeded")
        replica._synchronize("xpounder")
        replica.require_provider_usable(organization_id="xpounder", protocol="openai")

        with self.psycopg.connect(self.owner_dsn) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        self.sql.SQL(
                            "SELECT activated_at, resolved_at, lifecycle_event_id, resolution_lifecycle_event_id "
                            "FROM {}.custody_runtime_projection_barriers "
                            "WHERE organization_id = %s AND execution_id = %s"
                        ).format(self.sql.Identifier(self.schema)),
                        ("xpounder", pending.execution_id),
                    )
                    barrier = cursor.fetchone()
        assert barrier is not None
        self.assertIsNone(barrier[0])
        self.assertIsNotNone(barrier[1])
        self.assertIsNone(barrier[2])
        self.assertIsNotNone(barrier[3])

    def test_gateway_startup_requires_registered_immutable_catalog_and_rejects_rebinding(self) -> None:
        config, environment, _issuer = self._managed_custody_config(lifecycle=True)
        with self.assertRaises(CustodyRuntimeProjectionError) as raised:
            CustodyRuntimeProjection(config, environ=environment)
        self.assertEqual(raised.exception.code, "custody_runtime_projection_unavailable")

        CustodyControlService(config, environ=environment).bootstrap(
            organization_id="xpounder",
            credential_env="HORMUZ_CUSTODY_ADMIN_TOKEN",
        )
        CustodyExecutorService(config, environ=environment).register_asset_catalog()
        projection = CustodyRuntimeProjection(config, environ=environment, start_background=False)
        self.addCleanup(projection.close)
        self.assertTrue(projection.readiness_healthy())

        assert config.custody_lifecycle is not None
        provider = config.custody_lifecycle.assets.asset(
            organization_id="xpounder",
            asset_type="provider_credential",
            asset_id="openai-primary",
            generation=1,
        )
        rebound = replace(
            provider,
            binding={"protocol": "openai", "source": "env:DIFFERENT_OPENAI_KEY"},
            binding_fingerprint=binding_fingerprint(
                organization_id=provider.organization_id,
                asset_type=provider.asset_type,
                asset_id=provider.asset_id,
                generation=provider.generation,
                binding={"protocol": "openai", "source": "env:DIFFERENT_OPENAI_KEY"},
            ),
        )
        rebound_catalog = CustodyAssetCatalog(
            tuple(rebound if asset.key == provider.key else asset for asset in config.custody_lifecycle.assets.assets)
        )
        rebound_config = replace(
            config,
            custody_lifecycle=CustodyLifecycleConfig(
                freshness_lease_seconds=config.custody_lifecycle.freshness_lease_seconds,
                assets=rebound_catalog,
            ),
        )
        with self.assertRaises(CustodyLifecycleError) as rebound_error:
            CustodyExecutorService(rebound_config, environ=environment).register_asset_catalog()
        self.assertEqual(rebound_error.exception.code, "custody_lifecycle_asset_identity_reused")

    def _authorized_lifecycle_request(
        self,
        control: CustodyControlService,
        *,
        operation_type: str,
        target: dict[str, object],
        parameters: dict[str, object],
    ) -> CustodyExecutionRequest:
        pending = CustodyExecutionRequest(
            organization_id="xpounder",
            operation_id="00000000-0000-4000-8000-000000000000",
            operation_type=operation_type,
            target=target,
            parameters=parameters,
        )
        operation = control.authorize_operation(
            organization_id="xpounder",
            credential_env="HORMUZ_CUSTODY_ADMIN_TOKEN",
            operation_type=operation_type,
            target_sha256=pending.target_sha256,
            parameters_sha256=pending.parameters_sha256,
        )
        control.approve_operation(
            organization_id="xpounder",
            credential_env="HORMUZ_CUSTODY_BOB_TOKEN",
            operation_id=operation.operation_id,
        )
        return CustodyExecutionRequest(
            organization_id="xpounder",
            operation_id=operation.operation_id,
            operation_type=operation_type,
            target=target,
            parameters=parameters,
        )

    def _authorized_request(
        self,
        control: CustodyControlService,
        *,
        operation_type: str,
        target: dict[str, object],
        parameters: dict[str, object],
    ) -> CustodyExecutionRequest:
        pending = CustodyExecutionRequest(
            organization_id="xpounder",
            operation_id="00000000-0000-4000-8000-000000000000",
            operation_type=operation_type,
            target=target,
            parameters=parameters,
        )
        operation = control.authorize_operation(
            organization_id="xpounder",
            credential_env="HORMUZ_CUSTODY_ADMIN_TOKEN",
            operation_type=operation_type,
            target_sha256=pending.target_sha256,
            parameters_sha256=pending.parameters_sha256,
        )
        if operation.required_approvals == 2:
            control.approve_operation(
                organization_id="xpounder",
                credential_env="HORMUZ_CUSTODY_BOB_TOKEN",
                operation_id=operation.operation_id,
            )
        return CustodyExecutionRequest(
            organization_id="xpounder",
            operation_id=operation.operation_id,
            operation_type=operation_type,
            target=target,
            parameters=parameters,
        )

    def _set_role_and_tenant(self, cursor, role: str, organization_id: str) -> None:
        cursor.execute(self.sql.SQL("SET LOCAL ROLE {}").format(self.sql.Identifier(role)))
        cursor.execute(
            self.sql.SQL("SET LOCAL search_path TO {}, pg_catalog").format(self.sql.Identifier(self.schema))
        )
        cursor.execute("SELECT set_config('hormuz.organization_id', %s, true)", (organization_id,))


if __name__ == "__main__":
    unittest.main()
