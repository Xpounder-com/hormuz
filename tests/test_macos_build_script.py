"""Exercise build-path and developer-directory setup before any native build."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "clients/macos/script/build_and_run.sh"
XCODE = Path("/Applications/Xcode.app/Contents/Developer")


@unittest.skipIf(sys.platform == "win32" or shutil.which("bash") is None, "requires Unix bash")
class MacOSBuildScriptTests(unittest.TestCase):
    def _cargo_setup(self, target: str, developer: str | None = None) -> tuple[dict, Path]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            cargo = root / "cargo-probe"
            cargo.write_text(
                f"#!{sys.executable}\n"
                "import json, os, sys\n"
                "print(json.dumps({'arguments': sys.argv[1:], 'developer': os.environ.get('DEVELOPER_DIR')}))\n"
                "raise SystemExit(77)\n"
            )
            cargo.chmod(0o700)
            environment = dict(os.environ, HORMUZ_CARGO=str(cargo), CARGO_TARGET_DIR=target)
            environment.pop("DEVELOPER_DIR", None)
            if developer is not None:
                environment["DEVELOPER_DIR"] = developer
            result = subprocess.run(["bash", str(SCRIPT), "--build-only"], cwd=root,
                                    env=environment, capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 77, result.stderr)
            return json.loads(result.stdout), root

    def test_relative_target_and_xcode_fallback_are_set_before_cargo(self) -> None:
        setup, root = self._cargo_setup("relative-target")
        self.assertEqual(setup["arguments"], ["build", "--target-dir", str(root / "relative-target"),
                                               "--locked", "--package", "hormuz-client-relay"])
        self.assertEqual(setup["developer"], str(XCODE) if XCODE.is_dir() else None)

    def test_absolute_target_and_explicit_developer_directory_are_preserved(self) -> None:
        setup, _ = self._cargo_setup("/private/tmp/synthetic-cargo-target", "/synthetic/Xcode/Developer")
        self.assertEqual(setup["arguments"][2], "/private/tmp/synthetic-cargo-target")
        self.assertEqual(setup["developer"], "/synthetic/Xcode/Developer")


if __name__ == "__main__":
    unittest.main()
