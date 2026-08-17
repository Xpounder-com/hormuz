import copy
import json
from pathlib import Path
import tempfile
import unittest

from scripts.client_lock_contract import (
    ClientLockContractError,
    validate_client_lock,
)


ROOT = Path(__file__).resolve().parents[1]


class ClientLockContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.package = json.loads(
            (ROOT / "deploy/clients/package.json").read_text()
        )
        self.lock = json.loads(
            (ROOT / "deploy/clients/package-lock.json").read_text()
        )

    def _validate(
        self,
        package: dict[str, object],
        lock: dict[str, object],
    ) -> dict[str, object]:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package_file = root / "package.json"
            lock_file = root / "package-lock.json"
            package_file.write_text(json.dumps(package))
            lock_file.write_text(json.dumps(lock))
            return validate_client_lock(package_file, lock_file)

    def test_tracked_client_lock_is_exact_and_content_free(self) -> None:
        evidence = validate_client_lock(
            ROOT / "deploy/clients/package.json",
            ROOT / "deploy/clients/package-lock.json",
        )

        self.assertEqual(evidence["schema"], "hormuz.pinned-client-lock.v1")
        self.assertEqual(evidence["node_version"], "24.19.0")
        self.assertEqual(evidence["npm_version"], "11.17.0")
        self.assertEqual(evidence["package_count"], 16)
        self.assertRegex(evidence["package_lock_sha256"], r"^[0-9a-f]{64}$")
        serialized = json.dumps(evidence)
        self.assertNotIn(str(ROOT), serialized)
        self.assertNotIn("prompt", serialized.lower())
        self.assertNotIn("credential", serialized.lower())

    def test_client_lock_rejects_dependency_and_registry_drift(self) -> None:
        cases: list[tuple[str, dict[str, object], dict[str, object]]] = []

        ranged_package = copy.deepcopy(self.package)
        ranged_package["dependencies"]["@openai/codex"] = "^0.147.0"
        cases.append(("ranged direct dependency", ranged_package, self.lock))

        extra_package_lock = copy.deepcopy(self.lock)
        extra_package_lock["packages"]["node_modules/unreviewed"] = {
            "version": "1.0.0",
            "resolved": "https://registry.npmjs.org/unreviewed/-/unreviewed-1.0.0.tgz",
            "integrity": "sha512-" + "A" * 86 + "==",
        }
        cases.append(("extra package", self.package, extra_package_lock))

        other_registry_lock = copy.deepcopy(self.lock)
        other_registry_lock["packages"]["node_modules/@openai/codex"][
            "resolved"
        ] = "https://packages.example/@openai/codex.tgz"
        cases.append(("other registry", self.package, other_registry_lock))

        for name, package, lock in cases:
            with self.subTest(name=name), self.assertRaises(ClientLockContractError):
                self._validate(package, lock)

    def test_client_lock_rejects_integrity_and_script_drift(self) -> None:
        bad_integrity_lock = copy.deepcopy(self.lock)
        bad_integrity_lock["packages"]["node_modules/@openai/codex"][
            "integrity"
        ] = "sha512-not-base64"
        with self.assertRaises(ClientLockContractError):
            self._validate(self.package, bad_integrity_lock)

        extra_script_lock = copy.deepcopy(self.lock)
        extra_script_lock["packages"]["node_modules/@openai/codex"][
            "hasInstallScript"
        ] = True
        with self.assertRaises(ClientLockContractError):
            self._validate(self.package, extra_script_lock)

        platform_integrity_lock = copy.deepcopy(self.lock)
        platform_integrity_lock["packages"][
            "node_modules/@anthropic-ai/claude-code-linux-x64"
        ]["integrity"] = "sha512-" + "A" * 86 + "=="
        with self.assertRaises(ClientLockContractError):
            self._validate(self.package, platform_integrity_lock)

        optional_edge_lock = copy.deepcopy(self.lock)
        del optional_edge_lock["packages"]["node_modules/@openai/codex"][
            "optionalDependencies"
        ]["@openai/codex-linux-x64"]
        with self.assertRaises(ClientLockContractError):
            self._validate(self.package, optional_edge_lock)


if __name__ == "__main__":
    unittest.main()
