from __future__ import annotations

import copy
import hashlib
import ipaddress
import json
import tempfile
import unittest
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any

from hormuz.config import ConfigError, GatewayConfig
from hormuz.custody_lifecycle import CustodyAssetCatalog


ROOT = Path(__file__).resolve().parents[1]
MANAGED_REFERENCE = ROOT / "deploy" / "kubernetes" / "conformance" / "disaster-recovery" / "hormuz.json"


class _TrackingEnvironment(dict[str, str]):
    def __init__(self, values: Mapping[str, str]) -> None:
        super().__init__(values)
        self.reads: list[str] = []

    def get(self, key: str, default: str | None = None) -> str | None:
        self.reads.append(key)
        return super().get(key, default)


def _canonical(value: Any, *, config_directory: Path) -> object:
    if isinstance(value, Path):
        resolved = value.resolve()
        try:
            relative = resolved.relative_to(config_directory.resolve())
        except ValueError:
            return {"path": resolved.as_posix()}
        return {"config_relative_path": relative.as_posix()}
    if isinstance(value, (ipaddress.IPv4Network, ipaddress.IPv6Network)):
        return {"network": str(value)}
    if isinstance(value, bytes):
        return {"bytes_hex": value.hex()}
    if isinstance(value, CustodyAssetCatalog):
        return {
            "type": type(value).__name__,
            "assets": _canonical(value.assets, config_directory=config_directory),
        }
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "type": type(value).__name__,
            "fields": {
                field.name: _canonical(getattr(value, field.name), config_directory=config_directory)
                for field in fields(value)
            },
        }
    if isinstance(value, Mapping):
        pairs = [
            [
                _canonical(key, config_directory=config_directory),
                _canonical(item, config_directory=config_directory),
            ]
            for key, item in value.items()
        ]
        pairs.sort(key=lambda pair: json.dumps(pair[0], sort_keys=True, separators=(",", ":")))
        return {"mapping": pairs}
    if isinstance(value, (tuple, list)):
        return [_canonical(item, config_directory=config_directory) for item in value]
    if isinstance(value, (set, frozenset)):
        items = [_canonical(item, config_directory=config_directory) for item in value]
        return {"set": sorted(items, key=lambda item: json.dumps(item, sort_keys=True))}
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise AssertionError(f"configuration snapshot does not support {type(value).__name__}")


def _canonical_bytes(config: GatewayConfig) -> bytes:
    value = _canonical(config, config_directory=config.source_path.parent)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _snapshot_sha256(config: GatewayConfig) -> str:
    # Freeze every v1 field, without treating the separately tested, opt-in
    # portfolio extension as a change to the legacy configuration contract.
    value = _canonical(config, config_directory=config.source_path.parent)
    assert isinstance(value, dict) and isinstance(value["fields"], dict)
    assert value["fields"].pop("portfolio_control") is None
    assert value["fields"].pop("attribution_control") is None
    # The explicitly opt-in directory adds one default-false field. Keep every
    # preceding field's frozen digest, rather than replacing the baseline.
    assert value["fields"]["session_broker"]["fields"].pop("onboarding_enabled") is False
    assert value["fields"]["session_broker"]["fields"].pop("console_enabled") is False
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


class ConfigurationConstructionEquivalenceTests(unittest.TestCase):
    LOCAL_SNAPSHOT_SHA256 = "cf53bcb3b090eb158ce2717b511bceee0639174154aabcf9a3411d1812ddca9b"
    MANAGED_SNAPSHOT_SHA256 = "6f5ac7b96e7ab26409d63df7915c1e2691fb7a02dfa5fb9658623040fdb86a51"

    def _local_value(self) -> dict[str, object]:
        value = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        value["listen"] = {"host": "0.0.0.0", "port": 9443}
        value["ingress"] = {
            "mode": "external_tls_proxy",
            "trusted_proxy_cidrs": ["127.0.0.1/32", "10.42.0.0/16", "fd00:42::/64"],
            "credential_env": "TEST_INGRESS_CREDENTIAL",
        }
        value["authentication"] = {
            "oidc": {
                "issuers": [
                    {
                        "issuer": "https://identity.example.test",
                        "audiences": ["hormuz-api", "hormuz-cli"],
                        "jwks_uri": "https://identity.example.test/oauth2/keys",
                        "algorithms": ["RS256", "ES256"],
                        "clock_skew_seconds": 30,
                        "discovery_cache_seconds": 1800,
                        "subjects": [
                            {
                                "subject": "employee-bob-stable-id",
                                "actor_id": "bob",
                                "actor_name": "Bob Example",
                                "team_id": "marketing",
                                "team_name": "Marketing",
                                "organization_id": "xpounder",
                                "clearance": "internal",
                                "identity_type": "human",
                                "allowed_clients": ["codex"],
                            }
                        ],
                    }
                ]
            }
        }
        value["egress_controls"]["secrets"]["custom_secret_envs"] = ["TEST_CUSTOM_SECRET"]  # type: ignore[index]
        value["policies"]["actors"] = {  # type: ignore[index]
            "bob": {
                "allowed_clients": ["codex"],
                "allowed_models": ["gpt-5.4-mini"],
                "max_output_tokens": 512,
            }
        }
        return value

    @staticmethod
    def _local_environment() -> _TrackingEnvironment:
        return _TrackingEnvironment(
            {
                "HORMUZ_TOKEN": "test-static-identity-credential",
                "TEST_INGRESS_CREDENTIAL": "test-private-proxy-credential",
                "TEST_CUSTOM_SECRET": "test-redaction-marker",
                "OPENAI_API_KEY": "unused-provider-credential",
                "ANTHROPIC_API_KEY": "unused-provider-credential-two",
                "UNUSED_SECRET": "must-never-be-read",
            }
        )

    @staticmethod
    def _managed_environment() -> _TrackingEnvironment:
        return _TrackingEnvironment(
            {
                "HORMUZ_TOKEN": "test-managed-alice-credential",
                "HORMUZ_BOB_TOKEN": "test-managed-bob-credential",
                "HORMUZ_INGRESS_CREDENTIAL": "test-managed-proxy-credential",
                "HORMUZ_POSTGRES_DSN": "unused-runtime-dsn",
                "HORMUZ_POSTGRES_MIGRATION_DSN": "unused-migration-dsn",
                "HORMUZ_POLICY_CONTROL_DSN": "unused-policy-dsn",
                "HORMUZ_CUSTODY_CONTROL_DSN": "unused-custody-dsn",
                "HORMUZ_CUSTODY_EXECUTOR_DSN": "unused-executor-dsn",
                "HORMUZ_OPENBAO_TOKEN": "unused-openbao-credential",
                "OPENAI_API_KEY": "unused-provider-credential",
                "ANTHROPIC_API_KEY": "unused-provider-credential-two",
            }
        )

    def _assert_frozen_dataclass_tree(self, value: object, *, seen: set[int] | None = None) -> None:
        visited = seen if seen is not None else set()
        if id(value) in visited:
            return
        visited.add(id(value))
        if isinstance(value, CustodyAssetCatalog):
            self._assert_frozen_dataclass_tree(value.assets, seen=visited)
            return
        if is_dataclass(value) and not isinstance(value, type):
            self.assertTrue(type(value).__dataclass_params__.frozen, type(value).__name__)  # type: ignore[attr-defined]
            for field in fields(value):
                self._assert_frozen_dataclass_tree(getattr(value, field.name), seen=visited)
            return
        if isinstance(value, Mapping):
            for key, item in value.items():
                self._assert_frozen_dataclass_tree(key, seen=visited)
                self._assert_frozen_dataclass_tree(item, seen=visited)
            return
        if isinstance(value, (tuple, list, set, frozenset)):
            for item in value:
                self._assert_frozen_dataclass_tree(item, seen=visited)

    def test_representative_local_construction_matches_frozen_value_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "hormuz.json"
            path.write_text(json.dumps(self._local_value()), encoding="utf-8")
            first_environment = self._local_environment()
            second_environment = self._local_environment()
            first = GatewayConfig.load(path, environ=first_environment)
            second = GatewayConfig.load(path, environ=second_environment)

        self.assertEqual(_canonical_bytes(first), _canonical_bytes(second))
        self.assertEqual(_snapshot_sha256(first), self.LOCAL_SNAPSHOT_SHA256)
        self.assertEqual(
            first_environment.reads,
            ["HORMUZ_TOKEN", "TEST_INGRESS_CREDENTIAL", "TEST_CUSTOM_SECRET"],
        )
        self.assertEqual(second_environment.reads, first_environment.reads)
        self._assert_frozen_dataclass_tree(first)

    def test_representative_managed_construction_matches_frozen_value_snapshot(self) -> None:
        first_environment = self._managed_environment()
        second_environment = self._managed_environment()
        first = GatewayConfig.load(MANAGED_REFERENCE, environ=first_environment)
        second = GatewayConfig.load(MANAGED_REFERENCE, environ=second_environment)

        self.assertEqual(_canonical_bytes(first), _canonical_bytes(second))
        self.assertEqual(_snapshot_sha256(first), self.MANAGED_SNAPSHOT_SHA256)
        self.assertEqual(
            first_environment.reads,
            ["HORMUZ_TOKEN", "HORMUZ_BOB_TOKEN", "HORMUZ_INGRESS_CREDENTIAL"],
        )
        self.assertEqual(second_environment.reads, first_environment.reads)
        self._assert_frozen_dataclass_tree(first)

    def test_invalid_semantics_keep_exact_error_and_credential_rejection_point(self) -> None:
        cases: list[tuple[str, dict[str, object], str]] = []

        invalid_storage = self._local_value()
        invalid_storage["usage_storage"] = {"backend": "remote-magic"}
        cases.append(
            (
                "storage",
                invalid_storage,
                "usage_storage.backend must be sqlite or postgresql",
            )
        )

        invalid_policy = self._local_value()
        invalid_policy["policies"]["organization"]["allowed_models"] = ["unknown-model"]  # type: ignore[index]
        cases.append(
            (
                "policy",
                invalid_policy,
                "Policy references unknown model alias: unknown-model",
            )
        )

        managed = json.loads(MANAGED_REFERENCE.read_text(encoding="utf-8"))
        managed.pop("custody_retention")
        cases.append(
            (
                "custody",
                managed,
                "custody_control.mode postgresql requires custody_retention",
            )
        )

        for name, value, expected in cases:
            with self.subTest(case=name), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "hormuz.json"
                path.write_text(json.dumps(value), encoding="utf-8")
                environment = self._local_environment()
                with self.assertRaises(ConfigError) as raised:
                    GatewayConfig.load(path, environ=environment)
            self.assertEqual(str(raised.exception), expected)
            self.assertEqual(environment.reads, [])

        missing_ingress = self._local_value()
        environment = self._local_environment()
        del environment["TEST_INGRESS_CREDENTIAL"]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "hormuz.json"
            path.write_text(json.dumps(missing_ingress), encoding="utf-8")
            with self.assertRaises(ConfigError) as raised:
                GatewayConfig.load(path, environ=environment)
        self.assertEqual(
            str(raised.exception),
            "Required ingress credential environment variable is not set: TEST_INGRESS_CREDENTIAL",
        )
        self.assertEqual(environment.reads, ["HORMUZ_TOKEN", "TEST_INGRESS_CREDENTIAL"])

    def test_strict_input_failure_precedes_all_construction_and_environment_reads(self) -> None:
        environment = self._local_environment()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "hormuz.json"
            path.write_bytes(b'{"listen":{},"listen":{}}')
            with self.assertRaises(ConfigError) as raised:
                GatewayConfig.load(path, environ=environment)

        self.assertEqual(str(raised.exception), "configuration_duplicate_member")
        self.assertEqual(environment.reads, [])


if __name__ == "__main__":
    unittest.main()
