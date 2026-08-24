from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import unittest
from unittest.mock import patch

from hormuz.custody_control import CustodyControlService
from hormuz.custody_execution_repository import CustodyExecutionError, CustodyExecutionRequest
from hormuz.custody_executor import CustodyExecutionAmbiguous, CustodyExecutorService
from hormuz.custody_repository import CustodyAdministrator
from hormuz.postgres_custody_executor_store import PostgresCustodyExecutorStore
from hormuz.postgres_custody_store import PostgresCustodyControlStore
from hormuz.postgres import PostgresStorageError

if __package__:
    from ._postgres_fixture import PostgresTestCase
else:  # Isolated wheel compatibility discovery uses the tests directory as its import root.
    from _postgres_fixture import PostgresTestCase


class _Resolver:
    def resolve(self, *, organization_id: str, reference: str) -> bytes:
        del organization_id, reference
        return b"executor-test-secret"


class _SuccessfulRunner:
    def __init__(self) -> None:
        self.requests: list[CustodyExecutionRequest] = []

    def execute(self, request: CustodyExecutionRequest) -> None:
        self.requests.append(request)


class _AmbiguousRunner:
    def execute(self, request: CustodyExecutionRequest) -> None:
        del request
        raise CustodyExecutionAmbiguous("simulated external timeout")


class PostgresCustodyExecutorTests(PostgresTestCase):
    def test_executor_claims_exact_routine_intent_then_finalizes_once_and_exposes_metadata_status(self) -> None:
        config, environment, _issuer = self._managed_custody_config()
        control = CustodyControlService(config, environ=environment)
        control.bootstrap(organization_id="xpounder", credential_env="HORMUZ_CUSTODY_ADMIN_TOKEN")
        request = self._authorized_request(control, operation_type="seal_envelope")
        runner = _SuccessfulRunner()
        executor = CustodyExecutorService(
            config,
            protected_input_resolver=_Resolver(),
            environ=environment,
            runner=runner,
        )

        attempt = executor.execute(request=request)
        self.assertEqual(attempt.state, "succeeded")
        self.assertEqual([event.state for event in attempt.events], ["pending", "succeeded"])
        self.assertEqual(runner.requests, [request])
        with self.assertRaises(CustodyExecutionError) as raised:
            executor.execute(request=request)
        self.assertEqual(raised.exception.code, "custody_execution_already_claimed")

        status = control.status(organization_id="xpounder", credential_env="HORMUZ_CUSTODY_ADMIN_TOKEN")
        assert status.execution_status is not None
        self.assertEqual(status.execution_status.attempt_count, 1)
        self.assertEqual(status.execution_status.attempts[0].state, "succeeded")
        serialized = json.dumps(
            {
                "attempt": status.execution_status.attempts[0].contract_record(),
                "events": [event.contract_record() for event in status.execution_status.attempts[0].events],
            },
            sort_keys=True,
        )
        self.assertNotIn("executor-test-secret", serialized)
        self.assertNotIn(str(request.target["path"]), serialized)
        self.assertNotIn(str(request.protected_input_reference), serialized)

    def test_authority_mismatch_expiry_and_requester_revocation_fail_closed_before_runner(self) -> None:
        config, environment, _issuer = self._managed_custody_config()
        control = CustodyControlService(config, environ=environment)
        control.bootstrap(organization_id="xpounder", credential_env="HORMUZ_CUSTODY_ADMIN_TOKEN")
        runner = _SuccessfulRunner()
        executor = CustodyExecutorService(
            config,
            protected_input_resolver=_Resolver(),
            environ=environment,
            runner=runner,
        )

        mismatched = self._authorized_request(control, operation_type="seal_envelope")
        changed = CustodyExecutionRequest(
            organization_id=mismatched.organization_id,
            operation_id=mismatched.operation_id,
            operation_type=mismatched.operation_type,
            target={"kind": "owner_only_file", "path": "/private/other.envelope"},
            parameters=mismatched.parameters,
            protected_input_reference=mismatched.protected_input_reference,
        )
        with self.assertRaises(CustodyExecutionError) as raised:
            executor.execute(request=changed)
        self.assertEqual(raised.exception.code, "custody_execution_authorization_mismatch")

        expired = self._authorized_request(control, operation_type="verify_restore")
        with self.psycopg.connect(self.owner_dsn) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        self.sql.SQL(
                            "UPDATE {}.custody_operation_intents "
                            "SET created_at = CURRENT_TIMESTAMP - INTERVAL '2 minutes', "
                            "expires_at = CURRENT_TIMESTAMP - INTERVAL '1 minute', "
                            "authorized_at = CURRENT_TIMESTAMP - INTERVAL '2 minutes' "
                            "WHERE organization_id = %s AND operation_id = %s"
                        ).format(self.sql.Identifier(self.schema)),
                        ("xpounder", expired.operation_id),
                    )
        with self.assertRaises(CustodyExecutionError) as raised:
            executor.execute(request=expired)
        self.assertEqual(raised.exception.code, "custody_execution_authorization_expired")

        revoked = self._authorized_request(control, operation_type="rewrap_envelope")
        control.revoke_static_administrator(
            organization_id="xpounder",
            credential_env="HORMUZ_CUSTODY_BOB_TOKEN",
            actor_id="alice",
        )
        with self.assertRaises(CustodyExecutionError) as raised:
            executor.execute(request=revoked)
        self.assertEqual(raised.exception.code, "custody_execution_requester_inactive")
        self.assertEqual(runner.requests, [])

    def test_ambiguous_effect_remains_pending_until_swept_unknown_without_replay(self) -> None:
        config, environment, _issuer = self._managed_custody_config(authorization_ttl_seconds=60)
        control = CustodyControlService(config, environ=environment)
        control.bootstrap(organization_id="xpounder", credential_env="HORMUZ_CUSTODY_ADMIN_TOKEN")
        request = self._authorized_request(control, operation_type="seal_envelope")
        executor = CustodyExecutorService(
            config,
            protected_input_resolver=_Resolver(),
            environ=environment,
            runner=_AmbiguousRunner(),
        )
        with self.assertRaises(CustodyExecutionError) as raised:
            executor.execute(request=request)
        self.assertEqual(raised.exception.code, "custody_execution_outcome_unknown_pending")
        # A durable attempt is immutable. Narrow the test-only in-memory
        # sweeper window instead of rewriting the committed database clock.
        executor._repository._pending_attempt_ttl = timedelta(0)
        self.assertEqual(executor.sweep_stale_pending(), 1)
        with self.assertRaises(CustodyExecutionError) as replay:
            executor.execute(request=request)
        self.assertEqual(replay.exception.code, "custody_execution_already_claimed")
        status = control.status(organization_id="xpounder", credential_env="HORMUZ_CUSTODY_ADMIN_TOKEN")
        assert status.execution_status is not None
        self.assertEqual(status.execution_status.attempts[0].state, "outcome_unknown")
        self.assertEqual(status.execution_status.attempts[0].events[-1].reason_code, "stale_pending")

    def test_post_effect_finalization_failure_preserves_pending_for_unknown_recovery_without_replay(self) -> None:
        config, environment, _issuer = self._managed_custody_config(authorization_ttl_seconds=60)
        control = CustodyControlService(config, environ=environment)
        control.bootstrap(organization_id="xpounder", credential_env="HORMUZ_CUSTODY_ADMIN_TOKEN")
        request = self._authorized_request(control, operation_type="seal_envelope")
        runner = _SuccessfulRunner()
        executor = CustodyExecutorService(
            config,
            protected_input_resolver=_Resolver(),
            environ=environment,
            runner=runner,
        )

        with patch.object(
            executor._repository,
            "finalize",
            side_effect=PostgresStorageError("simulated terminal-write interruption"),
        ):
            with self.assertRaises(CustodyExecutionError) as raised:
                executor.execute(request=request)
        self.assertEqual(raised.exception.code, "custody_execution_finalization_unavailable")
        self.assertEqual(runner.requests, [request])

        # Preserve immutable evidence while deterministically exercising the
        # stale-pending recovery branch.
        executor._repository._pending_attempt_ttl = timedelta(0)
        self.assertEqual(executor.sweep_stale_pending(), 1)
        with self.assertRaises(CustodyExecutionError) as replay:
            executor.execute(request=request)
        self.assertEqual(replay.exception.code, "custody_execution_already_claimed")
        self.assertEqual(runner.requests, [request])

    def test_executor_role_is_tenant_isolated_and_cannot_rewrite_or_mismatch_execution_evidence(self) -> None:
        config, environment, _issuer = self._managed_custody_config()
        control = CustodyControlService(config, environ=environment)
        control.bootstrap(organization_id="xpounder", credential_env="HORMUZ_CUSTODY_ADMIN_TOKEN")
        request = self._authorized_request(control, operation_type="verify_restore")
        store = PostgresCustodyExecutorStore(
            self.custody_executor_dsn,
            schema=self.schema,
            custody_executor_role=self.custody_executor_role,
            pending_attempt_ttl_seconds=60,
        )
        attempt = store.claim(request=request)

        beta_admin = CustodyAdministrator(
            organization_id="beta",
            authentication_kind="static",
            actor_id="beta-admin",
        )
        control_store = PostgresCustodyControlStore(
            self.custody_control_dsn,
            schema=self.schema,
            custody_control_role=self.custody_control_role,
        )
        control_store.bootstrap(
            organization_id="beta",
            caller=beta_admin,
            administrators=(beta_admin,),
            retention_days=365,
            retention_legal_hold=False,
        )
        beta_template = CustodyExecutionRequest(
            organization_id="beta",
            operation_id="00000000-0000-4000-8000-000000000001",
            operation_type="verify_restore",
            target={"kind": "owner_only_file", "path": "/private/beta.restore"},
            parameters={},
        )
        beta_operation = control_store.request_operation(
            organization_id="beta",
            caller=beta_admin,
            operation_type="verify_restore",
            target_sha256=beta_template.target_sha256,
            parameters_sha256=beta_template.parameters_sha256,
            protected_input_ref_sha256=None,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        )
        beta_request = CustodyExecutionRequest(
            organization_id="beta",
            operation_id=beta_operation.operation_id,
            operation_type="verify_restore",
            target=beta_template.target,
            parameters=beta_template.parameters,
        )
        beta_attempt = store.claim(request=beta_request)
        raw_mismatch = self._authorized_request(control, operation_type="verify_restore")
        with self.psycopg.connect(self.custody_executor_dsn) as connection:
            with self.assertRaises(self.psycopg.Error):
                with connection.transaction():
                    with connection.cursor() as cursor:
                        self._set_role_and_tenant(cursor, self.custody_executor_role, "xpounder")
                        cursor.execute(
                            """
                            INSERT INTO custody_execution_attempts (
                                organization_id, execution_id, execution_schema_id, execution_schema_version,
                                operation_id, operation_type, target_kind, target_sha256, parameters_sha256,
                                protected_input_ref_sha256, claimed_at
                            ) VALUES (%s, %s, 'hormuz.custody-execution-attempt', 1, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                            """,
                            (
                                "xpounder",
                                "00000000-0000-4000-8000-000000000010",
                                raw_mismatch.operation_id,
                                raw_mismatch.operation_type,
                                "restore",
                                "0" * 64,
                                raw_mismatch.parameters_sha256,
                                None,
                            ),
                        )
        with self.psycopg.connect(self.custody_executor_dsn) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    self._set_role_and_tenant(cursor, self.custody_executor_role, "xpounder")
                    cursor.execute("SELECT organization_id FROM custody_execution_attempts ORDER BY organization_id")
                    self.assertEqual([row[0] for row in cursor.fetchall()], ["xpounder"])
        for statement in (
            "UPDATE custody_operation_intents SET state = state",
            "UPDATE custody_execution_attempts SET claimed_at = claimed_at",
            "DELETE FROM custody_execution_events",
            "SELECT * FROM custody_operation_approvals",
            "SELECT * FROM policy_tenants",
            "SELECT * FROM gateway_usage_events",
        ):
            with self.psycopg.connect(self.custody_executor_dsn) as connection:
                with self.assertRaises(self.psycopg.Error):
                    with connection.transaction():
                        with connection.cursor() as cursor:
                            self._set_role_and_tenant(cursor, self.custody_executor_role, "xpounder")
                            cursor.execute(statement)
        self.assertEqual(attempt.organization_id, "xpounder")
        self.assertEqual(beta_attempt.organization_id, "beta")

    def _set_role_and_tenant(self, cursor, role: str, organization_id: str) -> None:
        cursor.execute(self.sql.SQL("SET LOCAL ROLE {}").format(self.sql.Identifier(role)))
        cursor.execute(
            self.sql.SQL("SET LOCAL search_path TO {}, pg_catalog").format(self.sql.Identifier(self.schema))
        )
        cursor.execute("SELECT set_config('hormuz.organization_id', %s, true)", (organization_id,))

    def _authorized_request(self, control: CustodyControlService, *, operation_type: str) -> CustodyExecutionRequest:
        if operation_type == "seal_envelope":
            request = CustodyExecutionRequest(
                organization_id="xpounder",
                operation_id="00000000-0000-4000-8000-000000000000",
                operation_type=operation_type,
                target={"kind": "owner_only_file", "path": "/private/target.envelope"},
                parameters={"purpose": "provider_credential"},
                protected_input_reference="/private/owner-only-input.secret",
            )
        elif operation_type == "rewrap_envelope":
            request = CustodyExecutionRequest(
                organization_id="xpounder",
                operation_id="00000000-0000-4000-8000-000000000000",
                operation_type=operation_type,
                target={"kind": "owner_only_file", "path": "/private/rewrapped.envelope"},
                parameters={"source_envelope_path": "/private/source.envelope"},
            )
        else:
            request = CustodyExecutionRequest(
                organization_id="xpounder",
                operation_id="00000000-0000-4000-8000-000000000000",
                operation_type=operation_type,
                target={"kind": "owner_only_file", "path": "/private/restore.envelope"},
                parameters={},
            )
        operation = control.authorize_operation(
            organization_id="xpounder",
            credential_env="HORMUZ_CUSTODY_ADMIN_TOKEN",
            operation_type=request.operation_type,
            target_sha256=request.target_sha256,
            parameters_sha256=request.parameters_sha256,
            protected_input_ref_sha256=request.protected_input_ref_sha256,
        )
        return CustodyExecutionRequest(
            organization_id=request.organization_id,
            operation_id=operation.operation_id,
            operation_type=request.operation_type,
            target=request.target,
            parameters=request.parameters,
            protected_input_reference=request.protected_input_reference,
        )


if __name__ == "__main__":
    unittest.main()
