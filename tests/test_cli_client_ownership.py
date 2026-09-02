from __future__ import annotations

import argparse
import ast
import unittest
from pathlib import Path

import hormuz.cli as cli
import hormuz.commands.client as client_commands


def _subcommands(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    action = next(
        item
        for item in parser._actions
        if isinstance(item, argparse._SubParsersAction)
    )
    return action.choices


class ClientCliOwnershipTests(unittest.TestCase):
    def test_public_entry_points_and_private_compatibility_seams_remain_in_facade(self) -> None:
        self.assertEqual(cli.main.__module__, "hormuz.cli")
        self.assertEqual(cli.build_parser.__module__, "hormuz.cli")

        tree = ast.parse(Path(cli.__file__).read_text(encoding="utf-8"))
        definitions = {
            node.name: node
            for node in tree.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef))
        }
        expected = {"_client_config", "_client_base_url", "_auth_token"}
        self.assertEqual(
            {
                name
                for name in definitions
                if name.startswith("_client_") or name == "_auth_token"
            },
            expected,
        )
        for name in expected:
            wrapper = definitions[name]
            self.assertIsInstance(wrapper, ast.FunctionDef)
            self.assertEqual(len(wrapper.body), 1)
            self.assertIsInstance(wrapper.body[0], ast.Return)

    def test_client_module_owns_registration_and_execution_without_importing_cli(self) -> None:
        source = Path(client_commands.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        definitions = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef))
        }
        self.assertTrue(
            {
                "add_client_commands",
                "_client",
                "_client_config",
                "_client_base_url",
                "_auth_token",
            }.issubset(definitions)
        )
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
        self.assertTrue(imported.isdisjoint({"cli", "hormuz.cli"}))

    def test_client_and_auth_command_trees_remain_stable(self) -> None:
        commands = _subcommands(cli.build_parser())
        self.assertEqual(set(_subcommands(commands["client"])), {"config"})
        self.assertEqual(set(_subcommands(commands["auth"])), {"token", "session"})


if __name__ == "__main__":
    unittest.main()
