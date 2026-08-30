from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hormuz.config import ConfigError, GatewayConfig
from tests._session_fixtures import CLIENT_SECRET, fixture_environment, session_config


class SessionConfigTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "hormuz.json"
        self.value = session_config(self.root, "http://127.0.0.1:9000", "http://127.0.0.1:8787")

    def load(self, value=None, environ=None):
        self.path.write_text(json.dumps(self.value if value is None else value))
        return GatewayConfig.load(self.path, environ=fixture_environment() if environ is None else environ)

    def test_approved_defaults_hide_resolved_secrets(self):
        config = self.load()
        self.assertEqual(config.session_broker.access_ttl_seconds, 600)
        self.assertEqual(config.session_broker.absolute_ttl_seconds, 43200)
        self.assertNotIn(CLIENT_SECRET, repr(config))
        self.assertNotIn("mmmmmmmm", repr(config))

    def test_invalid_schema_and_ttl_fail_before_secret_resolution(self):
        for field, value in (("access_ttl_seconds", 60), ("absolute_ttl_seconds", 86400), ("enrollment_ttl_seconds", 999999), ("enabled", "true"), ("master_key", "never-reflect-this")):
            raw = copy.deepcopy(self.value)
            raw["authentication"]["session_broker"][field] = value
            with self.subTest(field=field), self.assertRaises(ConfigError) as raised:
                self.load(raw, environ={})
            self.assertNotIn("never-reflect-this", str(raised.exception))
            self.assertNotIn("base64", str(raised.exception))

    def test_http_and_credential_in_url_are_rejected(self):
        for url in ("http://remote.invalid", "https://user:password@gateway.invalid", "https://gateway.invalid/path", "https://gateway.invalid?token=secret", "https://gateway.invalid#fragment"):
            raw = copy.deepcopy(self.value)
            raw["authentication"]["session_broker"]["public_base_url"] = url
            with self.subTest(url=url), self.assertRaises(ConfigError):
                self.load(raw)

    def test_login_audience_is_distinct_and_secrets_are_required(self):
        raw = copy.deepcopy(self.value)
        raw["authentication"]["oidc"]["issuers"][0]["login"]["client_id"] = "hormuz-api"
        with self.assertRaisesRegex(ConfigError, "resource audiences"):
            self.load(raw)
        for removed in ("HORMUZ_SESSION_MASTER_KEY", "TEST_OIDC_SECRET"):
            env = fixture_environment()
            del env[removed]
            with self.subTest(removed=removed), self.assertRaises(ConfigError):
                self.load(environ=env)

    def test_usage_and_sessions_cannot_share_database(self):
        self.value["authentication"]["session_broker"]["database"] = self.value["database"]
        with self.assertRaisesRegex(ConfigError, "separate from usage"):
            self.load()

    def test_existing_resource_server_configuration_stays_session_free(self):
        del self.value["authentication"]["session_broker"]
        del self.value["authentication"]["oidc"]["issuers"][0]["login"]
        config = self.load(environ={})
        self.assertFalse(config.session_broker.enabled)
        self.assertFalse((self.root / "sessions.sqlite3").exists())

    def test_offline_policy_validation_does_not_resolve_login_secrets(self):
        self.path.write_text(json.dumps(self.value))
        with mock.patch.dict("os.environ", {}, clear=True):
            context = GatewayConfig.load_policy_validation_context(self.path)
        self.assertEqual(context.organization_ids, ("org-a", "org-b"))

    def test_oidc_credentials_cannot_reuse_a_provider_secret_variable(self):
        self.value["authentication"]["oidc"]["issuers"][0]["login"]["client_secret_env"] = "TEST_PROVIDER_KEY"
        with self.assertRaisesRegex(ConfigError, "dedicated"):
            self.load()
