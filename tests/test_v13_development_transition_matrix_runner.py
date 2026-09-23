"""Fail-closed checks for the combined #214 development transition runner."""

from __future__ import annotations

from contextlib import redirect_stdout
import io
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock
import venv
import zipfile

from tools import run_v13_development_transition_matrix as matrix


class DevelopmentTransitionMatrixRunnerTests(unittest.TestCase):
    def test_every_selected_case_id_resolves_to_exactly_one_test(self):
        def cases(suite):
            for item in suite:
                if isinstance(item, unittest.TestSuite):
                    yield from cases(item)
                else:
                    yield item

        test_path = str(Path(__file__).resolve().parent)
        with mock.patch.object(sys, "path", [test_path, *sys.path]):
            for name in matrix.SQLITE_CASES + matrix.POSTGRES_CASES:
                with self.subTest(name=name):
                    resolved = list(cases(unittest.defaultTestLoader.loadTestsFromName(name)))
                    self.assertEqual([case.id() for case in resolved], [name])

    def test_v1_installed_runtime_must_match_pinned_archive_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = root / "hormuz-1.0.0.tar.gz"
            archived = {
                "__init__.py": b"__version__ = '1.0.0'\n",
                "module.py": b"value = 'released'\n",
            }
            with tarfile.open(archive_path, "w:gz") as archive:
                for name, contents in archived.items():
                    member = tarfile.TarInfo(f"hormuz-1.0.0/hormuz/{name}")
                    member.size = len(contents)
                    archive.addfile(member, io.BytesIO(contents))
            digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
            environment = root / "venv"
            venv.EnvBuilder(with_pip=False, symlinks=True).create(environment)
            python = environment / "bin/python"
            site = Path(subprocess.run(
                (str(python), "-I", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"),
                check=True, capture_output=True, text=True,
            ).stdout.strip())
            package = site / "hormuz"
            package.mkdir()
            for name, contents in archived.items():
                (package / name).write_bytes(contents)
            metadata = site / "hormuz-1.0.0.dist-info"
            metadata.mkdir()
            (metadata / "METADATA").write_text("Metadata-Version: 2.4\nName: hormuz\nVersion: 1.0.0\n")
            (metadata / "direct_url.json").write_text(json.dumps({
                "archive_info": {"hashes": {"sha256": digest}},
            }))
            matrix.verify_v1_installed_runtime(archive_path, python, digest)

            (package / "module.py").write_bytes(b"value = 'modified'\n")
            with self.assertRaisesRegex(matrix.MatrixRefusal, "v1_installed_runtime_mismatch"):
                matrix.verify_v1_installed_runtime(archive_path, python, digest)
            (package / "module.py").write_bytes(archived["module.py"])
            (package / "extra.py").write_bytes(b"extra = True\n")
            with self.assertRaisesRegex(matrix.MatrixRefusal, "v1_installed_runtime_mismatch"):
                matrix.verify_v1_installed_runtime(archive_path, python, digest)

    def test_candidate_wheel_must_match_every_source_runtime_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "hormuz"
            package.mkdir()
            (package / "__init__.py").write_bytes(b"version = 1\n")
            wheel = root / "candidate.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr("hormuz/__init__.py", b"version = 1\n")
            self.assertEqual(
                matrix.verify_candidate_runtime_pair(root, wheel),
                {"hormuz/__init__.py": b"version = 1\n"},
            )

            (package / "__init__.py").write_bytes(b"version = 2\n")
            with self.assertRaisesRegex(matrix.MatrixRefusal, "candidate_source_wheel_runtime_mismatch"):
                matrix.verify_candidate_runtime_pair(root, wheel)

            (package / "__init__.py").write_bytes(b"version = 1\n")
            (package / "new_runtime.py").write_bytes(b"new = True\n")
            with self.assertRaisesRegex(matrix.MatrixRefusal, "candidate_source_wheel_runtime_mismatch"):
                matrix.verify_candidate_runtime_pair(root, wheel)

    def test_missing_or_replaced_published_inputs_refuse_before_any_case(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "forged.tar.gz"
            path.write_bytes(b"not a published release")
            with self.assertRaisesRegex(matrix.MatrixRefusal, "v1_archive_digest_mismatch"):
                matrix.verify_published_artifacts({"v1_archive": path})
            with self.assertRaisesRegex(matrix.MatrixRefusal, "v1_archive_missing"):
                matrix.verify_published_artifacts({"v1_archive": path.parent / "absent"})

    def test_skipped_required_case_is_a_matrix_refusal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tests = root / "tests"
            tests.mkdir()
            (tests / "test_matrix_skip_probe.py").write_text(
                "import unittest\n"
                "class Probe(unittest.TestCase):\n"
                "    @unittest.skip('unavailable predecessor')\n"
                "    def test_required(self): pass\n",
                encoding="utf-8",
            )
            with mock.patch.object(
                matrix, "selected_cases",
                return_value=("test_matrix_skip_probe.Probe.test_required",),
            ):
                with self.assertRaises(matrix.MatrixRefusal) as caught:
                    matrix.run_cases(root, postgres=False)
            self.assertIn("test_matrix_skip_probe.Probe.test_required", str(caught.exception))
            self.assertIn("matrix_cases_skipped", str(caught.exception))

    def test_postgres_is_never_counted_by_sqlite_only_selection(self):
        sqlite = matrix.selected_cases(False)
        full = matrix.selected_cases(True)
        self.assertEqual(full[:len(sqlite)], sqlite)
        self.assertEqual(len(full) - len(sqlite), len(matrix.POSTGRES_CASES))
        self.assertTrue(all("sqlite" in case.lower() or "SQLite" in case for case in sqlite))
        self.assertTrue(all("postgres" in case.lower() or "Postgres" in case
                            for case in full[len(sqlite):]))

    def test_venv_python_symlink_must_not_be_resolved_to_base_interpreter(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = root / "base-python"
            base.write_bytes(b"test")
            venv = root / "venv-python"
            venv.symlink_to(base)
            self.assertEqual(venv.absolute(), venv)
            self.assertNotEqual(venv.resolve(), venv)

    def test_dirty_source_refuses_before_loading_predecessors_or_cases(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(("git", "init", "-q", str(root)), check=True)
            subprocess.run(("git", "-C", str(root), "config", "user.name", "Matrix test"), check=True)
            subprocess.run(("git", "-C", str(root), "config", "user.email", "matrix@example.invalid"), check=True)
            (root / "README.md").write_text("clean\n", encoding="utf-8")
            subprocess.run(("git", "-C", str(root), "add", "README.md"), check=True)
            subprocess.run(("git", "-C", str(root), "commit", "-qm", "baseline"), check=True)
            (root / "untracked.txt").write_text("dirty\n", encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                status = matrix.main([
                    "--mode", "source", "--source-root", str(root),
                    "--candidate-wheel", "missing.whl",
                    "--v1-archive", "missing-v1.tar.gz", "--v1-manifest", "missing.json",
                    "--v1-python", "missing-v1-python", "--v12-source", "missing-v12.tar.gz",
                    "--v12-wheel", "missing-v12.whl", "--v12-python", "missing-v12-python",
                ])
            self.assertEqual(status, 1)
            self.assertEqual(json.loads(output.getvalue()), {
                "status": "refused", "reason": "candidate_checkout_dirty",
            })


if __name__ == "__main__":
    unittest.main()
