from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from hormuz.cli import build_parser, main
from hormuz.config import ConfigError, GatewayConfig
from hormuz.custody_repository import (
    CUSTODY_DESTRUCTIVE_OPERATIONS,
    CUSTODY_OPERATIONS,
    CUSTODY_ROUTINE_OPERATIONS,
    CustodyApproval,
    CustodyOperationIntent,
    operation_target_kind,
    required_approvals,
)


ROOT = Path(__file__).resolve().parents[1]
_DIGEST_A = "0" * 64
_DIGEST_B = "1" * 64
_DIGEST_C = "2" * 64
_IDENTITY_KEY = "static:" + "3" * 64


class CustodyControlUnitTests(unittest.TestCase):
    def test_precise_operation_vocabulary_has_fixed_risk_and_target_semantics(self) -> None:
        self.assertEqual(CUSTODY_OPERATIONS, CUSTODY_ROUTINE_OPERATIONS | CUSTODY_DESTRUCTIVE_OPERATIONS)
        self.assertEqual(required_approvals("seal_envelope"), 1)
        self.assertEqual(required_approvals("retire_envelope"), 2)
        self.assertEqual(operation_target_kind("disable_provider_credential"), "provider_credential")
        self.assertNotIn("revoke", CUSTODY_OPERATIONS)

    def test_expired_state_is_derived_without_rewriting_authorization_history(self) -> None:
        created_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        operation = CustodyOperationIntent(
            organization_id="xpounder",
            operation_id="01234567-89ab-4def-8123-456789abcdef",
            operation_type="retire_key_reference",
            risk_level="destructive",
            target_kind="key_reference",
            target_sha256=_DIGEST_A,
            parameters_sha256=_DIGEST_B,
            protected_input_ref_sha256=None,
            state="pending",
            required_approvals=2,
            approvals=(
                CustodyApproval(
                    approver_kind="static",
                    approver_identity_key=_IDENTITY_KEY,
                    approved_at=created_at,
                ),
            ),
            created_at=created_at,
            expires_at=created_at + timedelta(minutes=5),
            authorized_at=None,
            requested_by_kind="static",
            requested_by_identity_key=_IDENTITY_KEY,
        )
        self.assertEqual(operation.state, "pending")
        self.assertEqual(operation.effective_state(), "expired")

        authorized = replace(
            operation,
            operation_type="verify_restore",
            risk_level="routine",
            target_kind="restore",
            required_approvals=1,
            authorized_at=created_at,
            state="authorized",
        )
        self.assertEqual(authorized.state, "authorized")
        self.assertEqual(authorized.effective_state(), "expired")

    def test_seal_requires_only_a_protected_input_reference_digest(self) -> None:
        now = datetime.now(timezone.utc)
        operation = CustodyOperationIntent(
            organization_id="xpounder",
            operation_id="01234567-89ab-4def-8123-456789abcdef",
            operation_type="seal_envelope",
            risk_level="routine",
            target_kind="envelope",
            target_sha256=_DIGEST_A,
            parameters_sha256=_DIGEST_B,
            protected_input_ref_sha256=_DIGEST_C,
            state="authorized",
            required_approvals=1,
            approvals=(
                CustodyApproval(
                    approver_kind="static",
                    approver_identity_key=_IDENTITY_KEY,
                    approved_at=now,
                ),
            ),
            created_at=now,
            expires_at=now + timedelta(minutes=15),
            authorized_at=now,
            requested_by_kind="static",
            requested_by_identity_key=_IDENTITY_KEY,
        )
        self.assertEqual(operation.protected_input_ref_sha256, _DIGEST_C)
        self.assertFalse(hasattr(operation, "plaintext"))
        with self.assertRaises(ValueError):
            replace(operation, protected_input_ref_sha256=None)

    def test_managed_configuration_requires_distinct_control_credentials_and_roles(self) -> None:
        value, environment = self._managed_value()
        config = self._load(value, environment)
        self.assertEqual(config.custody_control.mode, "postgresql")
        self.assertNotEqual(
            config.custody_control.postgres_control_dsn_env,
            config.usage_storage.postgres_dsn_env,
        )
        self.assertNotEqual(
            config.custody_control.postgres_control_role,
            config.usage_storage.postgres_runtime_role,
        )
        self.assertNotEqual(
            config.custody_control.postgres_control_dsn_env,
            config.policy_control.postgres_control_dsn_env,
        )
        self.assertNotEqual(
            config.custody_control.postgres_control_role,
            config.policy_control.postgres_control_role,
        )
        self.assertEqual(config.custody_executor.postgres_executor_dsn_env, "TEST_EXECUTOR_DSN")
        self.assertEqual(config.custody_executor.postgres_executor_role, "hormuz_custody_executor_test")
        self.assertNotEqual(
            config.custody_executor.postgres_executor_dsn_env,
            config.custody_control.postgres_control_dsn_env,
        )
        self.assertNotEqual(
            config.custody_executor.postgres_executor_role,
            config.custody_control.postgres_control_role,
        )

        duplicate_dsn = json.loads(json.dumps(value))
        duplicate_dsn["custody_control"]["postgres_control_dsn_env"] = "TEST_RUNTIME_DSN"
        with self.assertRaises(ConfigError):
            self._load(duplicate_dsn, environment)

        duplicate_role = json.loads(json.dumps(value))
        duplicate_role["custody_control"]["postgres_control_role"] = "hormuz_runtime_test"
        with self.assertRaises(ConfigError):
            self._load(duplicate_role, environment)

        duplicate_policy_dsn = json.loads(json.dumps(value))
        duplicate_policy_dsn["custody_control"]["postgres_control_dsn_env"] = "TEST_POLICY_DSN"
        with self.assertRaises(ConfigError):
            self._load(duplicate_policy_dsn, environment)

        duplicate_policy_role = json.loads(json.dumps(value))
        duplicate_policy_role["custody_control"]["postgres_control_role"] = "hormuz_policy_control_test"
        with self.assertRaises(ConfigError):
            self._load(duplicate_policy_role, environment)

        duplicate_executor_dsn = json.loads(json.dumps(value))
        duplicate_executor_dsn["custody_executor"]["postgres_executor_dsn_env"] = "TEST_CUSTODY_DSN"
        with self.assertRaises(ConfigError):
            self._load(duplicate_executor_dsn, environment)

        duplicate_executor_role = json.loads(json.dumps(value))
        duplicate_executor_role["custody_executor"]["postgres_executor_role"] = "hormuz_custody_control_test"
        with self.assertRaises(ConfigError):
            self._load(duplicate_executor_role, environment)

        local_executor = json.loads(json.dumps(value))
        local_executor["custody_control"] = {"mode": "local"}
        with self.assertRaises(ConfigError):
            self._load(local_executor, environment)

        missing_keys = json.loads(json.dumps(value))
        missing_keys.pop("key_custody")
        with self.assertRaises(ConfigError):
            self._load(missing_keys, environment)

        missing_retention = json.loads(json.dumps(value))
        missing_retention.pop("custody_retention")
        with self.assertRaises(ConfigError) as raised:
            self._load(missing_retention, environment)
        self.assertEqual(
            str(raised.exception),
            "custody_control.mode postgresql requires custody_retention",
        )

        invalid_retention = json.loads(json.dumps(value))
        invalid_retention["custody_retention"] = {"retention_days": 0, "legal_hold": False}
        with self.assertRaises(ConfigError):
            self._load(invalid_retention, environment)

    def test_lifecycle_configuration_binds_immutable_asset_generations_without_exposing_bindings_to_evidence(self) -> None:
        value, environment = self._managed_value()
        key_references = value["key_custody"]["key_references"]  # type: ignore[index]
        value["custody_lifecycle"] = {
            "freshness_lease_seconds": 5,
            "assets": [
                {
                    "asset_type": "provider_credential",
                    "asset_id": "openai-primary",
                    "generation": 1,
                    "binding": {"protocol": "openai"},
                },
                {
                    "asset_type": "provider_credential",
                    "asset_id": "anthropic-primary",
                    "generation": 1,
                    "binding": {"protocol": "anthropic"},
                },
                *[
                    {
                        "asset_type": "key_reference",
                        "asset_id": f"{purpose}-current",
                        "generation": 1,
                        "binding": {"purpose": purpose, "key_reference": reference},
                    }
                    for purpose, reference in key_references.items()
                ],
            ],
        }

        config = self._load(value, environment)
        assert config.custody_lifecycle is not None
        provider = config.custody_lifecycle.assets.asset(
            organization_id="xpounder",
            asset_type="provider_credential",
            asset_id="openai-primary",
            generation=1,
        )
        self.assertEqual(provider.audit_ref()["asset_id"], "openai-primary")
        self.assertNotIn("OPENAI_API_KEY", str(provider.audit_ref()))
        self.assertNotIn("OPENAI_API_KEY", repr(provider))

        variable_lease = json.loads(json.dumps(value))
        variable_lease["custody_lifecycle"]["freshness_lease_seconds"] = 6  # type: ignore[index]
        with self.assertRaises(ConfigError):
            self._load(variable_lease, environment)

        missing_provider = json.loads(json.dumps(value))
        missing_provider["custody_lifecycle"]["assets"] = missing_provider["custody_lifecycle"]["assets"][1:]  # type: ignore[index]
        with self.assertRaises(ConfigError) as raised:
            self._load(missing_provider, environment)
        self.assertEqual(
            str(raised.exception),
            "custody_lifecycle requires exactly one provider credential asset per configured upstream",
        )

        path_like_id = json.loads(json.dumps(value))
        path_like_id["custody_lifecycle"]["assets"][0]["asset_id"] = "/private/customer/openai.key"  # type: ignore[index]
        with self.assertRaises(ConfigError) as raised:
            self._load(path_like_id, environment)
        self.assertEqual(
            str(raised.exception),
            "custody_lifecycle.assets[0].asset_id must be a safe immutable asset identifier",
        )

        unmanaged = json.loads(json.dumps(value))
        unmanaged["custody_control"] = {"mode": "local"}
        unmanaged.pop("custody_executor")
        unmanaged.pop("custody_retention")
        with self.assertRaises(ConfigError) as raised:
            self._load(unmanaged, environment)
        self.assertEqual(str(raised.exception), "custody_lifecycle requires custody_control.mode postgresql")

    def test_governed_mode_blocks_direct_cli_execution_before_plaintext_lookup(self) -> None:
        value, environment = self._managed_value()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "hormuz.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            errors = io.StringIO()
            from unittest import mock

            with mock.patch.dict("os.environ", environment, clear=True), redirect_stderr(errors):
                result = main(
                    [
                        "--config",
                        str(path),
                        "custody",
                        "seal",
                        "--purpose",
                        "provider_credential",
                        "--input-env",
                        "PLAINTEXT_NOT_PRESENT",
                        "--output",
                        str(Path(temporary) / "secret.envelope"),
                    ]
                )
        self.assertEqual(result, 2)
        self.assertEqual(errors.getvalue(), "custody control error: custody_governed_executor_required\n")

    def test_cli_accepts_only_digest_authorization_inputs_and_no_self_asserted_actor(self) -> None:
        parsed = build_parser().parse_args(
            [
                "custody",
                "authorize",
                "--organization",
                "xpounder",
                "--operation",
                "seal_envelope",
                "--target-sha256",
                _DIGEST_A,
                "--parameters-sha256",
                _DIGEST_B,
                "--protected-input-ref-sha256",
                _DIGEST_C,
            ]
        )
        self.assertFalse(hasattr(parsed, "actor"))
        self.assertFalse(hasattr(parsed, "plaintext"))
        self.assertEqual(parsed.credential_env, "HORMUZ_CUSTODY_ADMIN_TOKEN")

        export = build_parser().parse_args(
            [
                "custody",
                "evidence",
                "export",
                "--organization",
                "xpounder",
            ]
        )
        self.assertEqual(export.custody_evidence_command, "export")
        self.assertFalse(hasattr(export, "actor"))
        self.assertFalse(hasattr(export, "plaintext"))

        deletion_check = build_parser().parse_args(
            [
                "custody",
                "evidence",
                "deletion-check",
                "--organization",
                "xpounder",
                "--source-schema-id",
                "hormuz.custody-control-event",
                "--source-schema-version",
                "1",
                "--source-event-id",
                "11111111-1111-4111-8111-111111111111",
            ]
        )
        self.assertEqual(deletion_check.custody_evidence_command, "deletion-check")
        self.assertFalse(hasattr(deletion_check, "delete"))

    def test_machine_catalog_registration_has_no_human_or_plaintext_arguments(self) -> None:
        parsed = build_parser().parse_args(["custody-executor", "register-assets"])

        self.assertEqual(parsed.command, "custody-executor")
        self.assertEqual(parsed.custody_executor_command, "register-assets")
        self.assertFalse(hasattr(parsed, "actor"))
        self.assertFalse(hasattr(parsed, "credential_env"))
        self.assertFalse(hasattr(parsed, "plaintext"))

    def test_machine_catalog_registration_routes_only_through_executor_service(self) -> None:
        config = SimpleNamespace(
            organization_ids=("xpounder",),
            custody_lifecycle=SimpleNamespace(assets=SimpleNamespace(assets=(object(), object()))),
        )
        service = mock.Mock()
        executor_factory = mock.Mock(return_value=service)
        output = io.StringIO()
        with (
            mock.patch("hormuz.cli.GatewayConfig.load", return_value=config),
            mock.patch("hormuz.cli.CustodyExecutorService", executor_factory),
            redirect_stdout(output),
        ):
            self.assertEqual(main(["--config", "ignored.json", "custody-executor", "register-assets"]), 0)
        executor_factory.assert_called_once_with(config)
        service.register_asset_catalog.assert_called_once_with()
        self.assertEqual(output.getvalue(), "custody asset catalog registered: organizations=1 assets=2\n")

    def _managed_value(self) -> tuple[dict[str, object], dict[str, str]]:
        value = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        value["usage_storage"] = {
            "backend": "postgresql",
            "postgres_dsn_env": "TEST_RUNTIME_DSN",
            "postgres_migration_dsn_env": "TEST_MIGRATION_DSN",
            "postgres_schema": "hormuz_test",
            "postgres_runtime_role": "hormuz_runtime_test",
        }
        value["key_custody"] = {
            "backend": "openbao-transit",
            "endpoint_url": "http://127.0.0.1:8200",
            "token_env": "HORMUZ_OPENBAO_TOKEN",
            "transit_mount": "transit",
            "key_references": {
                "provider_credential": "provider-key",
                "identity_connector_secret": "identity-key",
                "session_material": "session-key",
                "approval_fingerprint": "approval-key",
                "data_encryption": "data-key",
            },
        }
        value["policy_control"] = {
            "mode": "postgresql",
            "postgres_control_dsn_env": "TEST_POLICY_DSN",
            "postgres_control_role": "hormuz_policy_control_test",
            "bootstrap_administrators": [
                {"organization_id": "xpounder", "actor_id": "alice"}
            ],
        }
        value.pop("policies", None)
        value["custody_control"] = {
            "mode": "postgresql",
            "postgres_control_dsn_env": "TEST_CUSTODY_DSN",
            "postgres_control_role": "hormuz_custody_control_test",
            "authorization_ttl_seconds": 900,
            "bootstrap_administrators": [
                {"organization_id": "xpounder", "actor_id": "alice"}
            ],
        }
        value["custody_retention"] = {
            "retention_days": 365,
            "legal_hold": False,
        }
        value["custody_executor"] = {
            "postgres_executor_dsn_env": "TEST_EXECUTOR_DSN",
            "postgres_executor_role": "hormuz_custody_executor_test",
            "pending_attempt_ttl_seconds": 900,
        }
        return value, {
            "HORMUZ_TOKEN": "custody-test-alice-token",
            "TEST_RUNTIME_DSN": "postgresql://runtime.example/hormuz",
            "TEST_MIGRATION_DSN": "postgresql://migration.example/hormuz",
            "TEST_CUSTODY_DSN": "postgresql://custody.example/hormuz",
            "TEST_EXECUTOR_DSN": "postgresql://executor.example/hormuz",
            "TEST_POLICY_DSN": "postgresql://policy.example/hormuz",
            "HORMUZ_OPENBAO_TOKEN": "openbao-test-token-value",
        }

    def _load(self, value: dict[str, object], environment: dict[str, str]) -> GatewayConfig:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "hormuz.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return GatewayConfig.load(path, environ=environment)


if __name__ == "__main__":
    unittest.main()
