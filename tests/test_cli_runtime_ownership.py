from __future__ import annotations

import argparse
import ast
import unittest
from pathlib import Path

import hormuz.cli as cli
import hormuz.commands.runtime as runtime_commands


def _command_tree(parser: argparse.ArgumentParser) -> dict[str, object] | None:
    action = next(
        (
            item
            for item in parser._actions
            if isinstance(item, argparse._SubParsersAction)
        ),
        None,
    )
    if action is None:
        return None
    return {
        name: _command_tree(child)
        for name, child in sorted(action.choices.items())
    }


EXPECTED_COMMAND_TREE = {
    "audit": {
        "anchor": None,
        "chain": {
            "anchor": None,
            "epoch": None,
            "status": None,
            "verify": None,
        },
        "export": None,
    },
    "auth": {"token": None},
    "client": {"config": None},
    "contract": {"manifest": None},
    "custody": {
        "administrator": {
            "grant": None,
            "retire": {"static": None},
            "revoke": None,
        },
        "approve": None,
        "authorize": None,
        "bootstrap": None,
        "evidence": {
            "deletion": {"check": None},
            "export": None,
        },
        "executor": {"register": {"assets": None}},
        "rewrap": None,
        "seal": None,
        "status": None,
        "verify": None,
    },
    "demo": None,
    "doctor": None,
    "policy": {
        "activate": None,
        "administrator": {
            "grant": None,
            "retire": {"static": None},
            "revoke": None,
        },
        "apply": None,
        "bootstrap": None,
        "check": None,
        "compare": None,
        "create": None,
        "demo": None,
        "evaluate": None,
        "export": None,
        "history": None,
        "preview": None,
        "recover": None,
        "rollback": None,
        "scenarios": {
            "add": None,
            "create": None,
            "validate": None,
        },
        "show": None,
        "stage": None,
        "status": None,
        "templates": None,
        "validate": None,
    },
    "serve": None,
    "status": None,
    "storage": {"migrate": None, "verify": None},
}


class RuntimeCliOwnershipTests(unittest.TestCase):
    def test_facade_retains_entry_normalization_and_thin_runtime_seams(self) -> None:
        self.assertEqual(cli.main.__module__, "hormuz.cli")
        self.assertEqual(cli.build_parser.__module__, "hormuz.cli")
        self.assertEqual(cli._normalize_command_argv.__module__, "hormuz.cli")

        tree = ast.parse(Path(cli.__file__).read_text(encoding="utf-8"))
        definitions = {
            node.name: node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
        }
        runtime_seams = {
            "_provider_free_demo",
            "_serve",
            "_doctor",
            "_status",
            "_budget_for_scope",
            "_managed_budget_for_scope",
            "_display_number",
            "_required_organization",
            "_storage",
            "_close_runtime_pool",
        }
        self.assertTrue(runtime_seams.issubset(definitions))
        for name in runtime_seams:
            self.assertEqual(len(definitions[name].body), 1)

    def test_runtime_module_owns_operations_without_importing_cli(self) -> None:
        source = Path(runtime_commands.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        definitions = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef))
        }
        self.assertTrue(
            {
                "RuntimeCommandDependencies",
                "add_runtime_commands",
                "add_storage_commands",
                "_runtime",
                "_contract_manifest",
                "_provider_free_demo",
                "_serve",
                "_doctor",
                "_status",
                "_storage",
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

    def test_complete_primary_command_tree_is_frozen(self) -> None:
        tree = _command_tree(cli.build_parser())
        self.assertIsInstance(tree, dict)
        self.assertEqual(tree.pop("portfolio"), {
            "attribute": None, "attributions": None,
            "archive": None, "bind": None, "bindings": None, "create": None,
            "list": None, "outcomes": None, "show": None, "tombstone": None, "version": None,
        })
        self.assertEqual(tree, EXPECTED_COMMAND_TREE)


if __name__ == "__main__":
    unittest.main()
