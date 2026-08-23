from __future__ import annotations

import ast
import inspect
import unittest
from pathlib import Path

import hormuz._config_builder as config_builder
import hormuz._config_input as config_input
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

        self.assertNotIn("def _load_configuration_json", facade_source)
        self.assertNotIn("def _key_custody", facade_source)
        self.assertNotIn("def _postgres_pool_config", facade_source)
        self.assertIn("def build_gateway_config", builder_source)
        self.assertIn("def _key_custody", builder_source)
        self.assertIn("def _postgres_pool_config", builder_source)


if __name__ == "__main__":
    unittest.main()
