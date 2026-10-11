"""Pure synthetic config/env witnesses; no listeners, stores, or upstream calls."""

from __future__ import annotations

import copy
import io
import json
import logging
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stderr, redirect_stdout

from hormuz._hosted_config import HostedError
from hormuz._hosted_provider import PROVIDER_DEPLOYMENT_METADATA_NAMES
from hormuz._hosted_work import load_profile
from hormuz.hosted import main, proxy_settings, runtime_settings
from tests._hosted_fixtures import provider_profile


BASE_SECRET_NAMES = frozenset({
    "HORMUZ_INGRESS_CREDENTIAL", "HORMUZ_SESSION_MASTER_KEY",
    "HORMUZ_OIDC_CLIENT_SECRET", "HORMUZ_OPENAI_PROVIDER_KEY",
    "HORMUZ_ANTHROPIC_PROVIDER_KEY", "HORMUZ_WORK_BILLING_WEBHOOK_SECRET",
    "HORMUZ_WORK_BILLING_API_KEY",
})
PROJECT = "20000000-0000-4000-8000-000000000001"


class _Environment(dict):
    def __init__(self, values=(), *, forbid_reads=False):
        super().__init__(values)
        self.reads = []
        self.forbid_reads = forbid_reads

    def get(self, name, default=None):
        self.reads.append(name)
        if self.forbid_reads:
            raise AssertionError("environment read before configuration rejection")
        return super().get(name, default)


@unittest.skipUnless(os.name == "posix", "Hosted configuration validates POSIX ownership")
class HostedIntegratedConfigTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(logging.disable, logging.root.manager.disable)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        _, _, self.environment, self.document = provider_profile(self.root)
        self.state = Path(self.document["database"]).parent
        self.document["usage_storage"] = {"backend": "sqlite"}
        broker = self.document["authentication"]["session_broker"]
        broker["workspace_enabled"] = True
        broker["workspace_signup_issuer"] = self.document["authentication"]["oidc"]["issuers"][0]["issuer"]
        self.document["ai_work"] = {
            "enabled": True, "database": str(self.state / "hormuz-work.sqlite3"),
            "require_paid": True, "billing_price_id": "price_Synthetic",
            "billing_portal_configuration_id": "bpc_Synthetic",
        }
        self.environment.update({
            "HORMUZ_HOSTED_MODE": "ai-work",
            "HORMUZ_WORK_BILLING_WEBHOOK_SECRET": "whsec_" + "synthetic_fixture_" * 2,
            "HORMUZ_WORK_BILLING_API_KEY": "sk_" + "live_" + "A" * 32,
            "UNRELATED_SECRET": "synthetic-secret-never-selected",
            "GITHUB_TOKEN": "synthetic-account-key-never-selected",
            "HORMUZ_POSTGRES_MIGRATION_DSN": "synthetic-migration-key-never-selected",
            "HORMUZ_DOMAIN_API_KEY": "synthetic-domain-key-not-configured",
        })
        self.path = self.root / "integrated.json"

    def write(self, document=None):
        self.path.write_text(json.dumps(self.document if document is None else document))
        self.path.chmod(0o600)

    def load(self, document=None, environment=None):
        self.write(document)
        return load_profile(self.path, self.environment if environment is None else environment)

    def assert_rejected_before_environment(self, document):
        environment = _Environment(self.environment, forbid_reads=True)
        with self.assertRaises(HostedError):
            self.load(document, environment)
        self.assertEqual([], environment.reads)

    def test_full_config_selects_exact_secret_inventory_without_creating_state(self):
        environment = _Environment(self.environment)
        config, selected = self.load(environment=environment)
        self.assertEqual("sqlite", config.usage_storage.backend)
        self.assertTrue(config.session_broker.workspace_enabled)
        self.assertTrue(config.ai_work.enabled)
        self.assertEqual("bpc_Synthetic", config.ai_work.billing_portal_configuration_id)
        self.assertEqual({"openai", "anthropic"}, set(config.upstreams))
        expected = BASE_SECRET_NAMES | set(PROVIDER_DEPLOYMENT_METADATA_NAMES) | {"HORMUZ_HOSTED_MODE"}
        self.assertEqual(expected, set(selected))
        self.assertEqual(BASE_SECRET_NAMES | set(PROVIDER_DEPLOYMENT_METADATA_NAMES), set(environment.reads))
        for name in BASE_SECRET_NAMES:
            self.assertEqual(self.environment[name], selected[name])
        self.assertEqual("ai-work", selected["HORMUZ_HOSTED_MODE"])
        self.assertFalse(self.state.exists(), "loading a profile must not initialize its stores")

    def test_configured_secret_names_replace_legacy_names_in_child_environment(self):
        changes = (
            (self.document["ingress"], "credential_env", "HORMUZ_INGRESS_CREDENTIAL", "SYNTHETIC_PROXY_SECRET"),
            (self.document["authentication"]["session_broker"], "master_key_env", "HORMUZ_SESSION_MASTER_KEY", "SYNTHETIC_SESSION_KEY"),
            (self.document["authentication"]["oidc"]["issuers"][0]["login"], "client_secret_env", "HORMUZ_OIDC_CLIENT_SECRET", "SYNTHETIC_LOGIN_SECRET"),
            (self.document["upstreams"]["openai"], "api_key_env", "HORMUZ_OPENAI_PROVIDER_KEY", "SYNTHETIC_OPENAI_KEY"),
            (self.document["upstreams"]["anthropic"], "api_key_env", "HORMUZ_ANTHROPIC_PROVIDER_KEY", "SYNTHETIC_ANTHROPIC_KEY"),
            (self.document["ai_work"], "billing_webhook_secret_env", "HORMUZ_WORK_BILLING_WEBHOOK_SECRET", "SYNTHETIC_BILLING_WEBHOOK"),
            (self.document["ai_work"], "billing_api_key_env", "HORMUZ_WORK_BILLING_API_KEY", "SYNTHETIC_BILLING_API_KEY"),
        )
        for item, field, original, renamed in changes:
            item[field] = renamed
            self.environment[renamed] = self.environment[original]
        self.document["egress_controls"]["secrets"]["custom_secret_envs"] = ["SYNTHETIC_REDACTION_SECRET"]
        self.environment["SYNTHETIC_REDACTION_SECRET"] = "synthetic-custom-detector-value"
        config, selected = self.load()
        self.assertTrue(BASE_SECRET_NAMES.isdisjoint(selected))
        expected = {change[3] for change in changes} | {"SYNTHETIC_REDACTION_SECRET"}
        self.assertEqual(expected | set(PROVIDER_DEPLOYMENT_METADATA_NAMES) | {"HORMUZ_HOSTED_MODE"}, set(selected))
        self.assertEqual(self.environment["SYNTHETIC_PROXY_SECRET"], config.ingress.credential)
        proxy = proxy_settings({"PORT": "10000", "HORMUZ_INGRESS_CREDENTIAL": config.ingress.credential, **selected}, active=True)
        self.assertEqual({"PORT", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "HORMUZ_INGRESS_CREDENTIAL"}, set(proxy))
        self.assertTrue(expected.isdisjoint(proxy))

    def test_ai_work_runtime_inventory_does_not_read_fixed_legacy_secrets(self):
        environment = _Environment(self.environment)
        with patch("hormuz.hosted.os.environ", environment):
            settings = runtime_settings()
        self.assertEqual("ai-work", settings["HORMUZ_HOSTED_MODE"])
        self.assertTrue(BASE_SECRET_NAMES.isdisjoint(environment.reads))
        self.assertTrue(BASE_SECRET_NAMES.isdisjoint(settings))
        self.assertNotIn("UNRELATED_SECRET", settings)

    def test_missing_required_keys_fail_before_a_listener_can_be_created(self):
        for name in sorted(BASE_SECRET_NAMES):
            with self.subTest(name=name):
                environment = dict(self.environment)
                environment.pop(name)
                with self.assertRaisesRegex(HostedError, "credential_missing_or_invalid"):
                    self.load(environment=environment)

    def test_invalid_json_and_unsupported_structure_do_not_read_environment(self):
        for raw in ('{"listen":', '{"listen":{},"listen":{}}', '[]'):
            with self.subTest(raw=raw):
                self.path.write_text(raw)
                self.path.chmod(0o600)
                environment = _Environment(self.environment, forbid_reads=True)
                with self.assertRaises(HostedError):
                    load_profile(self.path, environment)
                self.assertEqual([], environment.reads)
        document = copy.deepcopy(self.document)
        document["unreviewed_settings"] = {"secret_env": "UNRELATED_SECRET"}
        self.assert_rejected_before_environment(document)

    def test_mode_and_store_expansions_are_rejected_before_secret_reads(self):
        mutations = [
            (("listen", "host"), "0.0.0.0"),
            (("listen", "port"), 8788),
            (("ingress", "trusted_proxy_cidrs"), ["0.0.0.0/0"]),
            (("usage_storage", "backend"), "postgresql"),
            (("database",), str(self.state / "other.sqlite3")),
            (("authentication", "session_broker", "database"), str(self.root / "sessions.sqlite3")),
            (("authentication", "session_broker", "trusted_parent_path"), "/"),
            (("authentication", "session_broker", "workspace_enabled"), False),
            (("authentication", "session_broker", "console_enabled"), False),
            (("authentication", "session_broker", "public_base_url"), "http://gateway.example.test"),
            (("ai_work", "enabled"), False),
            (("ai_work", "database"), str(self.root / "hormuz-work.sqlite3")),
            (("max_request_bytes",), 2 * 1024 * 1024 + 1),
            (("egress_controls", "secrets", "mode"), "off"),
            (("egress_controls", "secrets", "builtins"), False),
        ]
        for fields, value in mutations:
            with self.subTest(fields=fields):
                document = copy.deepcopy(self.document)
                target = document
                for field in fields[:-1]:
                    target = target[field]
                target[fields[-1]] = value
                self.assert_rejected_before_environment(document)
        document = copy.deepcopy(self.document)
        document["key_custody"] = {"backend": "aws-kms", "region": "us-east-1", "key_references": {"provider_credential": "alias/synthetic"}}
        self.assert_rejected_before_environment(document)

    def test_configuration_file_symlinks_and_writeable_files_are_rejected_before_reads(self):
        self.write()
        link = self.root / "linked.json"
        link.symlink_to(self.path)
        for path in (link, self.path):
            with self.subTest(path=path.name):
                if path == self.path:
                    path.chmod(0o660)
                environment = _Environment(self.environment, forbid_reads=True)
                with self.assertRaises(HostedError):
                    load_profile(path, environment)
                self.assertEqual([], environment.reads)

    def test_invalid_or_control_environment_names_are_rejected_before_reads(self):
        for name in ("bad-name", "PATH", "path", "PYTHONPATH", "DYLD_INSERT_LIBRARIES", "AWS_ACCESS_KEY_ID", "PORT", "HORMUZ_HOSTED_MODE", "RENDER_SERVICE_ID", "A" * 129):
            with self.subTest(name=name):
                document = copy.deepcopy(self.document)
                document["egress_controls"]["secrets"]["custom_secret_envs"] = [name]
                self.assert_rejected_before_environment(document)
        document = copy.deepcopy(self.document)
        document["egress_controls"]["secrets"]["custom_secret_envs"] = ["SYNTHETIC_DETECTOR_" + str(index) for index in range(129)]
        self.assert_rejected_before_environment(document)

    def test_secret_size_and_total_child_environment_are_bounded(self):
        for value in (None, "", "synthetic\x00value", "A" * 4097):
            with self.subTest(value_type=type(value).__name__, size=len(value) if isinstance(value, str) else None):
                environment = {**self.environment, "HORMUZ_OPENAI_PROVIDER_KEY": value}
                with self.assertRaisesRegex(HostedError, "credential_missing_or_invalid"):
                    self.load(environment=environment)
        names = ["SYNTHETIC_DETECTOR_" + str(index) for index in range(17)]
        self.document["egress_controls"]["secrets"]["custom_secret_envs"] = names
        environment = {**self.environment, **{name: "x" * 4096 for name in names}}
        with self.assertRaisesRegex(HostedError, "environment_too_large"):
            self.load(environment=environment)

    def test_ingress_and_paid_credentials_fail_closed_without_disclosing_values(self):
        cases = (
            ("HORMUZ_OPENAI_PROVIDER_KEY", self.environment["HORMUZ_INGRESS_CREDENTIAL"], "ingress_credential_not_dedicated"),
            ("HORMUZ_WORK_BILLING_API_KEY", "sk_" + "test_" + "S" * 32, "billing_credential_invalid"),
            ("HORMUZ_WORK_BILLING_WEBHOOK_SECRET", "synthetic-invalid-webhook-value", "billing_credential_invalid"),
            ("HORMUZ_OPENAI_PROVIDER_KEY", "synthetic provider key with spaces", "provider_credential_invalid"),
            ("HORMUZ_INGRESS_CREDENTIAL", "é" * 43, "ingress_credential_invalid"),
            ("HORMUZ_INGRESS_CREDENTIAL", "a" * 42, "ingress_credential_invalid"),
            ("HORMUZ_INGRESS_CREDENTIAL", "a" * 129, "ingress_credential_invalid"),
            ("HORMUZ_INGRESS_CREDENTIAL", "a" * 43 + "\r\n", "ingress_credential_invalid"),
        )
        for name, value, code in cases:
            with self.subTest(name=name, code=code):
                with self.assertRaisesRegex(HostedError, code) as caught:
                    self.load(environment={**self.environment, name: value})
                self.assertNotIn(value, str(caught.exception))

    def test_credentials_resolve_the_single_validated_snapshot_after_owner_edit(self):
        self.write()
        original = self.path.read_text()
        document = copy.deepcopy(self.document)
        document["listen"] = {"host": "0.0.0.0", "port": 23456}
        document["database"] = str(self.root / "unreviewed.sqlite3")
        path = self.path

        class EditingEnvironment(_Environment):
            def get(inner, name, default=None):
                if not inner.reads:
                    path.write_text(json.dumps(document))
                return super().get(name, default)

        environment = EditingEnvironment(self.environment)
        config, _ = load_profile(self.path, environment)
        self.assertNotEqual(original, self.path.read_text())
        self.assertEqual((config.listen.host, config.listen.port), ("127.0.0.1", 8787))
        self.assertEqual(config.database_path, self.state / "usage.sqlite3")
        self.assertEqual(self.environment["HORMUZ_INGRESS_CREDENTIAL"], config.ingress.credential)

    def test_all_new_cli_entrypoints_reject_invalid_profile_before_secret_reads(self):
        self.path.write_text('{"unrecognized":true}')
        self.path.chmod(0o600)
        # argparse/gettext also reads these credential-free locale settings.
        allowed = {"HORMUZ_CONFIG", "HORMUZ_HOSTED_MODE", "PORT", "HORMUZ_PROVIDER_CONFIG", "HORMUZ_PROVIDER_PROFILE", "LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG", *PROVIDER_DEPLOYMENT_METADATA_NAMES}

        class BootstrapEnvironment(_Environment):
            def get(inner, name, default=None):
                if name not in allowed:
                    raise AssertionError("credential read before structural validation: " + name)
                return super().get(name, default)

        paths = (
            ("maintenance", ["--ai-work-profile", "initialize"]),
            ("maintenance", ["--ai-work-profile", "check"]),
            ("maintenance", ["ai-work-check"]),
            ("maintenance", ["ai-work-backend"]),
            ("ai-work", ["check"]),
            ("ai-work", ["serve"]),
        )
        for mode, arguments in paths:
            with self.subTest(mode=mode, arguments=arguments):
                environment = BootstrapEnvironment({**self.environment, "HORMUZ_HOSTED_MODE": mode})
                with patch("hormuz.hosted.os.environ", environment), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()), patch("hormuz.hosted._spawn") as spawn:
                    self.assertEqual(main(["--config", str(self.path), *arguments]), 1)
                self.assertTrue(set(environment.reads).issubset(allowed))
                spawn.assert_not_called()

    def test_explicit_legacy_selectors_keep_fixed_credentials_under_ai_work_mode(self):
        for selector, loader in (("--workspace-profile", "hormuz.hosted.load_workspace_profile"),
                                 ("--provider-profile", "hormuz.hosted.load_provider_profile"),
                                 ("--gateway-profile", "hormuz.work_recovery.load_profile")):
            with self.subTest(selector=selector), patch("hormuz.hosted.os.environ", _Environment(self.environment)) as environment, patch(loader) as load, patch("hormuz.hosted.initialize") as initialize, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(main(["--config", str(self.path), selector, "initialize"]), 0)
                load.assert_called_once()
                initialize.assert_called_once_with(load.return_value)
                self.assertIn("HORMUZ_POSTGRES_DSN", environment.reads)
                if selector != "--gateway-profile":
                    self.assertEqual(load.call_args.args[-1]["HORMUZ_OPENAI_PROVIDER_KEY"], self.environment["HORMUZ_OPENAI_PROVIDER_KEY"])

    def test_optional_domain_secret_is_selected_only_when_domain_is_configured(self):
        _, selected = self.load()
        self.assertNotIn("HORMUZ_DOMAIN_API_KEY", selected)
        broker = self.document["authentication"]["session_broker"]
        broker.update({
            "public_base_url": "https://synthetic-workspace.onrender.com",
            "workspace_domain_target": "synthetic-workspace.onrender.com",
            "workspace_domain_service_id": "srv-" + "a" * 16,
            "workspace_domain_api_key_env": "SYNTHETIC_DOMAIN_API_KEY",
        })
        self.environment["SYNTHETIC_DOMAIN_API_KEY"] = "synthetic-configured-domain-api-key"
        config, selected = self.load()
        self.assertEqual(self.environment["SYNTHETIC_DOMAIN_API_KEY"], config.session_broker.workspace_domain_api_key)
        self.assertIn("SYNTHETIC_DOMAIN_API_KEY", selected)
        self.assertNotIn("HORMUZ_DOMAIN_API_KEY", selected)

    def connectors(self):
        self.document["identities"] = [{
            "token_env": "SYNTHETIC_ADMIN_TOKEN", "organization_id": "customer-a",
            "actor_id": "fixture-admin", "actor_name": "Fixture Admin",
            "team_id": "customer-a-eng", "team_name": "Engineering",
            "allowed_clients": ["codex", "claude-code"],
        }]
        self.environment["SYNTHETIC_ADMIN_TOKEN"] = "synthetic-static-admin-token-for-config"
        self.document["portfolio_control"] = {
            "schema_id": "hormuz.portfolio-control", "schema_version": 1,
            "role_bindings": [{"organization_id": "customer-a", "actor_id": "fixture-admin", "roles": ["portfolio_admin"]}],
            "connectors": [
                {"organization_id": "customer-a", "connector_id": "github-one", "provider": "github", "installation_id": "123", "workspace_id": None, "external_object_ids": ["456"]},
                {"organization_id": "customer-a", "connector_id": "linear-one", "provider": "linear", "installation_id": None, "workspace_id": "10000000-0000-4000-8000-000000000001", "external_object_ids": [PROJECT]},
            ],
        }
        names = set()

        def reference(version, name, *, previous=False):
            names.add(name)
            self.environment[name] = "synthetic-" + name.lower() + "-1234567890123456"
            result = {"version": version, "environment_variable": name}
            if previous:
                result["expires_at"] = "2020-01-01T00:00:00Z"
            return result

        self.document["outcome_connectors"] = {
            "schema_id": "hormuz.outcome-connectors", "schema_version": 3,
            "github": [{
                "organization_id": "customer-a", "connector_id": "github-one",
                "webhook_secrets": [reference("webhook-v1", "SYNTHETIC_GITHUB_WEBHOOK_OLD"), reference("webhook-v2", "SYNTHETIC_GITHUB_WEBHOOK_CURRENT")],
                "identity_keys": [reference("key-v1", "SYNTHETIC_GITHUB_IDENTITY_OLD"), reference("key-v2", "SYNTHETIC_GITHUB_IDENTITY_CURRENT")],
                "current_key_version": "key-v2", "delivery_identity_key_version": "key-v1",
            }],
            "linear": [{
                "organization_id": "customer-a", "connector_id": "linear-one", "binding_version": 1,
                "source_webhook_id": "30000000-0000-4000-8000-000000000001",
                "source_team_ids": ["40000000-0000-4000-8000-000000000001"],
                "typed_enrollment": {"initiative_ids": [], "project_ids": [PROJECT], "cycle_ids": [], "issue_ids": []},
                "active_webhook_secret": reference("webhook-v2", "SYNTHETIC_LINEAR_WEBHOOK_CURRENT"),
                "previous_webhook_secret": reference("webhook-v1", "SYNTHETIC_LINEAR_WEBHOOK_OLD", previous=True),
                "active_snapshot_secret": reference("snapshot-v2", "SYNTHETIC_LINEAR_SNAPSHOT_CURRENT"),
                "previous_snapshot_secret": reference("snapshot-v1", "SYNTHETIC_LINEAR_SNAPSHOT_OLD", previous=True),
                "identity_keys": [reference("1", "SYNTHETIC_LINEAR_IDENTITY_OLD"), reference("2", "SYNTHETIC_LINEAR_IDENTITY_CURRENT")],
                "current_key_version": "2", "body_fingerprint_key_version": "1", "source_fact_key_version": "2", "registered_by": "fixture-admin",
            }],
        }
        return names

    def test_connector_rotation_and_static_identity_refs_are_all_selected(self):
        names = self.connectors()
        config, selected = self.load()
        expected = BASE_SECRET_NAMES | names | {"SYNTHETIC_ADMIN_TOKEN", "HORMUZ_HOSTED_MODE"} | set(PROVIDER_DEPLOYMENT_METADATA_NAMES)
        self.assertEqual(expected, set(selected))
        github, linear = config.outcome_connectors.github[0], config.outcome_connectors.linear[0]
        references = (*github.webhook_secrets, *github.identity_keys, *linear.identity_keys,
                      linear.active_webhook_secret, linear.previous_webhook_secret,
                      linear.active_snapshot_secret, linear.previous_snapshot_secret)
        for reference in references:
            self.assertEqual(self.environment[reference.environment_variable].encode("ascii"), reference.value)
        self.assertIsNotNone(config.portfolio_control)
        self.assertIn(self.environment["SYNTHETIC_ADMIN_TOKEN"], config.identities_by_token)
        self.assertNotIn("GITHUB_TOKEN", selected)

    def test_previous_connector_refs_are_required_even_after_rotation_expiry(self):
        names = self.connectors()
        for name in sorted(names):
            with self.subTest(name=name):
                environment = dict(self.environment)
                environment.pop(name)
                with self.assertRaisesRegex(HostedError, "credential_missing_or_invalid"):
                    self.load(environment=environment)


if __name__ == "__main__":
    unittest.main()
