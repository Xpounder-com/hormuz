from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from hormuz.config import ConfigError, GatewayConfig
from hormuz.policy_document import PolicyDocument, PolicyDocumentError


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "policies"


def _document(*, actor_blocked: bool = False) -> dict[str, object]:
    actors: dict[str, object] = {}
    if actor_blocked:
        actors["alice"] = {"allowed_models": []}
    return {
        "schema_id": "hormuz.policy-document",
        "schema_version": 1,
        "organization_id": "xpounder",
        "policies": {
            "organization": {
                "allowed_clients": ["codex", "claude-code"],
                "allowed_models": ["gpt-5.4-mini", "gpt-5.4", "claude-sonnet-5"],
                "max_output_tokens": 32000,
                "monthly_budget_usd": 10000,
                "per_actor_monthly_budget_usd": 500,
            },
            "teams": {
                "engineering": {
                    "allowed_models": ["gpt-5.4-mini", "gpt-5.4", "claude-sonnet-5"],
                    "fallback_models": {
                        "openai": "gpt-5.4-mini",
                        "anthropic": "claude-sonnet-5",
                    },
                    "max_output_tokens": 16000,
                    "monthly_budget_usd": 5000,
                }
            },
            "actors": actors,
        },
        "egress_controls": {
            "openai": {"allow_response_storage": False, "allow_background": False},
            "secrets": {"mode": "redact"},
        },
    }


class PolicyDocumentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = GatewayConfig.load(
            ROOT / "config.example.json",
            environ={"HORMUZ_TOKEN": "test-identity-token"},
        )

    def test_document_is_canonical_and_change_summary_is_content_free(self) -> None:
        document = PolicyDocument.from_mapping(
            json.loads((FIXTURES / "policy-document-v1.json").read_text(encoding="utf-8")),
            config=self.config,
        )
        reordered = copy.deepcopy(_document())
        reordered["policies"] = dict(reversed(list(reordered["policies"].items())))  # type: ignore[index]
        equivalent = PolicyDocument.from_mapping(reordered, config=self.config)

        self.assertEqual(document.version_id, equivalent.version_id)
        self.assertTrue(document.version_id.startswith("sha256:"))
        self.assertEqual(document.snapshot_for(next(iter(self.config.identities_by_token.values()))).policy_version, document.version_id)
        summary = json.dumps(document.redacted_change_summary(), sort_keys=True)
        self.assertNotIn("gpt-5.4-mini", summary)
        self.assertNotIn("10000", summary)
        self.assertNotIn("5000", summary)

    def test_document_rejects_content_and_does_not_echo_it(self) -> None:
        value = _document()
        value["prompt"] = "do-not-store-this-sensitive-text"
        with self.assertRaises(PolicyDocumentError) as raised:
            PolicyDocument.from_mapping(value, config=self.config)
        self.assertNotIn("do-not-store-this-sensitive-text", str(raised.exception))

    def test_json_document_rejects_duplicate_keys_and_nonfinite_numbers(self) -> None:
        duplicate = json.dumps(_document()).replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1', 1)
        with self.assertRaises(PolicyDocumentError) as raised:
            PolicyDocument.from_json_bytes(duplicate.encode("utf-8"), config=self.config)
        self.assertNotIn("schema_version", str(raised.exception))

        nonfinite = _document()
        nonfinite["policies"]["organization"]["monthly_budget_usd"] = float("nan")  # type: ignore[index]
        with self.assertRaises(PolicyDocumentError) as raised:
            PolicyDocument.from_json_bytes(json.dumps(nonfinite).encode("utf-8"), config=self.config)
        self.assertNotIn("nan", str(raised.exception).lower())

        oversized = _document()
        oversized["policies"]["organization"]["monthly_budget_usd"] = 10**1000  # type: ignore[index]
        with self.assertRaises(PolicyDocumentError):
            PolicyDocument.from_mapping(oversized, config=self.config)

    def test_actor_policy_can_deny_an_administrator_without_affecting_authority_schema(self) -> None:
        document = PolicyDocument.from_mapping(_document(actor_blocked=True), config=self.config)
        snapshot = document.snapshot_for(next(iter(self.config.identities_by_token.values())))
        self.assertEqual(snapshot.effective_policy.allowed_models, ())

    def test_managed_policy_configuration_rejects_static_policy_and_group_bootstrap(self) -> None:
        managed = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        managed["usage_storage"] = {
            "backend": "postgresql",
            "postgres_dsn_env": "RUNTIME_DSN",
            "postgres_migration_dsn_env": "MIGRATION_DSN",
        }
        managed["policy_control"] = {
            "mode": "postgresql",
            "postgres_control_dsn_env": "CONTROL_DSN",
            "bootstrap_administrators": [
                {
                    "organization_id": "xpounder",
                    "actor_id": "alice",
                    "group": "Engineering",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "managed-policy-invalid.json"
            path.write_text(json.dumps(managed), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "policies is not permitted"):
                GatewayConfig.load(path, environ={"HORMUZ_TOKEN": "test-identity-token"})
            managed.pop("policies")
            path.write_text(json.dumps(managed), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "organization_id plus actor_id"):
                GatewayConfig.load(path, environ={"HORMUZ_TOKEN": "test-identity-token"})

    def test_managed_policy_configuration_requires_distinct_runtime_and_control_roles(self) -> None:
        managed = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        managed.pop("policies")
        managed["usage_storage"] = {
            "backend": "postgresql",
            "postgres_dsn_env": "RUNTIME_DSN",
            "postgres_migration_dsn_env": "MIGRATION_DSN",
            "postgres_runtime_role": "shared_hormuz_role",
        }
        managed["policy_control"] = {
            "mode": "postgresql",
            "postgres_control_dsn_env": "CONTROL_DSN",
            "postgres_control_role": "shared_hormuz_role",
            "bootstrap_administrators": [{"organization_id": "xpounder", "actor_id": "alice"}],
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "managed-policy-invalid-role.json"
            path.write_text(json.dumps(managed), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "must differ"):
                GatewayConfig.load(path, environ={"HORMUZ_TOKEN": "test-identity-token"})

    def test_configuration_rejects_nonfinite_model_price(self) -> None:
        value = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        value["model_routes"]["gpt-5.4-mini"]["input_cost_per_million"] = float("nan")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "invalid-price.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "input_cost_per_million"):
                GatewayConfig.load(path, environ={"HORMUZ_TOKEN": "test-identity-token"})


if __name__ == "__main__":
    unittest.main()
