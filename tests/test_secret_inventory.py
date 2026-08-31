from __future__ import annotations

import copy
import shutil
import tempfile
import unittest
from pathlib import Path

from hormuz._secret_inventory import (
    KEY_PURPOSES,
    SECRET_INVENTORY_FILENAME,
    SecretInventoryError,
    discover_ambient_credential_reads,
    discover_environment_reads,
    inventory_path,
    load_secret_inventory,
    validate_secret_inventory,
)


ROOT = Path(__file__).resolve().parents[1]


class SecretInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.inventory = load_secret_inventory(source_root=ROOT)

    def test_packaged_inventory_exactly_covers_active_core_sources(self) -> None:
        self.assertEqual(inventory_path().name, SECRET_INVENTORY_FILENAME)
        self.assertTrue(inventory_path().is_file())
        self.assertEqual(
            len(self.inventory["environment_reads"]),
            len(discover_environment_reads(ROOT)),
        )
        self.assertEqual(
            len(self.inventory["ambient_credential_reads"]),
            len(discover_ambient_credential_reads(ROOT)),
        )
        purposes = {
            entry["key_purpose"]: entry["status"]
            for entry in self.inventory["key_purposes"]
        }
        self.assertEqual(set(purposes), KEY_PURPOSES)
        self.assertEqual(purposes["provider_credential"], "active")
        self.assertEqual(purposes["data_encryption"], "active")
        self.assertEqual(purposes["session_material"], "active")
        self.assertEqual(purposes["identity_connector_secret"], "reserved")
        self.assertEqual(purposes["approval_fingerprint"], "reserved")

    def test_missing_or_duplicate_inventory_entry_fails_closed(self) -> None:
        missing = copy.deepcopy(self.inventory)
        missing["environment_reads"].pop()
        with self.assertRaises(SecretInventoryError) as raised:
            validate_secret_inventory(missing, source_root=ROOT)
        self.assertEqual(raised.exception.code, "secret_inventory_environment_read_mismatch")

        duplicate = copy.deepcopy(self.inventory)
        duplicate["ambient_credential_reads"].append(copy.deepcopy(duplicate["environment_reads"][0]))
        with self.assertRaises(SecretInventoryError) as raised:
            validate_secret_inventory(duplicate, source_root=ROOT)
        self.assertEqual(raised.exception.code, "secret_inventory_duplicate_id")

    def test_local_session_custody_does_not_relax_provider_envelope_requirements(self) -> None:
        candidate = copy.deepcopy(self.inventory)
        provider = next(item for item in candidate["managed_materials"] if item["id"] == "provider-credential-envelope")
        provider["custody_mode"] = "keyed_hash"
        with self.assertRaises(SecretInventoryError) as raised:
            validate_secret_inventory(candidate, source_root=ROOT)
        self.assertEqual(raised.exception.code, "secret_inventory_managed_custody_invalid")

    def test_invitation_handoff_is_restricted_to_its_operator_writer(self) -> None:
        candidate = copy.deepcopy(self.inventory)
        candidate["environment_reads"][1]["custody_mode"] = "private_invitation_handoff"
        with self.assertRaisesRegex(SecretInventoryError, "secret_inventory_secret_custody_invalid"):
            validate_secret_inventory(candidate, source_root=ROOT)
        candidate = copy.deepcopy(self.inventory)
        handoff = next(item for item in candidate["managed_materials"] if item["id"] == "team-invitation-handoff")
        handoff["source_module"] = "hormuz/session_store.py"
        handoff["source_qualname"] = "SQLiteSessionStore._digest"
        with self.assertRaisesRegex(SecretInventoryError, "secret_inventory_managed_custody_invalid"):
            validate_secret_inventory(candidate, source_root=ROOT)

    def test_console_cookie_custody_cannot_be_reused_for_other_secret_sources(self) -> None:
        for field, value in (("source_qualname", "_cookie"), ("storage_owner", "customer_filesystem"),
                             ("runtime_consumer", "gateway_runtime"), ("rotation_authority", "identity_operator"),
                             ("key_purpose", "provider_credential")):
            candidate = copy.deepcopy(self.inventory)
            entry = next(item for item in candidate["managed_materials"] if item["id"] == "console-browser-cookies")
            entry[field] = value
            with self.subTest(field=field), self.assertRaises(SecretInventoryError):
                validate_secret_inventory(candidate, source_root=ROOT)
        candidate = copy.deepcopy(self.inventory)
        candidate["environment_reads"][1]["custody_mode"] = "browser_http_only_cookie"
        with self.assertRaisesRegex(SecretInventoryError, "secret_inventory_secret_custody_invalid"):
            validate_secret_inventory(candidate, source_root=ROOT)

    def test_new_environment_read_requires_inventory_review(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copytree(ROOT / "hormuz", root / "hormuz")
            (root / "hormuz" / "unreviewed_secret.py").write_text(
                "import os\n\ndef read_secret():\n    runtime_env = os.environ\n"
                "    return runtime_env.pop('UNREVIEWED_SECRET', '')\n",
                encoding="utf-8",
            )
            with self.assertRaises(SecretInventoryError) as raised:
                validate_secret_inventory(self.inventory, source_root=root)
        self.assertEqual(raised.exception.code, "secret_inventory_environment_read_mismatch")

    def test_bulk_environment_access_requires_inventory_review(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copytree(ROOT / "hormuz", root / "hormuz")
            (root / "hormuz" / "bulk_environment.py").write_text(
                "import os\n\ndef read_environment():\n    return dict(os.environ)\n",
                encoding="utf-8",
            )
            with self.assertRaises(SecretInventoryError) as raised:
                validate_secret_inventory(self.inventory, source_root=root)
        self.assertEqual(raised.exception.code, "secret_inventory_environment_read_mismatch")

    def test_duplicate_read_at_an_existing_coordinate_requires_review(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copytree(ROOT / "hormuz", root / "hormuz")
            cli_path = root / "hormuz" / "cli.py"
            source = cli_path.read_text(encoding="utf-8")
            original = 'default=os.environ.get("HORMUZ_CONFIG", "hormuz.json"),'
            replacement = (
                'default=(os.environ.get("HORMUZ_CONFIG", "hormuz.json"), '
                'os.environ.get("HORMUZ_CONFIG", "hormuz.json"))[0],'
            )
            self.assertIn(original, source)
            cli_path.write_text(source.replace(original, replacement, 1), encoding="utf-8")
            with self.assertRaises(SecretInventoryError) as raised:
                validate_secret_inventory(self.inventory, source_root=root)
        self.assertEqual(raised.exception.code, "secret_inventory_environment_read_mismatch")

    def test_unknown_purpose_and_custody_mode_fail_closed(self) -> None:
        unknown_purpose = copy.deepcopy(self.inventory)
        unknown_purpose["key_purposes"][0]["key_purpose"] = "unreviewed_secret_purpose"
        with self.assertRaises(SecretInventoryError) as raised:
            validate_secret_inventory(unknown_purpose, source_root=ROOT)
        self.assertEqual(raised.exception.code, "secret_inventory_key_purpose_invalid")

        unknown_mode = copy.deepcopy(self.inventory)
        unknown_mode["environment_reads"][1]["custody_mode"] = "plaintext_file"
        with self.assertRaises(SecretInventoryError) as raised:
            validate_secret_inventory(unknown_mode, source_root=ROOT)
        self.assertEqual(raised.exception.code, "secret_inventory_custody_mode_invalid")

    def test_secret_value_field_is_structurally_rejected_without_reflection(self) -> None:
        candidate = copy.deepcopy(self.inventory)
        synthetic_secret = "sk-proj-must-never-appear-in-the-error"
        candidate["environment_reads"][1]["secret_value"] = synthetic_secret
        with self.assertRaises(SecretInventoryError) as raised:
            validate_secret_inventory(candidate, source_root=ROOT)
        self.assertEqual(raised.exception.code, "secret_inventory_entry_shape_invalid")
        self.assertNotIn(synthetic_secret, str(raised.exception))

    def test_duplicate_json_member_fails_before_source_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "inventory.json"
            path.write_text(
                '{"schema_id":"hormuz.secret-inventory","schema_id":"duplicate"}',
                encoding="utf-8",
            )
            with self.assertRaises(SecretInventoryError) as raised:
                load_secret_inventory(path, source_root=ROOT)
        self.assertEqual(raised.exception.code, "secret_inventory_json_duplicate_key")


if __name__ == "__main__":
    unittest.main()
