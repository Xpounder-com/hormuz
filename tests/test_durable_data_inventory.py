from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tools.verify_durable_data_inventory import (
    DurableDataInventoryError,
    validate_durable_data_inventory,
)


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


class DurableDataInventoryTests(unittest.TestCase):
    def _copy_contract(self, target: Path) -> None:
        for relative in (
            "docs/durable-data-v1.json",
            "docs/DURABLE_DATA.md",
            "hormuz/_sqlite_schema.py",
            "hormuz/_portfolio_schema.py",
            "hormuz/_attribution_schema.py",
            "hormuz/_outcome_schema.py",
            "hormuz/_finance_schema.py",
            "hormuz/_budget_schema.py",
            "hormuz/postgres.py",
            "MANIFEST.in",
        ):
            source = REPOSITORY_ROOT / relative
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        shutil.copytree(
            REPOSITORY_ROOT / "hormuz/migrations/postgresql",
            target / "hormuz/migrations/postgresql",
        )

    def _inventory(self, root: Path) -> tuple[Path, dict[str, object]]:
        path = root / "docs/durable-data-v1.json"
        return path, json.loads(path.read_text(encoding="utf-8"))

    def test_current_inventory_covers_every_durable_class(self) -> None:
        result = validate_durable_data_inventory(REPOSITORY_ROOT)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["database_class_count"], 21)
        self.assertEqual(result["sqlite_table_count"], 36)
        self.assertEqual(result["postgresql_table_count"], 58)
        self.assertEqual(result["operator_artifact_count"], 7)
        self.assertEqual(result["excluded_customer_system_count"], 7)
        self.assertFalse(result["hosted_customer_data_service"])
        self.assertFalse(result["universal_erasure_claim"])

    def test_unregistered_sqlite_table_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            path = root / "hormuz/_sqlite_schema.py"
            path.write_text(
                path.read_text(encoding="utf-8")
                + "\nCREATE TABLE undocumented_customer_data (id TEXT);\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                DurableDataInventoryError, "sqlite_table_inventory_mismatch"
            ):
                validate_durable_data_inventory(root)

    def test_unregistered_postgresql_table_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            path = root / "hormuz/migrations/postgresql/0008_custody_evidence_retention.sql"
            path.write_text(
                path.read_text(encoding="utf-8")
                + "\nCREATE TABLE {schema}.undocumented_customer_data (id TEXT);\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                DurableDataInventoryError, "postgres_table_inventory_mismatch"
            ):
                validate_durable_data_inventory(root)

    def test_request_content_storage_claim_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            path, inventory = self._inventory(root)
            classes = inventory["database_classes"]
            assert isinstance(classes, list) and isinstance(classes[0], dict)
            classes[0]["contains_prompt_or_response_body"] = True
            path.write_text(json.dumps(inventory), encoding="utf-8")
            with self.assertRaisesRegex(
                DurableDataInventoryError, "request_content_boundary_invalid"
            ):
                validate_durable_data_inventory(root)

    def test_hosted_service_claim_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            path, inventory = self._inventory(root)
            inventory["hosted_customer_data_service"] = True
            path.write_text(json.dumps(inventory), encoding="utf-8")
            with self.assertRaisesRegex(
                DurableDataInventoryError, "hosted_customer_data_service_claim_invalid"
            ):
                validate_durable_data_inventory(root)

    def test_universal_erasure_claim_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            path, inventory = self._inventory(root)
            inventory["universal_erasure_claim"] = True
            path.write_text(json.dumps(inventory), encoding="utf-8")
            with self.assertRaisesRegex(
                DurableDataInventoryError, "universal_erasure_claim_invalid"
            ):
                validate_durable_data_inventory(root)

    def test_encrypted_custody_envelope_cannot_claim_content_exclusion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            path, inventory = self._inventory(root)
            artifacts = inventory["operator_artifacts"]
            assert isinstance(artifacts, list)
            envelope = next(
                item
                for item in artifacts
                if isinstance(item, dict) and item.get("id") == "encrypted_custody_envelope"
            )
            envelope["prompt_or_response_body_boundary"] = "excluded_by_hormuz_contract"
            path.write_text(json.dumps(inventory), encoding="utf-8")
            with self.assertRaisesRegex(
                DurableDataInventoryError, "request_content_boundary_invalid"
            ):
                validate_durable_data_inventory(root)

    def test_client_local_history_cannot_be_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            path, inventory = self._inventory(root)
            systems = inventory["excluded_customer_systems"]
            assert isinstance(systems, list)
            inventory["excluded_customer_systems"] = [
                item
                for item in systems
                if not isinstance(item, dict) or item.get("id") != "client_local_history"
            ]
            path.write_text(json.dumps(inventory), encoding="utf-8")
            with self.assertRaisesRegex(
                DurableDataInventoryError, "excluded_customer_system_set_invalid"
            ):
                validate_durable_data_inventory(root)

    def test_documentation_must_name_every_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            path = root / "docs/DURABLE_DATA.md"
            path.write_text(
                path.read_text(encoding="utf-8").replace(
                    "`audit_export_jsonl`", "`removed_artifact`"
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                DurableDataInventoryError, "documentation_incomplete"
            ):
                validate_durable_data_inventory(root)


if __name__ == "__main__":
    unittest.main()
