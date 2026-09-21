"""Fail-closed checks for the combined #214 development transition runner."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile

from tools import run_v13_development_transition_matrix as matrix


class DevelopmentTransitionMatrixRunnerTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
