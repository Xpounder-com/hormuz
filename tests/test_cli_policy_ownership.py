from __future__ import annotations

import argparse
import ast
import inspect
import unittest
from pathlib import Path

import hormuz.cli as cli
import hormuz.commands.policy as policy_commands


class PolicyCliOwnershipTests(unittest.TestCase):
    def test_public_cli_entry_points_remain_in_the_compatibility_facade(self) -> None:
        self.assertEqual(cli.main.__module__, "hormuz.cli")
        self.assertEqual(cli.build_parser.__module__, "hormuz.cli")

        source = Path(cli.__file__).read_text(encoding="utf-8")
        definitions = {
            node.name
            for node in ast.parse(source).body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef))
        }
        policy_definitions = {
            name for name in definitions if name.startswith("_policy_")
        }
        self.assertEqual(policy_definitions, {"_policy_command_dependencies"})
        self.assertIn("_write_policy_document", definitions)
        self.assertLessEqual(
            len(inspect.getsource(cli._write_policy_document).splitlines()),
            4,
        )

    def test_policy_module_owns_registration_and_execution_without_importing_cli(self) -> None:
        source = Path(policy_commands.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        definitions = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef))
        }
        self.assertTrue(
            {
                "add_policy_commands",
                "_policy_demo",
                "_policy_check",
                "_policy_control",
                "_policy_analysis",
                "_policy_create",
                "_policy_validate",
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

    def test_policy_command_tree_remains_stable(self) -> None:
        parser = cli.build_parser()
        top_level = next(
            action
            for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        )
        policy = top_level.choices["policy"]
        policy_commands_action = next(
            action
            for action in policy._actions
            if isinstance(action, argparse._SubParsersAction)
        )
        self.assertEqual(
            set(policy_commands_action.choices),
            {
                "activate",
                "administrator",
                "apply",
                "bootstrap",
                "check",
                "compare",
                "create",
                "demo",
                "evaluate",
                "export",
                "history",
                "preview",
                "recover",
                "rollback",
                "scenarios",
                "show",
                "stage",
                "status",
                "templates",
                "validate",
            },
        )


if __name__ == "__main__":
    unittest.main()
