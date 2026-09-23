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
            "MANIFEST.in": (
                b"include SECURITY.md SUPPORT.md config.example.json\n"
                b"recursive-include docs *.md\n"
            ),
            "pyproject.toml": (
                b'[project]\nname = "hormuz"\nversion = "1.3.0"\n'
                b'description = "Test gateway"\nreadme = "README.md"\n'
                b'license = "Apache-2.0"\nlicense-files = ["LICENSE"]\n'
                b'requires-python = ">=3.11"\n'
                b'authors = [{name = "NeuralInt"}]\n'
                b'classifiers = ["Topic :: Security", "Environment :: Console"]\n'
                b'dependencies = ["PyJWT[crypto]>=2.13,<3"]\n'
                b'[project.optional-dependencies]\npostgres = ["psycopg[binary]>=3.3.4,<3.4"]\n'
                b'[project.scripts]\nhormuz = "hormuz.cli:main"\n'
            ),
            "README.md": b"# Test gateway\n",
            "LICENSE": b"Synthetic license\n",
            "SECURITY.md": b"# Synthetic security\n",
            "SUPPORT.md": b"# Synthetic support\n",
            "config.example.json": b"{}\n",
            "docs/additional-guide.md": b"# Included through MANIFEST\n",
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
            b"Summary: Test gateway\nAuthor: NeuralInt\n"
            b"License-Expression: Apache-2.0\n"
            b"Classifier: Topic :: Security\nClassifier: Environment :: Console\n"
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

    def _build_wheel(
        self, *, edits: dict[str, bytes] | None = None, bad_record: bool = False,
        file_modes: dict[str, int] | None = None,
    ) -> None:
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
                if file_modes and name in file_modes:
                    info = zipfile.ZipInfo(name)
                    info.create_system = 3
                    info.external_attr = (file_modes[name] | 0o644) << 16
                    archive.writestr(info, payload)
                else:
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

    def test_every_committed_manifest_include_is_required(self) -> None:
        for name in ("SECURITY.md", "SUPPORT.md", "config.example.json", "docs/additional-guide.md"):
            with self.subTest(name=name):
                self._build_source(omitted={name})
                with self.assertRaisesRegex(candidate.CandidateArtifactError, "candidate_source_git_bytes_mismatch"):
                    candidate.verify_candidate(self.root, self.commit, self.source, self.wheel)
        self._build_source()
        self.assertEqual(candidate.verify_candidate(self.root, self.commit, self.source, self.wheel)["candidate_commit"], self.commit)

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

    def test_author_and_classifiers_cannot_drift_together_in_source_and_wheel(self) -> None:
        for changed in (
            self._metadata().replace(b"Author: NeuralInt", b"Author: Other"),
            self._metadata().replace(b"Classifier: Topic :: Security\n", b""),
            self._metadata().replace(b"Summary: Test gateway", b"Summary: Test gateway\nAuthor-email: other@example.invalid"),
        ):
            with self.subTest(changed=hashlib.sha256(changed).hexdigest()[:8]):
                self._build_source(edits={
                    "PKG-INFO": changed,
                    "hormuz.egg-info/PKG-INFO": changed,
                })
                self._build_wheel(edits={"hormuz-1.3.0.dist-info/METADATA": changed})
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

    def test_wheel_version_and_file_directory_collisions_fail(self) -> None:
        self._build_wheel(edits={"hormuz-1.3.0.dist-info/METADATA/child": b"uninstallable\n"})
        with self.assertRaisesRegex(candidate.CandidateArtifactError, "candidate_wheel_file_directory_collision"):
            candidate.verify_candidate(self.root, self.commit, self.source, self.wheel)
        for wheel_header in (
            b"Generator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
            b"Wheel-Version: 999.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        ):
            with self.subTest(wheel_header=wheel_header[:25]):
                self._build_wheel(edits={"hormuz-1.3.0.dist-info/WHEEL": wheel_header})
                with self.assertRaisesRegex(candidate.CandidateArtifactError, "candidate_wheel_tag_mismatch"):
                    candidate.verify_candidate(self.root, self.commit, self.source, self.wheel)

    def test_wheel_nonregular_members_and_unbound_metadata_fail(self) -> None:
        metadata = "hormuz-1.3.0.dist-info/METADATA"
        for mode in (0o010000, 0o020000, 0o040000, 0o060000, 0o120000):
            with self.subTest(mode=oct(mode)):
                self._build_wheel(file_modes={metadata: mode})
                with self.assertRaisesRegex(candidate.CandidateArtifactError, "candidate_wheel_member_type_invalid"):
                    candidate.verify_candidate(self.root, self.commit, self.source, self.wheel)
        self._build_wheel(edits={"hormuz-1.3.0.dist-info/unknown.json": b"{}\n"})
        with self.assertRaisesRegex(candidate.CandidateArtifactError, "candidate_wheel_metadata_inventory_mismatch"):
            candidate.verify_candidate(self.root, self.commit, self.source, self.wheel)

    def test_returned_digests_identify_the_validated_snapshots(self) -> None:
        original_source = hashlib.sha256(self.source.read_bytes()).hexdigest()
        original_wheel = hashlib.sha256(self.wheel.read_bytes()).hexdigest()
        selected = candidate._wheel_selected

        def replace_paths_after_validation(*args, **kwargs):
            result = selected(*args, **kwargs)
            self.source.write_bytes(b"replaced source after validation")
            self.wheel.write_bytes(b"replaced wheel after validation")
            return result

        with patch.object(candidate, "_wheel_selected", replace_paths_after_validation):
            result = candidate.verify_candidate(self.root, self.commit, self.source, self.wheel)
        self.assertEqual(result["source_sha256"], original_source)
        self.assertEqual(result["wheel_sha256"], original_wheel)
        self.assertNotEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), original_source)
        self.assertNotEqual(hashlib.sha256(self.wheel.read_bytes()).hexdigest(), original_wheel)

    def test_archive_member_size_and_total_bounds_fail_closed(self) -> None:
        source_payload = self.source.read_bytes()
        with patch.object(candidate, "MAX_SOURCE_MEMBERS", 2):
            with self.assertRaisesRegex(candidate.CandidateArtifactError, "candidate_source_member_bounds"):
                candidate._source_files(source_payload)
        with patch.object(candidate, "MAX_SOURCE_TOTAL_BYTES", 8):
            with self.assertRaisesRegex(candidate.CandidateArtifactError, "candidate_source_total_bounds"):
                candidate._source_files(source_payload)
        with patch.object(candidate, "MAX_SOURCE_ARCHIVE_BYTES", 8):
            with self.assertRaisesRegex(candidate.CandidateArtifactError, "candidate_source_archive_bounds"):
                candidate.verify_candidate(self.root, self.commit, self.source, self.wheel)
        with patch.object(candidate, "MAX_WHEEL_MEMBERS", 2):
            with self.assertRaisesRegex(candidate.CandidateArtifactError, "candidate_wheel_member_bounds"):
                candidate.verify_candidate(self.root, self.commit, self.source, self.wheel)
        with patch.object(candidate, "MAX_WHEEL_TOTAL_BYTES", 8):
            with self.assertRaisesRegex(candidate.CandidateArtifactError, "candidate_wheel_total_bounds"):
                candidate.verify_candidate(self.root, self.commit, self.source, self.wheel)
        with patch.object(candidate, "MAX_WHEEL_ARCHIVE_BYTES", 8):
            with self.assertRaisesRegex(candidate.CandidateArtifactError, "candidate_wheel_archive_bounds"):
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
