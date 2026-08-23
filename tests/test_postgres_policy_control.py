from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from hormuz.cli import build_parser, main
from hormuz.contracts import validate_contract, validate_policy_control_event
from hormuz.policy import PolicyEngine
from hormuz.policy_control import PolicyControlService
from hormuz.policy_repository import PolicyAdministrator, PolicyControlError
from hormuz.policy_runtime import PolicyRuntime
from hormuz.postgres_policy_store import PostgresPolicyControlStore
from hormuz.store import UsageStore
if __package__:
    from ._postgres_fixture import PostgresTestCase
else:  # Isolated wheel compatibility discovery uses the tests directory as its import root.
    from _postgres_fixture import PostgresTestCase


class PostgresPolicyControlTests(PostgresTestCase):
    def test_policy_control_bootstrap_activation_rollback_and_request_pinning(self) -> None:
        config, environment, _issuer = self._managed_config()
        service = PolicyControlService(config, environ=environment)

        administrators = service.bootstrap(
            organization_id="xpounder",
            credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
        )
        self.assertEqual(len(administrators), 1)
        self.assertEqual(administrators[0].actor_id, "alice")

        first = self._stage(service, environment=environment, document=self._policy_document())
        first_activation = service.activate(
            organization_id="xpounder",
            credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
            version_id=first.version_id,
        )
        self.assertEqual(first_activation.generation, 1)

        runtime_one = PolicyRuntime(config, environ=environment)
        runtime_two = PolicyRuntime(config, environ=environment)
        identity = config.identities_by_actor["alice"]
        with tempfile.TemporaryDirectory() as temporary:
            evidence_store = UsageStore(Path(temporary) / "usage.sqlite3")
            engine = PolicyEngine(config, evidence_store, policy_runtime=runtime_one)
            pinned = engine.evaluate(
                identity=identity,
                client="codex",
                protocol="openai",
                requested_model="gpt-5.4-mini",
                requested_output_tokens=100,
            )
            self.assertTrue(pinned.allowed)
            self.assertEqual(pinned.policy_version, first.version_id)

            second = self._stage(
                service,
                environment=environment,
                document=self._policy_document(openai_model="gpt-5.4"),
            )
            second_activation = service.activate(
                organization_id="xpounder",
                credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
                version_id=second.version_id,
            )
            self.assertEqual(second_activation.generation, 2)
            self.assertEqual(runtime_one.snapshot_for(identity).policy_version, second.version_id)
            self.assertEqual(runtime_two.snapshot_for(identity).policy_version, second.version_id)
            # The decision created before activation holds the exact policy
            # version used for that request's accounting and reservation path.
            self.assertEqual(pinned.snapshot.policy_version, first.version_id)
            evidence_store.record(
                identity=identity,
                client="codex",
                protocol="openai",
                requested_model="gpt-5.4-mini",
                resolved_alias="gpt-5.4-mini",
                upstream_model="gpt-5.4-mini",
                policy_version=pinned.policy_version,
                policy_action="allowed",
                status="succeeded",
            )
            self.assertEqual(
                evidence_store.audit_events(
                    since="2000-01-01T00:00:00+00:00",
                    organization_id="xpounder",
                )[0]["policy_version"],
                first.version_id,
            )

        rollback = service.rollback(
            organization_id="xpounder",
            credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
            version_id=first.version_id,
        )
        self.assertEqual(rollback.action, "policy_rolled_back")
        self.assertEqual(rollback.generation, 3)
        status = service.status(organization_id="xpounder", credential_env="HORMUZ_POLICY_ADMIN_TOKEN")
        self.assertTrue(status.initialized)
        self.assertEqual(status.active.version_id if status.active else None, first.version_id)
        self.assertEqual({version.version_id for version in status.versions}, {first.version_id, second.version_id})

        with self.psycopg.connect(self.owner_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self.sql.SQL(
                        "SELECT event_schema_id, event_schema_version, organization_id, occurred_at, event_type, "
                        "actor_kind, actor_identity_key, target_identity_key, version_id, generation, reason_code, "
                        "change_summary "
                        "FROM {}.policy_control_events ORDER BY occurred_at"
                    ).format(self.sql.Identifier(self.schema))
                )
                events = cursor.fetchall()
        self.assertEqual(
            [event[4] for event in events],
            [
                "bootstrap_initialized",
                "policy_staged",
                "policy_activated",
                "policy_staged",
                "policy_activated",
                "policy_rolled_back",
            ],
        )
        self.assertTrue(all(event[0] == "hormuz.policy-control-event" and event[1] == 1 for event in events))
        event_fields = (
            "event_schema_id",
            "event_schema_version",
            "organization_id",
            "occurred_at",
            "event_type",
            "actor_kind",
            "actor_identity_key",
            "target_identity_key",
            "version_id",
            "generation",
            "reason_code",
            "change_summary",
        )
        for event in events:
            event_payload = dict(zip(event_fields, event, strict=True))
            event_payload["occurred_at"] = event_payload["occurred_at"].isoformat()
            validate_policy_control_event(event_payload)
        staged_summaries = [event[11] for event in events if event[4] == "policy_staged"]
        self.assertTrue(all("gpt-5.4" not in summary and "10000" not in summary for summary in staged_summaries))
    def test_policy_cli_uses_the_authenticated_service_boundary(self) -> None:
        config, environment, _issuer = self._managed_config()
        with tempfile.TemporaryDirectory() as temporary:
            policy_path = Path(temporary) / "policy.json"
            policy_path.write_text(json.dumps(self._policy_document()), encoding="utf-8")
            with mock.patch.dict(os.environ, environment, clear=True):
                bootstrap_output = io.StringIO()
                with redirect_stdout(bootstrap_output):
                    self.assertEqual(
                        main(
                            [
                                "--config",
                                str(config.source_path),
                                "policy",
                                "bootstrap",
                                "--organization",
                                "xpounder",
                                "--credential-env",
                                "HORMUZ_POLICY_ADMIN_TOKEN",
                            ]
                        ),
                        0,
                    )
                self.assertIn("policy bootstrap initialized", bootstrap_output.getvalue())
                stage_output = io.StringIO()
                with redirect_stdout(stage_output):
                    self.assertEqual(
                        main(
                            [
                                "--config",
                                str(config.source_path),
                                "policy",
                                "stage",
                                "--organization",
                                "xpounder",
                                "--credential-env",
                                "HORMUZ_POLICY_ADMIN_TOKEN",
                                "--file",
                                str(policy_path),
                            ]
                        ),
                        0,
                    )
                self.assertIn("policy staged: organization=xpounder version=sha256:", stage_output.getvalue())
                version_id = stage_output.getvalue().split("version=", 1)[1].split()[0]
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(
                        main(
                            [
                                "--config",
                                str(config.source_path),
                                "policy",
                                "activate",
                                "--organization",
                                "xpounder",
                                "--credential-env",
                                "HORMUZ_POLICY_ADMIN_TOKEN",
                                "--version",
                                version_id,
                            ]
                        ),
                        0,
                    )
                client_output = io.StringIO()
                with redirect_stdout(client_output):
                    self.assertEqual(
                        main(
                            [
                                "--config",
                                str(config.source_path),
                                "client-config",
                                "codex",
                                "--url",
                                "https://hormuz.example",
                            ]
                        ),
                        0,
                    )
                self.assertIn('model = "gpt-5.4-mini"', client_output.getvalue())
                status_output = io.StringIO()
                with redirect_stdout(status_output):
                    self.assertEqual(
                        main(
                            [
                                "--config",
                                str(config.source_path),
                                "policy",
                                "status",
                                "--organization",
                                "xpounder",
                                "--credential-env",
                                "HORMUZ_POLICY_ADMIN_TOKEN",
                                "--json",
                            ]
                        ),
                        0,
                    )
        status = json.loads(status_output.getvalue())
        validate_contract(status)
        self.assertEqual(status["administrators"][0]["actor_id"], "alice")
        parsed = build_parser().parse_args(
            [
                "policy",
                "stage",
                "--organization",
                "xpounder",
                "--credential-env",
                "HORMUZ_POLICY_ADMIN_TOKEN",
                "--file",
                "policy.json",
            ]
        )
        self.assertFalse(hasattr(parsed, "actor"))
    def test_policy_bootstrap_cannot_drift_and_non_administrator_cannot_change_policy(self) -> None:
        config, environment, _issuer = self._managed_config(include_bob=True)
        service = PolicyControlService(config, environ=environment)
        service.bootstrap(organization_id="xpounder", credential_env="HORMUZ_POLICY_ADMIN_TOKEN")

        # The initialization marker is the first post-bootstrap authority
        # lookup. Even a later configuration that no longer describes the
        # tenant must not be consulted before that database check.
        original_identity = config.identities_by_token[environment["HORMUZ_POLICY_ADMIN_TOKEN"]]
        config_without_tenant = replace(
            config,
            identities_by_token={
                environment["HORMUZ_POLICY_ADMIN_TOKEN"]: replace(original_identity, organization_id="moved-tenant")
            },
        )
        with self.assertRaises(PolicyControlError) as raised:
            PolicyControlService(config_without_tenant, environ=environment).bootstrap(
                organization_id="xpounder",
                credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
            )
        self.assertEqual(raised.exception.code, "policy_bootstrap_already_initialized")

        with self.assertRaises(PolicyControlError) as raised:
            self._stage(
                service,
                environment=environment,
                credential_env="HORMUZ_POLICY_BOB_TOKEN",
                document=self._policy_document(),
            )
        self.assertEqual(raised.exception.code, "policy_administrator_required")

        drifted_config, drifted_environment, _issuer = self._managed_config(
            include_bob=True,
            bootstrap_bob=True,
        )
        drifted_service = PolicyControlService(drifted_config, environ=drifted_environment)
        with self.assertRaises(PolicyControlError) as raised:
            drifted_service.bootstrap(
                organization_id="xpounder",
                credential_env="HORMUZ_POLICY_BOB_TOKEN",
            )
        self.assertEqual(raised.exception.code, "policy_bootstrap_already_initialized")
        with self.assertRaises(PolicyControlError) as raised:
            self._stage(
                drifted_service,
                environment=drifted_environment,
                credential_env="HORMUZ_POLICY_BOB_TOKEN",
                document=self._policy_document(),
            )
        self.assertEqual(raised.exception.code, "policy_administrator_required")

        status = service.status(organization_id="xpounder", credential_env="HORMUZ_POLICY_ADMIN_TOKEN")
        self.assertEqual([(admin.authentication_kind, admin.actor_id) for admin in status.administrators], [("static", "alice")])
    def test_explicit_oidc_administrator_is_separate_from_runtime_entitlement(self) -> None:
        config, environment, issuer = self._managed_config(include_oidc=True)
        assert issuer is not None
        service = PolicyControlService(config, environ=environment)
        service.bootstrap(organization_id="xpounder", credential_env="HORMUZ_POLICY_ADMIN_TOKEN")
        granted = service.grant_oidc_administrator(
            organization_id="xpounder",
            credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
            issuer=issuer,
            subject="unmapped-policy-admin",
        )
        self.assertEqual((granted.organization_id, granted.issuer, granted.subject), ("xpounder", issuer, "unmapped-policy-admin"))

        blocked = self._stage(service, environment=environment, document=self._policy_document(actor_blocked=True))
        service.activate(
            organization_id="xpounder",
            credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
            version_id=blocked.version_id,
        )
        identity = config.identities_by_actor["alice"]
        decision = PolicyEngine(
            config,
            UsageStore(Path(tempfile.mkdtemp()) / "usage.sqlite3"),
            policy_runtime=PolicyRuntime(config, environ=environment),
        ).evaluate(
            identity=identity,
            client="codex",
            protocol="openai",
            requested_model="gpt-5.4-mini",
            requested_output_tokens=100,
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.action, "denied")
        # Alice remains a policy authority even while the active policy denies
        # her inference request; authorization roles do not imply entitlement.
        self._stage(service, environment=environment, document=self._policy_document())

        status = service.status(organization_id="xpounder", credential_env="HORMUZ_POLICY_ADMIN_TOKEN")
        self.assertIn(
            ("oidc", issuer, "unmapped-policy-admin"),
            [(admin.authentication_kind, admin.issuer, admin.subject) for admin in status.administrators],
        )
    def test_policy_roles_are_separated_and_break_glass_requires_admin_loss(self) -> None:
        config, environment, issuer = self._managed_config(include_oidc=True, break_glass=True)
        assert issuer is not None
        service = PolicyControlService(config, environ=environment)
        service.bootstrap(organization_id="xpounder", credential_env="HORMUZ_POLICY_ADMIN_TOKEN")
        version = self._stage(service, environment=environment, document=self._policy_document())
        service.activate(
            organization_id="xpounder",
            credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
            version_id=version.version_id,
        )

        with self.psycopg.connect(self.runtime_dsn) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(self.sql.SQL("SET LOCAL ROLE {}").format(self.sql.Identifier(self.runtime_role)))
                    cursor.execute(
                        self.sql.SQL("SET LOCAL search_path TO {}, pg_catalog").format(self.sql.Identifier(self.schema))
                    )
                    cursor.execute("SELECT set_config('hormuz.organization_id', %s, true)", ("xpounder",))
                    cursor.execute("SELECT COUNT(*) FROM policy_active_versions")
                    self.assertEqual(cursor.fetchone()[0], 1)
                    with self.assertRaises(self.psycopg.Error):
                        cursor.execute("SELECT COUNT(*) FROM policy_administrators")

        with self.psycopg.connect(self.policy_control_dsn) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        self.sql.SQL("SET LOCAL ROLE {}").format(self.sql.Identifier(self.policy_control_role))
                    )
                    cursor.execute(
                        self.sql.SQL("SET LOCAL search_path TO {}, pg_catalog").format(self.sql.Identifier(self.schema))
                    )
                    cursor.execute("SELECT set_config('hormuz.organization_id', %s, true)", ("xpounder",))
                    with self.assertRaises(self.psycopg.Error):
                        cursor.execute("UPDATE policy_versions SET author_kind = 'oidc'")
                    with self.assertRaises(self.psycopg.Error):
                        cursor.execute("UPDATE policy_tenants SET initialized_at = initialized_at")

        with self.assertRaises(PolicyControlError) as raised:
            service.break_glass_recover(
                organization_id="xpounder",
                recovery_secret=environment["HORMUZ_POLICY_BREAK_GLASS_TOKEN"],
                issuer=issuer,
                subject="recovery-administrator",
                reason_code="all_administrators_lost",
            )
        self.assertEqual(raised.exception.code, "policy_break_glass_not_required")

        secondary = service.grant_oidc_administrator(
            organization_id="xpounder",
            credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
            issuer=issuer,
            subject="secondary-administrator",
        )
        repository = PostgresPolicyControlStore(
            self.policy_control_dsn,
            config=config,
            schema=self.schema,
            policy_control_role=self.policy_control_role,
        )
        with self.assertRaises(PolicyControlError) as raised:
            repository.grant_administrator(
                organization_id="xpounder",
                caller=PolicyAdministrator(
                    organization_id="xpounder",
                    authentication_kind="static",
                    actor_id="alice",
                ),
                administrator=PolicyAdministrator(
                    organization_id="xpounder",
                    authentication_kind="static",
                    actor_id="not-a-new-administrator",
                ),
            )
        self.assertEqual(raised.exception.code, "policy_static_administrator_grant_denied")
        service.revoke_static_administrator(
            organization_id="xpounder",
            credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
            actor_id="alice",
        )
        with self.assertRaises(PolicyControlError) as raised:
            repository.revoke_administrator(
                organization_id="xpounder",
                caller=secondary,
                administrator=secondary,
            )
        self.assertEqual(raised.exception.code, "policy_last_administrator_revoke_denied")

        # This owner-only mutation simulates a real loss of every authority.
        # Normal policy-control commands cannot perform this mutation because
        # revoking the final administrator is explicitly rejected.
        with self.psycopg.connect(self.owner_dsn) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        self.sql.SQL(
                            "UPDATE {}.policy_administrators "
                            "SET active = FALSE, revoked_at = CURRENT_TIMESTAMP, "
                            "revoked_by_kind = 'oidc', revoked_by_identity_key = 'owner-recovery-simulation'"
                        ).format(
                            self.sql.Identifier(self.schema)
                        )
                    )
        recovered = service.break_glass_recover(
            organization_id="xpounder",
            recovery_secret=environment["HORMUZ_POLICY_BREAK_GLASS_TOKEN"],
            issuer=issuer,
            subject="recovery-administrator",
            reason_code="all_administrators_lost",
        )
        self.assertEqual((recovered.issuer, recovered.subject), (issuer, "recovery-administrator"))
        recovered_status = repository.status(
            organization_id="xpounder",
            caller=PolicyAdministrator(
                organization_id="xpounder",
                authentication_kind="oidc",
                issuer=issuer,
                subject="recovery-administrator",
            ),
        )
        self.assertEqual(
            [(administrator.issuer, administrator.subject) for administrator in recovered_status.administrators],
            [(issuer, "recovery-administrator")],
        )
        with self.psycopg.connect(self.owner_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self.sql.SQL(
                        "SELECT event_type, actor_kind, reason_code FROM {}.policy_control_events "
                        "WHERE event_type = 'break_glass_recovered'"
                    ).format(self.sql.Identifier(self.schema))
                )
                event = cursor.fetchone()
        self.assertEqual(event, ("break_glass_recovered", "break_glass", "all_administrators_lost"))


if __name__ == "__main__":
    unittest.main()
