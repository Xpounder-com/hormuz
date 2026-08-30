from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from recording_support import collect_output, verified_revision


class RecordingTests(unittest.TestCase):
    def test_preserves_real_output_exit_code_and_monotonic_timings(self):
        events, code, duration = collect_output(
            [sys.executable, "-u", "-c", "import sys,time; print('first'); time.sleep(.05); print('second'); sys.exit(3)"],
            cwd=Path.cwd(), timeout=5,
        )
        self.assertEqual(code, 3)
        self.assertEqual("".join(event[2] for event in events), "first\nsecond\n")
        self.assertTrue(0 <= events[0][0] <= events[1][0] <= duration)

    def test_silent_or_partial_output_cannot_bypass_timeout_and_child_is_reaped(self):
        real_popen = subprocess.Popen
        for prefix in ("", "print('partial', end='', flush=True); "):
            with self.subTest(prefix=prefix):
                children = []

                def launch(*args, **kwargs):
                    child = real_popen(*args, **kwargs)
                    children.append(child)
                    return child

                start = time.monotonic()
                with patch("recording_support.subprocess.Popen", side_effect=launch):
                    with self.assertRaises(subprocess.TimeoutExpired):
                        collect_output(
                            [sys.executable, "-u", "-c", "import time; " + prefix + "time.sleep(60)"],
                            cwd=Path.cwd(), timeout=.2,
                        )
                self.assertLess(time.monotonic() - start, 3)
                self.assertIsNotNone(children[0].returncode)

    def test_timeout_must_be_positive(self):
        with self.assertRaises(ValueError):
            collect_output([sys.executable], cwd=Path.cwd(), timeout=0)


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.git_env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
        self.git("init", "--quiet")
        (self.root / "hormuz").mkdir()
        (self.root / "hormuz/demo.py").write_text("# committed synthetic fixture\n")
        self.git("add", "hormuz/demo.py")
        self.git("-c", "user.name=Website Test", "-c", "user.email=website-test@example.invalid", "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "fixture")

    def git(self, *arguments):
        return subprocess.check_output(["git", *arguments], cwd=self.root, env=self.git_env, text=True).strip()

    def test_clean_runtime_and_non_runtime_edits_retain_head(self):
        (self.root / "README.md").write_text("website changes\n")
        self.git("add", "README.md")
        self.assertEqual(verified_revision(self.root), self.git("rev-parse", "HEAD"))

    def test_staged_and_unstaged_runtime_changes_are_rejected(self):
        (self.root / "hormuz/demo.py").write_text("# changed fixture\n")
        with self.assertRaises(subprocess.CalledProcessError):
            verified_revision(self.root)
        self.git("add", "hormuz/demo.py")
        with self.assertRaises(subprocess.CalledProcessError):
            verified_revision(self.root)

    def test_untracked_runtime_and_ignored_runtime_files_are_rejected(self):
        (self.root / "hormuz/untracked.py").write_text("# untracked fixture\n")
        with self.assertRaisesRegex(RuntimeError, "Untracked runtime"):
            verified_revision(self.root)
        (self.root / ".git/info/exclude").write_text("hormuz/untracked.py\n")
        with self.assertRaisesRegex(RuntimeError, "Untracked runtime"):
            verified_revision(self.root)

    def test_interpreter_cache_is_not_an_untracked_source_file(self):
        cache = self.root / "hormuz/__pycache__"
        cache.mkdir()
        (cache / "demo.pyc").write_bytes(b"synthetic cache fixture")
        self.assertEqual(verified_revision(self.root), self.git("rev-parse", "HEAD"))


if __name__ == "__main__":
    unittest.main()
