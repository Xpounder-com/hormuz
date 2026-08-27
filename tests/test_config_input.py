from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from hormuz.cli import main
from hormuz.config import (
    MAX_CONFIGURATION_BYTES,
    MAX_CONFIGURATION_DEPTH,
    MAX_CONFIGURATION_NODES,
    ConfigError,
    GatewayConfig,
)


ROOT = Path(__file__).resolve().parents[1]
TEST_ENVIRONMENT = {"HORMUZ_TOKEN": "test-identity-token-with-sufficient-length"}


class _EnvironmentMustNotBeRead(dict[str, str]):
    def get(self, key: str, default: str | None = None) -> str | None:
        raise AssertionError(f"configuration schema validation resolved environment variable {key}")


class ConfigurationInputTests(unittest.TestCase):
    def _valid_configuration(self) -> dict[str, object]:
        return json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))

    def _assert_load_error(
        self,
        payload: bytes,
        expected: str,
        *,
        environ: dict[str, str] | None = None,
    ) -> ConfigError:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "hormuz.json"
            path.write_bytes(payload)
            with self.assertRaises(ConfigError) as raised:
                GatewayConfig.load(path, environ=environ)
        self.assertEqual(str(raised.exception), expected)
        return raised.exception

    def test_valid_example_configuration_remains_accepted(self) -> None:
        config = GatewayConfig.load(ROOT / "config.example.json", environ=TEST_ENVIRONMENT)

        self.assertEqual(config.listen.host, "127.0.0.1")
        self.assertIn("gpt-5.4-mini", config.model_routes)

    def test_policy_validation_context_never_resolves_credentials(self) -> None:
        with mock.patch("hormuz._config_builder.os.environ", _EnvironmentMustNotBeRead()):
            context = GatewayConfig.load_policy_validation_context(ROOT / "config.example.json")

        self.assertEqual(context.organization_ids, ("xpounder",))
        self.assertEqual(tuple(context.identities_by_actor), ("alice",))
        self.assertIn("gpt-5.4-mini", context.model_routes)

    def test_policy_analysis_context_never_resolves_credentials(self) -> None:
        with mock.patch("hormuz._config_builder.os.environ", _EnvironmentMustNotBeRead()):
            context = GatewayConfig.load_policy_analysis_context(ROOT / "config.example.json")

        self.assertEqual(context.organization_ids, ("xpounder",))
        self.assertEqual(tuple(context.identities_by_actor), ("alice",))
        self.assertIn("gpt-5.4-mini", context.model_routes)
        self.assertEqual(context.usage_storage.backend, "sqlite")
        self.assertEqual(context.database_path, (ROOT / "hormuz.sqlite3").resolve())

    def test_unavailable_configuration_path_is_content_free(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "operator-secret-never-expose.json"
            with self.assertRaises(ConfigError) as raised:
                GatewayConfig.load(path, environ=_EnvironmentMustNotBeRead())

        self.assertEqual(str(raised.exception), "configuration_unavailable")
        self.assertNotIn("operator-secret-never-expose", str(raised.exception))

    def test_raw_json_failures_are_bounded_and_content_free(self) -> None:
        oversized = b" " * (MAX_CONFIGURATION_BYTES + 1)
        deeply_nested = (
            b'{"listen":' + b"[" * (MAX_CONFIGURATION_DEPTH + 1) + b"0" + b"]" * (MAX_CONFIGURATION_DEPTH + 1) + b"}"
        )
        too_many_nodes = json.dumps({"identities": [0] * (MAX_CONFIGURATION_NODES + 1)}).encode("utf-8")

        cases = {
            "unavailable JSON": (b'{"operator_secret":"never-expose"', "configuration_invalid_json"),
            "invalid UTF-8": (b"\xff", "configuration_invalid_encoding"),
            "duplicate member": (
                b'{"operator_secret":"never-expose","operator_secret":"never-expose"}',
                "configuration_duplicate_member",
            ),
            "nonfinite number": (b'{"max_request_bytes":NaN}', "configuration_nonfinite_number"),
            "overflowed number": (b'{"max_request_bytes":1e1000}', "configuration_nonfinite_number"),
            "oversized": (oversized, "configuration_too_large"),
            "excessive depth": (deeply_nested, "configuration_structure_limit"),
            "excessive nodes": (too_many_nodes, "configuration_structure_limit"),
        }
        for name, (payload, expected) in cases.items():
            with self.subTest(name=name):
                error = self._assert_load_error(payload, expected, environ=_EnvironmentMustNotBeRead())
                self.assertNotIn("never-expose", str(error))
                self.assertNotIn("operator_secret", str(error))

    def test_unknown_fields_at_each_schema_boundary_fail_before_environment_resolution(self) -> None:
        def issuer_with_subject() -> dict[str, object]:
            return {
                "issuer": "https://issuer.example.test",
                "audiences": ["hormuz"],
                "subjects": [
                    {
                        "subject": "employee-123",
                        "actor_id": "oidc-alice",
                        "actor_name": "OIDC Alice",
                        "team_id": "engineering",
                        "team_name": "Engineering",
                    }
                ],
            }

        def mutate_root(value: dict[str, object]) -> None:
            value["operator_secret_never_expose"] = "never-expose"

        def mutate_listen(value: dict[str, object]) -> None:
            value["listen"]["operator_secret_never_expose"] = "never-expose"  # type: ignore[index]

        def mutate_ingress(value: dict[str, object]) -> None:
            value["ingress"] = {"operator_secret_never_expose": "never-expose"}

        def mutate_upstreams(value: dict[str, object]) -> None:
            value["upstreams"]["unsupported"] = {}  # type: ignore[index]

        def mutate_upstream(value: dict[str, object]) -> None:
            value["upstreams"]["openai"]["operator_secret_never_expose"] = "never-expose"  # type: ignore[index]

        def mutate_identity(value: dict[str, object]) -> None:
            value["identities"][0]["operator_secret_never_expose"] = "never-expose"  # type: ignore[index]

        def mutate_authentication(value: dict[str, object]) -> None:
            value["authentication"]["operator_secret_never_expose"] = "never-expose"  # type: ignore[index]

        def mutate_oidc(value: dict[str, object]) -> None:
            value["authentication"]["oidc"]["operator_secret_never_expose"] = "never-expose"  # type: ignore[index]

        def mutate_issuer(value: dict[str, object]) -> None:
            issuer = issuer_with_subject()
            issuer["operator_secret_never_expose"] = "never-expose"
            value["authentication"]["oidc"]["issuers"] = [issuer]  # type: ignore[index]

        def mutate_subject(value: dict[str, object]) -> None:
            issuer = issuer_with_subject()
            issuer["subjects"][0]["operator_secret_never_expose"] = "never-expose"  # type: ignore[index]
            value["authentication"]["oidc"]["issuers"] = [issuer]  # type: ignore[index]

        def mutate_route(value: dict[str, object]) -> None:
            value["model_routes"]["gpt-5.4-mini"]["operator_secret_never_expose"] = (  # type: ignore[index]
                "never-expose"
            )

        def mutate_egress(value: dict[str, object]) -> None:
            value["egress_controls"]["operator_secret_never_expose"] = "never-expose"  # type: ignore[index]

        def mutate_secret_controls(value: dict[str, object]) -> None:
            value["egress_controls"]["secrets"]["operator_secret_never_expose"] = "never-expose"  # type: ignore[index]

        def mutate_policies(value: dict[str, object]) -> None:
            value["policies"]["operator_secret_never_expose"] = "never-expose"  # type: ignore[index]

        def mutate_policy(value: dict[str, object]) -> None:
            value["policies"]["organization"]["operator_secret_never_expose"] = "never-expose"  # type: ignore[index]

        def mutate_team_policy(value: dict[str, object]) -> None:
            value["policies"]["teams"]["engineering"]["operator_secret_never_expose"] = (  # type: ignore[index]
                "never-expose"
            )

        def mutate_actor_policy(value: dict[str, object]) -> None:
            value["policies"]["actors"]["alice"] = {  # type: ignore[index]
                "operator_secret_never_expose": "never-expose"
            }

        def mutate_fallback_models(value: dict[str, object]) -> None:
            value["policies"]["organization"]["fallback_models"] = {  # type: ignore[index]
                "unsupported": "gpt-5.4-mini"
            }

        def mutate_usage_storage(value: dict[str, object]) -> None:
            value["usage_storage"] = {"operator_secret_never_expose": "never-expose"}

        def mutate_postgres_pool(value: dict[str, object]) -> None:
            value["usage_storage"] = {"postgres_pool": {"operator_secret_never_expose": "never-expose"}}

        def mutate_policy_control(value: dict[str, object]) -> None:
            value["policy_control"] = {"operator_secret_never_expose": "never-expose"}

        def mutate_break_glass(value: dict[str, object]) -> None:
            value["policy_control"] = {"break_glass": {"operator_secret_never_expose": "never-expose"}}

        def mutate_bootstrap_administrator(value: dict[str, object]) -> None:
            value["policy_control"] = {
                "bootstrap_administrators": [
                    {"organization_id": "xpounder", "operator_secret_never_expose": "never-expose"}
                ]
            }

        def mutate_key_custody(value: dict[str, object]) -> None:
            value["key_custody"] = {"operator_secret_never_expose": "never-expose"}

        def mutate_key_references(value: dict[str, object]) -> None:
            value["key_custody"] = {"key_references": {"operator_secret_never_expose": "never-expose"}}

        def mutate_audit_anchor(value: dict[str, object]) -> None:
            value["audit_anchor"] = {"operator_secret_never_expose": "never-expose"}

        def mutate_audit_chain(value: dict[str, object]) -> None:
            value["audit_chain"] = {"operator_secret_never_expose": "never-expose"}

        def mutate_custody_lifecycle(value: dict[str, object]) -> None:
            value["custody_lifecycle"] = {"operator_secret_never_expose": "never-expose"}

        mutations = (
            mutate_root,
            mutate_listen,
            mutate_ingress,
            mutate_upstreams,
            mutate_upstream,
            mutate_identity,
            mutate_authentication,
            mutate_oidc,
            mutate_issuer,
            mutate_subject,
            mutate_route,
            mutate_egress,
            mutate_secret_controls,
            mutate_policies,
            mutate_policy,
            mutate_team_policy,
            mutate_actor_policy,
            mutate_fallback_models,
            mutate_usage_storage,
            mutate_postgres_pool,
            mutate_policy_control,
            mutate_break_glass,
            mutate_bootstrap_administrator,
            mutate_key_custody,
            mutate_key_references,
            mutate_audit_anchor,
            mutate_audit_chain,
            mutate_custody_lifecycle,
        )
        for mutation in mutations:
            with self.subTest(boundary=mutation.__name__):
                raw = copy.deepcopy(self._valid_configuration())
                mutation(raw)
                error = self._assert_load_error(
                    json.dumps(raw).encode("utf-8"),
                    "configuration_unsupported_fields",
                    environ=_EnvironmentMustNotBeRead(),
                )
                self.assertNotIn("operator_secret_never_expose", str(error))
                self.assertNotIn("never-expose", str(error))

    def test_schema_shape_failure_is_content_free_and_precedes_environment_resolution(self) -> None:
        error = self._assert_load_error(
            b'{"upstreams":[]}',
            "configuration_schema_invalid",
            environ=_EnvironmentMustNotBeRead(),
        )
        self.assertNotIn("upstreams", str(error))

    def test_external_tls_proxy_ingress_is_strict_and_resolves_only_its_credential(self) -> None:
        raw = self._valid_configuration()
        raw["listen"] = {"host": "0.0.0.0", "port": 8787}
        raw["ingress"] = {
            "mode": "external_tls_proxy",
            "trusted_proxy_cidrs": ["10.42.0.0/16", "127.0.0.1/32", "fd00:42::/64"],
            "credential_env": "HORMUZ_TEST_INGRESS_CREDENTIAL",
        }
        environment = {
            **TEST_ENVIRONMENT,
            "HORMUZ_TEST_INGRESS_CREDENTIAL": "test-ingress-credential-with-sufficient-length",
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "hormuz.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            config = GatewayConfig.load(path, environ=environment)

        self.assertEqual(config.ingress.mode, "external_tls_proxy")
        self.assertEqual(config.ingress.credential_env, "HORMUZ_TEST_INGRESS_CREDENTIAL")
        self.assertEqual(config.ingress.trusted_proxy_cidrs, ("10.42.0.0/16", "127.0.0.1/32", "fd00:42::/64"))
        self.assertEqual(len(config.ingress.trusted_proxy_networks), 3)
        self.assertEqual(len(config.ingress.credential), len(environment["HORMUZ_TEST_INGRESS_CREDENTIAL"]))
        self.assertNotIn(environment["HORMUZ_TEST_INGRESS_CREDENTIAL"], repr(config))

    def test_external_tls_proxy_ingress_rejects_unsafe_or_incomplete_configuration(self) -> None:
        base = self._valid_configuration()

        cases: tuple[tuple[str, dict[str, object], dict[str, str], str], ...] = (
            (
                "public listener without proxy mode",
                {**base, "listen": {"host": "0.0.0.0", "port": 8787}},
                TEST_ENVIRONMENT,
                "a non-loopback listen.host requires ingress.mode external_tls_proxy",
            ),
            (
                "missing proxy credential environment value",
                {
                    **base,
                    "ingress": {
                        "mode": "external_tls_proxy",
                        "trusted_proxy_cidrs": ["127.0.0.1/32"],
                        "credential_env": "HORMUZ_TEST_INGRESS_CREDENTIAL",
                    },
                },
                TEST_ENVIRONMENT,
                "Required ingress credential environment variable is not set: HORMUZ_TEST_INGRESS_CREDENTIAL",
            ),
            (
                "uncanonical proxy network",
                {
                    **base,
                    "ingress": {
                        "mode": "external_tls_proxy",
                        "trusted_proxy_cidrs": ["10.42.0.1/16"],
                        "credential_env": "HORMUZ_TEST_INGRESS_CREDENTIAL",
                    },
                },
                {**TEST_ENVIRONMENT, "HORMUZ_TEST_INGRESS_CREDENTIAL": "test-ingress-credential-with-sufficient-length"},
                "ingress.trusted_proxy_cidrs[0] must be a canonical CIDR",
            ),
            (
                "all addresses network",
                {
                    **base,
                    "ingress": {
                        "mode": "external_tls_proxy",
                        "trusted_proxy_cidrs": ["0.0.0.0/0"],
                        "credential_env": "HORMUZ_TEST_INGRESS_CREDENTIAL",
                    },
                },
                {**TEST_ENVIRONMENT, "HORMUZ_TEST_INGRESS_CREDENTIAL": "test-ingress-credential-with-sufficient-length"},
                "ingress.trusted_proxy_cidrs must not admit every address",
            ),
            (
                "reused provider credential environment",
                {
                    **base,
                    "ingress": {
                        "mode": "external_tls_proxy",
                        "trusted_proxy_cidrs": ["127.0.0.1/32"],
                        "credential_env": "OPENAI_API_KEY",
                    },
                },
                TEST_ENVIRONMENT,
                "ingress.credential_env must name a credential distinct from all other Hormuz secrets",
            ),
        )
        for name, raw, environment, expected in cases:
            with self.subTest(name=name):
                self._assert_load_error(json.dumps(raw).encode("utf-8"), expected, environ=environment)

    def test_semantic_configuration_validation_precedes_environment_resolution(self) -> None:
        raw = self._valid_configuration()
        raw["model_routes"]["gpt-5.4-mini"]["protocol"] = "unsupported"  # type: ignore[index]

        error = self._assert_load_error(
            json.dumps(raw).encode("utf-8"),
            "model_routes.gpt-5.4-mini.protocol must be openai or anthropic",
            environ=_EnvironmentMustNotBeRead(),
        )
        self.assertNotIn("test-identity-token", str(error))

    def test_cross_reference_validation_precedes_environment_resolution(self) -> None:
        raw = self._valid_configuration()
        raw["policies"]["organization"]["allowed_models"] = ["unapproved-model"]  # type: ignore[index]

        error = self._assert_load_error(
            json.dumps(raw).encode("utf-8"),
            "Policy references unknown model alias: unapproved-model",
            environ=_EnvironmentMustNotBeRead(),
        )
        self.assertNotIn("test-identity-token", str(error))

    def test_audit_chain_requires_a_valid_external_anchor_and_has_a_bounded_age(self) -> None:
        raw = self._valid_configuration()
        raw["key_custody"] = {
            "backend": "aws-kms",
            "region": "us-east-1",
            "key_references": {
                "provider_credential": "alias/hormuz-provider",
                "data_encryption": "alias/hormuz-audit",
            },
        }
        raw["audit_anchor"] = {
            "backend": "aws-s3-object-lock",
            "region": "us-east-1",
            "bucket": "hormuz-audit-bucket",
            "prefix": "immutable/audit",
            "retention_days": 365,
            "legal_hold": False,
        }
        raw["audit_chain"] = {"maximum_anchor_age_seconds": 3600}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "hormuz.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            config = GatewayConfig.load(path, environ=TEST_ENVIRONMENT)
        self.assertEqual(config.audit_chain.maximum_anchor_age_seconds, 3600)  # type: ignore[union-attr]

        no_anchor = self._valid_configuration()
        no_anchor["audit_chain"] = {"maximum_anchor_age_seconds": 3600}
        self._assert_load_error(
            json.dumps(no_anchor).encode("utf-8"),
            "audit_chain requires audit_anchor",
            environ=TEST_ENVIRONMENT,
        )

        raw["audit_chain"] = {"maximum_anchor_age_seconds": 59}
        self._assert_load_error(
            json.dumps(raw).encode("utf-8"),
            "audit_chain.maximum_anchor_age_seconds must be at least 60 and at most 2678400",
            environ=TEST_ENVIRONMENT,
        )

    def test_cli_reports_only_fixed_raw_configuration_error_code(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "hormuz.json"
            path.write_bytes(b'{"operator_secret":"never-expose","operator_secret":"never-expose"}')
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                self.assertEqual(main(["--config", str(path), "doctor"]), 2)

        self.assertEqual(stderr.getvalue(), "configuration error: configuration_duplicate_member\n")
        self.assertNotIn("operator_secret", stderr.getvalue())
        self.assertNotIn("never-expose", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
