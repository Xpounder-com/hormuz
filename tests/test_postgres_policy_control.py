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

        with self.assertRaises(PolicyControlError) as raised:
            service.policy_version(
                organization_id="xpounder",
                credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
            )
        self.assertEqual(raised.exception.code, "policy_active_version_unavailable")

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

        self.assertEqual(
            service.policy_version(
                organization_id="xpounder",
                credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
            ).version_id,
            first.version_id,
        )
        self.assertEqual(
            service.policy_version(
                organization_id="xpounder",
                credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
                version_id=second.version_id,
            ).document.to_mapping(),
            second.document.to_mapping(),
        )
        with self.assertRaises(PolicyControlError) as raised:
            service.policy_version(
                organization_id="xpounder",
                credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
                version_id="sha256:" + "f" * 64,
            )
        self.assertEqual(raised.exception.code, "policy_version_not_found")

        history = service.history(
            organization_id="xpounder",
            credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
            limit=2,
        )
        self.assertEqual(history.limit, 2)
        self.assertTrue(history.has_more)
        self.assertEqual(
            [event.event_type for event in history.events],
            ["policy_rolled_back", "policy_activated"],
        )
        self.assertEqual([event.generation for event in history.events], [3, 2])
        self.assertEqual(history.events[0].content_sha256, first.content_sha256)
        self.assertEqual(history.events[1].content_sha256, second.content_sha256)
        self.assertEqual(
            [event.change_summary for event in history.events],
            [first.change_summary, second.change_summary],
        )
        self.assertTrue(all("gpt-5.4" not in json.dumps(event.change_summary) for event in history.events))
        complete_history = service.history(
            organization_id="xpounder",
            credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
            limit=20,
        )
        self.assertFalse(complete_history.has_more)
        self.assertEqual(
            [event.event_type for event in complete_history.events],
            [
                "policy_rolled_back",
                "policy_activated",
                "policy_staged",
                "policy_activated",
                "policy_staged",
            ],
        )
        self.assertEqual(
            [event.generation for event in complete_history.events],
            [3, 2, None, 1, None],
        )
        for invalid_limit in (0, 101):
            with self.assertRaises(PolicyControlError) as raised:
                service.history(
                    organization_id="xpounder",
                    credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
                    limit=invalid_limit,
                )
            self.assertEqual(raised.exception.code, "policy_history_limit_invalid")

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

    def test_atomic_apply_idempotency_guard_and_generation_rollback(self) -> None:
        config, environment, _issuer = self._managed_config()
        service = PolicyControlService(config, environ=environment)
        service.bootstrap(organization_id="xpounder", credential_env="HORMUZ_POLICY_ADMIN_TOKEN")

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "policy.json"

        def apply(
            document: dict[str, object],
            *,
            if_active_version_id: str | None = None,
        ):
            path.write_text(json.dumps(document), encoding="utf-8")
            return service.apply(
                organization_id="xpounder",
                credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
                policy_path=path,
                if_active_version_id=if_active_version_id,
            )

        first = apply(self._policy_document())
        self.assertEqual(first.generation, 1)
        with self.assertRaises(PolicyControlError) as raised:
            service.rollback(
                organization_id="xpounder",
                credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
            )
        self.assertEqual(raised.exception.code, "policy_rollback_predecessor_unavailable")

        repeated = apply(self._policy_document(), if_active_version_id=first.version_id)
        self.assertEqual((repeated.version_id, repeated.generation), (first.version_id, 1))

        second_record = self._stage(
            service,
            environment=environment,
            document=self._policy_document(openai_model="gpt-5.4"),
        )
        second = apply(
            self._policy_document(openai_model="gpt-5.4"),
            if_active_version_id=first.version_id,
        )
        self.assertEqual((second.version_id, second.generation), (second_record.version_id, 2))

        third_document = self._policy_document(actor_blocked=True)
        with self.assertRaises(PolicyControlError) as raised:
            apply(third_document, if_active_version_id=first.version_id)
        self.assertEqual(raised.exception.code, "policy_active_version_mismatch")
        status = service.status(organization_id="xpounder", credential_env="HORMUZ_POLICY_ADMIN_TOKEN")
        self.assertEqual(status.active.version_id if status.active else None, second.version_id)
        self.assertEqual(len(status.versions), 2)

        third = apply(third_document, if_active_version_id=second.version_id)
        self.assertEqual(third.generation, 3)

        with self.assertRaises(PolicyControlError) as raised:
            service.activate(
                organization_id="xpounder",
                credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
                version_id=first.version_id,
                if_active_version_id=first.version_id,
            )
        self.assertEqual(raised.exception.code, "policy_active_version_mismatch")

        undone = service.rollback(
            organization_id="xpounder",
            credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
            if_active_version_id=third.version_id,
        )
        self.assertEqual((undone.version_id, undone.generation), (second.version_id, 4))
        toggled = service.rollback(
            organization_id="xpounder",
            credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
            if_active_version_id=second.version_id,
        )
        self.assertEqual((toggled.version_id, toggled.generation), (third.version_id, 5))
        with self.assertRaises(PolicyControlError) as raised:
            service.rollback(
                organization_id="xpounder",
                credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
                if_active_version_id=first.version_id,
            )
        self.assertEqual(raised.exception.code, "policy_active_version_mismatch")

        history = service.history(
            organization_id="xpounder",
            credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
            limit=20,
        )
        self.assertEqual(
            [(event.event_type, event.version_id, event.generation) for event in reversed(history.events)],
            [
                ("policy_staged", first.version_id, None),
                ("policy_activated", first.version_id, 1),
                ("policy_staged", second.version_id, None),
                ("policy_activated", second.version_id, 2),
                ("policy_staged", third.version_id, None),
                ("policy_activated", third.version_id, 3),
                ("policy_rolled_back", second.version_id, 4),
                ("policy_rolled_back", third.version_id, 5),
            ],
        )

        repository = service._repository
        original_record_event = repository._record_event

        def fail_activation_event(cursor, **kwargs):
            if kwargs["event_type"] == "policy_activated":
                raise PolicyControlError("forced_activation_failure")
            return original_record_event(cursor, **kwargs)

        failed_document = self._policy_document()
        organization_policy = failed_document["policies"]
        assert isinstance(organization_policy, dict)
        organization_policy = organization_policy["organization"]
        assert isinstance(organization_policy, dict)
        organization_policy["max_output_tokens"] = 31_000
        before_failure = service.status(
            organization_id="xpounder",
            credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
        )
        with (
            mock.patch.object(repository, "_record_event", side_effect=fail_activation_event),
            self.assertRaises(PolicyControlError) as raised,
        ):
            apply(failed_document, if_active_version_id=third.version_id)
        self.assertEqual(raised.exception.code, "forced_activation_failure")
        after_failure = service.status(
            organization_id="xpounder",
            credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
        )
        self.assertEqual(after_failure.active, before_failure.active)
        self.assertEqual(
            {version.version_id for version in after_failure.versions},
            {version.version_id for version in before_failure.versions},
        )

    def test_policy_cli_uses_the_authenticated_service_boundary(self) -> None:
        config, environment, _issuer = self._managed_config()
        with tempfile.TemporaryDirectory() as temporary:
            policy_path = Path(temporary) / "policy.json"
            policy_path.write_text(json.dumps(self._policy_document()), encoding="utf-8")
            candidate_path = Path(temporary) / "candidate.json"
            candidate_path.write_text(
                json.dumps(self._policy_document(actor_blocked=True)),
                encoding="utf-8",
            )
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
                                "client",
                                "config",
                                "codex",
                                "--url",
                                "https://hormuz.example",
                            ]
                        ),
                        0,
                    )
                self.assertIn('model = "gpt-5.4-mini"', client_output.getvalue())
                usage_before = self._schema_v4_snapshot(self.schema)
                compare_output = io.StringIO()
                with redirect_stdout(compare_output):
                    self.assertEqual(
                        main(
                            [
                                "--config",
                                str(config.source_path),
                                "policy",
                                "compare",
                                str(candidate_path),
                                "--organization",
                                "xpounder",
                                "--credential-env",
                                "HORMUZ_POLICY_ADMIN_TOKEN",
                                "--json",
                            ]
                        ),
                        1,
                    )
                comparison = json.loads(compare_output.getvalue())
                validate_contract(comparison)
                self.assertEqual(comparison["baseline"]["version_id"], version_id)
                self.assertEqual(comparison["changes"][0]["path"], "policies.actors.alice.allowed_models")

                preview_output = io.StringIO()
                with redirect_stdout(preview_output):
                    self.assertEqual(
                        main(
                            [
                                "--config",
                                str(config.source_path),
                                "policy",
                                "preview",
                                str(candidate_path),
                                "--organization",
                                "xpounder",
                                "--credential-env",
                                "HORMUZ_POLICY_ADMIN_TOKEN",
                                "--actor",
                                "alice",
                                "--client",
                                "codex",
                                "--protocol",
                                "openai",
                                "--model",
                                "gpt-5.4-mini",
                                "--max-output-tokens",
                                "1000",
                                "--json",
                            ]
                        ),
                        3,
                    )
                preview = json.loads(preview_output.getvalue())
                validate_contract(preview)
                self.assertEqual(preview["baseline"]["version_id"], version_id)
                self.assertTrue(preview["baseline"]["decision"]["allowed"])
                self.assertFalse(preview["candidate"]["decision"]["allowed"])
                self.assertEqual(preview["usage_basis"], "current")
                self.assertEqual(self._schema_v4_snapshot(self.schema), usage_before)
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
        with self.assertRaises(PolicyControlError) as raised:
            service.history(
                organization_id="xpounder",
                credential_env="HORMUZ_POLICY_BOB_TOKEN",
                limit=20,
            )
        self.assertEqual(raised.exception.code, "policy_administrator_required")
        with self.assertRaises(PolicyControlError) as raised:
            service.policy_version(
                organization_id="xpounder",
                credential_env="HORMUZ_POLICY_BOB_TOKEN",
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
