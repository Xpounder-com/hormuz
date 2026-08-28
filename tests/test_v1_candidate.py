from __future__ import annotations

import hashlib
import io
import json
import stat
import subprocess
import tarfile
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from tools import v1_candidate


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_FIXTURE = (
    ROOT
    / "tests"
    / "fixtures"
    / "policy_admin_usability"
    / "complete-synthetic-v2.json"
)


class V1CandidateTests(unittest.TestCase):
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
        value["operator_attestation"][
            "distinct_humans_verified_off_repository"
        ] = True
        candidate = manifest["candidate"]
        assert isinstance(candidate, dict)
        value["candidate"] = {
            "target_version": candidate["target_version"],
            "artifact_kind": candidate["artifact_kind"],
            "artifact_digest": candidate["artifact_digest"],
            "source_commit": candidate["source_commit"],
            "frozen_at": candidate["frozen_at"],
        }
        for session in value["sessions"]:
            session["candidate_artifact_digest"] = candidate["artifact_digest"]
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
            "created_at": "2025-08-27T10:01:00Z",
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

    def test_freeze_workflow_has_one_build_and_no_final_tag_creation(self) -> None:
        workflow = (ROOT / ".github/workflows/freeze-v1-candidate.yml").read_text()
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertIn(
            "name: Authorize the designated v1 release steward\n    permissions: {}",
            workflow,
        )
        self.assertIn("V1_RELEASE_STEWARD", workflow)
        self.assertIn("ORIGINAL_ACTOR: ${{ github.actor }}", workflow)
        self.assertIn("TRIGGERING_ACTOR: ${{ github.triggering_actor }}", workflow)
        self.assertIn("needs: authorize", workflow)
        self.assertIn("environment: v1-release-custody", workflow)
        self.assertLess(workflow.index("authorize:"), workflow.index("contents: write"))
        self.assertIn("attestations: read", workflow)
        self.assertEqual(workflow.count("python -m build --sdist"), 1)
        self.assertIn("requirements/v1-source-build.lock", workflow)
        self.assertIn("--require-hashes", workflow)
        self.assertIn("--force-reinstall", workflow)
        self.assertIn("--only-binary=:all:", workflow)
        self.assertIn("--no-deps", workflow)
        self.assertIn("--no-isolation", workflow)
        self.assertIn('[[ "$GITHUB_RUN_ATTEMPT" == "1" ]]', workflow)
        self.assertIn("head_sha=$GITHUB_SHA", workflow)
        self.assertIn('[[ "$ORIGINAL_ACTOR" == "$AUTHORIZED_STEWARD" ]]', workflow)
        self.assertIn(
            '[[ "$TRIGGERING_ACTOR" == "$AUTHORIZED_STEWARD" ]]', workflow
        )
        self.assertNotIn('actor.get("login") == steward', workflow)
        self.assertNotIn('triggering_actor.get("login") == steward', workflow)
        self.assertIn("/jobs?filter=all&per_page=100", workflow)
        self.assertIn("tools/v1_candidate.py freeze-authorization", workflow)
        self.assertNotIn("freeze_run_identity", workflow)
        self.assertIn("create a new commit instead of rebuilding", workflow)
        self.assertIn("immutable-releases", workflow)
        self.assertIn("V1_RELEASE_ADMIN_TOKEN", workflow)
        self.assertIn("Administration and Environments permissions", workflow)
        self.assertIn("Steward workflow candidate tags", workflow)
        self.assertIn("candidate_creation_rule_contract", workflow)
        self.assertIn('"actor_id":15368', workflow)
        self.assertIn("environments/v1-release-custody", workflow)
        self.assertIn('item.get("type") == "required_reviewers"', workflow)
        self.assertIn('deployment.get("protected_branches") is True', workflow)
        self.assertIn('reviewer") or {}).get("login") == steward', workflow)
        self.assertIn('.source_type == "Repository"', workflow)
        self.assertIn("refs/tags/candidate-v1.0.0-*", workflow)
        self.assertIn("live no-bypass immutability", workflow)
        self.assertIn('gh release create "$CUSTODY_TAG"', workflow)
        self.assertIn("--prerelease", workflow)
        self.assertIn("--latest=false", workflow)
        self.assertNotIn("--draft", workflow)
        self.assertIn("candidate-v1.0.0-", workflow)
        self.assertNotIn("--clobber", workflow)
        self.assertNotIn("gh release upload", workflow)
        self.assertIn("could not prove $label is absent", workflow)
        self.assertIn("'HTTP 404'", workflow)

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
            "setuptools==80.9.0": (
                "062d34222ad13e0cc312a4c02d73f059e86a4acbfbdea8f8f76b28c99f306922"
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
                "requires": ["setuptools==80.9.0"],
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
            '"$checkout_commit:tools/verify_policy_admin_usability_evidence.py"',
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
