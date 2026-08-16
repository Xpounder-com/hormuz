from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path

from hormuz.config import ConfigError, GatewayConfig
from hormuz.session import SessionBrokerError, _build_authorization_url


ROOT = Path(__file__).resolve().parents[1]


class SessionConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.path = self.root / "hormuz.json"
        self.raw = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        self.raw["database"] = "./usage.sqlite3"
        self.raw["context_database"] = "./context.sqlite3"
        self.raw["authentication"] = {
            "session_broker": {
                "enabled": True,
                "database": "./sessions.sqlite3",
                "public_base_url": "https://hormuz.example",
                "master_key_env": "SESSION_MASTER_KEY",
            },
            "oidc": {
                "issuers": [
                    {
                        "issuer": "https://identity.example",
                        "login": {
                            "client_id": "hormuz-login",
                            "client_secret_env": "OIDC_CLIENT_SECRET",
                        },
                        "subjects": [
                            {
                                "subject": "stable-alice",
                                "actor_id": "alice",
                                "actor_name": "Alice Example",
                                "team_id": "engineering",
                                "team_name": "Engineering",
                                "organization_id": "xpounder",
                                "clearance": "confidential",
                                "allowed_clients": ["codex", "claude-code"],
                            }
                        ],
                    }
                ]
            },
        }
        self.environ = {
            "HORMUZ_TOKEN": "test-identity-token",
            "SESSION_MASTER_KEY": base64.urlsafe_b64encode(b"m" * 32).decode("ascii"),
            "OIDC_CLIENT_SECRET": "super-secret-oidc-client",
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _load(self) -> GatewayConfig:
        self.path.write_text(json.dumps(self.raw), encoding="utf-8")
        return GatewayConfig.load(self.path, environ=self.environ)

    def test_session_only_issuer_defaults_and_secrets_are_repr_hidden(self) -> None:
        config = self._load()
        issuer = config.oidc_issuers["https://identity.example"]
        self.assertEqual(issuer.audiences, ())
        self.assertEqual(issuer.login.scopes, ("openid",))  # type: ignore[union-attr]
        self.assertEqual(config.session_broker.access_ttl_seconds, 600)
        self.assertEqual(config.session_broker.absolute_ttl_seconds, 43_200)
        self.assertEqual(config.session_broker.enrollment_ttl_seconds, 300)
        representation = repr(config)
        self.assertNotIn(self.environ["OIDC_CLIENT_SECRET"], representation)
        self.assertNotIn(self.environ["SESSION_MASTER_KEY"], representation)

    def test_master_key_lifetimes_transport_and_database_are_fail_closed(self) -> None:
        cases = (
            ("missing_key", "Required session broker master key"),
            ("short_access", "access_ttl_seconds"),
            ("long_session", "absolute_ttl_seconds"),
            ("remote_http", "only loopback HTTP"),
            ("database_alias", "must be separate"),
            ("public_path", "must not include a path"),
            ("audience_overlap", "distinct audiences"),
        )
        for case, message in cases:
            with self.subTest(case=case):
                raw = json.loads(json.dumps(self.raw))
                environ = dict(self.environ)
                broker = raw["authentication"]["session_broker"]
                if case == "missing_key":
                    environ.pop("SESSION_MASTER_KEY")
                elif case == "short_access":
                    broker["access_ttl_seconds"] = 299
                elif case == "long_session":
                    broker["absolute_ttl_seconds"] = 43_201
                elif case == "remote_http":
                    broker["public_base_url"] = "http://hormuz.example"
                    broker["allow_insecure_http"] = True
                elif case == "database_alias":
                    broker["database"] = "./usage.sqlite3"
                elif case == "audience_overlap":
                    raw["authentication"]["oidc"]["issuers"][0]["audiences"] = [
                        "hormuz-login"
                    ]
                else:
                    broker["public_base_url"] = "https://hormuz.example/base"
                self.path.write_text(json.dumps(raw), encoding="utf-8")
                with self.assertRaisesRegex(ConfigError, message):
                    GatewayConfig.load(self.path, environ=environ)

    def test_authorization_endpoint_query_is_preserved_without_reserved_override(self) -> None:
        url = _build_authorization_url(
            "https://identity.example/authorize?tenant=company",
            {"client_id": "hormuz", "state": "state"},
        )
        self.assertIn("tenant=company", url)
        self.assertIn("client_id=hormuz", url)
        with self.assertRaisesRegex(SessionBrokerError, "invalid_authorization_endpoint"):
            _build_authorization_url(
                "https://identity.example/authorize?client_id=attacker",
                {"client_id": "hormuz", "state": "state"},
            )


if __name__ == "__main__":
    unittest.main()
