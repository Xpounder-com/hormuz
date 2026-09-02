from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import subprocess
import tarfile
import tempfile
import textwrap
import tomllib
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from tools import v1_candidate, verify_core_wheel


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_FIXTURE = (
    ROOT
    / "tests"
    / "fixtures"
    / "v1_internal_repeatability"
    / "complete-synthetic-v1.json"
)


class V1CandidateTests(unittest.TestCase):
    def _publisher_namespace(self) -> dict[str, object]:
        workflow = (ROOT / ".github/workflows/freeze-v1-candidate.yml").read_text(
            encoding="utf-8"
        )
        start = "# BEGIN V1 CANDIDATE PUBLISHER"
        end = "# END V1 CANDIDATE PUBLISHER"
        self.assertEqual(workflow.count(start), 1)
        self.assertEqual(workflow.count(end), 1)
        source = workflow.split(start, 1)[1].split(end, 1)[0]
        namespace: dict[str, object] = {"__name__": "workflow_contract_test"}
        exec(compile(textwrap.dedent(source), "embedded-publisher.py", "exec"), namespace)
        return namespace

    def _publisher_credential_preflight_namespace(self) -> dict[str, object]:
        workflow = (ROOT / ".github/workflows/freeze-v1-candidate.yml").read_text(
            encoding="utf-8"
        )
        step_name = (
            "      - name: Authenticate the publisher credential before "
            "the one permitted build\n"
        )
        self.assertEqual(workflow.count(step_name), 1)
        step = workflow.split(step_name, 1)[1].split("\n  build:\n", 1)[0]
        source = step.split("/usr/bin/python3 -I -B - <<'PYTHON'\n", 1)[1].split(
            "\n          PYTHON",
            1,
        )[0]
        namespace: dict[str, object] = {"__name__": "workflow_contract_test"}
        exec(
            compile(
                textwrap.dedent(source),
                "embedded-publisher-credential-preflight.py",
                "exec",
            ),
            namespace,
        )
        return namespace

    def _archive(self, directory: Path, *, marker: str = "first") -> Path:
        path = directory / v1_candidate.ARCHIVE_NAME
        members = {
            "PKG-INFO": (
                "Metadata-Version: 2.4\n"
                "Name: hormuz\n"
                "Version: 1.0.0\n"
                "Summary: candidate fixture\n"
            ).encode(),
            "pyproject.toml": b'[project]\nname = "hormuz"\nversion = "1.0.0"\n',
        }
        for relative in v1_candidate.REQUIRED_ARCHIVE_PATHS:
            members.setdefault(relative, f"fixture:{relative}:{marker}\n".encode())
        with tarfile.open(path, "w:gz") as archive:
            for relative, payload in sorted(members.items()):
                info = tarfile.TarInfo(f"hormuz-1.0.0/{relative}")
                info.size = len(payload)
                info.mode = 0o644
                info.mtime = 1_700_000_000
                archive.addfile(info, io.BytesIO(payload))
        return path

    def _manifest_value(self, archive: Path) -> dict[str, object]:
        return v1_candidate.create_manifest(
            archive,
            source_commit="a" * 40,
            frozen_at="2025-08-27T10:00:00Z",
            invocation_url=(
                "https://github.com/Xpounder-com/hormuz/actions/runs/123/attempts/1"
            ),
            run_id=123,
            run_attempt=1,
        )

    def _write_manifest(self, directory: Path, value: dict[str, object]) -> Path:
        path = directory / v1_candidate.MANIFEST_NAME
        v1_candidate._write_new_private_json(path, value)
        return path

    def _evidence(self, directory: Path, manifest: dict[str, object]) -> Path:
        value = json.loads(
            EVIDENCE_FIXTURE.read_text(encoding="utf-8").replace(
                "2026-08-27", "2025-08-27"
            )
        )
        value["evidence_kind"] = "candidate_gate_evidence"
        candidate = manifest["candidate"]
        assert isinstance(candidate, dict)
        value["candidate"] = {
            "target_version": candidate["target_version"],
            "artifact_kind": candidate["artifact_kind"],
            "artifact_digest": candidate["artifact_digest"],
            "source_commit": candidate["source_commit"],
            "frozen_at": candidate["frozen_at"],
        }
        for run in value["runs"]:
            run["candidate_artifact_digest"] = candidate["artifact_digest"]
        path = directory / "real-evidence.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def _api_files(
        self,
        directory: Path,
        manifest_path: Path,
        manifest: dict[str, object],
        archive: Path,
    ) -> tuple[Path, Path]:
        candidate = manifest["candidate"]
        custody = manifest["custody"]
        assert isinstance(candidate, dict) and isinstance(custody, dict)
        manifest_payload = manifest_path.read_bytes()
        archive_payload = archive.read_bytes()
        release = {
            "tag_name": custody["release_tag"],
            "target_commitish": candidate["source_commit"],
            "draft": False,
            "prerelease": True,
            "immutable": True,
            # GitHub reports the tagged source commit date here, not the time
            # at which the release record was created or published.
            "created_at": "2025-08-27T09:50:00Z",
            "published_at": "2025-08-27T10:04:00Z",
            "assets": [
                {
                    "name": v1_candidate.ARCHIVE_NAME,
                    "state": "uploaded",
                    "size": len(archive_payload),
                    "digest": "sha256:" + hashlib.sha256(archive_payload).hexdigest(),
                    "created_at": "2025-08-27T10:02:00Z",
                    "updated_at": "2025-08-27T10:02:00Z",
                },
                {
                    "name": v1_candidate.MANIFEST_NAME,
                    "state": "uploaded",
                    "size": len(manifest_payload),
                    "digest": "sha256:" + hashlib.sha256(manifest_payload).hexdigest(),
                    "created_at": "2025-08-27T10:03:00Z",
                    "updated_at": "2025-08-27T10:03:00Z",
                },
            ],
        }
        release_path = directory / "candidate-release.json"
        immutable_path = directory / "immutable-settings.json"
        release_path.write_text(json.dumps(release), encoding="utf-8")
        immutable_path.write_text(
            json.dumps({"enabled": True, "enforced_by_owner": False}),
            encoding="utf-8",
        )
        return release_path, immutable_path

    def _final_release(
        self,
        directory: Path,
        manifest: dict[str, object],
        evidence: Path,
    ) -> tuple[Path, Path]:
        candidate = manifest["candidate"]
        assert isinstance(candidate, dict)
        evidence_digest = "sha256:" + hashlib.sha256(evidence.read_bytes()).hexdigest()
        release = {
            "tag_name": v1_candidate.FINAL_TAG,
            # GitHub documents target_commitish as unused when this protected
            # annotated tag already exists; the tag and exact body are binding.
            "target_commitish": "main",
            "name": v1_candidate.FINAL_RELEASE_TITLE,
            "body": v1_candidate._final_release_notes(manifest, evidence_digest),
            "draft": False,
            "prerelease": False,
            "immutable": True,
            "created_at": "2025-08-27T23:00:00Z",
            "published_at": "2025-08-27T23:00:01Z",
            "assets": [],
        }
        release_path = directory / "final-release.json"
        immutable_path = directory / "final-immutable-settings.json"
        release_path.write_text(json.dumps(release), encoding="utf-8")
        immutable_path.write_text(json.dumps({"enabled": True}), encoding="utf-8")
        return release_path, immutable_path

    def _freeze_run(self, directory: Path, manifest: dict[str, object]) -> Path:
        candidate = manifest["candidate"]
        build = manifest["build"]
        assert isinstance(candidate, dict) and isinstance(build, dict)
        path = directory / "freeze-run-api.json"
        path.write_text(
            json.dumps(
                {
                    "id": build["run_id"],
                    "name": "Freeze v1.0.0 candidate",
                    "path": ".github/workflows/freeze-v1-candidate.yml",
                    "event": "workflow_dispatch",
                    "head_branch": "main",
                    "head_sha": candidate["source_commit"],
                    "run_attempt": 1,
                    "status": "completed",
                    "conclusion": "success",
                    "html_url": str(build["invocation_url"]).removesuffix(
                        "/attempts/1"
                    ),
                    "created_at": "2025-08-27T09:55:00Z",
                    "run_started_at": "2025-08-27T09:56:00Z",
                    "updated_at": "2025-08-27T10:05:00Z",
                    "repository": {"full_name": "Xpounder-com/hormuz"},
                    "head_repository": {"full_name": "Xpounder-com/hormuz"},
                }
            ),
            encoding="utf-8",
        )
        return path

    def _custody_tag(self, directory: Path, manifest: dict[str, object]) -> Path:
        candidate = manifest["candidate"]
        custody = manifest["custody"]
        assert isinstance(candidate, dict) and isinstance(custody, dict)
        path = directory / "custody-tag-api.json"
        path.write_text(
            json.dumps(
                {
                    "ref": f"refs/tags/{custody['release_tag']}",
                    "object": {
                        "type": "commit",
                        "sha": candidate["source_commit"],
                    },
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_manifest_binds_one_complete_source_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = self._archive(root)
            manifest = self._manifest_value(archive)

            validated = v1_candidate.validate_manifest(manifest)
            candidate = validated["candidate"]
            custody = validated["custody"]
            build = validated["build"]
            self.assertEqual(candidate["artifact_name"], v1_candidate.ARCHIVE_NAME)
            self.assertEqual(
                candidate["artifact_digest"],
                v1_candidate.inspect_archive(archive)["digest"],
            )
            self.assertRegex(
                custody["release_tag"], r"^candidate-v1\.0\.0-[0-9a-f]{64}$"
            )
            self.assertEqual(
                custody["release_state"], "published_immutable_candidate"
            )
            self.assertEqual(build["archive_build_count"], 1)
            self.assertFalse(build["promotion_rebuild_permitted"])

    def test_manifest_rejects_rerun_attempt_and_unknown_field(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive = self._archive(Path(temporary))
            with self.assertRaisesRegex(
                v1_candidate.V1CandidateError, "build_invocation_invalid"
            ):
                v1_candidate.create_manifest(
                    archive,
                    source_commit="a" * 40,
                    frozen_at="2025-08-27T10:00:00Z",
                    invocation_url=(
                        "https://github.com/Xpounder-com/hormuz/actions/runs/123/attempts/2"
                    ),
                    run_id=123,
                    run_attempt=2,
                )

            manifest = self._manifest_value(archive)
            manifest["unexpected"] = True
            with self.assertRaisesRegex(
                v1_candidate.V1CandidateError, "manifest_fields_invalid"
            ):
                v1_candidate.validate_manifest(manifest)

    def test_output_is_owner_only_atomic_and_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "proof.json"
            v1_candidate._write_new_private_json(path, {"status": "first"})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            with self.assertRaisesRegex(v1_candidate.V1CandidateError, "output_exists"):
                v1_candidate._write_new_private_json(path, {"status": "second"})
            self.assertEqual(json.loads(path.read_text()), {"status": "first"})

            source = root / "source.json"
            source.write_text('{"status":"untouched"}', encoding="utf-8")
            link = root / "linked-proof.json"
            link.symlink_to(source)
            with self.assertRaisesRegex(v1_candidate.V1CandidateError, "output_exists"):
                v1_candidate._write_new_private_json(link, {"status": "replaced"})
            self.assertEqual(json.loads(source.read_text()), {"status": "untouched"})

    def test_evidence_snapshot_pins_exact_owner_only_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = root / "evidence.json"
            payload = b'{\n  "sessions": ["exact bytes"]\n}\n'
            evidence.write_bytes(payload)
            snapshot = root / "snapshot.json"

            stdout = io.StringIO()
            with mock.patch("sys.stdout", new=stdout):
                result = v1_candidate.main(
                    [
                        "evidence-snapshot",
                        "--evidence",
                        str(evidence),
                        "--output",
                        str(snapshot),
                    ]
                )

            self.assertEqual(result, 0)
            self.assertEqual(snapshot.read_bytes(), payload)
            self.assertEqual(stat.S_IMODE(snapshot.stat().st_mode), 0o600)
            report = json.loads(stdout.getvalue())
            self.assertEqual(
                report,
                {
                    "schema_id": v1_candidate.EVIDENCE_SNAPSHOT_SCHEMA_ID,
                    "schema_version": 1,
                    "digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
                    "size_bytes": len(payload),
                },
            )

            evidence.write_bytes(b'{"sessions":["changed"]}\n')
            with mock.patch("sys.stderr", new=io.StringIO()):
                self.assertEqual(
                    v1_candidate.main(
                        [
                            "evidence-snapshot",
                            "--evidence",
                            str(evidence),
                            "--output",
                            str(snapshot),
                        ]
                    ),
                    2,
                )
            self.assertEqual(snapshot.read_bytes(), payload)

            linked_evidence = root / "linked-evidence.json"
            linked_evidence.symlink_to(evidence)
            with mock.patch("sys.stderr", new=io.StringIO()):
                self.assertEqual(
                    v1_candidate.main(
                        [
                            "evidence-snapshot",
                            "--evidence",
                            str(linked_evidence),
                            "--output",
                            str(root / "linked-snapshot.json"),
                        ]
                    ),
                    2,
                )

    def test_custody_cli_dispatch_does_not_require_promotion_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "custody-proof.json"
            expected = {"status": "verified"}
            with (
                mock.patch.object(
                    v1_candidate, "validate_custody", return_value=expected
                ) as validate,
                mock.patch.object(v1_candidate, "_write_new_private_json") as write,
                mock.patch("sys.stdout", new=io.StringIO()),
            ):
                result = v1_candidate.main(
                    [
                        "custody",
                        "--manifest",
                        str(root / "manifest.json"),
                        "--archive",
                        str(root / "archive.tar.gz"),
                        "--release-api",
                        str(root / "release.json"),
                        "--immutable-api",
                        str(root / "immutable.json"),
                        "--output",
                        str(output),
                    ]
                )

            self.assertEqual(result, 0)
            validate.assert_called_once_with(
                root / "manifest.json",
                root / "archive.tar.gz",
                root / "release.json",
                root / "immutable.json",
            )
            write.assert_called_once_with(output, expected)

    def test_promotion_cli_dispatch_forwards_all_provenance_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "promotion-proof.json"
            expected = {"status": "eligible_for_metadata_promotion"}
            with (
                mock.patch.object(
                    v1_candidate, "validate_promotion", return_value=expected
                ) as validate,
                mock.patch.object(v1_candidate, "_write_new_private_json") as write,
                mock.patch("sys.stdout", new=io.StringIO()),
            ):
                result = v1_candidate.main(
                    [
                        "promotion",
                        "--manifest",
                        str(root / "manifest.json"),
                        "--archive",
                        str(root / "archive.tar.gz"),
                        "--evidence",
                        str(root / "evidence.json"),
                        "--release-api",
                        str(root / "release.json"),
                        "--immutable-api",
                        str(root / "immutable.json"),
                        "--freeze-run-api",
                        str(root / "freeze-run.json"),
                        "--custody-tag-api",
                        str(root / "custody-tag.json"),
                        "--output",
                        str(output),
                    ]
                )

            self.assertEqual(result, 0)
            validate.assert_called_once_with(
                root / "manifest.json",
                root / "archive.tar.gz",
                root / "evidence.json",
                root / "release.json",
                root / "immutable.json",
                root / "freeze-run.json",
                root / "custody-tag.json",
            )
            write.assert_called_once_with(output, expected)

    def test_archive_rejects_symlink_members(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = root / v1_candidate.ARCHIVE_NAME
            with tarfile.open(archive_path, "w:gz") as archive:
                member = tarfile.TarInfo("hormuz-1.0.0/config.example.json")
                member.type = tarfile.SYMTYPE
                member.linkname = "/etc/passwd"
                archive.addfile(member)
            with self.assertRaisesRegex(
                v1_candidate.V1CandidateError, "archive_member_unsafe"
            ):
                v1_candidate.inspect_archive(archive_path)

    def test_immutable_candidate_custody_verifies_exact_release_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = self._archive(root)
            manifest = self._manifest_value(archive)
            manifest_path = self._write_manifest(root, manifest)
            release, immutable = self._api_files(
                root, manifest_path, manifest, archive
            )
            result = v1_candidate.validate_custody(
                manifest_path,
                archive,
                release,
                immutable,
            )
            self.assertTrue(result["archive_reverified"])
            self.assertTrue(result["release_immutable"])
            self.assertFalse(result["promotion_rebuild_permitted"])

    def test_custody_rejects_disabled_immutability_and_extra_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = self._archive(root)
            manifest = self._manifest_value(archive)
            manifest_path = self._write_manifest(root, manifest)
            release, immutable = self._api_files(root, manifest_path, manifest, archive)
            immutable.write_text(json.dumps({"enabled": False}), encoding="utf-8")
            with self.assertRaisesRegex(
                v1_candidate.V1CandidateError, "immutable_releases_not_enabled"
            ):
                v1_candidate.validate_custody(
                    manifest_path, archive, release, immutable
                )

            release, immutable = self._api_files(root, manifest_path, manifest, archive)
            value = json.loads(release.read_text())
            value["assets"].append(
                {
                    "name": "replacement.tar.gz",
                    "state": "uploaded",
                    "size": 1,
                    "digest": "sha256:" + "0" * 64,
                }
            )
            release.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(
                v1_candidate.V1CandidateError, "release_assets_invalid"
            ):
                v1_candidate.validate_custody(
                    manifest_path, archive, release, immutable
                )

    def test_real_gate_can_promote_only_the_exact_candidate_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first"
            second = root / "second"
            first.mkdir()
            second.mkdir()

            first_archive = self._archive(first, marker="first")
            first_manifest = self._manifest_value(first_archive)
            first_manifest_path = self._write_manifest(first, first_manifest)
            evidence = self._evidence(first, first_manifest)
            first_release, first_immutable = self._api_files(
                first, first_manifest_path, first_manifest, first_archive
            )
            first_run = self._freeze_run(first, first_manifest)
            first_tag = self._custody_tag(first, first_manifest)
            result = v1_candidate.validate_promotion(
                first_manifest_path,
                first_archive,
                evidence,
                first_release,
                first_immutable,
                first_run,
                first_tag,
            )
            self.assertEqual(result["status"], "eligible_for_metadata_promotion")
            self.assertTrue(result["final_tag_creation_permitted"])

            second_archive = self._archive(second, marker="changed")
            second_manifest = self._manifest_value(second_archive)
            second_manifest_path = self._write_manifest(second, second_manifest)
            second_release, second_immutable = self._api_files(
                second, second_manifest_path, second_manifest, second_archive
            )
            second_run = self._freeze_run(second, second_manifest)
            second_tag = self._custody_tag(second, second_manifest)
            self.assertNotEqual(
                first_manifest["candidate"]["artifact_digest"],
                second_manifest["candidate"]["artifact_digest"],
            )
            with self.assertRaisesRegex(
                v1_candidate.V1CandidateError, "gate_candidate_mismatch"
            ):
                v1_candidate.validate_promotion(
                    second_manifest_path,
                    second_archive,
                    evidence,
                    second_release,
                    second_immutable,
                    second_run,
                    second_tag,
                )

    def test_promotion_accepts_release_created_at_from_source_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = self._archive(root)
            manifest = self._manifest_value(archive)
            manifest_path = self._write_manifest(root, manifest)
            evidence = self._evidence(root, manifest)
            release, immutable = self._api_files(root, manifest_path, manifest, archive)
            freeze_run = self._freeze_run(root, manifest)
            custody_tag = self._custody_tag(root, manifest)
            release_value = json.loads(release.read_text())
            release_value["created_at"] = "2025-08-20T10:00:00Z"
            release.write_text(json.dumps(release_value), encoding="utf-8")

            result = v1_candidate.validate_promotion(
                manifest_path,
                archive,
                evidence,
                release,
                immutable,
                freeze_run,
                custody_tag,
            )

            self.assertEqual(result["status"], "eligible_for_metadata_promotion")

    def test_synthetic_gate_evidence_cannot_authorize_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = self._archive(root)
            manifest = self._manifest_value(archive)
            manifest_path = self._write_manifest(root, manifest)
            evidence = root / "synthetic.json"
            value = json.loads(
                EVIDENCE_FIXTURE.read_text(encoding="utf-8").replace(
                    "2026-08-27", "2025-08-27"
                )
            )
            evidence.write_text(json.dumps(value), encoding="utf-8")
            release, immutable = self._api_files(root, manifest_path, manifest, archive)
            freeze_run = self._freeze_run(root, manifest)
            custody_tag = self._custody_tag(root, manifest)
            with self.assertRaisesRegex(
                v1_candidate.V1CandidateError, "gate_evidence_not_real"
            ):
                v1_candidate.validate_promotion(
                    manifest_path,
                    archive,
                    evidence,
                    release,
                    immutable,
                    freeze_run,
                    custody_tag,
                )

    def test_promotion_rejects_a_run_not_bound_to_the_freeze_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = self._archive(root)
            manifest = self._manifest_value(archive)
            manifest_path = self._write_manifest(root, manifest)
            evidence = self._evidence(root, manifest)
            release, immutable = self._api_files(root, manifest_path, manifest, archive)
            freeze_run = self._freeze_run(root, manifest)
            custody_tag = self._custody_tag(root, manifest)
            run = json.loads(freeze_run.read_text())
            run["path"] = ".github/workflows/ci.yml"
            freeze_run.write_text(json.dumps(run), encoding="utf-8")

            with self.assertRaisesRegex(
                v1_candidate.V1CandidateError, "freeze_run_binding_invalid"
            ):
                v1_candidate.validate_promotion(
                    manifest_path,
                    archive,
                    evidence,
                    release,
                    immutable,
                    freeze_run,
                    custody_tag,
                )

    def test_promotion_rejects_an_asset_replaced_after_the_freeze_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = self._archive(root)
            manifest = self._manifest_value(archive)
            manifest_path = self._write_manifest(root, manifest)
            evidence = self._evidence(root, manifest)
            release, immutable = self._api_files(root, manifest_path, manifest, archive)
            freeze_run = self._freeze_run(root, manifest)
            custody_tag = self._custody_tag(root, manifest)
            release_value = json.loads(release.read_text())
            release_value["assets"][0]["created_at"] = "2025-08-27T10:06:00Z"
            release_value["assets"][0]["updated_at"] = "2025-08-27T10:06:00Z"
            release.write_text(json.dumps(release_value), encoding="utf-8")

            with self.assertRaisesRegex(
                v1_candidate.V1CandidateError, "release_asset_chronology_invalid"
            ):
                v1_candidate.validate_promotion(
                    manifest_path,
                    archive,
                    evidence,
                    release,
                    immutable,
                    freeze_run,
                    custody_tag,
                )

    def test_promotion_rejects_an_asset_created_before_the_freeze(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = self._archive(root)
            manifest = self._manifest_value(archive)
            manifest_path = self._write_manifest(root, manifest)
            evidence = self._evidence(root, manifest)
            release, immutable = self._api_files(root, manifest_path, manifest, archive)
            freeze_run = self._freeze_run(root, manifest)
            custody_tag = self._custody_tag(root, manifest)
            release_value = json.loads(release.read_text())
            release_value["assets"][0]["created_at"] = "2025-08-27T09:59:00Z"
            release_value["assets"][0]["updated_at"] = "2025-08-27T09:59:00Z"
            release.write_text(json.dumps(release_value), encoding="utf-8")

            with self.assertRaisesRegex(
                v1_candidate.V1CandidateError, "release_asset_chronology_invalid"
            ):
                v1_candidate.validate_promotion(
                    manifest_path,
                    archive,
                    evidence,
                    release,
                    immutable,
                    freeze_run,
                    custody_tag,
                )

    def test_promotion_rejects_publication_after_the_freeze_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = self._archive(root)
            manifest = self._manifest_value(archive)
            manifest_path = self._write_manifest(root, manifest)
            evidence = self._evidence(root, manifest)
            release, immutable = self._api_files(root, manifest_path, manifest, archive)
            freeze_run = self._freeze_run(root, manifest)
            custody_tag = self._custody_tag(root, manifest)
            release_value = json.loads(release.read_text())
            release_value["published_at"] = "2025-08-27T10:06:00Z"
            release.write_text(json.dumps(release_value), encoding="utf-8")

            with self.assertRaisesRegex(
                v1_candidate.V1CandidateError,
                "candidate_release_chronology_invalid",
            ):
                v1_candidate.validate_promotion(
                    manifest_path,
                    archive,
                    evidence,
                    release,
                    immutable,
                    freeze_run,
                    custody_tag,
                )

    def test_promotion_rejects_release_created_at_after_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = self._archive(root)
            manifest = self._manifest_value(archive)
            manifest_path = self._write_manifest(root, manifest)
            evidence = self._evidence(root, manifest)
            release, immutable = self._api_files(root, manifest_path, manifest, archive)
            freeze_run = self._freeze_run(root, manifest)
            custody_tag = self._custody_tag(root, manifest)
            release_value = json.loads(release.read_text())
            release_value["created_at"] = "2025-08-27T10:05:00Z"
            release.write_text(json.dumps(release_value), encoding="utf-8")

            with self.assertRaisesRegex(
                v1_candidate.V1CandidateError,
                "candidate_release_chronology_invalid",
            ):
                v1_candidate.validate_promotion(
                    manifest_path,
                    archive,
                    evidence,
                    release,
                    immutable,
                    freeze_run,
                    custody_tag,
                )

    def test_promotion_rejects_a_moved_custody_tag(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = self._archive(root)
            manifest = self._manifest_value(archive)
            manifest_path = self._write_manifest(root, manifest)
            evidence = self._evidence(root, manifest)
            release, immutable = self._api_files(root, manifest_path, manifest, archive)
            freeze_run = self._freeze_run(root, manifest)
            custody_tag = self._custody_tag(root, manifest)
            tag_value = json.loads(custody_tag.read_text())
            tag_value["object"]["sha"] = "b" * 40
            custody_tag.write_text(json.dumps(tag_value), encoding="utf-8")

            with self.assertRaisesRegex(
                v1_candidate.V1CandidateError, "custody_tag_binding_invalid"
            ):
                v1_candidate.validate_promotion(
                    manifest_path,
                    archive,
                    evidence,
                    release,
                    immutable,
                    freeze_run,
                    custody_tag,
                )

    def test_final_release_is_metadata_only_and_bound_to_exact_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = self._archive(root)
            manifest = self._manifest_value(archive)
            manifest_path = self._write_manifest(root, manifest)
            evidence = self._evidence(root, manifest)
            release, immutable = self._final_release(root, manifest, evidence)

            result = v1_candidate.validate_final_release(
                manifest_path,
                evidence,
                release,
                immutable,
            )
            self.assertEqual(
                result["status"], "published_metadata_for_exact_candidate"
            )
            self.assertFalse(result["release_assets_copied"])
            self.assertFalse(result["promotion_rebuild_permitted"])
            self.assertEqual(
                result["authoritative_binding"], "protected_annotated_tag"
            )

            release_value = json.loads(release.read_text())
            release_value["assets"] = [{"name": v1_candidate.ARCHIVE_NAME}]
            release.write_text(json.dumps(release_value), encoding="utf-8")
            with self.assertRaisesRegex(
                v1_candidate.V1CandidateError, "final_release_contract_invalid"
            ):
                v1_candidate.validate_final_release(
                    manifest_path,
                    evidence,
                    release,
                    immutable,
                )

    def test_final_release_notes_name_canonical_candidate_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = self._archive(root)
            manifest = self._manifest_value(archive)
            manifest_path = self._write_manifest(root, manifest)
            evidence = self._evidence(root, manifest)

            payload, proof = v1_candidate.create_final_release_notes(
                manifest_path, evidence
            )
            candidate = manifest["candidate"]
            custody = manifest["custody"]
            assert isinstance(candidate, dict) and isinstance(custody, dict)
            notes = payload.decode("utf-8")
            self.assertIn(str(custody["release_tag"]), notes)
            self.assertIn(str(candidate["artifact_digest"]), notes)
            self.assertIn("without rebuilding or copying", notes)
            self.assertEqual(proof["digest"], "sha256:" + hashlib.sha256(payload).hexdigest())

    def test_final_tag_annotation_requires_exact_standalone_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = self._archive(root)
            manifest = self._manifest_value(archive)
            candidate = manifest["candidate"]
            custody = manifest["custody"]
            assert isinstance(candidate, dict) and isinstance(custody, dict)
            candidate_digest = str(candidate["artifact_digest"])
            candidate_tag = str(custody["release_tag"])
            gate_digest = "sha256:" + "b" * 64
            message = "\n".join(
                v1_candidate._expected_final_tag_annotation_lines(
                    candidate_digest,
                    gate_digest,
                    candidate_tag,
                )
            ) + "\n"
            message_path = root / "tag-message.txt"
            message_path.write_text(message, encoding="utf-8")

            proof = v1_candidate.validate_final_tag_annotation(
                message_path,
                candidate_digest=candidate_digest,
                gate_evidence_digest=gate_digest,
                candidate_tag=candidate_tag,
            )
            self.assertEqual(proof["status"], "exact_annotation_valid")

            invalid_messages = (
                message.replace(candidate_tag, candidate_tag + "-stale"),
                message + "\nGate evidence: sha256:" + "c" * 64 + "\n",
                message.replace(
                    f"Gate evidence: {gate_digest}",
                    f"Gate evidence: sha256:{'d' * 64}",
                ),
            )
            for invalid in invalid_messages:
                with self.subTest(invalid=invalid[-80:]):
                    message_path.write_text(invalid, encoding="utf-8")
                    with self.assertRaisesRegex(
                        v1_candidate.V1CandidateError,
                        "final_tag_annotation_invalid",
                    ):
                        v1_candidate.validate_final_tag_annotation(
                            message_path,
                            candidate_digest=candidate_digest,
                            gate_evidence_digest=gate_digest,
                            candidate_tag=candidate_tag,
                        )

    def test_final_tag_object_uses_the_exact_annotation_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = self._archive(root)
            manifest = self._manifest_value(archive)
            candidate = manifest["candidate"]
            custody = manifest["custody"]
            assert isinstance(candidate, dict) and isinstance(custody, dict)
            source_commit = str(candidate["source_commit"])
            candidate_digest = str(candidate["artifact_digest"])
            candidate_tag = str(custody["release_tag"])
            gate_digest = "sha256:" + "b" * 64
            message = "\n".join(
                v1_candidate._expected_final_tag_annotation_lines(
                    candidate_digest,
                    gate_digest,
                    candidate_tag,
                )
            )
            tag_object = root / "tag-object.json"
            tag_object.write_text(
                json.dumps(
                    {
                        "tag": "v1.0.0",
                        "object": {"type": "commit", "sha": source_commit},
                        "tagger": {"date": "2025-08-27T11:00:00Z"},
                        "message": message,
                    }
                ),
                encoding="utf-8",
            )

            proof = v1_candidate.validate_final_tag_object(
                tag_object,
                source_commit=source_commit,
                gate_generated_at="2025-08-27T10:00:00Z",
                candidate_digest=candidate_digest,
                gate_evidence_digest=gate_digest,
                candidate_tag=candidate_tag,
            )
            self.assertEqual(proof["status"], "exact_protected_tag_valid")

            value = json.loads(tag_object.read_text())
            value["message"] += f"\n\nGate evidence: sha256:{'c' * 64}"
            tag_object.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(
                v1_candidate.V1CandidateError, "final_tag_annotation_invalid"
            ):
                v1_candidate.validate_final_tag_object(
                    tag_object,
                    source_commit=source_commit,
                    gate_generated_at="2025-08-27T10:00:00Z",
                    candidate_digest=candidate_digest,
                    gate_evidence_digest=gate_digest,
                    candidate_tag=candidate_tag,
                )

    def test_git_serialized_tag_annotation_matches_the_exact_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "repository"
            repository.mkdir()
            subprocess.run(
                ["git", "init", "--quiet", str(repository)],
                check=True,
            )
            identity = [
                "-c",
                "user.name=Hormuz release test",
                "-c",
                "user.email=release-test@example.invalid",
            ]
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repository),
                    *identity,
                    "commit",
                    "--quiet",
                    "--allow-empty",
                    "-m",
                    "initial",
                ],
                check=True,
            )
            candidate_digest = "sha256:" + "a" * 64
            gate_digest = "sha256:" + "b" * 64
            candidate_tag = "candidate-v1.0.0-" + "a" * 64
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repository),
                    *identity,
                    "tag",
                    "-a",
                    "v1.0.0",
                    "-m",
                    "Hormuz v1.0.0",
                    "-m",
                    f"Frozen source archive: {candidate_digest}",
                    "-m",
                    f"Gate evidence: {gate_digest}",
                    "-m",
                    f"Candidate custody tag: {candidate_tag}",
                ],
                check=True,
            )
            source_commit = subprocess.check_output(
                ["git", "-C", str(repository), "rev-parse", "HEAD"], text=True
            ).strip()
            tag_object_path = root / "serialized-tag-object.txt"
            tag_object_path.write_bytes(
                subprocess.check_output(
                    [
                        "git",
                        "-C",
                        str(repository),
                        "cat-file",
                        "tag",
                        "refs/tags/v1.0.0",
                    ]
                )
            )
            proof = v1_candidate.validate_local_final_tag_object(
                tag_object_path,
                source_commit=source_commit,
                gate_generated_at="2020-01-01T00:00:00Z",
                candidate_digest=candidate_digest,
                gate_evidence_digest=gate_digest,
                candidate_tag=candidate_tag,
            )
            self.assertEqual(proof["status"], "exact_local_tag_object_valid")
            self.assertEqual(proof["direct_target_type"], "commit")

            # Raw object bytes reach the validator unchanged; an extra trailing
            # paragraph cannot be erased by shell command substitution.
            tag_object_path.write_bytes(tag_object_path.read_bytes() + b"\n")
            with self.assertRaisesRegex(
                v1_candidate.V1CandidateError, "final_tag_annotation_invalid"
            ):
                v1_candidate.validate_local_final_tag_object(
                    tag_object_path,
                    source_commit=source_commit,
                    gate_generated_at="2020-01-01T00:00:00Z",
                    candidate_digest=candidate_digest,
                    gate_evidence_digest=gate_digest,
                    candidate_tag=candidate_tag,
                )

    def test_freeze_authorization_survives_steward_rotation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_commit = "a" * 40
            current_run_id = 202
            runs_path = root / "freeze-runs.json"
            jobs_directory = root / "jobs"
            jobs_directory.mkdir()
            runs_path.write_text(
                json.dumps(
                    [
                        {
                            "workflow_runs": [
                                {
                                    "id": 101,
                                    "event": "workflow_dispatch",
                                    "head_sha": source_commit,
                                    "path": (
                                        ".github/workflows/"
                                        "freeze-v1-candidate.yml@main"
                                    ),
                                    "actor": {"login": "former-steward"},
                                    "triggering_actor": {
                                        "login": "former-steward"
                                    },
                                },
                                {
                                    "id": current_run_id,
                                    "event": "workflow_dispatch",
                                    "head_sha": source_commit,
                                    "path": (
                                        ".github/workflows/"
                                        "freeze-v1-candidate.yml@main"
                                    ),
                                    "actor": {"login": "current-steward"},
                                    "triggering_actor": {
                                        "login": "current-steward"
                                    },
                                },
                            ]
                        }
                    ]
                ),
                encoding="utf-8",
            )

            def write_jobs(run_id: int, conclusion: str) -> None:
                (jobs_directory / f"{run_id}.json").write_text(
                    json.dumps(
                        [
                            {
                                "jobs": [
                                    {
                                        "run_id": run_id,
                                        "head_sha": source_commit,
                                        "name": (
                                            v1_candidate.FREEZE_AUTHORIZATION_JOB_NAME
                                        ),
                                        "status": "completed",
                                        "conclusion": conclusion,
                                    }
                                ]
                            }
                        ]
                    ),
                    encoding="utf-8",
                )

            write_jobs(101, "success")
            write_jobs(current_run_id, "success")
            with self.assertRaisesRegex(
                v1_candidate.V1CandidateError, "freeze_run_authorization_invalid"
            ):
                v1_candidate.validate_freeze_run_authorization(
                    runs_path,
                    jobs_directory,
                    source_commit=source_commit,
                    current_run_id=current_run_id,
                )

            # An unauthorized failed dispatch remains harmless; the actor names
            # are historical context and never get reinterpreted after rotation.
            write_jobs(101, "failure")
            proof = v1_candidate.validate_freeze_run_authorization(
                runs_path,
                jobs_directory,
                source_commit=source_commit,
                current_run_id=current_run_id,
            )
            self.assertEqual(proof["authorized_run_ids"], [current_run_id])

    def test_git_direct_tag_target_distinguishes_a_nested_tag(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary) / "repository"
            repository.mkdir()
            subprocess.run(["git", "init", "--quiet", str(repository)], check=True)
            identity = [
                "-c",
                "user.name=Hormuz release test",
                "-c",
                "user.email=release-test@example.invalid",
            ]
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repository),
                    *identity,
                    "commit",
                    "--quiet",
                    "--allow-empty",
                    "-m",
                    "initial",
                ],
                check=True,
            )
            commit = subprocess.check_output(
                ["git", "-C", str(repository), "rev-parse", "HEAD"], text=True
            ).strip()
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repository),
                    *identity,
                    "tag",
                    "-a",
                    "inner",
                    "-m",
                    "inner",
                ],
                check=True,
            )
            inner_tag_object = subprocess.check_output(
                ["git", "-C", str(repository), "rev-parse", "refs/tags/inner"],
                text=True,
            ).strip()
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repository),
                    *identity,
                    "tag",
                    "-a",
                    "v1.0.0",
                    "inner",
                    "-m",
                    "outer",
                ],
                check=True,
                stderr=subprocess.DEVNULL,
            )
            tag_object_path = Path(temporary) / "nested-tag-object.txt"
            tag_object = subprocess.check_output(
                [
                    "git",
                    "-C",
                    str(repository),
                    "cat-file",
                    "tag",
                    "refs/tags/v1.0.0",
                ]
            )
            tag_object_path.write_bytes(tag_object)
            self.assertTrue(
                tag_object.startswith(
                    f"object {inner_tag_object}\ntype tag\n".encode("ascii")
                )
            )
            with self.assertRaisesRegex(
                v1_candidate.V1CandidateError, "local_final_tag_object_invalid"
            ):
                v1_candidate.validate_local_final_tag_object(
                    tag_object_path,
                    source_commit=commit,
                    gate_generated_at="2020-01-01T00:00:00Z",
                    candidate_digest="sha256:" + "a" * 64,
                    gate_evidence_digest="sha256:" + "b" * 64,
                    candidate_tag="candidate-v1.0.0-" + "a" * 64,
                )

    def test_freeze_workflow_separates_authority_and_builds_once(self) -> None:
        workflow = (ROOT / ".github/workflows/freeze-v1-candidate.yml").read_text()
        self.assertIn("permissions: {}", workflow)
        self.assertNotIn("contents: write", workflow)
        self.assertNotIn("id-token: write", workflow)
        self.assertNotIn("self-hosted", workflow)
        self.assertEqual(workflow.count("  authorize:\n"), 1)
        self.assertEqual(workflow.count("  preflight:\n"), 1)
        self.assertEqual(workflow.count("  build:\n"), 1)
        self.assertEqual(workflow.count("  publish:\n"), 1)
        self.assertEqual(workflow.count("environment: v1-release-custody"), 2)
        self.assertIn("needs: authorize", workflow)
        self.assertIn("needs: preflight", workflow)
        self.assertIn("needs: build", workflow)
        self.assertIn("${{ github.workflow_ref }}", workflow)
        self.assertIn("@refs/heads/main", workflow)
        self.assertIn('[[ "$GITHUB_REF_PROTECTED" == "true" ]]', workflow)
        self.assertIn('[[ "$GITHUB_SHA" =~ ^[0-9a-f]{40}$ ]]', workflow)
        self.assertIn("GITHUB_TRIGGERING_ACTOR", workflow)
        self.assertIn("/jobs?filter=all&per_page=100", workflow)
        self.assertEqual(workflow.count("python -m build --sdist"), 1)
        self.assertIn("requirements/v1-source-build.lock", workflow)
        self.assertIn("--require-hashes", workflow)
        self.assertIn("--force-reinstall", workflow)
        self.assertIn("--only-binary=:all:", workflow)
        self.assertIn("--no-deps", workflow)
        self.assertIn("--no-isolation", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("ref: ${{ github.sha }}", workflow)
        self.assertIn("ACTIONS_ID_TOKEN_REQUEST_TOKEN", workflow)
        self.assertIn("actions/upload-artifact@", workflow)
        self.assertIn("actions/download-artifact@", workflow)
        self.assertIn("retention-days: 1", workflow)
        self.assertIn("overwrite: false", workflow)
        self.assertIn("include-hidden-files: false", workflow)

        build = workflow.split("  build:\n", 1)[1].split("  publish:\n", 1)[0]
        publish = workflow.split("  publish:\n", 1)[1]
        self.assertNotIn("${{ secrets.", build)
        self.assertNotIn("environment: v1-release-custody", build)
        self.assertNotIn("actions/checkout@", publish)
        self.assertNotIn("python tools/", publish)
        self.assertNotIn("python -m build", publish)
        self.assertNotIn("tarfile", publish)
        self.assertNotIn("extract", publish)
        self.assertEqual(
            workflow.count("GH_PUBLISH_TOKEN: ${{ secrets.V1_RELEASE_PUBLISH_TOKEN }}"),
            2,
        )
        self.assertEqual(
            workflow.count("GH_ADMIN_TOKEN: ${{ secrets.V1_RELEASE_ADMIN_TOKEN }}"),
            3,
        )
        credential_preflight = workflow.split(
            "      - name: Authenticate the publisher credential before the one permitted build\n",
            1,
        )[1].split("\n  build:\n", 1)[0]
        self.assertIn('get_json(token, "/user")', credential_preflight)
        self.assertIn(
            'get_json(token, f"/repos/{REPOSITORY}")', credential_preflight
        )
        self.assertIn('"mutation_performed": False', credential_preflight)
        self.assertIn("expected=(422,)", credential_preflight)
        self.assertIn("releases_after_probe == releases", credential_preflight)
        self.assertIn('GH_TOKEN="$GH_ADMIN_TOKEN" gh api "/user"', workflow)
        self.assertIn('candidate_ruleset.get("current_user_can_bypass") != "always"', workflow)
        self.assertIn("release_steward_cannot_bypass_candidate_ruleset", workflow)
        self.assertNotIn("GH_ADMIN_TOKEN", credential_preflight)
        self.assertNotIn("actions/checkout", credential_preflight)

    def test_publisher_secret_helper_uses_exact_stdin_without_a_body_literal(
        self,
    ) -> None:
        helper_path = ROOT / "tools/set_v1_release_publisher_secret.zsh"
        helper = helper_path.read_text(encoding="utf-8")
        manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")

        self.assertIn("IFS= read -r -s publisher_token", helper)
        self.assertIn('if [[ "${publisher_token}" == *[[:space:]]* ]]', helper)
        self.assertIn('if [[ "${publisher_probe_status}" != "422" ]]', helper)
        self.assertIn("/usr/bin/python3 -I -B -", helper)
        self.assertIn(
            '"orgs/Xpounder-com/memberships/${authorized_steward}"',
            helper,
        )
        self.assertGreaterEqual(helper.count("--hostname github.com"), 6)
        self.assertIn(
            'if print -rn -- "${publisher_token}" | env -u GH_TOKEN -u GITHUB_TOKEN',
            helper,
        )
        storage_command = helper.split(
            'if print -rn -- "${publisher_token}"', 1
        )[1].split("; then", 1)[0]
        self.assertIn('gh secret set "${secret_name}"', storage_command)
        self.assertIn('--repo "github.com/${repository}"', storage_command)
        self.assertIn('--env "${environment_name}"', storage_command)
        self.assertNotIn("--body", storage_command)
        self.assertIn(
            "include tools/set_v1_release_publisher_secret.zsh",
            manifest,
        )
        helper_archive_path = "tools/set_v1_release_publisher_secret.zsh"
        self.assertIn(helper_archive_path, v1_candidate.REQUIRED_ARCHIVE_PATHS)
        self.assertIn(
            helper_archive_path,
            verify_core_wheel.REQUIRED_POLICY_ADMIN_USABILITY_SDIST_PATHS,
        )

    def test_publisher_requires_effective_candidate_ruleset_bypass(self) -> None:
        namespace = self._publisher_namespace()

        ruleset_summaries = [
            {
                "id": 11,
                "name": "Owner-created candidate tags",
                "source_type": "Repository",
                "target": "tag",
                "enforcement": "active",
            },
            {
                "id": 12,
                "name": "Immutable version tags",
                "source_type": "Repository",
                "target": "tag",
                "enforcement": "active",
            },
        ]
        namespace["list_pages"] = lambda _token, _path: ruleset_summaries
        bypass_mode = "always"

        def ruleset_api(
            _token: str,
            method: str,
            path: str,
            **_kwargs: object,
        ) -> tuple[int, object]:
            self.assertEqual(method, "GET")
            if path == "/user":
                return 200, {"login": "steward"}
            if path.endswith("/rulesets/11"):
                return 200, {
                    "current_user_can_bypass": bypass_mode,
                    "bypass_actors": [
                        {
                            "actor_id": None,
                            "actor_type": "OrganizationAdmin",
                            "bypass_mode": "always",
                        }
                    ],
                    "conditions": {
                        "ref_name": {
                            "exclude": [],
                            "include": ["refs/tags/candidate-v1.0.0-*"],
                        }
                    },
                    "rules": [{"type": "creation"}],
                }
            if path.endswith("/rulesets/12"):
                return 200, {
                    "bypass_actors": [],
                    "conditions": {
                        "ref_name": {
                            "exclude": [],
                            "include": [
                                "refs/tags/candidate-v1.0.0-*",
                                "refs/tags/v*",
                            ],
                        }
                    },
                    "rules": [
                        {"type": "deletion"},
                        {"type": "non_fast_forward"},
                        {
                            "type": "update",
                            "parameters": {
                                "update_allows_fetch_and_merge": False
                            },
                        },
                    ],
                }
            self.fail(f"unexpected API call: {method} {path}")

        namespace["api_json"] = ruleset_api
        namespace["validate_rulesets"]("admin-token", "steward")

        bypass_mode = None
        with self.assertRaisesRegex(
            namespace["ContractError"],
            "release_steward_cannot_bypass_candidate_ruleset",
        ):
            namespace["validate_rulesets"]("admin-token", "steward")

    def test_publisher_validates_the_fixed_transfer_contract(self) -> None:
        namespace = self._publisher_namespace()
        validate_transfer = namespace["validate_transfer"]
        self.assertTrue(callable(validate_transfer))
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            archive = self._archive(directory)
            manifest = self._manifest_value(archive)
            self._write_manifest(directory, manifest)
            result = validate_transfer(
                directory,
                "Xpounder-com/hormuz",
                "a" * 40,
                123,
                1,
            )
            self.assertEqual(result["source_sha"], "a" * 40)
            self.assertEqual(
                result["digest"],
                "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest(),
            )
            self.assertEqual(stat.S_IMODE(archive.stat().st_mode), 0o600)
            self.assertEqual(
                stat.S_IMODE((directory / v1_candidate.MANIFEST_NAME).stat().st_mode),
                0o600,
            )

    def test_publisher_credential_preflight_labels_invalid_token_bytes(self) -> None:
        namespace = self._publisher_credential_preflight_namespace()
        get_json = namespace["get_json"]
        credential_error = namespace["CredentialError"]
        failure = urllib.error.HTTPError(
            "https://api.github.com/user",
            401,
            "Bad credentials",
            {},
            None,
        )
        self.addCleanup(failure.close)
        with mock.patch.object(namespace["OPENER"], "open", side_effect=failure):
            with self.assertRaisesRegex(
                credential_error,
                "publisher_token_authentication_failed",
            ):
                get_json("invalid-token", "/user")

    def test_publisher_credential_preflight_requires_write_without_mutation(
        self,
    ) -> None:
        namespace = self._publisher_credential_preflight_namespace()
        observed: dict[str, object] = {}
        failure = urllib.error.HTTPError(
            "https://api.github.com/repos/Xpounder-com/hormuz/releases",
            422,
            "Validation Failed",
            {},
            io.BytesIO(b'{"message":"Validation Failed"}'),
        )
        self.addCleanup(failure.close)

        def reject_invalid_release(request: object, *, timeout: int) -> None:
            observed["method"] = request.get_method()
            observed["data"] = request.data
            observed["timeout"] = timeout
            raise failure

        with mock.patch.object(
            namespace["OPENER"],
            "open",
            side_effect=reject_invalid_release,
        ):
            status, response = namespace["api_json"](
                "publisher-token",
                "POST",
                "/repos/Xpounder-com/hormuz/releases",
                value={},
                expected=(422,),
            )
        self.assertEqual(status, 422)
        self.assertEqual(response, {"message": "Validation Failed"})
        self.assertEqual(
            observed,
            {"method": "POST", "data": b"{}", "timeout": 30},
        )

    def test_publisher_reauthenticates_and_probes_write_before_mutation(
        self,
    ) -> None:
        namespace = self._publisher_namespace()
        calls: list[tuple[str, str, object, tuple[int, ...]]] = []
        release_snapshots = [[{"id": 1, "tag_name": "existing"}]] * 2

        def fake_api_json(
            _token: str,
            method: str,
            path: str,
            *,
            value: object = None,
            expected: tuple[int, ...] = (200,),
        ) -> tuple[int, object]:
            calls.append((method, path, value, expected))
            if method == "GET" and path == "/user":
                return 200, {"login": "steward"}
            if method == "POST" and path.endswith("/releases"):
                return 422, {"message": "Validation Failed"}
            self.fail(f"unexpected API call: {method} {path}")

        namespace["api_json"] = fake_api_json
        namespace["release_list"] = lambda _token: release_snapshots.pop(0)
        namespace["validate_publisher_credential"](
            "publisher-token",
            "steward",
            probe_write=True,
        )
        self.assertEqual(release_snapshots, [])
        self.assertEqual(
            calls,
            [
                ("GET", "/user", None, (200,)),
                (
                    "POST",
                    "/repos/Xpounder-com/hormuz/releases",
                    {},
                    (422,),
                ),
            ],
        )

        namespace["api_json"] = lambda *_args, **_kwargs: (
            200,
            {"login": "another-administrator"},
        )
        with self.assertRaisesRegex(
            namespace["ContractError"],
            "publisher_token_actor_invalid",
        ):
            namespace["validate_publisher_credential"](
                "publisher-token",
                "steward",
                probe_write=False,
            )

    def test_publisher_rejects_untrusted_transfer_drift(self) -> None:
        namespace = self._publisher_namespace()
        validate_transfer = namespace["validate_transfer"]
        contract_error = namespace["ContractError"]
        mutations = (
            "extra_file",
            "repository",
            "source_sha",
            "run_id",
            "run_attempt",
            "version",
            "digest",
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                archive = self._archive(directory)
                manifest = self._manifest_value(archive)
                repository = "Xpounder-com/hormuz"
                source_sha = "a" * 40
                run_id = 123
                run_attempt = 1
                if mutation == "extra_file":
                    (directory / "unexpected.txt").write_text("unexpected", encoding="utf-8")
                elif mutation == "repository":
                    repository = "attacker/example"
                elif mutation == "source_sha":
                    source_sha = "a" * 39
                elif mutation == "run_id":
                    manifest["build"]["run_id"] = 124
                elif mutation == "run_attempt":
                    run_attempt = 2
                elif mutation == "version":
                    manifest["candidate"]["package_version"] = "1.0.1"
                elif mutation == "digest":
                    manifest["candidate"]["artifact_digest"] = "sha256:" + "b" * 64
                self._write_manifest(directory, manifest)
                with self.assertRaises(contract_error):
                    validate_transfer(
                        directory,
                        repository,
                        source_sha,
                        run_id,
                        run_attempt,
                    )

    def test_publisher_rejects_symlink_and_duplicate_json_inputs(self) -> None:
        namespace = self._publisher_namespace()
        validate_transfer = namespace["validate_transfer"]
        contract_error = namespace["ContractError"]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            archive = self._archive(directory)
            manifest = self._manifest_value(archive)
            manifest_path = self._write_manifest(directory, manifest)
            manifest_path.unlink()
            manifest_path.symlink_to(archive)
            with self.assertRaises(contract_error):
                validate_transfer(directory, "Xpounder-com/hormuz", "a" * 40, 123, 1)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self._archive(directory)
            (directory / v1_candidate.MANIFEST_NAME).mkdir()
            with self.assertRaises(contract_error):
                validate_transfer(directory, "Xpounder-com/hormuz", "a" * 40, 123, 1)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self._archive(directory)
            (directory / v1_candidate.MANIFEST_NAME).write_text(
                '{"schema_id":"one","schema_id":"two"}', encoding="utf-8"
            )
            with self.assertRaises(contract_error):
                validate_transfer(directory, "Xpounder-com/hormuz", "a" * 40, 123, 1)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self._archive(directory)
            (directory / v1_candidate.MANIFEST_NAME).write_bytes(
                b"{" + b" " * (2 * 1024 * 1024)
            )
            with self.assertRaises(contract_error):
                validate_transfer(directory, "Xpounder-com/hormuz", "a" * 40, 123, 1)

    def test_publisher_hashes_remote_draft_bytes_before_publication(self) -> None:
        workflow = (ROOT / ".github/workflows/freeze-v1-candidate.yml").read_text()
        live_controls = workflow.split(
            "          def validate_live_controls(",
            1,
        )[1].split("\n          def expected_notes", 1)[0]
        self.assertLess(
            live_controls.index("validate_publisher_credential("),
            live_controls.index("immutable-releases"),
        )
        publisher = workflow.split("          def publish_candidate():", 1)[1].split(
            "          if __name__ == \"__main__\":", 1
        )[0]
        first_control_check = publisher.index("validate_live_controls(")
        first_mutation = publisher.index('"POST"', first_control_check)
        self.assertLess(first_control_check, first_mutation)
        create_draft = publisher.index('"draft": True')
        first_download = publisher.index("download_and_compare(", create_draft)
        immediate_recheck = publisher.index(
            "# Recheck every mutable control and the remote bytes immediately before publication."
        )
        second_download = publisher.index("download_and_compare(", immediate_recheck)
        publish_draft = publisher.index('value={"draft": False', second_download)
        self.assertLess(create_draft, first_download)
        self.assertLess(first_download, immediate_recheck)
        self.assertLess(immediate_recheck, second_download)
        self.assertLess(second_download, publish_draft)
        self.assertIn("remote_asset_bytes_mismatch", workflow)
        self.assertNotIn("gh release delete", workflow)
        self.assertNotIn("gh release upload", workflow)
        self.assertNotIn("git tag v1.0.0", workflow)

    def test_remote_byte_mismatch_cannot_publish_the_draft(self) -> None:
        namespace = self._publisher_namespace()
        contract_error = namespace["ContractError"]
        calls: list[tuple[str, str]] = []
        transfer = {
            "archive_payload": b"archive",
            "manifest_payload": b"manifest",
            "digest": "sha256:" + hashlib.sha256(b"archive").hexdigest(),
            "source_sha": "a" * 40,
            "tag": "candidate-v1.0.0-" + hashlib.sha256(b"archive").hexdigest(),
        }

        def fake_api_json(
            _token: str,
            method: str,
            path: str,
            *,
            value: object = None,
            expected: tuple[int, ...] = (200,),
        ) -> tuple[int, dict[str, object]]:
            del value, expected
            calls.append((method, path))
            if method == "POST":
                return 201, {
                    "id": 7,
                    "tag_name": transfer["tag"],
                    "target_commitish": transfer["source_sha"],
                    "name": "Hormuz v1.0.0 frozen candidate",
                    "body": namespace["expected_notes"](transfer),
                    "draft": True,
                    "prerelease": True,
                    "immutable": False,
                    "published_at": None,
                    "assets": [],
                }
            return 200, {}

        namespace["validate_transfer"] = lambda *_args: transfer
        namespace["validate_live_controls"] = lambda *_args, **_kwargs: None
        namespace["api_json"] = fake_api_json
        namespace["upload_asset"] = lambda *_args, **_kwargs: None
        namespace["validate_release"] = lambda *_args, **_kwargs: {
            v1_candidate.ARCHIVE_NAME: 1,
            v1_candidate.MANIFEST_NAME: 2,
        }

        def mismatch(*_args: object, **_kwargs: object) -> None:
            raise contract_error("remote_asset_bytes_mismatch")

        namespace["download_and_compare"] = mismatch
        environment = {
            "GH_READ_TOKEN": "read",
            "GH_ADMIN_TOKEN": "admin",
            "GH_PUBLISH_TOKEN": "publish",
            "AUTHORIZED_STEWARD": "steward",
            "RELEASE_TOKENS_SEPARATED": "true",
            "GITHUB_SHA": "a" * 40,
            "GITHUB_RUN_ID": "123",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_REPOSITORY": "Xpounder-com/hormuz",
            "RUNNER_TEMP": "/private/tmp",
        }
        with mock.patch.dict(os.environ, environment, clear=False):
            with self.assertRaises(contract_error):
                namespace["publish_candidate"]()
        self.assertTrue(any(method == "POST" for method, _path in calls))
        self.assertFalse(any(method == "PATCH" for method, _path in calls))

    def test_source_build_frontend_and_backend_are_exactly_hash_locked(self) -> None:
        lock = (ROOT / "requirements/v1-source-build.lock").read_text()
        expected = {
            "build==1.3.0": (
                "7145f0b5061ba90a1500d60bd1b13ca0a8a4cebdd0cc16ed8adf1c0e739f43b4"
            ),
            "packaging==25.0": (
                "29572ef2b1f17581046b3a2227d5c611fb25ec70ca1ba8554b24b0e69331a484"
            ),
            "pyproject-hooks==1.2.0": (
                "9e5c6bfa8dcc30091c74b0cf803c81fdd29d94f01992a7707bc97babb1141913"
            ),
            "setuptools==83.0.0": (
                "29b23c360f22f414dc7336bb39178cc7bcbf6021ed2733cde173f09dba19abb3"
            ),
        }
        self.assertEqual(lock.count("--hash=sha256:"), len(expected))
        for requirement, digest in expected.items():
            self.assertIn(
                f"{requirement} \\\n    --hash=sha256:{digest}",
                lock,
            )

        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
        self.assertEqual(
            pyproject["build-system"],
            {
                "requires": ["setuptools==83.0.0"],
                "build-backend": "setuptools.build_meta",
            },
        )

    def test_promotion_path_cannot_build_or_replace_assets(self) -> None:
        script = (ROOT / "tools/promote_v1_candidate.sh").read_text()
        self.assertNotIn("python -m build", script)
        self.assertNotIn("pip install build", script)
        self.assertNotIn("gh release upload", script)
        self.assertNotIn("gh release edit", script)
        self.assertNotIn("--clobber", script)
        first_gate = script.index("run_candidate_tool promotion")
        tag_push = script.index('git -C "$repository_root" push')
        signed_oci = script.index("wait_for_signed_oci\n", tag_push)
        reverify = script.index('prepublish_dir="$work_dir/prepublish-reverification"')
        final_notes = script.index("run_candidate_tool final-notes", reverify)
        publish = script.index('gh release create "$FINAL_TAG"', reverify)
        final_validation = script.index("run_candidate_tool final-release", publish)
        self.assertLess(first_gate, tag_push)
        self.assertLess(tag_push, signed_oci)
        self.assertLess(signed_oci, reverify)
        self.assertLess(reverify, final_notes)
        self.assertLess(final_notes, publish)
        self.assertLess(publish, final_validation)
        self.assertIn('gh release verify-asset "$tag"', script)
        self.assertIn('verify_release_attestations "$candidate_tag"', script)
        self.assertNotIn('gh release download "$FINAL_TAG"', script)
        self.assertIn("freeze_run_id", script)
        self.assertIn("candidate_tag_manifest_mismatch", script)
        self.assertIn("final_tag_target_chronology_or_annotation_invalid", script)
        self.assertIn('Gate evidence: $gate_evidence_digest', script)
        self.assertIn('Candidate custody tag: $candidate_tag', script)
        self.assertIn("run_candidate_tool local-tag", script)
        self.assertNotIn('local_tag_message="$(', script)
        self.assertIn('cat-file tag "refs/tags/$FINAL_TAG"', script)
        self.assertNotIn('rev-list -n 1 "$FINAL_TAG"', script)

    def test_promotion_requires_the_clean_exact_candidate_checkout(self) -> None:
        script = (ROOT / "tools/promote_v1_candidate.sh").read_text()
        clean_check = script.index(
            "status --porcelain=v1 --untracked-files=all --ignored=matching --ignore-submodules=none"
        )
        fetch_main = script.index('fetch --no-tags origin \\\n')
        explicit_main_ref = script.index(
            '"refs/heads/main:refs/remotes/origin/main"', fetch_main
        )
        ancestry_check = script.index(
            'merge-base --is-ancestor "$checkout_commit" origin/main'
        )
        manifest_match = script.index(
            '[[ "$manifest_source_commit" == "$checkout_commit" ]]'
        )
        first_validation = script.index("run_candidate_tool promotion")
        first_release_mutation = script.index(
            'git -C "$repository_root" -c tag.gpgSign=false tag'
        )

        self.assertLess(clean_check, fetch_main)
        self.assertLess(fetch_main, explicit_main_ref)
        self.assertLess(explicit_main_ref, ancestry_check)
        self.assertLess(ancestry_check, manifest_match)
        self.assertLess(manifest_match, first_validation)
        self.assertLess(first_validation, first_release_mutation)
        self.assertIn("promotion_checkout_not_clean", script)
        self.assertIn("promotion_checkout_not_on_main", script)
        self.assertIn("promotion_checkout_candidate_mismatch", script)
        self.assertIn("promotion_output_inside_checkout", script)
        self.assertIn("python3 -I -B -S", script)
        self.assertIn(
            '"$checkout_commit:tools/v1_candidate.py"',
            script,
        )
        self.assertIn(
            '"$checkout_commit:tools/verify_v1_internal_repeatability_evidence.py"',
            script,
        )
        self.assertNotIn('python3 "$tool"', script)

    def test_promotion_pins_evidence_and_revalidates_every_phase(self) -> None:
        script = (ROOT / "tools/promote_v1_candidate.sh").read_text()
        snapshot = script.index("run_candidate_tool evidence-snapshot")
        first_validation = script.index("run_candidate_tool promotion")
        promotion_count = script.count("run_candidate_tool promotion")
        self.assertLess(snapshot, first_validation)
        self.assertEqual(script.count('--evidence "$evidence_path"'), 1)
        self.assertEqual(
            script.count('--evidence "$pinned_evidence_path"'), promotion_count + 2
        )
        self.assertNotIn(
            '"$evidence_path"',
            script[script.index('>"$evidence_snapshot_report"') :],
        )
        self.assertEqual(
            script.count('assert_readiness_binding "$'),
            promotion_count,
        )
        self.assertEqual(script.count('snapshot_provenance "$'), promotion_count)
        self.assertEqual(
            script.count('--freeze-run-api "$initial_dir/freeze-run-api.json"'), 1
        )
        self.assertEqual(
            script.count('--custody-tag-api "$initial_dir/custody-tag-api.json"'),
            1,
        )
        self.assertIn("gate_evidence_snapshot_digest_mismatch", script)
        self.assertIn("promotion_readiness_binding_changed", script)

    def test_promotion_rechecks_candidate_tag_oci_and_final_metadata(self) -> None:
        script = (ROOT / "tools/promote_v1_candidate.sh").read_text()
        tag_create = script.index(
            '    git -C "$repository_root" -c tag.gpgSign=false tag'
        )
        current_time_guard = script.index("    require_gate_time_current\n")
        signed_oci = script.index("wait_for_signed_oci\n", tag_create)
        prepublish = script.index(
            'prepublish_dir="$work_dir/prepublish-reverification"', signed_oci
        )
        prepublish_snapshot = script.index(
            'snapshot_candidate "$prepublish_dir"', prepublish
        )
        prepublish_validation = script.index("run_candidate_tool promotion", prepublish)
        prepublish_attestation = script.index(
            'verify_release_attestations "$candidate_tag" "$prepublish_dir"',
            prepublish,
        )
        final_release_lookup = script.index(
            'if ! release_exists "$FINAL_TAG"', prepublish
        )
        final_release_create = script.index(
            'gh release create "$FINAL_TAG"', final_release_lookup
        )
        published = script.index(
            'published_dir="$work_dir/published-reverification"',
            final_release_create,
        )
        published_snapshot = script.index('snapshot_candidate "$published_dir"', published)
        published_validation = script.index("run_candidate_tool promotion", published)
        final_release_validation = script.index(
            "run_candidate_tool final-release", published
        )
        published_attestation = script.index(
            'verify_release_attestations "$candidate_tag" "$published_dir"', published
        )

        self.assertLess(current_time_guard, tag_create)
        self.assertLess(signed_oci, prepublish)
        self.assertLess(prepublish, prepublish_snapshot)
        self.assertLess(prepublish_snapshot, prepublish_validation)
        self.assertLess(prepublish_validation, prepublish_attestation)
        self.assertLess(prepublish_attestation, final_release_lookup)
        self.assertLess(final_release_lookup, final_release_create)
        self.assertLess(final_release_create, published)
        self.assertLess(published, published_snapshot)
        self.assertLess(published_snapshot, published_validation)
        self.assertLess(published_validation, final_release_validation)
        self.assertLess(final_release_validation, published_attestation)
        self.assertIn("gate_evidence_not_yet_current", script)
        self.assertIn("live_tag_immutability_contract_invalid", script)
        self.assertIn('.source_type == "Repository"', script)
        self.assertIn("update_allows_fetch_and_merge // false", script)

    def test_source_distribution_contract_includes_custody_tools(self) -> None:
        manifest = (ROOT / "MANIFEST.in").read_text()
        verifier = (ROOT / "tools/verify_core_wheel.py").read_text()
        for relative in (
            "requirements/v1-source-build.lock",
            "tools/v1_candidate.py",
            "tools/promote_v1_candidate.sh",
        ):
            self.assertIn(f"include {relative}", manifest)
            self.assertIn(f'"{relative}"', verifier)


if __name__ == "__main__":
    unittest.main()
