from __future__ import annotations

import ast
import inspect
import unittest
from pathlib import Path

import hormuz._config_builder as config_builder
import hormuz._config_custody as config_custody
import hormuz._config_identity as config_identity
import hormuz._config_ingress as config_ingress
import hormuz._config_input as config_input
import hormuz._config_persistence as config_persistence
import hormuz._config_policy as config_policy
import hormuz._config_routing as config_routing
import hormuz._config_values as config_values
import hormuz.config as config


class ConfigurationOwnershipTests(unittest.TestCase):
    def test_public_configuration_models_remain_in_the_compatibility_facade(self) -> None:
        for model in (
            config.GatewayConfig,
            config.Identity,
            config.ModelRoute,
            config.Policy,
            config.UsageStorageConfig,
        ):
            with self.subTest(model=model.__name__):
                self.assertEqual(model.__module__, "hormuz.config")

        load_source = inspect.getsource(config.GatewayConfig.load)
        self.assertIn("build_gateway_config", load_source)
        self.assertLessEqual(len(load_source.splitlines()), 8)

    def test_raw_input_validation_cannot_resolve_secrets_or_construct_runtime_config(self) -> None:
        source = Path(config_input.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        imported.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        self.assertTrue(imported.isdisjoint({"os", "config", "_config_builder"}))
        self.assertNotIn("GatewayConfig(", source)
        self.assertNotIn("environ", source)

    def test_facade_and_builder_have_distinct_ownership(self) -> None:
        facade_source = Path(config.__file__).read_text(encoding="utf-8")
        builder_source = Path(config_builder.__file__).read_text(encoding="utf-8")
        custody_source = Path(config_custody.__file__).read_text(encoding="utf-8")
        persistence_source = Path(config_persistence.__file__).read_text(encoding="utf-8")

        self.assertNotIn("def _load_configuration_json", facade_source)
        self.assertNotIn("def _key_custody", facade_source)
        self.assertNotIn("def _postgres_pool_config", facade_source)
        self.assertIn("def build_gateway_config", builder_source)
        self.assertNotIn("def _key_custody", builder_source)
        self.assertNotIn("def _postgres_pool_config", builder_source)
        self.assertIn("def _key_custody", custody_source)
        self.assertIn("def _postgres_pool_config", persistence_source)

    def test_domain_modules_own_construction_without_importing_the_orchestrator(self) -> None:
        domains = {
            config_custody: (
                "build_external_custody_domain",
                "build_custody_control_domain",
                "build_custody_lifecycle",
            ),
            config_identity: ("build_identity_domain", "resolve_static_identity_tokens"),
            config_ingress: ("build_ingress_domain", "resolve_ingress_credential"),
            config_persistence: ("build_persistence_domain",),
            config_policy: (
                "build_policy_control_domain",
                "build_policy_domain",
                "resolve_secret_controls",
            ),
            config_routing: ("build_upstream_domain", "build_model_route_domain"),
        }
        for module, owned_functions in domains.items():
            with self.subTest(module=module.__name__):
                source = Path(module.__file__).read_text(encoding="utf-8")
                self.assertNotIn("_config_builder", source)
                self.assertNotIn("import os", source)
                for function_name in owned_functions:
                    self.assertIn(f"def {function_name}", source)

    def test_builder_is_only_the_ordered_construction_orchestrator(self) -> None:
        source = Path(config_builder.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        function_names = {
            node.name for node in tree.body if isinstance(node, ast.FunctionDef)
        }
        self.assertEqual(
            function_names,
            {
                "build_gateway_config",
                "build_policy_analysis_context",
                "build_policy_validation_context",
                "_build_gateway_config",
            },
        )
        for module in (
            config_custody,
            config_identity,
            config_ingress,
            config_persistence,
            config_policy,
            config_routing,
            config_values,
        ):
            self.assertIn(module.__name__.removeprefix("hormuz."), source)


if __name__ == "__main__":
    unittest.main()
