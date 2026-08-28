from __future__ import annotations

import argparse
import ast
import inspect
import unittest
from pathlib import Path

import hormuz.cli as cli
import hormuz.commands.custody as custody_commands


def _subcommands(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    action = next(
        item
        for item in parser._actions
        if isinstance(item, argparse._SubParsersAction)
    )
    return action.choices


class CustodyCliOwnershipTests(unittest.TestCase):
    def test_public_cli_entry_points_and_compatibility_seam_remain_in_facade(self) -> None:
        self.assertEqual(cli.main.__module__, "hormuz.cli")
        self.assertEqual(cli.build_parser.__module__, "hormuz.cli")

        source = Path(cli.__file__).read_text(encoding="utf-8")
        definitions = {
            node.name
            for node in ast.parse(source).body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef))
        }
        self.assertEqual(
            {name for name in definitions if name.startswith("_custody_")},
            {"_custody_command_dependencies", "_custody_verify"},
        )
        self.assertLessEqual(len(inspect.getsource(cli._custody_verify).splitlines()), 4)

    def test_custody_module_owns_registration_and_execution_without_importing_cli(self) -> None:
        source = Path(custody_commands.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        definitions = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef))
        }
        self.assertTrue(
            {
                "CustodyCommandDependencies",
                "add_custody_commands",
                "_custody",
                "_custody_control",
                "_custody_executor",
                "_custody_verify",
                "_custody_seal",
                "_custody_rewrap",
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

    def test_custody_command_tree_remains_stable(self) -> None:
        custody = _subcommands(cli.build_parser())["custody"]
        custody_tree = _subcommands(custody)
        self.assertEqual(
            set(custody_tree),
            {
                "administrator",
                "approve",
                "authorize",
                "bootstrap",
                "evidence",
                "executor",
                "rewrap",
                "seal",
                "status",
                "verify",
            },
        )
        administrator_tree = _subcommands(custody_tree["administrator"])
        self.assertEqual(set(administrator_tree), {"grant", "retire", "revoke"})
        self.assertEqual(set(_subcommands(administrator_tree["retire"])), {"static"})

        evidence_tree = _subcommands(custody_tree["evidence"])
        self.assertEqual(set(evidence_tree), {"deletion", "export"})
        self.assertEqual(set(_subcommands(evidence_tree["deletion"])), {"check"})

        executor_tree = _subcommands(custody_tree["executor"])
        self.assertEqual(set(executor_tree), {"register"})
        self.assertEqual(set(_subcommands(executor_tree["register"])), {"assets"})


if __name__ == "__main__":
    unittest.main()
