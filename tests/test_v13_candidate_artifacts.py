from __future__ import annotations

import base64
import csv
import hashlib
import io
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
import zipfile

from tools import verify_v13_candidate_artifacts as candidate


class CandidateArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "checkout"
        self.root.mkdir()
        self.files = {
            path: (b"# transition\n" if path.endswith(".md") or path == "MANIFEST.in" else b"{}\n")
            for path in candidate.REQUIRED_SOURCE_KIT
        }
        self.files.update({
            "pyproject.toml": (
                b'[project]\nname = "hormuz"\nversion = "1.3.0"\n'
                b'[project.scripts]\nhormuz = "hormuz.cli:main"\n'
            ),
            "hormuz/__init__.py": b'__version__ = "1.3.0"\n',
            "hormuz/cli.py": b"def main():\n    return 0\n",
            "docs/example-wire-v1.json": b"{}\n",
            "tools/verify_example_transition_plan.py": b"# synthetic verifier\n",
            "tests/test_example_transition.py": b"# synthetic test\n",
        })
        for name, payload in self.files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        self._git("init", "-q")
        self._git("config", "user.name", "Candidate test")
        self._git("config", "user.email", "candidate@example.invalid")
        self._git("add", ".")
        self._git("commit", "-qm", "synthetic candidate")
        self.commit = self._git("rev-parse", "HEAD").strip()
        self.source = Path(self.temporary.name) / "hormuz-1.3.0.tar.gz"
        self.wheel = Path(self.temporary.name) / "hormuz-1.3.0-py3-none-any.whl"
        self._build_source()
        self._build_wheel()

    def _git(self, *arguments: str) -> str:
        return subprocess.run(
            ("git", "-C", str(self.root), *arguments), check=True,
            capture_output=True, text=True,
        ).stdout

    @staticmethod
    def _tar_member(archive: tarfile.TarFile, name: str, payload: bytes) -> None:
        member = tarfile.TarInfo(name)
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))

    def _build_source(self, *, edits: dict[str, bytes] | None = None, omitted: set[str] = frozenset()) -> None:
        files = dict(self.files)
        files["PKG-INFO"] = b"Metadata-Version: 2.4\nName: hormuz\nVersion: 1.3.0\n"
        files.update(edits or {})
        with tarfile.open(self.source, "w:gz") as archive:
            for name, payload in sorted(files.items()):
                if name not in omitted:
                    self._tar_member(archive, f"hormuz-1.3.0/{name}", payload)

    def _build_wheel(self, *, edits: dict[str, bytes] | None = None, bad_record: bool = False) -> None:
        files = {name: payload for name, payload in self.files.items() if name.startswith("hormuz/")}
        files.update({
            "hormuz-1.3.0.dist-info/METADATA": b"Metadata-Version: 2.4\nName: hormuz\nVersion: 1.3.0\n",
            "hormuz-1.3.0.dist-info/WHEEL": b"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
            "hormuz-1.3.0.dist-info/entry_points.txt": b"[console_scripts]\nhormuz = hormuz.cli:main\n",
        })
        files.update(edits or {})
        record = io.StringIO()
        writer = csv.writer(record, lineterminator="\n")
        for name, payload in sorted(files.items()):
            digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=").decode("ascii")
            if bad_record and name == "hormuz/cli.py":
                digest = "x" * len(digest)
            writer.writerow((name, f"sha256={digest}", str(len(payload))))
        record_name = "hormuz-1.3.0.dist-info/RECORD"
        writer.writerow((record_name, "", ""))
        files[record_name] = record.getvalue().encode("utf-8")
        with zipfile.ZipFile(self.wheel, "w") as archive:
            for name, payload in files.items():
                archive.writestr(name, payload)

    def test_exact_git_source_and_wheel_bytes_have_only_static_scope(self) -> None:
        result = candidate.verify_candidate(self.root, self.commit, self.source, self.wheel)
        self.assertEqual(result["candidate_commit"], self.commit)
        self.assertEqual(result["candidate_version"], "1.3.0")
        self.assertEqual(result["runtime_files_verified"], 2)
        self.assertEqual(result["proof_scope"], "git_runtime_and_transition_kit_source_wheel_byte_identity_only")
        self.assertIs(result["final_candidate_accepted"], False)

    def test_commit_and_version_must_be_exact(self) -> None:
        with self.assertRaisesRegex(candidate.CandidateArtifactError, "candidate_commit_not_head"):
            candidate.verify_candidate(self.root, "f" * 40, self.source, self.wheel)
        (self.root / "pyproject.toml").write_text(
            '[project]\nname = "hormuz"\nversion = "1.2.0"\n'
            '[project.scripts]\nhormuz = "hormuz.cli:main"\n', encoding="utf-8",
        )
        self._git("add", "pyproject.toml")
        self._git("commit", "-qm", "stale version")
        stale_commit = self._git("rev-parse", "HEAD").strip()
        with self.assertRaisesRegex(candidate.CandidateArtifactError, "candidate_version_mismatch"):
            candidate.verify_candidate(self.root, stale_commit, self.source, self.wheel)

    def test_missing_or_tampered_transition_kit_fails(self) -> None:
        missing = "docs/finance-transition-plan-v8.json"
        self._build_source(omitted={missing})
        with self.assertRaisesRegex(candidate.CandidateArtifactError, "candidate_source_git_bytes_mismatch"):
            candidate.verify_candidate(self.root, self.commit, self.source, self.wheel)
        self._build_source(edits={missing: b'{"changed": true}\n'})
        with self.assertRaisesRegex(candidate.CandidateArtifactError, "candidate_source_git_bytes_mismatch"):
            candidate.verify_candidate(self.root, self.commit, self.source, self.wheel)

    def test_untracked_runtime_and_stale_metadata_fail(self) -> None:
        self._build_source(edits={"hormuz/untracked.py": b"pass\n"})
        with self.assertRaisesRegex(candidate.CandidateArtifactError, "candidate_source_extra_selected_file"):
            candidate.verify_candidate(self.root, self.commit, self.source, self.wheel)
        self._build_source(edits={"setup.py": b"raise SystemExit(1)\n"})
        with self.assertRaisesRegex(candidate.CandidateArtifactError, "candidate_source_extra_selected_file"):
            candidate.verify_candidate(self.root, self.commit, self.source, self.wheel)
        self._build_source(edits={"PKG-INFO": b"Name: hormuz\nVersion: 1.2.0\n"})
        with self.assertRaisesRegex(candidate.CandidateArtifactError, "candidate_source_metadata_version_mismatch"):
            candidate.verify_candidate(self.root, self.commit, self.source, self.wheel)

    def test_wheel_bytes_entrypoint_and_record_fail_closed(self) -> None:
        self._build_wheel(edits={"hormuz/cli.py": b"def main():\n    return 1\n"})
        with self.assertRaisesRegex(candidate.CandidateArtifactError, "candidate_wheel_git_bytes_mismatch"):
            candidate.verify_candidate(self.root, self.commit, self.source, self.wheel)
        self._build_wheel(edits={"hormuz-1.3.0.dist-info/entry_points.txt": b"[console_scripts]\nhormuz = other:main\n"})
        with self.assertRaisesRegex(candidate.CandidateArtifactError, "candidate_wheel_entry_points_mismatch"):
            candidate.verify_candidate(self.root, self.commit, self.source, self.wheel)
        self._build_wheel(edits={"hormuz-1.3.0.dist-info/WHEEL": b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py2-none-any\n"})
        with self.assertRaisesRegex(candidate.CandidateArtifactError, "candidate_wheel_tag_mismatch"):
            candidate.verify_candidate(self.root, self.commit, self.source, self.wheel)
        self._build_wheel(bad_record=True)
        with self.assertRaisesRegex(candidate.CandidateArtifactError, "candidate_wheel_record_digest_mismatch"):
            candidate.verify_candidate(self.root, self.commit, self.source, self.wheel)

    def test_missing_artifacts_fail(self) -> None:
        with self.assertRaisesRegex(candidate.CandidateArtifactError, "candidate_source_missing_or_misnamed"):
            candidate.verify_candidate(self.root, self.commit, self.source.with_name("missing.tar.gz"), self.wheel)
        with self.assertRaisesRegex(candidate.CandidateArtifactError, "candidate_wheel_missing_or_misnamed"):
            candidate.verify_candidate(self.root, self.commit, self.source, self.wheel.with_name("missing.whl"))


if __name__ == "__main__":
    unittest.main()
