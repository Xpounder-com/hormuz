"""One-click Mac builds require a dynamic production HTTPS origin."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "clients/macos/script/package_release.sh"


@unittest.skipIf(shutil.which("bash") is None, "bash unavailable")
class MacOSPackagingOriginTests(unittest.TestCase):
    def _validate(self, origin: str) -> str:
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                ["bash", str(SCRIPT), "--output-directory", directory,
                 "--desktop-origin", origin, "--ad-hoc"],
                capture_output=True, text=True, check=False,
            )
        self.assertNotEqual(result.returncode, 0)
        return result.stderr

    def test_pages_and_nonproduction_render_hosts_are_rejected(self):
        for origin in (
            "",
            "https://usehormuz.github.io",
            "https://hormuz-https-preflight.onrender.com",
            "https://hormuz-staging.onrender.com",
            "https://example.com",
            " https://hormuz-desktop.onrender.com",
        ):
            with self.subTest(origin=origin):
                self.assertIn("production HTTPS --desktop-origin is required", self._validate(origin))

    def test_dedicated_render_host_passes_origin_check(self):
        error = self._validate("https://hormuz-desktop.onrender.com")
        self.assertIn("--context-helper-directory must contain", error)
        self.assertNotIn("production HTTPS --desktop-origin is required", error)

    def test_signed_build_without_one_click_origin_keeps_existing_packaging_path(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                ["bash", str(SCRIPT), "--output-directory", directory],
                capture_output=True, text=True, check=False,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--context-helper-directory must contain", result.stderr)
        self.assertNotIn("production HTTPS --desktop-origin is required", result.stderr)


if __name__ == "__main__":
    unittest.main()
