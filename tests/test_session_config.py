from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
import tempfile
import unittest
import urllib.parse
from unittest import mock
from pathlib import Path

from hormuz.config import ConfigError, GatewayConfig
from hormuz.auth import Authenticator
from hormuz.session import (
    SessionBroker,
    SessionBrokerError,
    _build_authorization_url,
    _exchange_code,
)
from hormuz.session_store import Enrollment


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

    def test_entra_reference_uses_the_generic_tenant_scoped_contract(self) -> None:
        issuer = self.raw["authentication"]["oidc"]["issuers"][0]
        issuer["issuer"] = (
            "https://login.microsoftonline.com/"
            "11111111-2222-3333-4444-555555555555/v2.0"
        )
        issuer["audiences"] = ["api://hormuz-workload"]
        issuer["algorithms"] = ["RS256"]
        issuer["login"]["token_endpoint_auth_method"] = "client_secret_post"
        issuer["subjects"][0]["subject"] = "entra-pairwise-test-subject"

        config = self._load()
        reference = config.oidc_issuers[
            "https://login.microsoftonline.com/11111111-2222-3333-4444-555555555555/v2.0"
        ]
        self.assertEqual(reference.algorithms, ("RS256",))
        self.assertEqual(reference.audiences, ("api://hormuz-workload",))
        self.assertEqual(
            reference.login.token_endpoint_auth_method,  # type: ignore[union-attr]
            "client_secret_post",
        )
        self.assertIn(
            (reference.issuer, "entra-pairwise-test-subject"),
            config.identities_by_subject,
        )

    def test_client_secret_post_exchange_uses_no_basic_authorization_header(self) -> None:
        captured: dict[str, object] = {}

        class Response:
            def geturl(self) -> str:
                return "https://identity.example/token"

            def read(self, _maximum: int) -> bytes:
                return b'{"id_token":"provider-id-token-not-retained"}'

            def __enter__(self):
                return self

            def __exit__(self, *_args: object) -> bool:
                return False

        class Opener:
            def open(self, request, *, timeout: int):
                captured["request"] = request
                captured["timeout"] = timeout
                return Response()

        with mock.patch(
            "hormuz.session.urllib.request.build_opener",
            return_value=Opener(),
        ):
            response = _exchange_code(
                "https://identity.example/token",
                allow_insecure_http=False,
                client_id="hormuz-login-client",
                client_secret="post-secret-must-not-leak",
                auth_method="client_secret_post",
                redirect_uri="https://hormuz.example/v1/auth/callback",
                code="authorization-code",
                pkce_verifier="pkce-verifier",
            )

        request = captured["request"]
        fields = urllib.parse.parse_qs(request.data.decode("ascii"), strict_parsing=True)
        self.assertEqual(fields["client_id"], ["hormuz-login-client"])
        self.assertEqual(fields["client_secret"], ["post-secret-must-not-leak"])
        self.assertEqual(fields["code_verifier"], ["pkce-verifier"])
        self.assertIsNone(request.get_header("Authorization"))
        self.assertEqual(response, {"id_token": "provider-id-token-not-retained"})
        self.assertNotIn("post-secret-must-not-leak", repr(response))

    def test_postgresql_usage_defaults_sessions_to_shared_postgresql(self) -> None:
        self.raw["usage_storage"] = {
            "backend": "postgresql",
            "postgres_dsn_env": "HORMUZ_POSTGRES_DSN",
        }
        self.raw["authentication"]["session_broker"].pop("database")
        config = self._load()
        self.assertEqual(config.session_broker.backend, "postgresql")
        self.assertIsNone(config.session_broker.database_path)

        self.raw["authentication"]["session_broker"]["database"] = "./sessions.sqlite3"
        with self.assertRaisesRegex(ConfigError, "only valid for the sqlite backend"):
            self._load()

        self.raw["authentication"]["session_broker"]["backend"] = "sqlite"
        config = self._load()
        self.assertEqual(config.session_broker.backend, "sqlite")
        self.assertEqual(
            config.session_broker.database_path,
            (self.root / "sessions.sqlite3").resolve(),
        )

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

    def test_oidc_issuer_and_subject_unknown_fields_fail_closed(self) -> None:
        sentinel = "company_secret_do_not_log_40d41b98"
        for target, expected_path in (
            ("issuer", "authentication.oidc.issuers[0]"),
            ("subject", "authentication.oidc.issuers[0].subjects[0]"),
        ):
            with self.subTest(target=target):
                raw = json.loads(json.dumps(self.raw))
                issuer = raw["authentication"]["oidc"]["issuers"][0]
                container = issuer if target == "issuer" else issuer["subjects"][0]
                container[sentinel] = True
                self.path.write_text(json.dumps(raw), encoding="utf-8")
                with self.assertRaises(ConfigError) as captured:
                    GatewayConfig.load(self.path, environ=self.environ)
                self.assertIn(
                    f"Unknown {expected_path} fields",
                    str(captured.exception),
                )
                self.assertNotIn(sentinel, str(captured.exception))

    def test_multi_organization_issuer_requires_explicit_tenant_binding(self) -> None:
        issuer = self.raw["authentication"]["oidc"]["issuers"][0]
        second = dict(issuer["subjects"][0])
        second.update(
            {
                "subject": "stable-bob",
                "actor_id": "bob",
                "actor_name": "Bob Example",
                "team_id": "engineering-other",
                "team_name": "Engineering Other",
                "organization_id": "another-tenant",
            }
        )
        issuer["subjects"].append(second)
        config = self._load()
        store = mock.Mock()
        store.create_enrollment.return_value = Enrollment(
            enrollment_id="e" * 24,
            issuer="https://identity.example",
            client_name="codex",
            expires_at=datetime.now(timezone.utc),
            organization_id="xpounder",
        )
        broker = SessionBroker(config, Authenticator(config), store)
        with self.assertRaisesRegex(SessionBrokerError, "organization_required"):
            broker.create_enrollment(
                issuer_name="https://identity.example",
                client_name="codex",
                enrollment_secret="s" * 32,
            )
        broker.create_enrollment(
            issuer_name="https://identity.example",
            organization_id="xpounder",
            client_name="codex",
            enrollment_secret="s" * 32,
        )
        self.assertEqual(store.create_enrollment.call_args.kwargs["organization_id"], "xpounder")

    def test_directory_requires_an_oidc_issuer_and_a_separate_database(self) -> None:
        self.raw["authentication"]["directory"] = {
            "enabled": True,
            "database": "./directory.sqlite3",
        }
        config = self._load()
        self.assertTrue(config.directory.enabled)
        self.assertEqual(
            config.directory.database_path,
            (self.root / "directory.sqlite3").resolve(),
        )
        self.raw["authentication"]["oidc"]["issuers"][0]["subjects"] = []
        config = self._load()
        self.assertEqual(config.identities_by_subject, {})

        self.raw["authentication"]["directory"]["database"] = "./usage.sqlite3"
        with self.assertRaisesRegex(ConfigError, "must be separate"):
            self._load()

        self.raw["authentication"]["directory"]["database"] = "./directory.sqlite3"
        self.raw["authentication"].pop("session_broker")
        self.raw["authentication"]["oidc"]["issuers"] = []
        with self.assertRaisesRegex(ConfigError, "requires at least one configured OIDC issuer"):
            self._load()

    def test_postgresql_directory_uses_a_distinct_keyed_routing_secret(self) -> None:
        self.raw["usage_storage"] = {
            "backend": "postgresql",
            "postgres_dsn_env": "HORMUZ_POSTGRES_DSN",
        }
        self.raw["authentication"].pop("session_broker")
        self.raw["authentication"]["directory"] = {
            "enabled": True,
            "backend": "postgresql",
            "routing_key_env": "DIRECTORY_ROUTING_KEY",
        }
        self.raw["authentication"]["oidc"]["issuers"][0]["subjects"] = []
        self.environ["DIRECTORY_ROUTING_KEY"] = base64.urlsafe_b64encode(
            b"directory-routing-key-test-value"[:32].ljust(32, b"d")
        ).decode("ascii")
        config = self._load()
        self.assertTrue(config.directory.enabled)
        self.assertEqual(config.directory.backend, "postgresql")
        self.assertIsNone(config.directory.database_path)
        self.assertEqual(config.directory.routing_key_env, "DIRECTORY_ROUTING_KEY")
        self.assertEqual(len(config.directory.routing_key), 32)
        self.assertNotIn(self.environ["DIRECTORY_ROUTING_KEY"], repr(config))

        self.environ.pop("DIRECTORY_ROUTING_KEY")
        with self.assertRaisesRegex(ConfigError, "Required directory routing key"):
            self._load()

        self.raw["usage_storage"] = {"backend": "sqlite"}
        self.environ["DIRECTORY_ROUTING_KEY"] = base64.urlsafe_b64encode(b"d" * 32).decode("ascii")
        with self.assertRaisesRegex(ConfigError, "requires usage_storage.backend postgresql"):
            self._load()

    def test_configured_oidc_identity_type_is_explicit_and_checked(self) -> None:
        subject = self.raw["authentication"]["oidc"]["issuers"][0]["subjects"][0]
        subject["identity_type"] = "ci"
        self.raw["identities"][0]["identity_type"] = "ci"
        config = self._load()
        identity = config.identities_by_subject[("https://identity.example", "stable-alice")]
        self.assertEqual(identity.identity_type, "ci")

        subject["identity_type"] = "unknown"
        with self.assertRaisesRegex(ConfigError, "identity_type must be human"):
            self._load()

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
