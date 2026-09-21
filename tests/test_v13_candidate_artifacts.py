from __future__ import annotations

import base64
from contextlib import redirect_stdout
import csv
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch
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
                b'description = "Test gateway"\nlicense = "Apache-2.0"\nrequires-python = ">=3.11"\n'
                b'dependencies = ["PyJWT[crypto]>=2.13,<3"]\n'
                b'[project.optional-dependencies]\npostgres = ["psycopg[binary]>=3.3.4,<3.4"]\n'
                b'[project.scripts]\nhormuz = "hormuz.cli:main"\n'
            ),
            "README.md": b"# Test gateway\n",
            "LICENSE": b"Synthetic license\n",
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

    @staticmethod
    def _metadata() -> bytes:
        return (
            b"Metadata-Version: 2.4\nName: hormuz\nVersion: 1.3.0\n"
            b"Summary: Test gateway\nLicense-Expression: Apache-2.0\n"
            b"Requires-Python: >=3.11\nDescription-Content-Type: text/markdown\n"
            b"License-File: LICENSE\nRequires-Dist: PyJWT[crypto]<3,>=2.13\n"
            b"Provides-Extra: postgres\n"
            b'Requires-Dist: psycopg[binary]<3.4,>=3.3.4; extra == "postgres"\n\n'
            b"# Test gateway\n"
        )

    def _build_source(
        self, *, edits: dict[str, bytes] | None = None,
        omitted: set[str] = frozenset(), colliding_first: tuple[str, bytes] | None = None,
    ) -> None:
        files = dict(self.files)
        files.update({
            "PKG-INFO": self._metadata(),
            "setup.cfg": candidate.GENERATED_SETUP_CFG,
            "hormuz.egg-info/PKG-INFO": self._metadata(),
            "hormuz.egg-info/dependency_links.txt": b"\n",
            "hormuz.egg-info/entry_points.txt": b"[console_scripts]\nhormuz = hormuz.cli:main\n",
            "hormuz.egg-info/requires.txt": (
                b"PyJWT[crypto]<3,>=2.13\n\n[postgres]\npsycopg[binary]<3.4,>=3.3.4\n"
            ),
            "hormuz.egg-info/top_level.txt": b"hormuz\n",
        })
        files.update(edits or {})
        listed = (set(files) - omitted) | {"hormuz.egg-info/SOURCES.txt"}
        files["hormuz.egg-info/SOURCES.txt"] = (
            "\n".join(sorted(listed - {"PKG-INFO", "setup.cfg"})) + "\n"
        ).encode("utf-8")
        if edits and "hormuz.egg-info/SOURCES.txt" in edits:
            files["hormuz.egg-info/SOURCES.txt"] = edits["hormuz.egg-info/SOURCES.txt"]
        with tarfile.open(self.source, "w:gz") as archive:
            if colliding_first:
                self._tar_member(archive, f"hormuz-1.3.0/{colliding_first[0]}", colliding_first[1])
            for name, payload in sorted(files.items()):
                if name not in omitted:
                    self._tar_member(archive, f"hormuz-1.3.0/{name}", payload)

    def _build_wheel(self, *, edits: dict[str, bytes] | None = None, bad_record: bool = False) -> None:
        files = {name: payload for name, payload in self.files.items() if name.startswith("hormuz/")}
        files.update({
            "hormuz-1.3.0.dist-info/METADATA": self._metadata(),
            "hormuz-1.3.0.dist-info/WHEEL": b"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
            "hormuz-1.3.0.dist-info/entry_points.txt": b"[console_scripts]\nhormuz = hormuz.cli:main\n",
            "hormuz-1.3.0.dist-info/licenses/LICENSE": self.files["LICENSE"],
            "hormuz-1.3.0.dist-info/top_level.txt": b"hormuz\n",
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
        self.assertEqual(result["proof_scope"], "git_source_archive_and_wheel_runtime_identity_only")
        self.assertIs(result["final_candidate_accepted"], False)

    def test_commit_and_version_must_be_exact(self) -> None:
        with self.assertRaisesRegex(candidate.CandidateArtifactError, "candidate_commit_not_head"):
            candidate.verify_candidate(self.root, "f" * 40, self.source, self.wheel)
        (self.root / "README.md").write_text("dirty checkout\n", encoding="utf-8")
        with self.assertRaisesRegex(candidate.CandidateArtifactError, "candidate_checkout_dirty"):
            candidate.verify_candidate(self.root, self.commit, self.source, self.wheel)
        (self.root / "README.md").write_bytes(self.files["README.md"])
        (self.root / "pyproject.toml").write_text(
            '[project]\nname = "hormuz"\nversion = "1.2.0"\n'
            'description = "Test gateway"\nlicense = "Apache-2.0"\nrequires-python = ">=3.11"\n'
            'dependencies = ["PyJWT[crypto]>=2.13,<3"]\n'
            '[project.optional-dependencies]\npostgres = ["psycopg[binary]>=3.3.4,<3.4"]\n'
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
        self._build_source(omitted={"tools/verify_finance_account_binding_preflight.py"})
        with self.assertRaisesRegex(candidate.CandidateArtifactError, "candidate_source_git_bytes_mismatch"):
            candidate.verify_candidate(self.root, self.commit, self.source, self.wheel)

    def test_untracked_runtime_and_stale_metadata_fail(self) -> None:
        self._build_source(edits={"hormuz/untracked.py": b"pass\n"})
        with self.assertRaisesRegex(candidate.CandidateArtifactError, "candidate_source_untracked_file"):
            candidate.verify_candidate(self.root, self.commit, self.source, self.wheel)
        self._build_source(edits={"setup.py": b"raise SystemExit(1)\n"})
        with self.assertRaisesRegex(candidate.CandidateArtifactError, "candidate_source_untracked_file"):
            candidate.verify_candidate(self.root, self.commit, self.source, self.wheel)
        self._build_source(edits={"PKG-INFO": b"Name: hormuz\nVersion: 1.2.0\n"})
        with self.assertRaisesRegex(candidate.CandidateArtifactError, "candidate_source_metadata_version_mismatch"):
            candidate.verify_candidate(self.root, self.commit, self.source, self.wheel)

    def test_build_active_source_files_and_generated_metadata_are_bound(self) -> None:
        for name in ("README.md", "LICENSE"):
            with self.subTest(name=name):
                self._build_source(edits={name: b"tampered\n"})
                with self.assertRaisesRegex(candidate.CandidateArtifactError, "candidate_source_git_bytes_mismatch"):
                    candidate.verify_candidate(self.root, self.commit, self.source, self.wheel)
        self._build_source(edits={"setup.cfg": b"[egg_info]\ntag_build = dev\n"})
        with self.assertRaisesRegex(candidate.CandidateArtifactError, "candidate_source_setup_cfg_mismatch"):
            candidate.verify_candidate(self.root, self.commit, self.source, self.wheel)
        self._build_source(edits={"hormuz.egg-info/PKG-INFO": b"Name: hormuz\nVersion: 1.2.0\n"})
        with self.assertRaisesRegex(candidate.CandidateArtifactError, "candidate_source_egg_metadata_mismatch"):
            candidate.verify_candidate(self.root, self.commit, self.source, self.wheel)
        self._build_source(edits={"hormuz.egg-info/requires.txt": b"PyJWT[crypto]>=2.0,<3\n"})
        with self.assertRaisesRegex(candidate.CandidateArtifactError, "candidate_source_egg_requirements_mismatch"):
            candidate.verify_candidate(self.root, self.commit, self.source, self.wheel)
        self._build_source(edits={"hormuz.egg-info/SOURCES.txt": b"README.md\n"})
        with self.assertRaisesRegex(candidate.CandidateArtifactError, "candidate_source_manifest_mismatch"):
            candidate.verify_candidate(self.root, self.commit, self.source, self.wheel)
        self._build_source(edits={"hormuz.egg-info/extra.txt": b"untracked\n"})
        with self.assertRaisesRegex(candidate.CandidateArtifactError, "candidate_source_untracked_file"):
            candidate.verify_candidate(self.root, self.commit, self.source, self.wheel)

    def test_dependency_tampering_fails_even_with_consistent_record(self) -> None:
        weakened = self._metadata().replace(b">=2.13", b">=2.0")
        self._build_wheel(edits={"hormuz-1.3.0.dist-info/METADATA": weakened})
        with self.assertRaisesRegex(candidate.CandidateArtifactError, "candidate_wheel_source_metadata_mismatch"):
            candidate.verify_candidate(self.root, self.commit, self.source, self.wheel)
        self._build_source(edits={
            "PKG-INFO": weakened,
            "hormuz.egg-info/PKG-INFO": weakened,
            "hormuz.egg-info/requires.txt": b"PyJWT[crypto]<3,>=2.0\n\n[postgres]\npsycopg[binary]<3.4,>=3.3.4\n",
        })
        self._build_wheel(edits={"hormuz-1.3.0.dist-info/METADATA": weakened})
        with self.assertRaisesRegex(candidate.CandidateArtifactError, "candidate_source_metadata_semantics_mismatch"):
            candidate.verify_candidate(self.root, self.commit, self.source, self.wheel)

    def test_archive_file_directory_collision_fails_before_extraction(self) -> None:
        self._build_source(colliding_first=("docs", b"file blocks docs directory\n"))
        with self.assertRaisesRegex(candidate.CandidateArtifactError, "candidate_source_file_directory_collision"):
            candidate.verify_candidate(self.root, self.commit, self.source, self.wheel)

    def test_wheel_bytes_entrypoint_and_record_fail_closed(self) -> None:
        self._build_wheel(edits={"hormuz/cli.py": b"def main():\n    return 1\n"})
        with self.assertRaisesRegex(candidate.CandidateArtifactError, "candidate_wheel_git_bytes_mismatch"):
            candidate.verify_candidate(self.root, self.commit, self.source, self.wheel)
        self._build_wheel(edits={"hormuz-1.3.0.dist-info/entry_points.txt": b"[console_scripts]\nhormuz = other:main\n"})
        with self.assertRaisesRegex(candidate.CandidateArtifactError, "candidate_entry_points_mismatch"):
            candidate.verify_candidate(self.root, self.commit, self.source, self.wheel)
        self._build_wheel(edits={"hormuz-1.3.0.dist-info/WHEEL": b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py2-none-any\n"})
        with self.assertRaisesRegex(candidate.CandidateArtifactError, "candidate_wheel_tag_mismatch"):
            candidate.verify_candidate(self.root, self.commit, self.source, self.wheel)
        self._build_wheel(edits={"hormuz-1.3.0.dist-info/licenses/LICENSE": b"changed license\n"})
        with self.assertRaisesRegex(candidate.CandidateArtifactError, "candidate_wheel_license_mismatch"):
            candidate.verify_candidate(self.root, self.commit, self.source, self.wheel)
        self._build_wheel(bad_record=True)
        with self.assertRaisesRegex(candidate.CandidateArtifactError, "candidate_wheel_record_digest_mismatch"):
            candidate.verify_candidate(self.root, self.commit, self.source, self.wheel)

    def test_encrypted_or_runtime_error_wheel_returns_fixed_failure(self) -> None:
        data = bytearray(self.wheel.read_bytes())
        for signature, flag_offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
            position = 0
            while (position := data.find(signature, position)) >= 0:
                flags = int.from_bytes(data[position + flag_offset:position + flag_offset + 2], "little")
                data[position + flag_offset:position + flag_offset + 2] = (flags | 1).to_bytes(2, "little")
                position += 4
        self.wheel.write_bytes(data)
        with self.assertRaisesRegex(candidate.CandidateArtifactError, "candidate_wheel_encrypted_entry"):
            candidate.verify_candidate(self.root, self.commit, self.source, self.wheel)

        self._build_wheel()
        original_read = zipfile.ZipFile.read

        def unreadable(archive: zipfile.ZipFile, name, *args, **kwargs):
            if str(name) == "hormuz/cli.py":
                raise RuntimeError("File is encrypted, password required")
            return original_read(archive, name, *args, **kwargs)

        with patch.object(zipfile.ZipFile, "read", unreadable):
            output = io.StringIO()
            with redirect_stdout(output):
                status = candidate.main([
                    "--repo-root", str(self.root), "--commit", self.commit,
                    "--source", str(self.source), "--wheel", str(self.wheel),
                ])
        self.assertEqual(status, 1)
        self.assertEqual(json.loads(output.getvalue()), {
            "status": "failed", "reason_code": "candidate_wheel_invalid",
        })

    def test_missing_artifacts_fail(self) -> None:
        with self.assertRaisesRegex(candidate.CandidateArtifactError, "candidate_source_missing_or_misnamed"):
            candidate.verify_candidate(self.root, self.commit, self.source.with_name("missing.tar.gz"), self.wheel)
        with self.assertRaisesRegex(candidate.CandidateArtifactError, "candidate_wheel_missing_or_misnamed"):
            candidate.verify_candidate(self.root, self.commit, self.source, self.wheel.with_name("missing.whl"))


if __name__ == "__main__":
    unittest.main()
