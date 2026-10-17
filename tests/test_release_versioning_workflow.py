"""Maintainer version commands against disposable repository metadata."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest


TOOL = Path(__file__).resolve().parents[1] / "tools" / "version.py"


class ReleaseVersioningWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="hormuz-version-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.files = {
            "pyproject.toml": '[project]\nname = "hormuz"\nversion = "2.0.0a2"\n\n[tool.fixture]\nversion = "2.0.0a2"\n',
            "hormuz/__init__.py": '# 2.0.0a2 is also mentioned in a comment.\n__version__ = "2.0.0a2"\nraise RuntimeError("metadata must not execute this file")\n',
            "plugin.json": '{"name": "hormuz", "version": "0.2.1", "extensions": {"fixture": {"version": "keep"}}}\n',
            ".claude-plugin/plugin.json": '{"name": "hormuz", "version": "0.2.1", "description": "Keep this description"}\n',
            "gemini-extension.json": '{"name": "hormuz", "version": "0.2.1", "contextFileName": "skills/hormuz/SKILL.md"}\n',
            "contracts.json": '{"schema_version": 1, "client_version": "0.160.0"}\n',
        }
        for name, content in self.files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)

    def snapshot(self):
        return {name: (self.root / name).read_bytes() if (self.root / name).exists() else None
                for name in self.files}

    def restore(self):
        for name, content in self.files.items():
            (self.root / name).write_text(content)

    def command(self, *arguments, expected=0):
        result = subprocess.run(
            [sys.executable, str(TOOL), "--root", str(self.root), *arguments],
            cwd=self.root.parent, text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return json.loads(result.stdout) if expected == 0 else result

    def test_check_reports_both_release_streams_without_writing(self):
        before = self.snapshot()
        self.assertEqual(self.command("check"),
                         {"runtime": "2.0.0a2", "plugin": "0.2.1", "status": "consistent"})
        self.assertEqual(self.snapshot(), before)

    def test_wildcard_reexports_cannot_hide_runtime_version_drift(self):
        self.files["version_provider.py"] = '__all__ = ["__version__"]\n__version__ = "9.0.0"\n'
        (self.root / "version_provider.py").write_text(self.files["version_provider.py"])
        (self.root / "hormuz/__init__.py").write_text(
            '__version__ = "2.0.0a2"\nfrom version_provider import *\n')
        imported = subprocess.run(
            [sys.executable, "-c", "import hormuz; print(hormuz.__version__)"],
            cwd=self.root, text=True, capture_output=True, check=True, timeout=10)
        self.assertEqual(imported.stdout.strip(), "9.0.0")
        before = self.snapshot()
        for arguments in (("check",), ("bump", "runtime", "prerelease"), ("bump", "plugin", "patch")):
            with self.subTest(arguments=arguments):
                self.command(*arguments, expected=2)
                self.assertEqual(self.snapshot(), before)

    def test_runtime_bump_updates_its_mirror_and_preserves_unrelated_content(self):
        before = self.snapshot()
        self.assertEqual(self.command("bump", "runtime", "prerelease"),
                         {"component": "runtime", "from": "2.0.0a2", "to": "2.0.0a3",
                          "dry_run": False, "files": ["pyproject.toml", "hormuz/__init__.py"]})
        self.assertEqual((self.root / "pyproject.toml").read_text(),
                         '[project]\nname = "hormuz"\nversion = "2.0.0a3"\n\n[tool.fixture]\nversion = "2.0.0a2"\n')
        self.assertEqual((self.root / "hormuz/__init__.py").read_text(),
                         '# 2.0.0a2 is also mentioned in a comment.\n__version__ = "2.0.0a3"\nraise RuntimeError("metadata must not execute this file")\n')
        for name in ("plugin.json", ".claude-plugin/plugin.json", "gemini-extension.json", "contracts.json"):
            self.assertEqual((self.root / name).read_bytes(), before[name])
        self.assertEqual(self.command("check")["runtime"], "2.0.0a3")

    def test_plugin_dry_run_and_numeric_bumps_preserve_runtime_and_manifest_fields(self):
        before = self.snapshot()
        preview = self.command("bump", "plugin", "patch", "--dry-run")
        self.assertEqual(preview, {"component": "plugin", "from": "0.2.1", "to": "0.2.2",
                                  "dry_run": True, "files": ["plugin.json", ".claude-plugin/plugin.json", "gemini-extension.json"]})
        self.assertEqual(self.snapshot(), before)
        for part, expected in (("patch", "0.2.2"), ("minor", "0.3.0"), ("major", "1.0.0")):
            with self.subTest(part=part):
                report = self.command("bump", "plugin", part)
                self.assertEqual(report["to"], expected)
                self.assertFalse(report["dry_run"])
                self.assertEqual(self.command("check")["plugin"], expected)
                self.assertEqual(json.loads((self.root / "plugin.json").read_text()),
                                 {"name": "hormuz", "version": expected, "extensions": {"fixture": {"version": "keep"}}})
                self.assertEqual(json.loads((self.root / ".claude-plugin/plugin.json").read_text()),
                                 {"name": "hormuz", "version": expected, "description": "Keep this description"})
                self.assertEqual(json.loads((self.root / "gemini-extension.json").read_text()),
                                 {"name": "hormuz", "version": expected, "contextFileName": "skills/hormuz/SKILL.md"})
        for name in ("pyproject.toml", "hormuz/__init__.py", "contracts.json"):
            self.assertEqual((self.root / name).read_bytes(), before[name])

    def test_runtime_prereleases_promote_and_finalize_before_the_next_release(self):
        before = self.snapshot()
        preview = self.command("bump", "runtime", "prerelease", "--pre", "beta", "--dry-run")
        self.assertEqual(preview["to"], "2.0.0b1")
        self.assertEqual(self.snapshot(), before)
        examples = (
            (("prerelease", "--pre", "alpha"), "2.0.0a3"),
            (("prerelease", "--pre", "beta"), "2.0.0b1"),
            (("prerelease",), "2.0.0b2"),
            (("prerelease", "--pre", "rc"), "2.0.0rc1"),
            (("final",), "2.0.0"),
            (("patch", "--pre", "alpha"), "2.0.1a1"),
            (("final",), "2.0.1"),
            (("minor", "--pre", "beta"), "2.1.0b1"),
            (("final",), "2.1.0"),
            (("major",), "3.0.0"),
        )
        for arguments, expected in examples:
            with self.subTest(arguments=arguments):
                self.assertEqual(self.command("bump", "runtime", *arguments)["to"], expected)
                self.assertEqual(self.command("check")["runtime"], expected)

    def test_unsupported_transitions_leave_all_metadata_unchanged(self):
        self.command("bump", "runtime", "prerelease", "--pre", "beta")
        arguments = (
            ("runtime", "prerelease", "--pre", "alpha"),
            ("runtime", "final", "--pre", "rc"),
            ("plugin", "prerelease"), ("plugin", "final"),
            ("plugin", "patch", "--pre", "alpha"),
        )
        for operation in arguments:
            with self.subTest(operation=operation):
                before = self.snapshot()
                self.command("bump", *operation, expected=2)
                self.assertEqual(self.snapshot(), before)
        self.command("bump", "runtime", "final")
        for part in ("prerelease", "final"):
            before = self.snapshot()
            self.command("bump", "runtime", part, expected=2)
            self.assertEqual(self.snapshot(), before)

    def test_noncanonical_versions_fail_check_and_bump_without_writes(self):
        examples = (
            ("runtime", "02.0.0a2"), ("runtime", "2.0.0a02"),
            ("runtime", "2.0.0.post1"), ("runtime", "2.0.0.dev1"),
            ("runtime", "1!2.0.0"), ("runtime", "2.0.0+local"),
            ("runtime", "2.0.0-alpha.2"), ("runtime", "٢.0.0a2"),
            ("runtime", True), ("plugin", "0.2.1rc1"),
            ("plugin", "00.2.1"), ("plugin", 2.1),
        )
        for component, value in examples:
            with self.subTest(component=component, value=value):
                self.restore()
                if component == "runtime":
                    (self.root / "pyproject.toml").write_text('[project]\nversion = ' + json.dumps(value) + '\n')
                    (self.root / "hormuz/__init__.py").write_text('__version__ = ' + repr(value) + '\n')
                else:
                    for name in ("plugin.json", ".claude-plugin/plugin.json", "gemini-extension.json"):
                        (self.root / name).write_text(json.dumps({"name": "hormuz", "version": value}))
                before = self.snapshot()
                for arguments in (("check",), ("bump", "plugin", "patch")):
                    failure = self.command(*arguments, expected=2)
                    self.assertIn("version:", failure.stderr)
                    self.assertNotIn("Traceback", failure.stderr)
                    self.assertEqual(self.snapshot(), before)

    def test_drift_and_malformed_or_missing_metadata_are_rejected_before_any_write(self):
        examples = (
            ("hormuz/__init__.py", '__version__ = "2.0.0a3"\n'),
            ("hormuz/__init__.py", '__version__ = "2.0.0a2"\n__version__ = "2.0.0a2"\n'),
            ("hormuz/__init__.py", '__version__ = "2.0.0a2"\n__version__: str = "9.0.0"\n'),
            ("hormuz/__init__.py", '__version__ = "2.0.0a2"\n__version__ += ".post1"\n'),
            ("hormuz/__init__.py", '__version__ = "2.0.0a2"\nif True:\n    __version__ = "9.0.0"\n'),
            ("hormuz/__init__.py", '__version__ = "2.0.0a2"\ndel __version__\n'),
            ("hormuz/__init__.py", '__version__ = "2.0.0a2"\nimport sys as __version__\n'),
            ("hormuz/__init__.py", '__version__ = "2.0.0a2"\nfrom sys import version as __version__\n'),
            ("hormuz/__init__.py", '__version__ = "2.0.0a2"\ndef __version__():\n    return "9.0.0"\n'),
            ("hormuz/__init__.py", '__version__ = "2.0.0a2"\nclass __version__:\n    pass\n'),
            ("hormuz/__init__.py", '__version__ = str("2.0.0a2")\n'),
            ("pyproject.toml", '[project\nversion = "broken"'),
            ("gemini-extension.json", '{"name":"hormuz","version":"0.2.2"}'),
            ("gemini-extension.json", '{"name":"hormuz"}'),
            ("gemini-extension.json", '{bad json'),
            ("gemini-extension.json", None),
        )
        for name, content in examples:
            with self.subTest(name=name, content=content):
                self.restore()
                if content is None:
                    (self.root / name).unlink()
                else:
                    (self.root / name).write_text(content)
                before = self.snapshot()
                for arguments in (("check",), ("bump", "runtime", "prerelease"), ("bump", "plugin", "patch")):
                    failure = self.command(*arguments, expected=2)
                    self.assertIn("version:", failure.stderr)
                    self.assertNotIn("Traceback", failure.stderr)
                    self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
