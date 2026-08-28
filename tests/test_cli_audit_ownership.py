from __future__ import annotations

import argparse
import ast
import inspect
import unittest
from pathlib import Path

import hormuz.cli as cli
import hormuz.commands.audit as audit_commands


def _subcommands(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    action = next(
        item
        for item in parser._actions
        if isinstance(item, argparse._SubParsersAction)
    )
    return action.choices


class AuditCliOwnershipTests(unittest.TestCase):
    def test_public_entry_points_and_private_compatibility_seams_remain_in_facade(self) -> None:
        self.assertEqual(cli.main.__module__, "hormuz.cli")
        self.assertEqual(cli.build_parser.__module__, "hormuz.cli")

        source = Path(cli.__file__).read_text(encoding="utf-8")
        definitions = {
            node.name
            for node in ast.parse(source).body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef))
        }
        expected = {
            "_audit_command_dependencies",
            "_audit_export",
            "_audit_anchor",
            "_audit_chain",
            "_write_audit_chain_checkpoint",
            "_read_audit_chain_checkpoint",
            "_is_sha256_digest",
            "_audit_since",
        }
        compatibility_names = {
            name
            for name in definitions
            if name.startswith("_audit_")
            or name
            in {
                "_write_audit_chain_checkpoint",
                "_read_audit_chain_checkpoint",
                "_is_sha256_digest",
            }
        }
        self.assertEqual(
            compatibility_names,
            expected,
        )
        for name in expected - {"_audit_command_dependencies"}:
            self.assertLessEqual(len(inspect.getsource(getattr(cli, name)).splitlines()), 4)

    def test_audit_module_owns_commands_without_importing_cli_or_backends(self) -> None:
        source = Path(audit_commands.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        definitions = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef))
        }
        self.assertTrue(
            {
                "AuditCommandDependencies",
                "add_audit_commands",
                "_audit",
                "_audit_export",
                "_audit_anchor",
                "_audit_chain",
                "_write_audit_chain_checkpoint",
                "_read_audit_chain_checkpoint",
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
        self.assertTrue(
            imported.isdisjoint(
                {
                    "cli",
                    "hormuz.cli",
                    "store",
                    "hormuz.store",
                    "postgres_store",
                    "hormuz.postgres_store",
                }
            )
        )

    def test_audit_command_tree_remains_stable(self) -> None:
        audit = _subcommands(cli.build_parser())["audit"]
        audit_tree = _subcommands(audit)
        self.assertEqual(set(audit_tree), {"anchor", "chain", "export"})
        self.assertEqual(
            set(_subcommands(audit_tree["chain"])),
            {"anchor", "epoch", "status", "verify"},
        )


if __name__ == "__main__":
    unittest.main()
