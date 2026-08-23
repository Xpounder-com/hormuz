from __future__ import annotations

import ast
import unittest
from pathlib import Path

import hormuz._contract_schemas.audit as audit_schemas
import hormuz._contract_schemas.common as common_schemas
import hormuz._contract_schemas.constants as schema_constants
import hormuz._contract_schemas.health as health_schemas
import hormuz._contract_schemas.policy as policy_schemas
import hormuz._contract_schemas.request_attempt as request_attempt_schemas
import hormuz._contract_schemas.usage as usage_schemas
import hormuz.contracts as contracts


class ContractOwnershipTests(unittest.TestCase):
    def test_public_contract_api_remains_on_the_compatibility_facade(self) -> None:
        for validator in (
            contracts.validate_audit_event,
            contracts.validate_audit_anchor,
            contracts.validate_audit_chain_entry,
            contracts.validate_audit_chain_checkpoint,
            contracts.validate_contract,
            contracts.validate_policy_control_event,
            contracts.validate_request_attempt,
            contracts.validate_request_attempt_event,
        ):
            with self.subTest(validator=validator.__name__):
                self.assertEqual(validator.__module__, "hormuz.contracts")

        self.assertIs(contracts.ContractValidationError, common_schemas.ContractValidationError)
        self.assertEqual(contracts.ContractValidationError.__module__, "hormuz.contracts")
        self.assertEqual(contracts.AUDIT_EVENT_SCHEMA_ID, schema_constants.AUDIT_EVENT_SCHEMA_ID)

    def test_schema_families_own_their_validator_implementations(self) -> None:
        expectations = {
            audit_schemas: ("validate_audit_event", "_validate_audit_anchor"),
            health_schemas: ("_validate_health", "_validate_readiness"),
            policy_schemas: ("validate_policy_control_event", "_validate_policy_decision"),
            request_attempt_schemas: ("validate_request_attempt", "validate_request_attempt_event"),
            usage_schemas: ("_validate_usage_summary", "_validate_usage_report"),
        }
        for module, names in expectations.items():
            source = Path(module.__file__).read_text(encoding="utf-8")
            tree = ast.parse(source)
            defined = {
                node.name
                for node in tree.body
                if isinstance(node, (ast.ClassDef, ast.FunctionDef))
            }
            with self.subTest(module=module.__name__):
                self.assertTrue(set(names).issubset(defined))

        facade_source = Path(contracts.__file__).read_text(encoding="utf-8")
        facade_definitions = {
            node.name
            for node in ast.parse(facade_source).body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef))
        }
        self.assertNotIn("_validate_audit_anchor", facade_definitions)
        self.assertNotIn("_validate_policy_decision", facade_definitions)
        self.assertNotIn("_validate_usage_report", facade_definitions)
        self.assertLess(len(facade_source.splitlines()), 700)

    def test_internal_schema_modules_do_not_import_the_public_facade(self) -> None:
        for module in (
            audit_schemas,
            common_schemas,
            schema_constants,
            health_schemas,
            policy_schemas,
            request_attempt_schemas,
            usage_schemas,
        ):
            source = Path(module.__file__).read_text(encoding="utf-8")
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
            with self.subTest(module=module.__name__):
                self.assertTrue(imported.isdisjoint({"contracts", "hormuz.contracts"}))


if __name__ == "__main__":
    unittest.main()
