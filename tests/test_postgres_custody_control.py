from __future__ import annotations

from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import json
import os
from unittest import mock

from hormuz.cli import main
from hormuz.contracts import (
    ContractValidationError,
    validate_contract,
    validate_custody_control_event,
    validate_custody_deletion_event,
    validate_custody_evidence_export,
)
from hormuz.custody_control import CustodyControlService
from hormuz.custody_repository import CustodyAdministrator, CustodyControlError
from hormuz.postgres_custody_store import PostgresCustodyControlStore
from hormuz.postgres_usage_store import PostgresUsageStore

if __package__:
    from ._postgres_fixture import PostgresTestCase
else:  # Isolated wheel compatibility discovery uses the tests directory as its import root.
    from _postgres_fixture import PostgresTestCase


_TARGET = "0" * 64
_PARAMETERS = "1" * 64
_PROTECTED_INPUT = "2" * 64


class PostgresCustodyControlTests(PostgresTestCase):
    def test_retained_custody_evidence_exports_through_control_and_deletion_is_only_blocked(self) -> None:
        config, environment, _issuer = self._managed_custody_config()
        service = CustodyControlService(config, environ=environment)
        service.bootstrap(organization_id="xpounder", credential_env="HORMUZ_CUSTODY_ADMIN_TOKEN")

        initial_export = service.export_evidence(
            organization_id="xpounder",
            credential_env="HORMUZ_CUSTODY_ADMIN_TOKEN",
        )
        validate_contract(initial_export)
        validate_custody_evidence_export(initial_export)
        self.assertEqual(initial_export["organization_id"], "xpounder")
        records = initial_export["records"]
        assert isinstance(records, list) and records
        first_entry = records[0]["entry"]
        assert isinstance(first_entry, dict)

        blocked = service.record_deletion_blocked(
            organization_id="xpounder",
            credential_env="HORMUZ_CUSTODY_ADMIN_TOKEN",
            source_schema_id=str(first_entry["source_schema_id"]),
            source_schema_version=int(first_entry["source_schema_version"]),
            source_event_id=str(first_entry["source_event_id"]),
        )
        validate_custody_deletion_event(blocked)
        self.assertEqual((blocked["decision"], blocked["reason_code"]), ("deletion_blocked", "retention_active"))
        self.assertNotIn("plaintext", json.dumps(blocked, sort_keys=True))
        self.assertNotIn("secret", json.dumps(blocked, sort_keys=True))

        # The runtime proves the new deletion-block source through its bounded
        # verifier, despite having no direct custody-source table privilege.
        auditor = PostgresUsageStore(
            self.runtime_dsn,
            organization_ids=("xpounder",),
            schema=self.schema,
            runtime_role=self.runtime_role,
        )
        self.assertGreater(auditor.verify_audit_chain(organization_id="xpounder").sequence, 1)

        export = service.export_evidence(
            organization_id="xpounder",
            credential_env="HORMUZ_CUSTODY_ADMIN_TOKEN",
        )
        validate_contract(export)
        validate_custody_evidence_export(export)
        exported_records = export["records"]
        assert isinstance(exported_records, list)
        self.assertEqual(len(exported_records), len(records) + 1)
        self.assertEqual(exported_records[-1]["entry"]["event"]["decision"], "deletion_blocked")
        serialized = json.dumps(export, sort_keys=True)
        self.assertNotIn("ciphertext", serialized)
        self.assertNotIn("provider-key", serialized)
        self.assertNotIn("/private/", serialized)

        with self.psycopg.connect(self.owner_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self.sql.SQL(
                        "SELECT occurred_at, retain_until, legal_hold FROM {}.custody_deletion_events"
                    ).format(self.sql.Identifier(self.schema))
                )
                deletion_row = cursor.fetchone()
        assert deletion_row is not None
        self.assertEqual(deletion_row[1] - deletion_row[0], timedelta(days=365))
        self.assertFalse(deletion_row[2])

        for role, statement in (
            (self.custody_control_role, "UPDATE custody_deletion_events SET reason_code = reason_code"),
            (self.custody_control_role, "DELETE FROM custody_deletion_events"),
            (self.runtime_role, "DELETE FROM custody_deletion_events"),
        ):
            with self.subTest(role=role, statement=statement):
                with self.psycopg.connect(self.owner_dsn) as connection:
                    with self.assertRaises(self.psycopg.Error):
                        with connection.transaction():
                            with connection.cursor() as cursor:
                                self._set_role_and_tenant(cursor, role, "xpounder")
                                cursor.execute(statement)

    def test_unlinked_deletion_evidence_rolls_back_at_commit(self) -> None:
        config, environment, _issuer = self._managed_custody_config()
        service = CustodyControlService(config, environ=environment)
        service.bootstrap(organization_id="xpounder", credential_env="HORMUZ_CUSTODY_ADMIN_TOKEN")
        event_id = "a0000000-0000-4000-8000-000000000001"
        with self.psycopg.connect(self.custody_control_dsn) as connection:
            with self.assertRaises(self.psycopg.Error):
                with connection.transaction():
                    with connection.cursor() as cursor:
                        self._set_role_and_tenant(cursor, self.custody_control_role, "xpounder")
                        cursor.execute("SELECT clock_timestamp() AS occurred_at")
                        occurred_at = cursor.fetchone()[0]
                        cursor.execute(
                            """
                            INSERT INTO custody_deletion_events (
                                organization_id, deletion_event_id, deletion_schema_id,
                                deletion_schema_version, occurred_at,
                                source_schema_id, source_schema_version, source_event_id,
                                source_retain_until, source_legal_hold,
                                decision, reason_code, evidence_json, retain_until, legal_hold
                            ) VALUES (
                                %s, %s, %s, %s, %s,
                                %s, %s, %s, %s, %s,
                                %s, %s, %s, %s, %s
                            )
                            """,
                            (
                                "xpounder",
                                event_id,
                                "hormuz.custody-deletion-event",
                                1,
                                occurred_at,
                                "hormuz.custody-control-event",
                                1,
                                "missing-source-event",
                                occurred_at,
                                False,
                                "deletion_blocked",
                                "retention_active",
                                json.dumps(
                                    {
                                        "deletion_schema_id": "hormuz.custody-deletion-event",
                                        "deletion_schema_version": 1,
                                        "organization_id": "xpounder",
                                        "deletion_event_id": event_id,
                                        "occurred_at": occurred_at.isoformat(),
                                        "source_schema_id": "hormuz.custody-control-event",
                                        "source_schema_version": 1,
                                        "source_event_id": "missing-source-event",
                                        "source_retain_until": occurred_at.isoformat(),
                                        "source_legal_hold": False,
                                        "decision": "deletion_blocked",
                                        "reason_code": "retention_active",
                                    },
                                    sort_keys=True,
                                    separators=(",", ":"),
                                ),
                                occurred_at + timedelta(days=365),
                                False,
                            ),
                        )
        with self.psycopg.connect(self.owner_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self.sql.SQL(
                        "SELECT COUNT(*) FROM {}.custody_deletion_events WHERE deletion_event_id = %s"
                    ).format(self.sql.Identifier(self.schema)),
                    (event_id,),
                )
                self.assertEqual(cursor.fetchone()[0], 0)

    def test_direct_custody_writer_cannot_use_evidence_as_a_json_side_channel(self) -> None:
        config, environment, _issuer = self._managed_custody_config()
        service = CustodyControlService(config, environ=environment)
        service.bootstrap(organization_id="xpounder", credential_env="HORMUZ_CUSTODY_ADMIN_TOKEN")
        event_id = "a0000000-0000-4000-8000-000000000002"
        with self.psycopg.connect(self.custody_control_dsn) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    self._set_role_and_tenant(cursor, self.custody_control_role, "xpounder")
                    cursor.execute("SELECT clock_timestamp() AS occurred_at")
                    occurred_at = cursor.fetchone()[0]
                    cursor.execute("SAVEPOINT malformed_custody_evidence")
                    with self.assertRaises(self.psycopg.Error) as raised:
                        cursor.execute(
                            """
                            INSERT INTO custody_deletion_events (
                                organization_id, deletion_event_id, deletion_schema_id,
                                deletion_schema_version, occurred_at,
                                source_schema_id, source_schema_version, source_event_id,
                                source_retain_until, source_legal_hold,
                                decision, reason_code, evidence_json, retain_until, legal_hold
                            ) VALUES (
                                %s, %s, %s, %s, %s,
                                %s, %s, %s, %s, %s,
                                %s, %s, %s, %s, %s
                            )
                            """,
                            (
                                "xpounder",
                                event_id,
                                "hormuz.custody-deletion-event",
                                1,
                                occurred_at,
                                "hormuz.custody-control-event",
                                1,
                                "missing-source-event",
                                occurred_at,
                                False,
                                "deletion_blocked",
                                "retention_active",
                                json.dumps(
                                    {
                                        "deletion_schema_id": "hormuz.custody-deletion-event",
                                        "deletion_schema_version": 1,
                                        "organization_id": "xpounder",
                                        "deletion_event_id": event_id,
                                        "occurred_at": occurred_at.isoformat(),
                                        "source_schema_id": "hormuz.custody-control-event",
                                        "source_schema_version": 1,
                                        "source_event_id": "missing-source-event",
                                        "source_retain_until": occurred_at.isoformat(),
                                        "source_legal_hold": False,
                                        "decision": "deletion_blocked",
                                        "reason_code": "retention_active",
                                        "plaintext": "must-never-persist",
                                    },
                                    sort_keys=True,
                                    separators=(",", ":"),
                                ),
                                occurred_at + timedelta(days=365),
                                False,
                            ),
                        )
                    self.assertIn("custody evidence contract is invalid", str(raised.exception))
                    cursor.execute("ROLLBACK TO SAVEPOINT malformed_custody_evidence")

    def test_runtime_cannot_insert_an_arbitrary_v2_audit_chain_entry(self) -> None:
        """The legacy runtime append grant must not open a v2 JSON side channel."""

        config, environment, _issuer = self._managed_custody_config()
        CustodyControlService(config, environ=environment).bootstrap(
            organization_id="xpounder",
            credential_env="HORMUZ_CUSTODY_ADMIN_TOKEN",
        )
        with self.psycopg.connect(self.owner_dsn) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    self._set_role_and_tenant(cursor, self.runtime_role, "xpounder")
                    cursor.execute("SAVEPOINT malformed_v2_chain_entry")
                    with self.assertRaises(self.psycopg.Error) as raised:
                        cursor.execute(
                            """
                            INSERT INTO gateway_audit_chain_entries (
                                organization_id, chain_version, chain_epoch, sequence,
                                entry_schema_id, entry_schema_version, event_id,
                                previous_digest, event_digest, event_json, appended_at,
                                source_schema_id, source_schema_version, source_event_id
                            ) VALUES (
                                %s, %s, %s, %s,
                                %s, %s, %s,
                                %s, %s, %s, clock_timestamp(),
                                %s, %s, %s
                            )
                            """,
                            (
                                "xpounder",
                                1,
                                1,
                                999,
                                "hormuz.commit-audit-chain-entry",
                                2,
                                "missing-custody-source",
                                None,
                                "0" * 64,
                                '{"unapproved":"metadata"}',
                                "hormuz.custody-control-event",
                                1,
                                "missing-custody-source",
                            ),
                        )
                    self.assertIn("audit source evidence mismatch", str(raised.exception))
                    cursor.execute("ROLLBACK TO SAVEPOINT malformed_v2_chain_entry")

    def test_bootstrap_authority_is_separate_from_runtime_policy_and_kms_entitlement(self) -> None:
        config, environment, issuer = self._managed_custody_config(include_oidc=True)
        assert issuer is not None
        service = CustodyControlService(config, environ=environment)
        administrators = service.bootstrap(
            organization_id="xpounder",
            credential_env="HORMUZ_CUSTODY_ADMIN_TOKEN",
        )
        self.assertEqual(
            [(administrator.authentication_kind, administrator.actor_id) for administrator in administrators],
            [("static", "alice"), ("static", "bob")],
        )
        granted = service.grant_oidc_administrator(
            organization_id="xpounder",
            credential_env="HORMUZ_CUSTODY_ADMIN_TOKEN",
            issuer=issuer,
            subject="unmapped-custody-administrator",
        )
        self.assertEqual((granted.issuer, granted.subject), (issuer, "unmapped-custody-administrator"))
        self.assertIsNone(config.identity_for_subject(issuer, "unmapped-custody-administrator"))

        repository = PostgresCustodyControlStore(
            self.custody_control_dsn,
            schema=self.schema,
            custody_control_role=self.custody_control_role,
        )
        with self.assertRaises(CustodyControlError) as raised:
            repository.grant_administrator(
                organization_id="xpounder",
                caller=administrators[0],
                administrator=CustodyAdministrator(
                    organization_id="xpounder",
                    authentication_kind="static",
                    actor_id="new-static-root",
                ),
            )
        self.assertEqual(raised.exception.code, "custody_static_administrator_grant_denied")

        self._assert_role_cannot_select(self.runtime_dsn, self.runtime_role, "custody_administrators")
        self._assert_role_cannot_select(
            self.policy_control_dsn,
            self.policy_control_role,
            "custody_administrators",
        )
        self._assert_role_cannot_select(
            self.custody_control_dsn,
            self.custody_control_role,
            "policy_administrators",
        )
        self._assert_role_cannot_select(
            self.custody_control_dsn,
            self.custody_control_role,
            "gateway_usage_events",
        )
        with self.psycopg.connect(self.custody_control_dsn) as connection:
            with self.assertRaises(self.psycopg.Error):
                with connection.transaction():
                    with connection.cursor() as cursor:
                        self._set_role_and_tenant(cursor, self.custody_control_role, "xpounder")
                        cursor.execute("UPDATE custody_control_events SET event_type = event_type")

    def test_routine_authorization_is_content_free_and_initial_enrollment_uses_only_a_handle_digest(self) -> None:
        config, environment, _issuer = self._managed_custody_config()
        service = CustodyControlService(config, environ=environment)
        service.bootstrap(organization_id="xpounder", credential_env="HORMUZ_CUSTODY_ADMIN_TOKEN")
        operation = service.authorize_operation(
            organization_id="xpounder",
            credential_env="HORMUZ_CUSTODY_ADMIN_TOKEN",
            operation_type="seal_envelope",
            target_sha256=_TARGET,
            parameters_sha256=_PARAMETERS,
            protected_input_ref_sha256=_PROTECTED_INPUT,
        )
        self.assertEqual(operation.state, "authorized")
        self.assertEqual(operation.required_approvals, 1)
        self.assertEqual(len(operation.approvals), 1)
        self.assertEqual(operation.protected_input_ref_sha256, _PROTECTED_INPUT)
        self.assertFalse(hasattr(operation, "plaintext"))

        with mock.patch.dict(os.environ, environment, clear=True):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(
                    main(
                        [
                            "--config",
                            str(config.source_path),
                            "custody",
                            "status",
                            "--organization",
                            "xpounder",
                            "--credential-env",
                            "HORMUZ_CUSTODY_ADMIN_TOKEN",
                            "--json",
                        ]
                    ),
                    0,
                )
        payload = json.loads(output.getvalue())
        validate_contract(payload)
        self.assertEqual(payload["operation_count"], 1)
        self.assertNotIn("plaintext", json.dumps(payload, sort_keys=True))

        events = self._custody_events()
        self.assertEqual(
            [event["event_type"] for event in events],
            ["bootstrap_initialized", "operation_requested", "operation_approved", "operation_authorized"],
        )
        for event in events:
            validate_custody_control_event(event)
        serialized = json.dumps(events, sort_keys=True)
        self.assertNotIn("secret", serialized)
        self.assertNotIn("ciphertext", serialized)

    def test_destructive_authorization_requires_two_distinct_active_administrators_and_cannot_replay(self) -> None:
        config, environment, _issuer = self._managed_custody_config()
        service = CustodyControlService(config, environ=environment)
        service.bootstrap(organization_id="xpounder", credential_env="HORMUZ_CUSTODY_ADMIN_TOKEN")
        operation = service.authorize_operation(
            organization_id="xpounder",
            credential_env="HORMUZ_CUSTODY_ADMIN_TOKEN",
            operation_type="retire_key_reference",
            target_sha256=_TARGET,
            parameters_sha256=_PARAMETERS,
        )
        self.assertEqual((operation.state, operation.required_approvals, len(operation.approvals)), ("pending", 2, 1))

        with self.assertRaises(CustodyControlError) as raised:
            service.approve_operation(
                organization_id="xpounder",
                credential_env="HORMUZ_CUSTODY_ADMIN_TOKEN",
                operation_id=operation.operation_id,
            )
        self.assertEqual(raised.exception.code, "custody_distinct_approver_required")

        authorized = service.approve_operation(
            organization_id="xpounder",
            credential_env="HORMUZ_CUSTODY_BOB_TOKEN",
            operation_id=operation.operation_id,
        )
        self.assertEqual((authorized.state, len(authorized.approvals)), ("authorized", 2))
        self.assertEqual(len({approval.approver_identity_key for approval in authorized.approvals}), 2)

        with self.assertRaises(CustodyControlError) as raised:
            service.approve_operation(
                organization_id="xpounder",
                credential_env="HORMUZ_CUSTODY_BOB_TOKEN",
                operation_id=operation.operation_id,
            )
        self.assertEqual(raised.exception.code, "custody_operation_already_authorized")

        with self.psycopg.connect(self.owner_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self.sql.SQL(
                        "SELECT state, authorized_at FROM {}.custody_operation_intents WHERE operation_id = %s"
                    ).format(self.sql.Identifier(self.schema)),
                    (operation.operation_id,),
                )
                state, authorized_at = cursor.fetchone()
                cursor.execute(
                    self.sql.SQL(
                        "SELECT COUNT(DISTINCT approver_identity_key) FROM {}.custody_operation_approvals "
                        "WHERE operation_id = %s"
                    ).format(self.sql.Identifier(self.schema)),
                    (operation.operation_id,),
                )
                approval_count = cursor.fetchone()[0]
        self.assertEqual(state, "authorized")
        self.assertIsNotNone(authorized_at)
        self.assertEqual(approval_count, 2)

    def test_expired_or_invalid_authorization_rolls_back_without_a_second_approval(self) -> None:
        config, environment, _issuer = self._managed_custody_config()
        service = CustodyControlService(config, environ=environment)
        service.bootstrap(organization_id="xpounder", credential_env="HORMUZ_CUSTODY_ADMIN_TOKEN")
        operation = service.authorize_operation(
            organization_id="xpounder",
            credential_env="HORMUZ_CUSTODY_ADMIN_TOKEN",
            operation_type="retire_envelope",
            target_sha256=_TARGET,
            parameters_sha256=_PARAMETERS,
        )
        with self.psycopg.connect(self.owner_dsn) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        self.sql.SQL(
                            "UPDATE {}.custody_operation_intents SET expires_at = created_at + INTERVAL '1 second' "
                            "WHERE operation_id = %s"
                        ).format(self.sql.Identifier(self.schema)),
                        (operation.operation_id,),
                    )
                    cursor.execute(
                        self.sql.SQL(
                            "UPDATE {}.custody_operation_intents SET created_at = created_at - INTERVAL '1 hour', "
                            "expires_at = expires_at - INTERVAL '1 hour' WHERE operation_id = %s"
                        ).format(self.sql.Identifier(self.schema)),
                        (operation.operation_id,),
                    )
        with self.assertRaises(CustodyControlError) as raised:
            service.approve_operation(
                organization_id="xpounder",
                credential_env="HORMUZ_CUSTODY_BOB_TOKEN",
                operation_id=operation.operation_id,
            )
        self.assertEqual(raised.exception.code, "custody_operation_expired")
        with self.psycopg.connect(self.owner_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self.sql.SQL(
                        "SELECT COUNT(*) FROM {}.custody_operation_approvals WHERE operation_id = %s"
                    ).format(self.sql.Identifier(self.schema)),
                    (operation.operation_id,),
                )
                self.assertEqual(cursor.fetchone()[0], 1)

        with mock.patch(
            "hormuz.postgres_custody_store.validate_custody_control_event",
            side_effect=ContractValidationError("invalid"),
        ):
            with self.assertRaises(CustodyControlError) as raised:
                service.authorize_operation(
                    organization_id="xpounder",
                    credential_env="HORMUZ_CUSTODY_ADMIN_TOKEN",
                    operation_type="verify_restore",
                    target_sha256="4" * 64,
                    parameters_sha256="5" * 64,
                )
        self.assertEqual(raised.exception.code, "custody_control_event_invalid")
        with self.psycopg.connect(self.owner_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self.sql.SQL(
                        "SELECT COUNT(*) FROM {}.custody_operation_intents WHERE target_sha256 = %s"
                    ).format(self.sql.Identifier(self.schema)),
                    ("4" * 64,),
                )
                self.assertEqual(cursor.fetchone()[0], 0)

    def test_destructive_authorization_requires_every_approver_to_remain_active(self) -> None:
        config, environment, _issuer = self._managed_custody_config()
        service = CustodyControlService(config, environ=environment)
        service.bootstrap(organization_id="xpounder", credential_env="HORMUZ_CUSTODY_ADMIN_TOKEN")
        operation = service.authorize_operation(
            organization_id="xpounder",
            credential_env="HORMUZ_CUSTODY_ADMIN_TOKEN",
            operation_type="disable_provider_credential",
            target_sha256=_TARGET,
            parameters_sha256=_PARAMETERS,
        )
        service.revoke_static_administrator(
            organization_id="xpounder",
            credential_env="HORMUZ_CUSTODY_BOB_TOKEN",
            actor_id="alice",
        )

        with self.assertRaises(CustodyControlError) as raised:
            service.approve_operation(
                organization_id="xpounder",
                credential_env="HORMUZ_CUSTODY_BOB_TOKEN",
                operation_id=operation.operation_id,
            )
        self.assertEqual(raised.exception.code, "custody_active_approvers_required")

        with self.psycopg.connect(self.owner_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self.sql.SQL(
                        "SELECT state FROM {}.custody_operation_intents WHERE operation_id = %s"
                    ).format(self.sql.Identifier(self.schema)),
                    (operation.operation_id,),
                )
                self.assertEqual(cursor.fetchone()[0], "pending")
                cursor.execute(
                    self.sql.SQL(
                        "SELECT COUNT(*) FROM {}.custody_operation_approvals WHERE operation_id = %s"
                    ).format(self.sql.Identifier(self.schema)),
                    (operation.operation_id,),
                )
                self.assertEqual(cursor.fetchone()[0], 1)

    def test_last_administrator_protection_and_all_admin_loss_require_separate_break_glass(self) -> None:
        config, environment, _issuer = self._managed_custody_config(bootstrap_bob=False)
        service = CustodyControlService(config, environ=environment)
        service.bootstrap(organization_id="xpounder", credential_env="HORMUZ_CUSTODY_ADMIN_TOKEN")
        with self.assertRaises(CustodyControlError) as raised:
            service.revoke_static_administrator(
                organization_id="xpounder",
                credential_env="HORMUZ_CUSTODY_ADMIN_TOKEN",
                actor_id="alice",
            )
        self.assertEqual(raised.exception.code, "custody_last_administrator_revoke_denied")

        with self.psycopg.connect(self.owner_dsn) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        self.sql.SQL(
                            "UPDATE {}.custody_administrators SET active = FALSE, revoked_at = CURRENT_TIMESTAMP, "
                            "revoked_by_kind = 'static', revoked_by_identity_key = 'owner-loss-simulation'"
                        ).format(self.sql.Identifier(self.schema))
                    )
        with self.assertRaises(CustodyControlError) as raised:
            service.status(
                organization_id="xpounder",
                credential_env="HORMUZ_CUSTODY_ADMIN_TOKEN",
            )
        self.assertEqual(raised.exception.code, "custody_break_glass_required")
        self.assertFalse(hasattr(service, "break_glass_recover"))

    def test_tenant_isolation_and_append_only_authorization_history_are_database_enforced(self) -> None:
        repository = PostgresCustodyControlStore(
            self.custody_control_dsn,
            schema=self.schema,
            custody_control_role=self.custody_control_role,
        )
        xpounder = CustodyAdministrator(
            organization_id="xpounder",
            authentication_kind="static",
            actor_id="alice",
        )
        beta = CustodyAdministrator(
            organization_id="beta",
            authentication_kind="static",
            actor_id="beta-root",
        )
        repository.bootstrap(
            organization_id="xpounder",
            caller=xpounder,
            administrators=(xpounder,),
            retention_days=365,
            retention_legal_hold=False,
        )
        repository.bootstrap(
            organization_id="beta",
            caller=beta,
            administrators=(beta,),
            retention_days=365,
            retention_legal_hold=False,
        )
        operation = repository.request_operation(
            organization_id="xpounder",
            caller=xpounder,
            operation_type="verify_restore",
            target_sha256=_TARGET,
            parameters_sha256=_PARAMETERS,
            protected_input_ref_sha256=None,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        )

        with self.psycopg.connect(self.custody_control_dsn) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    self._set_role_and_tenant(cursor, self.custody_control_role, "xpounder")
                    cursor.execute("SELECT organization_id FROM custody_tenants ORDER BY organization_id")
                    self.assertEqual([row[0] for row in cursor.fetchall()], ["xpounder"])

        for statement, parameters in (
            (
                "UPDATE custody_operation_approvals SET approved_at = CURRENT_TIMESTAMP "
                "WHERE organization_id = %s AND operation_id = %s",
                ("xpounder", operation.operation_id),
            ),
            (
                "UPDATE custody_operation_intents SET target_sha256 = %s "
                "WHERE organization_id = %s AND operation_id = %s",
                ("f" * 64, "xpounder", operation.operation_id),
            ),
        ):
            with self.psycopg.connect(self.custody_control_dsn) as connection:
                with self.assertRaises(self.psycopg.Error):
                    with connection.transaction():
                        with connection.cursor() as cursor:
                            self._set_role_and_tenant(cursor, self.custody_control_role, "xpounder")
                            cursor.execute(statement, parameters)

    def _assert_role_cannot_select(self, dsn: str, role: str, table: str) -> None:
        with self.psycopg.connect(dsn) as connection:
            with self.assertRaises(self.psycopg.Error):
                with connection.transaction():
                    with connection.cursor() as cursor:
                        self._set_role_and_tenant(cursor, role, "xpounder")
                        cursor.execute(self.sql.SQL("SELECT COUNT(*) FROM {}").format(self.sql.Identifier(table)))

    def _set_role_and_tenant(self, cursor, role: str, organization_id: str) -> None:
        cursor.execute(self.sql.SQL("SET LOCAL ROLE {}").format(self.sql.Identifier(role)))
        cursor.execute(
            self.sql.SQL("SET LOCAL search_path TO {}, pg_catalog").format(self.sql.Identifier(self.schema))
        )
        cursor.execute("SELECT set_config('hormuz.organization_id', %s, true)", (organization_id,))

    def _custody_events(self) -> list[dict[str, object]]:
        fields = (
            "event_schema_id",
            "event_schema_version",
            "organization_id",
            "occurred_at",
            "event_type",
            "actor_kind",
            "actor_identity_key",
            "target_identity_key",
            "operation_id",
            "operation_type",
            "risk_level",
            "target_kind",
            "target_sha256",
            "parameters_sha256",
            "protected_input_ref_sha256",
            "required_approvals",
            "approval_count",
            "expires_at",
        )
        with self.psycopg.connect(self.owner_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self.sql.SQL(
                        "SELECT event_schema_id, event_schema_version, organization_id, occurred_at, event_type, "
                        "actor_kind, actor_identity_key, target_identity_key, operation_id, operation_type, "
                        "risk_level, target_kind, target_sha256, parameters_sha256, protected_input_ref_sha256, "
                        "required_approvals, approval_count, expires_at "
                        "FROM {}.custody_control_events ORDER BY occurred_at, event_id"
                    ).format(self.sql.Identifier(self.schema))
                )
                rows = cursor.fetchall()
        result: list[dict[str, object]] = []
        for row in rows:
            event = dict(zip(fields, row, strict=True))
            event["occurred_at"] = event["occurred_at"].isoformat()
            if event["expires_at"] is not None:
                event["expires_at"] = event["expires_at"].isoformat()
            result.append(event)
        return result


if __name__ == "__main__":
    import unittest

    unittest.main()
