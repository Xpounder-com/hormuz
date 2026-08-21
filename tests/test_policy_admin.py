from __future__ import annotations

import copy
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest
from unittest import mock

from hormuz.config import (
    ConfigError,
    GatewayConfig,
    Identity,
    SCIMGroupAuthorizationError,
    configuration_from_policy_projection,
)
from hormuz.cli import main
from hormuz.policy_projection import policy_projection, policy_projection_sha256
from hormuz.policy_runtime import PolicyRuntime
from hormuz.postgres_policy_store import ActivePolicy, PolicyAdminError


ROOT = Path(__file__).resolve().parents[1]


class PolicyProjectionMaterializationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = GatewayConfig.load(
            ROOT / "config.example.json",
            environ={"HORMUZ_TOKEN": "test-identity-token"},
        )
        cls.identity = next(iter(cls.config.identities_by_actor.values()))

    def test_canonical_projection_round_trips_without_secret_material(self) -> None:
        projection = policy_projection(
            self.config,
            self.identity.organization_id,
        )
        candidate = configuration_from_policy_projection(
            self.config,
            projection,
            organization_id=self.identity.organization_id,
        )
        self.assertEqual(
            policy_projection(candidate, self.identity.organization_id),
            projection,
        )

    def test_v2_projection_remains_canonical_for_existing_active_policies(self) -> None:
        projection = policy_projection(
            self.config,
            self.identity.organization_id,
            schema="hormuz.policy-projection.v2",
        )
        candidate = configuration_from_policy_projection(
            self.config,
            projection,
            organization_id=self.identity.organization_id,
        )

        self.assertNotIn("provider_cache", projection["organization_policy"])
        self.assertEqual(
            policy_projection(
                candidate,
                self.identity.organization_id,
                schema="hormuz.policy-projection.v2",
            ),
            projection,
        )

    def test_scim_group_bindings_are_tenant_qualified_and_policy_owned(self) -> None:
        raw = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        raw["policies"]["authorization_profiles"] = {
            "engineering-standard": {
                "organization_id": "xpounder",
                "team_id": "engineering",
                "team_name": "Engineering",
                "clearance": "internal",
                "allowed_clients": ["codex"],
                "capabilities": ["usage_self_viewer"],
                "policy": {
                    "allowed_clients": ["codex"],
                    "allowed_models": ["gpt-5.4"],
                    "max_output_tokens": 4096,
                },
            }
        }
        raw["policies"]["team_bindings"] = [
            {
                "organization_id": "xpounder",
                "scim_group_external_id": "engineering-employees",
                "team_id": "engineering",
                "policy_id": "engineering-standard",
            }
        ]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            config = GatewayConfig.load(
                path,
                environ={"HORMUZ_TOKEN": "test-identity-token"},
            )

        authorization = config.resolve_scim_group_authorization(
            "xpounder", ("engineering-employees",)
        )
        self.assertEqual(authorization.policy_id, "engineering-standard")
        self.assertEqual(authorization.allowed_clients, ("codex",))
        self.assertEqual(authorization.capabilities, ("usage_self_viewer",))
        with self.assertRaisesRegex(
            SCIMGroupAuthorizationError, "directory_subject_group_unbound"
        ):
            config.resolve_scim_group_authorization(
                "xpounder", ("engineering-employees", "unbound-group")
            )

        dynamic_identity = Identity(
            token_env="",
            token="",
            actor_id="usr_directory_alice",
            actor_name="Directory Alice",
            team_id=authorization.team_id,
            team_name=authorization.team_name,
            organization_id="xpounder",
            clearance=authorization.clearance,
            allowed_clients=authorization.allowed_clients,
            capabilities=authorization.capabilities,
            authentication_source="directory:https://identity.example",
            authorization_profile_id=authorization.policy_id,
        )
        self.assertEqual(
            config.resolved_policy(dynamic_identity).allowed_models,
            ("gpt-5.4",),
        )

        projection = policy_projection(config, "xpounder")
        self.assertEqual(projection["schema"], "hormuz.policy-projection.v4")
        self.assertEqual(
            projection["team_bindings"], raw["policies"]["team_bindings"]
        )
        candidate = configuration_from_policy_projection(
            config,
            projection,
            organization_id="xpounder",
        )
        self.assertEqual(policy_projection(candidate, "xpounder"), projection)
        fingerprint = policy_projection_sha256(projection)
        active = ActivePolicy(
            version_id="hpv_v1_" + fingerprint,
            projection_sha256=fingerprint,
            projection=projection,
            activated_at="2026-08-20T00:00:00+00:00",
            activated_by_actor_id="policy-admin",
            activated_by_actor_name="Policy Admin",
            activation_sequence=1,
        )
        runtime = PolicyRuntime(
            config,
            SimpleNamespace(active_for_organization=lambda _: active),  # type: ignore[arg-type]
        )
        self.assertEqual(
            runtime.resolve_scim_group_authorization(
                "xpounder", ("engineering-employees",)
            ).policy_id,
            "engineering-standard",
        )

    def test_projection_cannot_cross_tenants_or_add_secret_sources(self) -> None:
        projection = policy_projection(
            self.config,
            self.identity.organization_id,
        )
        other = copy.deepcopy(projection)
        other["organization_id"] = "other-tenant"
        with self.assertRaisesRegex(ConfigError, "organization"):
            configuration_from_policy_projection(
                self.config,
                other,
                organization_id=self.identity.organization_id,
            )
        unprovisioned = copy.deepcopy(projection)
        unprovisioned["secret_controls"]["custom_secret_envs"] = ["NEW_SECRET"]
        with self.assertRaisesRegex(ConfigError, "unprovisioned"):
            configuration_from_policy_projection(
                self.config,
                unprovisioned,
                organization_id=self.identity.organization_id,
            )

    def test_preprovisioned_secret_dictionary_and_approval_key_round_trip(self) -> None:
        raw = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        raw["egress_controls"]["secrets"]["custom_secret_envs"] = ["COMPANY_SECRET"]
        raw["egress_controls"]["dlp"]["approval"] = {
            "enabled": True,
            "fingerprint_key_env": "APPROVAL_KEY",
        }
        raw["identities"][0]["capabilities"] = ["dlp_approver"]
        raw["egress_controls"]["dlp"]["dictionaries"] = [
            {
                "rule_id": "company.codename",
                "category": "company_dictionary",
                "confidence": "high",
                "action": "require_approval",
                "values_env": "COMPANY_VALUES",
            }
        ]
        environment = {
            "HORMUZ_TOKEN": "test-identity-token",
            "COMPANY_SECRET": "secret-value-long-enough",
            "COMPANY_VALUES": json.dumps(["PROJECT-ORCHID"]),
            "APPROVAL_KEY": base64.urlsafe_b64encode(b"k" * 32).decode("ascii"),
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            config = GatewayConfig.load(path, environ=environment)
        identity = next(iter(config.identities_by_actor.values()))
        projection = policy_projection(config, identity.organization_id)
        candidate = configuration_from_policy_projection(
            config,
            projection,
            organization_id=identity.organization_id,
        )
        self.assertEqual(
            policy_projection(candidate, identity.organization_id),
            projection,
        )
        self.assertEqual(
            candidate.secret_controls.custom_secret_values,
            config.secret_controls.custom_secret_values,
        )
        self.assertEqual(
            candidate.dlp_controls.approval.fingerprint_key,
            config.dlp_controls.approval.fingerprint_key,
        )

    def test_runtime_reads_active_pointer_each_time_and_uses_exact_version(self) -> None:
        projection = policy_projection(
            self.config,
            self.identity.organization_id,
        )
        changed = copy.deepcopy(projection)
        changed["organization_policy"]["max_output_tokens"] = 17
        fingerprint = policy_projection_sha256(changed)
        active = ActivePolicy(
            version_id="hpv_v1_" + fingerprint,
            projection_sha256=fingerprint,
            projection=changed,
            activated_at="2026-08-20T00:00:00+00:00",
            activated_by_actor_id="admin",
            activated_by_actor_name="Admin",
            activation_sequence=1,
        )
        store = SimpleNamespace(active=mock.Mock(return_value=active))
        runtime = PolicyRuntime(self.config, store)  # type: ignore[arg-type]

        first = runtime.resolve(self.identity)
        second = runtime.resolve(self.identity)

        self.assertEqual(first.version_id, active.version_id)
        self.assertEqual(
            first.config.organization_policy.max_output_tokens,
            17,
        )
        self.assertIs(first.config, second.config)
        self.assertEqual(store.active.call_count, 2)

    def test_runtime_accepts_an_existing_v2_active_projection(self) -> None:
        projection = policy_projection(
            self.config,
            self.identity.organization_id,
            schema="hormuz.policy-projection.v2",
        )
        fingerprint = policy_projection_sha256(projection)
        active = ActivePolicy(
            version_id="hpv_v1_" + fingerprint,
            projection_sha256=fingerprint,
            projection=projection,
            activated_at="2026-08-20T00:00:00+00:00",
            activated_by_actor_id="admin",
            activated_by_actor_name="Admin",
            activation_sequence=1,
        )
        runtime = PolicyRuntime(
            self.config,
            SimpleNamespace(active=lambda **_: active),  # type: ignore[arg-type]
        )

        resolved = runtime.resolve(self.identity)

        self.assertEqual(resolved.version_id, active.version_id)
        self.assertTrue(resolved.config.resolved_policy(self.identity).provider_cache.explicit_requests_allowed)

    def test_runtime_fails_closed_for_noncanonical_active_projection(self) -> None:
        projection = policy_projection(
            self.config,
            self.identity.organization_id,
        )
        fingerprint = policy_projection_sha256(projection)
        corrupted = copy.deepcopy(projection)
        corrupted["organization_policy"]["max_output_tokens"] = 19
        active = ActivePolicy(
            version_id="hpv_v1_" + fingerprint,
            projection_sha256=fingerprint,
            projection=corrupted,
            activated_at="2026-08-20T00:00:00+00:00",
            activated_by_actor_id="admin",
            activated_by_actor_name="Admin",
            activation_sequence=1,
        )
        runtime = PolicyRuntime(
            self.config,
            SimpleNamespace(active=lambda **_: active),  # type: ignore[arg-type]
        )
        with self.assertRaisesRegex(PolicyAdminError, "active_policy_invalid"):
            runtime.resolve(self.identity)

    def test_independent_runtime_readers_converge_after_pointer_change(self) -> None:
        first_projection = policy_projection(
            self.config,
            self.identity.organization_id,
        )
        second_projection = copy.deepcopy(first_projection)
        second_projection["organization_policy"]["max_output_tokens"] = 23

        def active_for(projection: dict[str, object], sequence: int) -> ActivePolicy:
            fingerprint = policy_projection_sha256(projection)
            return ActivePolicy(
                version_id="hpv_v1_" + fingerprint,
                projection_sha256=fingerprint,
                projection=projection,
                activated_at=f"2026-08-20T00:00:0{sequence}+00:00",
                activated_by_actor_id="admin",
                activated_by_actor_name="Admin",
                activation_sequence=sequence,
            )

        pointer = [active_for(first_projection, 1)]
        pointer_lock = threading.Lock()

        def read_active(**_: object) -> ActivePolicy:
            with pointer_lock:
                return pointer[0]

        runtimes = (
            PolicyRuntime(
                self.config,
                SimpleNamespace(active=read_active),  # type: ignore[arg-type]
            ),
            PolicyRuntime(
                self.config,
                SimpleNamespace(active=read_active),  # type: ignore[arg-type]
            ),
        )

        with ThreadPoolExecutor(max_workers=2) as executor:
            initial = tuple(
                executor.map(lambda runtime: runtime.resolve(self.identity), runtimes)
            )
        self.assertEqual({value.version_id for value in initial}, {pointer[0].version_id})

        with pointer_lock:
            pointer[0] = active_for(second_projection, 2)
        with ThreadPoolExecutor(max_workers=2) as executor:
            changed = tuple(
                executor.map(lambda runtime: runtime.resolve(self.identity), runtimes)
            )

        self.assertEqual({value.version_id for value in changed}, {pointer[0].version_id})
        self.assertEqual(
            {value.config.organization_policy.max_output_tokens for value in changed},
            {23},
        )


class PolicyAdminCLITests(unittest.TestCase):
    def test_remote_active_uses_admin_client_without_loading_server_config(self) -> None:
        client = mock.Mock()
        client.active.return_value = {"schema": "test-policy-active"}
        output = io.StringIO()
        with (
            mock.patch.dict(os.environ, {"HORMUZ_TOKEN": "operator-credential"}),
            mock.patch("hormuz.cli.PolicyAdminClient", return_value=client) as factory,
            redirect_stdout(output),
        ):
            result = main(
                [
                    "policy",
                    "active",
                    "--gateway",
                    "http://127.0.0.1:8787",
                    "--allow-insecure-http",
                ]
            )
        self.assertEqual(result, 0)
        factory.assert_called_once_with(
            "http://127.0.0.1:8787",
            credential="operator-credential",
            allow_insecure_http=True,
        )
        client.active.assert_called_once_with()
        self.assertEqual(json.loads(output.getvalue()), {"schema": "test-policy-active"})

    def test_export_prints_canonical_secret_free_projection(self) -> None:
        output = io.StringIO()
        with (
            mock.patch.dict(os.environ, {"HORMUZ_TOKEN": "test-identity-token"}),
            redirect_stdout(output),
        ):
            result = main(
                [
                    "--config",
                    str(ROOT / "config.example.json"),
                    "policy",
                    "export",
                    "--organization",
                    "xpounder",
                ]
            )
        self.assertEqual(result, 0)
        value = json.loads(output.getvalue())
        self.assertEqual(value["schema"], "hormuz.policy-projection.v4")
        self.assertEqual(value["organization_id"], "xpounder")
        self.assertNotIn("test-identity-token", output.getvalue())


if __name__ == "__main__":
    unittest.main()
