"""Source verification starts independently of installed tools packages."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest


TOOLS = Path(__file__).resolve().parents[1] / "tools"


class SourceVersionImportWorkflowTests(unittest.TestCase):
    def test_scope_reports_metadata_errors_with_an_unrelated_installed_tools_package(self):
        with tempfile.TemporaryDirectory(prefix="koto-source-import-") as directory:
            root = Path(directory)
            checkout = root / "checkout"
            (checkout / "tools").mkdir(parents=True)
            for name in ("version.py", "verify_router_scope.py"):
                shutil.copy2(TOOLS / name, checkout / "tools" / name)
            installed = root / "installed"
            (installed / "tools").mkdir(parents=True)
            (installed / "tools" / "__init__.py").write_text("", encoding="utf-8")
            unrelated = root / "unrelated"
            unrelated.mkdir()
            result = subprocess.run(
                [sys.executable, str(checkout / "tools" / "verify_router_scope.py")],
                cwd=unrelated,
                env={**os.environ, "PYTHONPATH": str(installed), "PYTHONDONTWRITEBYTECODE": "1"},
                capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertEqual(result.stdout, "")
            self.assertIn("Invalid release version metadata:", result.stderr)
            self.assertIn("pyproject.toml", result.stderr)
            self.assertNotIn("ModuleNotFoundError", result.stderr)
            self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
